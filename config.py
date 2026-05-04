"""
config.py — AX Sistem Sabitleri v4.0 (ULTIMATE ELITE)
======================================================
Filtreler esnetildi, trade frekansı artırıldı.
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

# ── Risk ─────────────────────────────────────────────────────────────────────
RISK_PCT                = float(os.getenv("RISK_PCT", "1.0"))
MAX_OPEN_TRADES         = int(os.getenv("MAX_OPEN_TRADES", "5")) # 2 -> 5
DAILY_MAX_LOSS_PCT      = float(os.getenv("DAILY_MAX_LOSS_PCT", "5.0"))
CIRCUIT_BREAKER_LOSSES  = int(os.getenv("CIRCUIT_BREAKER_LOSSES", "5"))
CIRCUIT_BREAKER_MINUTES = int(os.getenv("CIRCUIT_BREAKER_MINUTES", "60"))
MAX_CORRELATED_TRADES   = int(os.getenv("MAX_CORRELATED_TRADES", "3"))
MAX_LEVERAGE            = int(os.getenv("MAX_LEVERAGE", "20"))
PAPER_LEVERAGE          = int(os.getenv("PAPER_LEVERAGE", "10"))   # Paper trade simule kaldiraci (default 10x)

# ── Sinyal Geçitleri (ESNETİLDİ) ──────────────────────────────────────────────
DATA_THRESHOLD      = 30.0 # 45 -> 30
WATCHLIST_THRESHOLD = 50.0 # 65 -> 50
TELEGRAM_THRESHOLD  = 60.0 # 75 -> 60
TRADE_THRESHOLD     = 70.0 # 82 -> 70

# ── Filtreler ────────────────────────────────────────────────────────────────
ADX_MIN_THRESHOLD = 20 # 28 -> 20
ALLOWED_QUALITIES = ["S", "A+", "A", "B", "C"] # C eklendi
BAD_HOURS_UTC = [] # Tüm saatlere izin ver
GOOD_HOURS_UTC = list(range(24))
SHORT_REQUIRES_BTC_BEARISH = False # BTC trendine bakma

# ── Tarama ───────────────────────────────────────────────────────────────────
SCAN_INTERVAL = 60
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trading.db")
MIN_VOLUME_USD = 5000000 # 10M -> 5M
COIN_UNIVERSE = [] # Tüm coinleri tara

# ── Execution Sabitleri ───────────────────────────────────────────────────────
TP1_CLOSE_PCT        = float(os.getenv("TP1_CLOSE_PCT", "40"))    # TP1'de kapatılacak % miktar
TP2_CLOSE_PCT        = float(os.getenv("TP2_CLOSE_PCT", "40"))    # TP2'de kapatılacak % miktar
RUNNER_CLOSE_PCT     = float(os.getenv("RUNNER_CLOSE_PCT", "20")) # Runner % miktar
TRAIL_ATR_MULT       = float(os.getenv("TRAIL_ATR_MULT", "1.5"))  # Trailing stop ATR çarpanı
BREAKEVEN_ENABLED    = os.getenv("BREAKEVEN_ENABLED", "true").lower() == "true"
BREAKEVEN_OFFSET_PCT = float(os.getenv("BREAKEVEN_OFFSET_PCT", "0.05"))
ENABLE_LIVE_TRADING  = os.getenv("ENABLE_LIVE_TRADING", "false").lower() == "true"
