# AX Trade Engine v5.x — PAPER ENGINE / LIVE-BLOCKED

## Setup Standards
- **Project path**: `/root/trade_engine`
- **Python env**: `.venv`
- **Bot file**: `scalp_bot_v3.py`
- **Dashboard file**: `app.py`
- **Services**: `ax-bot`, `ax-dashboard`
- **Mode**: PAPER-ONLY / LIVE-BLOCKED
- **DB strategy**: backup + migration (no DB reset)
- **Live Trading**: no live trading guide

## Installation Flow

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
cp ax-bot.service /etc/systemd/system/ax-bot.service
cp ax-dashboard.service /etc/systemd/system/ax-dashboard.service
systemctl daemon-reload
systemctl enable ax-bot ax-dashboard
systemctl restart ax-bot
systemctl restart ax-dashboard
```
