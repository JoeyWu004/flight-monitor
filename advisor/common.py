# -*- coding: utf-8 -*-
"""
特征工程与轨迹聚合（对数据库全程只读）

从 eda/0903/common.py 拷贝而来，此后与 eda/ 分家。相对原版有三处改动：
  1. find_db 增加以 __file__ 为起点的兜底查找；
  2. 删除死代码 prices_at_leads（原版和 notebook 都没用到）；
  3. 新增 as_of_filter（回放截断）与 floor_by_int_lead（整数 lead 的 floor，
     滑动窗口分位的输入——粗桶 floor_by_lead 保留但只作对照）。

所有连接使用 sqlite3 URI mode=ro，绝不写回 flight_monitor.db。
派生数据只落在 advisor/core.py 管理的 advisor/cache/。
"""
import os
import re
import sqlite3

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# 数据库定位（不硬编码，支持从子目录运行）
# ---------------------------------------------------------------------------
def find_db(start=None):
    """向上逐级查找 flight_monitor.db，找到返回绝对路径。

    先按 start（默认 cwd）向上找；找不到再以本文件所在目录为起点找一次，
    这样 advisor/ 在任意 cwd 下被调用（比如被 Claude Code 从别处 import）都能定位。
    """
    candidates = [os.path.abspath(start or os.getcwd())]
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in candidates:
        candidates.append(here)
    for d in candidates:
        for _ in range(6):
            p = os.path.join(d, "flight_monitor.db")
            if os.path.exists(p):
                return p
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
    raise FileNotFoundError("未找到 flight_monitor.db，请从项目根目录或其子目录运行。")

DB_FILE = find_db()

# ---------------------------------------------------------------------------
# 表加载（只读）
# ---------------------------------------------------------------------------
def load_flight_prices(db=None):
    db = db or DB_FILE
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    df = pd.read_sql_query(
        "SELECT route_from, route_to, route_from_name, route_to_name, "
        "flight_date, flight_no, airline, aircraft_type, "
        "departure_airport, arrival_airport, departure_time, arrival_time, "
        "price, crawl_time "
        "FROM flight_prices", con)
    con.close()
    return df


def load_price_alerts(db=None):
    db = db or DB_FILE
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    df = pd.read_sql_query(
        "SELECT * FROM price_alerts", con)
    con.close()
    return df


def load_monitor_log(db=None):
    db = db or DB_FILE
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    df = pd.read_sql_query(
        "SELECT * FROM monitor_log", con)
    con.close()
    return df

# ---------------------------------------------------------------------------
# 特征工程
# ---------------------------------------------------------------------------
# 机场名归一：不写死任何城市/机场，纯按中文命名规律拆解，
# 这样任何航线（虹桥/浦东、双流/天府、首都/大兴…）都能自动识别多机场城市。
#
# 必须处理的两个坑：
#   1. 航站楼后缀 —— 『栎社国际机场T1』与『栎社国际机场T2』是**同一个机场**，
#      不剥掉 T{n} 会把宁波误判成"双机场城市"。
#   2. 『国际』二字 —— 『大兴国际机场』与『大兴机场』要归到同一个标签。
_TERMINAL_RE = re.compile(r"[\s\-]*[Tt]\d+$")


def airport_label(name):
    """机场全名 → 机场标签（剥掉航站楼、『国际』、『机场』）。

    大兴国际机场→大兴 · 首都国际机场T3→首都 · 栎社国际机场T1→栎社
    高崎国际机场T4→高崎 · 泉州晋江国际机场→泉州晋江 · 普陀山机场→普陀山
    无法解析返回 NaN。
    """
    if name is None or (isinstance(name, float) and np.isnan(name)):
        return np.nan
    s = str(name).strip()
    if not s or s.lower() == "nan":
        return np.nan
    s = _TERMINAL_RE.sub("", s)
    i = s.find("机场")
    if i > 0:
        s = s[:i]
    if s.endswith("国际"):
        s = s[:-2]
    s = s.strip(" -_·")
    return s if s else np.nan


def add_features(df):
    """原地给原始表加派生列（返回副本，不改库）。

    新增列：
      crawl_dt / flight_dt     日期时间
      lead_days                观察时距起飞的天数（大 = 早看，0/- 表示临近/已过）
      flight_dow / flight_month 起飞日星期/月份
      dep_hour / dep_slot      出发时刻段
      dep_ap / arr_ap          两端机场标签（通用解析，见 airport_label）
      route_label              中文航向
    """
    df = df.copy()
    df["crawl_dt"] = pd.to_datetime(df["crawl_time"])
    df["flight_dt"] = pd.to_datetime(df["flight_date"])
    df["lead_days"] = (df["flight_dt"].dt.normalize()
                       - df["crawl_dt"].dt.normalize()).dt.days
    df["flight_dow"] = df["flight_dt"].dt.dayofweek   # 0=周一
    df["flight_month"] = df["flight_dt"].dt.month
    df["dep_hour"] = pd.to_numeric(df["departure_time"].astype(str).str[:2],
                                   errors="coerce")

    def slot(h):
        if pd.isna(h):
            return "未知"
        if h < 9:
            return "早(<9)"
        if h < 12:
            return "上午(9-12)"
        if h < 14:
            return "中午(12-14)"
        if h < 18:
            return "下午(14-18)"
        return "晚(≥18)"
    df["dep_slot"] = df["dep_hour"].map(slot)

    # 两端机场各出一列标签，由 core 按航线判定哪一端是『多机场城市』。
    # 不在列里写死任何城市名——换成虹桥/浦东、双流/天府一样能识别。
    df["dep_ap"] = df["departure_airport"].map(airport_label)
    df["arr_ap"] = df["arrival_airport"].map(airport_label)

    name_map = {}
    for r in df.itertuples():
        name_map.setdefault((r.route_from, r.route_to), (r.route_from_name, r.route_to_name))
    route_label = {}
    for (a, b), (na, nb) in name_map.items():
        route_label[f"{a}->{b}"] = f"{na}→{nb}"
    df["route_label"] = df["route_from"] + "->" + df["route_to"]
    df["route_label"] = df["route_label"].map(route_label)
    df["traj_key"] = (df["route_from"] + "|" + df["route_to"] + "|"
                      + df["flight_no"] + "|" + df["flight_date"])
    return df


# ---------------------------------------------------------------------------
# 轨迹级聚合
# ---------------------------------------------------------------------------
def trajectory_summary(df, ref_df=None):
    """每个 (航向, 航班, 起飞日) 一条，汇总采样数、起止、价格低点等。

    censored（删失/疑似售罄）判定：
      - 该航班最后一次出现的时间 early_end
      - 而同一(航向, 起飞日)下其它航班在更晚的爬取里仍出现（说明监控还在跑）
      - 且当时尚未起飞
    满足则视为“提前消失”，最可能的原因是航班售罄/停止售卖。

    ref_df：『监控还在跑』的参照帧。带约束分析时 df 只剩部分航班，
    若用它自己算基准，会把『其它早班也没被爬到』误判成售罄——
    所以那时必须传入未过滤的全量帧。
    """
    g = df.sort_values(["traj_key", "crawl_dt"]).groupby("traj_key", sort=False)
    s = pd.DataFrame({
        "n_samples": g.size(),
        "first_crawl": g["crawl_dt"].min(),
        "last_crawl": g["crawl_dt"].max(),
        "n_days_observed": (g["crawl_dt"].max() - g["crawl_dt"].min()).dt.days + 1,
        "price_min": g["price"].min(),
        "price_max": g["price"].max(),
        "price_first": g["price"].first(),
        "price_last": g["price"].last(),
        "n_price_levels": g["price"].nunique(),
        "flight_date": g["flight_date"].first(),
        "flight_no": g["flight_no"].first(),
        "airline": g["airline"].first(),
        "route_from": g["route_from"].first(),
        "route_to": g["route_to"].first(),
        "route_label": g["route_label"].first(),
        "departure_airport": g["departure_airport"].first(),
        "arrival_airport": g["arrival_airport"].first(),
        "lead_at_last": g["lead_days"].last(),
        "lead_at_first": g["lead_days"].first(),
    }).reset_index()
    # 同(航向,起飞日)全局最晚爬取时间（参照帧，默认用自身）
    last_by_date = (ref_df if ref_df is not None else df).groupby(
        ["route_label", "flight_date"])["crawl_dt"].max()
    s["global_last_crawl"] = s.apply(
        lambda r: last_by_date.get((r.route_label, r.flight_date), pd.NaT), axis=1)
    # 起飞日是否晚于监控结束（这条轨迹本可继续被看到）
    s["flight_after_monitor"] = (pd.to_datetime(s["flight_date"])
                                 > s["global_last_crawl"].dt.normalize())
    # 删失 = 提前消失(比同组最晚爬取至少早 1 天) 且当时还没起飞
    s["censored"] = ((s["last_crawl"] < s["global_last_crawl"] - pd.Timedelta(days=1))
                     & s["flight_after_monitor"])
    # 提前消失时还剩多少天起飞
    s["soldout_lead"] = np.where(
        s["censored"],
        ((pd.to_datetime(s["flight_date"]) - s["last_crawl"].dt.normalize()).dt.days),
        np.nan)
    return s


def lead_bin(x):
    """把距起飞天数归成展示用分桶（供买时点/快照级价格分布使用）。"""
    if pd.isna(x):
        return "已过/未知"
    x = int(x)
    if x >= 45:
        return "45天前"
    if x >= 30:
        return "30-44天"
    if x >= 21:
        return "21-29天"
    if x >= 14:
        return "14-20天"
    if x >= 7:
        return "7-13天"
    if x >= 4:
        return "4-6天"
    if x >= 1:
        return "1-3天"
    if x == 0:
        return "起飞当天"
    return "已起飞"


LEAD_BIN_ORDER = ["45天前", "30-44天", "21-29天", "14-20天", "7-13天",
                  "4-6天", "1-3天", "起飞当天", "已起飞"]


# 监控实际能观察到的 lead 上限（config.MONITOR_DAYS_AHEAD 为 30，
# 加上告警航线会提前更久开始盯，实测最大值约 41）。
# LEAD_BIN_ORDER 里的 "45天前" 桶因此恒为空，仅作展示兼容保留。
FLOOR_LEAD_MAX = 90


def as_of_filter(df, as_of):
    """把 DataFrame 截断到 as_of 时刻之前（含），用于回放历史某一批。

    as_of 为 None 时原样返回。这是 §3 表对拍能逐位复现的关键：
    所有下游派生（today 快照 / grid / traj / floor）都必须从截断后的 df 出发。
    """
    if as_of is None:
        return df
    return df[df["crawl_dt"] <= pd.Timestamp(as_of)].copy()


def floor_by_int_lead(grid):
    """按 (航向, 起飞日, 距起飞整数天数) 求当日全网最低价 floor。

    与 floor_by_lead 的区别：不做 lead_bin 粗分桶，保留整数 lead。
    这是滑动窗口分位（core.floor_distribution）的输入——粗桶会让
    "提前1天"和"提前3天"共用同一个 P25，实测北京→泉州"1-3天"桶内
    每个起飞日的分位数完全相同，无法区分。
    """
    f = (grid.groupby(["route_label", "flight_date", "lead_days"])
          .agg(floor=("price", "min"), n_flights=("flight_no", "nunique"))
          .reset_index())
    return f


def lead_grid(df, max_lead=90):
    """把每条轨迹变成『按距起飞整数天数(lead)展开的价格台阶』。

    做法：该轨迹在 [首次爬取日, 末次爬取日] 内逐日取“最近一次已知价”
    (价格在其间视为不变, 但不会外推到没爬的日子) → 得到整数 lead 网格。
    删失(提前消失)的轨迹只覆盖到它真正消失那天, 不会造假“临期价”。

    返回: traj_key, lead_days, price, route_label, flight_date, airline, flight_no
    注意: 只在监控存在的 lead 范围里有值; 末尾可能因删失而没有临期价。
    """
    out = []
    g = df.sort_values("crawl_dt").groupby("traj_key", sort=False)
    for key, sub in g:
        fd = pd.to_datetime(sub["flight_date"].iloc[0]).normalize()
        s = (sub.groupby(sub["crawl_dt"].dt.normalize())["price"].last().sort_index())
        first, last = s.index.min(), s.index.max()
        rng = pd.date_range(first, last, freq="D")
        s = s.reindex(rng).ffill()
        leads = (fd - rng).days
        keep = (leads >= 0) & (leads <= max_lead)
        if keep.sum() == 0:
            continue
        sub2 = sub.iloc[0]
        for ld, px in zip(leads[keep], s[keep]):
            if pd.notna(px):
                out.append({"traj_key": key, "lead_days": int(ld), "price": float(px),
                            "route_label": sub2["route_label"],
                            "flight_date": sub2["flight_date"],
                            "flight_no": sub2["flight_no"],
                            "airline": sub2["airline"]})
    g2 = pd.DataFrame(out)
    if len(g2):
        g2["baseline"] = g2.groupby("traj_key")["price"].transform("median")
        g2["rel_price"] = 100.0 * g2["price"] / g2["baseline"]
    return g2


def floor_by_lead(grid):
    """按(航向, 起飞日, lead分桶)求『当日全网最低价』floor。

    含义：对某起飞日，若在距起飞 lead 天去查，全网能订到的最低价。
    来自 lead_grid——某个航班若已删失(疑似售罄)不会出现在后续 lead，
    因此便宜航班售罄后 floor 会自然抬高，这是我们希望捕捉的信号。
    """
    g = grid.copy()
    g["lead_bin"] = g["lead_days"].map(lead_bin)
    f = (g.groupby(["route_label", "flight_date", "lead_bin"])
          .agg(floor=("price", "min"), n_flights=("flight_no", "nunique"))
          .reset_index())
    return f


def lead_transitions(grid, targets=(45, 30, 21, 14, 10, 7, 4, 2, 1), tol=3):
    """每个轨迹在若干目标 lead 上的价格(取网格中与 target 最近的整数lead)。

    同一轨迹内比较不同 target 的价格 → 消除不同航班基准价差异，
    用于“从提前 X 天等到提前 Y 天，价是涨是跌”。
    """
    rows = []
    for key, sub in grid.groupby("traj_key", sort=False):
        s = sub.set_index("lead_days")["price"]
        rl = sub["route_label"].iloc[0]
        fd = sub["flight_date"].iloc[0]
        for t in targets:
            d = np.abs(s.index.to_numpy() - t)
            pos = int(np.argmin(d))
            j = s.index[pos]
            if abs(j - t) <= tol:
                rows.append({"traj_key": key, "route_label": rl, "flight_date": fd,
                             "target_lead": t, "price": float(s[j])})
    return pd.DataFrame(rows)


def ac_family(x):
    """机型 → 机型家族（用于横向比较）。"""
    s = str(x)
    for k in ["787", "C919", "MAX8", "738", "737", "321", "320"]:
        if k in s:
            return {"787": "波音787", "C919": "国产C919", "MAX8": "737MAX8",
                    "738": "波音738", "737": "波音737", "321": "空客321",
                    "320": "空客320"}[k]
    return "其他"


def change_events(df):
    """价格变动事件：同一轨迹相邻两次不同价格之间算一次变动。

    返回列：traj_key, at_dt, from_price, to_price, diff, pct,
            lead_at, direction(up/down), wait_hours(距上次变动小时)
    注意：连续相同价格的中间采样被折叠，只保留“价格真的变了”的跳变，
    同时每条轨迹的首次观测作为起点事件(等待/起始)。
    """
    rows = []
    df = df.sort_values(["traj_key", "crawl_dt"])
    for key, sub in df.groupby("traj_key", sort=False):
        prev_p = None
        prev_t = None
        prev_row = None
        for r in sub.itertuples():
            if prev_p is None:
                prev_p, prev_t, prev_row = r.price, r.crawl_dt, r
                continue
            if r.price != prev_p:
                # 记录上一次价格持续到本次变动的区间 [prev_t, r.crawl_dt)
                wait = (r.crawl_dt - prev_t).total_seconds() / 3600.0
                rows.append({
                    "traj_key": key,
                    "route_label": prev_row.route_label,
                    "flight_date": prev_row.flight_date,
                    "flight_no": prev_row.flight_no,
                    "from_price": prev_p,
                    "to_price": r.price,
                    "diff": r.price - prev_p,
                    "pct": (r.price - prev_p) / prev_p * 100 if prev_p else np.nan,
                    "lead_at": (pd.to_datetime(prev_row.flight_date).normalize()
                                - r.crawl_dt.normalize()).days,
                    "wait_hours": wait,
                    "direction": "down" if r.price < prev_p else "up",
                })
                prev_p, prev_t, prev_row = r.price, r.crawl_dt, r
    ev = pd.DataFrame(rows)
    return ev
