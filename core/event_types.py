from enum import Enum
from dataclasses import dataclass, field
from typing import Any, Dict, Optional
import time
import uuid

class EventType(Enum):
    SCANNED = "SCANNED"
    TREND_CHECKED = "TREND_CHECKED"
    TRIGGER_CHECKED = "TRIGGER_CHECKED"
    SIGNAL_CREATED = "SIGNAL_CREATED"
    AI_VALIDATED = "AI_VALIDATED"
    RISK_APPROVED = "RISK_APPROVED"
    EXECUTION_APPROVED = "EXECUTION_APPROVED"
    TRADE_OPENED = "TRADE_OPENED"
    POSITION_MANAGED = "POSITION_MANAGED"
    TP_OR_SL_TRIGGERED = "TP_OR_SL_TRIGGERED"
    TRADE_CLOSED = "TRADE_CLOSED"
    JOURNALED = "JOURNALED"
    LEARNING_UPDATED = "LEARNING_UPDATED"
    SYSTEM_SHUTDOWN = "SYSTEM_SHUTDOWN"

@dataclass
class Event:
    type: EventType
    payload: Dict[str, Any]
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)

@dataclass
class TradeLifecycleState:
    """Helper to track state across events."""
    symbol: str
    signal_id: Optional[int] = None
    direction: Optional[str] = None
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    tp1: Optional[float] = None
    tp2: Optional[float] = None
    tp3: Optional[float] = None
    score: float = 0.0
    quality: str = "C"
    reason: str = ""
    trade_id: Optional[int] = None
