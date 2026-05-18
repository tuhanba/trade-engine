"""
config.py — AX Trade Engine Merkezi Konfigurasyon v5.1
Merge conflict cozuldu. Her iki branch'in en iyisi birlestirme.
"""
import os
import logging
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger("ax.config")

def _env(key, default=""): return os.getenv(key, default).strip()
def _env_bool(key, default=False):
    v = os.getenv(key, "").strip().lower()
    return True if v in ("true","1","yes") else (False if v in ("false","0","no","") else default)
def _env_int(key, default=0):
    v = os.getenv(key, "").strip()
    try: return int(v) if v else default
    except: return default
def _env_float(key, default=0.0):
    v = os.getenv(key, "").strip()
    try: return float(v) if v else default
    except: return default

BASE_DIR = Path(__file__).resolve().parent
DB_PATH  = str(BASE_DIR / _env("DB_PATH", "trading.db"))

# Execution
EXECUTION_MODE          = _env("EXECUTION_MODE", "paper")
AX_MODE                 = _env("AX_MODE", "execute")
PAPER_MODE              = _env_bool("PAPER_MODE", True)
LIVE_TRADING_ENABLED    = _env_bool("LIVE_TRADING_ENABLED", False)
DRY_RUN                 = _env_bool("DRY_RUN", True)
CONFIRM_LIVE_TRADING    = _env_bool("CONFIRM_LIVE_TRADING", False)
LIVE_CONFIRM            = _env_bool("LIVE_CONFIRM", False)
USE_BINANCE_PRIVATE_API = _env_bool("USE_BINANCE_PRIVATE_API", False)

# API Keys
BINANCE_API_KEY    = _env("BINANCE_API_KEY")
BINANCE_API_SECRET = _env("BINANCE_API_SECRET")
TELEGRAM_BOT_TOKEN = _env("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = _env("TELEGRAM_CHAT_ID")
SECRET_KEY         = _env("SECRET_KEY", "ax_secret_prod_2026")
ANTHROPIC_API_KEY  = _env("ANTHROPIC_API_KEY")

# Risk
RISK_PCT                   = _env_float("RISK_PCT", 1.0)
MAX_OPEN_TRADES            = _env_int("MAX_OPEN_TRADES", 5)
DAILY_MAX_LOSS_PCT         = _env_float("DAILY_MAX_LOSS_PCT", 5.0)
MAX_LEVERAGE               = _env_int("MAX_LEVERAGE", 20)
DEFAULT_FEE_RATE           = _env_float("DEFAULT_FEE_RATE", 0.0004)
MAX_CONSECUTIVE_LOSSES     = _env_int("MAX_CONSECUTIVE_LOSSES", 5)
COIN_COOLDOWN_MINUTES      = _env_int("COIN_COOLDOWN_MINUTES", 30)
MAX_CORRELATED_TRADES      = _env_int("MAX_CORRELATED_TRADES", 3)
MAX_MARGIN_LOSS_PCT        = _env_float("MAX_MARGIN_LOSS_PCT", 0.40)
MAX_PORTFOLIO_EXPOSURE_PCT = _env_float("MAX_PORTFOLIO_EXPOSURE_PCT", 95.0)
MIN_RR                     = _env_float("MIN_RR", 1.5)
MIN_EXPECTED_MFE_R         = _env_float("MIN_EXPECTED_MFE_R", 1.2)

# TP / SL ATR multipliers
SL_ATR_MULT  = _env_float("SL_ATR_MULT", 1.2)
TP1_R        = _env_float("TP1_R", 1.0)
TP2_R        = _env_float("TP2_R", 2.0)
TP3_R        = _env_float("TP3_R", 3.0)

# TP Splits
TP1_CLOSE_PCT    = _env_float("TP1_CLOSE_PCT", 40)
TP2_CLOSE_PCT    = _env_float("TP2_CLOSE_PCT", 30)
RUNNER_CLOSE_PCT = _env_float("RUNNER_CLOSE_PCT", 30)

# Trailing
TRAIL_ATR_MULT       = _env_float("TRAIL_ATR_MULT", 1.5)
BREAKEVEN_ENABLED    = _env_bool("BREAKEVEN_ENABLED", True)
BREAKEVEN_OFFSET_PCT = _env_float("BREAKEVEN_OFFSET_PCT", 0.05)

# Paper / Timing
INITIAL_PAPER_BALANCE = _env_float("INITIAL_PAPER_BALANCE", 500.0)
MAX_HOLD_MINUTES      = _env_int("MAX_HOLD_MINUTES", 240)

# Scan
SCAN_INTERVAL            = _env_int("SCAN_INTERVAL", 60)
SCAN_INTERVAL_SECONDS    = _env_int("SCAN_INTERVAL_SECONDS", 60)
MIN_VOLUME_USDT          = _env_float("MIN_VOLUME_USDT", 5_000_000.0)
MIN_MOVE_PCT             = _env_float("MIN_MOVE_PCT", 0.5)
SCAN_INCLUDE_WATCH       = _env_bool("SCAN_INCLUDE_WATCH", True)
WATCHLIST_MIN_SCAN_SCORE = _env_float("WATCHLIST_MIN_SCAN_SCORE", 50.0)
MAX_COINS_PER_SCAN_LOOP  = _env_int("MAX_COINS_PER_SCAN_LOOP", 30)
MAX_DAILY_SIGNALS        = _env_int("MAX_DAILY_SIGNALS", 60)

# Sinyal Esikleri (v9.0 — gevşetilmiş, score max ~75 üretiyor)
DATA_THRESHOLD      = _env_float("DATA_THRESHOLD", 20.0)
WATCHLIST_THRESHOLD = _env_float("WATCHLIST_THRESHOLD", 25.0)
TELEGRAM_THRESHOLD  = _env_float("TELEGRAM_THRESHOLD", 28.0)
TRADE_THRESHOLD     = _env_float("TRADE_THRESHOLD", 35.0)

# Circuit Breaker
CIRCUIT_BREAKER_LOSSES  = _env_int("CIRCUIT_BREAKER_LOSSES", 3)
CIRCUIT_BREAKER_MINUTES = _env_int("CIRCUIT_BREAKER_MINUTES", 60)

# Paper Tracking
PAPER_TRACK_REJECTED_CANDIDATES = _env_bool("PAPER_TRACK_REJECTED_CANDIDATES", True)
PAPER_TRACK_WATCHLIST           = _env_bool("PAPER_TRACK_WATCHLIST", True)
PAPER_TRACK_TELEGRAM_GAPS       = _env_bool("PAPER_TRACK_TELEGRAM_GAPS", True)
PAPER_TRACK_HORIZON_HOURS       = _env_float("PAPER_TRACK_HORIZON_HOURS", 6.0)

# Trigger Engine
ALLOWED_QUALITIES          = ["S", "A+", "A", "B"]
BAD_HOURS_UTC              = [4, 5, 6, 11, 12, 13]
GOOD_HOURS_UTC             = [0, 1, 2, 3, 7, 8, 9, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23]
SHORT_REQUIRES_BTC_BEARISH = True
BTC_TREND_INTERVAL         = "4h"
ADX_MIN_THRESHOLD          = 18  # eskiden 20

# Scalp Filtreler (v9.0 — gevşetilmiş)
MIN_BB_WIDTH    = _env_float("MIN_BB_WIDTH", 1.0)      # eskiden 1.3
MIN_ADX_15M     = _env_float("MIN_ADX_15M", 18)        # eskiden 20
MIN_ADX_5M      = _env_float("MIN_ADX_5M", 13)         # eskiden 15
FUNDING_LONG_MAX  = _env_float("FUNDING_LONG_MAX", 0.003)   # eskiden 0.001
FUNDING_SHORT_MIN = _env_float("FUNDING_SHORT_MIN", -0.003)  # eskiden -0.001

# Flask
FLASK_HOST = _env("FLASK_HOST", "0.0.0.0")
FLASK_PORT = _env_int("FLASK_PORT", 5000)

# Log
LOG_DIR      = _env("LOG_DIR", "logs")
LOG_MAX_DAYS = _env_int("LOG_MAX_DAYS", 7)
LOG_MAX_MB   = _env_int("LOG_MAX_MB", 50)

# Coin Evreni (v2.3 - backtest onaylli 92 coin)
COIN_UNIVERSE = [
    "ALLOUSDT","DYDXUSDT","SNDKUSDT","FLUIDUSDT","ZEREBROUSDT",
    "HOODUSDT","GWEIUSDT","REZUSDT","TACUSDT","ARBUSDT",
    "ADAUSDT","EIGENUSDT","1000FLOKIUSDT","1000SHIBUSDT","FARTCOINUSDT",
    "AEROUSDT","EWYUSDT","FLOWUSDT","PENDLEUSDT","BZUSDT",
    "BASUSDT","TIAUSDT","PIPPINUSDT","OPUSDT","BCHUSDT",
    "XPTUSDT","MASKUSDT","UNIUSDT","SIRENUSDT","DASHUSDT",
    "MAGMAUSDT","MSFTUSDT","CRCLUSDT","ENAUSDT","ICPUSDT",
    "STRKUSDT","WLFIUSDT","MOODENGUSDT","CLUSDT","AIOTUSDT",
    "1000PEPEUSDT","SPXUSDT","SOONUSDT","TRADOORUSDT","XAGUSDT",
    "AIGENSYNUSDT","WIFUSDT","MSTRUSDT","DRIFTUSDT","RAYSOLUSDT",
    "ETHUSDT","HUSDT","USTCUSDT","COMPUSDT","BIOUSDT",
    "APTUSDT","CHZUSDT","AXSUSDT","NEIROUSDT","BLUAIUSDT",
    "CRVUSDT","RENDERUSDT","NAORISUSDT","BEATUSDT","DOTUSDT",
    "CGPTUSDT","TAOUSDT","PIEVERSEUSDT","LINEAUSDT","SKYAIUSDT",
    "AXLUSDT","PUMPUSDT","ARCUSDT","POLUSDT","ACHUSDT",
    "HBARUSDT","ASTERUSDT","BSBUSDT","ZBTUSDT","BERAUSDT",
    "FILUSDT","PENGUUSDT","1000BONKUSDT","CFXUSDT","ETHFIUSDT",
    "AIXBTUSDT","B2USDT","HUMAUSDT","ATOMUSDT","LITUSDT",
    "ZECUSDT","GOOGLUSDT",
]

# Ghost Learning
GHOST_WEIGHT = _env_float("GHOST_WEIGHT", 0.30)

# Guvenlik
def is_live_trading_allowed() -> bool:
    return (EXECUTION_MODE == "live" and LIVE_TRADING_ENABLED and CONFIRM_LIVE_TRADING)

def is_private_api_allowed() -> bool:
    return USE_BINANCE_PRIVATE_API

def safety_summary() -> dict:
    return {
        "execution_mode": EXECUTION_MODE, "ax_mode": AX_MODE,
        "live_trading_enabled": LIVE_TRADING_ENABLED, "dry_run": DRY_RUN,
        "live_allowed": is_live_trading_allowed(),
    }
