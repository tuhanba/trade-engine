"""
Data Layer — Tek Schema
Tüm modüller bu schema ile çalışır.
"""
import time
import uuid
from dataclasses import dataclass, asdict, field
from typing import Optional, Dict, Any

@dataclass
class SignalData:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    symbol: str = ""
    timestamp: float = field(default_factory=time.time)
    source: str = "system"
    timeframe: str = "1m"
    direction: Optional[str] = None
    
    # Scores
    coin_score: float = 0.0
    trend_score: float = 0.0
    trigger_score: float = 0.0
    risk_score: float = 0.0
    technical_score: Optional[float] = None
    ai_score: Optional[float] = None
    ml_score: Optional[float] = None
    cold_start_score: Optional[float] = None
    final_score: Optional[float] = None
    setup_quality: str = "D"
    score_source: str = ""
    score_confidence: Optional[float] = None
    
    # Risk & Entry
    entry_zone: float = 0.0
    stop_loss: float = 0.0
    tp1: float = 0.0
    tp2: float = 0.0
    tp3: float = 0.0
    rr: float = 0.0
    risk_percent: float = 0.0
    position_size: float = 0.0
    notional_size: float = 0.0
    leverage_suggestion: int = 1
    max_loss: float = 0.0
    invalidation_level: float = 0.0
    
    # Status
    confidence: float = 0.0
    status: str = "pending"
    reason: str = ""
    telegram_status: str = "pending"
    dashboard_status: str = "pending"
    error: str = ""
    lifecycle_stage: str = "SCANNED"
    candidate_id: str = ""
    reject_reason: str = ""
    ai_veto_reason: str = ""
    risk_reject_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def is_valid(self) -> bool:
        """Null veya eksik veri kontrolü"""
        if not self.symbol or not self.direction:
            return False
        if self.entry_zone <= 0 or self.stop_loss <= 0:
            return False
        if self.setup_quality in ["D", ""]:
            return False
        return True

class DataLayer:
    def __init__(self):
        self.active_signals = {}
        self.history = []

    def create_signal(self, symbol: str) -> SignalData:
        sig = SignalData(symbol=symbol)
        self.active_signals[sig.id] = sig
        return sig

    def update_signal(self, sig_id: str, updates: dict):
        if sig_id in self.active_signals:
            sig = self.active_signals[sig_id]
            for k, v in updates.items():
                if hasattr(sig, k):
                    setattr(sig, k, v)

    def get_signal(self, sig_id: str) -> Optional[SignalData]:
        return self.active_signals.get(sig_id)

    def archive_signal(self, sig_id: str):
        if sig_id in self.active_signals:
            sig = self.active_signals.pop(sig_id)
            self.history.append(sig)

    def get_valid_signals_for_dashboard(self):
        return [s.to_dict() for s in self.active_signals.values() if s.is_valid()]

    def get_valid_signals_for_telegram(self):
        return [s for s in self.active_signals.values() if s.is_valid() and s.setup_quality in ["S", "A+", "A", "B"] and s.telegram_status == "pending"]

data_layer = DataLayer()


def calculate_duration(open_time_str) -> tuple:
    """
    open_time string'inden geçen süreyi hesaplar.
    Returns: (duration_seconds: int, duration_str: str)
    """
    from datetime import datetime, timezone
    if not open_time_str:
        return 0, "0s"
    try:
        opened = datetime.fromisoformat(
            str(open_time_str).replace("Z", "+00:00")
        )
        if opened.tzinfo is None:
            opened = opened.replace(tzinfo=timezone.utc)
        secs = int((datetime.now(timezone.utc) - opened).total_seconds())
        secs = max(0, secs)
        if secs < 60:
            return secs, f"{secs}s"
        elif secs < 3600:
            dk = secs // 60
            ys = secs % 60
            return secs, f"{dk}dk {ys}s"
        else:
            sa = secs // 3600
            dk = (secs % 3600) // 60
            return secs, f"{sa}sa {dk}dk"
    except Exception:
        return 0, "?"
