# EDA — 探索性分析源码

三份 Jupyter notebook，用来摸清 `flight_monitor.db` 里价格数据的口径与规律，
是 [`advisor/`](../../advisor/) 那套决策逻辑的推导过程。

**这里的 notebook 已被清空输出**（`outputs` 与 `execution_count` 均为空），
只保留代码与说明单元格 —— 你自己跑一遍就能得到当时的图表，
而不是看到一个依赖特定数据快照、且无法复现的历史结果。

| 文件 | 内容 |
|---|---|
| `00_data_overview.ipynb` | 数据概览：批次、起飞日、航线、航班号分布，价格口径初探 |
| `01_buy_timing.ipynb` | 买时点：提前量与价格的关系、等待胜率、幸存者偏差 |
| `02_structure_volatility.ipynb` | 结构：航司 / 机场 / 时刻差异、价格波动与变动事件 |
| `common.py` | 三份 notebook 共用的取数与特征工程 |
| `tools/` | 辅助脚本（依赖图、质量探针、批量重跑 notebook） |

## 运行

需要一个 `flight_monitor.db`（由本仓库爬虫产出），放在**仓库根目录**。
notebook 里用 `os.getcwd()` 拼路径，所以请**从仓库根启动 Jupyter**：

```bash
cd <仓库根>
jupyter notebook eda/code/00_data_overview.ipynb
```

`common.py` 的 `find_db()` 会从当前目录逐级向上找 `flight_monitor.db`，
因此放在子目录里跑也能定位到。数据库**全程只读**（`sqlite3` 的 `mode=ro`）。

## 依赖

爬虫的 `requirements.txt` 不包含分析用的库，另需：

```bash
pip install pandas numpy matplotlib seaborn jupyter
```

中文字体：notebook 里设了 `Microsoft YaHei` / `SimHei` / `Noto Sans CJK SC` 的候选链，
Linux/macOS 下若图上中文变方块，装任一 CJK 字体即可。

## 注意

- 这些 notebook 是**分析代码**，不是可复用的库。要复现结论请跑
  [`advisor/cli.py`](../../advisor/)，它有固定的口径与自检。
- 数据是「抓取时该航班最低可订价」的快照，**绝对金额随季节漂移**。
  notebook 里出现的具体价格只对当时那段数据成立，别当成恒定规律。
