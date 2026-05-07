# AURVEX — Acil Durdurma

## 30 Saniyede Durdur
```bash
systemctl stop aurvex-bot
systemctl stop aurvex-dashboard
```

## Snapshot Al
```bash
cd ~/trade_engine/trade-engine
python3 scripts/monitor_paper_run.py > /tmp/snapshot_$(date +%Y%m%d_%H%M%S).txt
python3 scripts/audit_pnl_consistency.py >> /tmp/snapshot_$(date +%Y%m%d_%H%M%S).txt
```

## .env Güvenli Moda Al
```
LIVE_TRADING_ENABLED=False
DRY_RUN=True
CONFIRM_LIVE_TRADING=False
```

## Açık Pozisyonları Kontrol Et
```bash
python3 -c "
import sys; sys.path.insert(0,'.')
import database as db
trades = db.get_open_trades()
print(f'Açık: {len(trades)}')
for t in trades:
    print(f'  #{t[\"id\"]} {t[\"symbol\"]} {t[\"direction\"]} {t[\"status\"]}')
"
```

## Yeniden Başlatmadan Önce
1. `python3 scripts/audit_pnl_consistency.py` → 0 ERROR
2. `.env` kontrol et — LIVE_TRADING_ENABLED=False
3. `journalctl -u aurvex-bot -n 50` ile hatayı bul
