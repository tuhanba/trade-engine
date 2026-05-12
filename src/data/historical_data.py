# src/data/historical_data.py

import os
import pandas as pd
from utils.logger import setup_logger


class HistoricalData:
    """
    Geçmiş OHLCV verisini yerel dosya sistemine kaydeder ve okur.
    Dizin: data/historical/<symbol>/
    """

    def __init__(self, base_dir: str = "data/historical"):
        self.base_dir = base_dir
        self.logger = setup_logger(__name__)
        os.makedirs(base_dir, exist_ok=True)

    def _filepath(self, symbol: str, timeframe: str) -> str:
        symbol_dir = os.path.join(self.base_dir, symbol.replace("/", "_"))
        os.makedirs(symbol_dir, exist_ok=True)
        return os.path.join(symbol_dir, f"{timeframe}.csv")

    def save(self, symbol: str, timeframe: str, df: pd.DataFrame):
        """DataFrame'i CSV olarak kaydeder."""
        path = self._filepath(symbol, timeframe)
        df.to_csv(path)
        self.logger.info(f"Saved {len(df)} rows → {path}")

    def load(self, symbol: str, timeframe: str) -> pd.DataFrame:
        """CSV'den DataFrame yükler."""
        path = self._filepath(symbol, timeframe)
        if not os.path.exists(path):
            self.logger.warning(f"No historical data found: {path}")
            return pd.DataFrame()
        df = pd.read_csv(path, index_col="timestamp", parse_dates=True)
        self.logger.info(f"Loaded {len(df)} rows ← {path}")
        return df

    def append(self, symbol: str, timeframe: str, new_df: pd.DataFrame):
        """Mevcut CSV'ye yeni veri ekler, tekrarları çıkarır."""
        existing = self.load(symbol, timeframe)
        if existing.empty:
            self.save(symbol, timeframe, new_df)
        else:
            combined = pd.concat([existing, new_df])
            combined = combined[~combined.index.duplicated(keep="last")]
            combined.sort_index(inplace=True)
            self.save(symbol, timeframe, combined)
