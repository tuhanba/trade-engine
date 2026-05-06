"""
core/accounting.py — AX Merkezi Muhasebe Modülü
===============================================
Tüm PnL, Fee, Margin ve Leverage hesaplamaları burada yapılır.
"""

def calculate_pnl(direction, entry_price, current_price, qty, leverage=1):
    """
    Net PnL hesaplar. Leverage PnL'e ikinci kez çarpılmaz.
    LONG PnL = (price - entry) * qty
    SHORT PnL = (entry - price) * qty
    """
    if direction.upper() == "LONG":
        raw_pnl = (current_price - entry_price) * qty
    else:
        raw_pnl = (entry_price - current_price) * qty
    
    return raw_pnl

def calculate_notional_and_margin(entry_price, qty, leverage):
    """
    notional_size = qty * entry
    margin_used = notional_size / leverage
    """
    notional_size = qty * entry_price
    margin_used = notional_size / leverage
    return notional_size, margin_used

def calculate_fee(notional_size, fee_rate=0.0004):
    """
    Binance fee (default 0.04% for futures taker).
    Hem giriş hem çıkış için hesaplanmalıdır.
    """
    return notional_size * fee_rate

def get_net_pnl(direction, entry_price, exit_price, qty, leverage=1, fee_rate=0.0004):
    """
    Tüm masraflar düşüldükten sonraki net kar/zarar.
    """
    raw_pnl = calculate_pnl(direction, entry_price, exit_price, qty, leverage)
    entry_notional = entry_price * qty
    exit_notional = exit_price * qty
    
    total_fee = calculate_fee(entry_notional, fee_rate) + calculate_fee(exit_notional, fee_rate)
    return raw_pnl - total_fee, total_fee
