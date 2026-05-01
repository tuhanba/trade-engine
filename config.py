"""
config.py — AX Sistem Sabitleri v2.1
======================================
Tüm sabitler buradan okunur. Hiçbir dosya doğrudan os.getenv kullanmaz.
v2.1 Değişiklikleri (Backtest Bulgularına Dayalı):
  - AVAX ve SUI COIN_UNIVERSE'den çıkarıldı (WR<20%, PF<0.5)
  - SL_ATR_MULT: 1.5 → 1.2 (SL çıkışı -$1005, daha sıkı SL gerekli)
  - TP2_CLOSE_PCT: 30 → 50 (TP2 tüm kârın kaynağı: +$1186)
  - BAD_HOURS_UTC genişletildi: 13 yeni saat eklendi (WR<%25)
  - GOOD_HOURS_UTC güncellendi: 07, 17, 23, 00, 09 UTC (WR>%50)
  - BREAKEVEN_ENABLED: True (Max Loss serisi 17, Loss→Loss %80.2)
  - ADX_MIN_THRESHOLD: 28 (trend gücü filtresi)
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
DAILY_MAX_LOSS_PCT      = float(os.getenv("DAILY_MAX_LOSS_PCT", "5.0"))
CIRCUIT_BREAKER_LOSSES  = int(os.getenv("CIRCUIT_BREAKER_LOSSES", "3"))
CIRCUIT_BREAKER_MINUTES = int(os.getenv("CIRCUIT_BREAKER_MINUTES", "120"))

# ── Sinyal ───────────────────────────────────────────────────────────────────
MIN_RR             = float(os.getenv("MIN_RR", "1.5"))
# Backtest: SL çıkışı 106 trade, -$1005 zarar. Daha sıkı SL → daha küçük kayıp
SL_ATR_MULT        = float(os.getenv("SL_ATR_MULT", "1.2"))   # 1.5 → 1.2
MIN_EXPECTED_MFE_R = float(os.getenv("MIN_EXPECTED_MFE_R", "1.0"))

# ── ADX Trend Gücü Filtresi ──────────────────────────────────────────────────
# Backtest: ADX < 25 olan sinyallerde WR düşük, trend gücü zayıf
ADX_MIN_THRESHOLD = int(os.getenv("ADX_MIN_THRESHOLD", "28"))

# ── TP ───────────────────────────────────────────────────────────────────────
TP1_R            = float(os.getenv("TP1_R", "1.0"))
TP1_CLOSE_PCT    = float(os.getenv("TP1_CLOSE_PCT", "30"))    # 40 → 30 (TP2'ye daha fazla bırak)
TP2_R            = float(os.getenv("TP2_R", "2.0"))
# Backtest: TP2 çıkışı tüm kârın kaynağı (+$1186). Oranı artır.
TP2_CLOSE_PCT    = float(os.getenv("TP2_CLOSE_PCT", "50"))    # 30 → 50
TP3_R            = float(os.getenv("TP3_R", "3.0"))
RUNNER_CLOSE_PCT = float(os.getenv("RUNNER_CLOSE_PCT", "20")) # 30 → 20 (daha az runner)
TRAIL_ATR_MULT   = float(os.getenv("TRAIL_ATR_MULT", "1.0"))

# ── Breakeven Mekanizması ─────────────────────────────────────────────────────
# Backtest: Max Loss serisi 17, Loss→Loss %80.2. TP1 sonrası SL entry'e çek.
BREAKEVEN_ENABLED    = os.getenv("BREAKEVEN_ENABLED", "true").lower() == "true"
BREAKEVEN_TRIGGER_R  = float(os.getenv("BREAKEVEN_TRIGGER_R", "1.0"))  # TP1 tetiklenince
BREAKEVEN_OFFSET_PCT = float(os.getenv("BREAKEVEN_OFFSET_PCT", "0.05")) # entry + %0.05 buffer

# ── Sinyal Kalite Filtresi ───────────────────────────────────────────────────
# Backtest: A+ kalite kârlı (PF=1.20, ROI=+20%), B kalite zararlı (PF=0.78)
ALLOWED_QUALITIES = os.getenv("ALLOWED_QUALITIES", "A+").split(",")

# ── Saat Filtresi (UTC) ──────────────────────────────────────────────────────
# Backtest genişletilmiş analiz: WR<%25 olan tüm saatler engellendi
# Orijinal: [5,6,14,20] → Genişletilmiş: 13 saat
BAD_HOURS_UTC = [int(h) for h in os.getenv(
    "BAD_HOURS_UTC",
    "1,4,5,6,10,11,12,13,14,16,19,20,21,22"   # Backtest WR<%25
).split(",")]

# Backtest: En iyi saatler — WR>%50
GOOD_HOURS_UTC = [int(h) for h in os.getenv(
    "GOOD_HOURS_UTC",
    "0,3,7,9,17,23"   # WR: %55, %48, %80, %56, %100, %62
).split(",")]

# ── Yön Filtresi ─────────────────────────────────────────────────────────────
# Backtest: SHORT sinyalleri BULLISH piyasada -$399 zarar verdi
SHORT_REQUIRES_BTC_BEARISH = os.getenv("SHORT_REQUIRES_BTC_BEARISH", "true").lower() == "true"
BTC_TREND_INTERVAL         = os.getenv("BTC_TREND_INTERVAL", "4h")

# ── Sinyal Frekansı ──────────────────────────────────────────────────────────
MAX_DAILY_SIGNALS = int(os.getenv("MAX_DAILY_SIGNALS", "40"))
TARGET_DAILY_MIN  = int(os.getenv("TARGET_DAILY_MIN", "5"))   # Saat filtresi sonrası daha az
TARGET_DAILY_MAX  = int(os.getenv("TARGET_DAILY_MAX", "12"))  # Kalite > Miktar

# ── Tarama ───────────────────────────────────────────────────────────────────
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "60"))   # saniye

# ── Veritabanı ───────────────────────────────────────────────────────────────
DB_PATH = os.getenv("DB_PATH", "/root/trade_engine/trading.db")

# ── Market Scanner Filtreler ─────────────────────────────────────────────────
MIN_VOLUME_USD     = float(os.getenv("MIN_VOLUME_USD", "10000000"))   # 10M
MIN_PRICE          = float(os.getenv("MIN_PRICE", "0.001"))
MAX_PRICE_CHANGE   = float(os.getenv("MAX_PRICE_CHANGE", "30.0"))    # pump/dump filtresi

# ── Coin Evreni ──────────────────────────────────────────────────────────────
# v2.3: 207 coin backtest ile genişletildi — 92 coin, PF ≥ 1.0, ≥5 trade
# Test tarihi: 2026-05-01 | BTC 4H: BULLISH | Paralel backtest
COIN_UNIVERSE = [
    "ALLOUSDT", "DYDXUSDT", "SNDKUSDT", "FLUIDUSDT", "ZEREBROUSDT",
    "HOODUSDT", "GWEIUSDT", "REZUSDT", "TACUSDT", "ARBUSDT",
    "ADAUSDT", "EIGENUSDT", "1000FLOKIUSDT", "1000SHIBUSDT", "FARTCOINUSDT",
    "AEROUSDT", "EWYUSDT", "FLOWUSDT", "PENDLEUSDT", "BZUSDT",
    "BASUSDT", "TIAUSDT", "PIPPINUSDT", "OPUSDT", "BCHUSDT",
    "XPTUSDT", "MASKUSDT", "UNIUSDT", "SIRENUSDT", "DASHUSDT",
    "MAGMAUSDT", "MSFTUSDT", "CRCLUSDT", "ENAUSDT", "ICPUSDT",
    "STRKUSDT", "WLFIUSDT", "MOODENGUSDT", "CLUSDT", "AIOTUSDT",
    "1000PEPEUSDT", "SPXUSDT", "SOONUSDT", "TRADOORUSDT", "XAGUSDT",
    "AIGENSYNUSDT", "WIFUSDT", "MSTRUSDT", "DRIFTUSDT", "RAYSOLUSDT",
    "ETHUSDT", "HUSDT", "USTCUSDT", "COMPUSDT", "BIOUSDT",
    "APTUSDT", "CHZUSDT", "AXSUSDT", "NEIROUSDT", "BLUAIUSDT",
    "CRVUSDT", "RENDERUSDT", "NAORISUSDT", "BEATUSDT", "DOTUSDT",
    "CGPTUSDT", "TAOUSDT", "PIEVERSEUSDT", "LINEAUSDT", "SKYAIUSDT",
    "AXLUSDT", "PUMPUSDT", "ARCUSDT", "POLUSDT", "ACHUSDT",
    "HBARUSDT", "ASTERUSDT", "BSBUSDT", "ZBTUSDT", "BERAUSDT",
    "FILUSDT", "PENGUUSDT", "1000BONKUSDT", "CFXUSDT", "ETHFIUSDT",
    "AIXBTUSDT", "B2USDT", "HUMAUSDT", "ATOMUSDT", "LITUSDT",
    "ZECUSDT", "GOOGLUSDT",
]

# ── Log ──────────────────────────────────────────────────────────────────────
LOG_DIR      = os.getenv("LOG_DIR", "/root/trade_engine/logs")
LOG_MAX_DAYS = int(os.getenv("LOG_MAX_DAYS", "7"))
LOG_MAX_MB   = int(os.getenv("LOG_MAX_MB", "50"))
