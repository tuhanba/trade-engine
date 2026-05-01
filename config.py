"""
config.py — AX Sistem Sabitleri v2.0
======================================
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
SL_ATR_MULT        = float(os.getenv("SL_ATR_MULT", "1.5"))
MIN_EXPECTED_MFE_R = float(os.getenv("MIN_EXPECTED_MFE_R", "1.0"))

# ── TP ───────────────────────────────────────────────────────────────────────
TP1_R            = float(os.getenv("TP1_R", "1.0"))
TP1_CLOSE_PCT    = float(os.getenv("TP1_CLOSE_PCT", "40"))
TP2_R            = float(os.getenv("TP2_R", "2.0"))
TP2_CLOSE_PCT    = float(os.getenv("TP2_CLOSE_PCT", "30"))
TP3_R            = float(os.getenv("TP3_R", "3.0"))
RUNNER_CLOSE_PCT = float(os.getenv("RUNNER_CLOSE_PCT", "30"))
TRAIL_ATR_MULT   = float(os.getenv("TRAIL_ATR_MULT", "1.0"))

# ── Sinyal Kalite Filtresi ───────────────────────────────────────────────────
# Backtest bulgusu: A+ kalite kârlı (PF=1.20, ROI=+20%), B kalite zararlı (PF=0.78)
ALLOWED_QUALITIES = os.getenv("ALLOWED_QUALITIES", "A+").split(",")

# ── Saat Filtresi (UTC) ──────────────────────────────────────────────────────
# Backtest bulgusu: Bu saatlerde win rate %13-22 — trade yasak
BAD_HOURS_UTC  = [int(h) for h in os.getenv("BAD_HOURS_UTC",  "5,6,14,20").split(",")]
# Backtest bulgusu: Bu saatlerde win rate %44-51 — risk bonusu uygulanır
GOOD_HOURS_UTC = [int(h) for h in os.getenv("GOOD_HOURS_UTC", "0,2,3,7,15").split(",")]

# ── Yön Filtresi ─────────────────────────────────────────────────────────────
# Backtest bulgusu: SHORT sinyalleri BULLISH piyasada -$399 zarar verdi
# True: SHORT için BTC 4H trend BEARISH olmalı; LONG için BULLISH olmalı
SHORT_REQUIRES_BTC_BEARISH = os.getenv("SHORT_REQUIRES_BTC_BEARISH", "true").lower() == "true"
BTC_TREND_INTERVAL         = os.getenv("BTC_TREND_INTERVAL", "4h")

# ── Sinyal Frekansı ──────────────────────────────────────────────────────────
MAX_DAILY_SIGNALS = int(os.getenv("MAX_DAILY_SIGNALS", "40"))
TARGET_DAILY_MIN  = int(os.getenv("TARGET_DAILY_MIN", "8"))   # A+ filtreli — daha az ama kaliteli
TARGET_DAILY_MAX  = int(os.getenv("TARGET_DAILY_MAX", "15"))  # A+ filtreli — daha az ama kaliteli

# ── Tarama ───────────────────────────────────────────────────────────────────
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "60"))   # saniye

# ── Veritabanı ───────────────────────────────────────────────────────────────
DB_PATH = os.getenv("DB_PATH", "/root/trade_engine/trading.db")

# ── Market Scanner Filtreler ─────────────────────────────────────────────────
MIN_VOLUME_USD     = float(os.getenv("MIN_VOLUME_USD", "10000000"))   # 10M
MIN_PRICE          = float(os.getenv("MIN_PRICE", "0.001"))
MAX_PRICE_CHANGE   = float(os.getenv("MAX_PRICE_CHANGE", "30.0"))    # pump/dump filtresi

# ── Coin Evreni (fallback) ───────────────────────────────────────────────────
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
