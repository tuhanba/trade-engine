"""
core/data_layer.py – Sinyal ve trade veri modelleri.

Dataclass tabanlı modeller + legacy alan normalleştirme fonksiyonları.
Tüm modüller SignalData / TradeData üzerinden konuşur.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, Optional


# ── Yardımcı fonksiyonlar ──────────────────────────────────────────

def now_iso() -> str:
    """UTC zaman damgasını ISO formatında döner."""
    return datetime.now(timezone.utc).isoformat()


def normalize_side(side: str) -> str:
    """Side değerini LONG/SHORT olarak standardize eder."""
    s = str(side).strip().upper()
    if s in ("LONG", "BUY", "L"):
        return "LONG"
    if s in ("SHORT", "SELL", "S"):
        return "SHORT"
    return "LONG"


# ── Enum tanımları ──────────────────────────────────────────────────

class TradeStatus(enum.Enum):
    """Trade yaşam döngüsü durumları."""
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


class SignalDecision(enum.Enum):
    """Sinyal karar seçenekleri."""
    ALLOW = "ALLOW"
    WATCH = "WATCH"
    VETO = "VETO"
    SKIPPED_BY_RISK = "SKIPPED_BY_RISK"
    SKIPPED_BY_FILTER = "SKIPPED_BY_FILTER"


# ── SignalData ──────────────────────────────────────────────────────

@dataclass
class SignalData:
    """Ham sinyal verisi — tüm sinyal sistemi alanları dahil."""
    symbol: str = ""
    side: str = "LONG"
    entry_price: float = 0.0
    stop_loss: float = 0.0
    tp1: Optional[float] = None
    tp2: Optional[float] = None
    tp3: Optional[float] = None
    score: float = 0.0
    leverage: int = 1
    risk_pct: float = 1.0
    reason: str = ""
    source: str = ""
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    metadata: Optional[dict[str, Any]] = None
    # ── Genişletilmiş alanlar (signal_engine / format_signal tarafından kullanılır)
    direction: str = ""               # 'LONG' / 'SHORT' — side alias
    entry_zone: Optional[float] = None  # entry_price alias (eski compat)
    setup_quality: str = ""           # 'S' / 'A+' / 'A' / 'B' / 'C'
    final_score: float = 0.0
    rr: Optional[float] = None        # Risk/Reward oranı
    confidence: float = 0.0
    risk_percent: float = 0.0
    notional_size: float = 0.0
    leverage_suggestion: Optional[int] = None
    max_loss: float = 0.0
    position_size: float = 0.0
    market_regime: str = ""
    atr: float = 0.0
    telegram_status: str = ""
    # ── ID alanı (deliver_signal dedupe için)
    id: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict) -> "SignalData":
        """_SignalRecord.to_dict() çıktısından SignalData oluşturur."""
        sig = cls()
        sig.symbol        = d.get("symbol", "")
        sig.direction     = d.get("direction", "LONG")
        sig.side          = d.get("direction", "LONG")
        sig.entry_price   = float(d.get("entry_zone") or d.get("entry_price") or 0)
        sig.entry_zone    = sig.entry_price
        sig.stop_loss     = float(d.get("stop_loss") or 0)
        sig.tp1           = float(d.get("tp1") or 0) or None
        sig.tp2           = float(d.get("tp2") or 0) or None
        sig.tp3           = float(d.get("tp3") or 0) or None
        sig.final_score   = float(d.get("final_score") or 0)
        sig.score         = sig.final_score
        sig.setup_quality = d.get("setup_quality", "C")
        sig.leverage      = int(d.get("leverage_suggestion") or d.get("leverage") or 10)
        sig.risk_pct      = float(d.get("risk_percent") or d.get("risk_pct") or 0.75)
        sig.position_size = float(d.get("position_size") or 0)
        sig.confidence    = float(d.get("confidence") or 0.5)
        sig.reason        = d.get("reason", "")
        sig.rr            = float(d.get("rr") or 0) or None
        sig.ml_score      = float(d.get("ml_score") or 50)
        sig.trend_score   = float(d.get("trend_score") or 0)
        sig.trigger_score = float(d.get("trigger_score") or 0)
        sig.risk_score    = float(d.get("risk_score") or 0)
        sig.confluence_score = float(d.get("confluence_score") or 2)
        sig.notional_size = float(d.get("notional_size") or 0)
        sig.leverage_suggestion = d.get("leverage_suggestion")
        sig.max_loss      = float(d.get("max_loss") or 0)
        sig.market_regime = d.get("market_regime", "NEUTRAL")
        return sig

    def __post_init__(self):
        """direction ↔ side senkronizasyonu, entry_zone ↔ entry_price senkronizasyonu."""
        if self.direction and not self.side:
            self.side = self.direction
        elif self.side and not self.direction:
            self.direction = self.side
        if self.entry_zone is None and self.entry_price:
            self.entry_zone = self.entry_price
        elif self.entry_zone and not self.entry_price:
            self.entry_price = self.entry_zone
        if self.leverage_suggestion is None:
            self.leverage_suggestion = self.leverage
        if self.id is None:
            self.id = str(uuid.uuid4())

    def is_valid(self) -> bool:
        """Temel alan geçerlilik kontrolü."""
        _entry = self.entry_zone or self.entry_price
        return bool(self.symbol) and bool(_entry)


# ── TradeData ───────────────────────────────────────────────────────

@dataclass
class TradeData:
    """Trade kaydı – DB satırıyla 1:1 eşlenir."""
    id: Optional[int] = None
    symbol: str = ""
    side: str = "LONG"
    entry_price: float = 0.0
    stop_loss: float = 0.0
    tp1: Optional[float] = None
    tp2: Optional[float] = None
    tp3: Optional[float] = None
    quantity: float = 0.0
    qty_tp1: float = 0.0
    qty_tp2: float = 0.0
    qty_runner: float = 0.0
    leverage: int = 1
    notional: float = 0.0
    margin_used: float = 0.0
    risk_usd: float = 0.0
    risk_pct: float = 0.0
    status: str = TradeStatus.OPEN.value
    opened_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    closed_at: Optional[str] = None
    current_price: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    exit_price: float = 0.0
    close_reason: str = ""
    metadata: Optional[dict[str, Any]] = None


# ── Serialization helpers ──────────────────────────────────────────

def signal_to_dict(signal: SignalData) -> dict:
    """SignalData'yı dict'e dönüştürür."""
    d = asdict(signal)
    if d.get("metadata") is None:
        d["metadata"] = {}
    return d


def trade_to_dict(trade: TradeData) -> dict:
    """TradeData'yı dict'e dönüştürür."""
    d = asdict(trade)
    if d.get("metadata") is None:
        d["metadata"] = {}
    return d


# ── Legacy normalleştirme ───────────────────────────────────────────

def normalize_signal(raw: dict) -> SignalData:
    """
    Eski formattaki sinyal dict'ini SignalData'ya dönüştürür.

    Desteklenen legacy alan isimleri:
      entry / price       → entry_price
      sl / stop / stoploss→ stop_loss
      target / targets[0] → tp1
      targets[1]          → tp2
      targets[2]          → tp3
      leverage_suggestion → leverage
      direction           → side
    """
    targets = raw.get("targets", [])

    entry_price = (
        raw.get("entry_price")
        or raw.get("entry")
        or raw.get("price", 0.0)
    )
    stop_loss = (
        raw.get("stop_loss")
        or raw.get("sl")
        or raw.get("stop")
        or raw.get("stoploss", 0.0)
    )

    tp1 = (
        raw.get("tp1")
        or raw.get("target")
        or (targets[0] if len(targets) > 0 else None)
    )
    tp2 = raw.get("tp2") or (targets[1] if len(targets) > 1 else None)
    tp3 = raw.get("tp3") or (targets[2] if len(targets) > 2 else None)

    leverage = (
        raw.get("leverage")
        or raw.get("leverage_suggestion", 1)
    )

    side_raw = raw.get("side") or raw.get("direction", "LONG")

    return SignalData(
        symbol=raw.get("symbol", ""),
        side=normalize_side(side_raw),
        entry_price=float(entry_price or 0),
        stop_loss=float(stop_loss or 0),
        tp1=float(tp1) if tp1 is not None else None,
        tp2=float(tp2) if tp2 is not None else None,
        tp3=float(tp3) if tp3 is not None else None,
        score=float(raw.get("score", 0.0)),
        leverage=int(leverage),
        risk_pct=float(raw.get("risk_pct", 1.0)),
        reason=raw.get("reason", ""),
        source=raw.get("source", ""),
        created_at=raw.get(
            "created_at",
            datetime.now(timezone.utc).isoformat(),
        ),
        metadata=raw.get("metadata"),
    )


class _SignalRecord:
    """
    Sinyal pipeline boyunca taşınan mutable kayıt.
    DataLayer.create_signal() tarafından oluşturulur.
    """
    def __init__(self, symbol: str):
        self.id: str = str(uuid.uuid4())
        self.symbol: str = symbol
        self.direction: str = ""
        self.coin_score: float = 0.0
        self.trend_score: float = 0.0
        self.trigger_score: float = 0.0
        self.risk_score: float = 0.0
        self.setup_quality: str = ""
        self.ml_score: float = 50.0
        self.confluence_score: float = 0.0
        self.entry_zone: float = 0.0
        self.stop_loss: float = 0.0
        self.tp1: Optional[float] = None
        self.tp2: Optional[float] = None
        self.tp3: Optional[float] = None
        self.rr: float = 0.0
        self.risk_percent: float = 0.0
        self.position_size: float = 0.0
        self.notional_size: float = 0.0
        self.leverage_suggestion: int = 10
        self.max_loss: float = 0.0
        self.status: str = "pending"
        self.dashboard_status: str = ""
        self.final_score: float = 0.0
        self.confidence: float = 0.0
        self.reason: str = ""
        self.candidate_id: Optional[int] = None
        self.reject_reason: str = ""
        self.telegram_status: str = ""
        self.ai_veto_reason: str = ""
        self.created_at: str = datetime.now(timezone.utc).isoformat()

    @property
    def side(self) -> str:
        """AI engine uyumluluğu — direction alanından oku."""
        return getattr(self, 'direction', 'LONG') or 'LONG'

    @side.setter
    def side(self, val: str):
        self.direction = val

    @property
    def risk_pct(self) -> float:
        """AI engine ve risk engine uyumluluğu — risk_percent alanından oku."""
        return self.risk_percent

    @risk_pct.setter
    def risk_pct(self, val: float):
        self.risk_percent = val

    def is_valid(self) -> bool:
        return bool(
            self.symbol
            and self.direction
            and self.entry_zone > 0
            and self.stop_loss > 0
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "symbol": self.symbol,
            "direction": self.direction,
            "entry_zone": self.entry_zone,
            "stop_loss": self.stop_loss,
            "tp1": self.tp1,
            "tp2": self.tp2,
            "tp3": self.tp3,
            "rr": self.rr,
            "setup_quality": self.setup_quality,
            "final_score": self.final_score,
            "confidence": self.confidence,
            "reason": self.reason,
            "risk_percent": self.risk_percent,
            "position_size": self.position_size,
            "notional_size": self.notional_size,
            "leverage_suggestion": self.leverage_suggestion,
            "max_loss": self.max_loss,
            "coin_score": self.coin_score,
            "trend_score": self.trend_score,
            "trigger_score": self.trigger_score,
            "risk_score": self.risk_score,
            "ml_score": self.ml_score,
            "confluence_score": self.confluence_score,
            "status": self.status,
            "telegram_status": self.telegram_status,
            "reject_reason": self.reject_reason,
            "created_at": self.created_at,
        }


class DataLayer:
    """
    Pipeline boyunca sinyal nesnelerini bellekte tutar.
    Thread-safe değil — tek thread (ana bot döngüsü) kullanır.
    """

    def __init__(self):
        self._signals: Dict[str, _SignalRecord] = {}

    def create_signal(self, symbol: str) -> _SignalRecord:
        record = _SignalRecord(symbol)
        self._signals[record.id] = record
        if len(self._signals) > 500:
            oldest = next(iter(self._signals))
            del self._signals[oldest]
        return record

    def get_signal(self, signal_id: str) -> Optional[_SignalRecord]:
        return self._signals.get(signal_id)


data_layer = DataLayer()


def calculate_duration(open_time_str) -> tuple:
    """
    open_time string'inden geçen süreyi hesaplar.
    Returns: (duration_seconds: int, duration_str: str)
    """
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
