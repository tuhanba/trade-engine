"""
ghost_learner.py — AurvexAI Ghost Learning 2.0
================================================
Reddedilen sinyal adaylarını simüle ederek gizli fırsatları tespit eder.

Akış:
  signal_candidates (decision=REJECT/SKIP)
      → ghost_signals (kayıt)
      → Binance OHLCV (geçmiş fiyat)
      → ghost_results (simülasyon sonucu)
      → ghost_suggestions (AI Brain için öneri)

Kullanım:
  from ghost_learner import GhostLearner
  gl = GhostLearner()
  gl.run_cycle()          # tüm işlemi çalıştır
  gl.collect_ghosts()     # sadece kayıt
  gl.simulate_pending()   # sadece simülasyon
  gl.generate_report()    # Telegram raporu
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger("ax.ghost_learner")


# ── Yardımcı: DB bağlantısı ──────────────────────────────────────────────────

def _get_conn():
    from database import get_conn
    return get_conn()


# ── DDL bootstrap ─────────────────────────────────────────────────────────────

_GHOST_SIGNALS_DDL = """
CREATE TABLE IF NOT EXISTS ghost_signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id    INTEGER,
    symbol          TEXT NOT NULL,
    direction       TEXT NOT NULL,
    entry_price     REAL DEFAULT 0,
    stop_loss       REAL DEFAULT 0,
    tp1             REAL DEFAULT 0,
    tp2             REAL DEFAULT 0,
    tp3             REAL DEFAULT 0,
    atr             REAL DEFAULT 0,
    final_score     REAL DEFAULT 0,
    reject_reason   TEXT DEFAULT '',
    trigger_type    TEXT DEFAULT 'UNKNOWN',
    market_regime   TEXT DEFAULT 'NEUTRAL',
    simulated       INTEGER DEFAULT 0,
    created_at      TEXT DEFAULT (datetime('now'))
)
"""

_GHOST_RESULTS_DDL = """
CREATE TABLE IF NOT EXISTS ghost_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ghost_id        INTEGER NOT NULL,
    virtual_outcome TEXT DEFAULT 'OPEN',
    virtual_pnl_r   REAL DEFAULT 0,
    virtual_mfe     REAL DEFAULT 0,
    virtual_mae     REAL DEFAULT 0,
    bars_held       INTEGER DEFAULT 0,
    exit_price      REAL DEFAULT 0,
    simulated_at    TEXT DEFAULT (datetime('now'))
)
"""

_GHOST_SUGGESTIONS_DDL = """
CREATE TABLE IF NOT EXISTS ghost_suggestions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol              TEXT NOT NULL,
    trigger_type        TEXT NOT NULL,
    current_threshold   REAL DEFAULT 0,
    suggested_threshold REAL DEFAULT 0,
    virtual_wr          REAL DEFAULT 0,
    avg_virtual_r       REAL DEFAULT 0,
    sample_count        INTEGER DEFAULT 0,
    confidence          TEXT DEFAULT 'LOW',
    applied             INTEGER DEFAULT 0,
    created_at          TEXT DEFAULT (datetime('now'))
)
"""


def _ensure_ghost_tables():
    """Ghost tablolarını oluşturur — yoksa yaratır, varsa dokunmaz."""
    with _get_conn() as conn:
        conn.execute(_GHOST_SIGNALS_DDL)
        conn.execute(_GHOST_RESULTS_DDL)
        conn.execute(_GHOST_SUGGESTIONS_DDL)
    logger.info("[Ghost] Tablolar hazır.")


# ── Binance OHLCV ─────────────────────────────────────────────────────────────

def _fetch_ohlcv(symbol: str, start_time_ms: int,
                 limit: int = 120, interval: str = "5m") -> list[dict]:
    """
    Binance Futures'tan OHLCV çeker.
    start_time_ms: sinyal oluşturulma anı (milliseconds)
    limit: kaç bar simüle edilecek (120 bar = 10 saat @ 5m)
    """
    try:
        import config
        from binance.client import Client  # python-binance
        client = Client(
            api_key=getattr(config, "BINANCE_API_KEY", ""),
            api_secret=getattr(config, "BINANCE_API_SECRET", "")
        )
        klines = client.futures_klines(
            symbol=symbol,
            interval=interval,
            startTime=start_time_ms,
            limit=limit,
        )
        bars = []
        for i, k in enumerate(klines):
            bars.append({
                "index":  i,
                "open":   float(k[1]),
                "high":   float(k[2]),
                "low":    float(k[3]),
                "close":  float(k[4]),
                "volume": float(k[5]),
                "time_ms": int(k[0]),
            })
        return bars
    except Exception as exc:
        logger.warning("[Ghost] OHLCV çekme hatası %s: %s", symbol, exc)
        return []


# ── Simülasyon motoru ─────────────────────────────────────────────────────────

def _simulate_ghost(ghost: dict, bars: list[dict]) -> dict:
    """
    Ghost signal'ı bar-bar simüle eder.

    Returns:
        outcome: WIN / LOSS / OPEN
        virtual_pnl_r: R cinsinden PnL (-1.0 = tam SL, pozitif = TP)
        virtual_mfe: maksimum favorable excursion (fiyat farkı)
        virtual_mae: maksimum adverse excursion (fiyat farkı)
        bars_held: kaç bar tutuldu
        exit_price: çıkış fiyatı
    """
    entry = ghost["entry_price"]
    sl    = ghost["stop_loss"]
    tp1   = ghost["tp1"]
    tp2   = ghost["tp2"] or tp1   # tp2 yoksa tp1 kullan
    direction = ghost["direction"].upper()  # LONG / SHORT

    if entry <= 0 or sl <= 0 or tp1 <= 0:
        return {"virtual_outcome": "INVALID", "virtual_pnl_r": 0,
                "virtual_mfe": 0, "virtual_mae": 0,
                "bars_held": 0, "exit_price": entry}

    # Risk mesafesi (SL'ye uzaklık)
    if direction == "LONG":
        risk_dist = entry - sl
    else:
        risk_dist = sl - entry

    if risk_dist <= 0:
        return {"virtual_outcome": "INVALID", "virtual_pnl_r": 0,
                "virtual_mfe": 0, "virtual_mae": 0,
                "bars_held": 0, "exit_price": entry}

    mfe = 0.0
    mae = 0.0

    for bar in bars:
        if direction == "LONG":
            # MFE / MAE güncelle
            bar_mfe = bar["high"] - entry
            bar_mae = entry - bar["low"]
            mfe = max(mfe, bar_mfe)
            mae = max(mae, bar_mae)

            # SL kontrolü — önce SL'ye mi değdi?
            if bar["low"] <= sl:
                r = -1.0
                return {
                    "virtual_outcome": "LOSS",
                    "virtual_pnl_r": r,
                    "virtual_mfe": mfe,
                    "virtual_mae": mae,
                    "bars_held": bar["index"] + 1,
                    "exit_price": sl,
                }
            # TP2 kontrolü
            if tp2 > 0 and bar["high"] >= tp2:
                r = (tp2 - entry) / risk_dist
                return {
                    "virtual_outcome": "WIN",
                    "virtual_pnl_r": r,
                    "virtual_mfe": mfe,
                    "virtual_mae": mae,
                    "bars_held": bar["index"] + 1,
                    "exit_price": tp2,
                }
            # TP1 kontrolü
            if bar["high"] >= tp1:
                r = (tp1 - entry) / risk_dist
                return {
                    "virtual_outcome": "WIN",
                    "virtual_pnl_r": r,
                    "virtual_mfe": mfe,
                    "virtual_mae": mae,
                    "bars_held": bar["index"] + 1,
                    "exit_price": tp1,
                }

        else:  # SHORT
            bar_mfe = entry - bar["low"]
            bar_mae = bar["high"] - entry
            mfe = max(mfe, bar_mfe)
            mae = max(mae, bar_mae)

            if bar["high"] >= sl:
                r = -1.0
                return {
                    "virtual_outcome": "LOSS",
                    "virtual_pnl_r": r,
                    "virtual_mfe": mfe,
                    "virtual_mae": mae,
                    "bars_held": bar["index"] + 1,
                    "exit_price": sl,
                }
            if tp2 > 0 and bar["low"] <= tp2:
                r = (entry - tp2) / risk_dist
                return {
                    "virtual_outcome": "WIN",
                    "virtual_pnl_r": r,
                    "virtual_mfe": mfe,
                    "virtual_mae": mae,
                    "bars_held": bar["index"] + 1,
                    "exit_price": tp2,
                }
            if bar["low"] <= tp1:
                r = (entry - tp1) / risk_dist
                return {
                    "virtual_outcome": "WIN",
                    "virtual_pnl_r": r,
                    "virtual_mfe": mfe,
                    "virtual_mae": mae,
                    "bars_held": bar["index"] + 1,
                    "exit_price": tp1,
                }

    # Hiç çözümlenmedi — hâlâ açık
    last = bars[-1]["close"] if bars else entry
    if direction == "LONG":
        open_r = (last - entry) / risk_dist
    else:
        open_r = (entry - last) / risk_dist

    return {
        "virtual_outcome": "OPEN",
        "virtual_pnl_r": open_r,
        "virtual_mfe": mfe,
        "virtual_mae": mae,
        "bars_held": len(bars),
        "exit_price": last,
    }


# ── Ghost Learner ana sınıf ───────────────────────────────────────────────────

class GhostLearner:
    """
    Ghost Learning 2.0 orkestratörü.

    Kullanım:
        gl = GhostLearner(min_score=0.40, ohlcv_bars=120)
        gl.run_cycle()
    """

    def __init__(
        self,
        min_score: float = 0.40,   # Çok kötü sinyalleri alma
        ohlcv_bars: int = 120,     # Kaç bar simüle et (120x5m = 10 saat)
        ohlcv_interval: str = "5m",
        rate_limit_sleep: float = 0.3,  # Binance rate limit için
    ):
        self.min_score = min_score
        self.ohlcv_bars = ohlcv_bars
        self.ohlcv_interval = ohlcv_interval
        self.rate_limit_sleep = rate_limit_sleep
        _ensure_ghost_tables()

    # ── Adım 1: Reddedilen sinyalleri topla ──────────────────────────────────

    def collect_ghosts(self, hours_back: int = 48) -> int:
        """
        signal_candidates'tan reddedilen sinyalleri ghost_signals'a kopyalar.
        Zaten kopyalanmış olanları tekrar almaz (candidate_id unique).

        Returns: kaç yeni ghost kaydedildi
        """
        count = 0
        with _get_conn() as conn:
            # Zaten kaydedilmiş candidate_id'leri al
            existing_ids = {
                r[0] for r in conn.execute(
                    "SELECT candidate_id FROM ghost_signals WHERE candidate_id IS NOT NULL"
                ).fetchall()
            }

            cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours_back)
                      ).strftime("%Y-%m-%d %H:%M:%S")

            candidates = conn.execute("""
                SELECT id, symbol, direction, entry, sl, tp1, tp2, tp3,
                       final_score, decision, market_regime, created_at
                FROM signal_candidates
                WHERE decision IN ('REJECT', 'SKIP', 'FILTERED', 'VETO')
                  AND created_at >= ?
                  AND final_score >= ?
                ORDER BY id DESC
            """, (cutoff, self.min_score)).fetchall()

            for row in candidates:
                cid = row[0]
                if cid in existing_ids:
                    continue

                # entry/sl/tp kontrolü — eksik olanlara girme
                entry = row[3] or 0
                sl    = row[4] or 0
                tp1   = row[5] or 0
                if entry <= 0 or sl <= 0 or tp1 <= 0:
                    continue

                conn.execute("""
                    INSERT INTO ghost_signals
                        (candidate_id, symbol, direction, entry_price,
                         stop_loss, tp1, tp2, tp3,
                         final_score, reject_reason, market_regime,
                         simulated, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,0,?)
                """, (
                    cid,
                    row[1],   # symbol
                    row[2],   # direction
                    entry,
                    sl,
                    tp1,
                    row[6] or 0,  # tp2
                    row[7] or 0,  # tp3
                    row[8] or 0,  # final_score
                    row[9] or "", # decision (reject_reason olarak)
                    row[10] or "NEUTRAL",  # market_regime
                    row[11],  # created_at
                ))
                count += 1

        logger.info("[Ghost] %d yeni ghost signal kaydedildi.", count)
        return count

    # ── Adım 2: Simülasyon ───────────────────────────────────────────────────

    def simulate_pending(self, batch_size: int = 50) -> int:
        """
        Simüle edilmemiş ghost signal'larını işler.
        Binance'tan fiyat çeker, simüle eder, ghost_results'a yazar.

        Returns: kaç ghost simüle edildi
        """
        with _get_conn() as conn:
            pending = conn.execute("""
                SELECT id, symbol, direction, entry_price,
                       stop_loss, tp1, tp2, tp3, final_score,
                       reject_reason, trigger_type, created_at
                FROM ghost_signals
                WHERE simulated = 0
                ORDER BY id ASC
                LIMIT ?
            """, (batch_size,)).fetchall()

        if not pending:
            logger.info("[Ghost] Simüle edilecek ghost yok.")
            return 0

        count = 0
        for row in pending:
            ghost = {
                "id":          row[0],
                "symbol":      row[1],
                "direction":   row[2],
                "entry_price": row[3],
                "stop_loss":   row[4],
                "tp1":         row[5],
                "tp2":         row[6],
                "tp3":         row[7],
                "final_score": row[8],
                "reject_reason": row[9],
                "trigger_type": row[10],
                "created_at":  row[11],
            }

            # created_at → milliseconds
            try:
                dt = datetime.fromisoformat(ghost["created_at"].replace("Z", "+00:00"))
            except Exception:
                dt = datetime.now(timezone.utc)
            start_ms = int(dt.timestamp() * 1000)

            # OHLCV çek
            bars = _fetch_ohlcv(
                ghost["symbol"],
                start_time_ms=start_ms,
                limit=self.ohlcv_bars,
                interval=self.ohlcv_interval,
            )
            time.sleep(self.rate_limit_sleep)  # rate limit

            if not bars:
                logger.debug("[Ghost] %s için OHLCV boş, atlanıyor.", ghost["symbol"])
                continue

            # Simüle et
            result = _simulate_ghost(ghost, bars)

            # Kaydet
            with _get_conn() as conn:
                conn.execute("""
                    INSERT INTO ghost_results
                        (ghost_id, virtual_outcome, virtual_pnl_r,
                         virtual_mfe, virtual_mae, bars_held, exit_price)
                    VALUES (?,?,?,?,?,?,?)
                """, (
                    ghost["id"],
                    result["virtual_outcome"],
                    result["virtual_pnl_r"],
                    result["virtual_mfe"],
                    result["virtual_mae"],
                    result["bars_held"],
                    result["exit_price"],
                ))
                conn.execute(
                    "UPDATE ghost_signals SET simulated=1 WHERE id=?",
                    (ghost["id"],)
                )

            logger.debug(
                "[Ghost] %s %s → %s (%.2fR, %d bars)",
                ghost["symbol"], ghost["direction"],
                result["virtual_outcome"], result["virtual_pnl_r"],
                result["bars_held"],
            )
            count += 1

        logger.info("[Ghost] %d ghost simüle edildi.", count)
        return count

    # ── Adım 3: Öneri üret ───────────────────────────────────────────────────

    def generate_suggestions(self, min_samples: int = 10,
                              min_virtual_wr: float = 0.55,
                              min_avg_r: float = 0.7) -> list[dict]:
        """
        Ghost sonuçlarından threshold öneri üretir.
        Sonuçları ghost_suggestions tablosuna yazar.

        Returns: öneri listesi
        """
        suggestions = []
        with _get_conn() as conn:
            stats = conn.execute("""
                SELECT
                    gs.symbol,
                    gs.trigger_type,
                    COUNT(*) AS n,
                    SUM(CASE WHEN gr.virtual_outcome='WIN' THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS vwr,
                    AVG(gr.virtual_pnl_r) AS avg_r,
                    AVG(gs.final_score) AS avg_score
                FROM ghost_signals gs
                JOIN ghost_results gr ON gs.id = gr.ghost_id
                WHERE gr.virtual_outcome IN ('WIN','LOSS')
                  AND gs.created_at >= datetime('now', '-30 days')
                GROUP BY gs.symbol, gs.trigger_type
                HAVING n >= ?
                   AND vwr >= ?
                   AND avg_r >= ?
                ORDER BY avg_r DESC
            """, (min_samples, min_virtual_wr, min_avg_r)).fetchall()

        for row in stats:
            symbol, trigger, n, vwr, avg_r, avg_score = row

            # Güven seviyesi
            if n >= 30 and vwr >= 0.65:
                confidence = "HIGH"
            elif n >= 15:
                confidence = "MEDIUM"
            else:
                confidence = "LOW"

            # Mevcut threshold = avg_score (reddedilme anındaki skor)
            current = round(avg_score, 3) if avg_score else 0.60
            suggested = max(round(current - 0.05, 3), 0.40)

            suggestion = {
                "symbol":               symbol,
                "trigger_type":         trigger,
                "current_threshold":    current,
                "suggested_threshold":  suggested,
                "virtual_wr":           round(vwr, 3),
                "avg_virtual_r":        round(avg_r, 3),
                "sample_count":         n,
                "confidence":           confidence,
            }
            suggestions.append(suggestion)

            # DB'ye yaz (upsert değil — her hafta yeni kayıt)
            with _get_conn() as conn:
                conn.execute("""
                    INSERT INTO ghost_suggestions
                        (symbol, trigger_type, current_threshold,
                         suggested_threshold, virtual_wr, avg_virtual_r,
                         sample_count, confidence)
                    VALUES (?,?,?,?,?,?,?,?)
                """, (
                    symbol, trigger, current, suggested,
                    round(vwr, 3), round(avg_r, 3), n, confidence,
                ))

        logger.info("[Ghost] %d öneri üretildi.", len(suggestions))
        return suggestions

    # ── Adım 4: Rapor ────────────────────────────────────────────────────────

    def generate_report(self) -> str:
        """
        Haftalık ghost learning raporu metnini üretir.
        Telegram'a göndermek için string döner.
        """
        with _get_conn() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM ghost_signals WHERE simulated=1"
            ).fetchone()[0]

            win_count = conn.execute(
                "SELECT COUNT(*) FROM ghost_results WHERE virtual_outcome='WIN'"
            ).fetchone()[0]
            loss_count = conn.execute(
                "SELECT COUNT(*) FROM ghost_results WHERE virtual_outcome='LOSS'"
            ).fetchone()[0]
            avg_r = conn.execute(
                "SELECT AVG(virtual_pnl_r) FROM ghost_results WHERE virtual_outcome IN ('WIN','LOSS')"
            ).fetchone()[0] or 0

            # En iyi fırsatlar
            top_patterns = conn.execute("""
                SELECT gs.symbol, gs.trigger_type,
                       COUNT(*) as n,
                       AVG(gr.virtual_pnl_r) as avg_r,
                       SUM(CASE WHEN gr.virtual_outcome='WIN' THEN 1 ELSE 0 END)*100.0/COUNT(*) as wr
                FROM ghost_signals gs
                JOIN ghost_results gr ON gs.id=gr.ghost_id
                WHERE gr.virtual_outcome IN ('WIN','LOSS')
                  AND gs.created_at >= datetime('now','-7 days')
                GROUP BY gs.symbol, gs.trigger_type
                HAVING n >= 5 AND wr >= 55
                ORDER BY avg_r DESC
                LIMIT 5
            """).fetchall()

            # Öneriler
            suggestions = conn.execute("""
                SELECT symbol, trigger_type, current_threshold,
                       suggested_threshold, confidence
                FROM ghost_suggestions
                WHERE applied=0
                ORDER BY id DESC LIMIT 5
            """).fetchall()

        vwr = win_count * 100 / (win_count + loss_count) if (win_count + loss_count) > 0 else 0

        lines = [
            "👻 Ghost Learning Haftalık Rapor",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"📅 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC",
            f"🔍 Simüle edilen: {total} ghost signal",
            f"📊 Virtual WR: {vwr:.1f}%  |  Avg R: {avg_r:.2f}",
            "",
        ]

        if top_patterns:
            lines.append("🏆 En İyi Kaçırılan Fırsatlar")
            for row in top_patterns:
                sym, trig, n, r, wr = row
                lines.append(f"  {sym} [{trig}]  WR:{wr:.0f}%  R:{r:.2f}  ({n} ghost)")
            lines.append("")

        if suggestions:
            lines.append("💡 Threshold Önerileri (AI Brain için)")
            for row in suggestions:
                sym, trig, curr, sugg, conf = row
                lines.append(
                    f"  {sym} {trig}: {curr:.2f}→{sugg:.2f}  [{conf}]"
                )
            lines.append("")

        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        report = "\n".join(lines)
        logger.info("[Ghost] Rapor hazır:\n%s", report)
        return report

    # ── Tam döngü ─────────────────────────────────────────────────────────────

    def run_cycle(self, send_telegram: bool = True) -> dict:
        """
        Tam Ghost Learning döngüsü:
        1. collect_ghosts
        2. simulate_pending
        3. generate_suggestions
        4. generate_report (+ Telegram)

        Returns: özet dict
        """
        logger.info("[Ghost] === Ghost Learning 2.0 başlıyor ===")
        t0 = time.time()

        collected = self.collect_ghosts()
        simulated = self.simulate_pending()
        suggestions = self.generate_suggestions()
        report = self.generate_report()

        elapsed = round(time.time() - t0, 1)
        logger.info("[Ghost] Döngü tamamlandı: %.1f saniye", elapsed)

        if send_telegram and report:
            try:
                from notifier import send_telegram_message
                send_telegram_message(report)
            except Exception as exc:
                logger.warning("[Ghost] Telegram gönderilemedi: %s", exc)

        return {
            "collected": collected,
            "simulated": simulated,
            "suggestions": len(suggestions),
            "elapsed_seconds": elapsed,
        }


# ── Standalone çalıştırma ────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s — %(message)s",
    )

    gl = GhostLearner(min_score=0.40, ohlcv_bars=120)

    cmd = sys.argv[1] if len(sys.argv) > 1 else "cycle"

    if cmd == "collect":
        n = gl.collect_ghosts()
        print(f"Toplanan: {n}")
    elif cmd == "simulate":
        n = gl.simulate_pending()
        print(f"Simüle edilen: {n}")
    elif cmd == "report":
        print(gl.generate_report())
    elif cmd == "suggest":
        s = gl.generate_suggestions()
        for item in s:
            print(item)
    else:  # cycle (default)
        result = gl.run_cycle(send_telegram=False)
        print(result)
