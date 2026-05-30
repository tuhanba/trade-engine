"""
scripts/run_optimization.py – Makine Öğrenimi Tabanlı Parametre Optimizasyonu
=============================================================================
Optuna kullanarak geçmiş verilere göre en karlı trade parametrelerini bulur
ve veritabanına kaydeder (Hyperparameter Tuning).
"""

import os
import sys
import logging
import sqlite3
import pandas as pd
import optuna
import ccxt

# Üst dizindeki core modüllerini import edebilmek için yola ekle
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DB_PATH

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("optuna_optimizer")

# Optimizasyon ayarları
SYMBOL = "BTC/USDT:USDT"
TIMEFRAME = "5m"
LIMIT = 1000  # Son 1000 mum

def fetch_data() -> pd.DataFrame:
    """CCXT üzerinden geçmiş kline verilerini çeker."""
    logger.info(f"Fetching historical data for {SYMBOL}...")
    exchange = ccxt.binance({'options': {'defaultType': 'future'}})
    ohlcv = exchange.fetch_ohlcv(SYMBOL, timeframe=TIMEFRAME, limit=LIMIT)
    
    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
        
    return df

def calculate_indicators(df: pd.DataFrame, rsi_period: int) -> pd.DataFrame:
    """Temel indikatörleri hesaplar (RSI, ATR vb.)."""
    df = df.copy()
    
    # RSI Hesaplama
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=rsi_period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=rsi_period).mean()
    rs = gain / loss
    df['rsi'] = 100 - (100 / (1 + rs))
    
    # ATR Hesaplama (basit versiyon, 14 periyot)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift()).abs(),
        (df["low"]  - df["close"].shift()).abs(),
    ], axis=1).max(axis=1)
    df['atr'] = tr.rolling(14).mean()
    
    df.dropna(inplace=True)
    return df

def backtest(df: pd.DataFrame, params: dict) -> float:
    """Verilen parametrelerle çok basit bir momentum backtesti yapar."""
    sl_atr_mult = params['sl_atr_mult']
    tp_atr_mult = params['tp_atr_mult']
    rsi_min = params['rsi_min']
    
    total_pnl = 0.0
    in_trade = False
    entry_price = 0.0
    sl_price = 0.0
    tp_price = 0.0
    
    for i in range(len(df)):
        row = df.iloc[i]
        
        # Basit Long Stratejisi
        if not in_trade:
            if row['rsi'] < rsi_min:  # Aşırı satım, dip avcılığı
                in_trade = True
                entry_price = row['close']
                sl_price = entry_price - (row['atr'] * sl_atr_mult)
                tp_price = entry_price + (row['atr'] * tp_atr_mult)
        else:
            # Trade içinde
            if row['low'] <= sl_price:
                # Stop oldu
                total_pnl -= abs(entry_price - sl_price)
                in_trade = False
            elif row['high'] >= tp_price:
                # Kar aldı
                total_pnl += abs(tp_price - entry_price)
                in_trade = False
                
    return total_pnl

def objective(trial, df):
    """Optuna objective fonksiyonu."""
    # Hiperparametre uzayı
    params = {
        'sl_atr_mult': trial.suggest_float('sl_atr_mult', 0.5, 3.0, step=0.1),
        'tp_atr_mult': trial.suggest_float('tp_atr_mult', 1.0, 5.0, step=0.1),
        'rsi_min': trial.suggest_int('rsi_min', 20, 45),
        'rsi_period': trial.suggest_int('rsi_period', 7, 21)
    }
    
    df_indicators = calculate_indicators(df, params['rsi_period'])
    pnl = backtest(df_indicators, params)
    
    return pnl

def save_best_params_to_db(best_params: dict):
    """En iyi parametreleri veritabanına yazar."""
    try:
        conn = sqlite3.connect(DB_PATH)
        # Sadece params tablosundaki ID=1 olan kaydı günceller
        conn.execute("""
            UPDATE params SET 
                sl_atr_mult = ?,
                tp_atr_mult = ?,
                rsi5_min = ?,
                updated_at = datetime('now')
            WHERE id = 1
        """, (best_params['sl_atr_mult'], best_params['tp_atr_mult'], best_params['rsi_min']))
        conn.commit()
        logger.info("En iyi parametreler veritabanına kaydedildi.")
    except Exception as e:
        logger.error(f"DB Kayıt Hatası: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    logger.info("=== Optuna Hyperparameter Optimization Başlatıldı ===")
    
    try:
        df = fetch_data()
        
        # Optuna Study Oluştur
        study = optuna.create_study(direction="maximize")
        logger.info("Study başlatılıyor. 100 deneme (trial) yapılacak...")
        
        # Optimizasyonu çalıştır
        study.optimize(lambda trial: objective(trial, df), n_trials=100)
        
        logger.info("=== Optimizasyon Tamamlandı ===")
        logger.info(f"En iyi PnL Skoru: {study.best_value}")
        logger.info(f"En iyi Parametreler: {study.best_params}")
        
        # Veritabanına kaydet
        save_best_params_to_db(study.best_params)
        
    except Exception as e:
        logger.error(f"Optimizasyon hatası: {e}")
