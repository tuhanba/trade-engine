"""
config.py — AX Tek Merkez Konfigürasyon
========================================
Tüm sistem parametreleri buradan okunur.
.env dosyası yoksa güvenli varsayılanlar kullanılır.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────────
# TEMEL MODLAR
# ─────────────────────────────────────────────
AX_MODE        = os.getenv("AX_MODE",        "execute")   # execute | observe
EXECUTION_MODE = os.getenv("EXECUTION_MODE", "paper")     # paper | live

# ─────────────────────────────────────────────
# BAĞLANTI
# ─────────────────────────────────────────────
BINANCE_API_KEY    = os.getenv("BINANCE_API_KEY",    "")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")
BINANCE_TESTNET    = os.getenv("BINANCE_TESTNET", "false").lower() == "true"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID",   "")

N8N_BASE_URL           = os.getenv("N8N_BASE_URL",           "http://localhost:5678")
N8N_SECRET             = os.getenv("N8N_SECRET",             "aurvex-ax-secret")
N8N_WEBHOOK_TRADE_OPEN  = os.getenv("N8N_WEBHOOK_TRADE_OPEN",  f"{N8N_BASE_URL}/webhook/ax-trade-open")
N8N_WEBHOOK_TRADE_CLOSE = os.getenv("N8N_WEBHOOK_TRADE_CLOSE", f"{N8N_BASE_URL}/webhook/ax-trade-close")
N8N_WEBHOOK_AI_REPORT   = os.getenv("N8N_WEBHOOK_AI_REPORT",   f"{N8N_BASE_URL}/webhook/ax-ai-report")
N8N_WEBHOOK_ALERT       = os.getenv("N8N_WEBHOOK_ALERT",       f"{N8N_BASE_URL}/webhook/ax-alert")

# ─────────────────────────────────────────────
# VERİTABANI
# ─────────────────────────────────────────────
DB_PATH = os.getenv(
    "DB_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "trading.db")
)

# ─────────────────────────────────────────────
# RİSK PARAMETRELERİ
# ─────────────────────────────────────────────
RISK_PCT           = float(os.getenv("RISK_PCT",           "1.0"))
MAX_OPEN_TRADES    = int(os.getenv("MAX_OPEN_TRADES",       "2"))
DAILY_MAX_LOSS_PCT = float(os.getenv("DAILY_MAX_LOSS_PCT", "3.0"))
MIN_BALANCE_USDT   = float(os.getenv("MIN_BALANCE_USDT",   "10.0"))

# ─────────────────────────────────────────────
# CIRCUIT BREAKER
# ─────────────────────────────────────────────
CIRCUIT_BREAKER_LOSSES  = int(os.getenv("CIRCUIT_BREAKER_LOSSES",  "3"))
CIRCUIT_BREAKER_MINUTES = int(os.getenv("CIRCUIT_BREAKER_MINUTES", "120"))

# ─────────────────────────────────────────────
# SİNYAL / GİRİŞ FİLTRELERİ
# ─────────────────────────────────────────────
MIN_RR             = float(os.getenv("MIN_RR",             "1.5"))
MIN_EXPECTED_MFE_R = float(os.getenv("MIN_EXPECTED_MFE_R", "1.0"))
MIN_VOLUME_M       = float(os.getenv("MIN_VOLUME_M",       "10.0"))   # milyon USDT
MIN_CHANGE_PCT     = float(os.getenv("MIN_CHANGE_PCT",     "1.5"))    # 24h hareket

# ─────────────────────────────────────────────
# SL / TP PARAMETRELERİ
# ─────────────────────────────────────────────
SL_ATR_MULT      = float(os.getenv("SL_ATR_MULT",      "1.3"))

TP1_R            = float(os.getenv("TP1_R",            "0.9"))   # TP1 hedef (R cinsinden)
TP1_CLOSE_PCT    = float(os.getenv("TP1_CLOSE_PCT",    "40"))    # TP1'de pozisyonun %40'ı kapanır
TP2_R            = float(os.getenv("TP2_R",            "1.5"))   # TP2 hedef (R cinsinden)
TP2_CLOSE_PCT    = float(os.getenv("TP2_CLOSE_PCT",    "30"))    # TP2'de pozisyonun %30'u kapanır
RUNNER_CLOSE_PCT = float(os.getenv("RUNNER_CLOSE_PCT", "30"))    # Runner: kalan %30

TRAIL_ATR_MULT   = float(os.getenv("TRAIL_ATR_MULT",   "1.0"))  # Trailing stop ATR çarpanı

# ─────────────────────────────────────────────
# HEDEF PERFORMANS BANDI
# ─────────────────────────────────────────────
TARGET_WIN_RATE_MIN = float(os.getenv("TARGET_WIN_RATE_MIN", "0.45"))
TARGET_WIN_RATE_MAX = float(os.getenv("TARGET_WIN_RATE_MAX", "0.55"))
TARGET_RR           = float(os.getenv("TARGET_RR",           "1.5"))

# ─────────────────────────────────────────────
# BOT DÖNGÜSÜ
# ─────────────────────────────────────────────
SCAN_INTERVAL_SEC    = int(os.getenv("SCAN_INTERVAL_SEC",    "30"))   # tarama aralığı
HEALTH_CHECK_SEC     = int(os.getenv("HEALTH_CHECK_SEC",     "60"))   # health check aralığı
AI_BRAIN_INTERVAL    = int(os.getenv("AI_BRAIN_INTERVAL",    "3600")) # AI analiz aralığı (sn)
PAPER_BALANCE_START  = float(os.getenv("PAPER_BALANCE_START", "250.0"))

# ─────────────────────────────────────────────
# PAPER EXECUTION AYARLARI
# ─────────────────────────────────────────────
PAPER_FEE_RATE   = float(os.getenv("PAPER_FEE_RATE",   "0.0004"))  # %0.04 taker fee
PAPER_SLIPPAGE   = float(os.getenv("PAPER_SLIPPAGE",   "0.0002"))  # %0.02 slippage

# ─────────────────────────────────────────────
# DASHBOARD
# ─────────────────────────────────────────────
DASHBOARD_HOST = os.getenv("DASHBOARD_HOST", "0.0.0.0")
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "5000"))
DASHBOARD_DEBUG = os.getenv("DASHBOARD_DEBUG", "false").lower() == "true"
