"""
config.py — AX Trade Engine Merkezi Konfigürasyon v5.0
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
    return True if v in ("true","1","yes") else (default if v in ("false","0","no","") else default)
def _env_int(key, default=0):
    v = os.getenv(key, "").strip()
    try: return int(v) if v else default
    except: return default
def _env_float(key, default=0.0):
    v = os.getenv(key, "").strip()
    try: return float(v) if v else default
    except: return default


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = str(BASE_DIR / _env("DB_PATH", "trading.db"))

# ── Execution modu ──────────────────────────────────────────────────
EXECUTION_MODE = _env("EXECUTION_MODE", "paper")
LIVE_TRADING_ENABLED = _env_bool("LIVE_TRADING_ENABLED", False)
DRY_RUN = _env_bool("DRY_RUN", True)
CONFIRM_LIVE_TRADING = _env_bool("CONFIRM_LIVE_TRADING", False)
USE_BINANCE_PRIVATE_API = _env_bool("USE_BINANCE_PRIVATE_API", False)

# ── API Keys ─────────────────────────────────────────────────────────
BINANCE_API_KEY = _env("BINANCE_API_KEY")
BINANCE_API_SECRET = _env("BINANCE_API_SECRET")
TELEGRAM_BOT_TOKEN = _env("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = _env("TELEGRAM_CHAT_ID")
SECRET_KEY = _env("SECRET_KEY", "ax_secret_prod_2026")

# ── Risk parametreleri ──────────────────────────────────────────────
RISK_PCT = _env_float("RISK_PCT", 1.0)
MAX_OPEN_TRADES = _env_int("MAX_OPEN_TRADES", 5)
DAILY_MAX_LOSS_PCT = _env_float("DAILY_MAX_LOSS_PCT", 5.0)
MAX_LEVERAGE = _env_int("MAX_LEVERAGE", 20)
DEFAULT_FEE_RATE = _env_float("DEFAULT_FEE_RATE", 0.0004)
MAX_CONSECUTIVE_LOSSES = _env_int("MAX_CONSECUTIVE_LOSSES", 5)
COIN_COOLDOWN_MINUTES = _env_int("COIN_COOLDOWN_MINUTES", 60)
MAX_CORRELATED_TRADES = _env_int("MAX_CORRELATED_TRADES", 3)
MAX_MARGIN_LOSS_PCT = _env_float("MAX_MARGIN_LOSS_PCT", 0.40)

# ── TP Splits ───────────────────────────────────────────────────────
TP1_CLOSE_PCT = _env_float("TP1_CLOSE_PCT", 40)
TP2_CLOSE_PCT = _env_float("TP2_CLOSE_PCT", 30)
RUNNER_CLOSE_PCT = _env_float("RUNNER_CLOSE_PCT", 30)

# ── Trailing ────────────────────────────────────────────────────────
TRAIL_ATR_MULT = _env_float("TRAIL_ATR_MULT", 1.5)
BREAKEVEN_ENABLED = _env_bool("BREAKEVEN_ENABLED", True)
BREAKEVEN_OFFSET_PCT = _env_float("BREAKEVEN_OFFSET_PCT", 0.05)

# ── Paper / Timing ──────────────────────────────────────────────────
INITIAL_PAPER_BALANCE = _env_float("INITIAL_PAPER_BALANCE", 250.0)
MAX_HOLD_MINUTES = _env_int("MAX_HOLD_MINUTES", 240)

# ── Scan ────────────────────────────────────────────────────────────
SCAN_INTERVAL_SECONDS = _env_int("SCAN_INTERVAL_SECONDS", 60)
MIN_VOLUME_USDT = _env_float("MIN_VOLUME_USDT", 5_000_000.0)
MIN_MOVE_PCT = _env_float("MIN_MOVE_PCT", 0.5)

# ── Dashboard / Flask ────────────────────────────────────────────────
FLASK_HOST = _env("FLASK_HOST", "0.0.0.0")
FLASK_PORT = _env_int("FLASK_PORT", 5000)

# ── Trigger Engine ──────────────────────────────────────────────────
ALLOWED_QUALITIES = ["S", "A+", "A", "B"]
BAD_HOURS_UTC = [1, 4, 5, 6, 10, 11, 12, 13, 14, 16, 19, 20, 21, 22]
GOOD_HOURS_UTC = [0, 3, 7, 9, 17, 23]
SHORT_REQUIRES_BTC_BEARISH = True
BTC_TREND_INTERVAL = "4h"
ADX_MIN_THRESHOLD = 28


# ── Güvenlik ────────────────────────────────────────────────────────

def is_live_trading_allowed() -> bool:
    return (
        EXECUTION_MODE == "live"
        and LIVE_TRADING_ENABLED is True
        and CONFIRM_LIVE_TRADING is True
    )


def is_private_api_allowed() -> bool:
    return USE_BINANCE_PRIVATE_API is True


def safety_summary() -> dict:
    return {
        "execution_mode": EXECUTION_MODE,
        "live_trading_enabled": LIVE_TRADING_ENABLED,
        "dry_run": DRY_RUN,
        "confirm_live_trading": CONFIRM_LIVE_TRADING,
        "use_binance_private_api": USE_BINANCE_PRIVATE_API,
        "live_allowed": is_live_trading_allowed(),
        "private_api_allowed": is_private_api_allowed(),
    }
