"""
Advanced Trend Engine v3.0
Mean Reversion, Volume Profile ve Çoklu Zaman Dilimi Analizi.
"""
import pandas as pd
import numpy as np
import logging
from .trend_engine import TrendEngine

logger = logging.getLogger(__name__)

class AdvancedTrendEngine(TrendEngine):
    def __init__(self, client):
        super().__init__(client)

    def _calculate_rsi(self, series, period=14):
        delta = series.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / (loss + 1e-10)
        return 100 - (100 / (1 + rs))

    def analyze_mean_reversion(self, df: pd.DataFrame) -> dict:
        """Fiyatın ortalamadan sapmasını (Mean Reversion) analiz eder."""
        close = df["close"]
        ema200 = self._ema(close, 200)
        rsi = self._calculate_rsi(close)
        
        # Z-Score (Fiyatın 20 periyotluk ortalamadan kaç standart sapma uzakta olduğu)
        sma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        z_score = (close - sma20) / (std20 + 1e-10)
        
        last_z = z_score.iloc[-1]
        last_rsi = rsi.iloc[-1]
        
        # Aşırı sapma tespiti
        oversold = last_z < -2.5 and last_rsi < 30
        overbought = last_z > 2.5 and last_rsi > 70
        
        return {
            "z_score": round(last_z, 2),
            "rsi": round(last_rsi, 2),
            "is_mean_reversion_candidate": oversold or overbought,
            "reversion_direction": "LONG" if oversold else "SHORT" if overbought else "NONE"
        }

    def analyze_volume_profile(self, df: pd.DataFrame) -> dict:
        """Basit Volume Profile (POC - Point of Control) analizi."""
        # Fiyat aralığını 10 kutuya (bin) böl
        bins = 10
        df['price_bin'] = pd.cut(df['close'], bins=bins)
        vp = df.groupby('price_bin', observed=True)['volume'].sum()
        poc_bin = vp.idxmax()
        
        # POC fiyat seviyesini hesapla (kutunun orta noktası)
        poc_price = (poc_bin.left + poc_bin.right) / 2
        current_price = df['close'].iloc[-1]
        
        return {
            "poc_price": round(poc_price, 6),
            "dist_to_poc_pct": round((current_price - poc_price) / poc_price * 100, 2),
            "above_poc": current_price > poc_price
        }

    def analyze(self, symbol: str) -> dict:
        # Temel trend analizi (TrendEngine'den)
        base_analysis = super().analyze(symbol)
        if base_analysis["direction"] == "NO TRADE":
            # Trend yoksa Mean Reversion fırsatı ara
            df15 = self.get_candles(symbol, "15m", 200)
            if df15.empty: return base_analysis
            
            mr = self.analyze_mean_reversion(df15)
            if mr["is_mean_reversion_candidate"]:
                base_analysis.update({
                    "direction": mr["reversion_direction"],
                    "score": 7.0, # Mean reversion için sabit başlangıç skoru
                    "type": "MEAN_REVERSION",
                    **mr
                })
                return base_analysis
        
        # Trend varsa Volume Profile ile destekle
        df15 = self.get_candles(symbol, "15m", 100)
        if not df15.empty:
            vp = self.analyze_volume_profile(df15)
            base_analysis.update({"volume_profile": vp})
            
            # Trend yönü POC ile uyumluysa skor ekle
            if base_analysis["direction"] == "LONG" and not vp["above_poc"]:
                base_analysis["score"] += 1.0 # POC altından yukarı dönüş desteği
            elif base_analysis["direction"] == "SHORT" and vp["above_poc"]:
                base_analysis["score"] += 1.0 # POC üstünden aşağı dönüş desteği
                
        return base_analysis
