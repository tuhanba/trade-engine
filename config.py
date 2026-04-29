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
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "60"))   # saniye

# ── Veritabanı ───────────────────────────────────────────────────────────────
DB_PATH = os.getenv("DB_PATH", "/root/trade_engine/trading.db")

# ── Coin Evreni ──────────────────────────────────────────────────────────────
COIN_UNIVERSE = [
    # Büyük cap — yüksek likidite
    "BTCUSDT",  "ETHUSDT",  "SOLUSDT",  "BNBUSDT",  "XRPUSDT",
    "DOGEUSDT", "ADAUSDT",  "AVAXUSDT", "TRXUSDT",  "XLMUSDT",
    "DOTUSDT",  "LTCUSDT",  "ATOMUSDT", "HBARUSDT", "ICPUSDT",

    # DeFi & Layer2
    "LINKUSDT", "UNIUSDT",  "AAVEUSDT", "LDOUSDT",  "CRVUSDT",
    "ARBUSDT",  "OPUSDT",   "POLUSDT",  "STXUSDT",  "RUNEUSDT",

    # Yüksek volatilite / trend
    "NEARUSDT", "APTUSDT",  "SUIUSDT",  "INJUSDT",  "SEIUSDT",
    "TAOUSDT",  "ENAUSDT",  "TIAUSDT",  "EIGENUSDT","ONDOUSDT",

    # Meme & hype
    "PEPEUSDT", "WIFUSDT",  "FLOKIUSDT","BONKUSDT", "SHIBUSDT",

    # AI & infra
    "FETUSDT",  "RNDRUSDT", "WLDUSDT",  "JUPUSDT",  "ORDIUSDT",

    # Diğer likit
    "FTMUSDT",  "SANDUSDT", "GALAUSDT", "MNTUSDT",  "MKRUSDT",
]

# ── AI Eşikleri ──────────────────────────────────────────────────────────────
CONFIDENCE_MIN = float(os.getenv("CONFIDENCE_MIN", "0.55"))
SCORE_MIN      = float(os.getenv("SCORE_MIN", "60"))

# ── Candidate Eşikleri (daha gevşek — öğrenme için) ──────────────────────────
CANDIDATE_MIN_VOLUME_M      = float(os.getenv("CANDIDATE_MIN_VOLUME_M", "2.0"))
CANDIDATE_VOL_RATIO_MIN     = float(os.getenv("CANDIDATE_VOL_RATIO_MIN", "0.8"))
CANDIDATE_MIN_CHANGE_PCT    = float(os.getenv("CANDIDATE_MIN_CHANGE_PCT", "0.5"))
CANDIDATE_MIN_RR            = float(os.getenv("CANDIDATE_MIN_RR", "1.2"))
CANDIDATE_MIN_EXPECTED_MFE_R= float(os.getenv("CANDIDATE_MIN_EXPECTED_MFE_R", "0.7"))

# ── Debug ─────────────────────────────────────────────────────────────────────
DEBUG_SIGNAL_MODE = os.getenv("DEBUG_SIGNAL_MODE", "false").lower() == "true"

# ── Telegram ─────────────────────────────────────────────────────────────────
TELEGRAM_ALIVE_INTERVAL_HOURS = int(os.getenv("TELEGRAM_ALIVE_INTERVAL_HOURS", "6"))

# ── Dashboard ─────────────────────────────────────────────────────────────────
DASHBOARD_REFRESH_SECONDS = int(os.getenv("DASHBOARD_REFRESH_SECONDS", "10"))

# ── Log ──────────────────────────────────────────────────────────────────────
LOG_DIR      = os.getenv("LOG_DIR", "/root/trade_engine/logs")
LOG_MAX_DAYS = int(os.getenv("LOG_MAX_DAYS", "7"))
LOG_MAX_MB   = int(os.getenv("LOG_MAX_MB", "50"))
