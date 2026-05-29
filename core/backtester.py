import logging
import pandas as pd
import numpy as np
import time
from binance.client import Client
import config
from core.ai_decision_engine import ai_decision_engine, SignalData

logger = logging.getLogger("ax.backtester")

class Backtester:
    """
    Tarihsel Binance verilerini çekip, yapay zeka karar motoru (AI Decision Engine)
    üzerinden geçirerek sanal PnL simülasyonu yapan motor.
    """
    def __init__(self, client: Client):
        self.client = client
        
    def _ema(self, series: pd.Series, period: int) -> pd.Series:
        return series.ewm(span=period, adjust=False).mean()

    def _rsi(self, series: pd.Series, period: int = 14) -> pd.Series:
        delta = series.diff()
        gain = (delta.where(delta > 0, 0)).fillna(0)
        loss = (-delta.where(delta < 0, 0)).fillna(0)
        avg_gain = gain.rolling(window=period, min_periods=period).mean()
        avg_loss = loss.rolling(window=period, min_periods=period).mean()
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def _atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        high_low = df['high'] - df['low']
        high_close = np.abs(df['high'] - df['close'].shift())
        low_close = np.abs(df['low'] - df['close'].shift())
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = np.max(ranges, axis=1)
        return true_range.rolling(period).mean()

    def run_backtest(self, symbol: str, interval: str = "5m", limit: int = 1000):
        logger.info(f"[{symbol}] {limit} mumluk backtest baslatiliyor...")
        try:
            klines = self.client.futures_klines(symbol=symbol, interval=interval, limit=limit)
        except Exception as e:
            logger.error(f"Klines alinamadi: {e}")
            return None

        df = pd.DataFrame(klines, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_av', 'trades', 'tb_base_av', 'tb_quote_av', 'ignore'
        ])
        df['close'] = df['close'].astype(float)
        df['high'] = df['high'].astype(float)
        df['low'] = df['low'].astype(float)
        
        # Indikatorler
        df['ema9'] = self._ema(df['close'], 9)
        df['ema21'] = self._ema(df['close'], 21)
        df['ema55'] = self._ema(df['close'], 55)
        df['rsi14'] = self._rsi(df['close'], 14)
        df['atr'] = self._atr(df, 14)

        trades = []
        balance = 1000.0 # Baslangic bakiyesi
        win_count = 0
        loss_count = 0

        # En son mumlar stabil olmasi icin 100'den basla
        for i in range(100, len(df)-1):
            row = df.iloc[i]
            prev = df.iloc[i-1]
            
            # Basit Kesisim Tetigi (Trigger Engine benzeri)
            long_cond = prev['ema9'] <= prev['ema21'] and row['ema9'] > row['ema21'] and row['close'] > row['ema55']
            short_cond = prev['ema9'] >= prev['ema21'] and row['ema9'] < row['ema21'] and row['close'] < row['ema55']
            
            if not long_cond and not short_cond:
                continue
                
            direction = "LONG" if long_cond else "SHORT"
            entry_price = row['close']
            atr_val = row['atr']
            if pd.isna(atr_val) or atr_val == 0:
                continue
                
            # SL / TP Hesaplama (Fix Ratios)
            if direction == "LONG":
                sl = entry_price - (atr_val * 1.5)
                tp1 = entry_price + (atr_val * 2.25)
            else:
                sl = entry_price + (atr_val * 1.5)
                tp1 = entry_price - (atr_val * 2.25)
                
            # Mock SignalData
            sig = SignalData(
                id=i, symbol=symbol, direction=direction, entry_price=entry_price,
                stop_loss=sl, tp1=tp1, tp2=tp1, tp3=tp1, atr=atr_val,
                base_score=75.0, final_score=80.0, trend_confluence=3,
                quality="A", market_regime="TRENDING"
            )
            
            # AI Karari
            ai_res = ai_decision_engine.classify_signal(sig)
            if ai_res.decision == "VETO":
                continue # AI Reddetti
                
            # Simulasyon: Gelecek mumlara bakip islem sonucunu bulalim
            # (Basit SL / TP dokunma simulasyonu)
            trade_result = None
            for j in range(i+1, len(df)):
                future_row = df.iloc[j]
                if direction == "LONG":
                    if future_row['low'] <= sl:
                        trade_result = "LOSS"
                        break
                    if future_row['high'] >= tp1:
                        trade_result = "WIN"
                        break
                else:
                    if future_row['high'] >= sl:
                        trade_result = "LOSS"
                        break
                    if future_row['low'] <= tp1:
                        trade_result = "WIN"
                        break
            
            if trade_result:
                # %2 risk simulasyonu
                risk_amt = balance * 0.02
                if trade_result == "WIN":
                    win_count += 1
                    profit = risk_amt * 1.5 # (TP1/SL orani 1.5R)
                    balance += profit
                else:
                    loss_count += 1
                    balance -= risk_amt
                    
                trades.append({
                    "time": df.iloc[i]['timestamp'],
                    "direction": direction,
                    "result": trade_result,
                    "balance": balance
                })

        total_trades = win_count + loss_count
        win_rate = (win_count / total_trades * 100) if total_trades > 0 else 0
        
        logger.info(f"[{symbol}] BACKTEST SONUCU: Trades: {total_trades}, WinRate: %{win_rate:.1f}, Son Bakiye: ${balance:.2f}")
        return {
            "symbol": symbol,
            "total_trades": total_trades,
            "win_rate": win_rate,
            "final_balance": balance,
            "trades": trades
        }

if __name__ == "__main__":
    import config
    logging.basicConfig(level=logging.INFO)
    client = Client(config.BINANCE_API_KEY, config.BINANCE_API_SECRET)
    bt = Backtester(client)
    bt.run_backtest("BTCUSDT", "5m", 1500)
