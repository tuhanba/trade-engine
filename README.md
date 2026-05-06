# 🤖 AX Trade Engine v5.0

AI-driven cryptocurrency trade engine for Binance Futures with multi-layered risk management, real-time dashboard, Telegram reporting, and autonomous Ghost Learning.

## Features

- **AI Decision Engine** — ALLOW / WATCH / VETO signal classification
- **Ghost Learning** — Learns from trades it didn't take (paper outcome tracking)
- **Coin Personality** — Per-coin win rate, danger score, EMA-based personality updates
- **Multi-TP Management** — TP1 (40%), TP2 (30%), Runner (30%) with trailing stop
- **Real-time Dashboard** — Live PnL, open trades, calendar, weekly stats, coin profiles
- **Telegram Integration** — Trade open/close notifications with PnL breakdown
- **Circuit Breaker** — Auto-pause after consecutive losses
- **Comprehensive Audit** — 9-stage PnL consistency verification

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env
# Edit .env with your API keys

# 3. Initialize database
python -c "from database import init_db; init_db()"

# 4. Run migration (if upgrading)
python scripts/migrate_accounting_schema.py

# 5. Run audit
python scripts/audit_pnl_consistency.py

# 6. Start Dashboard (Terminal 1)
python app.py

# 7. Start Bot (Terminal 2)
python scalp_bot_v3.py
```

## Architecture

```
scalp_bot_v3.py          — Main scan loop
├── core/
│   ├── async_market_scanner.py   — Async Binance market scanner
│   ├── advanced_trend_engine.py  — 1h EMA trend + mean reversion
│   ├── trigger_engine.py         — 5m+1m multi-TF entry confirmation
│   ├── advanced_risk_engine.py   — Position sizing + safety checks
│   ├── ai_decision_engine.py     — AI ALLOW/WATCH/VETO + Ghost Learning
│   ├── accounting.py             — Centralized PnL/Fee/Margin math
│   ├── paper_tracker.py          — Paper outcome simulation
│   ├── coin_library.py           — Exchange filter management
│   ├── data_layer.py             — Signal/Trade data structures
│   └── elite_monitor.py          — System health + cleanup
├── execution_engine.py           — Trade lifecycle (open/TP/SL/close)
├── database.py                   — SQLite with 12+ tables
├── telegram_delivery.py          — Thread-safe Telegram notifications
├── dashboard_service.py          — Background stats aggregation
├── app.py                        — Flask dashboard + 10 API endpoints
├── config.py                     — All settings from .env
└── scripts/
    ├── audit_pnl_consistency.py  — 9-stage system audit
    ├── backtest_engine.py        — Historical trade replay
    └── migrate_accounting_schema.py — DB schema migration
```

## Dashboard

Access at `http://localhost:5000` after starting `app.py`.

### API Endpoints

| Endpoint | Description |
|----------|-------------|
| `/api/live` | Open trades with real-time PnL breakdown |
| `/api/stats` | Overall performance statistics |
| `/api/trades` | Paginated trade history |
| `/api/pnl_chart` | Cumulative PnL chart data |
| `/api/daily_pnl` | Daily PnL calendar data |
| `/api/weekly` | Weekly performance summary |
| `/api/coin_profiles` | Per-coin learning profiles |
| `/api/ax_status` | System status (circuit breaker, mode, etc.) |
| `/api/scalp_signal_stats` | Signal quality breakdown |

## Safety Rules

- `EXECUTION_MODE=paper` by default — no real money
- `LIVE_TRADING_ENABLED=False` by default
- `DRY_RUN=True` by default
- Max margin loss check (40%) before every trade
- Daily max loss circuit breaker (5%)
- Consecutive loss cooldown
- Never delete historic trade data

## Audit

Run before any deployment:

```bash
python scripts/audit_pnl_consistency.py
```

Must return `0 ERROR` before going live.

## Environment Variables

See `.env.example` for all available configuration options.

## License

Private — All rights reserved.
