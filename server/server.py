"""
Flight-Monitor Server — FastAPI 数据看板后端

提供 JWT 登录认证 + 航班价格数据 API。
直接读取 flight_monitor.db（与 main.py 共享数据库）。
"""

import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

import jwt
from fastapi import FastAPI, HTTPException, Request
from urllib.request import Request as UrlRequest, urlopen
from urllib.error import URLError
from fastapi.responses import FileResponse, JSONResponse

# DeepSeek API 配置（看板聊天专用，独立于 config.py）
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_CHAT_KEY", "")
DEEPSEEK_MODEL = "deepseek-chat"
from fastapi.staticfiles import StaticFiles

# ============================================================
#  配置
# ============================================================

# 数据库路径：优先环境变量，否则找 server/ 同级目录
DB_PATH = os.environ.get("FLIGHT_DB_PATH", str(Path(__file__).parent.parent / "flight_monitor.db"))
USERS_FILE = os.environ.get("USERS_FILE", str(Path(__file__).parent / "users.json"))
JWT_SECRET = os.environ.get("JWT_SECRET", "flight-monitor-jwt-secret-change-me")
JWT_EXPIRE_HOURS = 24

app = FastAPI(title="Flight-Monitor Dashboard")

# 静态文件目录
STATIC_DIR = Path(__file__).parent / "static"


# ============================================================
#  工具函数
# ============================================================

def get_db():
    """获取数据库连接（只读，WAL 模式兼容）"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def hash_password(password: str) -> str:
    """SHA256 哈希密码"""
    return hashlib.sha256(password.encode()).hexdigest()


def load_users() -> dict:
    """加载用户列表"""
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def create_token(username: str) -> str:
    """生成 JWT token"""
    payload = {
        "username": username,
        "exp": datetime.utcnow() + timedelta(hours=JWT_EXPIRE_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def verify_token(token: str) -> Optional[str]:
    """验证 JWT token，返回 username 或 None"""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        return payload.get("username")
    except Exception:
        return None


# ============================================================
#  认证中间件
# ============================================================

NO_AUTH = os.environ.get("NO_AUTH", "").lower() in ("1", "true", "yes")

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """JWT 鉴权中间件：/api/* 路由（除 /api/login）需要 Bearer token
    设置环境变量 NO_AUTH=1 可跳过鉴权（用于本地看板）"""
    # 本地模式跳过鉴权
    if NO_AUTH:
        request.state.username = "local"
        return await call_next(request)

    path = request.url.path

    # 放过静态文件和登录接口
    if not path.startswith("/api/") or path == "/api/login":
        return await call_next(request)

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return JSONResponse({"detail": "未登录，请先登录"}, status_code=401)

    token = auth_header[7:]
    username = verify_token(token)
    if not username:
        return JSONResponse({"detail": "登录已过期，请重新登录"}, status_code=401)

    # 将 username 注入请求上下文
    request.state.username = username
    return await call_next(request)


# ============================================================
#  API 路由
# ============================================================

@app.post("/api/login")
async def login(body: dict):
    """用户登录，返回 JWT token"""
    username = body.get("username", "").strip()
    password = body.get("password", "")

    if not username or not password:
        raise HTTPException(400, "请输入用户名和密码")

    users = load_users()
    stored_hash = users.get(username)

    if not stored_hash or stored_hash != hash_password(password):
        raise HTTPException(401, "用户名或密码错误")

    token = create_token(username)
    return {"token": token, "username": username}


@app.get("/api/routes")
async def get_routes(request: Request):
    """获取所有航线列表"""
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT DISTINCT route_from, route_to, route_from_name, route_to_name
            FROM flight_prices
            ORDER BY route_from_name, route_to_name
        """).fetchall()

        routes = []
        seen = set()
        for r in rows:
            key = (r["route_from"], r["route_to"])
            if key not in seen:
                seen.add(key)
                routes.append({
                    "from": r["route_from"],
                    "to": r["route_to"],
                    "from_name": r["route_from_name"] or r["route_from"],
                    "to_name": r["route_to_name"] or r["route_to"],
                })
        return {"routes": routes}
    finally:
        conn.close()


@app.get("/api/dates")
async def get_dates(request: Request, frm: str = "", to: str = ""):
    """获取某航线的所有日期"""
    if not frm or not to:
        return {"dates": []}

    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT DISTINCT flight_date
            FROM flight_prices
            WHERE route_from = ? AND route_to = ?
            ORDER BY flight_date DESC
        """, (frm, to)).fetchall()

        return {"dates": [r["flight_date"] for r in rows]}
    finally:
        conn.close()


def _is_flight_stopped(flight_date, crawl_time, global_max_crawl_time, last_price_is_none):
    """判断航班是否已停止更新

    满足任一条件即为停止：
    1. 航班日期已过（已起飞，不会再有新爬取数据）
    2. 同日其他航班被最新批次爬到了，但这个航班没出现（取消/下架）
    """
    if last_price_is_none:
        return False  # 新航班不可能是停止更新
    try:
        # 条件1: 航班日期已过
        if flight_date:
            flight_dt = datetime.strptime(flight_date, '%Y-%m-%d')
            if flight_dt.date() < datetime.now().date():
                return True
    except ValueError:
        pass
    # 条件2: 最新一批爬取未覆盖该航班
    if crawl_time and global_max_crawl_time:
        try:
            ct = datetime.strptime(crawl_time, '%Y-%m-%d %H:%M:%S')
            max_ct = datetime.strptime(global_max_crawl_time, '%Y-%m-%d %H:%M:%S')
            return (max_ct - ct).total_seconds() > 300
        except ValueError:
            pass
    return False


@app.get("/api/flights")
async def get_flights(request: Request, frm: str = "", to: str = "", date: str = ""):
    """获取某航线+日期的最新航班列表（含涨跌信息）"""
    if not frm or not to or not date:
        return {"flights": []}

    conn = get_db()
    try:
        # 全局最新爬取时间（用于判断哪些航班已停止更新）
        global_max = conn.execute("""
            SELECT MAX(crawl_time) AS max_time
            FROM flight_prices
            WHERE route_from = ? AND route_to = ? AND flight_date = ?
        """, (frm, to, date)).fetchone()
        global_max_crawl = global_max["max_time"] if global_max else None

        # 每个航班号取最新一次抓取记录
        rows = conn.execute("""
            SELECT f.*
            FROM flight_prices f
            INNER JOIN (
                SELECT flight_no, MAX(crawl_time) AS max_time
                FROM flight_prices
                WHERE route_from = ? AND route_to = ? AND flight_date = ?
                GROUP BY flight_no
            ) latest ON f.flight_no = latest.flight_no AND f.crawl_time = latest.max_time
            WHERE f.route_from = ? AND f.route_to = ? AND f.flight_date = ?
            ORDER BY f.price ASC
        """, (frm, to, date, frm, to, date)).fetchall()

        flights = []
        for r in rows:
            # 查上一次价格
            prev = conn.execute("""
                SELECT price, crawl_time FROM flight_prices
                WHERE flight_no = ? AND route_from = ? AND route_to = ?
                  AND flight_date = ? AND crawl_time < ?
                ORDER BY crawl_time DESC LIMIT 1
            """, (r["flight_no"], frm, to, date, r["crawl_time"])).fetchone()

            flight = {
                "flight_no": r["flight_no"],
                "airline": r["airline"] or "",
                "departure_airport": r["departure_airport"] or "",
                "arrival_airport": r["arrival_airport"] or "",
                "departure_time": r["departure_time"] or "",
                "arrival_time": r["arrival_time"] or "",
                "price": r["price"],
                "crawl_time": r["crawl_time"],
            }

            if prev:
                flight["last_price"] = prev["price"]
                flight["change_amount"] = r["price"] - prev["price"]
                if prev["price"] > 0:
                    flight["change_percent"] = round(
                        (r["price"] - prev["price"]) / prev["price"] * 100, 1
                    )
                else:
                    flight["change_percent"] = 0
                # 距上次爬取时间
                last_time = datetime.strptime(prev["crawl_time"], "%Y-%m-%d %H:%M:%S")
                delta = datetime.now() - last_time
                if delta.days > 0:
                    flight["time_ago"] = f"{delta.days}天前"
                elif delta.seconds >= 3600:
                    flight["time_ago"] = f"{delta.seconds // 3600}小时前"
                elif delta.seconds >= 60:
                    flight["time_ago"] = f"{delta.seconds // 60}分钟前"
                else:
                    flight["time_ago"] = "刚刚"
            else:
                flight["last_price"] = None
                flight["change_amount"] = 0
                flight["change_percent"] = 0
                flight["time_ago"] = None

            flight["is_stopped"] = _is_flight_stopped(
                r["flight_date"], r["crawl_time"], global_max_crawl,
                flight["last_price"] is None
            )
            flights.append(flight)

        return {"flights": flights}
    finally:
        conn.close()


@app.get("/api/trend")
async def get_trend(
    request: Request,
    flight_no: str = "",
    frm: str = "",
    to: str = "",
    date: str = "",
):
    """获取单航班历史价格趋势"""
    if not flight_no or not frm or not to or not date:
        return {"trend": []}

    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT price, crawl_time FROM flight_prices
            WHERE flight_no = ? AND route_from = ? AND route_to = ? AND flight_date = ?
            ORDER BY crawl_time ASC
        """, (flight_no, frm, to, date)).fetchall()

        return {
            "trend": [{"price": r["price"], "time": r["crawl_time"]} for r in rows]
        }
    finally:
        conn.close()


@app.get("/api/trends")
async def get_trends(
    request: Request,
    frm: str = "",
    to: str = "",
    date: str = "",
):
    """获取一个或多个航线+日期的所有航班历史价格趋势（批量，to 逗号分隔多选）"""
    if not frm or not to or not date:
        return {"trends": {}}

    # 支持多目的地：逗号分隔，如 ?to=jjn,xmn
    dests = [d.strip() for d in to.split(",") if d.strip()]
    if not dests:
        return {"trends": {}}

    conn = get_db()
    result = {}
    try:
        for dest in dests:
            # 获取该航线+日期的最新航班列表
            flights = conn.execute("""
                SELECT f.*
                FROM flight_prices f
                INNER JOIN (
                    SELECT flight_no, MAX(crawl_time) AS max_time
                    FROM flight_prices
                    WHERE route_from = ? AND route_to = ? AND flight_date = ?
                    GROUP BY flight_no
                ) latest ON f.flight_no = latest.flight_no AND f.crawl_time = latest.max_time
                WHERE f.route_from = ? AND f.route_to = ? AND f.flight_date = ?
                ORDER BY f.price ASC
            """, (frm, dest, date, frm, dest, date)).fetchall()

            for fl in flights:
                rows = conn.execute("""
                    SELECT price, crawl_time FROM flight_prices
                    WHERE flight_no = ? AND route_from = ? AND route_to = ? AND flight_date = ?
                    ORDER BY crawl_time ASC
                """, (fl["flight_no"], frm, dest, date)).fetchall()

                # key 带上目的地以便区分：KN5967 (jjn)
                key = f"{fl['flight_no']} ({dest})"
                result[key] = {
                    "airline": fl["airline"] or "",
                    "flight_no": fl["flight_no"],
                    "price": fl["price"],
                    "route_to": dest,
                    "route_to_name": fl["route_to_name"] or dest,
                    "departure_time": fl["departure_time"] or "",
                    "arrival_time": fl["arrival_time"] or "",
                    "trend": [{"price": r["price"], "time": r["crawl_time"]} for r in rows],
                }

        return {"trends": result}
    finally:
        conn.close()


@app.get("/api/multi-flights")
async def get_multi_flights(
    request: Request,
    frm: str = "",
    to: str = "",
    date: str = "",
):
    """获取多目的地航班列表（用于表格展示），to 逗号分隔"""
    if not frm or not to or not date:
        return {"flights": []}

    dests = [d.strip() for d in to.split(",") if d.strip()]
    if not dests:
        return {"flights": []}

    conn = get_db()
    result = []
    try:
        for dest in dests:
            # 该目的地全局最新爬取时间
            global_max = conn.execute("""
                SELECT MAX(crawl_time) AS max_time
                FROM flight_prices
                WHERE route_from = ? AND route_to = ? AND flight_date = ?
            """, (frm, dest, date)).fetchone()
            global_max_crawl = global_max["max_time"] if global_max else None

            flights = conn.execute("""
                SELECT f.*
                FROM flight_prices f
                INNER JOIN (
                    SELECT flight_no, MAX(crawl_time) AS max_time
                    FROM flight_prices
                    WHERE route_from = ? AND route_to = ? AND flight_date = ?
                    GROUP BY flight_no
                ) latest ON f.flight_no = latest.flight_no AND f.crawl_time = latest.max_time
                WHERE f.route_from = ? AND f.route_to = ? AND f.flight_date = ?
                ORDER BY f.price ASC
            """, (frm, dest, date, frm, dest, date)).fetchall()

            for r in flights:
                prev = conn.execute("""
                    SELECT price, crawl_time FROM flight_prices
                    WHERE flight_no = ? AND route_from = ? AND route_to = ?
                      AND flight_date = ? AND crawl_time < ?
                    ORDER BY crawl_time DESC LIMIT 1
                """, (r["flight_no"], frm, dest, date, r["crawl_time"])).fetchone()

                flight = {
                    "flight_no": r["flight_no"],
                    "airline": r["airline"] or "",
                    "departure_airport": r["departure_airport"] or "",
                    "arrival_airport": r["arrival_airport"] or "",
                    "departure_time": r["departure_time"] or "",
                    "arrival_time": r["arrival_time"] or "",
                    "price": r["price"],
                    "route_to": dest,
                    "route_to_name": r["route_to_name"] or dest,
                    "crawl_time": r["crawl_time"],
                }
                if prev:
                    flight["last_price"] = prev["price"]
                    flight["change_amount"] = r["price"] - prev["price"]
                    if prev["price"] > 0:
                        flight["change_percent"] = round((r["price"] - prev["price"]) / prev["price"] * 100, 1)
                    else:
                        flight["change_percent"] = 0
                    last_time = datetime.strptime(prev["crawl_time"], "%Y-%m-%d %H:%M:%S")
                    delta = datetime.now() - last_time
                    if delta.days > 0:
                        flight["time_ago"] = f"{delta.days}天前"
                    elif delta.seconds >= 3600:
                        flight["time_ago"] = f"{delta.seconds // 3600}小时前"
                    elif delta.seconds >= 60:
                        flight["time_ago"] = f"{delta.seconds // 60}分钟前"
                    else:
                        flight["time_ago"] = "刚刚"
                else:
                    flight["last_price"] = None
                    flight["change_amount"] = 0
                    flight["change_percent"] = 0
                    flight["time_ago"] = None
                flight["is_stopped"] = _is_flight_stopped(
                    r["flight_date"], r["crawl_time"], global_max_crawl,
                    flight["last_price"] is None
                )
                result.append(flight)

        return {"flights": result}
    finally:
        conn.close()


@app.post("/api/chat")
async def chat_proxy(request: Request, body: dict):
    """代理 DeepSeek API 请求，避免前端暴露 API Key"""
    if not DEEPSEEK_API_KEY:
        raise HTTPException(500, "DeepSeek API Key 未配置")

    messages = body.get("messages", [])
    if not messages:
        raise HTTPException(400, "缺少 messages 参数")

    try:
        payload = json.dumps({
            "model": DEEPSEEK_MODEL,
            "messages": messages,
            "max_tokens": body.get("max_tokens", 1024),
        }).encode("utf-8")
        req = UrlRequest(
            "https://api.deepseek.com/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            },
        )
        with urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return {"reply": data["choices"][0]["message"]["content"]}
    except URLError as e:
        raise HTTPException(502, f"DeepSeek API 请求失败: {e.reason}")
    except Exception as e:
        raise HTTPException(500, str(e))


# ============================================================
#  静态文件 & 前端
# ============================================================

@app.get("/")
async def index():
    """返回前端页面"""
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return JSONResponse({"detail": "index.html not found"}, status_code=404)


# ============================================================
#  启动入口
# ============================================================

if __name__ == "__main__":
    import uvicorn

    print(f"[DB] {DB_PATH}")
    print(f"[Users] {USERS_FILE}")
    print(f"[Server] http://0.0.0.0:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000)
