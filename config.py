"""
config.py — AX Sistem Sabitleri v5.0 FINAL
==========================================
Scalp bot: öğrenen, büyüyen, crash-proof.
"""
import os
from dotenv import load_dotenv
load_dotenv()

# ── Mod ──────────────────────────────────────────────────────────────────────
AX_MODE        = os.getenv("AX_MODE", "execute")
EXECUTION_MODE = os.getenv("EXECUTION_MODE", "paper")
PAPER_MODE     = EXECUTION_MODE == "paper"

# ── API ──────────────────────────────────────────────────────────────────────
BINANCE_API_KEY    = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")

# ── Telegram ─────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8404489471:AAEU3uk-i_IWj4EcHXlf4Zt8-PkpIPAAc54")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "958182551")

# ── Risk Yonetimi ─────────────────────────────────────────────────────────────
RISK_PCT                = float(os.getenv("RISK_PCT", "1.0"))
MAX_OPEN_TRADES         = int(os.getenv("MAX_OPEN_TRADES", "15"))
DAILY_MAX_LOSS_PCT      = float(os.getenv("DAILY_MAX_LOSS_PCT", "5.0"))
CIRCUIT_BREAKER_LOSSES  = int(os.getenv("CIRCUIT_BREAKER_LOSSES", "5"))
CIRCUIT_BREAKER_MINUTES = int(os.getenv("CIRCUIT_BREAKER_MINUTES", "60"))
MAX_CORRELATED_TRADES   = int(os.getenv("MAX_CORRELATED_TRADES", "3"))

# ── Kaldirach ─────────────────────────────────────────────────────────────────
PAPER_LEVERAGE = int(os.getenv("PAPER_LEVERAGE", "10"))
MAX_LEVERAGE   = int(os.getenv("MAX_LEVERAGE", "20"))
MIN_LEVERAGE   = int(os.getenv("MIN_LEVERAGE", "5"))

# ── Sinyal Esikleri ───────────────────────────────────────────────────────────
DATA_THRESHOLD      = 30.0
WATCHLIST_THRESHOLD = 50.0
TELEGRAM_THRESHOLD  = 55.0
TRADE_THRESHOLD     = 65.0

# ── Filtreler ─────────────────────────────────────────────────────────────────
ADX_MIN_THRESHOLD          = 12
ALLOWED_QUALITIES          = ["S", "A+", "A", "B", "C"]
BAD_HOURS_UTC              = []
GOOD_HOURS_UTC             = list(range(24))
SHORT_REQUIRES_BTC_BEARISH = False
BTC_TREND_INTERVAL       = "4h"

# ── Tarama ───────────────────────────────────────────────────────────────────
SCAN_INTERVAL  = 60
MIN_VOLUME_USD = 3_000_000
COIN_UNIVERSE  = []
TOP_COINS_SCAN = 50
DB_PATH        = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trading.db")

# ── Execution Sabitleri ───────────────────────────────────────────────────────
TP1_CLOSE_PCT        = float(os.getenv("TP1_CLOSE_PCT",    "40"))
TP2_CLOSE_PCT        = float(os.getenv("TP2_CLOSE_PCT",    "40"))
RUNNER_CLOSE_PCT     = float(os.getenv("RUNNER_CLOSE_PCT", "20"))
TRAIL_ATR_MULT       = float(os.getenv("TRAIL_ATR_MULT",   "1.2"))
BREAKEVEN_ENABLED    = os.getenv("BREAKEVEN_ENABLED", "true").lower() == "true"
BREAKEVEN_OFFSET_PCT = float(os.getenv("BREAKEVEN_OFFSET_PCT", "0.03"))
ENABLE_LIVE_TRADING  = os.getenv("ENABLE_LIVE_TRADING", "false").lower() == "true"
TRADE_TIMEOUT_HOURS  = int(os.getenv("TRADE_TIMEOUT_HOURS", "12"))

# ── Ogrenme Parametreleri ─────────────────────────────────────────────────────
AI_MIN_TRADES_FOR_COIN_LEARNING = 3
AI_GHOST_HORIZON_MINUTES        = 360
AI_DANGER_SCORE_VETO_THRESHOLD  = 75
AI_MAX_DAILY_SIGNALS            = 150
