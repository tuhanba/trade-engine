"""
Paper outcome tracker — girilmeyen sinyallerin fiyat yolunu 1m mumlarla takip eder.
Konservatif intrabar sıralama: LONG'ta önce düşüş (stop) sonra yükseliş (TP).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from database import DB_PATH, get_pending_paper_results, update_paper_result

logger = logging.getLogger(__name__)


def _parse_created_at(s: str | None):
    if not s:
        return datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return datetime.now(timezone.utc)


def _resolve_bar(direction: str, high: float, low: float, sl: float, tp1: float) -> str | None:
    if direction == "LONG":
        sl_hit = low <= sl
        tp_hit = high >= tp1
        if sl_hit and tp_hit:
            return "stop"
        if sl_hit:
            return "stop"
        if tp_hit:
            return "tp1"
        return None
    if direction == "SHORT":
        sl_hit = high >= sl
        tp_hit = low <= tp1
        if sl_hit and tp_hit:
            return "stop"
        if sl_hit:
            return "stop"
        if tp_hit:
            return "tp1"
        return None
    return None


def _simulate_path(
    kl: list,
    direction: str,
    entry: float,
    sl: float,
    tp1: float,
    start_ms: int,
    horizon_minutes: float,
):
    """kline Binance futures format: open time ms ..."""
    if not kl:
        return {
            "first_touch": "no_data",
            "hit_tp": 0,
            "hit_stop_first": 0,
            "setup_worked": 0,
            "would_have_won": 0,
            "ttm": 0.0,
            "mfe_r": 0.0,
            "mae_r": 0.0,
        }

    sl_dist = abs(entry - sl) + 1e-12

    cutoff = start_ms + int(horizon_minutes * 60_000)
    filtered = [b for b in kl if float(b[0]) >= float(start_ms) and float(b[0]) <= cutoff]
    if not filtered:
        filtered = kl

    mfe_r = 0.0
    mae_r = 0.0
    first_touch = None
    ttm = 0.0
    hit_tp = 0
    hit_stop_first = 0

    for b in filtered:
        o, h, l = float(b[1]), float(b[2]), float(b[3])
        ts = float(b[0])
        touch = _resolve_bar(direction, h, l, sl, tp1)
        if direction == "LONG":
            mfe_r = max(mfe_r, max(0.0, (h - entry) / sl_dist))
            mae_r = max(mae_r, max(0.0, (entry - l) / sl_dist))
        else:
            mfe_r = max(mfe_r, max(0.0, (entry - l) / sl_dist))
            mae_r = max(mae_r, max(0.0, (h - entry) / sl_dist))

        if first_touch is None and touch == "stop":
            first_touch = "stop"
            hit_stop_first = 1
            elapsed = (ts - start_ms) / 60_000.0
            ttm = max(0.0, elapsed + 0.5)
            break
        if first_touch is None and touch == "tp1":
            first_touch = "tp1"
            hit_tp = 1
            elapsed = (ts - start_ms) / 60_000.0
            ttm = max(0.0, elapsed + 0.5)
            break

    if first_touch is None:
        first_touch = "neither_horizon"

    would_have_win = hit_tp == 1 and hit_stop_first == 0

    return {
        "first_touch": first_touch,
        "hit_tp": hit_tp,
        "hit_stop_first": hit_stop_first,
        "setup_worked": would_have_win,
        "would_have_won": would_have_win,
        "ttm": ttm,
        "mfe_r": round(mfe_r, 4),
        "mae_r": round(mae_r, 4),
    }


def finalize_paper_row(client, row: dict) -> bool:
    symbol = row["symbol"]
    direction = row.get("direction") or "LONG"
    entry = float(row["preview_entry"])
    sl = float(row["preview_sl"])
    tp1 = float(row["preview_tp1"])
    horizon = float(row.get("horizon_minutes") or 480.0)
    created = _parse_created_at(row.get("created_at"))
    start_ms = int(created.timestamp() * 1000)
    end_ms = start_ms + int(horizon * 60_000) + 120_000

    try:
        kl = client.futures_klines(
            symbol=symbol,
            interval="1m",
            startTime=start_ms,
            endTime=end_ms,
            limit=1500,
        )
    except Exception as e:
        logger.warning(f"[paper_tracker] kline hatası {symbol}: {e}")
        return False

    out = _simulate_path(kl, direction, entry, sl, tp1, start_ms, horizon)
    tracked_from = row.get("tracked_from") or "candidate"

    if tracked_from in ("candidate", "watchlist", "telegram_gap"):
        skip_dec = 0 if int(out["would_have_won"]) == 1 else 1
    else:
        skip_dec = 1

    if out["first_touch"] == "no_data":
        return False

    now = datetime.now(timezone.utc).isoformat()
    update_paper_result(
        row["id"],
        {
            "hit_tp": out["hit_tp"],
            "hit_stop_first": out["hit_stop_first"],
            "time_to_move_minutes": out["ttm"],
            "max_favorable_excursion": out["mfe_r"],
            "max_adverse_excursion": out["mae_r"],
            "setup_worked": out["setup_worked"],
            "would_have_won": out["would_have_won"],
            "first_touch": out["first_touch"],
            "skip_decision_correct": skip_dec,
            "status": "completed",
            "finalized_at": now,
        },
    )

    try:
        from core.ai_decision_engine import AIDecisionEngine

        AIDecisionEngine(db_path=DB_PATH).learn_from_paper_outcome(
            symbol=symbol,
            tracked_from=tracked_from,
            would_have_won=int(out["would_have_won"]),
            mfe_r=float(out["mfe_r"]),
            mae_r=float(out["mae_r"]),
            first_touch=out["first_touch"],
            skip_correct=int(skip_dec),
        )
    except Exception as e:
        logger.debug(f"[paper_tracker] AI paper learn atlandı: {e}")

    return True


def process_pending_paper_results(client, limit: int = 35) -> int:
    rows = get_pending_paper_results(limit=limit)
    done = 0
    for row in rows:
        try:
            if finalize_paper_row(client, row):
                done += 1
        except Exception as e:
            logger.warning(f"[paper_tracker] finalize hata {row.get('symbol')}: {e}")
    return done


# ─────────────────────────────────────────────────────────────────────────────
# PaperTracker — scalp_bot_v3.py ile uyumlu wrapper sınıfı
# ─────────────────────────────────────────────────────────────────────────────
class PaperTracker:
    """
    paper_tracker modülündeki fonksiyonları sınıf arayüzüyle saran wrapper.
    scalp_bot_v3.py: _tracker = PaperTracker(db_path=DB_PATH)
    """
    def __init__(self, db_path: str = None, client=None):
        self.db_path = db_path
        self.client  = client

    def set_client(self, client):
        self.client = client

    def process_pending(self, limit: int = 35) -> int:
        """Horizon süresi dolan WATCH/VETO sinyallerini simüle et ve AI'a öğret."""
        try:
            return process_pending_paper_results(self.client, limit=limit)
        except Exception as e:
            logger.error(f"[PaperTracker] process_pending hata: {e}")
            return 0

    def finalize_row(self, row: dict) -> bool:
        """Tek bir paper_results satırını simüle et."""
        try:
            return finalize_paper_row(self.client, row)
        except Exception as e:
            logger.error(f"[PaperTracker] finalize_row hata: {e}")
            return False
