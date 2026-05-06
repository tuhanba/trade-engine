"""
Data Layer — Tek Schema v4.5
Tüm modüller bu schema ile çalışır. Aşama 5: Zaman ve Süre eklendi.
"""
import time
import uuid
from dataclasses import dataclass, asdict, field
from typing import Optional, Dict, Any
from datetime import datetime

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
    ml_score: int = 0
    final_score: float = 0.0
    setup_quality: str = "D"
    
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

def calculate_duration(open_time_str, close_time_str=None):
    """
    open_time ve close_time arasındaki süreyi saniye ve formatlı string olarak döner.
    ISO format ve basit format destekler.
    """
    def _parse_ts(s):
        if not s:
            return datetime.utcnow()
        # ISO format dene (T separator, timezone)
        try:
            dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
            return dt.replace(tzinfo=None)  # naive'e çevir
        except Exception:
            pass
        # Basit format dene
        try:
            return datetime.strptime(str(s), "%Y-%m-%d %H:%M:%S")
        except Exception:
            return datetime.utcnow()

    try:
        start = _parse_ts(open_time_str)
        end = _parse_ts(close_time_str) if close_time_str else datetime.utcnow()

        duration_seconds = max(0, int((end - start).total_seconds()))

        if duration_seconds >= 3600:
            hours = duration_seconds / 3600
            duration_str = f"{hours:.1f}sa"
        elif duration_seconds >= 60:
            minutes = duration_seconds // 60
            seconds = duration_seconds % 60
            duration_str = f"{minutes}dk {seconds}s"
        else:
            duration_str = f"{duration_seconds}s"

        return duration_seconds, duration_str
    except Exception:
        return 0, "0s"

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
