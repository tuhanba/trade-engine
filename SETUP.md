# AX Trade Engine v5.x PAPER - Server Setup

## 1. Requirements
- Ubuntu 24.04 LTS or similar
- Python 3.12+

## 2. Installation
```bash
cd /root
git clone https://github.com/tuhanba/trade-engine.git trade_engine
cd /root/trade_engine
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
python -c "from database import init_db; init_db()"
python scripts/migrate_accounting_schema.py || true
python scripts/audit_pnl_consistency.py || true

systemctl daemon-reload
systemctl enable ax-bot ax-dashboard
systemctl restart ax-bot
systemctl restart ax-dashboard
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

## 4. DB Migration
**NEVER DELETE trading.db. Backup and migrate instead.**

## 5. Validation Tests
Verify the deployment:
```bash
systemctl status ax-bot -l --no-pager
systemctl status ax-dashboard -l --no-pager
curl -s http://127.0.0.1:5000/api/health
curl -s http://127.0.0.1:5000/api/live
curl -s http://127.0.0.1:5000/api/stats
```
