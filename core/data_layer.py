"""
core/data_layer.py – Sinyal ve trade veri modelleri.

Dataclass tabanlı modeller + legacy alan normalleştirme fonksiyonları.
Tüm modüller SignalData / TradeData üzerinden konuşur.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional


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
    """Ham sinyal verisi."""
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


<<<<<<< HEAD
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
=======
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
>>>>>>> 0797c70b8640d2006e47a50d5580ffae4606199b
