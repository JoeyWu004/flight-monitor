# advisor — 购票决策助手

基于 `flight_monitor.db` 的历史价格快照，回答两个问题：

1. **现在该买，还是再等等？**（时机）
2. **买哪一班？**（航司 / 机场 / 时刻 / 具体航班）

`core.py` 是唯一分析真相，**完全不依赖 LLM**；上面挂两个薄入口：命令行 `cli.py`，
以及供 Claude Code 读的决策手册 `SKILL.md`。

## 快速开始

**在仓库根目录（含 `flight_monitor.db` 的那层）执行。**
解释器必须是装了 pandas/numpy 的那个——通常就是爬虫自己用的环境：

```bash
export PYTHONIOENCODING=utf-8     # 不设会因 ¥ 触发 GBK 报错

# 找到能用的解释器（不要硬编码路径，别人机器上不一样）
# 顺序同 run_dashboard.bat：CONDA_PREFIX > conda 根 > 常见安装位置 > PATH
# 每一档都要真的 import 一次才认——只看文件存在会挑到没装 pandas 的环境
find_py() {
  local c r roots
  for c in "$CONDA_PREFIX/python.exe" "$CONDA_PREFIX/bin/python"; do
    [ -n "$c" ] && [ -x "$c" ] && "$c" -c "import pandas,numpy" 2>/dev/null && { echo "$c"; return 0; }
  done
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
  for c in python python3 py; do
    command -v "$c" >/dev/null 2>&1 || continue
    "$c" -c "import pandas,numpy" 2>/dev/null && { echo "$c"; return 0; }
  done
  return 1
}
PY="$(find_py)" || echo "找不到装了 pandas/numpy 的 Python——检查是否已按 run_dashboard.bat 装好环境"
CLI="advisor/cli.py"

# --route 的值必须加引号：`>` 在 bash 里是重定向符，
# 不加会截成 `--route jjn-` 并生成一个名为 jjn 的空文件
$PY "$CLI" --list-routes                       # 航线 + 覆盖的起飞日范围 + 数据够不够
$PY "$CLI" --route "jjn->bjs" --date 2026-10-17  # 单日决策卡片
$PY "$CLI" --route "jjn->bjs" --scan 21          # 未来 21 天逐日建议
$PY "$CLI" --route "jjn->bjs" --date 2026-10-17 --json   # 结构化输出
$PY "$CLI" --selftest                          # 口径自检（确认没被改坏）

# 硬约束：用户的到达时刻 / 预算（见 SKILL.md §2.6）
$PY "$CLI" --route "jjn->bjs" --date 2026-10-17 --arrive-by 16:00
$PY "$CLI" --route "jjn->bjs" --date 2026-10-17 --arrive-by 16:00 --max-price 520
$PY "$CLI" --route "jjn->bjs" --date 2026-10-17 --flight NS8012   # 单航班价格轨迹
```

选错解释器时 `cli.py` 会直接告诉你该怎么找，不会只抛 `ImportError`。
嫌路径长可以先 `conda activate <爬虫的环境>`，然后 `python advisor/cli.py ...`。

> **作者环境备忘**：`D:/Softwares/anaconda3/envs/flight-monitor/python.exe`，
> 仓库在 `D:/Flight-Monitor`。别处不适用——按上面的 `find_py` 现找。

## 退出码

| 码 | 含义 |
|---|---|
| 0 | 正常 |
| 1 | 内部错误 |
| 2 | 参数错误 |
| 3 | **无法出结论**——两种子情况：该日/该约束下无观测，或样本不足建不出分位。措辞应区分，别混为一谈 |
| 4 | **数据陈旧**（结论需附带数据截止时间） |

## 文件

```
advisor/
  core.py                  分析引擎（LLM 无关，唯一真相）
  cli.py                   命令行入口（纯 I/O，零业务逻辑）
  common.py                从 eda/0903/common.py 拷贝，分家后自包含
  SKILL.md                 Claude Code 决策手册
  reference/
    strategy.md            原 eda/北京-泉州购票策略.md 的拷贝
    data-semantics.md      数据口径与 8 个已验证的陷阱 ← 看数字前先读这个
  cache/                   派生帧缓存（自动失效 + 自我清理，可随时整个删掉）
```

## 在 Claude Code 里用（可选）

`SKILL.md` 是一份**决策手册**：它规定 AI 助手什么时候该跑哪条命令、怎么解读输出、
哪些坑不能踩。`core.py` 不依赖它 —— 没有它，AI 只能看到 `cli.py --help`。

仓库里**不预置** Claude Code 的注册副本。装不装由你自己决定：

```bash
# 方式一：只在本项目可用
mkdir -p .claude/skills/flight-advisor
cp advisor/SKILL.md .claude/skills/flight-advisor/SKILL.md

# 方式二：所有项目都能用
mkdir -p ~/.claude/skills/flight-advisor
cp advisor/SKILL.md ~/.claude/skills/flight-advisor/SKILL.md
```

之后在仓库里直接问「泉州到北京 10 月 17 号该不该买」，或输入 `/flight-advisor`。
Claude Code 会在会话中自动发现新增的 skill，**不必重启**。
（管理技能用 `/skills`；frontmatter 写坏时用 `/doctor` 检查。）

> 🔧 **维护者注意**：本仓库的作者本地同时保留两份，改动需**手动同步**——
> ```bash
> cp advisor/SKILL.md .claude/skills/flight-advisor/SKILL.md
> ```
> 两份文件里的路径因此要么写相对仓库根、要么写绝对路径，**相对本文件写必然在其中
> 一处指错**。若你只维护一份、或不用 Claude Code，忽略这段。

## 与 `eda/` 的关系

`eda/` 是**研究侧**（notebook 形态，一次性 EDA，产出结论文档）；
`advisor/` 是**执行侧**（可重复调用、口径已修正、对数据不足有明确降级）。

`common.py` 是拷贝而非 import，两者分家：
`advisor/common.py` 修了 `find_db` 的兜底路径、去掉了死代码 `prices_at_leads`、
新增了 `as_of_filter` 与 `floor_by_int_lead`。**EDA 侧改口径不会自动同步到这里。**

## 设计要点

- **分位实时重算**，不硬编码任何金额阈值。原策略文档里的 `P25=350` 在代码里根本不存在。
- **滑动窗口取代粗桶**：粗桶使北京→泉州 "1-3天" 桶内每个起飞日的分位数完全相同；
  长提前量桶更只有 n=6 个样本。滑窗同位置有 n=70+。
- **"当前价"按 (航线, 起飞日) 各自取最新观测**，不用全局最后一批
  （否则告警优先爬取的航线会被静默丢弃）。
- **幸存者偏差显式上报**：等待期航班消失率与涨跌率并列，不藏。
- **参照集敏感度显式上报**：换历史范围分位可能摆动 20+ 个百分点，据此下调置信度。
- **数据不足就拒绝出结论**：稀疏航线（hsn/ngb）、页面级起价航线（xmn）都被明确拦住。

详细口径见 `reference/data-semantics.md`。
