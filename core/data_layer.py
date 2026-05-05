"""
Data Layer — AX Scalp Bot v5.0 FINAL
Tüm modüller bu schema ile çalışır.
"""
import time
import uuid
import requests
from dataclasses import dataclass, asdict, field
from typing import Optional, Dict, Any
import logging
logger = logging.getLogger(__name__)

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
    leverage_suggestion: int = 10
    leverage: int = 10
    max_loss: float = 0.0
    invalidation_level: float = 0.0
    confidence: float = 0.0

    # Status
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
        if self.entry_zone <= 0:
            return False
        if self.setup_quality in ["D", ""]:
            return False
        return True


def _get_live_price(symbol: str) -> float:
    """Binance Futures REST ile anlık fiyat çek (API key gerektirmez)."""
    try:
        r = requests.get(
            "https://fapi.binance.com/fapi/v1/ticker/price",
            params={"symbol": symbol},
            timeout=5
        )
        if r.status_code == 200:
            p = float(r.json().get("price", 0))
            if p > 0:
                return p
    except Exception:
        pass
    return 0.0


class DataLayer:
    def __init__(self, client=None):
        self.client = client
        self.active_signals: Dict[str, SignalData] = {}
        self.history = []

    def create_signal(self, symbol: str) -> SignalData:
        """Yeni sinyal oluştur ve kaydet."""
        sig = SignalData(symbol=symbol)
        self.active_signals[sig.id] = sig
        return sig

    def get_signal(self, symbol_or_id: str) -> Optional[SignalData]:
        """
        Sembol veya UUID ile sinyal döndür.
        Eğer sembol verilmişse, o sembol için yeni bir SignalData oluştur
        ve Binance'ten anlık fiyatı çek.
        """
        # Önce UUID ile ara
        if symbol_or_id in self.active_signals:
            return self.active_signals[symbol_or_id]

        # Sembol ile ara (en son oluşturulan)
        for sig in reversed(list(self.active_signals.values())):
            if sig.symbol == symbol_or_id:
                return sig

        # Yoksa yeni oluştur ve fiyat çek
        return self.get_signal_for_symbol(symbol_or_id)

    def get_signal_for_symbol(self, symbol: str) -> Optional[SignalData]:
        """
        Sembol için SignalData oluştur.
        Binance Futures REST ile anlık fiyatı çeker.
        """
        try:
            price = _get_live_price(symbol)
            if price <= 0:
                # Client varsa fallback
                if self.client:
                    try:
                        t = self.client.futures_ticker(symbol=symbol)
                        price = float(t.get("lastPrice", 0))
                    except Exception:
                        pass
            if price <= 0:
                return None

            sig = SignalData(
                symbol=symbol,
                entry_zone=price,
                setup_quality="B",  # Trigger engine override edecek
                status="pending",
            )
            self.active_signals[sig.id] = sig
            return sig
        except Exception as e:
            logger.error(f"[DataLayer] {symbol} sinyal olusturulamadi: {e}")
            return None

    def update_signal(self, sig_id: str, updates: dict):
        if sig_id in self.active_signals:
            sig = self.active_signals[sig_id]
            for k, v in updates.items():
                if hasattr(sig, k):
                    setattr(sig, k, v)

    def archive_signal(self, sig_id: str):
        if sig_id in self.active_signals:
            sig = self.active_signals.pop(sig_id)
            self.history.append(sig)

    def get_valid_signals_for_dashboard(self):
        return [s.to_dict() for s in self.active_signals.values() if s.is_valid()]

    def get_valid_signals_for_telegram(self):
        return [
            s for s in self.active_signals.values()
            if s.is_valid()
            and s.setup_quality in ["S", "A+", "A", "B"]
            and s.telegram_status == "pending"
        ]


data_layer = DataLayer()
