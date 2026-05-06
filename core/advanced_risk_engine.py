"""
core/advanced_risk_engine.py — AX Gelişmiş Risk Yönetimi v4.3
========================================================
Aşama 3: Sıkı Risk Kontrolleri ve Güvenlik Duvarı.
"""
import logging
from core.accounting import calculate_notional_and_margin, calculate_fee

logger = logging.getLogger(__name__)

class AdvancedRiskEngine:
    def __init__(self, client=None, db_path="trading.db"):
        self.client = client
        self.db_path = db_path
        self.LIVE_TRADING_ENABLED = False # Default False
        self.DRY_RUN = True # Default True

    def check_trade_safety(self, balance, entry, sl, leverage, risk_pct):
        """
        Trade açmadan önceki zorunlu kontroller.
        """
        # 1. Risk USD Hesabı
        risk_usd = balance * (risk_pct / 100.0)
        
        # 2. Stop Mesafesi ve Marjin Kaybı
        stop_dist_pct = abs(entry - sl) / entry
        margin_loss_pct = stop_dist_pct * leverage
        
        # Kural: x20 + %5 stop = %100 margin kaybı. 
        # %40'tan fazla marjin kaybı riski varsa trade açma.
        if margin_loss_pct > 0.40:
            return False, f"Yüksek Marjin Kaybı Riski: %{margin_loss_pct*100:.1f}"

        # 3. Fee Dahil Max Kayıp Kontrolü
        qty = risk_usd / (entry * stop_dist_pct)
        notional, margin = calculate_notional_and_margin(entry, qty, leverage)
        total_fee = calculate_fee(notional) * 2 # Giriş + Çıkış tahmini
        
        max_loss_after_fee = risk_usd + total_fee
        if max_loss_after_fee > (risk_usd * 1.2): # Fee riskin %20'sinden fazlaysa uyarı/red
             return False, f"Yüksek Komisyon Maliyeti: {total_fee:.2f} USD"

        return True, "Güvenli"

    def calculate(self, symbol: str, direction: str, entry: float, quality: str, balance: float, open_trades: list = None, atr_pct: float = None) -> dict:
        # Kaliteye göre risk yüzdesi
        risk_pct = 1.0
        if quality == "S": risk_pct = 2.0
        elif quality == "A+": risk_pct = 1.5
        
        # Dinamik ATR tabanlı stop
        if atr_pct and atr_pct > 0.005:
            # Min %1, Max %5 ATR sınırı
            stop_dist_pct = min(0.05, max(0.01, atr_pct * 1.5))
        else:
            stop_dist_pct = 0.02 
            
        sl = entry * (1 - stop_dist_pct) if direction == "LONG" else entry * (1 + stop_dist_pct)
        
        leverage = 10
        
        # Güvenlik Kontrolü
        is_safe, reason = self.check_trade_safety(balance, entry, sl, leverage, risk_pct)
        if not is_safe:
            return {"valid": False, "reason": reason}
        
        # Pozisyon büyüklüğü
        risk_usd = balance * (risk_pct / 100.0)
        qty = risk_usd / (entry * stop_dist_pct)
        notional, margin = calculate_notional_and_margin(entry, qty, leverage)
        
        return {
            "valid": True,
            "symbol": symbol,
            "direction": direction,
            "entry": entry,
            "sl": sl,
            "tp1": entry * (1 + stop_dist_pct * 1.5) if direction == "LONG" else entry * (1 - stop_dist_pct * 1.5),
            "tp2": entry * (1 + stop_dist_pct * 2.5) if direction == "LONG" else entry * (1 - stop_dist_pct * 2.5),
            "risk_pct": risk_pct,
            "risk_usd": risk_usd,
            "position_size": qty,
            "notional": notional,
            "margin_used": margin,
            "leverage": leverage,
            "margin_loss_pct": (abs(entry - sl) / entry) * leverage,
            "max_loss_after_fee": risk_usd + (calculate_fee(notional) * 2)
        }
