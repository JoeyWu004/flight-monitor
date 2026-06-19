# Flight-Monitor 服务器部署全流程

> 服务器：Alibaba Cloud Linux 3 / 2C2G / IP: <YOUR_SERVER_IP>  
> 部署路径：`/opt/flight-monitor-server/`  
> 时间：2026-06-19

---

## 一、上传文件

```bash
# 在 Git Bash 中执行

# 先创建目标目录
ssh root@<YOUR_SERVER_IP> "mkdir -p /opt/flight-monitor-server"

# 上传 server 文件夹
scp -r "D:\Flight-Monitor\server" root@<YOUR_SERVER_IP>:/opt/flight-monitor-server/

# 上传数据库
scp "D:\Flight-Monitor\flight_monitor.db" root@<YOUR_SERVER_IP>:/opt/flight-monitor-server/
```

## 二、解决目录嵌套问题

scp 上传 `server/` 文件夹会多套一层目录，需要移出来：

```bash
ssh root@<YOUR_SERVER_IP>

mv /opt/flight-monitor-server/server/* /opt/flight-monitor-server/
rmdir /opt/flight-monitor-server/server
```

## 三、升级 Python（3.6 → 3.8）

Alibaba Cloud Linux 3 默认 Python 3.6 太旧，不支持新版 FastAPI。

```bash
dnf install -y python38

# 确认版本
python3.8 --version    # 应输出 Python 3.8.17
```

## 四、安装 Python 依赖

```bash
python3.8 -m pip install fastapi uvicorn PyJWT python-multipart
```

## 五、部署 Nginx

```bash
# 安装
dnf install -y nginx

# 配置反向代理（一行版，避免多行终端问题）
echo 'server { listen 80; server_name _; location /api/ { proxy_pass http://127.0.0.1:8000; proxy_set_header Host $host; proxy_set_header X-Real-IP $remote_addr; } location / { proxy_pass http://127.0.0.1:8000; proxy_set_header Host $host; } }' > /etc/nginx/conf.d/flight-monitor.conf

# 验证配置并启动
nginx -t && systemctl enable nginx && systemctl restart nginx
```

## 六、创建 systemd 服务

```bash
cat > /etc/systemd/system/flight-monitor-server.service << 'EOF'
[Unit]
Description=Flight-Monitor Dashboard
After=network.target
[Service]
Type=simple
WorkingDirectory=/opt/flight-monitor-server
ExecStart=/usr/bin/python3.8 /opt/flight-monitor-server/server.py
Restart=always
RestartSec=5
Environment=FLIGHT_DB_PATH=/opt/flight-monitor-server/flight_monitor.db
Environment=USERS_FILE=/opt/flight-monitor-server/users.json
[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable flight-monitor-server
systemctl restart flight-monitor-server
```

## 七、开放端口

```bash
# 服务器内部防火墙
firewall-cmd --add-port=80/tcp --permanent
firewall-cmd --reload

# 阿里云控制台 → 安全组 → 添加入方向规则 → 80 端口 → 0.0.0.0/0
```

## 八、配置 SSH 免密登录

```bash
# 在 Git Bash 中

# 生成密钥（如果没有）
ssh-keygen -t ed25519 -C "flight-monitor" -f ~/.ssh/id_ed25519 -N ""

# 上传公钥
ssh-copy-id root@<YOUR_SERVER_IP>
```

## 九、常见问题与修复

### 1. Python 3.8 类型注解报错

**错误**：`TypeError: unsupported operand type(s) for |: 'type' and 'NoneType'`

**原因**：`str | None` 语法需要 Python 3.10+，3.8 不支持。

**修复**：改用 `Optional[str]`，需 `from typing import Optional`。

### 2. 切换筛选条件页面卡住

**错误**：`Cannot read properties of null (reading 'style')`

**原因**：ECharts 初始化后会替换 `chart-empty` 元素，后续调用 `getElementById("chart-empty")` 返回 null。

**修复**：访问前做空值判断，`clearFlights` 时 dispose 图表实例并重建占位符。

### 3. sync_db.bat 双击闪退/乱码

**原因**：Windows cmd 没有 `scp`，中文 GBK 编码问题。

**修复**：脚本内查找 Git Bash 路径，通过 `bash.exe -c "scp ..."` 执行。

## 十、数据库同步

同步脚本已写好：`server/sync_db.bat`，双击即可。

原理：`scp` 覆盖整个 DB 文件 → 不会产生重复数据。

注意：最好在监控爬取的间歇执行（间隔 180 分钟，时间充足）。

## 十一、管理命令

| 操作 | 命令 |
|------|------|
| 查看状态 | `systemctl status flight-monitor-server --no-pager` |
| 重启服务 | `systemctl restart flight-monitor-server` |
| 查看日志 | `journalctl -u flight-monitor-server -f` |
| 停止服务 | `systemctl stop flight-monitor-server` |

## 十二、修改账号密码

```bash
ssh root@<YOUR_SERVER_IP>

# 生成新密码的 SHA256 哈希
python3.8 -c "import hashlib; print(hashlib.sha256(input('密码: ').encode()).hexdigest())"

# 编辑用户文件
vi /opt/flight-monitor-server/users.json
# 格式: { "用户名": "哈希值" }

# 重启生效
systemctl restart flight-monitor-server
```

## 十三、文件清单

| 文件 | 用途 |
|------|------|
| `server/server.py` | FastAPI 后端，JWT 认证 + 数据 API |
| `server/static/index.html` | ECharts 看板前端 |
| `server/requirements.txt` | Python 依赖列表 |
| `server/users.json` | 用户账户（已 gitignore） |
| `server/sync_db.bat` | Windows 数据库同步脚本（已 gitignore） |
| `server/deploy.sh` | 服务器一键部署脚本 |
