"""
Flight-Monitor - 数据库模块
使用 SQLite 存储所有航班价格记录
"""

import sqlite3
import config
from datetime import datetime, timedelta, timezone


def now_beijing():
    """返回当前北京时间字符串 (UTC+8)"""
    return datetime.now(timezone.utc).astimezone(
        timezone(timedelta(hours=8))
    ).strftime('%Y-%m-%d %H:%M:%S')


def get_connection():
    """获取数据库连接"""
    conn = sqlite3.connect(config.DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """初始化数据库表结构"""
    conn = get_connection()
    cursor = conn.cursor()

    # 航班价格记录表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS flight_prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            route_from TEXT NOT NULL,
            route_to TEXT NOT NULL,
            route_from_name TEXT,
            route_to_name TEXT,
            flight_date TEXT NOT NULL,
            flight_no TEXT NOT NULL,
            airline TEXT,
            departure_airport TEXT,
            arrival_airport TEXT,
            departure_time TEXT,
            arrival_time TEXT,
            price INTEGER NOT NULL,
            crawl_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 价格变动告警记录表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS price_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            flight_no TEXT NOT NULL,
            route_from TEXT NOT NULL,
            route_to TEXT NOT NULL,
            flight_date TEXT NOT NULL,
            old_price INTEGER,
            new_price INTEGER,
            change_amount INTEGER,
            change_percent REAL,
            alert_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            notified INTEGER DEFAULT 0
        )
    """)

    # 监控运行日志
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS monitor_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            routes_checked INTEGER,
            flights_found INTEGER,
            alerts_generated INTEGER,
            status TEXT,
            error_msg TEXT
        )
    """)

    # 索引优化查询
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_flight_prices_lookup
        ON flight_prices(route_from, route_to, flight_date, flight_no)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_flight_prices_crawl_time
        ON flight_prices(crawl_time)
    """)

    conn.commit()
    conn.close()


def insert_price(record):
    """插入一条航班价格记录

    record: dict {
        route_from, route_to, route_from_name, route_to_name,
        flight_date, flight_no, airline,
        departure_airport, arrival_airport,
        departure_time, arrival_time, price
    }
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO flight_prices
            (route_from, route_to, route_from_name, route_to_name,
             flight_date, flight_no, airline,
             departure_airport, arrival_airport,
             departure_time, arrival_time, price, crawl_time)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        record['route_from'],
        record['route_to'],
        record.get('route_from_name', ''),
        record.get('route_to_name', ''),
        record['flight_date'],
        record['flight_no'],
        record.get('airline', ''),
        record.get('departure_airport', ''),
        record.get('arrival_airport', ''),
        record.get('departure_time', ''),
        record.get('arrival_time', ''),
        record['price'],
        now_beijing(),
    ))
    conn.commit()
    conn.close()


def get_last_price(flight_no, route_from, route_to, flight_date):
    """获取同一航班上一次抓取的价格（最近的一条记录）"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT price, crawl_time FROM flight_prices
        WHERE flight_no = ? AND route_from = ? AND route_to = ? AND flight_date = ?
        ORDER BY crawl_time DESC
        LIMIT 1
    """, (flight_no, route_from, route_to, flight_date))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {'price': row['price'], 'crawl_time': row['crawl_time']}
    return None


def insert_alert(flight_no, route_from, route_to, flight_date,
                 old_price, new_price):
    """插入一条价格告警记录"""
    change_amount = new_price - old_price
    if old_price > 0:
        change_percent = round(change_amount / old_price * 100, 2)
    else:
        change_percent = 0

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO price_alerts
            (flight_no, route_from, route_to, flight_date,
             old_price, new_price, change_amount, change_percent, alert_time)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (flight_no, route_from, route_to, flight_date,
          old_price, new_price, change_amount, change_percent, now_beijing()))
    conn.commit()
    conn.close()

    return {
        'change_amount': change_amount,
        'change_percent': change_percent,
    }


def insert_monitor_log(routes_checked, flights_found, alerts_generated,
                       status='OK', error_msg=''):
    """记录监控运行日志"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO monitor_log
            (routes_checked, flights_found, alerts_generated, status, error_msg, run_time)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (routes_checked, flights_found, alerts_generated, status, error_msg, now_beijing()))
    conn.commit()
    conn.close()


def get_price_history(flight_no, route_from, route_to, flight_date, days=30):
    """获取某个航班的历史价格"""
    conn = get_connection()
    cursor = conn.cursor()
    cutoff = datetime.now() - timedelta(days=days)
    cursor.execute("""
        SELECT price, crawl_time FROM flight_prices
        WHERE flight_no = ? AND route_from = ? AND route_to = ? AND flight_date = ?
          AND crawl_time >= ?
        ORDER BY crawl_time ASC
    """, (flight_no, route_from, route_to, flight_date, cutoff.strftime('%Y-%m-%d %H:%M:%S')))
    rows = cursor.fetchall()
    conn.close()
    return [{'price': r['price'], 'time': r['crawl_time']} for r in rows]


def cleanup_expired_alerts():
    """清理已过期的告警记录（航班日期已过，告警无意义）"""
    conn = get_connection()
    cursor = conn.cursor()
    today = datetime.now().strftime('%Y-%m-%d')
    cursor.execute("DELETE FROM price_alerts WHERE flight_date < ?", (today,))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    if deleted:
        print(f"🧹 清理过期告警: {deleted}条（航班日期已过）")


def cleanup_old_records():
    """清理超过保留期限的历史记录"""
    if config.DB_RETENTION_DAYS <= 0:
        return

    conn = get_connection()
    cursor = conn.cursor()
    cutoff = datetime.now() - timedelta(days=config.DB_RETENTION_DAYS)
    cutoff_str = cutoff.strftime('%Y-%m-%d %H:%M:%S')

    cursor.execute("DELETE FROM flight_prices WHERE crawl_time < ?", (cutoff_str,))
    deleted_prices = cursor.rowcount

    cursor.execute("DELETE FROM price_alerts WHERE alert_time < ?", (cutoff_str,))
    deleted_alerts = cursor.rowcount

    cursor.execute("DELETE FROM monitor_log WHERE run_time < ?", (cutoff_str,))
    deleted_logs = cursor.rowcount

    conn.commit()
    conn.close()

    if any([deleted_prices, deleted_alerts, deleted_logs]):
        print(f"🧹 清理过期记录: {deleted_prices}条价格, "
              f"{deleted_alerts}条告警, {deleted_logs}条日志")
