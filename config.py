"""
config.py — AX Trade Engine Merkezi Konfigurasyon v5.1
Merge conflict cozuldu. Her iki branch'in en iyisi birlestirme.
"""
import os
import logging
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
    env_path = Path(__file__).resolve().parent / ".env"
    load_dotenv(dotenv_path=env_path)
except ImportError:
    pass

logger = logging.getLogger("ax.config")

def _env(key, default=""): return os.getenv(key, default).strip()
def _env_bool(key, default=False):
    v = os.getenv(key, "").strip().lower()
    if not v:
        return default
    return v in ("true", "1", "yes")
def _env_int(key, default=0):
    v = os.getenv(key, "").strip()
    try: return int(v) if v else default
    except Exception: return default
def _env_float(key, default=0.0):
    v = os.getenv(key, "").strip()
    try: return float(v) if v else default
    except Exception: return default

BASE_DIR = Path(__file__).resolve().parent
DB_PATH  = str(BASE_DIR / _env("DB_PATH", "db/trading.db" if (BASE_DIR / "db").exists() else "trading.db"))

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

# Risk & Compounding
AUTO_COMPOUNDING           = _env_bool("AUTO_COMPOUNDING", True)
BASE_ACCOUNT_SIZE          = _env_float("BASE_ACCOUNT_SIZE", 1000.0) # Eger auto-compounding kapaliysa baz alinacak bakiye
RISK_PCT                   = _env_float("RISK_PCT", 0.75)
MAX_OPEN_TRADES            = _env_int("MAX_OPEN_TRADES", 5)
DAILY_MAX_LOSS_PCT         = _env_float("DAILY_MAX_LOSS_PCT", 5.0)
MAX_LEVERAGE               = _env_int("MAX_LEVERAGE", 10)
DEFAULT_FEE_RATE           = _env_float("DEFAULT_FEE_RATE", 0.0004)
MAX_CONSECUTIVE_LOSSES     = _env_int("MAX_CONSECUTIVE_LOSSES", 5)
COIN_COOLDOWN_MINUTES      = _env_int("COIN_COOLDOWN_MINUTES", 30)
MAX_CORRELATED_TRADES      = _env_int("MAX_CORRELATED_TRADES", 3)
MAX_MARGIN_LOSS_PCT        = _env_float("MAX_MARGIN_LOSS_PCT", 0.40)
MAX_PORTFOLIO_EXPOSURE_PCT = _env_float("MAX_PORTFOLIO_EXPOSURE_PCT", 95.0)
MIN_RR                     = _env_float("MIN_RR", 1.5)   # v6.1 düzeltildi: 1.2→1.5
MIN_EXPECTED_MFE_R         = _env_float("MIN_EXPECTED_MFE_R", 1.2)

# TP / SL ATR multipliers  (fix-tp-sl-ratios: TP1=1.5R TP2=2.5R SL min %1.5)
SL_ATR_MULT  = _env_float("SL_ATR_MULT", 1.8)   # v6.1 düzeltildi: 1.2→1.8 (gürültü koruması)
TP1_R        = _env_float("TP1_R", 1.5)   # v6.1 düzeltildi: 1.0→1.5
TP2_R        = _env_float("TP2_R", 2.5)   # v6.1 düzeltildi: 2.0→2.5
TP3_R        = _env_float("TP3_R", 4.0)   # runner hedef
MIN_SL_PCT   = _env_float("MIN_SL_PCT", 0.015)  # v6.1 düzeltildi: 0.008→0.015 (SL min %1.5)
# HUMAN MODE parametreleri (.env'den override edilir)
HUMAN_MODE              = _env_bool("HUMAN_MODE", False)
HUMAN_SL_ATR_MULT       = _env_float("HUMAN_SL_ATR_MULT", 2.0)
HUMAN_TP1_R             = _env_float("HUMAN_TP1_R", 1.5)
HUMAN_TP2_R             = _env_float("HUMAN_TP2_R", 2.5)
HUMAN_TRADE_THRESHOLD   = _env_float("HUMAN_TRADE_THRESHOLD", 72.0)
HUMAN_MAX_OPEN_TRADES   = _env_int("HUMAN_MAX_OPEN_TRADES", 2)

# TP Splits — toplamı 100 olmalı (test_config_tp_logic)
TP1_CLOSE_PCT    = _env_float("TP1_CLOSE_PCT", 40)   # 50→40: toplam=100
TP2_CLOSE_PCT    = _env_float("TP2_CLOSE_PCT", 30)   # 35→30: toplam=100
RUNNER_CLOSE_PCT = _env_float("RUNNER_CLOSE_PCT", 30) # 25→30: toplam=100

# Trailing
TRAIL_ATR_MULT       = _env_float("TRAIL_ATR_MULT", 1.5)
BREAKEVEN_ENABLED    = _env_bool("BREAKEVEN_ENABLED", True)
BREAKEVEN_OFFSET_PCT = _env_float("BREAKEVEN_OFFSET_PCT", 0.1)

# Paper / Timing
INITIAL_PAPER_BALANCE = _env_float("INITIAL_PAPER_BALANCE", 2000.0)
MAX_HOLD_MINUTES      = _env_int("MAX_HOLD_MINUTES", 240)

# Scan
SCAN_INTERVAL            = _env_int("SCAN_INTERVAL", 45)   # 60→45s
MIN_VOLUME_USDT          = _env_float("MIN_VOLUME_USDT", 3_000_000.0)
MIN_MOVE_PCT             = _env_float("MIN_MOVE_PCT", 0.3)
SCAN_INCLUDE_WATCH       = _env_bool("SCAN_INCLUDE_WATCH", True)
WATCHLIST_MIN_SCAN_SCORE = _env_float("WATCHLIST_MIN_SCAN_SCORE", 45.0)
MAX_COINS_PER_SCAN_LOOP  = _env_int("MAX_COINS_PER_SCAN_LOOP", 40)
MAX_DAILY_SIGNALS        = _env_int("MAX_DAILY_SIGNALS", 9999)   # bilgi amaçlı eski compat
DAILY_SIGNAL_LIMIT       = _env_int("DAILY_SIGNAL_LIMIT", 60)   # günlük hard limit
MAX_SIGNALS_PER_COIN     = _env_int("MAX_SIGNALS_PER_COIN", 3)  # coin başına günlük max

# Sinyal Eşikleri — DATA < WATCHLIST < TELEGRAM < TRADE sıralaması zorunlu
DATA_THRESHOLD      = _env_float("DATA_THRESHOLD", 20.0)
WATCHLIST_THRESHOLD = _env_float("WATCHLIST_THRESHOLD", 28.0) # 35→28: TELEGRAM'dan düşük olmalı
TELEGRAM_THRESHOLD  = _env_float("TELEGRAM_THRESHOLD", 35.0)  # v6.0 gevşetildi (28→35)
TRADE_THRESHOLD     = _env_float("TRADE_THRESHOLD", 55.0)     # v6.0 gevşetildi

# Circuit Breaker
CIRCUIT_BREAKER_LOSSES  = _env_int("CIRCUIT_BREAKER_LOSSES", 3)
CIRCUIT_BREAKER_MINUTES = _env_int("CIRCUIT_BREAKER_MINUTES", 60)

# Paper Tracking
PAPER_TRACK_REJECTED_CANDIDATES = _env_bool("PAPER_TRACK_REJECTED_CANDIDATES", True)
PAPER_TRACK_WATCHLIST           = _env_bool("PAPER_TRACK_WATCHLIST", True)
PAPER_TRACK_TELEGRAM_GAPS       = _env_bool("PAPER_TRACK_TELEGRAM_GAPS", True)
PAPER_TRACK_HORIZON_HOURS       = _env_float("PAPER_TRACK_HORIZON_HOURS", 6.0)

# Trigger Engine
ALLOWED_QUALITIES          = ["S", "A+", "A", "B", "C"]
EXECUTABLE_QUALITIES       = _env("EXECUTABLE_QUALITIES", "S,A+,A,B,C").split(",")
# London (08-12 UTC) + NY (13-17 UTC) = en aktif seanslar
GOOD_HOURS_UTC             = list(range(8, 22))   # 08-21 UTC (NY kapanışına kadar)
BAD_HOURS_UTC              = list(range(0, 6))    # 00-05 UTC (Asian close)
SESSION_FILTER_ENABLED     = _env_bool("SESSION_FILTER_ENABLED", True)
SESSION_SCORE_BONUS        = _env_float("SESSION_SCORE_BONUS", 5.0)
SESSION_SCORE_PENALTY      = _env_float("SESSION_SCORE_PENALTY", -5.0)
SHORT_REQUIRES_BTC_BEARISH = True
BTC_TREND_INTERVAL         = "4h"
ADX_MIN_THRESHOLD          = 18  # eskiden 20
MIN_ADX_5M_FILTER          = _env_float("MIN_ADX_5M_FILTER", 20.0)

# Scalp Filtreler (v9.0 — gevşetilmiş)
MIN_BB_WIDTH    = _env_float("MIN_BB_WIDTH", 0.8)      # v6.0 gevşetildi
MIN_ADX_15M     = _env_float("MIN_ADX_15M", 15)        # v6.0 gevşetildi
MIN_ADX_5M      = _env_float("MIN_ADX_5M", 12)         # v6.0 gevşetildi
FUNDING_LONG_MAX  = _env_float("FUNDING_LONG_MAX", 0.005)   # v6.0 gevşetildi
FUNDING_SHORT_MIN = _env_float("FUNDING_SHORT_MIN", -0.005)  # v6.0 gevşetildi

# Flask
FLASK_HOST = _env("FLASK_HOST", "0.0.0.0")
FLASK_PORT = _env_int("FLASK_PORT", 5000)

# Log
LOG_DIR      = _env("LOG_DIR", "logs")
LOG_MAX_DAYS = _env_int("LOG_MAX_DAYS", 7)
LOG_MAX_MB   = _env_int("LOG_MAX_MB", 50)

# Tüm Binance Futures USDT evrenini tara (boş = filtre yok)
# MIN_VOLUME_USDT kalite filtresi, COIN_UNIVERSE_LIMIT ise kaç coin pipeline'a girer
COIN_UNIVERSE = []

# Ghost Learning
GHOST_WEIGHT         = _env_float("GHOST_WEIGHT", 0.30)
GHOST_MIN_CONFIDENCE = _env_float("GHOST_MIN_CONFIDENCE", 0.40)

# Coin Library v2
COIN_MIN_VOLUME_USDT = _env_float("COIN_MIN_VOLUME_USDT", 10_000_000)
COIN_MIN_MOVE_PCT    = _env_float("COIN_MIN_MOVE_PCT", 0.5)
COIN_MIN_SCORE       = _env_float("COIN_MIN_SCORE", 40.0)
COIN_UNIVERSE_LIMIT  = _env_int("COIN_UNIVERSE_LIMIT", 150)

# Redis (hot-state cache — SQLite lock baskısını azaltır)
REDIS_ENABLED  = _env_bool("REDIS_ENABLED", True)
REDIS_HOST     = os.getenv("REDIS_HOST", "redis" if os.path.exists("/.dockerenv") else "127.0.0.1")
REDIS_PORT     = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB       = int(os.getenv("REDIS_DB", "0"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "") or None

# CVD / OI / Liquidity Zone parametreleri
CVD_ENABLED         = _env_bool("CVD_ENABLED", True)
OI_TRACKER_ENABLED  = _env_bool("OI_TRACKER_ENABLED", True)
OI_MIN_CHANGE_PCT   = _env_float("OI_MIN_CHANGE_PCT", 2.0)    # Anlamlı OI değişim eşiği
CVD_DIVERGENCE_BONUS= _env_float("CVD_DIVERGENCE_BONUS", 1.5) # CVD divergence bonusu
CVD_CONFIRM_BONUS   = _env_float("CVD_CONFIRM_BONUS", 1.0)    # CVD confirm bonusu

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

# ── Dynamic Configuration Bridging ───────────────────────────────────────────
_DYNAMIC_PARAMS_MAP = {
    "TRADE_THRESHOLD":      ("trade_threshold", float),
    "TELEGRAM_THRESHOLD":   ("telegram_threshold", float),
    "WATCHLIST_THRESHOLD":  ("watchlist_threshold", float),
    "DATA_THRESHOLD":       ("data_threshold", float),
    "HUMAN_MODE":           ("tg_human_mode", lambda v: v.strip().lower() in ("true", "1", "yes")),
    "EXECUTION_MODE":       ("tg_execution_mode", str),
}

def __getattr__(name: str) -> Any:
    """Modül düzeyinde dinamik parametreleri veritabanından okur."""
    if name in _DYNAMIC_PARAMS_MAP:
        try:
            db_key, cast_fn = _DYNAMIC_PARAMS_MAP[name]
            import sqlite3
            # Dairesel importu önlemek için raw connection:
            conn = sqlite3.connect(DB_PATH, timeout=5)
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT value FROM system_state WHERE key = ?", (db_key,)).fetchone()
            conn.close()
            if row and row["value"] is not None:
                return cast_fn(row["value"])
        except Exception:
            pass

    # Fallback to local globals
    if name in globals():
        return globals()[name]
    raise AttributeError(f"module {__name__} has no attribute {name}")
