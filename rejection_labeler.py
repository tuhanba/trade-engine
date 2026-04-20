"""
Rejection Labeler — Reddedilen sinyallerin sonuçlarını retroaktif olarak etiketler.
Her 15 dakikada bir çalışır (outcome_labeler ile aynı schedule).

Mantık:
  - rejected_signals tablosundan labeled=0 olan kayıtları al
  - Binance 1m klines ile reddetme anından sonraki fiyat hareketlerini hesapla
  - outcome_5m, outcome_15m, outcome_60m (fiyat değişim %)
  - would_win: eğer trade açılsaydı kazanır mıydı?
  - Sonuçları kaydedip labeled=1 yap
"""

import os
import sqlite3
import requests
from datetime import datetime, timezone, timedelta

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trading.db")
BINANCE_URL = "https://fapi.binance.com/fapi/v1/klines"

def _fetch_klines(symbol: str, interval: str, start_ms: int, limit: int = 5):
    try:
        resp = requests.get(BINANCE_URL, params={
            "symbol": symbol,
            "interval": interval,
            "startTime": start_ms,
            "limit": limit,
        }, timeout=10)
        return resp.json() if resp.status_code == 200 else []
    except Exception:
        return []

def _price_at_offset(symbol: str, start_ms: int, offset_min: int):
    """start_ms'den offset_min dakika sonraki kapanış fiyatını döner."""
    target_ms = start_ms + offset_min * 60 * 1000
    klines = _fetch_klines(symbol, "1m", target_ms, limit=1)
    if not klines:
        return None
    return float(klines[0][4])  # close

def run_rejection_labeling(limit: int = 30) -> dict:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM rejected_signals WHERE labeled=0 ORDER BY id LIMIT ?",
        (limit,)
    ).fetchall()

    labeled = 0
    skipped = 0
    results = []

    for row in rows:
        symbol      = row["symbol"]
        direction   = row["direction"]  # may be None for bb/trend rejections
        rejected_at = row["rejected_at"]
        price       = row["price"]

        if not price or not symbol:
            skipped += 1
            conn.execute("UPDATE rejected_signals SET labeled=1 WHERE id=?", (row["id"],))
            continue

        try:
            dt = datetime.fromisoformat(rejected_at.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            start_ms = int(dt.timestamp() * 1000)
        except Exception:
            skipped += 1
            conn.execute("UPDATE rejected_signals SET labeled=1 WHERE id=?", (row["id"],))
            continue

        # Fiyatları al
        p5m  = _price_at_offset(symbol, start_ms, 5)
        p15m = _price_at_offset(symbol, start_ms, 15)
        p60m = _price_at_offset(symbol, start_ms, 60)

        if p5m is None:
            skipped += 1
            continue  # henüz mevcut değil, sonra tekrar dene

        def pct(p_now, p_base):
            if not p_base:
                return None
            return round((p_now - p_base) / p_base * 100, 4)

        out5  = pct(p5m, price)
        out15 = pct(p15m, price) if p15m else None
        out60 = pct(p60m, price) if p60m else None

        # Kazanır mıydı? direction varsa ona göre, yoksa en iyi yönü varsay
        if direction in ("LONG", "SHORT"):
            move = out15 or out5
            if direction == "LONG":
                would_win = 1 if (move and move > 0.3) else 0
            else:
                would_win = 1 if (move and move < -0.3) else 0
        else:
            # Yön bilinmiyor — her iki yönde 0.3% hareket var mı
            move = abs(out15 or out5 or 0)
            would_win = 1 if move > 0.3 else 0

        conn.execute("""
            UPDATE rejected_signals
            SET labeled=1, outcome_5m=?, outcome_15m=?, outcome_60m=?, would_win=?
            WHERE id=?
        """, (out5, out15, out60, would_win, row["id"]))

        labeled += 1
        results.append(
            f"{symbol} [{row['reason']}] → 15m: {out15}% win:{would_win}"
        )

    conn.commit()
    conn.close()
    return {"labeled": labeled, "skipped": skipped, "results": results}

def get_rejection_stats() -> dict:
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("""
        SELECT reason,
               COUNT(*) total,
               SUM(would_win) wins,
               ROUND(AVG(outcome_15m), 3) avg_15m
        FROM rejected_signals
        WHERE labeled=1
        GROUP BY reason
        ORDER BY total DESC
    """).fetchall()
    conn.close()
    stats = []
    for r in rows:
        total = r[1] or 0
        wins  = r[2] or 0
        stats.append({
            "reason":   r[0],
            "total":    total,
            "would_win_rate": round(wins / total * 100, 1) if total else 0,
            "avg_15m":  r[3],
        })
    return {"rejection_stats": stats}

if __name__ == "__main__":
    result = run_rejection_labeling(limit=50)
    print(f"Labeled: {result['labeled']} | Skipped: {result['skipped']}")
    for r in result["results"]:
        print(" ", r)
    print()
    stats = get_rejection_stats()
    for s in stats["rejection_stats"]:
        print(f"  {s['reason']}: {s['total']} total, {s['would_win_rate']}% would_win, avg15m={s['avg_15m']}%")
