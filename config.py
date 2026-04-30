"""
config.py — AX Sistem Sabitleri
================================
Tüm sabitler buradan okunur. Hiçbir dosya doğrudan os.getenv kullanmaz.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── Mod ──────────────────────────────────────────────────────────────────────
AX_MODE        = os.getenv("AX_MODE", "execute")      # execute | observe
EXECUTION_MODE = os.getenv("EXECUTION_MODE", "paper") # paper | live
PAPER_MODE     = EXECUTION_MODE == "paper"

# ── API ──────────────────────────────────────────────────────────────────────
BINANCE_API_KEY    = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")

# ── Telegram ─────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Risk ─────────────────────────────────────────────────────────────────────
RISK_PCT                = float(os.getenv("RISK_PCT", "1.0"))
MAX_OPEN_TRADES         = int(os.getenv("MAX_OPEN_TRADES", "2"))
DAILY_MAX_LOSS_PCT      = float(os.getenv("DAILY_MAX_LOSS_PCT", "3.0"))
CIRCUIT_BREAKER_LOSSES  = int(os.getenv("CIRCUIT_BREAKER_LOSSES", "3"))
CIRCUIT_BREAKER_MINUTES = int(os.getenv("CIRCUIT_BREAKER_MINUTES", "120"))

# ── Sinyal ───────────────────────────────────────────────────────────────────
MIN_RR             = float(os.getenv("MIN_RR", "1.5"))
SL_ATR_MULT        = float(os.getenv("SL_ATR_MULT", "1.3"))
MIN_EXPECTED_MFE_R = float(os.getenv("MIN_EXPECTED_MFE_R", "1.0"))

# ── TP ───────────────────────────────────────────────────────────────────────
TP1_R            = float(os.getenv("TP1_R", "0.9"))
TP1_CLOSE_PCT    = float(os.getenv("TP1_CLOSE_PCT", "40"))
TP2_R            = float(os.getenv("TP2_R", "1.5"))
TP2_CLOSE_PCT    = float(os.getenv("TP2_CLOSE_PCT", "30"))
RUNNER_CLOSE_PCT = float(os.getenv("RUNNER_CLOSE_PCT", "30"))
TRAIL_ATR_MULT   = float(os.getenv("TRAIL_ATR_MULT", "1.0"))

# ── Tarama ───────────────────────────────────────────────────────────────────
SCAN_INTERVAL    = int(os.getenv("SCAN_INTERVAL",    "60"))    # saniye
MAX_HOLD_MINUTES = int(os.getenv("MAX_HOLD_MINUTES", "240"))   # dakika

# ── Veritabanı ───────────────────────────────────────────────────────────────
DB_PATH = os.getenv("DB_PATH", "/root/trade_engine/trading.db")

# ── Coin Evreni ──────────────────────────────────────────────────────────────
COIN_UNIVERSE = [
    "BTCUSDT",  "ETHUSDT",  "SOLUSDT",  "BNBUSDT",  "XRPUSDT",
    "DOGEUSDT", "ADAUSDT",  "AVAXUSDT", "LINKUSDT", "NEARUSDT",
    "OPUSDT",   "ARBUSDT",  "INJUSDT",  "SUIUSDT",  "FETUSDT",
    "RNDRUSDT", "WIFUSDT",  "PEPEUSDT", "SEIUSDT",  "LTCUSDT",
]

# ── Log ──────────────────────────────────────────────────────────────────────
LOG_DIR      = os.getenv("LOG_DIR", "/root/trade_engine/logs")
LOG_MAX_DAYS = int(os.getenv("LOG_MAX_DAYS", "7"))
LOG_MAX_MB   = int(os.getenv("LOG_MAX_MB", "50"))
