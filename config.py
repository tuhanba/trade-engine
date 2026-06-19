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

def _env(key, default=""):
    val = os.getenv(key, default).strip()
    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
        val = val[1:-1].strip()
    return val
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
_db_env = _env("DB_PATH")
if _db_env:
    DB_PATH = str(BASE_DIR / _db_env)
else:
    if (BASE_DIR / "trading.db").exists():
        DB_PATH = str(BASE_DIR / "trading.db")
    elif (BASE_DIR / "db" / "trading.db").exists():
        DB_PATH = str(BASE_DIR / "db" / "trading.db")
    else:
        DB_PATH = str(BASE_DIR / "trading.db")

# PostgreSQL Config
POSTGRES_ENABLED = _env_bool("POSTGRES_ENABLED", False)
POSTGRES_HOST = _env("POSTGRES_HOST", "postgres")
POSTGRES_PORT = _env_int("POSTGRES_PORT", 5432)
POSTGRES_DB = _env("POSTGRES_DB", "aurvex")
POSTGRES_USER = _env("POSTGRES_USER", "aurvex")
POSTGRES_PASSWORD = _env("POSTGRES_PASSWORD", "aurvexpass")

# Execution
EXECUTION_MODE          = _env("EXECUTION_MODE", "paper")
AX_MODE                 = _env("AX_MODE", "execute")
PAPER_MODE              = _env_bool("PAPER_MODE", True)
LIVE_TRADING_ENABLED    = _env_bool("LIVE_TRADING_ENABLED", False)
DRY_RUN                 = _env_bool("DRY_RUN", True)
CONFIRM_LIVE_TRADING    = _env_bool("CONFIRM_LIVE_TRADING", False)
LIVE_CONFIRM            = _env_bool("LIVE_CONFIRM", False)
USE_BINANCE_PRIVATE_API = _env_bool("USE_BINANCE_PRIVATE_API", False)
CANARY_MODE             = _env_bool("CANARY_MODE", False)
# NEDEN (P0-2, directive Section 20): Otonom self-healing Optuna dongusu trade
# parametrelerini (rsi_limit/cvd_filter_val) VARSAYILAN olarak UYGULAMAZ —
# report-first. Acilirsa bile her degisiklik param_gate (backtest kaniti)
# onayindan gecer. Kanit birikene kadar kapali kalir; fail-safe.
SELF_HEALING_AUTO_APPLY = _env_bool("SELF_HEALING_AUTO_APPLY", False)

# ── SCALP MODE (mode-gate) ────────────────────────────────────────────────────
# NEDEN (CLAUDE_FIX_SCALP_SIMPLIFY): Sistemi "her şeyi yapmaya çalışan, hiç trade
# etmeyen" yapıdan sade/hızlı gerçek scalp engine'e döndüren ANA bayrak. Beyin
# SİLİNMEZ, KAPATILIR: SCALP_MODE=True iken çelişen filtreler/otonom eşik
# oynamaları/optimizasyon döngüleri devre dışı kalır ve scalp parametreleri
# (kısa hold, tight SL, düşük TRADE_THRESHOLD) uygulanır. Tam geri dönüş için
# `SCALP_MODE=false` (env) — eski "brain" davranışı aynen geri gelir.
SCALP_MODE = _env_bool("SCALP_MODE", True)

# API Keys
BINANCE_API_KEY    = _env("BINANCE_API_KEY")
BINANCE_API_SECRET = _env("BINANCE_API_SECRET")
TELEGRAM_BOT_TOKEN = _env("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = _env("TELEGRAM_CHAT_ID")
SECRET_KEY         = _env("SECRET_KEY", "ax_secret_prod_2026")
ANTHROPIC_API_KEY  = _env("ANTHROPIC_API_KEY")
GEMINI_API_KEY     = _env("GEMINI_API_KEY")
OLLAMA_API_BASE    = _env("OLLAMA_API_BASE", "http://localhost:11434/v1")
FRIDAY_LLM_PROVIDER = _env("FRIDAY_LLM_PROVIDER", "auto")
FRIDAY_LLM_MODE = _env("FRIDAY_LLM_MODE", "offline")  # offline | report_only | full
FRIDAY_LLM_DAILY_BUDGET = int(_env("FRIDAY_LLM_DAILY_BUDGET", "5"))

# Risk & Compounding
AUTO_COMPOUNDING           = _env_bool("AUTO_COMPOUNDING", True)
BASE_ACCOUNT_SIZE          = _env_float("BASE_ACCOUNT_SIZE", 1000.0) # Eger auto-compounding kapaliysa baz alinacak bakiye
RISK_PCT                   = _env_float("RISK_PCT", 0.5 if SCALP_MODE else 0.75)   # NEDEN: scalp = yüksek frekans, trade başı risk düşük
MAX_OPEN_TRADES            = _env_int("MAX_OPEN_TRADES", 5)
DAILY_MAX_LOSS_PCT         = _env_float("DAILY_MAX_LOSS_PCT", 5.0)
MAX_LEVERAGE               = _env_int("MAX_LEVERAGE", 10)
MAX_CHASE_PCT              = _env_float("MAX_CHASE_PCT", 0.15)
DEFAULT_FEE_RATE           = _env_float("DEFAULT_FEE_RATE", 0.0004)
MAX_CONSECUTIVE_LOSSES     = _env_int("MAX_CONSECUTIVE_LOSSES", 5)
COIN_COOLDOWN_MINUTES      = _env_int("COIN_COOLDOWN_MINUTES", 30)
COOLDOWN_WIN_BASE_MINUTES  = _env_float("COOLDOWN_WIN_BASE_MINUTES", 15.0)
COOLDOWN_LOSS_BASE_MINUTES = _env_float("COOLDOWN_LOSS_BASE_MINUTES", 60.0)
MAX_CORRELATED_TRADES      = _env_int("MAX_CORRELATED_TRADES", 3)
MAX_MARGIN_LOSS_PCT        = _env_float("MAX_MARGIN_LOSS_PCT", 0.40)
MAX_PORTFOLIO_EXPOSURE_PCT = _env_float("MAX_PORTFOLIO_EXPOSURE_PCT", 95.0)
MIN_RR                     = _env_float("MIN_RR", 1.1 if SCALP_MODE else 1.5)   # scalp: daha sık kazanır, R küçük
MIN_EXPECTED_MFE_R         = _env_float("MIN_EXPECTED_MFE_R", 0.8 if SCALP_MODE else 1.2)

# Unified Capital & Risk Protection Package Settings
DRAWDOWN_DEFENSIVE_PCT         = _env_float("DRAWDOWN_DEFENSIVE_PCT", 5.0)
DRAWDOWN_LOCK_PCT              = _env_float("DRAWDOWN_LOCK_PCT", 10.0)
EQUITY_CURVE_EMA_PERIOD        = _env_int("EQUITY_CURVE_EMA_PERIOD", 10)
EQUITY_CURVE_RISK_REDUCTION    = _env_float("EQUITY_CURVE_RISK_REDUCTION", 0.5)
EQUITY_CURVE_FILTER_ENABLED    = _env_bool("EQUITY_CURVE_FILTER_ENABLED", not SCALP_MODE)   # scalp: kapalı
MTF_TREND_ALIGN_RISK_REDUCTION = _env_float("MTF_TREND_ALIGN_RISK_REDUCTION", 0.4)
MTF_TREND_ALIGN_ENABLED        = _env_bool("MTF_TREND_ALIGN_ENABLED", not SCALP_MODE)   # scalp: sert kapı kapalı


# TP / SL ATR multipliers  (fix-tp-sl-ratios: TP1=1.5R TP2=2.5R SL min %1.5)
SL_ATR_MULT  = _env_float("SL_ATR_MULT", 1.8)   # v6.1 düzeltildi: 1.2→1.8 (gürültü koruması)
TP1_R        = _env_float("TP1_R", 1.0 if SCALP_MODE else 1.5)   # scalp: kârın çoğunu erken al
TP2_R        = _env_float("TP2_R", 1.8 if SCALP_MODE else 2.5)
TP3_R        = _env_float("TP3_R", 3.0 if SCALP_MODE else 4.0)   # runner, küçük pay
MIN_SL_PCT   = _env_float("MIN_SL_PCT", 0.004 if SCALP_MODE else 0.015)  # scalp: %0.4 tight stop
# HUMAN MODE parametreleri (.env'den override edilir)
HUMAN_MODE              = _env_bool("HUMAN_MODE", False)
HUMAN_SL_ATR_MULT       = _env_float("HUMAN_SL_ATR_MULT", 2.0)
HUMAN_TP1_R             = _env_float("HUMAN_TP1_R", 1.5)
HUMAN_TP2_R             = _env_float("HUMAN_TP2_R", 2.5)
HUMAN_TRADE_THRESHOLD   = _env_float("HUMAN_TRADE_THRESHOLD", 72.0)
HUMAN_MAX_OPEN_TRADES   = _env_int("HUMAN_MAX_OPEN_TRADES", 2)

# TP Splits — toplamı 100 olmalı (test_config_tp_logic)
TP1_CLOSE_PCT    = _env_float("TP1_CLOSE_PCT", 60 if SCALP_MODE else 40)   # scalp: kârın çoğunu erken al; toplam=100
TP2_CLOSE_PCT    = _env_float("TP2_CLOSE_PCT", 25 if SCALP_MODE else 30)   # toplam=100
RUNNER_CLOSE_PCT = _env_float("RUNNER_CLOSE_PCT", 15 if SCALP_MODE else 30) # toplam=100 (60+25+15)

# Trailing
TRAIL_ATR_MULT       = _env_float("TRAIL_ATR_MULT", 1.0 if SCALP_MODE else 1.5)   # scalp: daha sıkı trailing
BREAKEVEN_ENABLED    = _env_bool("BREAKEVEN_ENABLED", True)
BREAKEVEN_OFFSET_PCT = _env_float("BREAKEVEN_OFFSET_PCT", 0.1)
TRAILING_STOP_TYPE   = _env("TRAILING_STOP_TYPE", "atr")
CONFIRMATION_MODE    = _env_bool("CONFIRMATION_MODE", False)
CONFIRMATION_AUTO_EXECUTE_HIGH_QUALITY = _env_bool("CONFIRMATION_AUTO_EXECUTE_HIGH_QUALITY", True)
CONFIRMATION_AUTO_EXECUTE_QUALITIES = _env("CONFIRMATION_AUTO_EXECUTE_QUALITIES", "S,A+,A").split(",")
CONFIRMATION_AUTO_EXECUTE_SCORE = _env_float("CONFIRMATION_AUTO_EXECUTE_SCORE", 70.0)
MACRO_GUARD_ENABLED = _env_bool("MACRO_GUARD_ENABLED", True)
LATENCY_GUARD_ENABLED = _env_bool("LATENCY_GUARD_ENABLED", True)
BYPASS_LIVE_RISK_SHIELDS = _env_bool("BYPASS_LIVE_RISK_SHIELDS", False)
REGIME_FILTER_ENABLED = _env_bool("REGIME_FILTER_ENABLED", True)
REGIME_FILTER_MIN_QUALITY_IN_CHOPPY = _env("REGIME_FILTER_MIN_QUALITY_IN_CHOPPY", "B")
ORDER_BOOK_WALL_FILTER_ENABLED = _env_bool("ORDER_BOOK_WALL_FILTER_ENABLED", not SCALP_MODE)   # scalp: kapalı
CONNORS_RSI_ENABLED          = _env_bool("CONNORS_RSI_ENABLED", not SCALP_MODE)   # scalp: kapalı
ORDER_BOOK_WALL_FILTER_MODE  = _env("ORDER_BOOK_WALL_FILTER_MODE", "soft")
REGIME_STABILIZATION_PERIODS = _env_int("REGIME_STABILIZATION_PERIODS", 3)
FRIDAY_CEO_LOOP_INTERVAL = _env_int("FRIDAY_CEO_LOOP_INTERVAL", 3600)
FRIDAY_CEO_MODEL = _env("FRIDAY_CEO_MODEL", "claude-sonnet-4-6")
FRIDAY_SUBAGENT_MODEL = _env("FRIDAY_SUBAGENT_MODEL", "claude-haiku-4-5-20251001")
FRIDAY_VOICE_REPORTS_ENABLED = _env_bool("FRIDAY_VOICE_REPORTS_ENABLED", False)
GHOST_WARMUP_ENABLED          = _env_bool("GHOST_WARMUP_ENABLED", False)
GHOST_WARMUP_MIN_WIN_RATE     = _env_float("GHOST_WARMUP_MIN_WIN_RATE", 0.55)
GHOST_WARMUP_TRADES_LOOKBACK  = _env_int("GHOST_WARMUP_TRADES_LOOKBACK", 10)
DYNAMIC_KELLY_ENABLED         = _env_bool("DYNAMIC_KELLY_ENABLED", False)
DYNAMIC_KELLY_LOOKBACK_DAYS   = _env_int("DYNAMIC_KELLY_LOOKBACK_DAYS", 7)
CHANDELIER_EXIT_ENABLED       = _env_bool("CHANDELIER_EXIT_ENABLED", False)
CHANDELIER_ATR_MULT           = _env_float("CHANDELIER_ATR_MULT", 3.0)


# Otonom Kâr Kilitleme Kalkanı (Dynamic Profit Lock Settings)
DAILY_PROFIT_LOCK_PCT  = _env_float("DAILY_PROFIT_LOCK_PCT", 3.0)
WEEKLY_PROFIT_LOCK_PCT = _env_float("WEEKLY_PROFIT_LOCK_PCT", 10.0)
MAX_SAME_DIRECTION     = _env_int("MAX_SAME_DIRECTION", 5)

# Time Decay Stop Loss Settings
TIME_DECAY_ENABLED                 = _env_bool("TIME_DECAY_ENABLED", True)
TIME_DECAY_START_MINUTES           = _env_int("TIME_DECAY_START_MINUTES", 45)
TIME_DECAY_BREAKEVEN_MINUTES       = _env_int("TIME_DECAY_BREAKEVEN_MINUTES", 105)

# Scalp-specific Time Decay Stop Loss Settings
SCALP_TIME_DECAY_START_MINUTES     = _env_int("SCALP_TIME_DECAY_START_MINUTES", 5)
SCALP_TIME_DECAY_BREAKEVEN_MINUTES = _env_int("SCALP_TIME_DECAY_BREAKEVEN_MINUTES", 8 if SCALP_MODE else 15)

# Spread & Fee-Aware Take-Profit Optimizer Settings
SCALP_TP_OPTIMIZER_ENABLED         = _env_bool("SCALP_TP_OPTIMIZER_ENABLED", True)
MIN_TP_FEE_SPREAD_RATIO            = _env_float("MIN_TP_FEE_SPREAD_RATIO", 2.5)

# Scalp Order Book Wall & CVD Divergence Filter Settings
SCALP_OB_WALL_MULTIPLIER           = _env_float("SCALP_OB_WALL_MULTIPLIER", 5.0)
SCALP_OB_WALL_PCT                  = _env_float("SCALP_OB_WALL_PCT", 0.002)
SCALP_CVD_DIVERGENCE_FILTER_ENABLED = _env_bool("SCALP_CVD_DIVERGENCE_FILTER_ENABLED", not SCALP_MODE)   # scalp: kapalı
RSI_LIMIT = 30.0
CVD_FILTER_VAL = -0.10

# Paper / Timing
INITIAL_PAPER_BALANCE = _env_float("INITIAL_PAPER_BALANCE", 2000.0)
MAX_HOLD_MINUTES      = _env_int("MAX_HOLD_MINUTES", 25 if SCALP_MODE else 240)   # scalp: pozisyon dakikalar içinde kapanır

# Scan
SCAN_INTERVAL            = _env_int("SCAN_INTERVAL", 45)   # 60→45s
MIN_VOLUME_USDT          = _env_float("MIN_VOLUME_USDT", 3_000_000.0)
MIN_MOVE_PCT             = _env_float("MIN_MOVE_PCT", 0.2 if SCALP_MODE else 0.3)   # scalp: küçük hareketten doğar
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
TRADE_THRESHOLD     = _env_float("TRADE_THRESHOLD", 45.0 if SCALP_MODE else 55.0)     # scalp: tek statik eşik (FAZ 3.3)

# Phase G: Quantum & Hedge Fund Automation Configuration
# NEDEN (FAZ 3.1): scalp modda rejim/RL/dinamik eşik oynaması KAPALI — "tek statik
# hakikat". Kapalıyken __getattr__ rejim-skalalamasını atlar ve ML online-prob
# kapısı (execution_engine) ile self-healing param mutasyonu da devre dışı kalır.
DYNAMIC_THRESHOLD_ENABLED = _env_bool("DYNAMIC_THRESHOLD_ENABLED", not SCALP_MODE)
MAX_CORRELATION_THRESHOLD = _env_float("MAX_CORRELATION_THRESHOLD", 0.75)
PORTFOLIO_VAR_LIMIT = _env_float("PORTFOLIO_VAR_LIMIT", 0.05)
MIN_ONLINE_PROBABILITY_THRESHOLD = _env_float("MIN_ONLINE_PROBABILITY_THRESHOLD", 0.45)


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
EXECUTABLE_QUALITIES       = _env("EXECUTABLE_QUALITIES", "S,A+,A,B,C,M").split(",")
# London (08-12 UTC) + NY (13-17 UTC) = en aktif seanslar
GOOD_HOURS_UTC             = list(range(8, 22))   # 08-21 UTC (NY kapanışına kadar)
BAD_HOURS_UTC              = list(range(0, 6))    # 00-05 UTC (Asian close)
SESSION_FILTER_ENABLED     = _env_bool("SESSION_FILTER_ENABLED", not SCALP_MODE)   # scalp: seans bonus/penaltısı gürültü
SESSION_SCORE_BONUS        = _env_float("SESSION_SCORE_BONUS", 5.0)
SESSION_SCORE_PENALTY      = _env_float("SESSION_SCORE_PENALTY", -5.0)
SHORT_REQUIRES_BTC_BEARISH = (not SCALP_MODE)   # scalp: SHORT için BTC-bearish zorunluluğu kapalı
BTC_TREND_INTERVAL         = "4h"
ADX_MIN_THRESHOLD          = 18  # eskiden 20
MIN_ADX_5M_FILTER          = _env_float("MIN_ADX_5M_FILTER", 20.0)

# Scalp Filtreler (v9.0 — gevşetilmiş)
MIN_BB_WIDTH    = _env_float("MIN_BB_WIDTH", 0.8)      # v6.0 gevşetildi
MIN_ADX_15M     = _env_float("MIN_ADX_15M", 15)        # v6.0 gevşetildi
MIN_ADX_5M      = _env_float("MIN_ADX_5M", 12)         # v6.0 gevşetildi
FUNDING_LONG_MAX  = _env_float("FUNDING_LONG_MAX", 0.005)   # v6.0 gevşetildi
FUNDING_SHORT_MIN = _env_float("FUNDING_SHORT_MIN", -0.005)  # v6.0 gevşetildi

# Funding-Rate Avcısı (Faz 6.7) — bağımsız mean-reversion strateji servisi.
# NEDEN: Aşırı funding = kalabalık taraf; mean-reversion ters yön sinyali üretir.
# Pipeline'a SCANNED event'iyle eklemlenir (mevcut akışı bozmaz). Varsayılan KAPALI.
FUNDING_HUNTER_ENABLED   = _env_bool("FUNDING_HUNTER_ENABLED", False)
FUNDING_HUNTER_THRESHOLD = _env_float("FUNDING_HUNTER_THRESHOLD", 0.0008)  # |funding| eşiği (≈%0.08/8h)
FUNDING_HUNTER_INTERVAL  = _env_int("FUNDING_HUNTER_INTERVAL", 900)        # tarama periyodu (sn)
FUNDING_HUNTER_MAX_SIGNALS = _env_int("FUNDING_HUNTER_MAX_SIGNALS", 3)     # tur başına max aday

# Flask
FLASK_HOST = _env("FLASK_HOST", "0.0.0.0")
FLASK_PORT = _env_int("FLASK_PORT", 5000)
DASHBOARD_PIN = _env("DASHBOARD_PIN", "")


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
OI_SPIKE_LIMIT      = _env_float("OI_SPIKE_LIMIT", 5.0)       # OI aşırı yükseliş eşiği
CVD_DIVERGENCE_BONUS= _env_float("CVD_DIVERGENCE_BONUS", 1.5) # CVD divergence bonusu
CVD_CONFIRM_BONUS   = _env_float("CVD_CONFIRM_BONUS", 1.0)    # CVD confirm bonusu

# Guvenlik
def is_live_trading_allowed() -> bool:
    """Full live safety gate (directive Section 18). Fails CLOSED.

    All seven conditions must hold. live_readiness.check() embeds the
    profit/expectancy/drawdown/uptime proof requirements, so PASS there
    satisfies both profit_readiness and live_readiness intent.
    """
    # NEDEN: Canli trade'i 7 kapinin TAMAMI gecmeden imkansiz kil; herhangi
    # bir hata/okuma basarisizligi => canli BLOKE (fail-closed). Paper modu
    # etkilenmez.
    try:
        # --- env flag conditions ---
        if __getattr__("EXECUTION_MODE") != "live":
            return False
        if not LIVE_TRADING_ENABLED:
            return False
        if DRY_RUN:                       # DRY_RUN must be explicitly false
            return False
        if not CONFIRM_LIVE_TRADING:
            return False
        if not USE_BINANCE_PRIVATE_API:
            return False

        # --- proof-of-edge condition (readiness gate) ---
        # Lazy import to avoid circular import at module load.
        from core import live_readiness
        result = live_readiness.check()
        if not result.get("ready"):
            return False

        return True
    except Exception as e:
        # Any failure to evaluate the gate => live BLOCKED.
        try:
            import logging
            logging.getLogger("ax.config").error(
                "is_live_trading_allowed() failed-closed: %s", e
            )
        except Exception:
            pass
        return False

def is_private_api_allowed() -> bool:
    return USE_BINANCE_PRIVATE_API

# NEDEN (P0-3, directive Section 12/18): Otonom aktorler (Friday, Ghost,
# self-healing) bu key'leri ASLA yazamaz — canli kontrol + risk kalkani
# parametreleri. Yalniz acik insan komutu (/live, /set) degistirebilir.
# "No component may bypass the live gate."
AUTONOMOUS_FORBIDDEN_KEYS = frozenset({
    "EXECUTION_MODE", "LIVE_TRADING_ENABLED", "DRY_RUN", "CONFIRM_LIVE_TRADING",
    "LIVE_CONFIRM", "USE_BINANCE_PRIVATE_API", "BYPASS_LIVE_RISK_SHIELDS",
})

def is_autonomous_forbidden(key: str) -> bool:
    """True if `key` may NOT be set by an autonomous actor (Friday/Ghost/etc.).

    Live-control and risk-shield params are human-command only.
    """
    return str(key).upper().strip() in AUTONOMOUS_FORBIDDEN_KEYS

def safety_summary() -> dict:
    return {
        "execution_mode": __getattr__("EXECUTION_MODE"), "ax_mode": AX_MODE,
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
    "MAX_SPREAD_PCT":       ("max_spread_pct", float),
    "MAX_OPEN_TRADES":      ("max_open_trades", int),
    "MAX_CHASE_PCT":        ("max_chase_pct", float),
    "AUTO_COMPOUNDING":     ("auto_compounding", lambda v: v.strip().lower() in ("true", "1", "yes")),
    "DRAWDOWN_DEFENSIVE_PCT":         ("drawdown_defensive_pct", float),
    "DRAWDOWN_LOCK_PCT":              ("drawdown_lock_pct", float),
    "EQUITY_CURVE_EMA_PERIOD":        ("equity_curve_ema_period", int),
    "EQUITY_CURVE_RISK_REDUCTION":    ("equity_curve_risk_reduction", float),
    "EQUITY_CURVE_FILTER_ENABLED":    ("equity_curve_filter_enabled", lambda v: v.strip().lower() in ("true", "1", "yes")),
    "MTF_TREND_ALIGN_RISK_REDUCTION": ("mtf_trend_align_risk_reduction", float),
    "MTF_TREND_ALIGN_ENABLED":        ("mtf_trend_align_enabled", lambda v: v.strip().lower() in ("true", "1", "yes")),
    "TRAILING_STOP_TYPE":             ("trailing_stop_type", str),
    "CONFIRMATION_MODE":              ("confirmation_mode", lambda v: v.strip().lower() in ("true", "1", "yes")),
    "CONFIRMATION_AUTO_EXECUTE_HIGH_QUALITY": ("confirmation_auto_execute_high_quality", lambda v: v.strip().lower() in ("true", "1", "yes")),
    "OI_SPIKE_LIMIT":                 ("oi_spike_limit", float),
    "DAILY_PROFIT_LOCK_PCT":          ("daily_profit_lock_pct", float),
    "WEEKLY_PROFIT_LOCK_PCT":         ("weekly_profit_lock_pct", float),
    "RSI_LIMIT":                      ("rsi_limit", float),
    "CVD_FILTER_VAL":                 ("cvd_filter_val", float),
    "TRAIL_ATR_MULT":                 ("trail_atr_mult", float),
    "MACRO_GUARD_ENABLED":            ("macro_guard_enabled", lambda v: v.strip().lower() in ("true", "1", "yes")),
    "LATENCY_GUARD_ENABLED":          ("latency_guard_enabled", lambda v: v.strip().lower() in ("true", "1", "yes")),
    "BYPASS_LIVE_RISK_SHIELDS":       ("bypass_live_risk_shields", lambda v: v.strip().lower() in ("true", "1", "yes")),
    "REGIME_FILTER_ENABLED":          ("regime_filter_enabled", lambda v: v.strip().lower() in ("true", "1", "yes")),
    "REGIME_FILTER_MIN_QUALITY_IN_CHOPPY": ("regime_filter_min_quality_in_choppy", str),
    "ORDER_BOOK_WALL_FILTER_ENABLED": ("order_book_wall_filter_enabled", lambda v: v.strip().lower() in ("true", "1", "yes")),
    "CONFIRMATION_AUTO_EXECUTE_SCORE": ("confirmation_auto_execute_score", float),
    "FRIDAY_CEO_LOOP_INTERVAL":        ("friday_ceo_loop_interval", int),
    "FRIDAY_VOICE_REPORTS_ENABLED":    ("friday_voice_reports_enabled", lambda v: v.strip().lower() in ("true", "1", "yes")),
    "MAX_SAME_DIRECTION":              ("max_same_direction", int),
    "CONNORS_RSI_ENABLED":             ("connors_rsi_enabled", lambda v: v.strip().lower() in ("true", "1", "yes")),
    "ORDER_BOOK_WALL_FILTER_MODE":     ("order_book_wall_filter_mode", str),
    "REGIME_STABILIZATION_PERIODS":    ("regime_stabilization_periods", int),
    "GHOST_WARMUP_ENABLED":             ("ghost_warmup_enabled", lambda v: v.strip().lower() in ("true", "1", "yes")),
    "GHOST_WARMUP_MIN_WIN_RATE":        ("ghost_warmup_min_win_rate", float),
    "GHOST_WARMUP_TRADES_LOOKBACK":     ("ghost_warmup_trades_lookback", int),
    "DYNAMIC_KELLY_ENABLED":            ("dynamic_kelly_enabled", lambda v: v.strip().lower() in ("true", "1", "yes")),
    "DYNAMIC_KELLY_LOOKBACK_DAYS":      ("dynamic_kelly_lookback_days", int),
    "CHANDELIER_EXIT_ENABLED":          ("chandelier_exit_enabled", lambda v: v.strip().lower() in ("true", "1", "yes")),
    "CHANDELIER_ATR_MULT":              ("chandelier_atr_mult", float),
}

_AI_PARAMS_MAP = {
    "SL_ATR_MULT":          ("sl_atr_mult", float),
    "RISK_PCT":             ("risk_pct", float),
    "TP2_R":                ("tp_atr_mult", float),
}

_CONFIG_CACHE = {}
_CONFIG_CACHE_TTL = 2.0

def __getattr__(name: str) -> Any:
    """Modül düzeyinde dinamik parametreleri veritabanından okur, 2 sn önbellekleme ile."""
    import time
    import sys
    
    is_testing = "pytest" in sys.modules or "unittest" in sys.modules
    now = time.time()
    
    if not is_testing and name in _CONFIG_CACHE:
        val, expires = _CONFIG_CACHE[name]
        if now < expires:
            return val
            
    val = _read_dynamic_param_from_db(name)
    
    # DB lock/hata durumunda önbellekteki son başarılı değeri kullan
    if val is None and name in _CONFIG_CACHE:
        val = _CONFIG_CACHE[name][0]
        
    # Eğer önbellekte de yoksa statik varsayılana dön
    if val is None:
        val = _STATIC_DEFAULTS.get(name)
        
    # Dynamic scaling based on market regime and starvation alarm
    # NEDEN (FAZ 3.1): DYNAMIC_THRESHOLD_ENABLED=False (scalp) iken rejim/RL/starvation
    # bazlı eşik oynaması TAMAMEN atlanır — değer DB'den/statik varsayılandan geldiği
    # gibi döner ("tek statik hakikat"). Manuel /set yine çalışır (DB okuması korunur).
    if val is not None and globals().get("DYNAMIC_THRESHOLD_ENABLED", True):
        if name == "RISK_PCT":
            try:
                import database
                regime = database.get_market_regime()
                if regime == "BULLISH":
                    val = val * 1.3
                elif regime == "BEARISH":
                    val = val * 0.7
                elif regime == "SIDEWAYS":
                    val = val * 0.8
                elif regime == "HIGH_MOMENTUM":
                    val = val * 1.2
                elif regime == "NEWS_DRIVEN":
                    val = val * 0.4
                elif regime == "HIGH_VOLATILITY":
                    val = val * 0.6
                elif regime == "LOW_VOLATILITY":
                    val = val * 0.9
                # Backwards compatibility fallbacks
                elif regime == "TRENDING_HIGH_VOL":
                    val = val * 1.2
                elif regime == "CHOPPY_HIGH_VOL":
                    val = val * 0.5
                elif regime == "CHOPPY_LOW_VOL":
                    val = val * 0.75
            except Exception:
                pass
        elif name == "RSI_LIMIT":
            try:
                import database
                regime = database.get_market_regime()
                starvation = database.get_system_state("trade_starvation_alarm") == "true"
                if regime in ("BULLISH", "HIGH_MOMENTUM") or "TRENDING" in regime:
                    val = 24.0  # 18→24: gerçek boğa-devamı dip'leri RSI 25-40 bandında olur
                elif regime == "LOW_VOLATILITY":
                    val = 20.0  # Düşük vol'da sinyal kıtlığı — RSI filtresi gevşetildi
                elif regime in ("BEARISH", "SIDEWAYS", "HIGH_VOLATILITY", "NEWS_DRIVEN") or "CHOPPY" in regime:
                    val = 30.0  # Strict for choppy range protection
                # Trade açlığı varsa daha da gevşet
                if starvation:
                    val = max(15.0, val - 8.0)  # Min 15.0 (SHORT veto eşiği = RSI>85)
                # Q-Learning shift
                rl_shift = float(database.get_system_state("rl_rsi_limit_shift") or 0.0)
                val += rl_shift
            except Exception:
                pass
        elif name == "CVD_FILTER_VAL":
            try:
                import database
                regime = database.get_market_regime()
                starvation = database.get_system_state("trade_starvation_alarm") == "true"
                if regime in ("BULLISH", "HIGH_MOMENTUM") or "TRENDING" in regime:
                    val = -0.35  # Relaxed for strong trending markets
                elif regime == "LOW_VOLATILITY":
                    val = -0.25  # Düşük vol'da CVD sinyali zayıf — filtre gevşetildi
                elif regime in ("BEARISH", "SIDEWAYS", "HIGH_VOLATILITY", "NEWS_DRIVEN") or "CHOPPY" in regime:
                    val = -0.10  # Tight filter for choppy range protection
                # Trade açlığı varsa daha da gevşet
                if starvation:
                    val = min(-0.40, val - 0.15)  # Max -0.40 (çok permissive)
                # Q-Learning shift
                rl_shift = float(database.get_system_state("rl_cvd_filter_shift") or 0.0)
                val += rl_shift
            except Exception:
                pass
        elif name == "TRAIL_ATR_MULT":
            try:
                import database
                # Q-Learning shift
                rl_shift = float(database.get_system_state("rl_trail_mult_shift") or 0.0)
                val += rl_shift
            except Exception:
                pass
        elif name == "TRADE_THRESHOLD":
            try:
                import database
                regime = database.get_market_regime()
                if regime == "BULLISH":
                    val -= 4.0
                elif regime == "HIGH_MOMENTUM":
                    val -= 3.0
                elif regime == "BEARISH":
                    val += 3.0
                elif regime == "SIDEWAYS":
                    val += 4.0
                elif regime == "NEWS_DRIVEN":
                    val += 5.0
                elif regime == "HIGH_VOLATILITY":
                    val += 3.0
                elif regime == "LOW_VOLATILITY":
                    val += 2.0
                
                # Trade Starvation Relaxation (Phase C)
                if database.get_system_state("trade_starvation_alarm") == "true":
                    val -= 5.0
            except Exception:
                pass
        elif name == "REGIME_FILTER_MIN_QUALITY_IN_CHOPPY":
            try:
                import database
                if database.get_system_state("trade_starvation_alarm") == "true":
                    val = "B"
            except Exception:
                pass

    if not is_testing:
        _CONFIG_CACHE[name] = (val, now + _CONFIG_CACHE_TTL)
        
    return val

def _read_dynamic_param_from_db(name: str) -> Any:
    import sqlite3
    import time

    max_retries = 5
    base_delay = 0.05

    if name in _DYNAMIC_PARAMS_MAP:
        db_key, cast_fn = _DYNAMIC_PARAMS_MAP[name]

        # 1) Redis-first (Faz 1.1)
        # NEDEN: Ghost loop 12 sn'de bir, execution monitoring 1 sn'de bir bu
        # yoldan geçiyor — her okumada SQLite bağlantısı açmak lock baskısının
        # ana kaynağıydı. Redis'te varsa SQLite'a HİÇ gidilmez.
        try:
            from core import redis_state
            raw = redis_state.get_param(db_key)
            if raw is not None:
                return cast_fn(raw)
        except Exception:
            pass  # Redis sorunlu → aşağıdaki SQLite fallback aynen çalışır

        # 2) SQLite fallback + read-repair
        # NEDEN (Faz 1.2): doğrudan sqlite3.connect yerine database.get_conn()
        # kullanılır — WAL/busy_timeout pragma'ları tek noktadan yönetilir.
        for attempt in range(max_retries):
            try:
                import database
                with database.get_conn() as conn:
                    row = conn.execute("SELECT value FROM system_state WHERE key = ?", (db_key,)).fetchone()
                if row and row["value"] is not None:
                    try:
                        from core import redis_state
                        # NEDEN: read-repair — miss sonrası Redis'e kalıcı yaz,
                        # sonraki okumalar SQLite'a inmesin.
                        redis_state.set_param(db_key, row["value"])
                    except Exception:
                        pass
                    return cast_fn(row["value"])
                break
            except (sqlite3.OperationalError, sqlite3.DatabaseError) as e:
                if attempt == max_retries - 1:
                    logger.warning(f"Database lockout reading dynamic param {name}: {e}")
                time.sleep(base_delay * (2 ** attempt))
            except Exception:
                break

    if name in _AI_PARAMS_MAP:
        # NEDEN (FAZ 2 / 3.3): scalp modda (DYNAMIC_THRESHOLD_ENABLED=False) RISK_PCT ve
        # TP2_R, AI-tuner'ın `params` tablosundan DEĞİL statik scalp varsayılanından gelir
        # ("tek statik hakikat"; tuner zaten FAZ 4'te kapalı). SL_ATR_MULT eskisi gibi
        # `params`'tan okunur — doküman onu değiştirmiyor. None döndürmek __getattr__'ı
        # _STATIC_DEFAULTS'a düşürür.
        if not globals().get("DYNAMIC_THRESHOLD_ENABLED", True) and name in ("RISK_PCT", "TP2_R"):
            return None
        db_col, cast_fn = _AI_PARAMS_MAP[name]
        for attempt in range(max_retries):
            try:
                # NEDEN (Faz 1.2): database.get_conn() — WAL pragma disiplini.
                import database
                with database.get_conn() as conn:
                    row = conn.execute("SELECT * FROM params ORDER BY id DESC LIMIT 1").fetchone()
                if row and row[db_col] is not None:
                    return cast_fn(row[db_col])
                break
            except (sqlite3.OperationalError, sqlite3.DatabaseError) as e:
                if attempt == max_retries - 1:
                    logger.warning(f"Database lockout reading AI param {name}: {e}")
                time.sleep(base_delay * (2 ** attempt))
            except Exception:
                break

    if name in _STATIC_DEFAULTS:
        return _STATIC_DEFAULTS[name]

    # Fallback to local globals
    if name in globals():
        return globals()[name]
    raise AttributeError(f"module {__name__} has no attribute {name}")

# ── Clean up globals for dynamic parameters to force __getattr__ lookup ──────
_STATIC_DEFAULTS = {}
for name in list(_DYNAMIC_PARAMS_MAP.keys()) + list(_AI_PARAMS_MAP.keys()):
    if name in globals():
        _STATIC_DEFAULTS[name] = globals()[name]
        del globals()[name]
