# -*- coding: utf-8 -*-
"""
EDA 质量探针（只读）—— 合并自最初临时的 db_probe2/3/4
检查：同批次同价共振、高值价格（全价舱口径）、告警覆盖范围、500/520 平台价堆集。
用法: python quality_probes.py
"""
import sqlite3

DB = r'D:\Flight-Monitor\flight_monitor.db'


def main():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    print('--- 1) 同批次同价(>=5航班) top 30 ---')
    for r in cur.execute('''SELECT crawl_time, price, COUNT(DISTINCT flight_no) nf, COUNT(*) n
                            FROM flight_prices GROUP BY crawl_time, price
                            HAVING COUNT(DISTINCT flight_no) >= 5
                            ORDER BY n DESC LIMIT 30'''):
        print(f'  {r[0]}  price={r[1]}  flights={r[2]} rows={r[3]}')

    print('\n--- 2) 高值价格出现统计（疑似全价舱/口径跳变） ---')
    for r in cur.execute('''SELECT price, COUNT(*) n, COUNT(DISTINCT flight_no) nf,
                            MIN(crawl_time) cmin, MAX(crawl_time) cmax
                            FROM flight_prices WHERE price IN (3090,3460,5230,3150,2089)
                            GROUP BY price ORDER BY price'''):
        print(' ', dict(r))

    print('\n--- 3) price_alerts 按航向/日期（87 条全部为 泉州→北京） ---')
    for r in cur.execute('''SELECT route_from, route_to, substr(alert_time,1,10) d, COUNT(*) n
                            FROM price_alerts GROUP BY 1,2,3 ORDER BY d'''):
        print(' ', dict(r))

    tot = cur.execute('SELECT COUNT(*) FROM flight_prices').fetchone()[0]
    n52 = cur.execute("SELECT COUNT(*) FROM flight_prices WHERE price IN (500,520)").fetchone()[0]
    print(f'\n--- 4) price in (500,520): {n52} / {tot} = {100*n52/tot:.1f}% ---')

    print('\n--- 5) 高值(>=2000)快照按 lead 分布（集中 lead<=3） ---')
    for r in cur.execute('''SELECT (julianday(flight_date) - julianday(substr(crawl_time,1,10))) ld,
                            COUNT(*) n FROM flight_prices WHERE price >= 2000
                            GROUP BY 1 ORDER BY ld'''):
        print(f'  lead={r[0]:.0f}  n={r[1]}')

    print('\n--- 6) 500/520 按航司 ---')
    for r in cur.execute('''SELECT airline, price, COUNT(*) n, COUNT(DISTINCT flight_no) nf
                            FROM flight_prices WHERE price IN (500,520)
                            GROUP BY airline, price ORDER BY n DESC'''):
        print(' ', dict(r))

    conn.close()


if __name__ == '__main__':
    main()
