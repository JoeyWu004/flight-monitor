"""
Flight-Monitor - 主程序

功能：
- 多航线、多日期、多机场组合监控
- 自动过滤红眼航班（23:00-06:00）
- 价格变动检测 + 飞书/控制台推送
- SQLite 存储完整历史价格记录
"""

from bs4 import BeautifulSoup
from DrissionPage import WebPage, ChromiumOptions
from time import sleep
import requests
import json
import re
import io
import sys
import os
import random
from datetime import datetime, timedelta

import config
import database
import predictor


# ============================================================
#  日志分流：控制台 + 文件同时输出
# ============================================================

class Tee:
    """同时写入多个输出流；某个流写入失败不影响其他流"""
    def __init__(self, *files):
        self.files = files

    def write(self, obj):
        for f in self.files:
            try:
                f.write(obj)
                f.flush()
            except Exception:
                pass

    def flush(self):
        for f in self.files:
            try:
                f.flush()
            except Exception:
                pass


def setup_logging():
    """配置日志分流：控制台 + 日志文件"""
    if not config.LOG_FILE:
        return
    try:
        log_f = open(config.LOG_FILE, 'a', encoding='utf-8')
        log_f.write(f"\n{'=' * 55}\n")
        log_f.write(f"  启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        log_f.write(f"{'=' * 55}\n")
        log_f.flush()

        sys.stdout = Tee(sys.__stdout__, log_f)
        sys.stderr = Tee(sys.__stderr__, log_f)
    except Exception:
        pass  # 日志初始化失败不阻塞主程序


# ============================================================
#  工具函数
# ============================================================

def show_windows_toast(title, message, is_error=False):
    """Windows 原生 Toast 通知（通过 win11toast），子进程发送不阻塞主程序"""
    try:
        import subprocess
        import json
        # 启动独立子进程发送通知，主进程不等待
        subprocess.Popen(
            [sys.executable, '-c',
             f'from win11toast import toast; toast({json.dumps(title)}, {json.dumps(message)})'],
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass  # 任何异常都不影响监控主流程


def get_monitor_dates():
    """获取监控日期列表：常规日期（今天起共 MONITOR_DAYS_AHEAD 天）+ 告警日期，去重排序"""
    today = datetime.now()
    dates = [(today + timedelta(days=i)).strftime('%Y-%m-%d')
             for i in range(config.MONITOR_DAYS_AHEAD)]
    # 告警日期始终纳入监控范围
    for d in config.ALERT_DATES:
        if d not in dates:
            dates.append(d)
    dates.sort()
    return dates


def is_red_eye(departure_time_str):
    """判断是否为红眼航班（出发时间在 23:00 ~ 06:00）
    输入格式如: '07:25', '23:30', '06:00'
    """
    try:
        hour = int(departure_time_str.strip().split(':')[0])
        return hour >= config.RED_EYE_START_HOUR or hour < config.RED_EYE_END_HOUR
    except (ValueError, AttributeError):
        return False  # 无法解析时不过滤


def parse_price(price_str):
    """解析价格字符串为整数，处理各种格式
    '¥580' → 580, '¥1,280' → 1280, '580' → 580
    """
    if not price_str:
        return None
    cleaned = price_str.replace('¥', '').replace(',', '').replace('￥', '').strip()
    try:
        return int(cleaned)
    except ValueError:
        # 尝试用正则提取数字
        match = re.search(r'(\d+)', cleaned)
        return int(match.group(1)) if match else None


def should_alert(old_price, new_price):
    """判断是否应该发送价格变动告警"""
    if old_price <= 0:
        return False
    change = abs(new_price - old_price)
    pct = change / old_price * 100
    return (pct >= config.PRICE_CHANGE_THRESHOLD_PCT and
            change >= config.PRICE_CHANGE_THRESHOLD_MIN)


def random_delay(min_sec=None, max_sec=None):
    """随机延迟，避免机器行为被检测"""
    lo = min_sec if min_sec is not None else config.SEARCH_DELAY_MIN
    hi = max_sec if max_sec is not None else config.SEARCH_DELAY_MAX
    s = random.uniform(lo, hi)
    sleep(s)
    return s


# ============================================================
#  通知推送
# ============================================================

def send_to_feishu(content, webhook_url=None):
    """发送消息到飞书机器人"""
    url = webhook_url or config.FEISHU_WEBHOOK
    if not url:
        return False

    try:
        headers = {'Content-Type': 'application/json'}
        # 飞书单条消息有长度限制，超长分段发送
        max_len = 15000
        chunks = [content[i:i+max_len] for i in range(0, len(content), max_len)]

        for i, chunk in enumerate(chunks):
            prefix = f"({i+1}/{len(chunks)}) " if len(chunks) > 1 else ""
            data = {
                "msg_type": "text",
                "content": {"text": prefix + chunk}
            }
            resp = requests.post(url, headers=headers, data=json.dumps(data), timeout=10)
            if resp.status_code != 200:
                print(f"❌ 飞书推送失败: HTTP {resp.status_code}")
                return False
            if len(chunks) > 1:
                sleep(0.5)  # 避免飞书限流

        print("✅ 消息已推送到飞书")
        return True
    except Exception as e:
        print(f"❌ 飞书推送异常: {e}")
        return False



def format_alert_message(alerts, run_time):
    """格式化价格变动告警消息"""
    lines = [
        f"🚨 机票价格变动提醒 ({run_time})",
        "=" * 45,
    ]

    # 按涨跌分组
    drops = [a for a in alerts if a['change_amount'] < 0]
    rises = [a for a in alerts if a['change_amount'] > 0]

    if drops:
        lines.append(f"\n📉 降价 ({len(drops)}个):")
        for a in sorted(drops, key=lambda x: x['change_amount']):
            lines.append(
                f"   🔽 ¥{a['old_price']} → ¥{a['new_price']} "
                f"({a['change_amount']:+d}元, {a['change_percent']:+.1f}%)"
            )
            lines.append(
                f"      {a['airline']} {a['flight_no']} "
                f"{a['from_name']}→{a['to_name']} "
                f"{a['flight_date']} {a['departure_time']}-{a['arrival_time']}"
            )
            if a.get('ai_trend'):
                lines.append(f"      {a['ai_trend']}")

    if rises:
        lines.append(f"\n📈 涨价 ({len(rises)}个):")
        for a in sorted(rises, key=lambda x: -x['change_amount']):
            lines.append(
                f"   🔼 ¥{a['old_price']} → ¥{a['new_price']} "
                f"({a['change_amount']:+d}元, {a['change_percent']:+.1f}%)"
            )
            lines.append(
                f"      {a['airline']} {a['flight_no']} "
                f"{a['from_name']}→{a['to_name']} "
                f"{a['flight_date']} {a['departure_time']}-{a['arrival_time']}"
            )
            if a.get('ai_trend'):
                lines.append(f"      {a['ai_trend']}")

    lines.append(f"\n{'=' * 45}")
    return "\n".join(lines)


def _short_airport(name):
    """缩短机场名称用于手机显示：去除"国际""机场"等通用后缀"""
    if not name:
        return ''
    return re.sub(r'国际|机场', '', name)

def format_alert_summary_message(alert_summary, run_time):
    """格式化告警航线+日期的完整航班报告（全部航班 + 变动信息）"""
    lines = [
        f"📋 航班监控报告 {run_time[:10]}",
        f"                {run_time[11:]}",
        "=" * 15,
    ]

    total_flights = 0
    for key, flights in alert_summary.items():
        from_name, to_name, date_str = key
        lines.append(f"\n✈️ {from_name} → {to_name}  {date_str}")
        lines.append(f"   共 {len(flights)} 个航班（已排除红眼）")

        # 按价格排序
        for f in sorted(flights, key=lambda x: x['price']):
            # 变动标记
            if f['last_price'] is None:
                tag = "🆕 新航班"
            elif f['change_amount'] > 0:
                tag = f"📈 +{f['change_amount']}元 +{f['change_percent']}%"
            elif f['change_amount'] < 0:
                tag = f"📉 {f['change_amount']}元 {f['change_percent']}%"
            else:
                tag = "➖ 未变动"

            airport = _short_airport(f.get('departure_airport', ''))

            # 第一行: 价格 航司 时间
            lines.append(
                f"   ¥{f['price']:<5} {f['airline']} "
                f"{f['departure_time']}-{f['arrival_time']}"
            )
            # 第二行: 航班号 航站楼
            lines.append(f"         {f['flight_no']}  {airport}")
            # 第三行: 价格变动
            if f['time_ago']:
                lines.append(f"         {tag} · {f['time_ago']}")
            else:
                lines.append(f"         {tag}")
            # 第四行: AI 分析
            if f.get('ai_trend'):
                lines.append(f"         {f['ai_trend']}")

        total_flights += len(flights)

    lines.append(f"\n{'=' * 20}")
    lines.append(f"📌 共 {len(alert_summary)} 条航线日期, {total_flights} 个航班")
    return "\n".join(lines)


# ============================================================
#  携程页面抓取
# ============================================================

def create_page(headless=True):
    """创建浏览器页面实例，带反检测配置

    使用持久化 Chrome 用户目录保存 Cookie 和浏览器指纹，
    多轮运行之间保持身份一致，绕过携程反爬。
    """
    co = ChromiumOptions()

    # 反检测：去掉自动化标记
    co.set_argument('--disable-blink-features=AutomationControlled')
    co.set_argument('--no-sandbox')
    co.set_argument('--disable-gpu')
    # 禁用帧率限制，headless 下虚拟列表需要正常帧率
    co.set_argument('--disable-frame-rate-limit')
    co.set_argument('--disable-background-timer-throttling')
    # 禁用自动化提示（navigator.webdriver = false）
    co.set_argument('--disable-features=AutomationControlled')
    co.set_pref('excludeSwitches', ['enable-automation'])
    co.set_pref('useAutomationExtension', False)
    # 隐藏 headless 特征
    if headless:
        co.set_argument('--headless=new')  # 新版 headless 模式，指纹更接近真实

    # 轮换 User-Agent（多版本 Chrome，减少指纹一致性）
    ua_list = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36',
    ]
    co.set_argument(f'--user-agent={random.choice(ua_list)}')

    # 随机窗口尺寸（微调，模拟真实用户屏幕差异）
    w = random.randint(1880, 1940)
    h = random.randint(1040, 1100)
    co.set_argument(f'--window-size={w},{h}')

    # 持久化用户数据目录（保存 Cookie、LocalStorage、浏览器指纹）
    if config.CHROME_USER_DATA_PATH:
        user_dir = os.path.abspath(config.CHROME_USER_DATA_PATH)
        os.makedirs(user_dir, exist_ok=True)
        co.set_user_data_path(user_dir)
        co.auto_port()  # 自动选择调试端口，避免多实例冲突

    if headless:
        co.headless()

    return WebPage(chromium_options=co)


def build_ctrip_url(dep_code, arr_code, dep_date):
    """构建携程单程机票搜索URL"""
    return (
        f'https://flights.ctrip.com/online/list/oneway-{dep_code}-{arr_code}'
        f'?depdate={dep_date}&cabin=y_s_c_f&adult=1&child=0&infant=0'
    )


def crawl_flights_page(dep_code, arr_code, dep_date, page=None, headless=True, debug=False):
    """抓取携程指定航线+日期的航班列表页面

    Args:
        page: 复用的 WebPage 实例（传入则不创建/销毁，由调用方管理生命周期）
        headless: 仅在 page 为 None 时生效
        debug: 保存调试 HTML

    返回: BeautifulSoup 对象，异常时返回 None
    """
    own_page = False  # 是否本函数自行创建了 page
    try:
        if page is None:
            page = create_page(headless)
            own_page = True

        url = build_ctrip_url(dep_code, arr_code, dep_date)
        print(f"   🌐 加载页面: {dep_code}→{arr_code} {dep_date}")
        page.get(url)
        # 随机等待页面 JS 渲染（2~5 秒抖动）
        sleep(random.uniform(2, 5))

        # 检测是否被携程拦截（页面白屏 / 无内容）
        html_snippet = page.html[:5000] if page.html else ""
        if len(html_snippet) < 500 or ('flight-box' not in html_snippet and 'flight' not in html_snippet.lower()):
            # 可能被拦截，额外等待再检查
            sleep(random.uniform(3, 6))
            if len(page.html or '') < 500:
                print(f"   ⚠️ 页面内容异常（仅 {len(page.html or '')} 字符），可能被拦截")

        # 调试模式：保存原始 HTML（滚动前）
        if debug:
            debug_file = f"debug_{dep_code}_{arr_code}_{dep_date}.html"
            with open(debug_file, 'w', encoding='utf-8') as f:
                f.write(page.html)
            print(f"      🐛 调试HTML已保存: {debug_file}")
            html = page.html
            for keyword in ['flight-box', 'flightbox', 'flight', 'price', 'airline', 'depart', 'arrive']:
                count = html.count(keyword)
                if count > 0:
                    print(f"      🔍 class '{keyword}' 出现 {count} 次")

        # 滚动加载全部航班（携程虚拟列表需真实滚动 + dispatchEvent 触发渲染）
        last_count = 0
        stable_rounds = 0
        for i in range(max(config.SCROLL_TIMES, 40)):  # 至少 40 轮
            sleep(config.SCROLL_DELAY_SECONDS)
            try:
                page.run_js("""
                    window.scrollBy(0, 800);
                    // 派发 scroll 事件，确保携程虚拟列表收到
                    window.dispatchEvent(new Event('scroll', {bubbles: true}));
                    // 也尝试直接设置列表 scrollTop
                    const lists = document.querySelectorAll('.flight-list');
                    lists.forEach(l => { l.scrollTop += l.clientHeight || 800; });
                """)
            except Exception:
                page.scroll.to_bottom()
            # 再调用原生滚动兜底
            try:
                page.scroll.to_bottom()
            except Exception:
                pass
            # 检查是否有新航班加载
            cur_count = page.html.count('flight-box')
            if cur_count == last_count:
                stable_rounds += 1
                if stable_rounds >= 5:  # 连续5轮无新数据
                    break
            else:
                stable_rounds = 0
                last_count = cur_count
            if debug:
                print(f"      📜 第{i+1}轮滚动, flight-box: {cur_count}")

        # 滚动完后回顶部再慢慢滚一遍，让虚拟列表完整渲染每个航班
        try:
            page.run_js("window.scrollTo(0, 0);")
            sleep(2)
            for _ in range(6):
                page.run_js("window.scrollBy(0, 600);")
                sleep(1.5)
        except Exception:
            pass

        # 最终等待，确保 DOM 完全稳定
        sleep(3)

        # 调试模式：保存滚动后的 HTML
        if debug:
            debug_file2 = f"debug_{dep_code}_{arr_code}_{dep_date}_scrolled.html"
            with open(debug_file2, 'w', encoding='utf-8') as f:
                f.write(page.html)
            print(f"      🐛 滚动后HTML已保存: {debug_file2}")

        soup = BeautifulSoup(page.html, "html.parser")
        return soup

    except Exception as e:
        print(f"   ❌ 页面抓取失败: {e}")
        return None
    finally:
        if own_page and page:
            try:
                page.quit()
            except Exception:
                pass


# ============================================================
#  航班数据解析
# ============================================================

def parse_single_flight(flight_div):
    """解析单个航班 div 为结构化数据

    返回: dict 或 None（解析失败/红眼航班）
    """
    try:
        # 航空公司 —— 新版 DOM 使用 airline-name（dash），旧版 airlineName 已废弃
        airline_el = flight_div.find("div", {"class": "airline-name"})
        if airline_el:
            airline = airline_el.text.strip()
        else:
            # 兼容旧版
            airline_divs = flight_div.find_all("div", {"class": "airlineName"})
            airline = '/'.join([d.get_text(strip=True) for d in airline_divs]) if airline_divs else ""

        if not airline:
            return None

        # 出发信息
        depart_box = flight_div.find("div", {"class": "depart-box"})
        if not depart_box:
            return None
        dep_airport_el = depart_box.find("div", {"class": "airport"})
        dep_time_el = depart_box.find("div", {"class": "time"})
        departure_airport = dep_airport_el.text.strip() if dep_airport_el else ""
        departure_time = dep_time_el.text.strip() if dep_time_el else ""

        # 到达信息
        arrive_box = flight_div.find("div", {"class": "arrive-box"})
        if not arrive_box:
            return None
        arr_airport_el = arrive_box.find("div", {"class": "airport"})
        arr_time_el = arrive_box.find("div", {"class": "time"})
        arrival_airport = arr_airport_el.text.strip() if arr_airport_el else ""
        arrival_time = arr_time_el.text.strip() if arr_time_el else ""

        # 航班号 —— 携程新版 DOM 在 span.plane-No 中
        plane_no_el = flight_div.find("span", {"class": "plane-No"})
        if plane_no_el:
            plane_text = plane_no_el.text.strip()
            # 优先匹配航班号格式（如 "KN5967 Boeing..." → KN5967, "南航 CZ3112" → CZ3112）
            m = re.search(r'([A-Z0-9]{2}\d{2,4})', plane_text)
            flight_no = m.group(1) if m else plane_text.split()[0]
        else:
            flight_no = ""

        # 备选：从 id 属性中提取航班号（如 flightInfo-NS8015_xxx → NS8015）
        if not flight_no:
            for elem in flight_div.find_all(attrs={"id": True}):
                m = re.search(r'(?:flightInfo|comfort|departureTerminal|arrivalTerminal)[-_]'
                              r'([A-Z0-9]{2}\d{2,4})', elem.get('id', ''))
                if m:
                    flight_no = m.group(1)
                    break

        # 经停信息（只在有经停的航班中才有内容）
        info_el = flight_div.find("div", {"class": "transfer-info-group"})
        flight_info = info_el.text.strip() if info_el else ""

        # 价格（支持多种选择器）
        price_el = (flight_div.find("span", {"class": "price"}) or
                    flight_div.find("div", {"class": "price"}))
        price_str = price_el.text.strip() if price_el else ""
        price = parse_price(price_str)

        if not price or not airline:
            return None

        # 过滤红眼航班（出发时间在 23:00 ~ 06:00）
        if is_red_eye(departure_time):
            return None

        # 过滤通程/中转航班
        if config.DIRECT_FLIGHTS_ONLY:
            if airline == "通程航班":
                return None
            # 有经停/中转信息（transfer-info-group 有内容 = 非直飞）
            if flight_info:
                return None
            # 航空公司字段包含航班号格式 = 多段拼接航班
            if re.search(r'\b[A-Z0-9]{2}\d{2,4}[A-Z]?\b', airline):
                return None

        # 过滤共享/代码共享航班（DOM 中有 <span class="plane-share">共享</span>）
        if flight_div.find("span", string=lambda t: t and "共享" in t):
            return None

        return {
            'airline': airline,
            'flight_no': flight_no,
            'departure_airport': departure_airport,
            'arrival_airport': arrival_airport,
            'departure_time': departure_time,
            'arrival_time': arrival_time,
            'flight_info': flight_info,
            'price': price,
        }

    except Exception as e:
        # 解析异常，静默跳过
        return None


def parse_all_flights(soup):
    """从页面 BeautifulSoup 中解析所有有效航班

    返回: 航班 dict 列表（已过滤红眼航班）
    """
    if not soup:
        return []

    flight_boxes = soup.find_all("div", {"class": "flight-box"})
    if not flight_boxes or len(flight_boxes) <= 1:
        # 只有1个或0个（第一个可能不是实际航班）
        return []

    flights = []
    red_eye_count = 0

    for box in flight_boxes:
        flight = parse_single_flight(box)
        if flight:
            flights.append(flight)
        elif box.find("div", {"class": "depart-box"}):
            # 能解析到出发信息但被过滤了，大概率是红眼航班
            red_eye_count += 1

    if red_eye_count > 0:
        print(f"      🔇 已过滤 {red_eye_count} 个红眼航班")

    # 去重：同一出发+到达时间 = 代码共享航班，只保留价格最低的（通常为实际承运航司）
    seen = {}
    for f in flights:
        key = (f['departure_time'], f['arrival_time'])
        if key not in seen or f['price'] < seen[key]['price']:
            seen[key] = f
    deduped = list(seen.values())
    if len(deduped) < len(flights):
        print(f"      🔇 已过滤 {len(flights) - len(deduped)} 个代码共享航班")

    return deduped


# ============================================================
#  主监控逻辑
# ============================================================

def monitor_all_routes(debug=False):
    """执行一轮完整的航线监控

    使用单个浏览器实例复用于所有请求，配合 Chrome 用户数据目录
    持久化 Cookie 和指纹，绕过携程反爬。

    返回: {
        'all_flights': dict,   # 所有抓取到的航班
        'alerts': list,        # 价格变动告警
        'stats': dict,         # 统计信息
    }
    """
    all_flights = {}   # key: (from_name, to_name, date)
    alerts = []
    alert_summary = {}  # 告警航线+日期的全部航班（含变动信息）
    stats = {
        'routes_checked': 0,
        'flights_found': 0,
        'alerts_generated': 0,
        'errors': 0,
    }

    dates = get_monitor_dates()
    now = datetime.now()

    # 整轮监控统一 crawl_time，保证同一批次数据时间戳一致
    batch_crawl_time = database.now_beijing()

    # 构建所有 (route, date) 组合，分为优先和常规两组
    has_priority = bool(config.ALERT_ROUTES and config.ALERT_DATES)
    priority_items = []
    normal_items = []
    # MONITOR_DAYS_AHEAD=0 且有告警航线时，只爬告警航线（不爬无关航线）
    only_alert_routes = (config.MONITOR_DAYS_AHEAD == 0 and config.ALERT_ROUTES)
    for route in config.ROUTES:
        route_key = (route['from'], route['to'])
        if only_alert_routes and route_key not in config.ALERT_ROUTES:
            continue
        # alert_only 航线：只在告警日期爬取，不爬满30天
        is_alert_only = route.get('alert_only', False)
        for date_str in dates:
            if is_alert_only and date_str not in config.ALERT_DATES:
                continue  # alert_only 航线跳过非告警日期
            is_priority = (has_priority and
                          route_key in config.ALERT_ROUTES and
                          date_str in config.ALERT_DATES)
            if is_priority:
                priority_items.append((route, date_str))
            else:
                normal_items.append((route, date_str))

    if has_priority and priority_items:
        print(f"   🔔 {len(priority_items)} 个优先项（告警航线+日期），将优先处理并立即推送")

    # 创建共享浏览器实例（整轮复用，含持久化用户数据）
    shared_page = None
    try:
        shared_page = create_page(config.HEADLESS)
        print(f"   🌐 浏览器已启动（{'无头' if config.HEADLESS else '可见'}模式）")
    except Exception as e:
        print(f"   ⚠️ 共享浏览器启动失败: {e}，将逐次创建独立浏览器")

    # ---- 内部函数：处理单条航线+日期 ----
    def _process_route(route, date_str, is_priority):
        """爬取、解析、存储、比价一条航线+日期组合，支持空结果重试"""
        stats['routes_checked'] += 1
        key = (route['from_name'], route['to_name'], date_str)

        tag = "🔔" if is_priority else "  "

        # 抓取页面（支持重试）
        soup = None
        flights = []
        max_retries = config.MAX_RETRY_ON_EMPTY if not debug else 0
        for attempt in range(1 + max_retries):
            soup = crawl_flights_page(
                route['from'], route['to'], date_str,
                page=shared_page,
                headless=config.HEADLESS,
                debug=debug,
            )

            if not soup:
                if attempt < max_retries:
                    wait = random.uniform(config.RETRY_DELAY_MIN, config.RETRY_DELAY_MAX)
                    print(f"   🔄 页面加载失败，{wait:.0f}秒后重试 ({attempt+1}/{max_retries})...")
                    sleep(wait)
                continue

            # 解析航班
            flights = parse_all_flights(soup)
            if len(flights) > 0:
                break  # 有数据，退出重试循环

            # 0 个航班：可能被影子封禁或真的没航班
            if attempt < max_retries:
                wait = random.uniform(config.RETRY_DELAY_MIN, config.RETRY_DELAY_MAX)
                print(f"   🔄 获取 0 个航班，{wait:.0f}秒后重试 ({attempt+1}/{max_retries})...")
                sleep(wait)
            elif max_retries > 0:
                print(f"   ⚠️ 重试 {max_retries} 次后仍为 0 个航班")

        if not soup:
            stats['errors'] += 1
            print(f"   ⚠️ 跳过: {route['from_name']}→{route['to_name']} {date_str}")
            return

        print(f"   {tag} ✅ 获取 {len(flights)} 个有效航班（已排除红眼）")
        stats['flights_found'] += len(flights)

        # 存储 + 比价（使用整轮统一的 batch_crawl_time）
        for flight in flights:
            # 查询上次价格
            last = database.get_last_price(
                flight['flight_no'],
                route['from'],
                route['to'],
                date_str
            )

            # 插入新记录
            database.insert_price({
                'route_from': route['from'],
                'route_to': route['to'],
                'route_from_name': route['from_name'],
                'route_to_name': route['to_name'],
                'flight_date': date_str,
                'flight_no': flight['flight_no'],
                'airline': flight['airline'],
                'departure_airport': flight['departure_airport'],
                'arrival_airport': flight['arrival_airport'],
                'departure_time': flight['departure_time'],
                'arrival_time': flight['arrival_time'],
                'price': flight['price'],
            }, crawl_time=batch_crawl_time)

            # 检测价格变动（航线+日期双过滤，空列表=不过滤）
            if flight['flight_no'] and last and last['price'] != flight['price']:
                route_key = (route['from'], route['to'])
                route_ok = (not config.ALERT_ROUTES or route_key in config.ALERT_ROUTES)
                date_ok = (not config.ALERT_DATES or date_str in config.ALERT_DATES)
                if route_ok and date_ok and should_alert(last['price'], flight['price']):
                    alert_info = database.insert_alert(
                        flight['flight_no'],
                        route['from'],
                        route['to'],
                        date_str,
                        last['price'],
                        flight['price'],
                    )
                    alert_entry = {
                        **flight,
                        'flight_date': date_str,
                        'from_name': route['from_name'],
                        'to_name': route['to_name'],
                        'old_price': last['price'],
                        'new_price': flight['price'],
                        'change_amount': alert_info['change_amount'],
                        'change_percent': alert_info['change_percent'],
                        'route_from': route['from'],
                        'route_to': route['to'],
                    }
                    # 调用 AI 趋势预测（内部自动处理数据不足/API失败等情况）
                    trend = predictor.predict_trend(alert_entry)
                    if trend:
                        alert_entry['ai_trend'] = trend
                    alerts.append(alert_entry)
                    stats['alerts_generated'] += 1

            # 告警航线+日期：收集全部航班（含变动信息），用于推送完整报告
            if is_priority:
                change_info = {
                    **flight,
                    'flight_date': date_str,
                    'from_name': route['from_name'],
                    'to_name': route['to_name'],
                }
                if last:
                    change_info['last_price'] = last['price']
                    change_info['change_amount'] = flight['price'] - last['price']
                    if last['price'] > 0:
                        change_info['change_percent'] = round(
                            (flight['price'] - last['price']) / last['price'] * 100, 1)
                    else:
                        change_info['change_percent'] = 0
                    # 距上次爬取多久
                    last_time = datetime.strptime(last['crawl_time'], '%Y-%m-%d %H:%M:%S')
                    delta = now - last_time
                    if delta.days > 0:
                        change_info['time_ago'] = f"{delta.days}天前"
                    elif delta.seconds >= 3600:
                        change_info['time_ago'] = f"{delta.seconds // 3600}小时前"
                    elif delta.seconds >= 60:
                        change_info['time_ago'] = f"{delta.seconds // 60}分钟前"
                    else:
                        change_info['time_ago'] = "刚刚"
                    # 价格变动时调用 AI 趋势预测
                    if last['price'] != flight['price']:
                        trend = predictor.predict_trend({
                            **change_info,
                            'route_from': route['from'],
                            'route_to': route['to'],
                            'old_price': last['price'],
                            'new_price': flight['price'],
                        })
                        if trend:
                            change_info['ai_trend'] = trend
                else:
                    change_info['last_price'] = None
                    change_info['change_amount'] = 0
                    change_info['change_percent'] = 0
                    change_info['time_ago'] = None  # 新航班
                alert_summary.setdefault(key, []).append(change_info)

        all_flights[key] = flights

    # ================================================================
    #  阶段1：优先爬取告警航线+日期 → 爬完立即推送
    # ================================================================
    pushed_summary_keys = set()

    try:
        if priority_items:
            print(f"\n   ╔{'═'*50}╗")
            print(f"   ║  🔔 阶段1: 优先处理 {len(priority_items)} 个告警项")
            print(f"   ╚{'═'*50}╝")

            for route, date_str in priority_items:
                _process_route(route, date_str, is_priority=True)
                random_delay()

            # 优先项全部爬完 → 立即推送，不等剩余航线
            if alert_summary:
                push_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                msg = format_alert_summary_message(alert_summary, push_time)
                print(f"\n   {'─'*50}")
                print(f"   📤 优先航线数据已就绪，立即推送（其他航线继续后台爬取）")
                print(f"   {'─'*50}")
                if config.CONSOLE_OUTPUT:
                    print(f"\n{msg}")
                send_to_feishu(msg)
                pushed_summary_keys = set(alert_summary.keys())

        # ================================================================
        #  阶段2：爬取剩余航线+日期
        # ================================================================
        if normal_items:
            print(f"\n   ╔{'═'*50}╗")
            print(f"   ║  📋 阶段2: 处理剩余 {len(normal_items)} 个项目")
            print(f"   ╚{'═'*50}╝")

            for route, date_str in normal_items:
                _process_route(route, date_str, is_priority=False)
                random_delay()

    finally:
        # 确保浏览器被关闭
        if shared_page:
            try:
                shared_page.quit()
                print(f"   🌐 浏览器已关闭")
            except Exception:
                pass

    return {
        'all_flights': all_flights,
        'alerts': alerts,
        'alert_summary': alert_summary,
        'pushed_summary_keys': pushed_summary_keys,
        'stats': stats,
    }


def run_once(debug=False):
    """执行单次监控（供手动调用和调度使用）"""
    run_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"\n{'='*55}")
    print(f"🛫 Flight-Monitor · {run_time}")
    if debug:
        print(f"🐛 调试模式: 开启")
    print(f"{'='*55}")

    # 初始化数据库
    database.init_db()

    # 执行监控
    result = monitor_all_routes(debug=debug)
    stats = result['stats']

    # 记录日志
    database.insert_monitor_log(
        stats['routes_checked'],
        stats['flights_found'],
        stats['alerts_generated'],
    )

    # 清理过期记录
    database.cleanup_old_records()
    database.cleanup_expired_alerts()

    # ---- 输出结果 ----

    # 告警航线+日期：推送完整航班报告（全部航班+变动信息）
    # 注意：阶段1已推送过优先项则跳过，避免重复推送
    if result.get('alert_summary') and not result.get('pushed_summary_keys'):
        msg = format_alert_summary_message(result['alert_summary'], run_time)
        if config.CONSOLE_OUTPUT:
            print(f"\n{msg}")
        send_to_feishu(msg)

    # 常规价格变动告警（排除已在阶段1推送过的航线+日期，避免重复）
    pushed_keys = result.get('pushed_summary_keys', set())
    summary_keys = pushed_keys or set(result.get('alert_summary', {}).keys())
    remaining_alerts = [a for a in result.get('alerts', [])
                        if (a['from_name'], a['to_name'], a['flight_date']) not in summary_keys]
    if remaining_alerts:
        msg = format_alert_message(remaining_alerts, run_time)
        if config.CONSOLE_OUTPUT:
            print(f"\n{msg}")
        send_to_feishu(msg)
    elif not result.get('alert_summary'):
        print(f"✅ 无价格变动（超过阈值） | "
              f"已检查 {stats['routes_checked']} 条航线日期, {stats['flights_found']} 个航班")

    # 统计摘要
    print(f"\n📊 本轮统计: "
          f"航线日期={stats['routes_checked']}, "
          f"航班={stats['flights_found']}, "
          f"告警={stats['alerts_generated']}, "
          f"错误={stats['errors']}")

    # Windows 通知
    show_windows_toast(
        "Flight-Monitor · 本轮完成 ✅",
        f"{stats['routes_checked']} 条航线日期 | {stats['flights_found']} 个航班\n"
        f"告警: {stats['alerts_generated']} | 错误: {stats['errors']}"
    )

    return result


def run_scheduled():
    """定时循环监控"""
    print(f"\n🚀 启动定时监控")
    print(f"   航线: {len(config.ROUTES)} 条")

    # 显示日期范围（常规日期 + 告警日期合并）
    monitor_dates = get_monitor_dates()
    for d in config.ALERT_DATES:
        if d not in monitor_dates:
            monitor_dates.append(d)
    monitor_dates.sort()
    if not monitor_dates:
        print(f"   ❌ 无日期可监控。请在 config.py 中设置 MONITOR_DAYS_AHEAD >= 1 或配置 ALERT_DATES。")
        return
    print(f"   日期范围: {monitor_dates[0]} ~ {monitor_dates[-1]} (共{len(monitor_dates)}天)")

    print(f"   每轮请求: {len(config.ROUTES)}条航线 × {len(monitor_dates)}天 = {len(config.ROUTES) * len(monitor_dates)}次")
    print(f"   监控间隔: {config.MONITOR_INTERVAL_MINUTES} 分钟")
    print(f"   红眼过滤: {config.RED_EYE_START_HOUR}:00 ~ {config.RED_EYE_END_HOUR}:00")
    print(f"   告警阈值: 变动 ≥ {config.PRICE_CHANGE_THRESHOLD_PCT}% 且 ≥ ¥{config.PRICE_CHANGE_THRESHOLD_MIN}")
    if config.ALERT_ROUTES:
        route_names = [f"{r[0]}→{r[1]}" for r in config.ALERT_ROUTES]
        print(f"   告警航线: {', '.join(route_names)}")
    else:
        print(f"   告警航线: 全部（{len(config.ROUTES)}条）")
    if config.ALERT_DATES:
        print(f"   告警日期: {', '.join(config.ALERT_DATES)}")
    else:
        print(f"   告警日期: 全部（{len(monitor_dates)}天）")

    if config.FEISHU_WEBHOOK:
        print(f"   飞书推送: ✅ 已配置")
    else:
        print(f"   飞书推送: ❌ 未配置（仅控制台输出）")

    print(f"\n   按 Ctrl+C 停止监控\n")

    # 弹窗提示启动成功
    show_windows_toast(
        "Flight-Monitor 已启动 ✅",
        f"航线: {len(config.ROUTES)} 条 | 间隔: {config.MONITOR_INTERVAL_MINUTES} 分钟\n"
        f"日期: {monitor_dates[0]} ~ {monitor_dates[-1]}"
    )

    while True:
        try:
            run_once()
        except KeyboardInterrupt:
            print("\n⏹️ 监控已停止")
            show_windows_toast("Flight-Monitor 已停止 ⏹️", "监控进程已退出，下次开机登录后会自动重启")
            break
        except Exception as e:
            print(f"❌ 监控异常: {e}")
            database.insert_monitor_log(0, 0, 0, status='ERROR', error_msg=str(e))
            show_windows_toast("Flight-Monitor 异常 ❌", f"发生错误: {e}\n将在 {config.MONITOR_INTERVAL_MINUTES} 分钟后重试", is_error=True)

        # 等待下一次
        next_run = datetime.now() + timedelta(minutes=config.MONITOR_INTERVAL_MINUTES)
        print(f"\n⏰ 下次监控: {next_run.strftime('%H:%M:%S')} "
              f"(等待 {config.MONITOR_INTERVAL_MINUTES} 分钟)...")
        sleep(config.MONITOR_INTERVAL_MINUTES * 60)


# ============================================================
#  入口
# ============================================================

if __name__ == "__main__":
    import sys

    # 初始化日志文件（控制台 + 文件双通道）
    setup_logging()

    # 计算日期信息（常规日期 + 告警日期，去重排序）
    monitor_dates = get_monitor_dates()
    # 告警日期始终纳入监控范围（即使 MONITOR_DAYS_AHEAD=0）
    for d in config.ALERT_DATES:
        if d not in monitor_dates:
            monitor_dates.append(d)
    monitor_dates.sort()
    total_requests = len(config.ROUTES) * len(monitor_dates)

    print(f"\n{'='*55}")
    print(f"  ✈️  Flight-Monitor v2.0")
    print(f"  航线: {len(config.ROUTES)} 条")

    if monitor_dates:
        print(f"  日期: {monitor_dates[0]} ~ {monitor_dates[-1]} (共{len(monitor_dates)}天)")
    else:
        print(f"  日期: (无)")

    print(f"  红眼过滤: {config.RED_EYE_START_HOUR}:00~{config.RED_EYE_END_HOUR}:00")
    print(f"  每轮请求: {total_requests} 次（{len(config.ROUTES)}航线 × {len(monitor_dates)}天）")
    print(f"{'='*55}")

    if not monitor_dates:
        print(f"❌ 没有要监控的日期。请在 config.py 中设置 MONITOR_DAYS_AHEAD >= 1 或配置 ALERT_DATES。")
        sys.exit(1)

    if "--once" in sys.argv:
        # 单次运行模式：始终显示汇总
        debug_mode = "--debug" in sys.argv
        print("  模式: 单次抓取\n")
        run_once(debug=debug_mode)
        print("\n✅ 单次抓取完成")
    elif "--setup" in sys.argv:
        # 初始化 Chrome 用户数据目录：打开可见浏览器让用户手动浏览携程建立身份
        print("""
   ╔══════════════════════════════════════════════════╗
   ║  🔧 Chrome 身份初始化                            ║
   ║                                                  ║
   ║  即将打开 Chrome 浏览器，请按以下步骤操作：        ║
   ║  1. 浏览器会自动打开携程首页                      ║
   ║  2. 搜索一条航线（比如 北京→泉州）                 ║
   ║  3. 随便点点，翻翻页面，模拟真实用户               ║
   ║  4. (可选) 登录携程账号                           ║
   ║  5. 完成后回到终端，按 Enter 保存身份并退出        ║
   ║                                                  ║
   ║  之后爬虫将复用此身份，不再被识别为机器             ║
   ╚══════════════════════════════════════════════════╝
   """)
        input("   按 Enter 开始...")

        page = None
        try:
            page = create_page(headless=False)
            print("   🌐 打开携程首页...")
            page.get("https://flights.ctrip.com/")
            print("   ✅ 浏览器已打开，请在上面手动搜索和浏览")
            print("   完成后回到此窗口，按 Enter 保存并退出...")
            input()
            print("   💾 Chrome 身份已保存到:", os.path.abspath(config.CHROME_USER_DATA_PATH))
        except Exception as e:
            print(f"   ❌ 初始化失败: {e}")
        finally:
            if page:
                try:
                    page.quit()
                except Exception:
                    pass
    elif "--help" in sys.argv or "-h" in sys.argv:
        print("""
用法:
  python main.py                    持续监控模式（默认），按配置的间隔循环运行
  python main.py --once             单次抓取模式，运行一次后退出
  python main.py --once --debug     调试模式，保存页面HTML并打印DOM诊断信息
  python main.py --setup            初始化 Chrome 身份（首次使用前必须运行一次）
  python main.py --help             显示此帮助信息

配置:
  编辑 config.py 修改航线、日期、监控间隔、飞书 Webhook 等
  首次运行会自动创建 flight_monitor.db 数据库文件

反爬说明:
  首次部署或遇到封禁后，请先运行 python main.py --setup
  在打开的浏览器中手动浏览携程，建立真实 Cookie 和指纹
  之后爬虫将复用该 Chrome 用户数据目录，每次请求使用同一身份
        """)
    else:
        # 持续监控模式
        run_scheduled()
