# Flight-Monitor

自动监控携程航班价格，支持多航线、多日期、红眼过滤、价格变动告警，通过飞书机器人推送提醒。

## 功能

- 🛫 多航线 + 多日期组合监控（自动滚动，始终监控未来 N 天）
- 🌙 自动过滤红眼航班（23:00 ~ 06:00 出发）
- 💰 价格变动检测，超过阈值自动推送飞书
- 🗄️ SQLite 存储完整历史价格，支持价格走势查询
- 🔔 告警模式：指定航线+日期后，推送**全部非红眼航班**，附带涨跌和距上次爬取时间
- 🧹 过期告警自动清理（航班日期过后自动删除）
- 🎯 告警项优先爬取，先跑完推提醒再爬剩余数据
- 🤖 Windows 任务计划程序开机自启动，静默后台运行
- 🐛 调试模式：保存页面 HTML 辅助排查问题

## 灵感来源

本项目基于 [hyperMoss/FLIGHT-TRACKER](https://github.com/hyperMoss/FLIGHT-TRACKER)，保留了以下核心设计：

- **DrissionPage + BeautifulSoup** 作为爬虫技术栈
- **携程单程航班 URL 参数结构** (`oneway-dep-arr?depdate=...`)
- **隐藏共享航班** 的交互策略 + **滚动到底部** 加载全部航班
- 关键 DOM class 选择器：`flight-box`、`depart-box`、`arrive-box`、`airline-name`、`price` 等
- **飞书 Webhook** 作为消息推送渠道
- **定时循环监控** 模式

在此基础上扩展了多航线×多日期配置驱动、红眼航班过滤、价格变动检测、SQLite 历史存储、两阶段优先爬取等功能。

## 快速开始

### 1. 编辑配置文件

打开 `config.py`，按顺序完成以下配置：

**① 添加航班信息**

修改 `ROUTES` 列表，填入你要监控的航线。`from` / `to` 使用携程城市三字码：

```python
ROUTES = [
    {"from": "bjs", "to": "jjn", "from_name": "北京", "to_name": "泉州"},
    {"from": "bjs", "to": "xmn", "from_name": "北京", "to_name": "厦门"},
    {"from": "jjn", "to": "bjs", "from_name": "泉州", "to_name": "北京"},
]
```

> 💡 常用城市代码：`bjs` 北京、`sha` 上海、`can` 广州、`szx` 深圳、`ctu` 成都、`cgo` 郑州。更多代码可在携程搜索页面 URL 中查看。

**② 填写飞书 Webhook 地址**

在飞书群聊中添加「自定义机器人」，复制 Webhook 地址，填入配置：

```python
FEISHU_WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/xxxxx"
```

> ⚠️ 留空则不会推送飞书消息，仅输出到控制台。

**③ 填写 DeepSeek API Key（可选）**

告警触发时自动调用 DeepSeek 分析价格趋势，结果附带在飞书消息中。不配则跳过 AI 分析：

```python
DEEPSEEK_API_KEY = "sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
DEEPSEEK_MODEL = "deepseek-chat"    # 可选 deepseek-chat / deepseek-reasoner
```

> 💡 获取 Key：访问 [DeepSeek 开放平台](https://platform.deepseek.com/)，注册后创建 API Key。

**④ 按需调整其他参数**

| 配置 | 说明 | 默认值 |
|------|------|--------|
| `MONITOR_DAYS_AHEAD` | 监控未来多少天（0=仅今天） | 0 |
| `MONITOR_INTERVAL_MINUTES` | 两轮监控间隔（分钟） | 180 |
| `RED_EYE_START_HOUR` | 红眼开始时间 | 23 |
| `RED_EYE_END_HOUR` | 红眼结束时间 | 6 |
| `PRICE_CHANGE_THRESHOLD_PCT` | 价格变动告警百分比 | 1 |
| `ALERT_ROUTES` | 仅这些航线推送告警（空=全部） | `[]` |
| `ALERT_DATES` | 仅这些日期推送告警（空=全部） | `[]` |

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 以管理员身份运行

右键点击「命令提示符」或「PowerShell」→ **以管理员身份运行**，然后执行：

```bash
python main.py              # 持续监控模式
python main.py --once       # 单次抓取测试
python main.py --once --debug  # 调试模式，保存页面HTML
```

> ⚠️ 必须以管理员身份运行，否则浏览器自动化可能无法正常工作。

## 配置说明

`config.py` 关键配置：

| 配置 | 说明 | 默认值 |
|------|------|--------|
| `ROUTES` | 航线列表（携程城市代码） | 北京↔泉州/厦门 |
| `MONITOR_DAYS_AHEAD` | 监控未来多少天 | 30 |
| `MONITOR_INTERVAL_MINUTES` | 两轮监控间隔（分钟） | 180 |
| `SEARCH_DELAY_SECONDS` | 每次请求间隔（防封） | 10 |
| `SCROLL_TIMES` | 页面滚动次数 | 4 |
| `RED_EYE_START_HOUR` | 红眼开始时间 | 23 |
| `RED_EYE_END_HOUR` | 红眼结束时间 | 6 |
| `PRICE_CHANGE_THRESHOLD_PCT` | 价格变动告警百分比 | 1 |
| `PRICE_CHANGE_THRESHOLD_MIN` | 价格变动告警最小值（元） | 10 |
| `ALERT_ROUTES` | 仅这些航线推送告警（空=全部） | `[]` |
| `ALERT_DATES` | 仅这些日期推送告警（空=全部） | `[]` |
| `FEISHU_WEBHOOK` | 飞书机器人 Webhook 地址 | - |
| `DEEPSEEK_API_KEY` | DeepSeek API Key（不配则告警不附带AI分析） | - |
| `DEEPSEEK_MODEL` | DeepSeek 模型名称 | `deepseek-chat` |
| `DB_RETENTION_DAYS` | 数据库保留天数（0=永久） | 0 |

### 告警过滤

平时设为空，静默爬取不打扰。买票时指定航线 + 日期：

```python
ALERT_ROUTES = [("bjs", "jjn"), ("bjs", "xmn")]   # 仅北京出发
ALERT_DATES = ["2026-07-15", "2026-07-20"]         # 仅这两天
```

设为非空后行为变化：

- **优先爬取**告警航线+日期，先跑完先推
- **推送全部非红眼航班**（不只是变动的），每条标注涨跌和距上次爬取时间
- 其余数据照常爬取入库，但不推送
- 航班日期一过，相关告警自动清理

## Windows 开机自启动

右键 `startup_setup.bat` → **以管理员身份运行**，脚本会自动完成：

1. 检测 Python 安装位置（支持 conda / 系统 Python / 注册表）
2. 校验 DrissionPage 依赖
3. 创建 `登录时` 触发的计划任务

```powershell
# 手动控制
schtasks /run /tn "FlightMonitor"    # 立即启动
schtasks /end /tn "FlightMonitor"    # 停止运行
```

卸载: 右键 `startup_remove.bat` → **以管理员身份运行**。

## 查看数据

```bash
# 命令行
sqlite3 flight_monitor.db "SELECT * FROM flight_prices ORDER BY crawl_time DESC LIMIT 20;"

# 或安装图形工具
winget install sqlitebrowser.sqlitebrowser
```

## 数据库结构

| 表 | 说明 |
|----|------|
| `flight_prices` | 航班价格历史（每次爬取快照） |
| `price_alerts` | 价格变动告警记录 |
| `monitor_log` | 每轮监控运行日志 |

## 依赖

- [DrissionPage](https://github.com/g1879/DrissionPage) — 浏览器自动化
- [BeautifulSoup4](https://www.crummy.com/software/BeautifulSoup/) — HTML 解析
- [requests](https://github.com/psf/requests) — HTTP 请求

## 注意事项

- 请遵守携程网站的使用条款
- 建议设置合理的抓取间隔，避免对服务器造成压力
- 仅供个人学习使用，请勿用于商业用途

## 许可证

MIT License
