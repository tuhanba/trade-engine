"""
Advanced Risk Engine v3.0
Korelasyon Koruması ve Dinamik Pozisyon Yönetimi.
"""
import logging
from .risk_engine import RiskEngine

logger = logging.getLogger(__name__)

class AdvancedRiskEngine(RiskEngine):
    def __init__(self, client, db_path="trade_engine.db"):
        super().__init__(client)
        self.db_path = db_path
        self.max_sector_exposure = 3 # Aynı sektör/tipte max 3 trade

    def check_correlation(self, symbol: str, open_trades: list) -> bool:
        """Aynı yönde çok fazla korele trade olup olmadığını kontrol eder."""
        # Basit korelasyon: Aynı sembol zaten açıksa engelle (scalp_bot zaten yapıyor ama burada da olsun)
        if any(t['symbol'] == symbol for t in open_trades):
            return False
            
        # Gelişmiş: Sektörel korelasyon (örneğin tüm AI coinleri veya tüm Layer 1'ler)
        # Şimdilik sadece toplam açık işlem sayısına ve yönüne bakıyoruz
        longs = sum(1 for t in open_trades if t.get('direction') == 'LONG')
        shorts = sum(1 for t in open_trades if t.get('direction') == 'SHORT')
        
        if longs >= 5 or shorts >= 5: # Tek yönde aşırı yığılma
            logger.warning(f"Korelasyon uyarısı: Tek yönde çok fazla işlem var (L:{longs} S:{shorts})")
            return False
            
        return True

    def calculate_dynamic_risk(self, balance: float, win_rate: float, current_drawdown: float) -> float:
        """Drawdown'a göre riski dinamik olarak azaltır."""
        base_risk = 1.0 # %1
        
        # Drawdown %5'i geçerse riski yarıya indir
        if current_drawdown > 5.0:
            base_risk *= 0.5
        # Drawdown %10'u geçerse riski %0.25'e indir
        if current_drawdown > 10.0:
            base_risk *= 0.5
            
        return base_risk

    def calculate(self, symbol: str, direction: str, entry: float, quality: str, balance: float, open_trades: list = None) -> dict:
        # Önce korelasyon kontrolü
        if open_trades is not None:
            if not self.check_correlation(symbol, open_trades):
                return {"valid": False, "score": 0, "risk_reject_reason": "high_correlation"}

        # Temel hesaplama
        res = super().calculate(symbol, direction, entry, quality, balance)
        
        # Ekstra: Volatiliteye göre kaldıraç ayarı
        if res.get("valid"):
            atr_pct = res.get("stop_distance_percent", 0)
            if atr_pct > 2.0: # Yüksek volatilite
                res["leverage"] = max(1, int(res["leverage"] * 0.7)) # Kaldıracı %30 düşür
                res["risk_reject_reason"] = res.get("risk_reject_reason", "") + " | high_vol_leverage_adj"
                
        return res
