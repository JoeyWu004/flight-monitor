#!/bin/bash
# ============================================================
#  Flight-Monitor Server — 云服务器一键部署脚本
#  适用系统: Alibaba Cloud Linux 3 / CentOS 8+ / RHEL 8+
# ============================================================
#  用法:
#    1. 上传整个 server/ 文件夹到服务器
#       scp -r server/ root@<IP>:/opt/flight-monitor-server/
#    2. SSH 登录服务器
#       ssh root@<IP>
#    3. 运行此脚本
#       bash /opt/flight-monitor-server/deploy.sh
# ============================================================

set -e

APP_DIR="/opt/flight-monitor-server"
APP_NAME="flight-monitor-server"

echo "============================================"
echo "  Flight-Monitor Server — 部署脚本"
echo "============================================"
echo ""

# ---- 1. 检查是否为 root ----
if [ "$(id -u)" != "0" ]; then
    echo "[错误] 请使用 root 用户运行此脚本"
    exit 1
fi

cd "$APP_DIR"

# ---- 2. 检查 Python3 ----
echo "[1/6] 检查 Python3..."
if ! command -v python3 &>/dev/null; then
    echo "       安装 Python3..."
    dnf install -y python3 python3-pip
fi
python3 --version
echo "       [OK]"

# ---- 3. 安装 Python 依赖 ----
echo "[2/6] 安装 Python 依赖..."
pip3 install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/ --quiet
echo "       [OK]"

# ---- 4. 检查/安装 Nginx ----
echo "[3/6] 配置 Nginx..."
if ! command -v nginx &>/dev/null; then
    dnf install -y nginx
fi

# 写 Nginx 配置
cat > /etc/nginx/conf.d/flight-monitor.conf << 'NGINX_EOF'
server {
    listen 80;
    server_name _;

    # 日志
    access_log /var/log/nginx/flight-monitor-access.log;
    error_log  /var/log/nginx/flight-monitor-error.log;

    # API 反向代理到 FastAPI
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 30s;
    }

    # 前端页面
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
NGINX_EOF

# 确保 nginx 配置正确
nginx -t
systemctl enable nginx
systemctl restart nginx
echo "       [OK]"

# ---- 5. 创建 systemd 服务 ----
echo "[4/6] 创建 systemd 服务..."

cat > /etc/systemd/system/${APP_NAME}.service << SERVICE_EOF
[Unit]
Description=Flight-Monitor Dashboard Server
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=${APP_DIR}
ExecStart=/usr/bin/python3 ${APP_DIR}/server.py
Restart=always
RestartSec=5
Environment=FLIGHT_DB_PATH=${APP_DIR}/flight_monitor.db
Environment=USERS_FILE=${APP_DIR}/users.json

[Install]
WantedBy=multi-user.target
SERVICE_EOF

systemctl daemon-reload
systemctl enable ${APP_NAME}
echo "       [OK]"

# ---- 6. 开放防火墙 ----
echo "[5/6] 配置防火墙..."
if command -v firewall-cmd &>/dev/null; then
    firewall-cmd --add-port=80/tcp --permanent 2>/dev/null || true
    firewall-cmd --reload 2>/dev/null || true
    echo "       firewalld 已配置"
elif command -v iptables &>/dev/null; then
    iptables -I INPUT -p tcp --dport 80 -j ACCEPT 2>/dev/null || true
    echo "       iptables 已配置"
fi
echo "       [OK]"

# ---- 7. 启动服务 ----
echo "[6/6] 启动服务..."
systemctl restart ${APP_NAME}
sleep 2
systemctl status ${APP_NAME} --no-pager

echo ""
echo "============================================"
echo "  部署完成!"
echo "============================================"
echo ""
echo "  访问地址: http://$(hostname -I | awk '{print $1}')"
echo ""
echo "  管理命令:"
echo "    查看状态: systemctl status ${APP_NAME}"
echo "    查看日志: journalctl -u ${APP_NAME} -f"
echo "    重启服务: systemctl restart ${APP_NAME}"
echo "    停止服务: systemctl stop ${APP_NAME}"
echo ""
echo "  修改密码: 编辑 ${APP_DIR}/users.json"
echo "    生成密码哈希: python3 -c 'import hashlib;print(hashlib.sha256(input(\"密码:\").encode()).hexdigest())'"
echo ""
echo "  同步数据库: 在 Windows 上运行 sync_db.bat"
echo ""
