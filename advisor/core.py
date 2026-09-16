# -*- coding: utf-8 -*-
"""advisor.core — 购票决策分析引擎（唯一真相）

设计约束：
  * 零 LLM、零网络、零写库。只用 sqlite3 / pandas / numpy。
  * 所有公开函数返回纯 Python 原生类型（dict/list/str/int/float/None），
    可直接 json.dumps，不需要自定义 encoder。
  * 决策路径完全不经过 common.lead_bin 的粗分桶——粗桶会让
    "提前1天"和"提前3天"共用同一个 P25（实测北京→泉州"1-3天"桶内
    每个起飞日的分位数完全相同），无法用于决策。粗桶只在卡片里作对照。

对外接口只有两个：advise() 和 scan()。
"""
from __future__ import annotations

import os
import pickle
import sqlite3

import numpy as np
import pandas as pd

import common as C

# 改动 add_features 的产出列、或任何进入缓存的派生结构时，必须递增这个版本号——
# 否则 _cache_read 会拿旧 pickle 里的 df 继续用，新列缺失且不报错。
# v1.1：bj_airport → dep_ap/arr_ap（机场归一改为通用解析）
SCHEMA_VERSION = "1.1"

# 分位置信度门槛（样本数 n / 覆盖起飞日数 nd）
CONF_HIGH = (60, 8)
CONF_MED = (25, 5)
CONF_LOW = (12, 3)

# 参照集阶梯：先用最窄的 lead 窗口（同期性最强），样本不够再放宽
WINDOW_LADDER = (3, 5, 7, 10)
# 再看时间范围：优先最贴近的日期
SCOPE_LADDER = (14, 30, 62, None)

# 数据新鲜度分级（小时）
FRESH_HOURS = 12
STALE_HOURS = 48
OLD_HOURS = 24 * 7

_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
_MEMO: dict = {}
_CON_MEMO: dict = {}


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _f(x):
    """numpy 标量 → Python float；NaN/inf → None。"""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if (np.isnan(v) or np.isinf(v)) else v


def _i(x):
    v = _f(x)
    return None if v is None else int(round(v))


def _db_version(db):
    st = os.stat(db)
    return {"path": os.path.abspath(db), "mtime_ns": st.st_mtime_ns, "size": st.st_size}


def _cache_path(db_ver, as_of, route):
    from hashlib import md5
    tag = f"{db_ver['mtime_ns']}_{db_ver['size']}_{as_of}_{route}"
    h = md5(tag.encode()).hexdigest()[:16]
    name = f"{route.replace('->', '_')}_{'latest' if as_of is None else md5(str(as_of).encode()).hexdigest()[:8]}_{h}.pkl"
    return os.path.join(_CACHE_DIR, name)


def _cache_read(path):
    try:
        with open(path, "rb") as fh:
            obj = pickle.load(fh)
        if obj.get("schema_version") != SCHEMA_VERSION:
            return None
        return obj
    except Exception:
        # 缓存永远只是加速，不是真相来源；任何异常都退回重算
        return None


def _cache_write(path, obj):
    """写缓存，并清掉同一个 (route, as_of) 的旧版本。

    缓存 key 里含 DB 的 mtime+size，所以爬虫每写一次库就会产生一批新文件。
    如果不清理，这些文件会无限累积——实测跑了一天就涨到 389MB，
    其中 14 份完整 DataFrame 副本占 224MB。这里只保留最新一份。
    """
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        obj = dict(obj, schema_version=SCHEMA_VERSION)
        tmp = path + ".tmp"
        with open(tmp, "wb") as fh:
            pickle.dump(obj, fh, protocol=4)
        os.replace(tmp, path)
        _cache_evict(path)
    except Exception:
        pass


def _cache_evict(keep_path):
    """删除同一 (route, as_of) 前缀下的其它缓存文件，并兜底限制总量。"""
    try:
        keep = os.path.basename(keep_path)
        # 文件名格式：{route}_{as_of_tag}_{dbhash}.pkl
        # 从右侧剥掉 dbhash 段才是『同一 route + 同一 as_of』的前缀。
        # 不能用 split("_")[:2] —— 航线名本身含下划线（bjs_jjn），会切错。
        prefix = keep[:-4].rsplit("_", 1)[0]
        for name in os.listdir(_CACHE_DIR):
            if name == keep or not name.endswith(".pkl"):
                continue
            if name.startswith(prefix):
                try:
                    os.remove(os.path.join(_CACHE_DIR, name))
                except OSError:
                    pass
    except Exception:
        pass
    _cache_cap()


def _cache_cap(max_files=12):
    """总量兜底：按修改时间保留最近 max_files 个（防止 --as-of 用出很多组）。"""
    try:
        files = [os.path.join(_CACHE_DIR, n) for n in os.listdir(_CACHE_DIR)
                 if n.endswith(".pkl")]
        if len(files) <= max_files:
            return
        files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        for p in files[max_files:]:
            try:
                os.remove(p)
            except OSError:
                pass
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 载入层
# ---------------------------------------------------------------------------
def load_df_all(db=None, as_of=None, use_cache=True):
    """全库特征化 DataFrame（按 as_of 截断）。进程内记忆化 + pickle 缓存。"""
    db = db or C.DB_FILE
    db_ver = _db_version(db)
    key = ("df_all", db_ver["mtime_ns"], db_ver["size"], str(as_of))
    if key in _MEMO:
        return _MEMO[key]

    path = _cache_path(db_ver, as_of, "all")
    if use_cache:
        obj = _cache_read(path)
        if obj is not None:
            _MEMO[key] = obj["df"]
            return obj["df"]

    df = C.as_of_filter(C.add_features(C.load_flight_prices(db)), as_of)
    _MEMO[key] = df
    if use_cache:
        _cache_write(path, {"df": df})
    return df


def load_route(df_all, route_from, route_to, as_of=None, use_cache=True):
    """目标航线的派生帧：df / grid / floor_int / floor_bin / traj。

    只对目标航线跑 lead_grid —— 全量需 15s，单航线约 6s。
    """
    route = f"{route_from}->{route_to}"
    db_ver = _db_version(C.DB_FILE)
    key = ("route", db_ver["mtime_ns"], db_ver["size"], str(as_of), route)
    if key in _MEMO:
        return _MEMO[key]

    path = _cache_path(db_ver, as_of, route)
    if use_cache:
        obj = _cache_read(path)
        if obj is not None:
            _MEMO[key] = obj
            return obj

    df = df_all[(df_all.route_from == route_from) & (df_all.route_to == route_to)].copy()
    out = build_route(df)
    _MEMO[key] = out
    if use_cache and not df.empty:
        _cache_write(path, out)
    return out


def build_route(df, ref_df=None):
    """由特征化后的航线帧构造 route bundle（grid / floor_int / traj）。

    单独抽出来是为了让『带约束』的分析能复用同一条构造路径——
    约束必须在 df 层生效后重建全部派生帧，只筛快照会得到错位的分位。

    ref_df 只影响删失判定（见 common.trajectory_summary），默认与 df 同源。
    """
    if df.empty:
        return {"df": df, "label": None, "grid": pd.DataFrame(),
                "floor_int": pd.DataFrame(), "floor_bin": pd.DataFrame(),
                "traj": pd.DataFrame()}
    grid = C.lead_grid(df, max_lead=C.FLOOR_LEAD_MAX)
    return {
        "df": df,
        "label": df.route_label.iloc[0],
        "grid": grid,
        "floor_int": C.floor_by_int_lead(grid),
        "floor_bin": C.floor_by_lead(grid),
        "traj": C.trajectory_summary(df, ref_df),
    }


def list_routes(db=None, as_of=None):
    """列出库里所有航线 + 覆盖率分级（供 --list-routes）。"""
    df = load_df_all(db, as_of)
    rows = []
    for (rf, rt), g in df.groupby(["route_from", "route_to"], sort=False):
        label = g.route_label.iloc[0]
        cov = route_coverage(g, label)
        rows.append({
            "route": f"{rf}->{rt}", "route_label": label,
            "n_snapshots": int(len(g)),
            "n_flight_dates": int(g.flight_date.nunique()),
            "n_batches": int(g.crawl_dt.nunique()),
            "last_crawl": str(g.crawl_dt.max()),
            # 覆盖的起飞日范围是任何一次分析的硬边界，要第一眼就看到：
            # 库外的日期一律 exit 3，只能靠逐个 probe 撞出来太费事。
            "flight_date_min": str(g.flight_date.min()),
            "flight_date_max": str(g.flight_date.max()),
            "grade": cov["grade"],
            "note": cov["note"],
        })
    rows.sort(key=lambda r: -r["n_snapshots"])
    return rows


# ---------------------------------------------------------------------------
# 数据新鲜度 / 覆盖率
# ---------------------------------------------------------------------------
def data_freshness(df_all, as_of=None, db=None, now=None):
    """数据新鲜度分级。看板服务器上的 DB 是手动 scp 快照，可能落后数天。"""
    crawl_max = df_all.crawl_dt.max()
    as_of_ts = pd.Timestamp(as_of) if as_of is not None else crawl_max
    # 回放模式下『陈旧度』必须以模拟时刻为基准，而不是真实当前时间——
    # 否则 --as-of 一个历史批次会被判成『严重陈旧』而拒绝出结论，回放就没意义了。
    now_ts = (pd.Timestamp(now) if now is not None
              else pd.Timestamp(as_of) if as_of is not None
              else pd.Timestamp.now())
    age_h = (now_ts - crawl_max).total_seconds() / 3600.0

    # 爬虫心跳：monitor_log 的最后一次运行
    last_run, median_gap = None, None
    try:
        ml = C.load_monitor_log(db or C.DB_FILE)
        if as_of is not None and len(ml):
            # 回放时也只能看到当时之前的运行记录，否则会显示"未来"的心跳
            ml = ml[pd.to_datetime(ml["run_time"]) <= pd.Timestamp(as_of)]
        if len(ml):
            runs = pd.to_datetime(ml["run_time"]).sort_values()
            last_run = runs.iloc[-1]
            if len(runs) > 2:
                median_gap = float(runs.diff().dt.total_seconds().div(3600).median())
    except Exception:
        pass

    if age_h <= max(FRESH_HOURS, 2 * (median_gap or 0)):
        level = "fresh"
    elif age_h <= STALE_HOURS:
        level = "stale"
    elif age_h <= OLD_HOURS:
        level = "old"
    else:
        level = "very_old"

    alive = None
    if last_run is not None:
        alive = bool((now_ts - last_run).total_seconds() / 3600.0 < 24)

    label = {"fresh": "新鲜", "stale": "略旧", "old": "陈旧", "very_old": "严重陈旧"}[level]
    note = f"库内最后写入 {crawl_max:%Y-%m-%d %H:%M}（{age_h:.1f} 小时前）· {label}"
    if not alive and level != "fresh":
        note += "；monitor_log 显示爬虫已超过 24 小时未运行"
    if as_of is not None:
        note = f"【回放模式】模拟到 {as_of_ts:%Y-%m-%d %H:%M}，" + note

    return {
        "crawl_time": str(crawl_max), "age_hours": round(age_h, 2), "freshness": level,
        "freshness_label": label, "n_batches": int(df_all.crawl_dt.nunique()),
        "n_snapshots": int(len(df_all)),
        "last_monitor_run": None if last_run is None else str(last_run),
        "median_batch_gap_hours": None if median_gap is None else round(median_gap, 2),
        "crawler_alive": alive, "replay_mode": as_of is not None, "note": note,
    }


def route_coverage(df_route, label):
    """航线覆盖率分级——数据稀少的航线必须拒绝出分位，而不是基于几行数据编结论。"""
    n = len(df_route)
    nd = int(df_route.flight_date.nunique()) if n else 0
    nb = int(df_route.crawl_dt.nunique()) if n else 0
    last = df_route.crawl_dt.max() if n else None
    recent = bool(n and (pd.Timestamp.now() - last).days <= 7)

    if nd >= 30 and nb >= 50 and recent:
        grade, note = "rich", "数据充足，可出分位"
    elif nd >= 5 and nb >= 20:
        grade, note = "thin", "数据偏薄，分位置信度会下调"
    elif nd >= 2:
        grade, note = "sparse", "数据稀疏，不出分位，只报当前各航班绝对价格"
    else:
        grade, note = "insufficient", (
            f"{label} 仅有 {n} 条快照、{nd} 个起飞日、{nb} 个批次，"
            f"无法建立同期价格分布")

    return {"grade": grade, "note": note, "n_snapshots": n,
            "n_flight_dates": nd, "n_batches": nb,
            "last_crawl": None if last is None else str(last)}


# ---------------------------------------------------------------------------
# 口径污染
# ---------------------------------------------------------------------------
def data_quality(df_route, grid):
    """识别已知的价格口径污染，避免把异常当信号。所有阈值实时算，不硬编码。"""
    out = {"warnings": []}
    if df_route.empty:
        return out

    # 1) 平台价：航司固定档位，会形成一个占比显著突出的价位带。
    #    实测北京/泉州两向都是 ¥500–520（占 10–13%），而其它价位是 3–6% 的
    #    平滑衰减——所以判据必须是『突出』而不是『占比超过某个绝对值』，
    #    否则会把十几个正常价位全捞进来。
    vc = df_route.price.value_counts(normalize=True)
    platform = []
    if len(vc) >= 3:
        top = float(vc.iloc[0])
        second = float(vc.iloc[1])
        # 最高档占比至少是次高档的 1.8 倍，才算『突出』而非自然衰减
        if second > 0 and top / second >= 1.8 and top >= 0.04:
            p_star = int(vc.index[0])
            # 成员必须自身也够突出，否则会把价位带左右两侧的正常价位一起捞进来
            platform = sorted(int(p) for p in vc.index
                              if p_star * 0.95 <= p <= p_star * 1.05
                              and float(vc[p]) >= 0.6 * top)
            out["platform_prices"] = {
                "anchor": p_star,
                "prominence": _f(top / second),
                "prices": platform,
                "band": [min(platform), max(platform)],
                "share": _f(vc[vc.index.isin(platform)].sum()),
                "interpretation": ("航司固定档位（买不到更低价时它就是常态），"
                                   "不是异常值，也不代表便宜"),
            }
    if "platform_prices" not in out:
        out["platform_prices"] = {
            "prices": [], "share": 0.0,
            "interpretation": "未检测到占比突出的固定价位带",
        }

    # 2) 全价舱跳变：仅剩全价舱时会出现的价格上跳，集中在临近起飞
    if len(df_route) > 100:
        thr = int(max(df_route.price.quantile(0.99), df_route.price.quantile(0.75) * 3))
        hi = df_route[df_route.price >= thr]
        base = float((df_route.lead_days <= 3).mean())
        at = float((hi.lead_days <= 3).mean()) if len(hi) else 0.0
        out["full_fare_jumps"] = {
            "threshold": thr, "n": int(len(hi)),
            "share_at_lead_le3": _f(at), "baseline_share_at_lead_le3": _f(base),
            "enrichment": _f(at / base) if base else None,
            "interpretation": "仅剩全价舱的口径跳变，不计入『普通涨价』",
        }
        if at > 0.3 and base and at / base > 2:
            out["warnings"].append(
                f"价格 ≥{thr} 的快照有 {at*100:.0f}% 落在提前 ≤3 天"
                f"（基线 {base*100:.0f}%，富集 {at/base:.1f}×）——属全价舱口径跳变")

    # 3) 页面级起价污染
    #    判据一（EDA 00 §4c 原口径）：同(航向,起飞日,批次,价格)下 ≥5 个不同航班号
    #      —— 注意是『≥5 个航班同价』，不是『所有航班同价』，后者实测恒为 0 组。
    #    判据二（更强的判别）：单航班在单批次内的价格档数。真实逐航班报价下，
    #      同一航班在一个批次里会有多个舱位档（主线中位 8.5–9 档）；
    #      若抓到的是页面级“¥xxx 起”占位价，则中位只有 1–2 档。
    res = df_route.groupby(["flight_date", "crawl_time", "price"])["flight_no"].nunique()
    n_res = int((res >= 5).sum())

    per_flight = (df_route.groupby(["flight_no", "crawl_time"])["price"]
                  .nunique().groupby("flight_no").median())
    levels_med = _f(per_flight.median()) if len(per_flight) else None
    n_batches = int(df_route.crawl_dt.nunique())

    flagged = bool(
        n_res >= 10
        or (levels_med is not None and levels_med <= 3 and n_batches >= 20)
    )
    reason = None
    if n_res >= 10:
        reason = f"检测到 {n_res} 组『同批次 ≥5 个航班完全同价』"
    elif flagged:
        reason = (f"单航班在同批次内的价格档数中位仅 {levels_med:.0f} 档"
                  f"（主线航线通常 8–9 档）")
    out["page_level_pollution"] = {
        "resonance_groups": n_res,
        "price_levels_per_flight_batch_median": levels_med,
        "n_batches": n_batches, "flagged": flagged, "reason": reason,
    }
    if flagged:
        out["warnings"].append(
            f"疑似页面级起价（{reason}），抓到的大概率是页面『¥xxx 起』占位价"
            f"而非逐航班最低价，该航线的分位结论不可采信")

    return out


# ---------------------------------------------------------------------------
# 核心：滑动窗口分位
# ---------------------------------------------------------------------------
def floor_distribution(floor_int, route_label, flight_date, lead_now,
                       window_ladder=WINDOW_LADDER, scope_ladder=SCOPE_LADDER,
                       leave_one_out=True, bootstrap=500):
    """现价在『同航线、同提前量、同期』历史 floor 分布中的分位。

    取代 §3 表的 lead_bin 粗桶。窗口从 ±3 天起逐级放宽到 ±10，
    时间范围从 ±14 天放宽到全历史，取第一个满足置信门槛的组合。

    返回的 scope_spread 是参照集敏感度——同一个价格换不同参照集，
    分位可能摆动 20+ 个百分点（实测 6-7 月暑期系统性高于 9 月），
    这个摆动必须让用户看见，而不是藏起来。
    """
    if floor_int.empty:
        return {"ok": False, "why": "该航线无可用历史 floor", "confidence": "insufficient"}

    fd = pd.Timestamp(flight_date)
    f = floor_int[floor_int.route_label == route_label].copy()
    if f.empty:
        return {"ok": False, "why": "该航线无可用历史 floor", "confidence": "insufficient"}
    if leave_one_out:
        f = f[f.flight_date != flight_date]
    f["_fd"] = pd.to_datetime(f.flight_date)

    floor_now = f_ = None
    # 现价由调用方传入 floor_now；这里只算参照分布，故先取出
    pool_best = None
    picked = None
    for w in window_ladder:
        for scope in scope_ladder:
            p = f[(f.lead_days >= lead_now - w) & (f.lead_days <= lead_now + w)]
            if scope is not None:
                p = p[(p["_fd"] - fd).dt.days.abs() <= scope]
            n, nd = len(p), int(p.flight_date.nunique())
            if n == 0:
                continue
            conf = None
            if n >= CONF_HIGH[0] and nd >= CONF_HIGH[1]:
                conf = "high"
            elif n >= CONF_MED[0] and nd >= CONF_MED[1]:
                conf = "medium"
            elif n >= CONF_LOW[0] and nd >= CONF_LOW[1]:
                conf = "low"
            if conf is None:
                continue
            cand = {"window": w, "scope": scope, "n_samples": n, "n_dates": nd,
                    "confidence": conf, "samples": p.floor.to_numpy(),
                    "scope_label": "全部历史" if scope is None else f"±{scope}天"}
            if conf == "high":
                picked = cand
                break
            if pool_best is None or _rank(cand) > _rank(pool_best):
                pool_best = cand
        if picked:
            break
    picked = picked or pool_best
    if picked is None:
        return {"ok": False, "confidence": "insufficient",
                "why": f"target lead={lead_now} 附近无足够历史样本（需 ≥{CONF_LOW[0]} 条 / "
                       f"≥{CONF_LOW[1]} 个起飞日）"}

    return {"ok": True, "_pool": picked}


def _rank(cand):
    order = {"high": 3, "medium": 2, "low": 1}
    return order[cand["confidence"]] * 1000 - cand["window"] * 10 - (
        0 if cand["scope"] is None else cand["scope"])


def _percentile(samples, value):
    """连续分位：严格小于的占比 + 相等的一半。"""
    n = len(samples)
    if n == 0:
        return None
    lt = int((samples < value).sum())
    eq = int((samples == value).sum())
    return 100.0 * (lt + 0.5 * eq) / n


def percentile_report(floor_int, route_label, flight_date, lead_now, floor_now,
                      bootstrap=500, rng=None):
    """floor_distribution 的完整包装：分位 + 置信区间 + 参照集敏感度 + 长假污染。"""
    base = floor_distribution(floor_int, route_label, flight_date, lead_now)
    if not base.get("ok"):
        return {"ok": False, "confidence": "insufficient",
                "why": base.get("why", "样本不足")}

    pool = base["_pool"]
    s = pool["samples"]
    pct = _percentile(s, floor_now)
    ref = {f"P{q:02d}": _f(np.percentile(s, q)) for q in (5, 10, 25, 50, 75, 90, 95)}

    # bootstrap 置信区间
    ci = None
    if bootstrap and len(s) >= 20:
        rng = rng or np.random.default_rng(42)
        idx = rng.integers(0, len(s), size=(bootstrap, len(s)))
        draws = s[idx]
        vals = 100.0 * ((draws < floor_now).sum(axis=1) + 0.5 * (draws == floor_now).sum(axis=1)) / len(s)
        ci = [_f(np.percentile(vals, 5)), _f(np.percentile(vals, 95))]

    # 参照集敏感度：固定窗口，换时间范围看分位怎么摆
    spread = []
    f = floor_int[floor_int.route_label == route_label]
    f = f[(f.flight_date != flight_date)].copy()
    f["_fd"] = pd.to_datetime(f.flight_date)
    w = pool["window"]
    p_all = f[(f.lead_days >= lead_now - w) & (f.lead_days <= lead_now + w)]
    for scope in SCOPE_LADDER:
        p = p_all if scope is None else p_all[(p_all["_fd"] - pd.Timestamp(flight_date)).dt.days.abs() <= scope]
        if len(p) < CONF_LOW[0]:
            continue
        spread.append({"scope": "全部历史" if scope is None else f"±{scope}天",
                       "n": int(len(p)),
                       "percentile": _f(_percentile(p.floor.to_numpy(), floor_now))})
    rng_pct = None
    if len(spread) >= 2:
        vals = [x["percentile"] for x in spread if x["percentile"] is not None]
        rng_pct = _f(max(vals) - min(vals)) if len(vals) >= 2 else None

    # 长假/高峰污染：按『该起飞日的 floor 中位 / 全航线 floor 中位』判高，不硬编码日期
    holidays = None
    per_date = f.groupby("flight_date")["floor"].median()
    route_med = float(per_date.median()) if len(per_date) else None
    if route_med:
        hot_dates = set(per_date[per_date > route_med * 1.35].index)
        touched = p_all[p_all.flight_date.isin(hot_dates)]
        if len(touched):
            share = len(touched) / max(len(p_all), 1)
            clean = p_all[~p_all.flight_date.isin(hot_dates)]
            holidays = {
                "n_hot_dates": len(hot_dates),
                "share_in_baseline": _f(share),
                "percentile_excluding": _f(_percentile(clean.floor.to_numpy(), floor_now))
                if len(clean) >= CONF_LOW[0] else None,
                "note": (f"参照集里 {share*100:.0f}% 的样本来自高峰日期"
                         f"（该航线共 {len(hot_dates)} 个高峰起飞日），会把分位抬高"),
            }

    conf = pool["confidence"]
    if rng_pct is not None and rng_pct > 25:
        conf = {"high": "medium", "medium": "low", "low": "low"}[conf]

    band = ("≤25%·偏便宜" if pct <= 25 else "25–50%" if pct <= 50
            else "50–75%" if pct <= 75 else "≥75%·偏贵")

    return {
        "ok": True, "floor_now": _f(floor_now), "percentile": _f(pct),
        "percentile_ci90": ci, "band": band, "reference": ref,
        "scope": {"window": pool["window"], "scope": pool["scope_label"],
                  "n_samples": pool["n_samples"], "n_dates": pool["n_dates"],
                  "lead_range": [int(lead_now - pool["window"]), int(lead_now + pool["window"])],
                  "confidence": conf,
                  "confidence_before_spread": pool["confidence"]},
        "scope_spread": {"range": rng_pct, "by_scope": spread},
        "holiday_contamination": holidays,
    }


# ---------------------------------------------------------------------------
# 等待胜率（含幸存者偏差）
# ---------------------------------------------------------------------------
def wait_analysis(grid, route_label, lead_now, horizons=(1, 3, 7, 14), min_pairs=20):
    """『现在不买、等到 lead=X 再买』的历史结果。

    关键：present（现在能买到）里有一部分在 lead=X 时**已经买不到了**。
    只对活下来的样本统计涨跌会严重高估『等待』的收益——实测等到起飞当天，
    幸存样本里 87.5% 涨价，但同期 63.3% 的航班直接消失了。
    所以 disappear_rate 与涨跌率同级返回，不可隐藏。
    """
    if grid.empty:
        return []
    piv = grid.pivot_table(index="traj_key", columns="lead_days",
                           values="price", aggfunc="last")
    if lead_now not in piv.columns:
        return []
    a = piv[lead_now]
    present = a.notna()
    n_now = int(present.sum())

    out = []
    for k in horizons:
        t = lead_now - k
        if t < 0 or t not in piv.columns:
            continue
        b = piv[t]
        paired = present & b.notna()
        gone = present & b.isna()
        n_pair, n_gone = int(paired.sum()), int(gone.sum())
        if n_pair < min_pairs:
            out.append({"horizon_days": k, "target_lead": t, "n_at_now": n_now,
                        "n_paired": n_pair, "confidence": "insufficient"})
            continue
        d = (b[paired] - a[paired]).astype(float)
        dis = n_gone / max(n_now, 1)
        out.append({
            "horizon_days": k, "target_lead": t, "n_at_now": n_now,
            "n_paired": n_pair, "n_disappeared": n_gone,
            "disappear_rate": _f(dis),
            "down_rate": _f((d < 0).mean()), "flat_rate": _f((d == 0).mean()),
            "up_rate": _f((d > 0).mean()),
            "median_diff": _f(d.median()), "mean_diff": _f(d.mean()),
            "p25_diff": _f(d.quantile(0.25)), "p75_diff": _f(d.quantile(0.75)),
            "survivor_bias_warning": bool(dis > 0.25),
            "confidence": "high" if n_pair >= 100 else "medium",
        })
    return out


# ---------------------------------------------------------------------------
# 售罄 / 台阶趋势
# ---------------------------------------------------------------------------
def censoring_signal(traj, route_label, flight_date, floor_int):
    """疑似售罄：便宜航班提前从列表里消失，是这个系统里最强的『别等了』信号。"""
    if traj.empty:
        return {"n_traj": 0, "n_censored": 0, "censored_flights": [],
                "route_censored_rate": None, "verdict": "no_data"}

    route_rate = _f(traj.censored.mean())
    by_airline = []
    for al, g in traj.groupby("airline"):
        if len(g) >= 20:
            by_airline.append({"airline": al, "censored_rate": _f(g.censored.mean()),
                               "n": int(len(g))})
    by_airline.sort(key=lambda r: -(r["censored_rate"] or 0))

    day = traj[traj.flight_date == flight_date]
    cen = day[day.censored]
    cheapest_censored = False
    if len(cen) and not floor_int.empty:
        f = floor_int[(floor_int.route_label == route_label) & (floor_int.flight_date == flight_date)]
        if len(f):
            p25 = float(np.percentile(f.floor.to_numpy(), 25))
            cheapest_censored = bool(cen.price_min.min() <= p25)

    return {
        "n_traj": int(len(day)), "n_censored": int(len(cen)),
        "route_censored_rate": route_rate,
        "route_censored_by_airline": by_airline[:6],
        "censored_flights": [
            {"flight_no": r.flight_no, "airline": r.airline,
             "soldout_lead": _i(r.soldout_lead), "price_min": _i(r.price_min)}
            for r in cen.sort_values("price_min").itertuples()],
        "cheapest_censored": cheapest_censored,
        "verdict": ("cheap_seats_disappearing" if cheapest_censored
                    else "some_censoring" if len(cen) else "none"),
    }


def floor_trend(floor_int, route_label, flight_date, lead_now, lookback=(1, 3, 7)):
    """同一起飞日的 floor 随提前量变化的台阶——判断是『在涨』还是『在盘』。"""
    f = floor_int[(floor_int.route_label == route_label) & (floor_int.flight_date == flight_date)]
    if f.empty:
        return {"now": None, "direction": "unknown"}
    s = f.set_index("lead_days")["floor"].sort_index()
    now = _f(s.get(lead_now))
    steps = {}
    for k in lookback:
        t = lead_now + k
        if t in s.index:
            v = float(s.loc[t])
            steps[f"d{k}"] = {"lead": int(t), "floor": _f(v),
                              "delta": _f((now - v) if now is not None else None),
                              "pct": _f((now - v) / v * 100 if v and now is not None else None)}
    ref = steps.get("d3") or steps.get("d1") or steps.get("d7")
    direction = "unknown"
    if ref and ref["pct"] is not None:
        direction = "rising" if ref["pct"] > 10 else "falling" if ref["pct"] < -10 else "flat"
    return {"now": now, "steps": steps, "direction": direction,
            "basis": None if not ref else f"对比 lead={ref['lead']} 时的 {ref['floor']}"}


def season_context(floor_int, route_label, flight_date):
    """这个起飞日是不是节假日/高峰——数据驱动判定，不硬编码具体日期（明年会变）。"""
    f = floor_int[floor_int.route_label == route_label]
    if f.empty:
        return {"index": None, "is_peak": False}
    per_date = f.groupby("flight_date")["floor"].median()
    med = float(per_date.median())
    idx = _f(per_date.get(flight_date) / med) if flight_date in per_date.index and med else None
    fd = pd.Timestamp(flight_date)
    nbr = []
    for off in (-3, -2, -1, 1, 2, 3):
        d = (fd + pd.Timedelta(days=off)).strftime("%Y-%m-%d")
        if d in per_date.index:
            nbr.append({"flight_date": d, "floor": _i(per_date.loc[d])})
    return {
        "index": idx, "route_median_floor": _i(med),
        "is_peak": bool(idx is not None and idx > 1.35),
        "neighbor_dates": nbr,
        "note": (None if idx is None else
                 f"该日起飞日的历史 floor 中位是全线中位的 {idx:.2f} 倍"),
    }


# ---------------------------------------------------------------------------
# 用户约束（到达时刻 / 价格上限）
#
# 为什么需要这一层：日 floor 口径的 verdict 在用户有硬约束时会**误导**——
# 实测 jjn->bjs 2026-10-17 的 `wait`（观望偏贵）基于 ¥430 的晚班，
# 而一个要求 16:00 前落地的用户根本买不到那班。关于『买不到的东西』的
# 正确判定，比没有判定更危险，因为用户会以为问题已经被回答了。
#
# 所以约束必须**同时**作用在快照与历史 floor 分布上，保证是同一批航班在比。
# ---------------------------------------------------------------------------
def norm_hhmm(s):
    """'16:00' / '1600' / '16' / '9:5' / '16点' → 'HH:MM'；非法返回 None。

    归一成零填充的 'HH:MM' 后即可用字符串比较判断先后——
    arrival_time 的 '12:50' / '14:10' 正是这个格式。
    """
    if s is None:
        return None
    t = str(s).strip().replace("：", ":").replace("点", ":")
    if not t:
        return None
    if ":" in t:
        a, _, b = t.partition(":")
        a, b = a.strip(), b.strip()
        if not a.isdigit():
            return None
        b = b if b.isdigit() else "0"
    else:
        if not t.isdigit():
            return None
        # 支持不带分隔符的写法：16→16:00 / 905→09:05 / 1600→16:00
        if len(t) <= 2:
            a, b = t, "0"
        elif len(t) == 3:
            a, b = t[:1], t[1:]
        else:
            a, b = t[:-2], t[-2:]
    h, m = int(a), int(b)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return f"{h:02d}:{m:02d}"


def arrival_key(x):
    """arrival_time → 可比字符串；跨天（含 '+1'）返回 None。

    跨天到达（如 '00:45 +1天'）在『当日 HH:MM 前落地』的语义下必然不满足，
    返回 None 会让它被任何 arrive_by 约束排除。不设约束时不受影响。
    """
    if not isinstance(x, str) or not x:
        return None
    if "+1" in x:
        return None
    return norm_hhmm(x)


def normalize_constraints(constraints):
    """把 CLI 传来的原始约束规整成 {arrive_by, max_price}，非法项丢弃。

    返回给的错误串要能让 CLI 直接报出来，而不是静默忽略一个拼错的参数。
    """
    c = constraints or {}
    bad = []
    ab = c.get("arrive_by")
    ab_n = None
    if ab not in (None, ""):
        ab_n = norm_hhmm(ab)
        if ab_n is None:
            bad.append(f"--arrive-by 无法解析：{ab!r}（应为 HH:MM）")
    mp = c.get("max_price")
    mp_n = None
    if mp not in (None, ""):
        try:
            mp_n = int(mp)
            if mp_n <= 0:
                raise ValueError
        except (TypeError, ValueError):
            bad.append(f"--max-price 无法解析：{mp!r}（应为正整数）")
            mp_n = None
    if ab_n is None and mp_n is None:
        return None, bad
    return {"arrive_by": ab_n, "max_price": mp_n}, bad


def filter_by_arrival(df, hhmm):
    """按到达时刻筛观测行（df 级，而非快照级）。

    为什么必须在 df 级：到达时刻**不是稳定属性**——同一航班号有换季档，
    同一 (航班, 起飞日) 也有 10% 会在追踪中途改时刻。所以只能逐观测判定，
    筛完再重建 grid/floor_int/traj，全部派生量同源。

    早先按 traj_key 筛的写法是错的：那会把历史样本缩到当天一个起飞日，
    而分位算法又要 leave-one-out 排除目标日，于是样本恒为空、永远算不出分位。
    """
    if not hhmm:
        return df, None
    keys = df.arrival_time.map(arrival_key)
    ok = keys.notna() & (keys <= hhmm)
    kept = df[ok].copy()
    dropped = df[~ok]
    info = {
        "arrive_by": hhmm,
        "n_obs_before": int(len(df)), "n_obs_after": int(len(kept)),
        "n_dates_before": int(df.flight_date.nunique()),
        "n_dates_after": int(kept.flight_date.nunique()),
    }
    # 不给『航线级排除清单』：它跨全部起飞日，会把本日明明可用的航班也列进去。
    # 当日口径的清单由 advise 算（excluded_here / kept_here）。
    return kept, info


def constrained_route(route_from, route_to, as_of, db, arrive_by, R_full):
    """带到达时刻约束的 route bundle（进程内记忆化）。

    没有这层记忆化，--scan 30 天 × 每次重建 lead_grid（约 6s）就是 3 分钟。
    """
    db_ver = _db_version(db or C.DB_FILE)
    key = ("con", db_ver["mtime_ns"], db_ver["size"], str(as_of),
           f"{route_from}->{route_to}", arrive_by)
    if key in _CON_MEMO:
        return _CON_MEMO[key]
    kept_df, info = filter_by_arrival(R_full["df"], arrive_by)
    built = build_route(kept_df, ref_df=R_full["df"]) if not kept_df.empty else None
    _CON_MEMO[key] = (built, info)
    return _CON_MEMO[key]


def budget_report(floor_int, route_label, flight_date, lead_now, max_price, floor_now):
    """预算命中报告：在【约束后的】同期 floor 分布里，有多大比例 ≤ 预算。

    这里刻意**不用**预算去筛航班：预算筛的是『我肯出多少钱』，不是航班的
    固有属性。拿当前价格去筛历史样本会把参照集掐死，得到的比例毫无意义。
    """
    base = floor_distribution(floor_int, route_label, flight_date, lead_now)
    out = {"max_price": _i(max_price), "floor_now": _i(floor_now),
           "within_budget": bool(floor_now is not None and floor_now <= max_price)}
    if not base.get("ok"):
        out.update({"hist_n": 0, "hist_share_within": None,
                    "why": base.get("why", "样本不足")})
        return out
    pool = base["_pool"]
    s = pool["samples"]
    out.update({
        "hist_n": int(len(s)),
        "hist_scope": pool["scope_label"],
        "hist_share_within": _f(float((s <= max_price).mean())) if len(s) else None,
        "note": ("同期同提前量的观测里，有这么多比例的最低价落在你的预算内"
                 "——这才是『等不等得到』的基线，不是当前价的高低"),
    })
    return out


def flight_detail(route_from, route_to, flight_date, flight_no,
                  as_of=None, db=None, use_cache=True):
    """单个航班的价格轨迹与命运（供 --flight）。

    这是卡片最缺的一块：卡片只给当日快照，不给『这一班自己随时间怎么走』。
    实测同一航线上，晚班会在 ¥520↔¥430 之间来回，而固定档位的早班
    23 次观测纹丝不动——只看快照完全区分不出这两类。
    """
    db = db or C.DB_FILE
    df_all = load_df_all(db, as_of, use_cache)
    R = load_route(df_all, route_from, route_to, as_of, use_cache)
    label = R["label"] or f"{route_from}->{route_to}"
    fn = str(flight_no).strip().upper()

    out = {"schema_version": SCHEMA_VERSION,
           "query": {"route": f"{route_from}->{route_to}", "route_label": label,
                     "flight_date": flight_date, "flight_no": fn, "as_of": as_of},
           "warnings": []}
    out["data_as_of"] = data_freshness(df_all, as_of, db)

    sub = R["df"][(R["df"].flight_date == flight_date)
                  & (R["df"].flight_no.str.upper() == fn)]
    if sub.empty:
        have = sorted(R["df"][R["df"].flight_date == flight_date].flight_no.unique())
        out["ok"] = False
        out["why"] = (f"{label} {flight_date} 没有航班 {fn}。"
                      + (f"当日可选：{'、'.join(have)}" if have else "该日无任何观测"))
        return out

    sub = sub.sort_values("crawl_dt")
    first, last = sub.iloc[0], sub.iloc[-1]
    traj = R["traj"]
    row = traj[(traj.flight_date == flight_date) & (traj.flight_no.str.upper() == fn)]
    censored = bool(row.censored.iloc[0]) if len(row) else False
    soldout_lead = _i(row.soldout_lead.iloc[0]) if len(row) else None

    snap_all, snap_t = _snapshot(R["df"], flight_date)
    floor_day = _i(snap_all.price.min()) if snap_all is not None else None
    best = _i(sub.price.min())

    out.update({
        "ok": True,
        "flight": {"flight_no": first.flight_no, "airline": first.airline,
                   "aircraft_type": first.aircraft_type,
                   "departure_airport": first.departure_airport,
                   "arrival_airport": first.arrival_airport,
                   "dep_ap_label": None if pd.isna(first.dep_ap) else str(first.dep_ap),
                   "arr_ap_label": None if pd.isna(first.arr_ap) else str(first.arr_ap),
                   "departure_time": first.departure_time,
                   "arrival_time": first.arrival_time,
                   "dep_slot": first.dep_slot},
        "n_obs": int(len(sub)),
        "price_now": _i(last.price),
        "price_min": best, "price_max": _i(sub.price.max()),
        "n_price_levels": int(sub.price.nunique()),
        "lead_first": _i(first.lead_days), "lead_last": _i(last.lead_days),
        "censored": censored, "soldout_lead": soldout_lead,
        "floor_now": floor_day,
        "vs_floor_now": None if (floor_day is None or last.lead_days != snap_all.lead_days.iloc[0]) else _i(last.price - floor_day),
        "trajectory": [{"lead_days": _i(r.lead_days), "price": _i(r.price),
                        "crawl_time": str(r.crawl_dt), "crawl_date": str(r.crawl_dt)[:10]}
                       for r in sub.itertuples()],
    })
    if censored and soldout_lead is not None:
        out["warnings"].append(
            f"⚠️ 该航班在提前 {soldout_lead} 天时从列表消失（疑似售罄），"
            f"之后再未被观测到——『涨跌率』统计里不含它")
    return out


# ---------------------------------------------------------------------------
# 选班
# ---------------------------------------------------------------------------
def _snapshot(df_route, flight_date):
    """目标日『现在能买到什么』。

    必须按 (航线, 起飞日) 各自取最新一次观测，不能用全局最后一批——
    告警优先爬取会让各航线最后一批时间不同，实测北京→泉州 2026-10-17
    完全不在全局最后一批里，会被静默丢掉。
    """
    sub = df_route[df_route.flight_date == flight_date]
    if sub.empty:
        return None, None
    t = sub.crawl_dt.max()
    return sub[sub.crawl_dt == t].copy(), t


def select_flights(snap, grid, df_route, traj, floor_int, route_label, flight_date,
                   lead_now, quality, top_n=3):
    """选班：候选清单 + 航司/机场/时刻对照 + 具体推荐。"""
    if snap is None or snap.empty:
        return {}

    quality = quality or {}
    platform = set((quality.get("platform_prices") or {}).get("prices") or [])
    full_thr = (quality.get("full_fare_jumps") or {}).get("threshold")
    floor_now = float(snap.price.min())

    def flags(r):
        fl = []
        if platform and int(r.price) in platform:
            fl.append("PLATFORM_PRICE")
        if full_thr and r.price >= full_thr:
            fl.append("FULL_FARE_JUMP")
        if r.dep_hour is not None and not pd.isna(r.dep_hour) and (r.dep_hour < 6 or r.dep_hour >= 23):
            fl.append("RED_EYE")
        if isinstance(r.arrival_time, str) and "+1" in r.arrival_time:
            fl.append("ARRIVES_NEXT_DAY")
        if lead_now <= 3 and r.price > floor_now:
            fl.append("LAST_SEATS_SUSPECT")
        return fl

    side_info = airport_side(df_route)
    ap_col = None
    if side_info:
        ap_col = "dep_ap" if side_info["side"] == "dep" else "arr_ap"

    def cmp_ap(r):
        """该候选在『多机场城市』那一侧的机场标签；无多机场端则 None。"""
        if ap_col is None:
            return None
        v = getattr(r, ap_col, None)
        return None if v is None or (isinstance(v, float) and pd.isna(v)) else str(v)

    cands = []
    notes = []
    for r in snap.sort_values("price").itertuples():
        cands.append({
            "flight_no": r.flight_no, "airline": r.airline,
            "aircraft_type": r.aircraft_type, "ac_family": C.ac_family(r.aircraft_type),
            "departure_airport": r.departure_airport, "arrival_airport": r.arrival_airport,
            "cmp_airport": cmp_ap(r),
            "departure_time": r.departure_time, "arrival_time": r.arrival_time,
            "dep_hour": _i(r.dep_hour), "dep_slot": r.dep_slot,
            "price": _i(r.price),
            "premium_vs_floor": _i(r.price - floor_now),
            "premium_pct": _f((r.price - floor_now) / floor_now * 100 if floor_now else None),
            "flags": flags(r),
        })

    # --- 航司分层：以 grid 的 (起飞日, lead, 航班) 为单位，避免按爬取频次加权 ---
    # 起飞日太少时不出这个表——否则会从 1 个起飞日算出"某航司 100% 是最低价"
    # 这种假结论，正是本工具要避免的。
    n_dates = int(grid.flight_date.nunique()) if not grid.empty else 0
    if n_dates >= 5:
        airline_tbl = _airline_table(grid, traj)
    else:
        airline_tbl = []
        notes.append(f"仅 {n_dates} 个起飞日，航司/时刻对照不具统计意义，已略去")
    airport_tbl = _airport_table(grid, df_route, snap, side_info)
    slot_tbl = _slot_table(grid, df_route, snap, lead_now) if n_dates >= 5 else []

    rec = _rank_candidates(cands, airline_tbl, airport_tbl, slot_tbl,
                           floor_now, top_n)
    return {"floor_now": _i(floor_now), "n_flights": len(cands),
            "candidates": cands, "airline_table": airline_tbl,
            "airport_table": airport_tbl, "slot_table": slot_tbl,
            "notes": notes, **rec}


def _airline_table(grid, traj):
    if grid.empty:
        return []
    g = grid.copy()
    day_min = g.groupby(["flight_date", "lead_days"])["price"].transform("min")
    g["is_min"] = g.price == day_min
    g["gap"] = g.price - day_min
    cen = traj.groupby("airline")["censored"].mean() if len(traj) else None
    rows = []
    for al, sub in g.groupby("airline"):
        rows.append({
            "airline": al, "n_obs": int(len(sub)),
            "floor_win_rate": _f(sub.is_min.mean()),
            "premium_median": _i(sub.loc[sub.gap > 0, "gap"].median()) if (sub.gap > 0).any() else 0,
            "price_p50": _i(sub.price.median()),
            "price_min_ever": _i(sub.price.min()),
            "censored_rate": _f(cen.get(al)) if cen is not None and al in cen.index else None,
        })
    rows.sort(key=lambda r: -(r["floor_win_rate"] or 0))
    for r in rows:
        cr = r["censored_rate"] or 0
        wr = r["floor_win_rate"] or 0
        if wr >= 0.5:
            r["verdict"] = "守门员·盯它" + ("，但低舱也最早售罄" if cr > 0.04 else "")
        elif wr >= 0.15:
            r["verdict"] = "次低价·备选"
        else:
            r["verdict"] = "极少成为最低价，除非时刻刚需不看"
    return rows


def airport_side(df_route):
    """判定该航线哪一端是『多机场城市』（如 北京=大兴/首都、上海=虹桥/浦东）。

    不写死任何城市：机场标签由 common.airport_label 从原始机场名解析，
    再把同一个城市端的标签去重。两端都是多机场时取机场数多的一侧。
    """
    if df_route.empty:
        return None
    cands = []
    for side, col in (("dep", "dep_ap"), ("arr", "arr_ap")):
        if col not in df_route.columns:
            continue
        labs = sorted(str(x) for x in df_route[col].dropna().unique())
        if len(labs) >= 2:
            cands.append((len(labs), 0 if side == "arr" else 1, side, labs))
    if not cands:
        return None
    cands.sort(key=lambda x: (-x[0], x[1]))
    return {"side": cands[0][2], "airports": cands[0][3]}


def _airport_table(grid, df_route, snap, side_info):
    """同城多机场对比（通用）。

    注意 grid 不含机场列（lead_grid 只保留 traj_key/lead/price 等），
    必须按 traj_key 从 df 映射回来——漏掉这一步会让整块功能静默失效：
    早先直接读 grid.bj_airport，那一列根本不存在，于是恒返回『不涉及机场对比』。
    """
    if not side_info:
        return {"available": False, "note": "该航线两端各自只有一个机场，无机场对比"}
    if grid.empty or df_route.empty:
        return {"available": False, "note": "无足够数据"}
    col = "dep_ap" if side_info["side"] == "dep" else "arr_ap"
    if col not in df_route.columns:
        return {"available": False, "note": "机场列缺失"}
    m = df_route.drop_duplicates("traj_key").set_index("traj_key")[col]
    g = grid.copy()
    g["ap"] = g.traj_key.map(m)
    g = g.dropna(subset=["ap"])
    if g.empty:
        return {"available": False, "note": "机场标签无法解析"}
    per = g.groupby(["flight_date", "ap"])["price"].min().unstack()
    labs = [a for a in side_info["airports"] if a in per.columns]
    if len(labs) < 2:
        return {"available": False, "note": "两机场中有一侧无数据"}
    a1, a2 = labs[0], labs[1]
    diff = (per[a1] - per[a2]).dropna()
    if len(diff) < 30:
        return {"available": False, "n_obs": int(len(diff)),
                "note": f"{a1}/{a2} 可比日期仅 {len(diff)} 天，不足以给出方向"}
    cur = {}
    for ap in (a1, a2):
        s = snap[snap[col] == ap] if col in snap.columns else pd.DataFrame()
        cur[ap] = _i(s.price.min()) if len(s) else None
    cheaper = a1 if float(diff.median()) < 0 else a2
    cheaper_rate = float((diff < 0).mean() if cheaper == a1 else (diff > 0).mean())
    return {
        "available": True, "n_obs": int(len(diff)), "side": side_info["side"],
        "airports": [a1, a2], "cheaper": cheaper, "current": cur,
        "median_diff": _i(abs(diff.median())),
        "cheaper_rate": _f(cheaper_rate),
        "verdict": f"{cheaper}更便宜（历史 {cheaper_rate*100:.0f}% 的日期）",
    }


def _slot_table(grid, df_route, snap, lead_now):
    """时刻段对比。历史列必须限定在 lead_now±7 —— 否则把 lead 45 和 lead 2
    混在一起，晚班会被临期全价舱抬高，得出与事实相反的结论。

    注意 lead_grid 的输出不含 dep_slot（它只保留 traj_key/lead/price 等），
    所以时刻要从 df 里按 traj_key 映射回来。
    """
    order = ["早(<9)", "上午(9-12)", "中午(12-14)", "下午(14-18)", "晚(≥18)"]
    hist = {}
    if not grid.empty and not df_route.empty:
        slot_map = df_route.drop_duplicates("traj_key").set_index("traj_key")["dep_slot"]
        w = grid[(grid.lead_days >= lead_now - 7) & (grid.lead_days <= lead_now + 7)].copy()
        w["dep_slot"] = w.traj_key.map(slot_map)
        w = w.dropna(subset=["dep_slot"])
        if len(w):
            dm = w.groupby(["flight_date", "lead_days"])["price"].transform("min")
            w = w.assign(gap=w.price - dm)
            hist = w.groupby("dep_slot").agg(med=("price", "median"),
                                             gap=("gap", "median"), n=("price", "size"))
    rows = []
    for sl in order:
        cur = snap[snap.dep_slot == sl] if "dep_slot" in snap.columns else pd.DataFrame()
        h = hist.loc[sl] if sl in getattr(hist, "index", []) else None
        rows.append({
            "slot": sl,
            "n_flights_now": int(len(cur)),
            "price_min_now": _i(cur.price.min()) if len(cur) else None,
            "price_median_hist": _i(h["med"]) if h is not None else None,
            "premium_median_hist": _i(h["gap"]) if h is not None else None,
            "n_obs_hist": int(h["n"]) if h is not None else 0,
        })
    return rows


def _rank_candidates(cands, airline_tbl, airport_tbl, slot_tbl, floor_now, top_n):
    """排序只用相对量，不用任何绝对金额阈值。"""
    slot_pen = {r["slot"]: (r["premium_median_hist"] or 0) for r in slot_tbl}
    _ap = airport_tbl or {}
    ap_diff = abs(_ap.get("median_diff") or 0)
    cheap_ap = _ap.get("cheaper") if _ap.get("available") else None

    scored = []
    for c in cands:
        s = float(c["price"])
        if ap_diff and cheap_ap and c["cmp_airport"] and c["cmp_airport"] != cheap_ap:
            s += ap_diff * 0.5
        s += (slot_pen.get(c["dep_slot"]) or 0) * 0.3
        if "ARRIVES_NEXT_DAY" in c["flags"]:
            s += floor_now * 0.08
        if "FULL_FARE_JUMP" in c["flags"]:
            s += c["price"] * 0.5
        scored.append((s, c))
    scored.sort(key=lambda x: x[0])

    def reason(c):
        bits = []
        if c["premium_vs_floor"] == 0:
            bits.append("当日最低价")
        else:
            bits.append(f"比当日最低贵 ¥{c['premium_vs_floor']}")
        if cheap_ap and c["cmp_airport"]:
            bits.append(f"{c['cmp_airport']}场次"
                        + (f"（历史比另一机场中位低 ¥{ap_diff:.0f}）"
                           if c["cmp_airport"] == cheap_ap else
                           f"（历史中位贵 ¥{ap_diff:.0f}）"))
        sl = next((r for r in slot_tbl if r["slot"] == c["dep_slot"]), None)
        if sl and sl["premium_median_hist"] is not None:
            bits.append(f"{c['dep_slot']}（该时段历史中位 ¥{sl['price_median_hist']}）")
        if "PLATFORM_PRICE" in c["flags"]:
            bits.append("平台价档位，非促销")
        if "FULL_FARE_JUMP" in c["flags"]:
            bits.append("全价舱口径跳变")
        if "ARRIVES_NEXT_DAY" in c["flags"]:
            bits.append("次日到达")
        al = next((a for a in airline_tbl if a["airline"] == c["airline"]), None)
        if al:
            bits.append(f"{c['airline']}历史 {al['floor_win_rate']*100:.0f}% 的时点是最低价")
            if (al.get("censored_rate") or 0) > 0.04:
                bits.append("但其低舱最早售罄，看中别拖")
        return "；".join(bits)

    rec = []
    for rank, (_, c) in enumerate(scored[:max(1, top_n)], 1):
        rec.append(dict(c, rank=rank, reason=reason(c)))
    avoid = [dict(c, reason="全价舱口径跳变，比当日最低贵 "
                            f"{c['premium_pct']:.0f}%")
             for _, c in scored if "FULL_FARE_JUMP" in c["flags"]][:3]
    return {"recommended": rec, "avoid": avoid}


# ---------------------------------------------------------------------------
# 决策
# ---------------------------------------------------------------------------
def _decide_timing(lead_now, pct, conf, trend, dis_worst, dis_near, season, freshness):
    """确定性决策矩阵。first-match-wins，可单测，不含任何绝对金额。"""
    reasons = []
    if lead_now is None or lead_now <= 0:
        return "departed_or_today", ["已起飞或当天"]
    if conf == "insufficient":
        return "insufficient_data", ["同期样本不足，无法给出分位"]
    if freshness in ("old", "very_old"):
        return "insufficient_data", [f"数据{freshness}，价格可能已变，不宜据此决策"]
    if lead_now <= 3:
        reasons.append(f"距起飞仅 {lead_now} 天，临期涨价是主旋律")
        return "buy_now", reasons
    if lead_now <= 7:
        reasons.append(f"距起飞 {lead_now} 天，已进入提前一周——历史上涨占多数")
        if dis_worst and dis_worst >= 0.10:
            reasons.append(f"且等待期内有 {dis_worst*100:.0f}% 的航班消失")
        return "buy_now", reasons
    if pct is None:
        return "insufficient_data", ["无法定位分位"]
    # 低价舱正在消失 + 价格台阶抬升 → 催促。但『偏贵』不能被催促盖掉：
    # 现价已经是同期最贵时，说『赶紧买』是自相矛盾的，应改为『尽快决定或改期』。
    if trend == "rising" and (dis_near or 0) >= 0.10:
        if pct <= 50:
            reasons.append(f"价格台阶在抬升，且 {dis_near*100:.0f}% 的低价航班已消失")
            return "buy_soon", reasons
        reasons.append(f"偏贵（{pct:.0f}% 分位）但时间已紧，"
                       f"且 {dis_near*100:.0f}% 的低价航班已消失")
        return "wait_risky", reasons
    if pct <= 25:
        reasons.append(f"现价处于同期 {pct:.0f}% 分位，低于 P25")
        return "buy", reasons
    if pct <= 50:
        reasons.append(f"现价处于同期 {pct:.0f}% 分位（P25–P50 之间）")
        return "neutral", reasons
    if pct <= 75:
        reasons.append(f"现价处于同期 {pct:.0f}% 分位（P50–P75 之间），偏贵")
        return "wait", reasons
    reasons.append(f"现价处于同期 {pct:.0f}% 分位，高于 P75")
    if lead_now > 30:
        reasons.append("但距起飞还早，历史最低价多出现在提前 20–28 天")
    if season and season.get("is_peak"):
        reasons.append("该起飞日是高峰日（节假日效应），偏贵属结构性")
    return "wait", reasons


VERDICT_LABEL = {
    "buy_now": "现在就买", "buy_soon": "尽快买，别等了", "buy": "可以下手",
    "neutral": "中性，可再等 1–2 天", "wait": "观望，偏贵",
    "wait_risky": "偏贵且时间紧，尽快决定或改期",
    "insufficient_data": "数据不足，无法判断", "departed_or_today": "已起飞/当天",
    "unsupported": "无法分析",
    "no_eligible_flight": "约束下无可选航班",
}


def advise(route_from, route_to, flight_date, as_of=None, db=None, use_cache=True,
           constraints=None):
    """单日完整决策。返回可直接 json.dumps 的 dict。

    constraints: {"arrive_by": "16:00", "max_price": 520} —— 任一可省略。
    传入后，快照与**历史 floor 分布**同时收窄到满足约束的航班集合，
    因此 percentile / verdict 说的都是『你买得到的那几班』。
    """
    db = db or C.DB_FILE
    df_all = load_df_all(db, as_of, use_cache)
    R = load_route(df_all, route_from, route_to, as_of, use_cache)
    label = R["label"] or f"{route_from}->{route_to}"

    con, con_bad = normalize_constraints(constraints)

    out = {
        "schema_version": SCHEMA_VERSION,
        "query": {"route": f"{route_from}->{route_to}", "route_label": label,
                  "flight_date": flight_date, "as_of": as_of,
                  "constraints": con},
        "warnings": [f"⚠️ {b}" for b in con_bad],
    }

    fresh = data_freshness(df_all, as_of, db)
    out["data_as_of"] = fresh
    cov = route_coverage(R["df"], label)

    if R["df"].empty:
        out["verdict"] = "unsupported"
        out["verdict_label"] = VERDICT_LABEL["unsupported"]
        out["why"] = f"数据库中没有 {route_from}->{route_to} 这条航线"
        out["coverage"] = cov
        return out

    quality = data_quality(R["df"], R["grid"])
    out["data_quality"] = quality
    out["coverage"] = cov
    out["warnings"].extend(quality.get("warnings", []))
    if cov["grade"] in ("sparse", "insufficient"):
        out["warnings"].append(cov["note"])
    if fresh["freshness"] in ("stale", "old", "very_old"):
        out["warnings"].append(
            f"⚠️ 数据{fresh['freshness_label']}：{fresh['note']}")

    snap, snap_t = _snapshot(R["df"], flight_date)
    if snap is None:
        out["verdict"] = "unsupported"
        out["verdict_label"] = VERDICT_LABEL["unsupported"]
        out["why"] = (f"数据库中没有 {label} {flight_date} 的观测。"
                      f"该航线覆盖 {R['df'].flight_date.nunique()} 个起飞日，"
                      f"范围 {R['df'].flight_date.min()} ~ {R['df'].flight_date.max()}")
        return out

    # --- 用户约束：在 df 层过滤后重建全部派生帧 ---
    n_flights_all = int(len(snap))
    floor_all = float(snap.price.min())
    if con is not None and con.get("arrive_by"):
        R_full = R  # 参照帧：删失判定必须看全部航班，而不是筛剩的早班
        flights_here = sorted(snap.flight_no.unique().tolist())
        built, f_info = constrained_route(route_from, route_to, as_of, db,
                                          con["arrive_by"], R_full)
        con.update(f_info)
        con.update({"n_flights_before": n_flights_all,
                    "floor_now_unconstrained": _i(floor_all),
                    "budget_note": ("--max-price 是预算口径，不参与筛选；"
                                    "它只用于报告同期有多少比例落在预算内"),
                    "basis": ("到达时刻在【观测级】过滤后重建 grid/floor/traj，"
                              "历史参照集因此仍覆盖全部起飞日")})
        if built is None:
            out["constraints"] = con
            out["verdict"] = "no_eligible_flight"
            out["verdict_label"] = VERDICT_LABEL["no_eligible_flight"]
            out["why"] = (f"{label} 全库都没有 {con['arrive_by']} 前落地的航班。")
            return out
        R = built
        snap, snap_t = _snapshot(R["df"], flight_date)
        if snap is None:
            con["excluded_here"] = flights_here
            out["constraints"] = con
            out["verdict"] = "no_eligible_flight"
            out["verdict_label"] = VERDICT_LABEL["no_eligible_flight"]
            out["why"] = (f"{label} {flight_date} 没有 {con['arrive_by']} 前落地的航班。"
                          f"该航线历史上共 {f_info['n_dates_after']} 个起飞日"
                          f"出现过符合条件的航班，但都不是这一天。"
                          f"当日全部 {n_flights_all} 班最低 ¥{_i(floor_all)}。")
            return out
        con["n_flights_after"] = int(len(snap))
        con["floor_now_constrained"] = _i(snap.price.min())
        # 当日（而非全库）被排除的航班号：otherwise 会把其它日期的航班、
        # 甚至本日符合条件的航班都列进来，完全是误导。
        con["excluded_here"] = sorted(set(flights_here) - set(snap.flight_no.unique()))
        con["kept_here"] = sorted(snap.flight_no.unique().tolist())
        out["constraints"] = con

    floor_now = float(snap.price.min())
    lead_now = int(snap.lead_days.iloc[0])
    out["snapshot"] = {"crawl_time": str(snap_t), "lead_days": lead_now,
                       "floor_now": _i(floor_now), "n_flights": int(len(snap))}

    # 选班：即使分位算不出，相对结构（航司/机场/时刻）仍然有价值
    out["selection"] = select_flights(snap, R["grid"], R["df"], R["traj"], R["floor_int"],
                                      label, flight_date, lead_now, quality)

    out["censoring"] = censoring_signal(R["traj"], label, flight_date, R["floor_int"])
    out["floor_trend"] = floor_trend(R["floor_int"], label, flight_date, lead_now)
    out["season"] = season_context(R["floor_int"], label, flight_date)

    # 分位
    if cov["grade"] in ("sparse", "insufficient"):
        pr = {"ok": False, "confidence": "insufficient", "why": cov["note"]}
    elif (quality.get("page_level_pollution") or {}).get("flagged"):
        pr = {"ok": False, "confidence": "insufficient",
              "why": "该航线疑似页面级起价，EDA 已排除，advisor 同样不采信其分位"}
    else:
        pr = percentile_report(R["floor_int"], label, flight_date, lead_now, floor_now)
    out["timing"] = pr

    if con is not None and con.get("max_price"):
        out["budget"] = budget_report(R["floor_int"], label, flight_date,
                                      lead_now, con["max_price"], floor_now)

    waits = wait_analysis(R["grid"], label, lead_now)
    out["wait_analysis"] = waits
    dis_worst = max([w.get("disappear_rate") or 0 for w in waits], default=0) or None

    # 决策只看『现实会考虑的短等待』的消失率。用最大消失率会让规则几乎恒真
    # ——因为『等 14 天』的消失率天然很高，连 99% 分位都会被判成『赶紧买』。
    # 必须同时要求 disappear_rate 存在：配对数不足的 horizon 只返回 4 个字段
    # （见 wait_analysis 的 insufficient 分支），直接取键会 KeyError。
    near = [w for w in waits if w.get("disappear_rate") is not None
            and w.get("n_paired") and w["horizon_days"] <= 7]
    dis_near = (max(near, key=lambda w: w["horizon_days"])["disappear_rate"]
                if near else (waits[0].get("disappear_rate") if waits else None))

    verdict, reasons = _decide_timing(
        lead_now, pr.get("percentile"), pr.get("confidence"),
        out["floor_trend"].get("direction"), dis_worst, dis_near, out["season"],
        fresh["freshness"])
    out["verdict"] = verdict
    out["verdict_label"] = VERDICT_LABEL[verdict]
    out["reasons"] = reasons

    # 有约束时，必须在最前面说清『这个结论是对哪几班说的』——
    # 否则读者会以为它还是针对当日最低价，而那可能根本不是他能买的航班。
    if out.get("constraints"):
        c = out["constraints"]
        out["reasons"].insert(0,
            f"已按约束筛选（{c['arrive_by']} 前落地）："
            f"{c['n_flights_after']}/{c['n_flights_before']} 班，"
            f"本结论只针对这 {c['n_flights_after']} 班")
        if (c.get("floor_now_unconstrained") is not None
                and c.get("floor_now_constrained") is not None
                and c["floor_now_unconstrained"] < c["floor_now_constrained"]):
            out["warnings"].insert(0,
                f"约束把当日最低价从 ¥{c['floor_now_unconstrained']} 抬到 "
                f"¥{c['floor_now_constrained']}（+¥{c['floor_now_constrained']-c['floor_now_unconstrained']}）"
                f"——未被约束的日级分位对你不适用，上面的分位已按约束重算")

    if dis_worst and dis_worst >= 0.25:
        out["warnings"].append(
            f"🚨 幸存者偏差：等到更晚的提前量时，{dis_worst*100:.0f}% 的现有航班会消失，"
            f"『涨跌率』只在幸存者里统计")
    if (pr.get("scope_spread") or {}).get("range") and pr["scope_spread"]["range"] > 25:
        out["warnings"].append(
            f"参照集敏感：换不同历史范围，分位摆动 {pr['scope_spread']['range']:.0f} 个百分点"
            f"（已下调置信度至 {pr['scope']['confidence']}）")
    if pr.get("holiday_contamination"):
        out["warnings"].append("⚠️ " + pr["holiday_contamination"]["note"])
    if out["season"].get("is_peak"):
        out["warnings"].append(
            f"🎌 高峰日：{out['season']['note']}，预算需上调，不适用平日的提前量规律")
    if out["censoring"]["verdict"] == "cheap_seats_disappearing":
        out["warnings"].append(
            f"低价航班已提前消失（{out['censoring']['n_censored']} 班），floor 抬高部分是售罄而非涨价")

    return out


def scan(route_from, route_to, days=30, as_of=None, db=None, use_cache=True,
         constraints=None, start=None):
    """扫描未来 N 天，按起飞日给出逐日建议。一次载入，多日复用。

    start 给定时从该起飞日起算（默认从『今天+1』起算）。没有它，一个
    4 周后的日期根本进不了 --scan 的窗口，而 --scan 又正是 SKILL 推荐给
    『日期还没定』用户的主力工具。
    """
    db = db or C.DB_FILE
    df_all = load_df_all(db, as_of, use_cache)
    base = pd.Timestamp(start) if start else (
        pd.Timestamp(as_of) if as_of else df_all.crawl_dt.max())
    lo = (base + (pd.Timedelta(days=0) if start else pd.Timedelta(days=1))).strftime("%Y-%m-%d")
    hi = (base + pd.Timedelta(days=days)).strftime("%Y-%m-%d")

    R = load_route(df_all, route_from, route_to, as_of, use_cache)
    dates = sorted(R["df"][(R["df"].flight_date >= lo) & (R["df"].flight_date <= hi)]
                   .flight_date.unique())
    return [advise(route_from, route_to, d, as_of, db, use_cache, constraints)
            for d in dates]


# 纯函数的不变量测试：与库无关，任何机器上都该通过
_PURE_CASES = [
    ("norm_hhmm", norm_hhmm, [
        ("16:00", "16:00"), ("16", "16:00"), ("1600", "16:00"),
        ("9:5", "09:05"), ("16点", "16:00"), ("16：30", "16:30"),
        ("24:00", None), ("abc", None), (None, None),
    ]),
    ("airport_label", C.airport_label, [
        ("大兴国际机场", "大兴"), ("首都国际机场T3", "首都"),
        ("栎社国际机场T1", "栎社"), ("栎社国际机场T2", "栎社"),
        ("高崎国际机场T4", "高崎"), ("泉州晋江国际机场", "泉州晋江"),
        ("普陀山机场", "普陀山"), ("", None),
    ]),
    ("arrival_key", arrival_key, [
        ("14:10", "14:10"), ("22:40", "22:40"), ("00:45 +1天", None),
    ]),
]


def _selftest_pure():
    """纯函数不变量——换库、换机器都必须通过。"""
    def same(a, b):
        # NaN 与 None 视为等价：airport_label 用 np.nan 表示"解析不出"，
        # 直接 != 比较会把正确行为判成失败。
        an = a is None or (isinstance(a, float) and np.isnan(a))
        bn = b is None or (isinstance(b, float) and np.isnan(b))
        return an and bn if (an or bn) else a == b

    ok = True
    for name, fn, cases in _PURE_CASES:
        for src, exp in cases:
            got = fn(src)
            if not same(got, exp):
                print(f"FAIL {name}({src!r}) = {got!r}，期望 {exp!r}")
                ok = False
    if ok:
        total = sum(len(c) for _, _, c in _PURE_CASES)
        print(f"OK   纯函数不变量 {total} 条"
              f"（{', '.join(n for n, _, _ in _PURE_CASES)}）")
    return ok


def _selftest_invariants(sample_per_route=1):
    """跨库通用不变量：在任何非空库上都应成立的结构性约束。

    不做金额断言——那正是换库必挂的原因。
    """
    ok = True
    try:
        df_all = load_df_all(C.DB_FILE, None, use_cache=False)
    except Exception as e:
        print(f"SKIP 通用不变量：无法载入数据库（{type(e).__name__}: {e}）")
        return True
    if df_all.empty:
        print("SKIP 通用不变量：库为空")
        return True

    checked = 0
    for (rf, rt), g in df_all.groupby(["route_from", "route_to"], sort=False):
        dates = sorted(g.flight_date.unique())
        for d in dates[-sample_per_route:]:
            try:
                a = advise(rf, rt, d, None, C.DB_FILE, use_cache=False)
            except Exception as e:
                print(f"FAIL {rf}->{rt} {d}: advise 抛异常 {type(e).__name__}: {e}")
                ok = False
                continue
            if a.get("verdict") in ("unsupported", "no_eligible_flight"):
                continue
            checked += 1
            line = f"{rf}->{rt} {d}"
            t = a.get("timing") or {}
            if t.get("ok"):
                p = t.get("percentile")
                if p is None or not (0 <= p <= 100):
                    print(f"FAIL {line}: percentile 越界 {p!r}"); ok = False
                for q in (5, 25, 50, 75, 95):
                    if f"P{q:02d}" not in (t.get("reference") or {}):
                        print(f"FAIL {line}: reference 缺 P{q:02d}"); ok = False
            s = a.get("snapshot") or {}
            cands = (a.get("selection") or {}).get("candidates") or []
            if cands and s.get("floor_now") != min(c["price"] for c in cands):
                print(f"FAIL {line}: floor_now 与候选中最低价不一致"); ok = False
            for w in (a.get("wait_analysis") or []):
                for k in ("disappear_rate", "down_rate", "up_rate"):
                    v = w.get(k)
                    if v is not None and not (0 <= v <= 1):
                        print(f"FAIL {line}: wait_analysis.{k} 越界 {v!r}"); ok = False
            c = a.get("constraints")
            if c and c.get("floor_now_constrained") is not None:
                if c["floor_now_constrained"] < c["floor_now_unconstrained"]:
                    print(f"FAIL {line}: 约束后最低价不应低于未约束"); ok = False
    if ok:
        print(f"OK   通用不变量：{checked} 个日期通过（routes={df_all.groupby(['route_from','route_to']).ngroups}）")
    return ok


def self_test(verbose=True):
    """口径自检 = 纯函数不变量 + 跨库不变量 + 黄金回归。

    黄金回归的期望值是 `D:/Flight-Monitor` 那份库的历史事实（§3 表），
    换一份库必然对不上——所以**库不对时只 SKIP 并说明，不算失败**，
    否则别人拿到这个工具第一步就是红的。
    """
    as_of = "2026-09-03 11:29:22"
    cases = [
        # route,        date,        期望 floor, n, P25,    P50,    P75,    band
        ("bjs", "jjn", "2026-09-04", 300, 7, 357.5, 475.0, 560.0, "≤25%·偏便宜"),
        ("jjn", "bjs", "2026-09-04", 400, 7, 362.5, 445.0, 580.0, "25–50%"),
        ("jjn", "bjs", "2026-09-07", 350, 7, 350.0, 430.0, 500.0, "≤25%·偏便宜"),
    ]
    ok = _selftest_pure()

    df_hist = load_df_all(C.DB_FILE, as_of)
    have = set(zip(df_hist.route_from, df_hist.route_to))
    missing = [c for c in cases if (c[0], c[1]) not in have]
    if missing:
        print(f"SKIP 黄金回归：本库不含基线航线 "
              f"{'、'.join(f'{c[0]}->{c[1]}' for c in missing)}"
              f"（基线只对 D:/Flight-Monitor 那份库有效）")
    else:
        for rf, rt, d, floor, n, p25, p50, p75, band in cases:
            # 用粗桶口径复现 notebook —— 这是回归基线，不是默认路径
            R = load_route(df_hist, rf, rt, as_of)
            snap, _ = _snapshot(R["df"], d)
            line = f"{rf}->{rt} {d}"
            if snap is None:
                print(f"SKIP 黄金回归 {line}：该库此刻无此快照")
                continue
            got_floor = float(snap.price.min())
            got_n = int(len(snap))
            fb = R["floor_bin"]
            fb = fb[fb.route_label == R["label"]]
            if fb.empty:
                print(f"FAIL {line}: floor_bin 为空"); ok = False; continue
            b = C.lead_bin(int(snap.lead_days.iloc[0]))
            # 基准必须跨全部起飞日统计（§3 表口径），只按 lead_bin 分组，不按目标日过滤
            hist = fb[fb.lead_bin == b]["floor"]
            if hist.empty:
                print(f"FAIL {line}: 粗桶 {b} 无基准"); ok = False; continue
            got = (got_floor, got_n, float(hist.quantile(.25)),
                   float(hist.quantile(.5)), float(hist.quantile(.75)))
            exp = (floor, n, p25, p50, p75)
            if got != exp:
                print(f"FAIL {line}: got {got} want {exp}"); ok = False
            else:
                print(f"OK   黄金回归 {line}: floor={got[0]:.0f} n={got[1]} "
                      f"P25/P50/P75={got[2]:.1f}/{got[3]:.1f}/{got[4]:.1f} ({band})")

    ok = _selftest_invariants() and ok
    return ok


if __name__ == "__main__":
    import sys
    sys.exit(0 if self_test() else 1)
