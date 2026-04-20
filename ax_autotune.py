"""
AX Auto-Tune — Otomatik Parametre Optimizasyonu
================================================
Her 6 saatte bir son 50-100 trade'i analiz eder ve gerekirse
parametreleri (TP, SL, risk_pct) otomatik ayarlar.

Kurallar (tek seferde max 1 değişiklik, en önemlisi önce):
  1. Drawdown yüksekse risk azalt (savunma öncelikli)
  2. Expectancy negatifse en kırılgan parametreyi düzelt
  3. TP düşük (avg win R < 1.5): TP artır
  4. SL geniş (avg loss R < -1.3): SL daralt
  5. Win rate yüksek + düşük drawdown: risk artır (ödül)

Kısıtlar:
  tp_atr_mult:  [1.5, 4.0]
  sl_atr_mult:  [0.8, 2.0]
  risk_pct:     [0.5, 3.0]
  Min 20 trade: değişiklik yok
"""

import os
import sqlite3
import logging
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trading.db")
logger  = logging.getLogger(__name__)

MIN_TRADES   = 20
SAMPLE_SIZE  = 80

BOUNDS = {
    "tp_atr_mult": (1.5, 4.0),
    "sl_atr_mult": (0.8, 2.0),
    "risk_pct":    (0.5, 3.0),
}

def _clamp(val, key):
    lo, hi = BOUNDS[key]
    return round(max(lo, min(hi, val)), 2)


def run_autotune() -> dict:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Mevcut params
    p_row = conn.execute(
        "SELECT * FROM params ORDER BY version DESC LIMIT 1"
    ).fetchone()
    if not p_row:
        conn.close()
        return {"changed": False, "reason": "params yok"}
    p = dict(p_row)

    # Son N trade
    trades = conn.execute("""
        SELECT status, net_pnl, r_multiple, pnl_pct
        FROM trades
        WHERE status IN ('WIN','LOSS')
        ORDER BY id DESC LIMIT ?
    """, (SAMPLE_SIZE,)).fetchall()

    if len(trades) < MIN_TRADES:
        conn.close()
        return {
            "changed": False,
            "reason": f"yetersiz veri: {len(trades)}/{MIN_TRADES} trade",
            "n": len(trades),
        }

    # Metrikler
    n      = len(trades)
    wins   = [t for t in trades if t["status"] == "WIN"]
    losses = [t for t in trades if t["status"] == "LOSS"]
    win_rate   = len(wins) / n * 100
    avg_win_r  = sum(t["r_multiple"] for t in wins)   / max(len(wins), 1)
    avg_loss_r = sum(t["r_multiple"] for t in losses) / max(len(losses), 1)
    avg_pnl    = sum(t["net_pnl"] for t in trades)    / n
    total_pnl  = sum(t["net_pnl"] for t in trades)

    # Kümülatif drawdown
    cum = 0.0; peak = 0.0; max_dd = 0.0
    for t in reversed(list(trades)):
        cum  += t["net_pnl"]
        peak  = max(peak, cum)
        max_dd = min(max_dd, cum - peak)

    # Profit factor
    gross_win  = sum(t["net_pnl"] for t in wins)
    gross_loss = abs(sum(t["net_pnl"] for t in losses))
    pf = gross_win / gross_loss if gross_loss > 0 else (1.5 if gross_win > 0 else 0)

    expectancy = (win_rate/100 * avg_win_r) + ((1 - win_rate/100) * avg_loss_r)

    summary = {
        "n": n, "win_rate": round(win_rate, 1),
        "avg_win_r": round(avg_win_r, 2), "avg_loss_r": round(avg_loss_r, 2),
        "expectancy": round(expectancy, 3), "profit_factor": round(pf, 2),
        "max_dd": round(max_dd, 3), "total_pnl": round(total_pnl, 3),
    }

    # ── KARAR AĞACI ──────────────────────────────────────────────────────────

    change_key  = None
    change_old  = None
    change_new  = None
    reason_text = None

    # 1. Savunma: drawdown kritik
    if max_dd < -15.0:
        new_risk = _clamp(p["risk_pct"] - 0.25, "risk_pct")
        if new_risk < p["risk_pct"]:
            change_key  = "risk_pct"
            change_old  = p["risk_pct"]
            change_new  = new_risk
            reason_text = f"Kritik drawdown {max_dd:.1f}$ → risk düşürüldü"

    # 2. Expectancy negatif + TP düşük
    elif expectancy < 0 and avg_win_r < 1.5 and len(wins) >= 5:
        new_tp = _clamp(p["tp_atr_mult"] + 0.2, "tp_atr_mult")
        if new_tp > p["tp_atr_mult"]:
            change_key  = "tp_atr_mult"
            change_old  = p["tp_atr_mult"]
            change_new  = new_tp
            reason_text = f"Negatif expectancy ({expectancy:.3f}), avg_win_R={avg_win_r:.2f} → TP artırıldı"

    # 3. Expectancy negatif + SL geniş
    elif expectancy < 0 and avg_loss_r < -1.3 and len(losses) >= 5:
        new_sl = _clamp(p["sl_atr_mult"] - 0.1, "sl_atr_mult")
        if new_sl < p["sl_atr_mult"]:
            change_key  = "sl_atr_mult"
            change_old  = p["sl_atr_mult"]
            change_new  = new_sl
            reason_text = f"Kayıplar fazla büyük (avg_loss_R={avg_loss_r:.2f}) → SL daraltıldı"

    # 4. TP çok düşük (win oranı iyi ama kazanç küçük)
    elif avg_win_r < 1.3 and win_rate >= 45 and pf < 1.2 and len(wins) >= 8:
        new_tp = _clamp(p["tp_atr_mult"] + 0.15, "tp_atr_mult")
        if new_tp > p["tp_atr_mult"]:
            change_key  = "tp_atr_mult"
            change_old  = p["tp_atr_mult"]
            change_new  = new_tp
            reason_text = f"avg_win_R={avg_win_r:.2f} düşük, WR={win_rate:.0f}% iyi → TP hafif artırıldı"

    # 5. Ödül: iyi performans → risk artır
    elif win_rate >= 55 and pf >= 1.5 and max_dd > -8.0 and avg_pnl > 0:
        new_risk = _clamp(p["risk_pct"] + 0.1, "risk_pct")
        if new_risk > p["risk_pct"]:
            change_key  = "risk_pct"
            change_old  = p["risk_pct"]
            change_new  = new_risk
            reason_text = f"Güçlü performans WR={win_rate:.0f}% PF={pf:.2f} → risk artırıldı"

    # Değişiklik yok
    if change_key is None:
        conn.close()
        return {
            "changed": False,
            "reason": "parametre değişikliği gerekmiyor",
            **summary,
        }

    # ── YENİ PARAMS VERSİYONU ──────────────────────────────────────────────
    new_p = dict(p)
    new_p[change_key] = change_new
    new_version = p["version"] + 1

    conn.execute("""
        INSERT INTO params
          (version, sl_atr_mult, tp_atr_mult, rsi5_min, rsi5_max,
           rsi1_min, rsi1_max, vol_ratio_min, min_volume_m,
           min_change_pct, risk_pct, updated_at, ai_reason)
        SELECT ?, sl_atr_mult, tp_atr_mult, rsi5_min, rsi5_max,
               rsi1_min, rsi1_max, vol_ratio_min, min_volume_m,
               min_change_pct, risk_pct, datetime('now'), ?
        FROM params ORDER BY version DESC LIMIT 1
    """, (new_version, reason_text))

    # Değişen kolonu güncelle
    conn.execute(
        f"UPDATE params SET {change_key}=? WHERE version=?",
        (change_new, new_version)
    )

    # AI log
    conn.execute("""
        INSERT INTO ai_logs (created_at, trades_analyzed, win_rate, avg_rr, insight, changes)
        VALUES (datetime('now'), ?, ?, ?, ?, ?)
    """, (
        n, round(win_rate, 1), round(avg_win_r, 2),
        f"AutoTune: {reason_text}",
        f"{change_key}: {change_old} → {change_new}",
    ))

    conn.commit()
    conn.close()

    logger.info(f"AutoTune v{new_version}: {change_key} {change_old}→{change_new} | {reason_text}")

    return {
        "changed":     True,
        "new_version": new_version,
        "change_key":  change_key,
        "change_old":  change_old,
        "change_new":  change_new,
        "reason":      reason_text,
        **summary,
    }


if __name__ == "__main__":
    result = run_autotune()
    print(f"Changed: {result['changed']}")
    print(f"Reason:  {result['reason']}")
    if result.get("n"):
        print(f"N={result['n']} WR={result.get('win_rate')}% "
              f"avgWinR={result.get('avg_win_r')} avgLossR={result.get('avg_loss_r')} "
              f"E={result.get('expectancy')} PF={result.get('profit_factor')} "
              f"DD={result.get('max_dd')}")
    if result["changed"]:
        print(f"  {result['change_key']}: {result['change_old']} → {result['change_new']}")
