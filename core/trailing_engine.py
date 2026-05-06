"""
core/trailing_engine.py — AX Trailing Engine v4.11
==================================================
Aşama 11: Multi-TP & Trailing Stop Mantığı.
"""
import logging

logger = logging.getLogger(__name__)

class TrailingEngine:
    @staticmethod
    def calculate_trailing_stop(current_price, direction, entry_price, current_sl, activation_price, callback_rate=0.01):
        """
        Trailing stop seviyesini hesaplar.
        """
        if direction == 'LONG':
            if current_price >= activation_price:
                new_sl = current_price * (1 - callback_rate)
                return max(current_sl, new_sl)
        else: # SHORT
            if current_price <= activation_price:
                new_sl = current_price * (1 + callback_rate)
                return min(current_sl, new_sl)
        return current_sl

    @staticmethod
    def check_tp_hit(current_price, direction, tp_price):
        """
        TP seviyesinin vurulup vurulmadığını kontrol eder.
        """
        if direction == 'LONG':
            return current_price >= tp_price
        else:
            return current_price <= tp_price
