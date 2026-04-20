"""
Counterfactual Engine — Adım C
outcome_labels'dan daha derin analiz:
  - setup_verdict:  VALID / INVALID / UNCLEAR
  - timing_verdict: GOOD / EARLY / LATE / CHOPPY
  - sl_verdict:     OK / TOO_TIGHT / TOO_WIDE
  - tp_verdict:     OK / TOO_EARLY / TOO_LATE
  - lesson:         AX'e öğretilecek kısa ders
  - cf_pnl_pct:     Optimal çıkışta olabilecek PnL %
"""
import os, sqlite3, logging
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("counterfactual")

DB_PATH = os.path.join(os.path.dirname(__file__), "trading.db")

def get_conn():
    return sqlite3.connect(DB_PATH)

def init_cf_table():
    conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS counterfactual_analysis (
            trade_id        INTEGER PRIMARY KEY,
            symbol          TEXT,
            direction       TEXT,
            setup_verdict   TEXT,
            timing_verdict  TEXT,
            sl_verdict      TEXT,
            tp_verdict      TEXT,
            cf_pnl_pct      REAL,
            lesson          TEXT,
            analyzed_at     TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    conn.close()

def _setup_verdict(mfe_pct, mae_pct):
    """Fiyat doğru yönde gitti mi?"""
    if mfe_pct >= 0.15:
        return "VALID"
    if mfe_pct < 0.05:
        return "INVALID"
    return "UNCLEAR"

def _timing_verdict(mfe_pct, mae_pct, p5m, p15m):
    """Giriş zamanlaması nasıldı?"""
    if mfe_pct < 0.05:
        return "CHOPPY"
    mae_ratio = mae_pct / (mfe_pct + 1e-6)
    # Erken girmiş: ilk 5dk negatif ama sonra MFE gelmiş
    if p5m is not None and p5m < -0.05 and mfe_pct > 0.15:
        return "EARLY"
    # Geç girmiş: p15m zaten MFE civarında, fırsat kaçmış
    if p15m is not None and p15m is not None and mfe_pct > 0 and p15m / mfe_pct > 0.85:
        return "LATE"
    if mae_ratio < 0.3:
        return "GOOD"
    if mae_ratio > 0.7:
        return "CHOPPY"
    return "GOOD"

def _sl_verdict(mfe_pct, mae_pct, actual_pnl_pct, quality):
    """Stop loss uygunluğu."""
    if actual_pnl_pct >= 0:
        # Kazandı — SL sorun değil
        if mae_pct > mfe_pct * 0.8:
            return "TOO_WIDE"
        return "OK"
    # Kaybetti
    if quality == "BAD_SETUP":
        return "OK"  # Setup zaten kötüydü, SL sorunu değil
    if mfe_pct > 0.1 and mae_pct < mfe_pct * 0.5:
        return "TOO_TIGHT"  # MFE vardı ama MAE'den stop yedi
    if mae_pct > mfe_pct * 3:
        return "TOO_WIDE"   # Çok fazla zarar izin verildi
    return "OK"

def _tp_verdict(mfe_pct, exit_eff, actual_pnl_pct):
    """Take profit uygunluğu."""
    if actual_pnl_pct < 0:
        return "OK"  # Zaten kaybetti, TP konu değil
    if exit_eff >= 0.8:
        return "OK"
    if exit_eff < 0.4 and mfe_pct > 0.3:
        return "TOO_EARLY"
    if mfe_pct > actual_pnl_pct * 3:
        return "TOO_EARLY"
    return "OK"

def _lesson(setup, timing, sl, tp, mfe, mae, eff, pnl):
    parts = []
    if setup == "INVALID":
        parts.append("Setup geçersiz: fiyat doğru yönde hiç gitmedi")
    if timing == "EARLY":
        parts.append("Erken giriş: ilk mumlar negatifti, beklemek daha iyiydi")
    if timing == "LATE":
        parts.append("Geç giriş: hareket çoğunlukla yaşandı, fırsat kaçtı")
    if timing == "CHOPPY":
        parts.append("Choppy piyasa: net yön yoktu")
    if sl == "TOO_TIGHT":
        parts.append(f"SL çok dardı: MFE {mfe:.2f}% vardı ama stop yendi")
    if tp == "TOO_EARLY":
        parts.append(f"Erken çıkış: MFE {mfe:.2f}% iken {pnl:.2f}% ile çıkıldı (eff:{eff:.2f})")
    if not parts:
        if pnl > 0:
            parts.append(f"Temiz trade: setup+timing+exit uyumlu (eff:{eff:.2f})")
        else:
            parts.append("Kabul edilebilir kayıp: setup kötüydü veya piyasa tersine döndü")
    return " | ".join(parts)

def analyze_trade(row):
    tid      = row["trade_id"]
    mfe      = row["mfe_pct"] or 0
    mae      = row["mae_pct"] or 0
    eff      = row["exit_eff"] or 0
    pnl      = row["actual_pnl_pct"] or 0
    quality  = row["quality"] or ""
    p5m      = row["price_5m_pct"]
    p15m     = row["price_15m_pct"]

    setup   = _setup_verdict(mfe, mae)
    timing  = _timing_verdict(mfe, mae, p5m, p15m)
    sl_v    = _sl_verdict(mfe, mae, pnl, quality)
    tp_v    = _tp_verdict(mfe, eff, pnl)
    cf_pnl  = round(mfe * 0.85, 4)  # Optimal çıkış tahmini (%85 MFE)
    lesson  = _lesson(setup, timing, sl_v, tp_v, mfe, mae, eff, pnl)

    return {
        "trade_id": tid,
        "symbol": row["symbol"],
        "direction": row["direction"],
        "setup_verdict": setup,
        "timing_verdict": timing,
        "sl_verdict": sl_v,
        "tp_verdict": tp_v,
        "cf_pnl_pct": cf_pnl,
        "lesson": lesson
    }

def run_counterfactual(limit=30):
    init_cf_table()
    conn = get_conn()
    rows = conn.execute("""
        SELECT o.trade_id, o.symbol, o.direction,
               o.mfe_pct, o.mae_pct, o.exit_eff,
               o.price_5m_pct, o.price_15m_pct,
               o.quality,
               t.pnl_pct as actual_pnl_pct
        FROM outcome_labels o
        JOIN trades t ON o.trade_id = t.id
        LEFT JOIN counterfactual_analysis c ON o.trade_id = c.trade_id
        WHERE c.trade_id IS NULL
        ORDER BY o.labeled_at DESC
        LIMIT ?
    """, (limit,)).fetchall()
    cols = ["trade_id","symbol","direction","mfe_pct","mae_pct","exit_eff",
            "price_5m_pct","price_15m_pct","quality","actual_pnl_pct"]
    trades = [dict(zip(cols, r)) for r in rows]
    conn.close()

    results = []
    for t in trades:
        try:
            data = analyze_trade(t)
            conn = get_conn()
            conn.execute("""
                INSERT OR REPLACE INTO counterfactual_analysis
                (trade_id,symbol,direction,setup_verdict,timing_verdict,
                 sl_verdict,tp_verdict,cf_pnl_pct,lesson)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (data["trade_id"], data["symbol"], data["direction"],
                  data["setup_verdict"], data["timing_verdict"],
                  data["sl_verdict"], data["tp_verdict"],
                  data["cf_pnl_pct"], data["lesson"]))
            conn.commit()
            conn.close()
            results.append(f"{data['symbol']} → setup:{data['setup_verdict']} timing:{data['timing_verdict']} sl:{data['sl_verdict']}")
        except Exception as e:
            logger.error(f"CF hata trade {t['trade_id']}: {e}")

    return {"analyzed": len(results), "results": results}

if __name__ == "__main__":
    import json
    print(json.dumps(run_counterfactual(20), indent=2, ensure_ascii=False))
