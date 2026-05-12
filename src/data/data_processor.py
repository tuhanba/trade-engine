# src/data/data_processor.py

import pandas as pd
from utils.logger import setup_logger


class DataProcessor:
    """
    Ham OHLCV listesini pandas DataFrame'e dönüştürür,
    eksik verileri temizler ve normalize eder.
    """

    def __init__(self):
        self.logger = setup_logger(__name__)

    def to_dataframe(self, candles: list) -> pd.DataFrame:
        """Liste formatındaki OHLCV'yi DataFrame'e çevirir."""
        if not candles:
            return pd.DataFrame()
        df = pd.DataFrame(candles)
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df.set_index("timestamp", inplace=True)
        df.sort_index(inplace=True)
        return df

    def clean(self, df: pd.DataFrame) -> pd.DataFrame:
        """Eksik ve sıfır değerleri temizler."""
        before = len(df)
        df = df.dropna()
        df = df[df["close"] > 0]
        after = len(df)
        if before != after:
            self.logger.warning(f"Cleaned {before - after} invalid rows.")
        return df

    def normalize(self, df: pd.DataFrame, column: str = "close") -> pd.Series:
        """Bir sütunu 0-1 aralığına normalize eder."""
        col = df[column]
        min_val, max_val = col.min(), col.max()
        if max_val == min_val:
            return col * 0
        return (col - min_val) / (max_val - min_val)
