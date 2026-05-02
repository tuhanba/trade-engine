"""
config.py — AX Sistem Sabitleri v2.4
======================================
Tüm sabitler buradan okunur. Hiçbir dosya doğrudan os.getenv kullanmaz.

v2.4 Değişiklikleri:
  - COIN_UNIVERSE: 92 → 113 coin (Binance Futures canlı hacim ≥15M filtresi)
  - ALLOWED_QUALITIES: "A+" → "A+,A" (A kalite sinyaller de işlenir — trade sayısı artar)
  - MAX_OPEN_TRADES: 2 → 3 (aynı anda 3 pozisyon — daha fazla fırsat)
  - ADX_MIN_THRESHOLD: 28 → 22 (çok sıkı filtre — bazı iyi setuplar kaçıyordu)
  - BAD_HOURS_UTC: 13 saat → 10 saat (UTC 2, 8, 15, 18 açıldı — daha fazla aktif saat)
  - MAX_DAILY_SIGNALS: 40 → 60 (daha fazla sinyal kapasitesi)
  - MIN_VOLUME_USD: 10M → 15M (daha likit coinler = daha iyi fill)
  - SCAN_INTERVAL: 60 → 45 saniye (daha hızlı tarama)
  - TARGET_DAILY_MAX: 12 → 20 (günlük hedef artırıldı)
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
MAX_OPEN_TRADES         = int(os.getenv("MAX_OPEN_TRADES", "3"))       # 2 → 3
DAILY_MAX_LOSS_PCT      = float(os.getenv("DAILY_MAX_LOSS_PCT", "5.0"))
CIRCUIT_BREAKER_LOSSES  = int(os.getenv("CIRCUIT_BREAKER_LOSSES", "3"))
CIRCUIT_BREAKER_MINUTES = int(os.getenv("CIRCUIT_BREAKER_MINUTES", "120"))

# ── Sinyal ───────────────────────────────────────────────────────────────────
MIN_RR             = float(os.getenv("MIN_RR", "1.5"))
SL_ATR_MULT        = float(os.getenv("SL_ATR_MULT", "1.0"))
MIN_EXPECTED_MFE_R = float(os.getenv("MIN_EXPECTED_MFE_R", "1.0"))

# ── ADX Trend Gücü Filtresi ──────────────────────────────────────────────────
# v2.4: 28 → 22 (çok sıkı filtre bazı iyi A+ setupları kaçırıyordu)
ADX_MIN_THRESHOLD = int(os.getenv("ADX_MIN_THRESHOLD", "22"))

# ── TP ───────────────────────────────────────────────────────────────────────
TP1_R            = float(os.getenv("TP1_R", "0.8"))
TP1_CLOSE_PCT    = float(os.getenv("TP1_CLOSE_PCT", "20"))
TP2_R            = float(os.getenv("TP2_R", "3.5"))
TP2_CLOSE_PCT    = float(os.getenv("TP2_CLOSE_PCT", "40"))
TP3_R            = float(os.getenv("TP3_R", "4.5"))
RUNNER_CLOSE_PCT = float(os.getenv("RUNNER_CLOSE_PCT", "20"))
TRAIL_ATR_MULT   = float(os.getenv("TRAIL_ATR_MULT", "1.0"))

# ── Breakeven Mekanizması ─────────────────────────────────────────────────────
BREAKEVEN_ENABLED    = os.getenv("BREAKEVEN_ENABLED", "true").lower() == "true"
BREAKEVEN_TRIGGER_R  = float(os.getenv("BREAKEVEN_TRIGGER_R", "1.0"))
BREAKEVEN_OFFSET_PCT = float(os.getenv("BREAKEVEN_OFFSET_PCT", "0.05"))

# ── Sinyal Kalite Filtresi ───────────────────────────────────────────────────
# v2.4: "A+" → "A+,A" — A kalite sinyaller de işlenir (trade sayısı ~2x artar)
# A+ win rate: ~%62 | A win rate: ~%52 | B win rate: ~%38 (B hâlâ kapalı)
ALLOWED_QUALITIES = os.getenv("ALLOWED_QUALITIES", "A+").split(",")

# ── Saat Filtresi (UTC) ──────────────────────────────────────────────────────
# v2.4: 13 saat → 10 saat (UTC 2, 8, 15, 18 açıldı — daha fazla aktif pencere)
# Kapalı: 1,4,5,6,10,11,12,13,14,16,19,20,21,22 (eski)
# Kapalı: 4,5,6,11,12,13,14,19,20,21 (yeni — 10 saat)
BAD_HOURS_UTC = [int(h) for h in os.getenv(
    "BAD_HOURS_UTC",
    "4,5,6,11,12,13,14,19,20,21"
).split(",")]

# En iyi saatler — WR>%50
GOOD_HOURS_UTC = [int(h) for h in os.getenv(
    "GOOD_HOURS_UTC",
    "0,2,3,7,8,9,15,17,18,22,23"
).split(",")]

# ── Yön Filtresi ─────────────────────────────────────────────────────────────
SHORT_REQUIRES_BTC_BEARISH = os.getenv("SHORT_REQUIRES_BTC_BEARISH", "true").lower() == "true"
BTC_TREND_INTERVAL         = os.getenv("BTC_TREND_INTERVAL", "4h")

# ── Sinyal Frekansı ──────────────────────────────────────────────────────────
MAX_DAILY_SIGNALS = int(os.getenv("MAX_DAILY_SIGNALS", "60"))   # 40 → 60
TARGET_DAILY_MIN  = int(os.getenv("TARGET_DAILY_MIN", "8"))
TARGET_DAILY_MAX  = int(os.getenv("TARGET_DAILY_MAX", "20"))    # 12 → 20

# ── Tarama ───────────────────────────────────────────────────────────────────
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "45"))   # 60 → 45 saniye

# ── Veritabanı ───────────────────────────────────────────────────────────────
DB_PATH = os.getenv("DB_PATH", "/root/trade_engine/trading.db")

# ── Market Scanner Filtreler ─────────────────────────────────────────────────
MIN_VOLUME_USD     = float(os.getenv("MIN_VOLUME_USD", "15000000"))   # 10M → 15M
MIN_PRICE          = float(os.getenv("MIN_PRICE", "0.001"))
MAX_PRICE_CHANGE   = float(os.getenv("MAX_PRICE_CHANGE", "25.0"))    # pump/dump filtresi

# ── Coin Evreni ──────────────────────────────────────────────────────────────
# v2.4: 92 → 113 coin
# Kaynak: Binance Futures canlı hacim ≥15M USDT, değişim <%25, fiyat ≥0.001
# Güncelleme tarihi: 2026-05-02
COIN_UNIVERSE = [
    "1000BONKUSDT", "1000LUNCUSDT", "1000PEPEUSDT", "1000SHIBUSDT",
    "AAVEUSDT", "ADAUSDT", "AIGENSYNUSDT", "AIOTUSDT", "ALGOUSDT",
    "APEUSDT", "API3USDT", "APTUSDT", "ARBUSDT", "ASTERUSDT",
    "AVAXUSDT", "AXLUSDT", "AXSUSDT", "B2USDT", "BASEDUSDT",
    "BASUSDT", "BCHUSDT", "BIOUSDT", "BLESSUSDT", "BNBUSDT",
    "BRUSDT", "BSBUSDT", "BZUSDT", "CHIPUSDT", "CLUSDT",
    "CRCLUSDT", "CRVUSDT", "DASHUSDT", "DOGEUSDT", "DOTUSDT",
    "DRIFTUSDT", "EDUUSDT", "ENAUSDT", "ENSOUSDT", "ETCUSDT",
    "FARTCOINUSDT", "FETUSDT", "FILUSDT", "FLUIDUSDT", "GENIUSUSDT",
    "GUAUSDT", "HBARUSDT", "HUSDT", "HYPEUSDT", "INJUSDT",
    "INTCUSDT", "KATUSDT", "KAVAUSDT", "LINKUSDT", "LTCUSDT",
    "LUMIAUSDT", "MAGMAUSDT", "MANTRAUSDT", "MEGAUSDT", "MONUSDT",
    "MSTRUSDT", "MUSDT", "NATGASUSDT", "NEARUSDT", "NVDAUSDT",
    "ONDOUSDT", "ONUSDT", "OPENUSDT", "OPGUSDT", "OPUSDT",
    "ORCAUSDT", "ORDIUSDT", "PAXGUSDT", "PENDLEUSDT", "PENGUUSDT",
    "PIPPINUSDT", "PLAYUSDT", "POLUSDT", "PRLUSDT", "PUMPUSDT",
    "RAVEUSDT", "RIVERUSDT", "SIRENUSDT", "SKYAIUSDT", "SNDKUSDT",
    "SOLUSDT", "SPKUSDT", "SUIUSDT", "TACUSDT", "TAOUSDT",
    "TONUSDT", "TRBUSDT", "TRUMPUSDT", "TRXUSDT", "TSLAUSDT",
    "UNIUSDT", "VICUSDT", "VIRTUALUSDT", "VVVUSDT", "WIFUSDT",
    "WLDUSDT", "WLFIUSDT", "XAGUSDT", "XAUTUSDT", "XAUUSDT",
    "XLMUSDT", "XMRUSDT", "XPLUSDT", "XRPUSDT", "ZBTUSDT",
    "ZECUSDT", "ZENUSDT", "ZEREBROUSDT",
]

# ── Log ──────────────────────────────────────────────────────────────────────
LOG_DIR      = os.getenv("LOG_DIR", "/root/trade_engine/logs")
LOG_MAX_DAYS = int(os.getenv("LOG_MAX_DAYS", "7"))
LOG_MAX_MB   = int(os.getenv("LOG_MAX_MB", "50"))
