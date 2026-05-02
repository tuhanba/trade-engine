"""
Data Layer v2 — DB-Persistent Signal Lifecycle
================================================
3 eşik katmanı:
  DATA_THRESHOLD       : DB'ye kaydet (veri toplama)
  WATCHLIST_THRESHOLD  : Dashboard watchlist
  TELEGRAM_THRESHOLD   : Telegram bildirimi
  TRADE_THRESHOLD      : İşlem açma

Signal lifecycle stages:
  SCANNED → TREND_CHECKED → TRIGGER_CHECKED → RISK_CHECKED
  → AI_CHECKED → APPROVED_FOR_WATCHLIST → APPROVED_FOR_TELEGRAM
  → APPROVED_FOR_TRADE → REJECTED → OPENED → MANAGED → CLOSED | ERROR

Tüm lifecycle geçişleri signal_events tablosuna yazılır.
Memory'de sadece aktif sinyallerin referansı tutulur.
"""
import time
import uuid
import logging
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Any

logger = logging.getLogger(__name__)

try:
    import sys as _s, os as _o
    _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
    from config import (
        DATA_THRESHOLD, WATCHLIST_THRESHOLD,
        TELEGRAM_THRESHOLD, TRADE_THRESHOLD,
    )
except ImportError:
    DATA_THRESHOLD      = 45
    WATCHLIST_THRESHOLD = 65
    TELEGRAM_THRESHOLD  = 75
    TRADE_THRESHOLD     = 82

# Reject sebepleri — net logging için standart kodlar
REJECT_REASONS = {
    "low_volume", "bad_spread", "weak_trend", "weak_trigger",
    "bad_rr", "high_funding", "low_confidence", "bad_session",
    "ai_veto", "risk_guard_failed", "duplicate_signal",
    "correlation_risk", "pump_dump_risk", "quality_too_low",
    "below_min_notional", "zero_position_size", "invalid_atr",
    "no_candle_data", "low_movement",
}


@dataclass
class SignalData:
    id:        str   = field(default_factory=lambda: str(uuid.uuid4()))
    symbol:    str   = ""
    timestamp: float = field(default_factory=time.time)
    source:    str   = "system"
    timeframe: str   = "1m"
    direction: Optional[str] = None

    # Scores
    coin_score:    float = 0.0
    trend_score:   float = 0.0
    trigger_score: float = 0.0
    risk_score:    float = 0.0
    ai_score:      float = 0.0
    final_score:   float = 0.0
    setup_quality: str   = "D"
    confidence:    float = 0.0

    # Risk & Entry
    entry_zone:         float = 0.0
    stop_loss:          float = 0.0
    tp1:                float = 0.0
    tp2:                float = 0.0
    tp3:                float = 0.0
    rr:                 float = 0.0
    net_rr:             float = 0.0
    risk_percent:       float = 0.0
    risk_amount:        float = 0.0
    position_size:      float = 0.0
    notional_size:      float = 0.0
    leverage_suggestion:int   = 1
    max_loss:           float = 0.0
    estimated_fee:      float = 0.0
    estimated_slippage: float = 0.0
    invalidation_level: float = 0.0

    # Lifecycle
    lifecycle_stage:        str  = "SCANNED"
    status:                 str  = "pending"
    reason:                 str  = ""
    reject_reason:          str  = ""
    telegram_status:        str  = "pending"
    dashboard_status:       str  = "pending"
    error:                  str  = ""
    approved_for_watchlist: bool = False
    approved_for_telegram:  bool = False
    approved_for_trade:     bool = False
    linked_trade_id:        Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "id": self.id, "symbol": self.symbol, "timestamp": self.timestamp,
            "source": self.source, "timeframe": self.timeframe,
            "direction": self.direction,
            "coin_score": self.coin_score, "trend_score": self.trend_score,
            "trigger_score": self.trigger_score, "risk_score": self.risk_score,
            "ai_score": self.ai_score, "final_score": self.final_score,
            "setup_quality": self.setup_quality, "confidence": self.confidence,
            "entry_zone": self.entry_zone, "stop_loss": self.stop_loss,
            "tp1": self.tp1, "tp2": self.tp2, "tp3": self.tp3,
            "rr": self.rr, "net_rr": self.net_rr,
            "risk_percent": self.risk_percent, "risk_amount": self.risk_amount,
            "position_size": self.position_size, "notional_size": self.notional_size,
            "leverage_suggestion": self.leverage_suggestion,
            "max_loss": self.max_loss,
            "estimated_fee": self.estimated_fee,
            "estimated_slippage": self.estimated_slippage,
            "invalidation_level": self.invalidation_level,
            "lifecycle_stage": self.lifecycle_stage,
            "status": self.status, "reason": self.reason,
            "reject_reason": self.reject_reason,
            "telegram_status": self.telegram_status,
            "dashboard_status": self.dashboard_status,
            "error": self.error,
            "approved_for_watchlist": int(self.approved_for_watchlist),
            "approved_for_telegram":  int(self.approved_for_telegram),
            "approved_for_trade":     int(self.approved_for_trade),
            "linked_trade_id": self.linked_trade_id,
        }
        return d

    def is_valid(self) -> bool:
        if not self.symbol or not self.direction:
            return False
        if self.entry_zone <= 0 or self.stop_loss <= 0:
            return False
        if self.setup_quality in ("D", ""):
            return False
        return True

    def passes_data_threshold(self) -> bool:
        return self.final_score >= DATA_THRESHOLD

    def passes_watchlist_threshold(self) -> bool:
        return self.final_score >= WATCHLIST_THRESHOLD

    def passes_telegram_threshold(self) -> bool:
        return self.final_score >= TELEGRAM_THRESHOLD

    def passes_trade_threshold(self) -> bool:
        return self.final_score >= TRADE_THRESHOLD


class DataLayer:
    """
    Sinyallerin bellekte ve DB'de yönetildiği katman.

    Kullanım:
      sig = data_layer.create_signal("BTCUSDT")
      data_layer.update_stage(sig, "TREND_CHECKED", {"trend_score": 7.5})
      data_layer.persist(sig)    # DB'ye yaz
      data_layer.reject(sig, "bad_rr")
    """

    def __init__(self):
        self._active: Dict[str, SignalData] = {}

    # ── Signal oluşturma ──────────────────────────────────────────────────────

    def create_signal(self, symbol: str) -> SignalData:
        sig = SignalData(symbol=symbol)
        self._active[sig.id] = sig
        self._log_event(sig.id, "SCANNED")
        return sig

    def restore_from_db(self):
        """Bot restart sonrası açık sinyalleri DB'den geri yükle."""
        try:
            from database import get_candidate_signals
            rows = get_candidate_signals(limit=200, stage="OPENED")
            restored = 0
            for row in rows:
                sig = SignalData(id=row["id"])
                for k, v in row.items():
                    if hasattr(sig, k):
                        try:
                            setattr(sig, k, v)
                        except Exception:
                            pass
                self._active[sig.id] = sig
                restored += 1
            if restored:
                logger.info(f"DataLayer: {restored} aktif sinyal DB'den restore edildi.")
        except Exception as e:
            logger.error(f"DataLayer restore hatası: {e}")

    # ── Lifecycle yönetimi ────────────────────────────────────────────────────

    def update_stage(self, sig: SignalData, stage: str, updates: dict = None):
        """Lifecycle stage'ini güncelle ve event logla."""
        sig.lifecycle_stage = stage
        if updates:
            for k, v in updates.items():
                if hasattr(sig, k):
                    setattr(sig, k, v)
        self._log_event(sig.id, stage, updates)

    def reject(self, sig: SignalData, reason: str, detail: str = None):
        """Sinyali reddet, lifecycle'ı güncelle, DB'ye persist et."""
        sig.lifecycle_stage = "REJECTED"
        sig.reject_reason   = reason
        sig.status          = "rejected"
        self._log_event(sig.id, "REJECTED", {"reject_reason": reason, "detail": detail})
        self.persist(sig)
        self._active.pop(sig.id, None)

    def approve_watchlist(self, sig: SignalData):
        sig.approved_for_watchlist = True
        self.update_stage(sig, "APPROVED_FOR_WATCHLIST")
        self.persist(sig)

    def approve_telegram(self, sig: SignalData):
        sig.approved_for_telegram = True
        self.update_stage(sig, "APPROVED_FOR_TELEGRAM")
        self.persist(sig)

    def approve_trade(self, sig: SignalData):
        sig.approved_for_trade = True
        self.update_stage(sig, "APPROVED_FOR_TRADE")
        self.persist(sig)

    def mark_opened(self, sig: SignalData, trade_id: int):
        sig.linked_trade_id = trade_id
        self.update_stage(sig, "OPENED")
        self.persist(sig)

    def mark_closed(self, sig_id: str):
        self.update_lifecycle_db(sig_id, "CLOSED")
        self._active.pop(sig_id, None)

    def mark_error(self, sig: SignalData, error: str):
        sig.error = error
        self.update_stage(sig, "ERROR")
        self.persist(sig)
        self._active.pop(sig.id, None)

    # ── Eşik kontrolü + otomatik approval ────────────────────────────────────

    def evaluate_thresholds(self, sig: SignalData):
        """final_score'a göre approval flag'lerini set et."""
        if sig.passes_watchlist_threshold():
            sig.approved_for_watchlist = True
        if sig.passes_telegram_threshold():
            sig.approved_for_telegram = True
        if sig.passes_trade_threshold():
            sig.approved_for_trade = True

    # ── DB Operasyonları ──────────────────────────────────────────────────────

    def persist(self, sig: SignalData):
        """Sinyali DB'ye yaz/güncelle."""
        if not sig.passes_data_threshold() and sig.lifecycle_stage != "REJECTED":
            return  # DATA_THRESHOLD altı hiç kaydedilmez
        try:
            from database import save_scalp_signal
            save_scalp_signal(sig.to_dict())
        except Exception as e:
            logger.error(f"Signal persist hatası {sig.id}: {e}")

    def update_lifecycle_db(self, signal_id: str, stage: str, fields: dict = None):
        """Sadece DB'deki lifecycle'ı güncelle."""
        try:
            from database import update_signal_lifecycle
            update_signal_lifecycle(signal_id, stage, fields)
        except Exception as e:
            logger.error(f"DB lifecycle güncelleme hatası: {e}")

    def _log_event(self, signal_id: str, stage: str, data: dict = None):
        try:
            from database import log_signal_event
            detail = None
            if data:
                import json
                detail = json.dumps({k: str(v) for k, v in data.items()
                                     if v is not None}, ensure_ascii=False)
            log_signal_event(signal_id, stage, detail)
        except Exception as e:
            logger.debug(f"Signal event log hatası: {e}")

    # ── Query ─────────────────────────────────────────────────────────────────

    def get_signal(self, sig_id: str) -> Optional[SignalData]:
        return self._active.get(sig_id)

    def get_active_signals(self) -> List[SignalData]:
        return list(self._active.values())

    def get_valid_for_dashboard(self) -> list:
        return [s.to_dict() for s in self._active.values()
                if s.is_valid() and s.approved_for_watchlist]

    def get_pending_telegram(self) -> List[SignalData]:
        return [s for s in self._active.values()
                if s.approved_for_telegram and s.telegram_status == "pending"]

    def get_pending_trade(self) -> List[SignalData]:
        return [s for s in self._active.values()
                if s.approved_for_trade
                and s.lifecycle_stage not in ("OPENED", "CLOSED", "ERROR", "REJECTED")]

    def archive_signal(self, sig_id: str):
        self._active.pop(sig_id, None)


# Singleton
data_layer = DataLayer()
