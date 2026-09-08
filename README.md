# Flight-Monitor

自动监控携程航班价格，支持多航线多日期、红眼过滤、价格变动告警、AI 趋势预测，飞书机器人推送 + Web 数据看板。

## 功能

### 爬虫核心
- 🛫 **多航线 × 多日期**组合监控，自动滚动加载全部航班
- 🌙 自动过滤**红眼航班**（23:00 ~ 06:00 出发）
- 🛬 **直飞过滤**：排除通程/中转/代码共享航班，按出发+到达时间去重
- ✈️ **机型识别**：自动提取机型，大客机（宽体）标注 ⭐
- 🔄 机型变动检测（如 A320 → A330）
- 🛡️ **反爬保护**：持久化 Chrome 身份、UA 轮换、随机窗口尺寸、随机延迟
- 🌐 **浏览器可自定义**：支持 Chrome / Edge，在 `config.py` 用 `BROWSER_PATH` 指向浏览器可执行文件即可
- 🔁 空结果自动重试 + 浏览器实例崩溃自动重建

### 价格告警
- 💰 价格变动检测，超过阈值自动推送飞书
- 🤖 **AI 趋势预测**：接入 DeepSeek，告警附带购买建议
- 🎯 **告警航线优先爬取**，爬完立即推送，不等待整轮结束
- 📋 告警推送**完整航班报告**（全部航班 + 涨跌 + 距上次时间），不只是变动项
- 🧹 过期告警自动清理

### Web 数据看板
- 📊 **FastAPI + ECharts** 网页看板，价格趋势图、多目的地对比
- 🔐 JWT 登录认证，公网部署也安全
- 📱 响应式布局，手机浏览器也能看
- 🤖 看板内置 AI 助手（DeepSeek），可直接问价格走势
- 📈 多日期价格摘要、最佳入手日期推荐

### 运维
- 🪟 Windows 任务计划程序**开机自启动**，后台静默运行
- 🔔 Windows Toast 通知（启动/完成/异常/停止）
- 📝 日志文件双通道输出（控制台 + 文件）
- 🌐 启动前**网络连通性检测**，Wi-Fi 未连就等
- 😴 爬取期间**阻止系统休眠**，完成后释放
- ⏸️ 重启冷却机制：用 DB 最新写入时间判断，避免重启后立即重复爬取

## 灵感来源

本项目基于 [hyperMoss/FLIGHT-TRACKER](https://github.com/hyperMoss/FLIGHT-TRACKER)，保留了以下核心设计：

- **DrissionPage + BeautifulSoup** 作为爬虫技术栈
- **携程单程航班 URL 参数结构** (`oneway-dep-arr?depdate=...`)
- **代码共享航班去重** + **滚动到底部** 加载全部航班
- 关键 DOM class 选择器：`flight-box`、`depart-box`、`arrive-box`、`airline-name`、`price` 等
- **飞书 Webhook** 作为消息推送渠道
- **定时循环监控** 模式

在此基础上扩展了多航线×多日期配置驱动、红眼过滤、AI 趋势预测、SQLite 历史存储、Web 看板、反爬保护等功能。

## 快速开始

### 1. 环境准备

```bash
# 安装爬虫依赖
pip install -r requirements.txt

# 安装看板依赖（可选，仅本地看板需要）
pip install -r server/requirements.txt
```

### 2. 初始化 Chrome 身份（首次必须）

```bash
python main.py --setup
```

浏览器会打开携程首页，手动搜索一条航线、随便点点页面，模拟真实用户。完成后回到终端按 Enter。之后爬虫会复用这个身份，不会被识别为机器人。

### 3. 编辑配置

打开 `config.py`，按需修改：

**① 航线配置**

```python
ROUTES = [
    {"from": "bjs", "to": "jjn", "from_name": "北京", "to_name": "泉州"},
    {"from": "bjs", "to": "xmn", "from_name": "北京", "to_name": "厦门",
     "alert_only": True},   # 只在 ALARM 中为本航线配置的日期爬取（需同时加入 ALARM）
    {"from": "jjn", "to": "bjs", "from_name": "泉州", "to_name": "北京"},
]
```

每条航线可配置：
| 字段 | 说明 |
|------|------|
| `from` / `to` | 携程城市三字码 |
| `from_name` / `to_name` | 显示用中文名 |
| `alert_only` | 可选，`True` = 只在 `ALARM` 为该航线配置的日期爬取；**该航线必须已加入 `ALARM`**，否则整条航线被跳过、完全不爬 |
| `days_ahead` | 可选，覆盖全局 `MONITOR_DAYS_AHEAD`（对 `alert_only` 航线无效） |

> 💡 常用城市代码：`bjs` 北京、`sha` 上海、`can` 广州、`szx` 深圳、`ctu` 成都、`cgo` 郑州、`jjn` 泉州、`xmn` 厦门、`hsn` 舟山、`ngb` 宁波。

**② 告警配置**

```python
ALARM = {
    ("jjn", "bjs"): ["2026-10-17"],   # 航线是键 + 日期在列表中，两者同时命中才推送
}
```

- 键：`(出发代码, 到达代码)` 元组；值：该航线的告警日期列表
- **同时满足**「航线是键 且 日期在列表中」才会推送飞书 + 调用 DeepSeek
- `ALARM` 里是否包含某航线，同时决定了 `alert_only` 航线要不要爬（见下方行为说明）
- `ALARM` 空 `{}` 时普通航线静默爬取入库（不推送、不调用 DeepSeek），但 `alert_only` 航线会被跳过

**③ 飞书 Webhook**

```python
FEISHU_WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/xxxxx"
```

> 在飞书群聊中添加「自定义机器人」，复制 Webhook 地址。留空则不推送飞书。

**④ DeepSeek API Key（可选）**

```python
DEEPSEEK_API_KEY = "sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
DEEPSEEK_MODEL = "deepseek-chat"
```

> 告警触发时自动调用 DeepSeek 分析价格趋势，不配则跳过 AI 分析。
> 获取 Key：[DeepSeek 开放平台](https://platform.deepseek.com/)

### 4. 运行

```bash
python main.py              # 持续监控模式（按配置间隔循环）
python main.py --once       # 单次抓取，跑完退出
python main.py --once --debug  # 调试模式，保存页面 HTML
python main.py --setup      # 初始化 Chrome 身份
```

> ⚠️ 建议以管理员身份运行，否则浏览器自动化可能无法正常工作。
> 🌐 浏览器可选 Chrome 或 Edge：在 `config.py` 把 `BROWSER_PATH` 设为对应浏览器可执行文件即可（如本机只有 Edge 就填 Edge 的路径；留空则由 DrissionPage 自动探测默认浏览器）。

## 配置参考

`config.py` 全部配置项：

| 配置 | 说明 | 默认值 |
|------|------|--------|
| `ROUTES` | 航线列表 | 北京⇄泉州，北京⇄厦门/舟山/宁波（后三者 `alert_only`） |
| `ALARM` | 告警航线+日期 | 见当前 `config.py` |
| `MONITOR_DAYS_AHEAD` | 监控未来多少天 | 30 |
| `MONITOR_INTERVAL_MINUTES` | 两轮监控间隔（分钟） | 180 |
| `HEADLESS` | 无头模式（True=后台运行） | `True` |
| `BROWSER_PATH` | 浏览器可执行文件路径（留空由 DrissionPage 自动探测） | 自动探测 |
| `DIRECT_FLIGHTS_ONLY` | 仅直飞航班 | `True` |
| `RED_EYE_START_HOUR` | 红眼开始时间 | 23 |
| `RED_EYE_END_HOUR` | 红眼结束时间 | 6 |
| `PRICE_CHANGE_THRESHOLD_PCT` | 价格变动告警百分比 | 1 |
| `PRICE_CHANGE_THRESHOLD_MIN` | 价格变动告警最低金额（元） | 10 |
| `FEISHU_WEBHOOK` | 飞书机器人 Webhook | - |
| `DEEPSEEK_API_KEY` | DeepSeek API Key | - |
| `DEEPSEEK_MODEL` | DeepSeek 模型 | `deepseek-chat` |
| `CONSOLE_OUTPUT` | 控制台输出开关 | `True` |
| `LOG_FILE` | 日志文件路径（留空不写） | `monitor.log` |
| `DB_FILE` | SQLite 数据库路径 | `flight_monitor.db` |
| `DB_RETENTION_DAYS` | 数据库保留天数（0=永久） | 0 |
| `SEARCH_DELAY_MIN` / `MAX` | 每次搜索间隔范围（秒） | 8 / 18 |
| `SCROLL_TIMES` | 页面滚动次数 | 4 |
| `SCROLL_DELAY_SECONDS` | 每次滚动后等待（秒） | 2 |
| `CHROME_USER_DATA_PATH` | Chrome 用户数据目录 | `chrome_user_data` |
| `MAX_RETRY_ON_EMPTY` | 空结果最大重试次数 | 2 |
| `RETRY_DELAY_MIN` / `MAX` | 重试前等待范围（秒） | 30 / 60 |
| `NETWORK_CHECK_INTERVAL` | 网络检测间隔（秒） | 15 |

### 告警与爬取行为说明

某条航线**爬不爬、爬哪些日期**，由其自身的 `alert_only` 与是否在 `ALARM` 中共同决定：

| 航线类型 | 不在 `ALARM` 中 | 在 `ALARM` 中 |
|------|------|------|
| 普通航线 | 爬满未来 N 天入库，不推送 | 爬满 N 天；仅 `ALARM` 列出的日期**优先爬取并推送** |
| `alert_only` 航线 | **整条跳过，完全不爬** | 只爬 `ALARM` 为该航线列出的日期，命中即推送 |

其他规则：
- `MONITOR_DAYS_AHEAD = 0` 且 `ALARM` 非空时：只爬 `ALARM` 中的航线，不爬无关航线
- 告警项爬完**立即推送**完整航班报告（全部航班 + 涨跌 + 距上次时间），再继续爬其余数据
- 航班日期一过，相关告警自动清理
- 所有航线都非 `alert_only` 且 `ALARM` 空 `{}` 时 = 纯静默积累数据（只入库，不推送、不调用 DeepSeek）

## Web 数据看板

### 本地运行

双击 `run_dashboard.bat` 或：

```bash
cd server
set NO_AUTH=1
python server.py
```

浏览器打开 `http://127.0.0.1:8000`，无需登录。

### 功能

- 📊 航班列表（航司、机型、起降机场、价格、涨跌），支持**表头排序**、点击行查看该航班走势
- 💚 **历史最低价**对比：当日价格即历史最低时标绿高亮
- 📈 历史价格趋势图（ECharts），多目的地多航班同时对比，图例可开关
- 🗺️ **多目的地多选**同时查看、**出发/到达一键互换**、今日/前后日快捷切换
- 🤖 **AI 助手**（DeepSeek 聊天问答）：回答渲染 Markdown；随附页面真实数据（航班列表/价格趋势/多日期摘要），可一键查看并复制**每次实际发送的 Prompt**（调试）
- 📅 多日期价格摘要 + 最佳入手日期推荐
- 🔐 JWT 登录认证，公网部署也安全

### 服务器部署

参考 `server/` 目录。已在阿里云 2C2G 服务器上部署：FastAPI + Nginx + systemd，地址 `http://115.28.209.155`。

本地数据同步到服务器：

```bash
# 双击 sync_db.bat，或手动：
scp flight_monitor.db root@115.28.209.155:/opt/flight-monitor-server/
```

> ⚠️ 爬虫运行中同步也安全 — SQLite 写入是原子提交，不会产生损坏文件。最坏情况少几条最新数据。

## Windows 开机自启动

右键 `startup_setup.bat` → **以管理员身份运行**，自动创建「登录时触发」的计划任务。

```powershell
schtasks /run /tn "FlightMonitor"   # 手动启动
schtasks /end /tn "FlightMonitor"   # 手动停止
```

卸载：右键 `startup_remove.bat` → 以管理员身份运行。

## 数据库结构

| 表 | 说明 |
|----|------|
| `flight_prices` | 航班价格历史（每次爬取快照，追加不覆盖） |
| `price_alerts` | 价格变动告警记录 |
| `monitor_log` | 每轮监控运行日志 |

```bash
# 命令行查看
sqlite3 flight_monitor.db "SELECT * FROM flight_prices ORDER BY crawl_time DESC LIMIT 20;"

# 图形工具
winget install sqlitebrowser.sqlitebrowser
```

## 项目结构

```
Flight-Monitor/
├── main.py              # 爬虫主程序
├── config.py            # 配置文件
├── database.py          # SQLite 数据库模块
├── predictor.py         # DeepSeek AI 趋势预测
├── requirements.txt     # 爬虫依赖
├── run_monitor.bat      # 本地快速启动爬虫
├── run_dashboard.bat    # 本地快速启动看板
├── startup_setup.bat    # 开机自启动安装
├── startup_remove.bat   # 开机自启动卸载
├── server/
│   ├── server.py        # FastAPI 看板后端
│   ├── requirements.txt # 看板依赖
│   ├── sync_db.bat      # 同步 DB 到服务器
│   ├── users.json       # 登录用户
│   └── static/
│       └── index.html   # 看板前端页面
├── chrome_user_data/    # Chrome 持久化身份（--setup 生成）
├── flight_monitor.db    # SQLite 数据库
└── monitor.log          # 运行日志
```

## 依赖

- [DrissionPage](https://github.com/g1879/DrissionPage) — 浏览器自动化
- [BeautifulSoup4](https://www.crummy.com/software/BeautifulSoup/) — HTML 解析
- [requests](https://github.com/psf/requests) — HTTP 请求
- [FastAPI](https://fastapi.tiangolo.com/) + [Uvicorn](https://www.uvicorn.org/) — Web 看板
- [ECharts](https://echarts.apache.org/) — 前端图表
- [marked](https://marked.js.org/) — 看板 AI 助手回复的 Markdown 渲染

## 注意事项

- 请遵守携程网站的使用条款
- 建议设置合理的抓取间隔，避免对服务器造成压力
- 仅供个人学习使用，请勿用于商业用途

## 许可证

MIT License
