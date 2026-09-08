"""
Flight-Monitor - 配置文件
"""

# ============================================================
# 监控航班配置
# ============================================================
# from/to: 携程城市代码（三字码）
# from_name/to_name: 显示用中文名称
# 每条航线可配置:
#   alert_only: True = 只在 ALARM 中为该航线配置的日期爬取（需同时把该航线加入 ALARM 才会爬；不配则整条航线跳过）
#   days_ahead: 覆盖全局 MONITOR_DAYS_AHEAD（对 alert_only 航线无效）
ROUTES = [
    {
        "from": "bjs",          # 北京（包含首都PEK和大兴PKX两个机场）
        "to": "jjn",            # 泉州晋江
        "from_name": "北京",
        "to_name": "泉州",
    },
    {
        "from": "bjs",          # 北京（包含首都PEK和大兴PKX两个机场）
        "to": "xmn",            # 厦门高崎
        "from_name": "北京",
        "to_name": "厦门",
        "alert_only": True,     # 只在告警日期爬取，不爬满30天
    },
    {
        "from": "jjn",          # 泉州晋江
        "to": "bjs",            # 北京（包含首都PEK和大兴PKX两个机场）
        "from_name": "泉州",
        "to_name": "北京",
    },
    {
        "from": "bjs",  # 北京（包含首都PEK和大兴PKX两个机场）
        "to": "hsn",  # 舟山
        "from_name": "北京",
        "to_name": "舟山",
        "alert_only": True,     # 只在告警日期爬取，不爬满30天
    },
    {
        "from": "bjs",  # 北京（包含首都PEK和大兴PKX两个机场）
        "to": "ngb",  # 宁波
        "from_name": "北京",
        "to_name": "宁波",
        "alert_only": True,  # 只在告警日期爬取，不爬满30天
    },
    {
        "from": "hsn",  # 舟山
        "to": "bjs",  # 北京
        "from_name": "舟山",
        "to_name": "北京",
        "alert_only": True,  # 只在告警日期爬取，不爬满30天
    },
    {
        "from": "ngb",  # 宁波
        "to": "bjs",  # 北京
        "from_name": "宁波",
        "to_name": "北京",
        "alert_only": True,  # 只在告警日期爬取，不爬满30天
    },
]

# 告警过滤：仅这些航线+日期有价格变动才推送飞书（空字典=不推送飞书、不调用DS）
# 键: (出发代码, 到达代码) 元组，值: 该航线对应的告警日期列表
# 两个条件同时生效：航线必须是键 AND 日期必须在对应列表中才会推送
ALARM = {
    ("jjn", "bjs"): ["2026-10-17"]
}
# 爬虫仍然会抓取所有航线+日期的数据存入数据库，只是不推送给飞书

# 直飞航班过滤（True=仅直飞，排除通程/中转航班）
DIRECT_FLIGHTS_ONLY = True

# 红眼航班过滤
# 出发时间不在 [RED_EYE_START, RED_EYE_END) 区间内，即排除 23:00 ~ 次日 06:00 出发的航班
RED_EYE_START_HOUR = 23         # 红眼开始时间（小时）
RED_EYE_END_HOUR = 6            # 红眼结束时间（小时）

# 监控未来多少天（始终从今天开始）
MONITOR_DAYS_AHEAD = 30

# ============================================================
# 监控设置
# ============================================================
MONITOR_INTERVAL_MINUTES = 180  # 监控间隔（分钟），30天数据量大，建议120以上
HEADLESS = True                 # 无头模式，True=后台运行

# ============================================================
# 价格变动提醒设置
# ============================================================
# 价格变化超过此百分比才告警（避免微小波动骚扰）
PRICE_CHANGE_THRESHOLD_PCT = 1  # 1% 以上变动才告警
# 价格变化绝对值超过此金额才告警（单位：元）
PRICE_CHANGE_THRESHOLD_MIN = 10  # 至少变动10元

# ============================================================
# 通知渠道配置
# ============================================================
# 飞书机器人 Webhook 地址（留空则不推送飞书）
FEISHU_WEBHOOK = ""

# DeepSeek API 配置（用于价格趋势预测，不配则告警不附带AI分析）
DEEPSEEK_API_KEY = ""               # DeepSeek API Key
DEEPSEEK_MODEL = "deepseek-chat"    # 模型名称

# 控制台输出开关
CONSOLE_OUTPUT = True

# 日志文件路径（后台运行时所有 print 输出同时写入此文件，留空则不写文件）
LOG_FILE = "monitor.log"

# ============================================================
# 数据库配置
# ============================================================
DB_FILE = "flight_monitor.db"

# 数据库保留天数（超过此天数的历史记录自动清理，0=不清理）
DB_RETENTION_DAYS = 0  # 0 = 永不清理

# ============================================================
# 携程页面配置
# ============================================================
# 每次搜索后等待秒数范围（随机抖动，避免请求节奏被识别为机器）
SEARCH_DELAY_MIN = 8
SEARCH_DELAY_MAX = 18
# 页面滚动次数（携程懒加载，通常3-4次即可加载全部航班）
SCROLL_TIMES = 4
# 每次滚动后等待秒数
SCROLL_DELAY_SECONDS = 2

# ============================================================
# 反反爬配置
# ============================================================
# 浏览器可执行文件路径（默认自动探测，找不到 Chrome 时可改用 Edge）
# Windows 示例: r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
# 留空则自动探测（优先 Chrome，其次 Edge）
BROWSER_PATH = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

# Chrome 用户数据目录（持久化 Cookie 和浏览器指纹，绕过携程反爬）
# 首次使用请运行: python main.py --setup  打开浏览器手动浏览携程建立身份
CHROME_USER_DATA_PATH = "chrome_user_data"

# 遇到空结果时最大重试次数（0=不重试）
MAX_RETRY_ON_EMPTY = 2
# 重试前等待秒数范围
RETRY_DELAY_MIN = 30
RETRY_DELAY_MAX = 60

# 网络检测间隔（秒），笔记本开机后 Wi-Fi 可能尚未连接，每轮爬取前先检测
NETWORK_CHECK_INTERVAL = 15
