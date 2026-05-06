# AX Trade Engine v5.x PAPER Engine
**PAPER-ONLY / LIVE-BLOCKED**

This is a production-grade AI trading engine strictly configured for "Paper Trading". It incorporates institutional-grade risk management and self-optimizing AI edge (Ghost Learning).

**It does NOT execute real orders.**

## Architecture
- **Bot**: `scalp_bot_v3.py` (Core engine)
- **Dashboard**: `app.py` (Flask + SocketIO)
- **Execution**: `execution_engine.py` (Paper Mode Execution)
- **Virtual Env**: `.venv`
- **Path**: `/root/trade_engine`
- **Services**: `ax-bot`, `ax-dashboard`

## Safety (Paper Mode)
- `EXECUTION_MODE=paper`
- `LIVE_TRADING_ENABLED=False`
- `DRY_RUN=True`
- `CONFIRM_LIVE_TRADING=False`
- `USE_BINANCE_PRIVATE_API=False`

No real orders are executed. All API keys and secrets are strictly excluded from logs.

## Deployment & Updates
Do **NOT** delete the database (`trading.db`). The system uses a strict backup + migration standard to preserve AI Ghost Learning data.

To update:
```bash
git pull origin main
systemctl restart ax-bot
systemctl restart ax-dashboard
```

## Before Deploy Checklist
Run these commands to validate the system before starting services:
```bash
python -m py_compile config.py database.py execution_engine.py app.py scalp_bot_v3.py
python scripts/audit_pnl_consistency.py || true
systemctl status ax-bot -l --no-pager
systemctl status ax-dashboard -l --no-pager
curl -s http://127.0.0.1:5000/api/health
curl -s http://127.0.0.1:5000/api/live
curl -s http://127.0.0.1:5000/api/stats
```
