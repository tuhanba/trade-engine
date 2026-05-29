import sys
import os
import time
import logging
import pandas as pd
from binance.client import Client

# Proje kok dizinini yola ekle
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from database import save_ghost_signal
from core.trend_engine import TrendEngine
from core.ai_decision_engine import ai_decision_engine, SignalData

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("ax.mtf_ghost")

class MTFGhostScanner:
    """
    Ana 5m motorunu bozmadan, 15m ve 1h zaman dilimleri uzerinde 
    TrendEngine ve AIDecisionEngine'i simule edip ghost_signals kayitlari olusturur.
    """
    def __init__(self):
        self.client = Client(config.BINANCE_API_KEY, config.BINANCE_API_SECRET)
        self.trend_engine = TrendEngine(self.client)
        self.timeframes = ["15m", "1h"]

    def run_scan(self):
        logger.info("MTF Ghost Scan baslatiliyor...")
        try:
            # En yuksek hacimli 15 coini bul
            tickers = self.client.futures_ticker()
            usdt_pairs = [t for t in tickers if t['symbol'].endswith('USDT')]
            usdt_pairs.sort(key=lambda x: float(x.get('quoteVolume', 0)), reverse=True)
            top_symbols = [t['symbol'] for t in usdt_pairs[:15]]
            
            for tf in self.timeframes:
                for symbol in top_symbols:
                    self._scan_symbol(symbol, tf)
                    time.sleep(0.5) # API Rate Limit korumasi
        except Exception as e:
            logger.error(f"MTF Scan hatasi: {e}")

    def _scan_symbol(self, symbol: str, timeframe: str):
        try:
            # Mum verisini cek
            limit = 100
            klines = self.client.futures_klines(symbol=symbol, interval=timeframe, limit=limit)
            if len(klines) < 50: return
            
            df = pd.DataFrame(klines, columns=['time', 'open', 'high', 'low', 'close', 'vol', 'close_time', 'qav', 'trades', 'tbb', 'tbq', 'ignore'])
            df['close'] = df['close'].astype(float)
            
            # Trend Analizi (TrendEngine'i basite indirgenmis sekilde cagiriyoruz)
            # Normalde TrendEngine icinde analyze() 15m'e hardcoded'dir. Biz manuel analiz yapiyoruz.
            e9 = df['close'].ewm(span=9, adjust=False).mean().iloc[-1]
            e21 = df['close'].ewm(span=21, adjust=False).mean().iloc[-1]
            e55 = df['close'].ewm(span=55, adjust=False).mean().iloc[-1]
            c = df['close'].iloc[-1]
            
            direction = "NEUTRAL"
            if e9 > e21 and c > e55:
                direction = "LONG"
            elif e9 < e21 and c < e55:
                direction = "SHORT"
                
            if direction == "NEUTRAL": return
            
            # Sinyal Adayi Uret
            atr = c * 0.02 # Basit ATR 
            sl = c - atr if direction == "LONG" else c + atr
            tp1 = c + (atr * 1.5) if direction == "LONG" else c - (atr * 1.5)
            
            sig = SignalData(
                id=int(time.time()),
                symbol=symbol,
                direction=direction,
                entry_price=c,
                stop_loss=sl,
                tp1=tp1, tp2=tp1, tp3=tp1,
                atr=atr,
                base_score=75.0,
                final_score=80.0,
                trend_confluence=2,
                quality="B",
                market_regime="TRENDING"
            )
            
            # AI Karari
            ai_res = ai_decision_engine.classify_signal(sig)
            
            if ai_res.decision == "VETO":
                logger.debug(f"[MTF {timeframe}] {symbol} VETO edildi: {ai_res.reason}")
                return
                
            # Ghost Kaydi (MTF olarak)
            save_ghost_signal({
                "symbol": symbol,
                "direction": direction,
                "timeframe": timeframe,
                "entry": c,
                "sl": sl,
                "tp1": tp1,
                "tp2": tp1,
                "tp3": tp1,
                "atr": atr,
                "final_score": ai_res.adjusted_score,
                "market_regime": "TRENDING",
                "confidence": ai_res.confidence,
                "reject_reason": ai_res.reason,
                "trigger_type": "MTF_SCANNER"
            })
            
            logger.info(f"[MTF {timeframe}] {symbol} Ghost Sinyal kaydedildi (Scor: {ai_res.adjusted_score})")
            
        except Exception as e:
            logger.error(f"{symbol} MTF tarama hatasi: {e}")

if __name__ == "__main__":
    scanner = MTFGhostScanner()
    while True:
        scanner.run_scan()
        logger.info("Tarama bitti, 15 dakika bekleniyor...")
        time.sleep(900)
