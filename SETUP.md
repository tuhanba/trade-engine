# AX Trade Engine v5.x PAPER - Server Setup

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
cp .env.example .env
```

## 3. Configuration
**MANDATORY FOR PAPER TRADING:**
```
EXECUTION_MODE=paper
LIVE_TRADING_ENABLED=False
DRY_RUN=True
CONFIRM_LIVE_TRADING=False
USE_BINANCE_PRIVATE_API=False
```

## 4. DB Migration & Initial Setup
**NEVER DELETE trading.db.**
```bash
python -c "from database import init_db; init_db()"
python scripts/migrate_v6.py || true
python scripts/audit_pnl_consistency.py || true
```

## 5. Systemd Services
Link the services to systemd:
```bash
cp systemd/ax-bot.service /etc/systemd/system/
cp systemd/ax-dashboard.service /etc/systemd/system/

systemctl daemon-reload
systemctl enable ax-bot ax-dashboard
systemctl restart ax-bot ax-dashboard
```

## 6. Validation Tests
Verify the deployment:
```bash
curl http://127.0.0.1:5000/api/health
curl http://127.0.0.1:5000/api/live
curl http://127.0.0.1:5000/api/stats
```
