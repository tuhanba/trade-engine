"""
Outcome Labeler — Adım B
Her kapalı trade için:
  - MFE / MAE (Binance 1m kline'dan)
  - exit_efficiency (ne kadar MFE yakalandı)
  - price_5m / price_15m / price_60m (entry'den sonra)
  - quality_label: PERFECT / GOOD / LUCKY / BAD_TIMING / BAD_SETUP / EARLY_EXIT
"""
import os, sqlite3, logging
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("outcome_labeler")

DB_PATH = os.path.join(os.path.dirname(__file__), "trading.db")

def get_conn():
    return sqlite3.connect(DB_PATH)

def init_outcome_table():
    conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS outcome_labels (
            trade_id     INTEGER PRIMARY KEY,
            symbol       TEXT,
            direction    TEXT,
            mfe_pct      REAL,
            mae_pct      REAL,
            exit_eff     REAL,
            price_5m_pct  REAL,
            price_15m_pct REAL,
            price_60m_pct REAL,
            quality      TEXT,
            labeled_at   TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    conn.close()

def _get_client():
    try:
        from binance.client import Client
        return Client(os.getenv("BINANCE_API_KEY",""), os.getenv("BINANCE_API_SECRET",""))
    except Exception as e:
        logger.error(f"Binance client hata: {e}")
        return None

def _fetch_klines(client, symbol, start_ms, end_ms):
    try:
        klines = client.get_historical_klines(
            symbol, "1m",
            start_str=start_ms,
            end_str=end_ms,
            limit=240
        )
        return klines
    except Exception as e:
        logger.error(f"klines hata {symbol}: {e}")
        return []

def _pct(entry, price, direction):
    if entry <= 0:
        return 0.0
    diff = (price - entry) / entry * 100
    return diff if direction == "LONG" else -diff

def _assign_label(mfe, mae, exit_eff, actual_pnl):
    if mfe < 0.05:
        return "BAD_SETUP"
    if mae > mfe * 2:
        return "LUCKY" if actual_pnl > 0 else "BAD_SETUP"
    if exit_eff >= 0.75:
        return "PERFECT"
    if exit_eff >= 0.4:
        return "GOOD"
    if mfe > 0.3 and exit_eff < 0.3:
        return "EARLY_EXIT"
    if mae > mfe * 0.8 and actual_pnl < 0:
        return "BAD_TIMING"
    return "GOOD"

def label_trade(trade, client):
    tid      = trade["id"]
    symbol   = trade["symbol"]
    direction= trade["direction"]
    entry    = trade["entry"]
    pnl_pct  = trade["pnl_pct"] or 0
    open_ms  = int(datetime.fromisoformat(trade["open_time"].replace("Z","")).replace(tzinfo=timezone.utc).timestamp() * 1000)
    close_ms = int(datetime.fromisoformat(trade["close_time"].replace("Z","")).replace(tzinfo=timezone.utc).timestamp() * 1000)
    end_ms   = close_ms + 60 * 60 * 1000  # +1h after close for 60m labels

    klines = _fetch_klines(client, symbol, open_ms, end_ms)
    if not klines:
        return None

    highs  = [float(k[2]) for k in klines]
    lows   = [float(k[3]) for k in klines]
    closes = [float(k[4]) for k in klines]
    times  = [int(k[0])   for k in klines]

    trade_dur = [i for i, t in enumerate(times) if t <= close_ms]
    if not trade_dur:
        return None

    trade_highs = [highs[i] for i in trade_dur]
    trade_lows  = [lows[i]  for i in trade_dur]

    if direction == "LONG":
        mfe_pct = _pct(entry, max(trade_highs), direction)
        mae_pct = _pct(entry, min(trade_lows),  direction)
    else:
        mfe_pct = _pct(entry, min(trade_lows),  direction)
        mae_pct = _pct(entry, max(trade_highs), direction)

    mae_pct = abs(min(mae_pct, 0))  # always positive

    exit_eff = round(pnl_pct / mfe_pct, 2) if mfe_pct > 0.001 else 0.0
    exit_eff = max(-1.0, min(2.0, exit_eff))

    def price_at(offset_ms):
        target = open_ms + offset_ms
        idx = next((i for i, t in enumerate(times) if t >= target), None)
        return _pct(entry, closes[idx], direction) if idx is not None else None

    p5m  = price_at(5  * 60 * 1000)
    p15m = price_at(15 * 60 * 1000)
    p60m = price_at(60 * 60 * 1000)

    label = _assign_label(mfe_pct, mae_pct, exit_eff, pnl_pct)

    return {
        "trade_id": tid, "symbol": symbol, "direction": direction,
        "mfe_pct": round(mfe_pct, 4), "mae_pct": round(mae_pct, 4),
        "exit_eff": exit_eff,
        "price_5m_pct": round(p5m, 4) if p5m is not None else None,
        "price_15m_pct": round(p15m, 4) if p15m is not None else None,
        "price_60m_pct": round(p60m, 4) if p60m is not None else None,
        "quality": label
    }

def run_labeling(limit=20):
    init_outcome_table()
    client = _get_client()
    if not client:
        return {"labeled": 0, "error": "Binance client yok"}

    conn = get_conn()
    rows = conn.execute("""
        SELECT t.id, t.symbol, t.direction, t.entry, t.pnl_pct, t.open_time, t.close_time
        FROM trades t
        LEFT JOIN outcome_labels o ON t.id = o.trade_id
        WHERE t.status IN ('WIN','LOSS','MANUAL')
          AND t.open_time IS NOT NULL
          AND t.close_time IS NOT NULL
          AND o.trade_id IS NULL
        ORDER BY t.close_time DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()

    trades = [
        {"id": r[0], "symbol": r[1], "direction": r[2],
         "entry": r[3], "pnl_pct": r[4], "open_time": r[5], "close_time": r[6]}
        for r in rows
    ]

    labeled = 0
    errors  = 0
    results = []

    for t in trades:
        try:
            data = label_trade(t, client)
            if data:
                conn = get_conn()
                conn.execute("""
                    INSERT OR REPLACE INTO outcome_labels
                    (trade_id,symbol,direction,mfe_pct,mae_pct,exit_eff,
                     price_5m_pct,price_15m_pct,price_60m_pct,quality)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                """, (
                    data["trade_id"], data["symbol"], data["direction"],
                    data["mfe_pct"], data["mae_pct"], data["exit_eff"],
                    data["price_5m_pct"], data["price_15m_pct"], data["price_60m_pct"],
                    data["quality"]
                ))
                conn.commit()
                conn.close()
                labeled += 1
                results.append(f"{data['symbol']} → {data['quality']} (MFE:{data['mfe_pct']:.2f}% EFF:{data['exit_eff']:.2f})")
        except Exception as e:
            logger.error(f"label_trade hata {t['id']}: {e}")
            errors += 1

    return {"labeled": labeled, "errors": errors, "results": results}

if __name__ == "__main__":
    import json
    print(json.dumps(run_labeling(10), indent=2, ensure_ascii=False))
