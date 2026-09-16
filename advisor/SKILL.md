---
name: flight-advisor
description: >
  机票购票决策助手（flight ticket purchase advisor）。当用户问「该不该买机票」「现在买还是再等等」
  「机票什么时候买便宜」「买哪一班航班」「这个价格贵不贵」「机票还会降吗」「值不值得下手」
  「XX到XX的机票」「国庆/节假日机票要不要现在订」「提前几天买划算」「帮我看下这班」等
  购票决策、价格时机、航班选择问题时使用。基于本地 flight_monitor.db 的历史价格快照，
  实时重算同期分位，输出「时机判断（现在买/再等）+ 选班建议（航司/机场/时刻）」，
  并显式给出数据新鲜度、样本量与置信度。
  不适用于：查航班时刻表、订票下单、退改签规则、航班动态/延误、以及本库未覆盖的航线。
---

# 购票决策 Skill

> **本文件是权威版本**：`<仓库根>/advisor/SKILL.md`
>
> 仓库里**不预置** Claude Code 的注册副本。想启用就把这一份复制过去：
>
> ```bash
> mkdir -p .claude/skills/flight-advisor
> cp advisor/SKILL.md .claude/skills/flight-advisor/SKILL.md
> ```
>
> （放 `~/.claude/skills/flight-advisor/` 则所有项目可用。）
> Claude Code 会在会话中自动发现，**不必重启**。
>
> 🔧 **作者的本地维护**：作者机器上同时存在这两份，改动后需手动同步
> `cp advisor/SKILL.md .claude/skills/flight-advisor/SKILL.md`，
> 直接改副本会被下次同步覆盖。这一条**只对本地有两份的人有意义**——若你从
> 仓库新装，只有一份，不存在同步问题。
>
> **移植到别的机器/别的库**：见 §8。本文件里的路径一律写成相对仓库根，
> 只有明确标注"作者环境"的地方才是硬编码。

## §0 工作流（先看这里）

1. **确认四件事**：航线（如 `jjn->bjs`）、起飞日期、用户是否已看中某班、
   **有没有硬约束（到达时刻 / 预算）**。
   - 缺日期 → 先问；或直接 `--scan 21` 给窗口建议。
   - 问"什么时候买"但没定日期 → 用 `--scan`。
   - **用户提到"几点前到""不超过多少钱""必须上午到" → 一律走 `--arrive-by` /
     `--max-price`，不要事后手动解释。** 见 §2.6。
2. **先定位解释器和仓库**（这一步**不要跳过**，本 skill 要在别人的机器上也能跑）：

   - **仓库根** = 含 `flight_monitor.db` 的目录。`cli.py` 就在它下面的 `advisor/`。
     若当前 cwd 不是仓库根，先 `cd` 过去。
   - **解释器** = 任何一个能 `import pandas, numpy` 的 Python。**不要硬编码路径**。
     它通常就是爬虫自己用的那个环境：

   ```bash
   export PYTHONIOENCODING=utf-8        # Windows 下不设，输出 ¥ 会 GBK 报错

   # 逐个试候选解释器，第一个能 import pandas/numpy 的就是它。
   # 顺序与项目自带的 run_dashboard.bat 一致（CONDA_PREFIX > conda 根 > 常见安装位置 > PATH），
   # 但每一档都要**真的 import 一次**才认——只判断文件存在会挑到没装 pandas 的环境。
   find_py() {
     local c r roots
     # 1) 已激活的 conda
     for c in "$CONDA_PREFIX/python.exe" "$CONDA_PREFIX/bin/python"; do
       [ -n "$c" ] && [ -x "$c" ] && "$c" -c "import pandas,numpy" 2>/dev/null && { echo "$c"; return 0; }
     done
     # 2) conda 自己报告的位置 + 3) 常见安装位置
     #    实测：Windows 上 conda 未激活时 python/python3 可能压根不存在，
     #    只有这一档能找回装好包的那个环境。
     roots=""
     command -v conda >/dev/null 2>&1 && roots="$(dirname "$(dirname "$(command -v conda)")")"
     roots="$roots $HOME/anaconda3 $HOME/Anaconda3 $HOME/miniconda3 $HOME/Miniconda3
            /c/ProgramData/anaconda3 /c/ProgramData/miniconda3 /c/anaconda3 /c/miniconda3
            /d/anaconda3 /d/miniconda3 /d/Softwares/anaconda3 /d/Softwares/miniconda3"
     for r in $roots; do
       [ -d "$r" ] || continue
       for c in "$r/python.exe" "$r/bin/python" "$r"/envs/*/python.exe "$r"/envs/*/bin/python; do
         [ -x "$c" ] && "$c" -c "import pandas,numpy" 2>/dev/null && { echo "$c"; return 0; }
       done
     done
     # 4) PATH 上的
     for c in python python3 py; do
       command -v "$c" >/dev/null 2>&1 || continue
       "$c" -c "import pandas,numpy" 2>/dev/null && { echo "$c"; return 0; }
     done
     return 1
   }
   PY="$(find_py)" || echo "找不到装了 pandas/numpy 的 Python——检查是否已按 run_dashboard.bat 装好环境"
   CLI="advisor/cli.py"                 # 相对仓库根
   ```

   > 任何一个有 pandas/numpy 的解释器都能跑 advisor（只用 pandas/numpy/sqlite3），
   > **不必**是爬虫那个环境。换别人机器只要 `find_py` 找得到就行。
   > 常用安装位置那串可以按需增删——它是兜底，不是硬编码依赖。

   **解释器选错时 cli.py 会自己给出可照做的提示**，不会只抛 `ImportError`。

3. **跑命令**：

   ```bash
   $PY "$CLI" --route "jjn->bjs" --date 2026-10-17
   ```

   **`--route` 的值必须加引号。** `jjn->bjs` 里的 `>` 在 bash 下是重定向符，
   不加引号会被截成 `--route jjn-`，并在当前目录创建一个名为 `jjn` 的空文件。

   常用变体：`--scan 21`（扫未来 21 天）、`--json`（结构化）、`--format table`、
   `--list-routes`（库里有啥航线 + **覆盖的起飞日范围**）、
   `--as-of "2026-09-03 11:29:22"`（回放历史某一批）、
   `--arrive-by 16:00` / `--max-price 520`（硬约束）、
   `--flight NS8012`（单航班轨迹，配合 `--date`）、
   `--scan-from 2026-10-14`（`--scan` 从指定起飞日起算）。
4. **先看退出码，再用输出**：
   - `0` 正常 → 按 §3 组织回答
   - `3` **无法出结论** → 两种情况，措辞不同：
     - 「该日期/该约束下**没有观测**」→ 说清是"这天没被采集/没有符合的航班"，建议换日期或放宽约束
     - 「**样本不足**，建不出分位」→ 只转述 `why`，**绝不自己补结论**
   - `4` 数据陈旧 → **回答第一句必须说明数据截止何时**
5. **所有数字只能来自 cli 输出。** 不要自己算分位，不要凭记忆报价。
   **cli 表达不了的，按 §1 的盲区协议处理，不要偷偷绕过。**
6. **先确认目标日期在库内**：跑 `--list-routes` 看「覆盖的起飞日范围」。
   库外日期一律退出码 3，且**无法用任何方式补救**——不要逐个日期去试。

## §1 绝对禁令

- ❌ 不修改 `flight_monitor.db`（core 用 `mode=ro` 只读连接，任何写操作都是 bug）
- ❌ 不修改 `server/`、`main.py`、`eda/`、`config.py`
- ❌ **不引用记忆中的金额阈值**（"低于 400 就买"）。金额随季节漂移：策略文档记的
  "大兴比首都便宜 50 元"在最新数据上是 110 元；文档里的 `P25=350` 在代码里根本不存在，
  全是运行时算的。**只用 cli 给的相对分位与实时数字。**
- ❌ 不在 `insufficient_data` / `unsupported` 时给"买/等"建议
- ❌ 不隐藏 `wait_analysis` 里的 `disappear_rate`（等待期消失率）——它是本工具最有价值的信号
- ❌ 不把"平台价"说成"异常高价"，也不把"全价舱跳变"说成"普通涨价"

### 盲区协议（cli 覆盖不到时的唯一出路）

§4 那条"所有数字只能来自 cli 输出"是硬规矩，但 cli 表达不了所有问题。
遇到 cli 覆盖不到的问题时，**只有两条合规路径**：

1. **换一个 cli 能表达的等价问法**（多数情况都能换——先试这条）
2. **显式声明后做补充分析**，三件事缺一不可：
   - 明说「**这是本工具的盲区**，下面是我用原始数据自己算的」
   - 标注「**非 core 口径**，未经 `--selftest` 校验」
   - 优先用 `--as-of` 回放（cli 支持、有口径保证），而不是直接查库

**绝对不可以**：静默地用自己算的数字替换 cli 的结论，或用自己的统计覆盖 `verdict`。
曾经发生过的真实事故：助手绕过 cli 直接查库、自算分位与比例，**两次推翻 core 的
verdict 却未标注**，用户无从分辨哪些数字有口径保证。

### 约束追加协议（多轮对话专用）

用户会在对话里逐步加约束（本次实测：`bjs->jjn` → `jjn->bjs` → `≤16:00 到达` → `≤¥520`）。
每追加一个约束，**必须**：

1. **宣告作废**：明说"上一条结论基于 X 口径，在 Y 约束下**不再适用**"
2. **报收缩量**：可选集从 N 班筛到 M 班（cli 输出的 `constraints.n_flights_before/after`）
3. **重跑，不要复用**：加了约束就重跑 cli，**不要拿上一轮的 verdict 硬套**

不要因为"上次说过"就沿用上一轮结论——方向反转是常态。

## §2 决策规则

### 2.1 时机：`verdict` 由 core 判定，你负责解释与校验

| `verdict` | 你怎么说 | 必须附带 |
|---|---|---|
| `buy_now` | "现在就买" | 报 `reasons` 里最硬的那条 |
| `buy_soon` | "尽快买，别等了" | 报 `floor_trend` 抬升幅度 + `censoring` |
| `buy` | "可以下手" | 报 `percentile` 与 `reference.P25/P50/P75` |
| `neutral` | "中性，可再等 1–2 天" | 报 `wait_analysis` 里 1–3 天的跌/平/涨 |
| `wait` | "观望，偏贵" | 报 `percentile`；若 `season.is_peak` 说明是假期效应 |
| `wait_risky` | "偏贵，但时间紧，尽快决定或改期" | 同时报 `percentile`（偏贵）与 `floor_trend`/消失率（时间紧），**并提示改期或改机场是替代方案** |
| `insufficient_data` | "数据不足，无法判断" | 只转述 `why` 与 `coverage` |
| `no_eligible_flight` | "你的约束下没有可选航班" | 只转述 `why`；**不要**用当日最低价顶替，那是买不到的 |

> ⚠️ **verdict 是对哪几班说的？** 若 `constraints` 字段存在，verdict 与 `timing.percentile`
> 都只针对**符合约束的那几班**（`constraints.n_flights_after`）。回答时必须先报这一句，
> 否则用户会以为它还是针对当日最低价——而那可能正是他买不到的航班。

### 2.2 三条必须念出来的规律（**只在对应字段命中时才说，不要无脑背**）

- `timing.percentile <= 25` 且 `7 < lead <= 30` → 「处在你这个提前量的同期 P25 以下，属可下手档」
- `lead <= 7` → 「已进入提前一周，历史上涨占多数」——**数字取 `wait_analysis` 实际值，不背文档**
- 任一 `wait_analysis[].disappear_rate >= 0.10` → 「等到那时候，**X% 的航班已经买不到了**；
  剩下的样本里 Y% 涨价」——**先报消失率，再报涨跌**

### 2.3 选班三件事（`selection` 里已排好序，优先转述 `recommended`）

1. **机场**：`airport_table.verdict` 说大兴更便宜 → 优先大兴
2. **航司**：`airline_table` 里 `floor_win_rate` 最高的 = 低价守门员，盯它；
   `censored_rate > 0.04` 的补一句"看中及时下手"
3. **时刻**：报当日最低价所在的时间段，并说明该时段历史位置

### 2.4 三个"不要"（用户表现出追涨杀跌倾向时必说）

1. **不要追着变价走** —— 单次变价中位约 90 元，下跌后 2 次变价内反弹回原价概率约 58%。
   只看"现价 vs 同期分位"，不看单次涨跌。
2. **不要等临期捡漏** —— 临期低价是幸存者；便宜舱在提前 16–25 天就消失
   （用 `censoring` 的实际数字说）。
3. **不要被个案带节奏** —— 今天 300 元不等于明天还有；看 `floor_trend` 与 `disappear_rate`。

### 2.5 节假日

`season.is_peak == true` → 预算按 `season.index` 上调；
**不套用平日"提前 14–30 天"的绝对窗口**，只保留"越晚越贵"的相对规律；
报 `season.neighbor_dates` 建议避开峰值日。

### 2.6 硬约束：到达时刻与预算

用户说"几点前到""不超过 X 元"时，**必须改跑带约束的命令**，不要拿无条件结果硬解释。

```bash
--arrive-by 16:00      # 只要该时刻前落地的航班
--max-price 520        # 预算口径（不筛航班，只报命中率）
--flight NS8012        # 单航班轨迹
```

两者的语义**完全不同**，别搞混：

| 参数 | 语义 | 对历史参照集的影响 |
|---|---|---|
| `--arrive-by` | **筛航班**（到达时刻是航班的固有属性） | 在**观测级**过滤后重建 grid/floor/traj，参照集仍覆盖全部起飞日 |
| `--max-price` | **不筛航班**（预算是用户属性，不是航班属性） | 不影响；只在 `budget` 里报「同期有多少比例的最低價 ≤ 预算」 |

- 输出里出现 `constraints` 时，**第一句就要说清"本结论只针对这 N 班"**
- 报 `constraints.floor_now_unconstrained → floor_now_constrained` 的抬升，
  并明说「未被约束的日级分位对你不适用」
- 用 `budget.hist_share_within` 回答"能不能等到目标价"——**这是有口径的比例，
  不是拍脑袋的"会/不会"**
- `--flight` 输出里 `n_price_levels == 1` 表示全程价格一动没动（固定档位），
  此时「再等等会降价」**对它不成立**；`censored` 为真说明它已提前下架

**为什么这条规则重要**：无条件 verdict 建立在**当日最低价**上。若用户有约束，
那个最低价可能来自一班他根本坐不了的航班（实测 `jjn->bjs 2026-10-17`：
日级 verdict 说"观望，偏贵 ¥430"，而要求 16:00 前落地的用户只能买 ¥520）。
**一个关于"买不到的东西"的正确判定，比没有判定更危险。**

## §3 回答结构（固定顺序）

**结论 → 数据新鲜度 → 依据数字 → 选班 → 警告**

```
结论：<一句话，买/等/买哪班>
数据：截止 <data_as_of.crawl_time>（<age_hours> 小时前，<freshness_label>）
依据：现价 ¥X = 同期 <percentile>% 分位（<band>）；参照 n=<scope.n_samples>
      / <scope.n_dates> 个起飞日，置信度 <scope.confidence>
选班：<recommended[0] 的 flight_no / 航司 / 机场 / 时刻 / 价格 + reason>
警告：<warnings 逐条；尤其是 幸存者偏差 / 参照集敏感 / 数据陈旧>
```

详细排版见 `cli.py` 直接输出的 Markdown 卡片——**默认直接复述卡片内容，不要重排**。

## §4 字段速查

| 字段 | 含义 | 怎么念成人话 |
|---|---|---|
| `timing.percentile` | 现价在同期历史 floor 分布中的分位 | "比历史上同样提前量的 X% 的观测贵" |
| `timing.scope.n_samples` / `n_dates` | 参照集样本数 / 覆盖起飞日数 | "基于 N 个样本、M 个起飞日" |
| `timing.scope.confidence` | high / medium / low | 低于 high 就要说"置信度有限" |
| `timing.scope_spread.range` | 换参照集范围时分位的摆动幅度 | "参照集敏感，摆动 X 个百分点" |
| `timing.holiday_contamination` | 参照集里高峰日期的占比 | "基准被节假日抬高，实际偏贵" |
| `constraints` | 生效的硬约束（出现即表示结果已收窄） | "**本结论只针对符合约束的 N 班**" |
| `constraints.floor_now_unconstrained → _constrained` | 约束把当日最低价抬多少 | "你的约束要多花 ¥X，未约束的分位对你不适用" |
| `constraints.excluded_here` / `kept_here` | **当日**被排除/保留的航班号 | 逐班说明为什么坐不了 |
| `budget.hist_share_within` | 同期同提前量里，最低价落在预算内的比例 | "历史上 X% 的观测能以你的预算买到" |
| `flight.n_price_levels` | 单航班出现过的价格档数（`--flight`） | 等于 1 = "全程没动过，等不来降价" |
| `flight.censored` / `soldout_lead` | 该航班提前下架（疑似售罄） | "它在提前 X 天就没了" |
| `wait_analysis[].disappear_rate` | 等到更晚时，现有航班消失的比例 | **必报** |
| `censoring.verdict` | `cheap_seats_disappearing` = 便宜舱在消失 | "低价舱正在售罄，floor 抬高不全是涨价" |
| `floor_trend.direction` | rising / flat / falling | "价格台阶在抬升/横盘/回落" |
| `data_quality.platform_prices` | 航司固定档位价格 | "这是平台价，不是促销" |
| `data_quality.full_fare_jumps` | 仅剩全价舱的口径跳变 | "这是全价舱，不是普通涨价" |
| `coverage.grade` | rich / thin / sparse / insufficient | sparse 以下不给分位 |

## §5 常见追问

| 用户问 | 怎么做 |
|---|---|
| "还会降吗" | 报 `wait_analysis` + `disappear_rate`；**不说"会/不会"**，说"历史上等到那时：X% 跌 / Y% 涨 / Z% 买不到" |
| "现在是最低价吗" | 报 `percentile` + `reference`；≥75% 说偏贵，≤25% 说偏便宜 |
| "哪个航班好" | 转述 `selection.recommended`，逐条念 `reason` |
| "我下个月要去 XX" | 跑 `--scan 30`，给窗口建议表 |
| "你算得准吗" | 报 `scope_spread.range` 与 `percentile_ci90`，诚实说"参照集敏感度 ±N 个百分点" |
| 非本库航线 | 跑 `--list-routes`；不在库里就明说"本库未覆盖"，**不给通用建议冒充** |
| "我四点前要到" | 跑 `--arrive-by 16:00`，**不要**拿无条件结果解释 |
| "我想 ≤X 元拿下" | 跑 `--max-price X`，报 `budget.hist_share_within` |
| "我最多能等到什么时候订" | 跑 `--flight <航班号>` 看**这一班自己**的轨迹：1 种价格=固定档位，等不来降价；多种价格才谈得上"等" |
| "这班还在吗" | `--flight` 看 `censored` 与 `soldout_lead` |

## §6 命令速查

```bash
export PYTHONIOENCODING=utf-8     # 不设会因 ¥ 触发 GBK 报错
PY="<装了 pandas/numpy 的解释器>"   # 见 §0 步骤 2 的 find_py；不要硬编码
CLI="advisor/cli.py"              # 相对仓库根（含 flight_monitor.db 的目录）

# --route 的值一律加引号！`>` 在 bash 里是重定向符，不加会创建垃圾文件并执行错参数
$PY "$CLI" --list-routes                          # 航线 + 覆盖的起飞日范围
$PY "$CLI" --route "jjn->bjs" --date 2026-10-17   # 单日决策卡片
$PY "$CLI" --route "jjn->bjs" --scan 21           # 未来 21 天逐日建议
$PY "$CLI" --route "jjn->bjs" --scan 21 --scan-from 2026-10-14   # 从指定日起算
$PY "$CLI" --route "jjn->bjs" --date 2026-10-17 --json           # 结构化
$PY "$CLI" --route "jjn->bjs" --date 2026-10-17 --as-of "2026-09-03 11:29:22"  # 回放
$PY "$CLI" --route "jjn->bjs" --date 2026-10-17 --arrive-by 16:00            # 到达时刻约束
$PY "$CLI" --route "jjn->bjs" --date 2026-10-17 --arrive-by 16:00 --max-price 520
$PY "$CLI" --route "jjn->bjs" --date 2026-10-17 --flight NS8012              # 单航班轨迹
$PY "$CLI" --selftest                             # 口径自检，改完代码必跑
$PY "$CLI" --db /path/to/other.db --list-routes   # 临时用别的库
```

> 本机（作者环境）可用 `D:/Softwares/anaconda3/envs/flight-monitor/python.exe` 和
> `D:/Flight-Monitor/advisor/cli.py`；**别人机器上这两条路径必然不同**，按 §0 步骤 2 现找。

## §7 已知口径（解释数字时可能用得上）

- **`lead` 上限约 41 天**（爬虫只看未来 30 天 + 告警航线提前盯）。所以"提前 45 天"这类
  说法在本库里无数据支撑。
- **`price` = 抓取时该航班的"最低可订价"快照**，不是具体舱位票价。临期会出现全价舱口径跳变。
- **数据库是手动 scp 同步到看板服务器的**，服务器上的副本可能落后数天。本地库才是最新的。
- **参照集范围会让分位摆动**：6–7 月暑期票价系统性高于 9 月，用"全部历史"做基准会把 9 月的
  价格算得偏便宜。core 默认取最贴近的同期参照，并把摆动幅度显式报出来。
- **库有硬边界**：`--list-routes` 会显示每条航线覆盖的起飞日范围（实测 `2026-06-30 ~
  2026-10-17`）。**库外日期无解**，不要逐个日期试。
- **到达时刻不是稳定属性**：同一航班号有换季档（`NS8012` 有 14:10/14:15/14:20/15:45/
  22:00/23:00 六种），同一 (航班, 起飞日) 也有约 10% 会在追踪中途改时刻。
  所以 `--arrive-by` 必须在**观测级**过滤，不能按航班号或 traj_key 筛。
- **价位可能是"固定档位"而非波动价**：`PLATFORM_PRICE` 标记的价位（如 `NS8012` 的 ¥520）
  可以连续 23 次观测一动不动。这类航班「再等等会降价」不成立——
  用 `--flight` 看 `n_price_levels`，等于 1 就是这种情况。
  反之会变价的航班（同日 `KN5966` 走过 ¥520→¥430）才谈得上等。

## §8 移植（换机器 / 换库 / 换航线）

针对的是**同一个爬虫程序**在别人机器上的部署，不是换爬虫。适配面如下：

| 差异 | 是否已处理 | 说明 |
|---|---|---|
| **Python 环境目录不同** | ✅ | 不硬编码解释器；按 §0 步骤 2 现找，选错时 cli.py 给可照做的提示 |
| **仓库路径不同** | ✅ | 全部改用相对仓库根；`find_db()` 从 cwd 向上找 `flight_monitor.db` |
| **库里航线不同** | ✅ | 不写死任何航线；`--list-routes` 报覆盖范围，未知航线走通用路径 |
| **多机场城市不同** | ✅ | `airport_label()` 按中文命名规律解析，大兴/首都、虹桥/浦东、双流/天府通用；航站楼后缀（T1/T2）会被剥掉，不会把单机场城市误判成双机场 |
| **黄金回归基线对不上** | ✅ | `--selftest` 在本库不含基线航线时只 **SKIP**，换成纯函数+跨库不变量，任何库都能跑 |
| **数据库 schema 不同** | ❌ **不支持** | 只认本爬虫产出的 `flight_prices` / `monitor_log` 表结构，换爬虫需写适配层 |
| **机场三字码不同** | ⚠️ 部分 | `bjs`/`jjn` 是爬虫自己的城市码；换城市只要爬虫产得出对应行就自动可用 |

**给别人用的最小步骤**：把整个仓库（含 `advisor/` 和 `.claude/skills/`）拷过去 →
用爬虫那个环境跑 `python advisor/cli.py --selftest` 确认不红 → 跑
`--list-routes` 看有哪些航线 → 正常用。

**注意**：§2.4 里的具体数字（单次变价中位 ¥90、反弹概率 58%）与 §7 的
"lead 上限约 41 天"都是**作者这份数据的统计事实**，换库/换航线后数值会变。
**它们不是恒定规律，不要在别的库上照搬。**

---

详见 `<仓库根>/advisor/reference/data-semantics.md`（数据口径与 8 个陷阱）
与 `<仓库根>/advisor/reference/strategy.md`（原策略文档，含领域背景）。
本文件在 `advisor/` 和 `.claude/skills/` 各有一份，所以路径要么写相对仓库根、
要么写绝对路径——相对本文件写必然在其中一处指错。
