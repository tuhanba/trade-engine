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

try:
    from config import GHOST_WEIGHT as _GHOST_WEIGHT_FLOOR
except Exception:
    _GHOST_WEIGHT_FLOOR = 0.15

GHOST_WEIGHT    = _GHOST_WEIGHT_FLOOR   # floor; calculate_dynamic_ghost_weight() için referans
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


def _calc_excursions(entry: float, sl: float, current_price: float, is_long: bool) -> tuple[float, float]:
    """
    R cinsinden MFE ve MAE hesapla.
    MFE = max favorable excursion (kazanılan taraf)
    MAE = max adverse excursion (kaybedilen taraf)
    """
    risk = abs(entry - sl)
    if risk <= 0:
        return 0.0, 0.0
    if is_long:
        mfe = max(0.0, (current_price - entry) / risk)
        mae = max(0.0, (entry - current_price) / risk)
    else:
        mfe = max(0.0, (entry - current_price) / risk)
        mae = max(0.0, (current_price - entry) / risk)
    return round(mfe, 4), round(mae, 4)


def _calc_skip_correctness(hit_tp: int, hit_stop_first: int, tracked_from: str) -> int:
    """
    Atlama kararı doğru muydu?
    VETO/SKIP kararı + SL vuruldu → doğru atladık (1)
    VETO/SKIP kararı + TP vuruldu → yanlış atladık (0)
    İzleme verisi → -1 (geçersiz, kaydedilmez)
    """
    if tracked_from in ("VETO", "SKIPPED_BY_RISK", "SKIPPED_BY_REGIME",
                        "SKIPPED_BY_LOW_SCORE", "SKIPPED_BY_COOLDOWN",
                        "SKIPPED_BY_SPREAD", "SKIPPED_BY_MARGIN_RISK"):
        if hit_stop_first == 1 and hit_tp == 0:
            return 1   # Doğru atladık
        elif hit_tp == 1 and hit_stop_first == 0:
            return 0   # Yanlış atladık
    return -1  # İzleme verisi veya belirsiz


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

    # MFE/MAE hesapla ve kayıtta kayıtlı değerlerle karşılaştır
    cur_mfe, cur_mae = _calc_excursions(entry, sl, current_price, is_long)
    prev_mfe = float(record.get("max_favorable_excursion") or 0)
    prev_mae = float(record.get("max_adverse_excursion") or 0)
    new_mfe  = max(prev_mfe, cur_mfe)
    new_mae  = max(prev_mae, cur_mae)

    tracked_from = str(record.get("tracked_from") or "")

    if tp1_hit:
        correctness = _calc_skip_correctness(1, 0, tracked_from)
        updates = {
            "status":                "finalized",
            "finalized_at":          now.isoformat(),
            "hit_tp":                1,
            "hit_stop_first":        0,
            "setup_worked":          1,
            "would_have_won":        1,
            "max_favorable_excursion": new_mfe,
            "max_adverse_excursion":   new_mae,
            "first_touch":           "tp1",
        }
        if correctness >= 0:
            updates["skip_decision_correct"] = correctness
        update_paper_result(record_id, updates)
        logger.info(f"[Ghost] #{record_id} {symbol} GHOST_WIN (TP1 ulaşıldı) MFE={new_mfe:.2f}R MAE={new_mae:.2f}R")

    elif sl_hit:
        correctness = _calc_skip_correctness(0, 1, tracked_from)
        updates = {
            "status":                "finalized",
            "finalized_at":          now.isoformat(),
            "hit_tp":                0,
            "hit_stop_first":        1,
            "setup_worked":          0,
            "would_have_won":        0,
            "max_favorable_excursion": new_mfe,
            "max_adverse_excursion":   new_mae,
            "first_touch":           "stop",
        }
        if correctness >= 0:
            updates["skip_decision_correct"] = correctness
        update_paper_result(record_id, updates)
        logger.info(f"[Ghost] #{record_id} {symbol} GHOST_LOSS (SL vuruldu) MFE={new_mfe:.2f}R MAE={new_mae:.2f}R")

    else:
        # Henüz sonuçlanmadı — sadece MFE/MAE güncelle (değer artışı varsa)
        if new_mfe > prev_mfe or new_mae > prev_mae:
            update_paper_result(record_id, {
                "max_favorable_excursion": new_mfe,
                "max_adverse_excursion":   new_mae,
            })


def _get_price(client, symbol: str) -> float:
    """Public futures ticker fiyatı — paper modda kullanılabilir."""
    try:
        if client:
            ticker = client.futures_ticker(symbol=symbol)
            return float(ticker["lastPrice"])
    except Exception:
        pass
    return 0.0


def calculate_dynamic_ghost_weight() -> float:
    """
    Gerçek trade sayısına göre ghost ağırlığını dinamik hesapla.
    Az trade → yüksek ghost ağırlık (daha fazla öğren).
    Çok trade → düşük ghost ağırlık (gerçek data baskın).
    """
    try:
        from database import get_conn
        with get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM trades WHERE status='CLOSED'"
            ).fetchone()
        real_trades = row[0] if row else 0

        if real_trades < 10:
            return 0.45
        elif real_trades < 30:
            return 0.35
        elif real_trades < 100:
            return 0.25
        elif real_trades < 300:
            return 0.15
        else:
            return max(_GHOST_WEIGHT_FLOOR, 0.10)
    except Exception:
        return _GHOST_WEIGHT_FLOOR


def get_ghost_learning_stats() -> dict:
    """
    Ghost learning istatistiklerini döndür.
    AI karar motorunun coin profil hesabında kullanılir.
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
            "dynamic_weight": calculate_dynamic_ghost_weight(),
        }
    except Exception as e:
        logger.error(f"[Ghost] get_ghost_learning_stats hatası: {e}")
        return {}
