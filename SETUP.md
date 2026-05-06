# AURVEX Ai Server Setup

This guide provides the definitive steps for deploying the production-grade paper trading engine.

## 1. Requirements
- Ubuntu 24.04 LTS or similar
- Python 3.12+
- Path must be exactly `/root/trade_engine`

## 2. Installation
```bash
cd /root
git clone https://github.com/tuhanba/trade-engine.git
cd trade_engine

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 3. Configuration
Copy `.env.example` to `.env` and fill in the values.
**MANDATORY FOR PAPER TRADING:**
```
EXECUTION_MODE=paper
LIVE_TRADING_ENABLED=False
DRY_RUN=True
CONFIRM_LIVE_TRADING=False
USE_BINANCE_PRIVATE_API=False
```

## 4. Systemd Services
Link the services to systemd:
```bash
cp systemd/ax-bot.service /etc/systemd/system/
cp systemd/ax-dashboard.service /etc/systemd/system/

systemctl daemon-reload
systemctl enable ax-bot ax-dashboard
systemctl start ax-bot ax-dashboard
```

## 5. DB Migration & Backup
**NEVER DELETE trading.db.**
If schema changes, the system will perform automatic migrations.
Backup command:
```bash
cp trading.db trading.db.bak
```

## 6. Troubleshooting
Check port 5000 conflicts:
```bash
sudo lsof -i :5000
```
Check status:
```bash
systemctl status ax-bot -l --no-pager
systemctl status ax-dashboard -l --no-pager
```
