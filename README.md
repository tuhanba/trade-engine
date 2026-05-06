# AURVEX Ai - Trade Engine (Live Ready)
**Production-Grade Paper Trading System**

This is a 10/10 production-grade AI trading engine. It strictly focuses on "Paper Trading" with institutional-grade risk management and self-optimizing AI edge.

## Architecture
- **Bot**: `scalp_bot_v3.py` (Core engine)
- **Dashboard**: `app.py` (Flask + SocketIO)
- **Virtual Env**: `.venv`
- **Path**: `/root/trade_engine`
- **Services**: `ax-bot.service`, `ax-dashboard.service`

## Safety (Paper Mode)
- `EXECUTION_MODE=paper`
- `LIVE_TRADING_ENABLED=False`
- `USE_BINANCE_PRIVATE_API=False`
No real orders are executed. All API keys and secrets are strictly excluded from logs.

## Deployment & Updates
Do **NOT** delete the database (`trading.db`). The system uses migration standard for scheme updates to preserve AI Ghost Learning data.

To update:
```bash
git pull origin main
systemctl restart ax-bot ax-dashboard
```
