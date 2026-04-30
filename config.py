import os
from dotenv import load_dotenv

load_dotenv()

def _float(key, default):
    try:
        return float(os.getenv(key, default))
    except (ValueError, TypeError):
        return float(default)

def _int(key, default):
    try:
        return int(os.getenv(key, default))
    except (ValueError, TypeError):
        return int(default)

def _str(key, default):
    return os.getenv(key, default)

# --- Mode ---
AX_MODE        = _str("AX_MODE", "execute")        # execute | watch | off
EXECUTION_MODE = _str("EXECUTION_MODE", "paper")   # paper | live

# --- Risk ---
RISK_PCT             = _float("RISK_PCT", 1.0)
MAX_OPEN_TRADES      = _int("MAX_OPEN_TRADES", 5)
DAILY_MAX_LOSS_PCT   = _float("DAILY_MAX_LOSS_PCT", 4.0)

# --- Circuit Breaker ---
CIRCUIT_BREAKER_LOSSES  = _int("CIRCUIT_BREAKER_LOSSES", 4)
CIRCUIT_BREAKER_MINUTES = _int("CIRCUIT_BREAKER_MINUTES", 90)

# --- Signal / RR ---
MIN_RR              = _float("MIN_RR", 1.4)
SL_ATR_MULT         = _float("SL_ATR_MULT", 1.2)
MIN_EXPECTED_MFE_R  = _float("MIN_EXPECTED_MFE_R", 0.9)

# --- Partial Exit ---
TP1_R            = _float("TP1_R", 1.1)
TP1_CLOSE_PCT    = _float("TP1_CLOSE_PCT", 35.0)
TP2_R            = _float("TP2_R", 2.0)
TP2_CLOSE_PCT    = _float("TP2_CLOSE_PCT", 35.0)
RUNNER_CLOSE_PCT = _float("RUNNER_CLOSE_PCT", 30.0)
TRAIL_ATR_MULT   = _float("TRAIL_ATR_MULT", 1.2)

# --- Paths ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(BASE_DIR, _str("DB_PATH", "trading.db"))
LOG_DIR  = os.path.join(BASE_DIR, _str("LOG_DIR", "logs"))

os.makedirs(LOG_DIR, exist_ok=True)

# --- API Keys (used by execution engine) ---
BINANCE_API_KEY    = _str("BINANCE_API_KEY", "")
BINANCE_API_SECRET = _str("BINANCE_API_SECRET", "")
TELEGRAM_TOKEN     = _str("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID   = _str("TELEGRAM_CHAT_ID", "")
N8N_WEBHOOK_URL    = _str("N8N_WEBHOOK_URL", "")
