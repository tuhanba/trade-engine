"""
core/ghost_learning.py — Ghost Learning İşlemcisi v1.0
=======================================================
WATCH, VETO ve SKIPPED sinyalleri paper_results tablosuna kaydedilir.
Bu modül, her sinyal için fiyat hareketini takip eder ve sonucu
GHOST_WIN, GHOST_LOSS veya EXPIRED olarak kaydeder.

Ağırlıklar:
  Gerçek trade  = 1.0
  Ghost trade   = 0.25–0.35
  Son 30 gün    = daha yüksek ağırlık
  Eski veri     = azalan ağırlık
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

GHOST_WEIGHT    = 0.30   # Ghost sonuçların gerçek trade'lere oranı
HORIZON_MINUTES = 480    # 8 saat — sinyal takip süresi
EXPIRY_HOURS    = 12     # Bu süreden uzun bekleyen sinyaller EXPIRED olur

_TRACKED_DECISIONS = {
    "WATCH", "VETO", "SKIPPED_BY_RISK", "SKIPPED_BY_REGIME",
    "SKIPPED_BY_LOW_SCORE", "SKIPPED_BY_COOLDOWN",
    "SKIPPED_BY_SPREAD", "SKIPPED_BY_MARGIN_RISK",
}


def track_skipped_signal(signal: dict, decision: str, client=None):
    """
    WATCH, VETO veya SKIPPED sinyal için paper_results'a takip kaydı açar.
    Sadece entry, sl ve tp1 değerleri olan sinyaller için çalışır.
    """
    try:
        from database import save_paper_trade

        if decision not in _TRACKED_DECISIONS:
            return

        entry = signal.get("entry") or signal.get("entry_zone")
        sl    = signal.get("sl") or signal.get("stop_loss")
        tp1   = signal.get("tp1")

        if not entry or not sl or not tp1:
            return

        save_paper_trade({
            "symbol":    signal.get("symbol"),
            "direction": signal.get("direction"),
            "entry":     entry,
            "sl":        sl,
            "tp1":       tp1,
        }, tracked_from=decision)

        logger.debug(
            f"[Ghost] {signal.get('symbol')} {decision} takibe alındı "
            f"entry={entry} sl={sl} tp1={tp1}"
        )
    except Exception as e:
        logger.warning(f"[Ghost] track_skipped_signal hatası: {e}")


def process_pending_results(client):
    """
    Bekleyen paper_results kayıtlarını işle.
    Her kayıt için mevcut fiyatı kontrol et ve sonucu güncelle.
    client: Binance public client (futures_ticker kullanır).
    """
    try:
        from database import get_pending_paper_results

        pending = get_pending_paper_results(limit=50)
        if not pending:
            return

        now = datetime.now(timezone.utc)

        for record in pending:
            try:
                _process_single(record, client, now)
            except Exception as e:
                logger.warning(f"[Ghost] Kayıt #{record.get('id')} işlenemedi: {e}")

    except Exception as e:
        logger.error(f"[Ghost] process_pending_results hatası: {e}")


def _process_single(record: dict, client, now: datetime):
    """Tek bir paper_result kaydını işle."""
    from database import update_paper_result

    record_id = record["id"]
    symbol    = record["symbol"]
    direction = record["direction"]
    entry     = float(record.get("preview_entry") or 0)
    sl        = float(record.get("preview_sl") or 0)
    tp1       = float(record.get("preview_tp1") or 0)

    if not entry or not sl or not tp1:
        update_paper_result(record_id, {
            "status":       "finalized",
            "finalized_at": now.isoformat(),
            "skip_decision_correct": None,
        })
        return

    # Süre kontrolü — çok uzun bekleyen sinyalleri EXPIRED yap
    try:
        created = datetime.fromisoformat(
            str(record["created_at"]).replace("Z", "+00:00")
        )
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        elapsed_hours = (now - created).total_seconds() / 3600
    except Exception:
        elapsed_hours = 0

    if elapsed_hours > EXPIRY_HOURS:
        update_paper_result(record_id, {
            "status":         "finalized",
            "finalized_at":   now.isoformat(),
            "hit_tp":         0,
            "hit_stop_first": 0,
            "setup_worked":   0,
        })
        logger.debug(f"[Ghost] #{record_id} {symbol} EXPIRED ({elapsed_hours:.1f}sa)")
        return

    # Mevcut fiyatı al
    current_price = _get_price(client, symbol)
    if not current_price:
        return

    is_long = str(direction).upper() == "LONG"
    tp1_hit = (is_long and current_price >= tp1) or (not is_long and current_price <= tp1)
    sl_hit  = (is_long and current_price <= sl)  or (not is_long and current_price >= sl)

    if tp1_hit:
        update_paper_result(record_id, {
            "status":                "finalized",
            "finalized_at":          now.isoformat(),
            "hit_tp":                1,
            "hit_stop_first":        0,
            "setup_worked":          1,
            "would_have_won":        1,
            "skip_decision_correct": 0,  # Geçilseydi kâr ederdi
        })
        logger.info(f"[Ghost] #{record_id} {symbol} GHOST_WIN (TP1 ulaşıldı)")

    elif sl_hit:
        update_paper_result(record_id, {
            "status":                "finalized",
            "finalized_at":          now.isoformat(),
            "hit_tp":                0,
            "hit_stop_first":        1,
            "setup_worked":          0,
            "would_have_won":        0,
            "skip_decision_correct": 1,  # Geçilmesi doğruydu
        })
        logger.info(f"[Ghost] #{record_id} {symbol} GHOST_LOSS (SL vuruldu)")


def _get_price(client, symbol: str) -> float:
    """Public futures ticker fiyatı — paper modda kullanılabilir."""
    try:
        if client:
            ticker = client.futures_ticker(symbol=symbol)
            return float(ticker["lastPrice"])
    except Exception:
        pass
    return 0.0


def get_ghost_learning_stats() -> dict:
    """
    Ghost learning istatistiklerini döndür.
    AI karar motorunun coin profil hesabında kullanılır.
    """
    try:
        from database import get_conn
        with get_conn() as conn:
            row = conn.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN hit_tp=1 THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN hit_stop_first=1 THEN 1 ELSE 0 END) as losses,
                    SUM(CASE WHEN skip_decision_correct=1 THEN 1 ELSE 0 END) as correct_skips,
                    AVG(max_favorable_excursion) as avg_mfe,
                    AVG(max_adverse_excursion) as avg_mae
                FROM paper_results
                WHERE status='finalized'
            """).fetchone()

        if not row:
            return {}

        total = row[0] or 0
        return {
            "total":          total,
            "ghost_wins":     row[1] or 0,
            "ghost_losses":   row[2] or 0,
            "ghost_win_rate": round((row[1] or 0) / total, 4) if total > 0 else 0,
            "correct_skips":  row[3] or 0,
            "avg_mfe":        round(float(row[4] or 0), 4),
            "avg_mae":        round(float(row[5] or 0), 4),
            "weight":         GHOST_WEIGHT,
        }
    except Exception as e:
        logger.error(f"[Ghost] get_ghost_learning_stats hatası: {e}")
        return {}
