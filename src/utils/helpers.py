# src/utils/helpers.py
import uuid
from datetime import datetime
def generate_id() -> str:
    """Benzersiz ID üretir."""
    return str(uuid.uuid4())
def timestamp_now() -> str:
    """ISO formatında şu anki zamanı döner."""
    return datetime.utcnow().isoformat()
def calculate_pnl(entry_price: float, exit_price: float, quantity: float, side: str) -> float:
    """
    Gerçekleşmiş kâr/zarar hesaplar.
    side: 'BUY' veya 'SELL'
    """
    if side == "BUY":
        return (exit_price - entry_price) * quantity
    elif side == "SELL":
        return (entry_price - exit_price) * quantity
    return 0.0
def calculate_percentage_change(old_value: float, new_value: float) -> float:
    """Yüzde değişimi hesaplar."""
    if old_value == 0:
        return 0.0
    return ((new_value - old_value) / old_value) * 100
