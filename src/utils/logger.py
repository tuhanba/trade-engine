# src/utils/logger.py

import logging
import os
from logging.handlers import RotatingFileHandler


def setup_logger(name: str, log_level: str = "INFO", log_dir: str = "logs") -> logging.Logger:
    """
    Modül adına göre logger oluşturur.
    - Konsola renkli çıktı
    - logs/ klasörüne dönen dosya (max 5MB x 3 dosya)
    """
    os.makedirs(log_dir, exist_ok=True)

    logger = logging.getLogger(name)
    level = getattr(logging, log_level.upper(), logging.INFO)
    logger.setLevel(level)

    if logger.handlers:
        return logger  # Çift handler eklemeyi önle

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)-25s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Konsol handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # Dosya handler (rotating)
    file_handler = RotatingFileHandler(
        filename=os.path.join(log_dir, "trade_engine.log"),
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=3,
        encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger
