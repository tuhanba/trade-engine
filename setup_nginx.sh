#!/bin/bash
# AURVEX Dashboard — nginx reverse proxy kurulum scripti
# Port 80 -> 5000 yönlendirmesi

set -e

echo "=== nginx kuruluyor ==="
apt-get install -y nginx

echo "=== nginx config yazılıyor ==="
cat > /etc/nginx/sites-available/aurvex << 'EOF'
server {
    listen 80;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 86400;
    }
}
EOF

echo "=== default site devre disi ==="
unlink /etc/nginx/sites-enabled/default 2>/dev/null || true
ln -sf /etc/nginx/sites-available/aurvex /etc/nginx/sites-enabled/aurvex

echo "=== nginx test ==="
nginx -t

echo "=== nginx yeniden baslatiliyor ==="
systemctl enable nginx
systemctl restart nginx

echo "=== ufw port 80 aciliyor ==="
ufw allow 80/tcp

echo "=== Durum ==="
systemctl status nginx --no-pager | head -5
echo ""
echo "Dashboard: http://143.198.90.104"
