"""
config.py — AX Sistem Sabitleri v5.0 (LIVE-READY)
===================================================
Tüm yapılandırma değerleri .env dosyasından okunur.
Hardcoded token/API key YOKTUR.
"""
import os
from dotenv import load_dotenv
load_dotenv()

# ── Mod ──────────────────────────────────────────────────────────────────────
AX_MODE             = os.getenv("AX_MODE", "execute")
EXECUTION_MODE      = os.getenv("EXECUTION_MODE", "paper")
PAPER_MODE          = EXECUTION_MODE == "paper"
LIVE_TRADING_ENABLED = os.getenv("LIVE_TRADING_ENABLED", "False").lower() == "true"
DRY_RUN             = os.getenv("DRY_RUN", "True").lower() == "true"

# ── API ──────────────────────────────────────────────────────────────────────
BINANCE_API_KEY    = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")

# ── Telegram ─────────────────────────────────────────────────────────────────
# Token ve chat ID SADECE .env dosyasından okunur. Hardcoded default YOKTUR.
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Risk ─────────────────────────────────────────────────────────────────────
RISK_PCT                = float(os.getenv("RISK_PCT", "1.0"))
MAX_OPEN_TRADES         = int(os.getenv("MAX_OPEN_TRADES", "5"))
DAILY_MAX_LOSS_PCT      = float(os.getenv("DAILY_MAX_LOSS_PCT", "5.0"))
CIRCUIT_BREAKER_LOSSES  = int(os.getenv("CIRCUIT_BREAKER_LOSSES", "5"))
CIRCUIT_BREAKER_MINUTES = int(os.getenv("CIRCUIT_BREAKER_MINUTES", "60"))
MAX_CORRELATED_TRADES   = int(os.getenv("MAX_CORRELATED_TRADES", "3"))
MAX_LEVERAGE            = int(os.getenv("MAX_LEVERAGE", "20"))
MAX_CONSECUTIVE_LOSSES  = int(os.getenv("MAX_CONSECUTIVE_LOSSES", "5"))
COIN_COOLDOWN_MINUTES   = int(os.getenv("COIN_COOLDOWN_MINUTES", "60"))
MAX_MARGIN_LOSS_PCT     = float(os.getenv("MAX_MARGIN_LOSS_PCT", "0.40"))
DEFAULT_FEE_RATE        = float(os.getenv("DEFAULT_FEE_RATE", "0.0004"))

# ── TP / Partial Close ──────────────────────────────────────────────────────
TP1_CLOSE_PCT    = float(os.getenv("TP1_CLOSE_PCT", "40"))    # TP1'de kapanacak % miktar
TP2_CLOSE_PCT    = float(os.getenv("TP2_CLOSE_PCT", "30"))    # TP2'de kapanacak % miktar
RUNNER_CLOSE_PCT = float(os.getenv("RUNNER_CLOSE_PCT", "30")) # Runner / final kalan %

# ── Trailing Stop ────────────────────────────────────────────────────────────
TRAIL_ATR_MULT = float(os.getenv("TRAIL_ATR_MULT", "1.5"))

# ── Breakeven ────────────────────────────────────────────────────────────────
BREAKEVEN_ENABLED    = os.getenv("BREAKEVEN_ENABLED", "True").lower() == "true"
BREAKEVEN_OFFSET_PCT = float(os.getenv("BREAKEVEN_OFFSET_PCT", "0.05"))

# ── Sinyal Geçitleri ─────────────────────────────────────────────────────────
DATA_THRESHOLD      = float(os.getenv("DATA_THRESHOLD", "30.0"))
WATCHLIST_THRESHOLD = float(os.getenv("WATCHLIST_THRESHOLD", "50.0"))
TELEGRAM_THRESHOLD  = float(os.getenv("TELEGRAM_THRESHOLD", "60.0"))
TRADE_THRESHOLD     = float(os.getenv("TRADE_THRESHOLD", "70.0"))

# ── Filtreler ────────────────────────────────────────────────────────────────
ADX_MIN_THRESHOLD          = int(os.getenv("ADX_MIN_THRESHOLD", "20"))
ALLOWED_QUALITIES          = os.getenv("ALLOWED_QUALITIES", "S,A+,A,B,C").split(",")
BAD_HOURS_UTC              = []  # Tüm saatlere izin ver
GOOD_HOURS_UTC             = list(range(24))
SHORT_REQUIRES_BTC_BEARISH = os.getenv("SHORT_REQUIRES_BTC_BEARISH", "False").lower() == "true"
BTC_TREND_INTERVAL         = os.getenv("BTC_TREND_INTERVAL", "4h")

# ── Tarama ───────────────────────────────────────────────────────────────────
SCAN_INTERVAL  = int(os.getenv("SCAN_INTERVAL", "60"))
DB_PATH        = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trading.db")
MIN_VOLUME_USD = float(os.getenv("MIN_VOLUME_USD", "5000000"))
COIN_UNIVERSE  = []  # Boş = tüm coinleri tara (top limit yok)

# ── Paper Account ────────────────────────────────────────────────────────────
INITIAL_PAPER_BALANCE = float(os.getenv("INITIAL_PAPER_BALANCE", "250.0"))

# ── Max Hold ─────────────────────────────────────────────────────────────────
MAX_HOLD_MINUTES = int(os.getenv("MAX_HOLD_MINUTES", "240"))
