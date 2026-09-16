# -*- coding: utf-8 -*-
"""advisor.cli — 命令行入口（纯 I/O 薄壳，零业务逻辑）

所有阈值与判断都来自 core.py 的返回，这里只负责渲染。

退出码：0 正常 / 1 内部错误 / 2 参数错误 / 3 数据不足 / 4 数据陈旧
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# Windows 控制台默认 GBK，输出 ¥ / → / ≤ 会直接 UnicodeEncodeError 崩掉。
# 必须在任何输出之前重设编码。
for _s in ("stdout", "stderr"):
    try:
        getattr(sys, _s).reconfigure(encoding="utf-8")
    except Exception:
        pass

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

# 别人机器上最常见的失败就是解释器选错（系统 python 没装 pandas），
# 那种情况下原样抛 ImportError 完全看不出该怎么办。这里给一条能照做的出路。
try:
    import pandas  # noqa: F401
    import numpy  # noqa: F401
except ImportError as _e:
    sys.stderr.write(
        f"错误：当前 Python 缺少 {getattr(_e, 'name', 'pandas')}，无法运行 advisor。\n"
        f"  当前解释器：{sys.executable}\n"
        f"  请改用装了 pandas/numpy 的解释器，通常就是爬虫所用的那个环境。例如：\n"
        f"    • conda：conda activate <爬虫的环境名> && python {os.path.join(_HERE, 'cli.py')} ...\n"
        f"    • 直接指定：/path/to/envs/<env>/bin/python {os.path.join(_HERE, 'cli.py')} ...\n"
        f"    • Windows：C:/path/to/anaconda3/envs/<env>/python.exe {os.path.join(_HERE, 'cli.py')} ...\n"
        f"  自检：<那个python> -c \"import pandas, numpy\"\n")
    sys.exit(1)

import common as C  # noqa: E402
import core  # noqa: E402


def _log(msg):
    print(msg, file=sys.stderr)


def _die(msg, code=2):
    _log(f"错误：{msg}")
    return code


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------
def _md_card(a):
    q = a["query"]
    L = []
    add = L.append
    f = a["data_as_of"]

    add(f"## ✈️ {q['route_label']} · {q['flight_date']}")
    add("")
    v = a.get("verdict")
    if v:
        add(f"> **结论：{a['verdict_label']}**")
        for r in a.get("reasons", [])[:3]:
            add(f"> - {r}")
    add("")

    if a.get("why"):
        add(a["why"])
        add("")
        c = a.get("constraints")
        if c:
            nd = c.get("n_dates_after") or 0
            if nd:
                add(f"约束「{c['arrive_by']} 前落地」在该航线历史上有 {nd} 个起飞日"
                    f"出现符合的航班，但**这一天没有**。")
            else:
                add(f"约束「{c['arrive_by']} 前落地」在该航线全库范围内"
                    f"**从未出现过**符合的航班——这条约束对本航线无解。")
            if c.get("excluded_here"):
                add(f"本日全部航班：{'、'.join(c['excluded_here'])}")
            add("")
        cov = a.get("coverage") or {}
        if cov:
            add(f"覆盖率：{cov['n_snapshots']} 条快照 / {cov['n_flight_dates']} 个起飞日 / "
                f"{cov['n_batches']} 个批次（{cov['grade']}）")
        return "\n".join(L)

    # --- 数据新鲜度 ---
    icon = {"fresh": "✅", "stale": "⚠️", "old": "🔶", "very_old": "❌"}[f["freshness"]]
    add(f"**{icon} 数据**：截止 {f['crawl_time']}（{f['age_hours']:.1f} 小时前 · "
        f"{f['freshness_label']}）· {f['n_batches']} 批次 / {f['n_snapshots']:,} 快照")
    if f["last_monitor_run"]:
        add(f"　爬虫心跳：monitor_log 最后运行 {f['last_monitor_run']}"
            + ("（正常）" if f["crawler_alive"] else "（⚠️ 超过 24 小时未运行）"))
    add("")

    s = a.get("snapshot") or {}
    c = a.get("constraints")
    if c:
        add(f"**距起飞 {s.get('lead_days')} 天** · **你的约束下 {s.get('n_flights')} 班 · "
            f"最低 ¥{s.get('floor_now')}**（当日全部 {c['n_flights_before']} 班最低 "
            f"¥{c.get('floor_now_unconstrained')}）")
    else:
        add(f"**距起飞 {s.get('lead_days')} 天** · 当日 {s.get('n_flights')} 班 · "
            f"最低 ¥{s.get('floor_now')}")
    add("")

    if c:
        add(f"> 🎯 **已应用约束**：{c['arrive_by']} 前落地 —— "
            f"筛出 {c['n_flights_after']}/{c['n_flights_before']} 班。"
            f"下方分位与结论**只针对这 {c['n_flights_after']} 班**；"
            f"历史参照集覆盖 {c.get('n_dates_after')} 个起飞日。")
        add("")
        if c.get("excluded_here"):
            add(f"　本日被排除：{'、'.join(c['excluded_here'])}")
        if c.get("kept_here"):
            add(f"　本日保留：{'、'.join(c['kept_here'])}")
        add("")

    b = a.get("budget")
    if b:
        if b.get("hist_share_within") is not None:
            add(f"> 💰 **预算 ¥{b['max_price']}**：现价 ¥{b['floor_now']} "
                + ("✅ 在预算内" if b.get("within_budget") else "❌ 超预算")
                + f"；同期同提前量有 **{b['hist_share_within']*100:.0f}%** 的观测"
                  f"最低价 ≤¥{b['max_price']}（n={b['hist_n']}，{b.get('hist_scope','')}）")
        else:
            add(f"> 💰 **预算 ¥{b['max_price']}**：现价 ¥{b['floor_now']}"
                + (f"，样本不足无法给出历史命中率（{b.get('why','')}）"))
        add("")

    # --- 时机 ---
    add("### ⏱ 时机")
    t = a.get("timing") or {}
    if not t.get("ok"):
        add(f"**无法给出分位判断**：{t.get('why', '样本不足')}")
        add("")
        add("（分位算不出，但下面的选班对照仍基于真实数据，可参考相对结构。）")
    else:
        sp = t.get("scope_spread") or {}
        add(f"- **现价分位 {t['percentile']:.0f}%** → {t['band']}"
            + (f"（90% 置信区间 {t['percentile_ci90'][0]:.0f}–{t['percentile_ci90'][1]:.0f}%）"
               if t.get("percentile_ci90") else ""))
        ref = t["reference"]
        add(f"- 参照分布：P25 ¥{ref['P25']:.0f} · P50 ¥{ref['P50']:.0f} · P75 ¥{ref['P75']:.0f}")
        sc = t["scope"]
        add(f"- 参照集：{sc['scope']} · lead {sc['lead_range'][0]}–{sc['lead_range'][1]} 天 · "
            f"**{sc['n_samples']} 个样本 / {sc['n_dates']} 个起飞日** · 置信度 {sc['confidence']}")
        if sp.get("by_scope"):
            bits = " / ".join(f"{x['scope']} {x['percentile']:.0f}%" for x in sp["by_scope"])
            add(f"- 参照集敏感度：{bits}"
                + (f" → 摆动 {sp['range']:.0f} 个百分点" if sp.get("range") else ""))
        hc = t.get("holiday_contamination")
        if hc and hc.get("percentile_excluding") is not None:
            add(f"- ⚠️ 参照集含 {hc['share_in_baseline']*100:.0f}% 高峰日期样本，"
                f"剔除后分位为 {hc['percentile_excluding']:.0f}%")

    ft = a.get("floor_trend") or {}
    if ft.get("steps"):
        bits = " · ".join(f"lead {d['lead']} 时 ¥{d['floor']:.0f}"
                          + (f"({d['pct']:+.1f}%)" if d.get("pct") is not None else "")
                          for d in ft["steps"].values())
        add(f"- 价格台阶：{bits} → 方向 **{ft['direction']}**")
    add("")

    # --- 等待胜率（含幸存者偏差）---
    # disappear_rate 存在与否是判据：配对数不足的行没有这个键
    waits = [w for w in (a.get("wait_analysis") or [])
             if w.get("disappear_rate") is not None]
    if waits:
        add("### ⏳ 再等会怎样（同一批航班的历史结果）")
        add("")
        add("| 再等 | 配对数 | 变便宜 | 持平 | 变贵 | 中位价差 | 等待期消失 |")
        add("|---|---|---|---|---|---|---|")
        for w in waits:
            add(f"| {w['horizon_days']} 天 | {w['n_paired']} | {w['down_rate']*100:.0f}% | "
                f"{w['flat_rate']*100:.0f}% | {w['up_rate']*100:.0f}% | "
                f"¥{(w['median_diff'] or 0):+.0f} | "
                + (f"**{w['disappear_rate']*100:.0f}%**" if w.get("survivor_bias_warning")
                   else f"{w['disappear_rate']*100:.0f}%") + " |")
        worst = max(waits, key=lambda w: w.get("disappear_rate") or 0)
        if worst.get("survivor_bias_warning"):
            add("")
            add(f"> 🚨 **幸存者偏差**：最后一行的涨跌率只在**活下来的 "
                f"{worst['n_paired']} 班**里统计；同期有 "
                f"**{worst['n_disappeared']} 班（{worst['disappear_rate']*100:.0f}%）已经买不到了**。")
            add("> 等待的真实代价不是『涨多少』，而是『可能根本买不到原来那班』。")
        add("")

    # --- 选班 ---
    sel = a.get("selection") or {}
    if sel.get("candidates"):
        add(f"### 🎫 选班（当日 {sel['n_flights']} 班，最低 ¥{sel['floor_now']}）")
        add("")
        for c in sel.get("recommended", []):
            tag = ["**① 推荐**", "**② 备选**", "**③ 备选**"][min(c["rank"] - 1, 2)]
            add(f"{tag}：{c['airline']} {c['flight_no']} · "
                f"{c['cmp_airport'] or ''} {c['departure_time']} → {c['arrival_time']} · ¥{c['price']}")
            add(f"- {c['reason']}")
            if c["flags"]:
                add(f"- 标记：{', '.join(c['flags'])}")
            add("")
        for c in sel.get("avoid", []):
            add(f"**❌ 避免**：{c['airline']} {c['flight_no']} · "
                f"{c['departure_time']} · ¥{c['price']} — {c['reason']}")
            add("")

        for n in sel.get("notes") or []:
            add(f"> 注：{n}")
        add("")

        at = sel.get("airline_table") or []
        if at:
            add("**航司**（以『起飞日 × 提前量』为单位，不按爬取频次加权）")
            add("")
            add("| 航司 | 成为当日最低的占比 | 非最低时中位加价 | 价格中位 | 售罄率 | 判读 |")
            add("|---|---|---|---|---|---|")
            for r in at[:6]:
                cr = r.get("censored_rate")
                add(f"| {r['airline']} | {r['floor_win_rate']*100:.1f}% | ¥{r['premium_median']} | "
                    f"¥{r['price_p50']} | {'—' if cr is None else f'{cr*100:.1f}%'} | {r['verdict']} |")
            add("")

        ap = sel.get("airport_table") or {}
        if ap.get("available"):
            cur = ap.get("current") or {}
            a1, a2 = ap["airports"]
            side_name = "出发" if ap.get("side") == "dep" else "到达"
            add(f"**机场**（{side_name}端）：{ap['verdict']}——同日最低价差中位 "
                f"¥{ap['median_diff']}，共 {ap['n_obs']} 天可比")
            # 当日某一侧可能没有航班（cur[...] 为 None），必须渲染成「—」而不是 Python 的 None
            fmt = lambda k: "—" if cur.get(k) is None else f"¥{cur[k]}"
            add(f"　当日：{a1} {fmt(a1)} / {a2} {fmt(a2)}")
            add("")

        st = sel.get("slot_table") or []
        if any(r["price_median_hist"] for r in st):
            add("**时刻段**（历史列限定在 lead ±7 天，避免把远期和临期混算）")
            add("")
            add("| 时刻段 | 当日最低 | 历史中位 | 相对当日最低的中位加价 |")
            add("|---|---|---|---|")
            for r in st:
                add(f"| {r['slot']} | "
                    + (f"¥{r['price_min_now']}" if r["price_min_now"] is not None else "—")
                    + " | "
                    + (f"¥{r['price_median_hist']}" if r["price_median_hist"] is not None else "—")
                    + " | "
                    + (f"¥{r['premium_median_hist']:+d}" if r["premium_median_hist"] is not None else "—")
                    + " |")
            add("")

    # --- 警告 ---
    if a.get("warnings"):
        add("### ⚠️ 提示与口径")
        for w in a["warnings"]:
            add(f"- {w}")
        add("")

    add("---")
    add(f"*advisor v{core.SCHEMA_VERSION} · 数据 {f['crawl_time']} · "
        f"分位口径：同航线滑窗 · 数据源 {C.DB_FILE}*")
    return "\n".join(L)


def _md_flight(d):
    """单航班轨迹卡片（--flight）。"""
    q = d["query"]
    L = []
    add = L.append
    f = d.get("data_as_of") or {}
    head = f"## ✈️ {q['flight_no']} · {q['route_label']} · {q['flight_date']}"

    if not d.get("ok"):
        add(head)
        add("")
        add(d.get("why", "无数据"))
        return "\n".join(L)

    fl = d["flight"]
    add(head)
    add("")
    add(f"**{fl['airline']} {fl['flight_no']}** · "
        f"{fl.get('dep_ap_label') or ''} → {fl.get('arr_ap_label') or ''} · "
        f"{fl['departure_time']} → {fl['arrival_time']} · "
        f"{fl['dep_slot']} · {fl['aircraft_type']}")
    add("")
    icon = {"fresh": "✅", "stale": "⚠️", "old": "🔶", "very_old": "❌"}.get(f.get("freshness"), "")
    add(f"**{icon} 数据**：截止 {f.get('crawl_time')}（{f.get('age_hours', 0):.1f} 小时前 · "
        f"{f.get('freshness_label')}）")
    add("")

    gone = d.get("censored") and d.get("soldout_lead") is not None
    tag = "🟢 固定档位·未动过" if d["n_price_levels"] == 1 else "🔀 会变价"
    add(f"**{d['n_obs']} 次观测 · 出现过 {d['n_price_levels']} 种价格 · {tag}**")
    add(f"- {'最后已知价' if gone else '现价'} ¥{d['price_now']} · "
        f"区间 ¥{d['price_min']}–¥{d['price_max']}")
    if d.get("floor_now") is not None:
        diff = d["price_now"] - d["floor_now"]
        add(f"- 当日全网最低 ¥{d['floor_now']}，本班"
            + (f"就是最低价" if diff == 0 else f"贵 ¥{diff}"))
    add(f"- 追踪范围：提前 {d['lead_first']} 天 → 提前 {d['lead_last']} 天")
    if d.get("censored") and d.get("soldout_lead") is not None:
        add(f"- 🚨 **提前 {d['soldout_lead']} 天从列表消失**（疑似售罄），之后再未被观测到")
    add("")

    runs = []
    for p in d["trajectory"]:
        if not runs or runs[-1]["price"] != p["price"]:
            runs.append({"price": p["price"], "lead_first": p["lead_days"],
                         "lead_last": p["lead_days"], "crawl_first": p["crawl_date"],
                         "crawl_last": p["crawl_date"], "n": 1})
        else:
            runs[-1].update(lead_last=p["lead_days"], crawl_last=p["crawl_date"],
                            n=runs[-1]["n"] + 1)
    add("### 📉 价格轨迹（相同价格已折叠）")
    add("")
    add("| 价格 | 提前量区间 | 观测数 | 起止日期 |")
    add("|---|---|---|---|")
    for r in runs:
        span = (f"{r['lead_first']} → {r['lead_last']}" if r["lead_first"] != r["lead_last"]
                else str(r["lead_first"]))
        dates = (r["crawl_first"] if r["crawl_first"] == r["crawl_last"]
                 else f"{r['crawl_first']} ~ {r['crawl_last']}")
        add(f"| ¥{r['price']} | {span} | {r['n']} | {dates} |")
    add("")
    if len(runs) == 1:
        add(f"> 全程 {d['n_obs']} 次观测价格**一动没动**——这是固定档位，"
            f"『再等等会降价』对它不成立。")
        add("")
    for w in d.get("warnings") or []:
        add(f"- {w}")
    add("")
    add("---")
    add(f"*advisor v{core.SCHEMA_VERSION} · 数据 {f.get('crawl_time')} · 数据源 {C.DB_FILE}*")
    return "\n".join(L)


def _table(rows):
    L = []
    cons = next((a["constraints"] for a in rows if a.get("constraints")), None)
    if cons:
        L.append(f"⚠️ 已应用约束：{cons['arrive_by']} 前落地"
                 f"（保留 {cons['n_flights_after']}/{cons['n_flights_before']} 班）"
                 f"—— 下表的『当日最低』与分位均只针对符合约束的航班")
        L.append("")
    head = f"{'起飞日':<12}{'周':<3}{'距飞':>4}  {'当日最低':>8} {'分位':>6}  {'置信':<7}{'参照集':<16}{'趋势':<9}结论"
    L.append(head)
    L.append("─" * (len(head) + 8))
    wd = "一二三四五六日"
    for a in rows:
        d = a["query"]["flight_date"]
        s = a.get("snapshot") or {}
        t = a.get("timing") or {}
        sc = t.get("scope") or {}
        import datetime as _dt
        w = wd[_dt.date.fromisoformat(d).weekday()]
        pct = f"{t['percentile']:.0f}%" if t.get("ok") else "—"
        conf = sc.get("confidence", "insuff") if t.get("ok") else "insuff"
        if len(conf) > 5:
            conf = conf[:5]
        ref = f"n={sc.get('n_samples','—')} ({sc.get('scope','')})" if t.get("ok") else "—"
        trend = (a.get("floor_trend") or {}).get("direction", "—")
        L.append(f"{d:<12}{w:<3}{s.get('lead_days','—'):>4}  "
                 f"{'¥' + str(s.get('floor_now','—')):>8} {pct:>6}  {conf:<7}{ref:<16}{trend:<9}"
                 f"{a.get('verdict_label','')}")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def build_parser():
    p = argparse.ArgumentParser(
        prog="cli.py", description="机票购票决策助手（基于 flight_monitor.db）")
    p.add_argument("--from", dest="rf", help="出发三字码，如 bjs")
    p.add_argument("--to", dest="rt", help="到达三字码，如 jjn")
    p.add_argument("--route", help="简写，如 bjs->jjn")
    p.add_argument("--date", action="append", default=[], help="起飞日 YYYY-MM-DD，可多次")
    p.add_argument("--scan", type=int, metavar="N", help="扫描未来 N 天")
    p.add_argument("--scan-from", dest="scan_from", metavar="DATE",
                   help="--scan 的起算起飞日（默认为今天+1）")
    p.add_argument("--arrive-by", dest="arrive_by", metavar="HH:MM",
                   help="只要该时刻前落地的航班（会同时收窄历史参照集）")
    p.add_argument("--max-price", dest="max_price", type=int, metavar="N",
                   help="只要当前价 ≤N 的航班（会同时收窄历史参照集）")
    p.add_argument("--flight", metavar="NO",
                   help="查单个航班的价格轨迹（如 NS8012），与 --date 搭配")
    p.add_argument("--as-of", dest="as_of", help="回放到某一批爬取（对拍/复盘）")
    p.add_argument("--format", choices=["md", "json", "table"], default="md")
    p.add_argument("--json", action="store_true", help="等价 --format json")
    p.add_argument("--compact", action="store_true", help="批量模式只出表格")
    p.add_argument("--quiet", action="store_true", help="只输出一行结论")
    p.add_argument("--list-routes", action="store_true")
    p.add_argument("--selftest", action="store_true")
    p.add_argument("--db", help="覆盖数据库路径")
    p.add_argument("--no-cache", action="store_true")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    fmt = "json" if args.json else args.format

    if args.db:
        C.DB_FILE = args.db
        core.C.DB_FILE = args.db
    use_cache = not args.no_cache

    if args.selftest:
        return 0 if core.self_test() else 1

    if args.list_routes:
        rows = core.list_routes(args.db, args.as_of)
        if fmt == "json":
            print(json.dumps(rows, ensure_ascii=False, indent=2))
            return 0
        print(f"{'航线':<14}{'快照数':>8}{'起飞日':>7}{'批次':>6}  {'覆盖的起飞日范围':<25}"
              f"{'最后爬取':<21}{'分级':<13}说明")
        print("─" * 120)
        for r in rows:
            cov = f"{r.get('flight_date_min','?')} ~ {r.get('flight_date_max','?')}"
            print(f"{r['route']:<14}{r['n_snapshots']:>8}{r['n_flight_dates']:>7}"
                  f"{r['n_batches']:>6}  {cov:<25}"
                  f"{r['last_crawl']:<21}{r['grade']:<13}{r['note']}")
        print()
        print("注：库外日期一律无法分析（退出码 3）。分析前先确认目标起飞日落在上表范围内。")
        return 0

    rf, rt = args.rf, args.rt
    if args.route:
        if "->" not in args.route:
            return _die("--route 格式应为 bjs->jjn")
        rf, rt = args.route.split("->", 1)
    if not rf or not rt:
        return _die("需要 --from/--to 或 --route")
    if not args.date and not args.scan:
        return _die("需要 --date 或 --scan N")
    if args.flight and not args.date:
        return _die("--flight 需要配合 --date 指明起飞日")

    cons = {"arrive_by": args.arrive_by, "max_price": args.max_price}
    if args.flight:
        if len(args.date) > 1:
            return _die("--flight 一次只查一个起飞日")
        try:
            d = core.flight_detail(rf, rt, args.date[0], args.flight,
                                   args.as_of, args.db, use_cache)
        except FileNotFoundError as e:
            return _die(str(e), 1)
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            return _die(f"{type(e).__name__}: {e}", 1)
        if fmt == "json":
            print(json.dumps(d, ensure_ascii=False, indent=2))
        else:
            print(_md_flight(d))
        return 0 if d.get("ok") else 3

    try:
        if args.scan:
            rows = core.scan(rf, rt, args.scan, args.as_of, args.db, use_cache,
                             cons, args.scan_from)
        else:
            rows = [core.advise(rf, rt, d, args.as_of, args.db, use_cache, cons)
                    for d in args.date]
    except FileNotFoundError as e:
        return _die(str(e), 1)
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        return _die(f"{type(e).__name__}: {e}", 1)

    if fmt == "json":
        payload = rows[0] if len(rows) == 1 else {"count": len(rows), "rows": rows}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif fmt == "table" or (args.compact and len(rows) > 1):
        print(_table(rows))
    elif args.quiet:
        for a in rows:
            print(f"{a['query']['flight_date']} {a.get('verdict','')} "
                  f"{(a.get('snapshot') or {}).get('floor_now','')} "
                  f"{(a.get('timing') or {}).get('percentile') or ''}")
    else:
        print("\n\n".join(_md_card(a) for a in rows))

    # 退出码：让调用方不必解析中文
    verdicts = {a.get("verdict") for a in rows}
    if verdicts and verdicts <= {"insufficient_data", "unsupported", "no_eligible_flight"}:
        return 3
    fresh = {((a.get("data_as_of") or {}).get("freshness")) for a in rows}
    if fresh & {"old", "very_old"}:
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
