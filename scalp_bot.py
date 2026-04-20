"""
AURVEX.Ai — Scalp Bot v4.2
Kurumsal düzeyde tam özellikli algo trading sistemi.

Araştırma kaynakları:
  - Mark Douglas: Trading in the Zone
  - LuxAlgo 2025: Multi-timeframe RSI agreement
  - VT Markets 2025: Trend-following scalp %62 WR in trending markets
  - Stoic.ai institutional: Sharpe >2, drawdown <%20
  - Renaissance / Citadel prensipleri: circuit breaker, partial exit

v4.1 → v4.2:
  1. BTC Korelasyon Filtresi
  2. Order Book Imbalance (bid/ask oranı)
  3. Kısmi Çıkış — 1R'de %50 realize et, kalan trailing
  4. Zaman Bazlı Çıkış — 4 saat sonra zorla kapat
  5. Devre Kesici — 5 ardışık kayıp = 2 saat duraklama
  6. Hacim eşiği 5M'ye düşürüldü

v4.2 → v4.2.1:
  - AI Brain v3.1 tam entegrasyonu (coin kişilik analizi dahil)
  - TelegramManager — komut sistemi, drawdown uyarısı, sabah brifing
  - 7/24 tarama aktif (session filtresi kaldırıldı)
  - post_trade_analysis: her kapanışta MAE/MFE analizi
"""

import os, time, logging, math, threading
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from binance.client import Client
from binance.enums import *
import pandas as pd
import requests
from database import init_db, save_trade, close_trade, get_current_params, get_bot_control, log_rejection
from live_tracker import LiveTracker, init_tracker_tables, save_analysis

try:
    from ai_brain import analyze_and_adapt, post_trade_analysis, set_client
    AI_BRAIN_AVAILABLE = True
except ImportError:
    AI_BRAIN_AVAILABLE = False
    def post_trade_analysis(*a, **kw): pass
    def set_client(*a, **kw): pass

try:
    from ml_signal_scorer import get_scorer, train_model, should_trade as ml_should_trade
    ML_SCORER_AVAILABLE = True
    # Başlangıçta modeli yüklemeye çalış
    _ml_scorer = get_scorer()
except ImportError:
    ML_SCORER_AVAILABLE = False
    def ml_should_trade(sig): return True, 50
    def train_model(): return False

try:
    from telegram_manager import TelegramManager
    TG_MANAGER_AVAILABLE = True
except ImportError:
    TG_MANAGER_AVAILABLE = False

load_dotenv()

BINANCE_KEY    = os.getenv("BINANCE_API_KEY", "")
BINANCE_SECRET = os.getenv("BINANCE_API_SECRET", "")
TG_TOKEN       = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT        = os.getenv("TELEGRAM_CHAT_ID", "")

# =============================================================================
# KONFIGÜRASYON
# =============================================================================
PAPER_MODE               = True
ML_SCORE_THRESHOLD       = 50   # Bu skorun altındaki sinyaller reddedilir
ML_RETRAIN_INTERVAL      = 50   # Her 50 kapanışta bir modeli yeniden eğit
MAX_OPEN                 = 10  # Veri biriktirme: 3 → 10
SCAN_INTERVAL            = 20
MONITOR_TICK             = 5
AI_BRAIN_THRESHOLD       = 10
COMMISSION_RT            = 0.0008
MAX_HOLD_HOURS           = 4
CIRCUIT_BREAKER_LOSSES   = 15   # Veri biriktirme: 5 → 15
CIRCUIT_BREAKER_MINUTES  = 30   # Bekleme: 120 → 30 dk

BLACKLIST = {
    "USDCUSDT","FDUSDUSDT","UUSDT","RLUSDUSDT","USD1USDT",
    "EURUSDT","PAXGUSDT","XAUTUSDT","XUSDUSDT","USDEUSDT",
    "TUSDUSDT","BUSDUSDT","USDTUSDT","WBTCUSDT","STOUSDT",
    "OMNIUSDT","RADUSDT","CTSIUSDT","BTCUSDT","ETHUSDT",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)

client  = Client(BINANCE_KEY, BINANCE_SECRET)
tracker = LiveTracker(client)

# TelegramManager
if TG_MANAGER_AVAILABLE:
    tg_manager = TelegramManager(client)
    tg_manager.paper_mode = PAPER_MODE
else:
    tg_manager = None

open_trades           = {}
scan_cooldown         = {}
hot_coins             = []
closed_trade_counter  = 0
consecutive_losses    = 0
circuit_breaker_until = None

VALID_FUTURES_SYMBOLS: set = set()
_filter_cache: dict        = {}

# =============================================================================
# YARDIMCI FONKSİYONLAR
# =============================================================================

def is_circuit_breaker_active():
    global circuit_breaker_until
    if circuit_breaker_until and datetime.now(timezone.utc) < circuit_breaker_until:
        remaining = int((circuit_breaker_until - datetime.now(timezone.utc)).total_seconds() / 60)
        return True, remaining
    return False, 0

def trigger_circuit_breaker():
    global circuit_breaker_until, consecutive_losses
    circuit_breaker_until = datetime.now(timezone.utc) + timedelta(minutes=CIRCUIT_BREAKER_MINUTES)
    logger.warning(f"DEVRE KESİCİ AKTİF — {CIRCUIT_BREAKER_MINUTES} dakika duraklama")
    tg(
        f"⛔ <b>Devre Kesici Aktif</b>\n\n"
        f"{CIRCUIT_BREAKER_LOSSES} ardışık kayıp tespit edildi.\n"
        f"Bot {CIRCUIT_BREAKER_MINUTES} dakika duraklatıldı.\n"
        f"Devam: <code>{circuit_breaker_until.strftime('%H:%M UTC')}</code>"
    )

def load_futures_symbols():
    global VALID_FUTURES_SYMBOLS
    try:
        info = client.futures_exchange_info()
        VALID_FUTURES_SYMBOLS = {
            s["symbol"] for s in info["symbols"]
            if s.get("status") == "TRADING" and s.get("contractType") == "PERPETUAL"
        }
        logger.info(f"Futures sembol listesi: {len(VALID_FUTURES_SYMBOLS)} sembol")
    except Exception as e:
        logger.error(f"Sembol listesi alınamadı: {e}")

def get_filters(symbol):
    if symbol in _filter_cache:
        return _filter_cache[symbol]
    try:
        info = client.futures_exchange_info()
        for s in info["symbols"]:
            if s["symbol"] == symbol:
                step = tick = 0.001
                min_not = 5.0
                for f in s["filters"]:
                    if f["filterType"] == "LOT_SIZE":
                        step = float(f["stepSize"])
                    elif f["filterType"] == "PRICE_FILTER":
                        tick = float(f["tickSize"])
                    elif f["filterType"] == "MIN_NOTIONAL":
                        min_not = float(f["notional"])
                result = (step, tick, min_not)
                _filter_cache[symbol] = result
                return result
        BLACKLIST.add(symbol)
        raise ValueError(f"{symbol} futures'da bulunamadı")
    except Exception as e:
        raise Exception(f"get_filters hata: {e}")

N8N_WEBHOOK = os.getenv("N8N_WEBHOOK_URL", "http://localhost:5678/webhook/ax-signal")

def notify_n8n(event_type, payload):
    """AX sinyallerini n8n'e iletir — fire and forget."""
    try:
        data = {
            "event": event_type,
            "tg_token": TG_TOKEN,
            "chat_id": TG_CHAT,
            **payload
        }
        requests.post(N8N_WEBHOOK, json=data, timeout=2)
    except:
        pass

def tg(msg):
    """Merkezi Telegram gönderici. Her zaman TelegramManager üzerinden gönderir."""
    if tg_manager:
        tg_manager.send(msg)
        return
    # TelegramManager yoksa fallback
    if not TG_TOKEN or not TG_CHAT:
        return
    prefix = "🧪 <b>[PAPER]</b> " if PAPER_MODE else ""
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": prefix + msg, "parse_mode": "HTML"},
            timeout=5
        )
    except:
        pass

def get_params():
    p = get_current_params()
    if not p:
        return {
            "sl_atr_mult": 1.3, "tp_atr_mult": 2.0,
            "rsi5_min": 35, "rsi5_max": 72,   # Gevşetildi: 40-70 → 35-72
            "rsi1_min": 35, "rsi1_max": 72,   # Gevşetildi: 40-68 → 35-72
            "vol_ratio_min": 1.1, "min_volume_m": 3.0,  # vol_ratio 1.2 → 1.1
            "min_change_pct": 1.5, "risk_pct": 1.0, "version": 1,
        }
    return p

def get_balance():
    if PAPER_MODE:
        try:
            import sqlite3
            conn = sqlite3.connect(
                os.path.join(os.path.dirname(os.path.abspath(__file__)), "trading.db")
            )
            c = conn.cursor()
            c.execute("SELECT paper_balance FROM paper_account LIMIT 1")
            row = c.fetchone()
            conn.close()
            return float(row[0]) if row else 250.0
        except:
            return 250.0
    try:
        bal = client.futures_account_balance()
        for b in bal:
            if b["asset"] == "USDT":
                return max(float(b["availableBalance"]), 1.0)
    except Exception as e:
        logger.warning(f"Bakiye alınamadı: {e}")
    return 250.0

def init_paper_account():
    import sqlite3
    conn = sqlite3.connect(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "trading.db")
    )
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS paper_account (
            id INTEGER PRIMARY KEY,
            paper_balance REAL DEFAULT 250.0,
            total_commission REAL DEFAULT 0.0,
            updated_at TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS pattern_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT, direction TEXT,
            adx REAL, rv REAL, rsi5 REAL, rsi1 REAL,
            funding_favorable INTEGER, session TEXT,
            bb_width_pct REAL, ob_ratio REAL,
            btc_trend TEXT, volume_m REAL,
            result TEXT, net_pnl REAL,
            partial_exit INTEGER DEFAULT 0,
            hold_minutes REAL DEFAULT 0,
            created_at TEXT
        )
    """)
    c.execute("INSERT OR IGNORE INTO paper_account (id, paper_balance, updated_at) VALUES (1, 250.0, datetime('now'))")
    conn.commit()
    conn.close()

def update_paper_balance(pnl_net):
    import sqlite3
    conn = sqlite3.connect(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "trading.db")
    )
    c = conn.cursor()
    c.execute("UPDATE paper_account SET paper_balance = paper_balance + ?, updated_at = datetime('now') WHERE id = 1", (pnl_net,))
    conn.commit()
    conn.close()

def save_pattern(trade_data, result, net_pnl, partial_exit=0, hold_minutes=0):
    try:
        import sqlite3
        conn = sqlite3.connect(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "trading.db")
        )
        c = conn.cursor()
        now_hour = datetime.now(timezone.utc).hour
        if 1 <= now_hour < 4:
            session = "ASIA"
        elif 7 <= now_hour < 10:
            session = "LONDON"
        elif 13 <= now_hour < 17:
            session = "NEWYORK"
        else:
            session = "OFF"
        c.execute("""
            INSERT INTO pattern_memory
            (symbol,direction,adx,rv,rsi5,rsi1,funding_favorable,session,
             bb_width_pct,ob_ratio,btc_trend,volume_m,result,net_pnl,
             partial_exit,hold_minutes,bb_width_chg,momentum_3c,prev_result,
             created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
        """, (
            trade_data.get("symbol"), trade_data.get("direction"),
            trade_data.get("adx15", 0), trade_data.get("rv", 0),
            trade_data.get("rsi5", 0), trade_data.get("rsi1", 0),
            trade_data.get("funding_favorable", 0), session,
            trade_data.get("bb_width_pct", 0),
            trade_data.get("ob_ratio", 1.0),
            trade_data.get("btc_trend", "NEUTRAL"),
            trade_data.get("volume_m", 0),
            result, net_pnl, partial_exit, hold_minutes,
            trade_data.get("bb_width_chg", 0),
            trade_data.get("momentum_3c", 0),
            trade_data.get("prev_result", "NONE")
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"Pattern kayıt hatası: {e}")

def sync_open_paper_trades():
    """PAPER_MODE: Restart sonrası DB'deki açık trade'leri open_trades'e yükle."""
    from database import get_trades
    db_open = get_trades(status="OPEN")
    count = 0
    for t in db_open:
        sym = t["symbol"]
        if sym in open_trades:
            continue
        open_trades[sym] = {
            "trade_id":      t["id"],
            "symbol":        sym,
            "direction":     t["direction"],
            "entry":         t["entry"],
            "sl":            t["sl"],
            "tp":            t["tp"],
            "qty":           t.get("qty", 0),
            "leverage":      t.get("leverage", 10),
            "risk":          t.get("risk_usdt", 0),
            "paper":         True,
            "breakeven_set": False,
            "partial_done":  False,
            "open_time":     datetime.now(timezone.utc),
            "trade_data":    t,
        }
        tracker.add_trade(sym, open_trades[sym])
        count += 1
    if count:
        logger.info(f"[PAPER] {count} açık trade DB'den yüklendi.")

def sync_open_positions():
    if PAPER_MODE:
        return
    try:
        positions = client.futures_position_information()
        from database import get_trades
        db_open = {t["symbol"]: t for t in get_trades(status="OPEN")}
        count = 0
        for pos in positions:
            sym = pos["symbol"]
            amt = float(pos.get("positionAmt", 0))
            if amt == 0:
                continue
            direction = "LONG" if amt > 0 else "SHORT"
            entry = float(pos.get("entryPrice", 0))
            if sym in db_open:
                t = db_open[sym]
                open_trades[sym] = {
                    "trade_id":      t["id"],
                    "symbol":        sym,
                    "direction":     direction,
                    "entry":         entry,
                    "sl":            t["sl"],
                    "tp":            t["tp"],
                    "qty":           abs(amt),
                    "leverage":      t.get("leverage", 10),
                    "risk":          t.get("risk_usdt", 0),
                    "paper":         False,
                    "breakeven_set": False,
                    "partial_done":  False,
                    "open_time":     datetime.now(timezone.utc),
                    "trade_data":    t,
                }
                tracker.add_trade(sym, open_trades[sym])
                count += 1
        if count:
            logger.info(f"{count} pozisyon senkronize edildi.")
    except Exception as e:
        logger.error(f"Senkronizasyon hatası: {e}")

def run_ai_brain():
    if not AI_BRAIN_AVAILABLE:
        return
    try:
        insight = analyze_and_adapt()
        if insight:
            tg(f"🧠 <b>AI Brain Raporu</b>\n\n{insight[:800]}")
    except Exception as e:
        logger.error(f"AI Brain hata: {e}")

def run_ml_retrain():
    """ML modelini yeniden eğitir ve sonucu Telegram'a bildirir."""
    if not ML_SCORER_AVAILABLE:
        return
    try:
        success = train_model()
        if success:
            scorer = get_scorer()
            status = scorer.get_status()
            tg(
                f"🤖 <b>ML Model Güncellendi</b>\n\n"
                f"📊 Eğitim verisi: <code>{status['n_samples']}</code> trade\n"
                f"🎯 Eşik: <code>{status['threshold']}</code> puan\n"
                f"⏰ Eğitim: <code>{status['last_train']}</code>"
            )
        else:
            logger.info("ML yeniden eğitim: yetersiz veri")
    except Exception as e:
        logger.error(f"ML retrain hata: {e}")

# =============================================================================
# TEKNİK ANALİZ
# =============================================================================

def get_candles(symbol, interval, limit=200):
    try:
        klines = client.get_klines(symbol=symbol, interval=interval, limit=limit)
        df = pd.DataFrame(klines, columns=[
            "time","open","high","low","close","volume",
            "ct","qav","nt","tbbav","tbqav","ignore"
        ])
        for col in ["open","high","low","close","volume"]:
            df[col] = df[col].astype(float)
        return df
    except:
        return pd.DataFrame()

def ema(s, p):
    return s.ewm(span=p, adjust=False).mean()

def rsi_val(s, p=14):
    d = s.diff()
    g = d.clip(lower=0).rolling(p).mean()
    l = (-d.clip(upper=0)).rolling(p).mean()
    return round(100 - (100 / (1 + g.iloc[-1] / (l.iloc[-1] + 1e-10))), 1)

def atr_val(df, p=14):
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift()).abs(),
        (df["low"]  - df["close"].shift()).abs(),
    ], axis=1).max(axis=1)
    return float(tr.rolling(p).mean().iloc[-1])

def adx_val(df, p=14):
    high  = df["high"]
    low   = df["low"]
    close = df["close"]
    plus_dm  = (high.diff()).clip(lower=0)
    minus_dm = (-low.diff()).clip(lower=0)
    plus_dm[plus_dm < minus_dm.abs()]  = 0
    minus_dm[minus_dm < plus_dm]       = 0
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs(),
    ], axis=1).max(axis=1)
    atr14    = tr.rolling(p).mean()
    plus_di  = 100 * (plus_dm.rolling(p).mean()  / (atr14 + 1e-10))
    minus_di = 100 * (minus_dm.rolling(p).mean() / (atr14 + 1e-10))
    dx       = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di + 1e-10))
    adx      = dx.rolling(p).mean()
    return float(adx.iloc[-1]), float(plus_di.iloc[-1]), float(minus_di.iloc[-1])

def bollinger_width(df, p=20, std=2.0):
    middle    = df["close"].rolling(p).mean()
    std_dev   = df["close"].rolling(p).std()
    upper     = middle + std * std_dev
    lower     = middle - std * std_dev
    width_pct = ((upper - lower) / (middle + 1e-10) * 100).iloc[-1]
    return round(float(width_pct), 2)

def bollinger_width_change(df, p=20, std=2.0, lookback=5):
    """
    Band Growth: Son `lookback` muma göre BB genişleme hızı.
    Pozitif → bant genişliyor (volatilite artıyor, kırılım yakın)
    Negatif → bant daralıyor (konsolidasyon)
    """
    middle  = df["close"].rolling(p).mean()
    std_dev = df["close"].rolling(p).std()
    upper   = middle + std * std_dev
    lower   = middle - std * std_dev
    width   = (upper - lower) / (middle + 1e-10) * 100
    cur_w   = float(width.iloc[-1])
    past_w  = float(width.iloc[-1 - lookback]) if len(width) > lookback else cur_w
    return round(cur_w - past_w, 3)   # Pozitif = genişliyor


def momentum_3c(df):
    """
    Predictive Momentum Skoru: Son 3 mumun yönü ve büyüklüğüne göre
    -3 ile +3 arasında bir skor üretir.
    +3 = 3 güçlü yeşil mum (LONG için ideal)
    -3 = 3 güçlü kırmızı mum (SHORT için ideal)
    """
    if len(df) < 4:
        return 0
    score = 0
    for i in range(-3, 0):
        o = df["open"].iloc[i]
        c = df["close"].iloc[i]
        body = abs(c - o)
        atr  = df["high"].iloc[i] - df["low"].iloc[i]
        strength = body / (atr + 1e-10)   # 0-1 arası gövde oranı
        if c > o:    # Yeşil mum
            score += strength
        else:        # Kırmızı mum
            score -= strength
    return round(score, 3)   # -3 ile +3 arası


def vwap_val(df):
    tp = (df["high"] + df["low"] + df["close"]) / 3
    return float((tp * df["volume"]).sum() / df["volume"].sum())

def macd_hist(s):
    m   = ema(s, 12).iloc[-1] - ema(s, 26).iloc[-1]
    sig = ema(pd.Series((ema(s, 12) - ema(s, 26)).values), 9).iloc[-1]
    return m - sig

def relative_volume(df, period=20):
    avg = df["volume"].iloc[-period-1:-1].mean()
    cur = df["volume"].iloc[-1]
    return round(cur / (avg + 1e-10), 2)

def get_order_book_ratio(symbol):
    try:
        depth   = client.futures_order_book(symbol=symbol, limit=20)
        bid_vol = sum(float(b[1]) for b in depth["bids"])
        ask_vol = sum(float(a[1]) for a in depth["asks"])
        return round(bid_vol / (ask_vol + 1e-10), 2)
    except:
        return 1.0

_btc_cache = {"trend": "NEUTRAL", "ts": 0}
_BTC_CACHE_TTL = 300  # 5 dakika cache

def get_btc_trend():
    """
    BTC genel yönünü 1H + 4H çift zaman dilimiyle belirler.
    Sadece HER İKİ zaman dilimi de aynı yönü gösteriyorsa BULLISH/BEARISH döner.
    Biri NEUTRAL veya çelişiyorsa NEUTRAL döner — bu durumda sinyal ENGELLENMEZ.
    """
    import time as _t
    now = _t.time()
    if now - _btc_cache["ts"] < _BTC_CACHE_TTL:
        return _btc_cache["trend"]
    try:
        # 1H trend
        df1h = get_candles("BTCUSDT", "1h", 60)
        if df1h.empty:
            return "NEUTRAL"
        e21_1h = ema(df1h["close"], 21).iloc[-1]
        e55_1h = ema(df1h["close"], 55).iloc[-1]
        c1h    = df1h["close"].iloc[-1]
        adx1h, pdi1h, mdi1h = adx_val(df1h, 14)

        if e21_1h > e55_1h and c1h > e21_1h and adx1h > 18 and pdi1h > mdi1h:
            trend_1h = "BULLISH"
        elif e21_1h < e55_1h and c1h < e21_1h and adx1h > 18 and mdi1h > pdi1h:
            trend_1h = "BEARISH"
        else:
            trend_1h = "NEUTRAL"

        # 4H trend
        df4h = get_candles("BTCUSDT", "4h", 30)
        if df4h.empty:
            _btc_cache["trend"] = trend_1h
            _btc_cache["ts"] = now
            return trend_1h
        e21_4h = ema(df4h["close"], 21).iloc[-1]
        e55_4h = ema(df4h["close"], 55).iloc[-1]
        c4h    = df4h["close"].iloc[-1]
        adx4h, pdi4h, mdi4h = adx_val(df4h, 14)

        if e21_4h > e55_4h and c4h > e21_4h and adx4h > 18 and pdi4h > mdi4h:
            trend_4h = "BULLISH"
        elif e21_4h < e55_4h and c4h < e21_4h and adx4h > 18 and mdi4h > pdi4h:
            trend_4h = "BEARISH"
        else:
            trend_4h = "NEUTRAL"

        # Her iki zaman dilimi aynı yönü gösteriyorsa kesin karar
        if trend_1h == trend_4h and trend_1h != "NEUTRAL":
            result = trend_1h
        else:
            # Çelişki veya NEUTRAL — sinyali ENGELLEME, sadece NEUTRAL döndür
            result = "NEUTRAL"

        _btc_cache["trend"] = result
        _btc_cache["ts"] = now
        return result
    except:
        return "NEUTRAL"

# =============================================================================
# 4H TREND FİLTRESİ
# =============================================================================
_4h_cache: dict = {}  # {symbol: (trend, timestamp)}
_4H_CACHE_TTL = 240   # 4 dakika cache (4H mum yavaş değişir)

def get_4h_trend(symbol: str) -> str:
    """
    4 saatlik zaman diliminde ana trendi belirler.
    EMA 21 / EMA 55 / ADX kombinasyonu kullanır.
    Dönüş: 'BULLISH' | 'BEARISH' | 'NEUTRAL'
    """
    import time as _time
    now = _time.time()
    if symbol in _4h_cache:
        trend, ts = _4h_cache[symbol]
        if now - ts < _4H_CACHE_TTL:
            return trend
    try:
        df4h = get_candles(symbol, "4h", 60)
        if df4h.empty or len(df4h) < 30:
            return "NEUTRAL"
        e21_4h = ema(df4h["close"], 21)
        e55_4h = ema(df4h["close"], 55)
        adx4h, plus_di4h, minus_di4h = adx_val(df4h, 14)
        c4h = df4h["close"].iloc[-1]
        # Güçlü trend: EMA hizalaması + ADX > 20
        if (e21_4h.iloc[-1] > e55_4h.iloc[-1]
                and c4h > e21_4h.iloc[-1]
                and adx4h > 20
                and plus_di4h > minus_di4h):
            trend = "BULLISH"
        elif (e21_4h.iloc[-1] < e55_4h.iloc[-1]
                and c4h < e21_4h.iloc[-1]
                and adx4h > 20
                and minus_di4h > plus_di4h):
            trend = "BEARISH"
        else:
            trend = "NEUTRAL"
        _4h_cache[symbol] = (trend, now)
        return trend
    except Exception as e:
        logger.debug(f"4H trend hata {symbol}: {e}")
        return "NEUTRAL"

# =============================================================================
# VOLUME PROFILE (Hacim Yoğunluk Bölgeleri)
# =============================================================================

def get_volume_profile(symbol: str, interval: str = "5m", limit: int = 100):
    """
    Basit Volume Profile hesaplar.
    POC (Point of Control): En fazla hacim olan fiyat seviyesi.
    VAH (Value Area High): Hacmin %70'inin üst sınırı.
    VAL (Value Area Low): Hacmin %70'inin alt sınırı.
    Dönüş: dict {poc, vah, val, above_poc}
    """
    try:
        df = get_candles(symbol, interval, limit)
        if df.empty or len(df) < 20:
            return None
        price_min = df["low"].min()
        price_max = df["high"].max()
        if price_max <= price_min:
            return None
        # 30 fiyat seviyesine böl
        n_bins = 30
        bin_size = (price_max - price_min) / n_bins
        bins = [0.0] * n_bins
        for _, row in df.iterrows():
            lo, hi, vol = row["low"], row["high"], row["volume"]
            for i in range(n_bins):
                bin_lo = price_min + i * bin_size
                bin_hi = bin_lo + bin_size
                overlap = max(0, min(hi, bin_hi) - max(lo, bin_lo))
                if overlap > 0:
                    bins[i] += vol * (overlap / (hi - lo + 1e-10))
        # POC: en yüksek hacimli bin
        poc_idx  = bins.index(max(bins))
        poc      = price_min + (poc_idx + 0.5) * bin_size
        # Value Area: toplam hacmin %70'i
        total_vol = sum(bins)
        target    = total_vol * 0.70
        # POC'tan dışa doğru genişlet
        va_bins = [poc_idx]
        va_vol  = bins[poc_idx]
        lo_ptr, hi_ptr = poc_idx - 1, poc_idx + 1
        while va_vol < target:
            lo_add = bins[lo_ptr] if lo_ptr >= 0 else 0
            hi_add = bins[hi_ptr] if hi_ptr < n_bins else 0
            if lo_add >= hi_add and lo_ptr >= 0:
                va_bins.append(lo_ptr); va_vol += lo_add; lo_ptr -= 1
            elif hi_ptr < n_bins:
                va_bins.append(hi_ptr); va_vol += hi_add; hi_ptr += 1
            else:
                break
        val = price_min + (min(va_bins) + 0.0) * bin_size
        vah = price_min + (max(va_bins) + 1.0) * bin_size
        cur = df["close"].iloc[-1]
        return {
            "poc":       round(poc, 6),
            "vah":       round(vah, 6),
            "val":       round(val, 6),
            "above_poc": cur > poc,
            "in_va":     val <= cur <= vah,
            "cur":       round(cur, 6),
        }
    except Exception as e:
        logger.debug(f"Volume Profile hata {symbol}: {e}")
        return None

def get_funding_rate(symbol):
    try:
        result = client.futures_funding_rate(symbol=symbol, limit=1)
        if result:
            return float(result[-1]["fundingRate"])
    except:
        pass
    return 0.0

def get_dynamic_leverage(atr, price):
    vol_pct = (atr / price) * 100
    if vol_pct < 0.3:   return 5
    elif vol_pct < 0.6: return 8
    elif vol_pct < 1.0: return 10
    elif vol_pct < 2.0: return 8
    else:               return 5

def get_dynamic_tp_mult(atr, price):
    vol_pct = (atr / price) * 100
    if vol_pct < 0.6:   return 2.5   # Düşük volatilite - TP biraz yakın
    elif vol_pct < 1.5: return 2.0   # Normal volatilite
    else:               return 1.8   # Yüksek volatilite - TP daha yakın, daha çok kazan

def get_dynamic_risk_pct(base_risk):
    if consecutive_losses >= 5:   return base_risk * 0.25
    elif consecutive_losses >= 3: return base_risk * 0.50
    elif consecutive_losses == 0: return base_risk * 1.1
    return base_risk

def analyze(symbol, coin_info=None):
    null = {"direction": None, "symbol": symbol}
    p    = get_params()

    # Devre kesici kontrolü
    active, remaining = is_circuit_breaker_active()
    if active:
        return null

    # TelegramManager üzerinden pause kontrolü
    if tg_manager and tg_manager.is_paused:
        return null

    # --- SEANS FİLTRESİ ---
    utc_hour = datetime.now(timezone.utc).hour
    # Asya seansı (00:00-08:00 UTC): Düşük hacim, daha sıkı filtre
    # Londra (08:00-16:00 UTC) ve New York (13:00-21:00 UTC): Optimal
    if 0 <= utc_hour < 8:
        asia_session = True
    else:
        asia_session = False

    # --- 15m ANA TREND ---
    df15 = get_candles(symbol, "15m", 100)
    if df15.empty or len(df15) < 50:
        return null

    e9_15  = ema(df15["close"], 9)
    e21_15 = ema(df15["close"], 21)
    e50_15 = ema(df15["close"], 50)
    adx15, plus_di15, minus_di15 = adx_val(df15)
    c15      = df15["close"].iloc[-1]
    bb_width        = bollinger_width(df15)
    bb_width_chg    = bollinger_width_change(df15)   # Band Growth feature
    volume_m = coin_info["volume"] if coin_info else 0

    # Asya seansında daha sıkı volatilite filtresi (gevşetildi)
    bb_min = 1.7 if asia_session else 1.3  # 2.0/1.5 → 1.7/1.3
    if bb_width < bb_min:
        log_rejection(symbol, "bb_width_low", {"adx15": adx15, "bb_width": bb_width, "volume_m": volume_m, "price": c15})
        return null

    adx_threshold = 23 if asia_session else 20  # 25/22 → 23/20
    if volume_m < 20.0:
        adx_threshold = 25 if asia_session else 22  # 28/25 → 25/22
        if bb_width < (2.2 if asia_session else 1.8):  # 2.5/2.0 → 2.2/1.8
            log_rejection(symbol, "low_vol_bb_low", {"adx15": adx15, "bb_width": bb_width, "volume_m": volume_m, "price": c15})
            return null

    trend_up15 = (
        e9_15.iloc[-1] > e21_15.iloc[-1] > e50_15.iloc[-1]
        and c15 > e21_15.iloc[-1]  # Gevşetildi: e9 → e21 (pullback'e izin ver)
        and adx15 > adx_threshold
        and plus_di15 > minus_di15
    )
    trend_down15 = (
        e9_15.iloc[-1] < e21_15.iloc[-1] < e50_15.iloc[-1]
        and c15 < e21_15.iloc[-1]  # Gevşetildi: e9 → e21
        and adx15 > adx_threshold
        and minus_di15 > plus_di15
    )

    if not trend_up15 and not trend_down15:
        log_rejection(symbol, "no_15m_trend", {"adx15": adx15, "bb_width": bb_width, "volume_m": volume_m, "price": c15})
        return null

    # BTC Korelasyon Filtresi (PASIF — sadece log, müdahale yok)
    # BTC trend çok sık BULLISH dönüyor, tüm SHORT'ları engelliyor.
    # İleride veri biriktikten sonra aktif edilebilir.
    btc_trend = get_btc_trend()
    if trend_up15 and btc_trend == "BEARISH":
        logger.info(f"{symbol} LONG — BTC bearish (pasif, engelleme yok)")
    elif trend_down15 and btc_trend == "BULLISH":
        logger.info(f"{symbol} SHORT — BTC bullish (pasif, engelleme yok)")

    # --- 4H ANA TREND FİLTRESİ (PASIF — sadece log, müdahale yok) ---
    # İleride gerekirse aktif edilir. Şimdilik veri toplamak için izleniyor.
    trend_4h = get_4h_trend(symbol)
    if trend_up15 and trend_4h == "BEARISH":
        logger.info(f"{symbol} LONG — 4H bearish (pasif, engelleme yok)")
    elif trend_down15 and trend_4h == "BULLISH":
        logger.info(f"{symbol} SHORT — 4H bullish (pasif, engelleme yok)")

    # --- 5m SİNYAL ---
    df5 = get_candles(symbol, "5m", 150)
    if df5.empty or len(df5) < 50:
        return null

    e9_5  = ema(df5["close"], 9)
    e21_5 = ema(df5["close"], 21)
    e50_5 = ema(df5["close"], 50)
    vw5   = vwap_val(df5)
    rsi5  = rsi_val(df5["close"], 14)
    c5    = df5["close"].iloc[-1]
    atr5  = atr_val(df5, 14)

    if (atr5 / c5 * 100) < 0.03:  # Gevşetildi: 0.05 → 0.03
        return null

    bull5 = (
        e9_5.iloc[-1] > e21_5.iloc[-1] > e50_5.iloc[-1]
        and p["rsi5_min"] < rsi5 < p["rsi5_max"]
        and trend_up15
    )  # VWAP kaldırıldı — EMA hizalaması yeterli
    bear5 = (
        e9_5.iloc[-1] < e21_5.iloc[-1] < e50_5.iloc[-1]
        and p["rsi5_min"] < rsi5 < p["rsi5_max"]
        and trend_down15
    )  # VWAP kaldırıldı

    if not bull5 and not bear5:
        direction_15 = "LONG" if trend_up15 else "SHORT"
        log_rejection(symbol, "no_5m_signal", {
            "direction": direction_15, "adx15": adx15, "bb_width": bb_width,
            "rsi5": rsi5, "atr5": atr5, "volume_m": volume_m,
            "btc_trend": btc_trend, "trend_4h": trend_4h, "price": c5,
        })
        return null

    # --- 1m GİRİŞ ---
    df1 = get_candles(symbol, "1m", 100)
    if df1.empty or len(df1) < 30:
        return null

    e9_1    = ema(df1["close"], 9)
    rsi1    = rsi_val(df1["close"], 7)
    c1      = df1["close"].iloc[-1]
    atr1    = atr_val(df1, 14)
    hist    = macd_hist(df1["close"])
    rv      = relative_volume(df1, 20)
    ema9_1m = e9_1.iloc[-1]
    mom3c   = momentum_3c(df1)   # Predictive: son 3 mumun momentum skoru

    # Markov: Bir önceki trade sonucunu al (coin bazlı)
    try:
        import sqlite3 as _sq
        _mc = _sq.connect(os.path.join(os.path.dirname(os.path.abspath(__file__)), "trading.db"))
        _mr = _mc.execute(
            "SELECT result FROM trades WHERE symbol=? AND status!='OPEN' ORDER BY id DESC LIMIT 1",
            (symbol,)
        ).fetchone()
        _mc.close()
        prev_result = _mr[0] if _mr else "NONE"
    except Exception:
        prev_result = "NONE"

    rv_min = p.get("vol_ratio_min", 1.2)
    if volume_m < 20.0:
        rv_min = 1.3  # Gevşetildi: 1.5 → 1.3

    pullback_ok = abs(c1 - ema9_1m) / (ema9_1m + 1e-10) < 0.012  # Gevşetildi: 0.006 → 0.012
    rv_ok       = rv > rv_min

    funding = get_funding_rate(symbol)
    funding_favorable = 1
    if bull5 and funding > 0.001:   # Gevşetildi: 0.0005 → 0.001
        log_rejection(symbol, "funding_unfavorable", {
            "direction": "LONG", "adx15": adx15, "bb_width": bb_width,
            "rsi5": rsi5, "atr5": atr5, "rv": rv, "funding": round(funding * 100, 4),
            "volume_m": volume_m, "btc_trend": btc_trend, "trend_4h": trend_4h, "price": c1,
        })
        return null
    if bear5 and funding < -0.001:  # Gevşetildi: -0.0005 → -0.001
        log_rejection(symbol, "funding_unfavorable", {
            "direction": "SHORT", "adx15": adx15, "bb_width": bb_width,
            "rsi5": rsi5, "atr5": atr5, "rv": rv, "funding": round(funding * 100, 4),
            "volume_m": volume_m, "btc_trend": btc_trend, "trend_4h": trend_4h, "price": c1,
        })
        return null

    ob_ratio = get_order_book_ratio(symbol)
    # OB filtresi PASIF — sadece log, engelleme yok
    if bull5 and ob_ratio < 0.55:
        logger.info(f"{symbol} LONG — düşük OB ({ob_ratio:.2f}) pasif")
    elif bear5 and ob_ratio > 1.45:
        logger.info(f"{symbol} SHORT — yüksek OB ({ob_ratio:.2f}) pasif")

    # --- VOLUME PROFILE KONTROLÜ ---
    # Fiyat Value Area içinde veya POC yakınında olmalı
    vp = get_volume_profile(symbol, "5m", 100)
    vp_score = 0  # 0-2 puan, sinyal skoruna eklenecek
    poc_val = None
    vah_val = None
    val_val = None
    if vp:
        poc_val = vp["poc"]
        vah_val = vp["vah"]
        val_val = vp["val"]
        # LONG: fiyat VAL üzerinde ve POC üzerinde olmalı
        if bull5:
            if c1 > vp["poc"] and c1 > vp["val"]:
                vp_score = 2  # İdeal: POC üzerinde
            elif c1 > vp["val"]:
                vp_score = 1  # Kabul edilebilir: VA içinde
            else:
                # Fiyat Value Area altında — zayıf ama ENGELLEME, sadece skor 0
                logger.info(f"{symbol} LONG — VP altında (c={c1:.4f} val={vp['val']:.4f}) — devam")
                vp_score = 0
        # SHORT: fiyat VAH altında ve POC altında olmalı
        if bear5:
            if c1 < vp["poc"] and c1 < vp["vah"]:
                vp_score = 2
            elif c1 < vp["vah"]:
                vp_score = 1
            else:
                logger.info(f"{symbol} SHORT — VP üzerinde (c={c1:.4f} vah={vp['vah']:.4f}) — devam")
                vp_score = 0

    direction = None
    if (bull5
            and p["rsi1_min"] < rsi1 < p["rsi1_max"]):
        direction = "LONG"  # Gevşetildi: c1>ema9, hist>0, rv_ok, pullback_ok kaldırıldı
    elif (bear5
            and p["rsi1_min"] < rsi1 < p["rsi1_max"]):
        direction = "SHORT"

    if not direction:
        direction_15 = "LONG" if bull5 else "SHORT"
        log_rejection(symbol, "rsi1_out_of_range", {
            "direction": direction_15, "adx15": adx15, "bb_width": bb_width,
            "rsi5": rsi5, "rsi1": rsi1, "atr5": atr5, "rv": rv,
            "funding": round(funding * 100, 4),
            "volume_m": volume_m, "btc_trend": btc_trend, "trend_4h": trend_4h, "price": c1,
        })
        return null

    return {
        "symbol":    symbol,
        "direction": direction,
        "price":     c1,
        "atr":       atr1,
        "atr5":      atr5,
        "adx15":     round(adx15, 1),
        "bb_width":  bb_width,
        "rsi5":      rsi5,
        "rsi1":      rsi1,
        "rv":        rv,
        "ob_ratio":  ob_ratio,
        "btc_trend": btc_trend,
        "trend_4h":  trend_4h,
        "funding":   round(funding * 100, 4),
        "funding_favorable": funding_favorable,
        "change_24h": coin_info["change"] if coin_info else 0,
        "volume_m":   volume_m,
        "vp_poc":       poc_val,
        "vp_vah":       vah_val,
        "vp_val":       val_val,
        "vp_score":     vp_score,
        "bb_width_chg": bb_width_chg,   # Band Growth: BB genişleme hızı
        "momentum_3c":  mom3c,           # Predictive: son 3 mum momentum skoru
        "prev_result":  prev_result,     # Markov: önceki trade sonucu
    }

def get_hot_coins():
    try:
        tickers    = client.get_ticker()
        candidates = []
        p          = get_params()
        for t in tickers:
            sym = t["symbol"]
            if not sym.isascii():
                continue
            if not sym.endswith("USDT") or sym in BLACKLIST:
                continue
            if VALID_FUTURES_SYMBOLS and sym not in VALID_FUTURES_SYMBOLS:
                continue
            vol   = float(t["quoteVolume"]) / 1e6
            chg   = abs(float(t["priceChangePercent"]))
            price = float(t["lastPrice"])
            if vol < p.get("min_volume_m", 5.0):
                continue
            candidates.append({
                "symbol": sym,
                "volume": vol,
                "change": float(t["priceChangePercent"]),
                "price":  price,
                "score":  vol * 0.5 + chg * 0.5,
                "greedy_score": 0,   # analyze() sonrası ML skoru ile güncellenecek
            })
        candidates.sort(key=lambda x: x["score"], reverse=True)
        logger.info(f"Hot coins ({len(candidates)} aday): {[c['symbol'] for c in candidates[:6]]}")
        return candidates
    except Exception as e:
        logger.error(f"Ticker hata: {e}")
        return []

def floor_step(val, step):
    if step == 0:
        return val
    prec = len(str(step).rstrip("0").split(".")[-1]) if "." in str(step) else 0
    return round(math.floor(val / step) * step, prec)

# =============================================================================
# EMİR YÖNETİMİ
# =============================================================================

def place_order(sig):
    symbol    = sig["symbol"]
    direction = sig["direction"]
    price     = sig["price"]
    atr       = sig["atr"]

    if atr <= 0:
        return False

    # ML SİNYAL SKORU (PASIF — veri biriktirme aşamasında engelleme yok)
    # Yeterli veri biriktikten sonra aktif edilecek.
    if ML_SCORER_AVAILABLE:
        ml_ok, ml_score = ml_should_trade(sig)
        sig["ml_score"] = ml_score
        if not ml_ok:
            logger.info(f"{symbol} {direction} ML düşük skor (skor={ml_score}) — pasif, devam ediliyor")
        else:
            logger.info(f"{symbol} {direction} ML skoru: {ml_score}/100")
    else:
        sig["ml_score"] = 50

    p            = get_params()
    account_usdt = get_balance()
    leverage     = get_dynamic_leverage(atr, price)
    tp_mult      = get_dynamic_tp_mult(atr, price)
    risk_pct     = get_dynamic_risk_pct(p["risk_pct"])

    sl_dist = atr * p["sl_atr_mult"]
    tp_dist = atr * tp_mult

    if direction == "LONG":
        sl   = price - sl_dist
        tp   = price + tp_dist
        side = SIDE_BUY
    else:
        sl   = price + sl_dist
        tp   = price - tp_dist
        side = SIDE_SELL

    if sl <= 0 or tp <= 0:
        return False

    if PAPER_MODE:
        step = tick = 0.001
        min_not = 5.0
    else:
        try:
            step, tick, min_not = get_filters(symbol)
        except Exception as e:
            logger.error(f"{symbol} filtre: {e}")
            return False
        sl = floor_step(sl, tick)
        tp = floor_step(tp, tick)

    risk_usdt = account_usdt * (risk_pct / 100)
    qty = round(risk_usdt / (sl_dist + 1e-10), 3) if PAPER_MODE else floor_step(risk_usdt / (sl_dist + 1e-10), step)

    if qty <= 0:
        return False

    if not PAPER_MODE:
        if qty * price / leverage > account_usdt * 0.9:
            logger.warning(f"{symbol} yetersiz margin")
            return False

    logger.info(
        f"{'[PAPER] ' if PAPER_MODE else ''}ORDER → {symbol} {direction} x{leverage} "
        f"qty={qty} sl={sl:.4f} tp={tp:.4f} ADX={sig['adx15']} "
        f"BB%={sig['bb_width']} RV={sig['rv']} OB={sig['ob_ratio']} BTC={sig['btc_trend']}"
    )

    if PAPER_MODE:
        fill = price
        sl2  = sl
        tp2  = tp
    else:
        try:
            try:
                client.futures_change_leverage(symbol=symbol, leverage=leverage)
            except:
                pass
            order = client.futures_create_order(
                symbol=symbol, side=side, type="MARKET", quantity=qty
            )
            fill = float(order.get("avgPrice", price)) or price
            try:
                client.futures_cancel_all_open_orders(symbol=symbol)
                time.sleep(0.3)
            except:
                pass
            sl_side = SIDE_SELL if direction == "LONG" else SIDE_BUY
            sl2 = floor_step((fill - sl_dist) if direction == "LONG" else (fill + sl_dist), tick)
            tp2 = floor_step((fill + tp_dist) if direction == "LONG" else (fill - tp_dist), tick)
            try:
                client.futures_create_order(symbol=symbol, side=sl_side,
                    type="STOP_MARKET", stopPrice=str(sl2), closePosition=True)
                client.futures_create_order(symbol=symbol, side=sl_side,
                    type="TAKE_PROFIT_MARKET", stopPrice=str(tp2), closePosition=True)
            except Exception as e:
                logger.warning(f"SL/TP hata: {e}")
        except Exception as e:
            logger.error(f"Order hata {symbol}: {e}")
            tg(f"⚠️ Order fail: {symbol}\n{e}")
            return False

    trade_entry = fill if not PAPER_MODE else price
    trade_sl    = sl2  if not PAPER_MODE else sl
    trade_tp    = tp2  if not PAPER_MODE else tp

    trade_data = {
        "symbol": symbol, "direction": direction,
        "entry": trade_entry, "sl": trade_sl, "tp": trade_tp,
        "qty": qty, "leverage": leverage, "risk_usdt": risk_usdt,
        "rsi5": sig["rsi5"], "rsi1": sig["rsi1"],
        "vol_ratio": sig["rv"], "change_24h": sig["change_24h"],
        "volume_m": sig["volume_m"],
        "params_version": p.get("version", 1),
        "adx15": sig["adx15"], "bb_width_pct": sig["bb_width"],
        "ob_ratio": sig["ob_ratio"], "btc_trend": sig["btc_trend"],
        "funding": sig["funding"], "funding_favorable": sig["funding_favorable"],
        "rv":          sig["rv"],
        "bb_width_chg": sig.get("bb_width_chg", 0),
        "momentum_3c":  sig.get("momentum_3c", 0),
        "prev_result":  sig.get("prev_result", "NONE"),
    }
    tid = save_trade(trade_data)
    open_trades[symbol] = {
        "trade_id":     tid,
        "symbol":       symbol,
        "direction":    direction,
        "entry":        trade_entry,
        "sl":           trade_sl,
        "tp":           trade_tp,
        "qty":          qty,
        "leverage":     leverage,
        "risk":         risk_usdt,
        "paper":        PAPER_MODE,
        "breakeven_set": False,
        "partial_done": False,
        "open_time":    datetime.now(timezone.utc),
        "trade_data":   trade_data,
    }
    tracker.add_trade(symbol, open_trades[symbol])

    rr           = round(tp_dist / (sl_dist + 1e-10), 2)
    ml_score     = sig.get("ml_score", 50)
    trend_4h     = sig.get("trend_4h", "N/A")
    vp_score     = sig.get("vp_score", 0)
    greedy_score = sig.get("greedy_score", ml_score)
    mom3c        = sig.get("momentum_3c", 0)
    bb_chg       = sig.get("bb_width_chg", 0)
    prev_res     = sig.get("prev_result", "NONE")
    dir_emoji    = "📈 LONG" if direction == "LONG" else "📉 SHORT"
    tg(
        f"⚡ <b>FUTURES SCALP v4.4</b>\n\n"
        f"{dir_emoji} x{leverage} — <b>{symbol}</b>\n\n"
        f"💰 Entry: <code>{trade_entry:.6f}</code>\n"
        f"🛑 Stop:  <code>{trade_sl}</code>\n"
        f"🎯 TP:    <code>{trade_tp}</code>\n"
        f"📊 ADX: {sig['adx15']} | BB%: {sig['bb_width']} | RV: {sig['rv']}x\n"
        f"📖 OB: {sig['ob_ratio']} | BTC: {sig['btc_trend']} | 4H: {trend_4h}\n"
        f"📐 RR: 1:{rr} | Risk: ${risk_usdt:.2f} | VP: {vp_score}/2\n"
        f"🤖 ML: <code>{ml_score}/100</code> | Greedy: <code>{greedy_score:.0f}</code>\n"
        f"📈 Mom3C: {mom3c:+.2f} | BBΔ: {bb_chg:+.2f} | Prev: {prev_res}\n"
        f"💸 F: {sig['funding']}%\n\n"
        f"💰 Bakiye: ${get_balance():.2f}"
    )
    notify_n8n("trade_open", {
        "symbol": symbol, "direction": direction,
        "entry": round(trade_entry, 6), "sl": trade_sl, "tp": trade_tp,
        "leverage": leverage, "rr": rr, "ml_score": ml_score,
        "adx": sig.get("adx15"), "balance": round(get_balance(), 2),
        "message": f"⚡ <b>n8n</b> | {dir_emoji} <b>{symbol}</b> x{leverage}\nEntry: <code>{trade_entry:.6f}</code> | RR 1:{rr} | ML:{ml_score}"
    })
    return True

# =============================================================================
# POZİSYON TAKİBİ
# =============================================================================

def update_trailing_stop(symbol, t, price):
    """
    Gelişmiş Momentum Bazlı Trailing Stop:
    - 1R: Breakeven (giriş + küçük tampon)
    - 1.5R: ATR bazlı trailing başlar
    - 2R: Daha sıkı trailing (ATR * 0.8)
    - 3R+: Çok sıkı trailing (ATR * 0.5) — kârı koru
    - Momentum kaybı: RSI zıt yöne dönerse trailing sıkılaşır
    """
    entry   = t["entry"]
    sl      = t["sl"]
    sl_dist = abs(entry - sl)

    # Mevcut ATR'yi al (hızlı hesap)
    try:
        df1m = get_candles(symbol, "1m", 20)
        cur_atr = atr_val(df1m, 14) if not df1m.empty else sl_dist
        cur_rsi = rsi_val(df1m["close"], 7) if not df1m.empty else 50
    except:
        cur_atr = sl_dist
        cur_rsi = 50

    if t["direction"] == "LONG":
        pnl_r = (price - entry) / (sl_dist + 1e-10)

        # Breakeven: 1R
        if pnl_r >= 1.0 and not t.get("breakeven_set"):
            new_sl = entry + sl_dist * 0.08  # Giriş + %8 tampon
            open_trades[symbol]["sl"] = new_sl
            open_trades[symbol]["breakeven_set"] = True
            logger.info(f"{symbol} BREAKEVEN → {new_sl:.6f}")
            tg(f"🔒 <b>Breakeven</b> — {symbol}\n<code>{new_sl:.6f}</code>")

        # 1.5R: ATR bazlı trailing başlar
        elif pnl_r >= 1.5:
            # Momentum kontrolu: RSI düşüyorsa daha sıkı trailing
            if cur_rsi < 45:  # Momentum zayıflıyor
                atr_mult = 0.6
            elif pnl_r >= 3.0:
                atr_mult = 0.5  # 3R+ çok sıkı
            elif pnl_r >= 2.0:
                atr_mult = 0.8  # 2R sıkı
            else:
                atr_mult = 1.2  # 1.5R normal

            trailing = price - cur_atr * atr_mult
            if trailing > open_trades[symbol]["sl"]:
                old_sl = open_trades[symbol]["sl"]
                open_trades[symbol]["sl"] = trailing
                if trailing - old_sl > cur_atr * 0.3:  # Anlamlı hareket varsa log
                    logger.info(f"{symbol} TRAILING → {trailing:.6f} (R={pnl_r:.2f} ATRx{atr_mult})")

    elif t["direction"] == "SHORT":
        pnl_r = (entry - price) / (sl_dist + 1e-10)

        # Breakeven: 1R
        if pnl_r >= 1.0 and not t.get("breakeven_set"):
            new_sl = entry - sl_dist * 0.08
            open_trades[symbol]["sl"] = new_sl
            open_trades[symbol]["breakeven_set"] = True
            logger.info(f"{symbol} BREAKEVEN → {new_sl:.6f}")
            tg(f"🔒 <b>Breakeven</b> — {symbol}\n<code>{new_sl:.6f}</code>")

        # 1.5R: ATR bazlı trailing başlar
        elif pnl_r >= 1.5:
            if cur_rsi > 55:  # SHORT'ta RSI yüksekse momentum zayıflıyor
                atr_mult = 0.6
            elif pnl_r >= 3.0:
                atr_mult = 0.5
            elif pnl_r >= 2.0:
                atr_mult = 0.8
            else:
                atr_mult = 1.2

            trailing = price + cur_atr * atr_mult
            if trailing < open_trades[symbol]["sl"]:
                old_sl = open_trades[symbol]["sl"]
                open_trades[symbol]["sl"] = trailing
                if old_sl - trailing > cur_atr * 0.3:
                    logger.info(f"{symbol} TRAILING → {trailing:.6f} (R={pnl_r:.2f} ATRx{atr_mult})")

def handle_partial_exit(symbol, t, price):
    """
    3 Aşamalı Kısmi Çıkış:
    - 1R: %40 realize et (ilk aşama)
    - 1.5R: %30 daha realize et (ikinci aşama)
    - Kalan %30: Trailing stop ile takip et
    """
    entry   = t["entry"]
    sl      = t["sl"]
    sl_dist = abs(entry - sl)
    pnl_r   = 0

    if t["direction"] == "LONG":
        pnl_r = (price - entry) / (sl_dist + 1e-10)
    else:
        pnl_r = (entry - price) / (sl_dist + 1e-10)

    # Aşama 1: 1R'de %40 çık
    if pnl_r >= 1.0 and not t.get("partial1_done"):
        partial_qty = t["qty"] * 0.4
        partial_pnl = t["risk"] * 1.0 * 0.4
        commission  = entry * partial_qty * COMMISSION_RT
        net_partial = round(partial_pnl - commission, 4)

        if t.get("paper"):
            update_paper_balance(net_partial)

        open_trades[symbol]["qty"]                 = t["qty"] * 0.6
        open_trades[symbol]["risk"]                = t["risk"] * 0.6
        open_trades[symbol]["partial1_done"]        = True
        # Kısmi çıkışta realize edilen net PNL'i biriktir
        prev = open_trades[symbol].get("partial_pnl_realized", 0)
        open_trades[symbol]["partial_pnl_realized"] = round(prev + net_partial, 4)

        logger.info(f"{symbol} KİSMİ ÇIKIŞ 1 — %40 realize, net: ${net_partial:.3f}")
        tg(
            f"💫 <b>Kısmi Çıkış 1/2</b> — {symbol}\n"
            f"1R kâra ulaşıldı, %40 realize edildi.\n"
            f"Net: <code>+{net_partial:.3f}$</code>\n"
            f"Kalan %60: 1.5R bekleniyor..."
        )

    # Aşama 2: 1.5R'de %30 daha çık
    elif pnl_r >= 1.5 and t.get("partial1_done") and not t.get("partial2_done"):
        partial_qty = t["qty"] * 0.5   # Kalan miktarın yarısı (%30 toplam)
        partial_pnl = t["risk"] * 1.5 * 0.3
        commission  = entry * partial_qty * COMMISSION_RT
        net_partial = round(partial_pnl - commission, 4)

        if t.get("paper"):
            update_paper_balance(net_partial)

        open_trades[symbol]["qty"]                 = t["qty"] * 0.5
        open_trades[symbol]["risk"]                = t["risk"] * 0.5
        open_trades[symbol]["partial2_done"]        = True
        open_trades[symbol]["partial_done"]         = True   # Eski uyumluluk
        # Kısmi çıkışta realize edilen net PNL'i biriktir
        prev = open_trades[symbol].get("partial_pnl_realized", 0)
        open_trades[symbol]["partial_pnl_realized"] = round(prev + net_partial, 4)

        logger.info(f"{symbol} KİSMİ ÇIKIŞ 2 — %30 daha realize, net: ${net_partial:.3f}")
        tg(
            f"✨ <b>Kısmi Çıkış 2/2</b> — {symbol}\n"
            f"1.5R kâra ulaşıldı, %30 daha realize edildi.\n"
            f"Net: <code>+{net_partial:.3f}$</code>\n"
            f"Kalan %30: Trailing stop ile takip"
        )

def monitor_trades():
    global closed_trade_counter, consecutive_losses
    if not open_trades:
        return
    to_close = []

    for symbol, t in list(open_trades.items()):
        try:
            price = float(client.get_symbol_ticker(symbol=symbol)["price"])

            update_trailing_stop(symbol, t, price)
            handle_partial_exit(symbol, t, price)

            open_time    = t.get("open_time", datetime.now(timezone.utc))
            hold_minutes = (datetime.now(timezone.utc) - open_time).total_seconds() / 60
            force_close  = hold_minutes >= MAX_HOLD_HOURS * 60

            if t.get("paper"):
                hit_sl = (
                    (t["direction"] == "LONG"  and price <= t["sl"]) or
                    (t["direction"] == "SHORT" and price >= t["sl"])
                )
                hit_tp = (
                    (t["direction"] == "LONG"  and price >= t["tp"]) or
                    (t["direction"] == "SHORT" and price <= t["tp"])
                )
                if not hit_sl and not hit_tp and not force_close:
                    continue
                exit_price = price
                if force_close and not hit_sl and not hit_tp:
                    result = "WIN" if (
                        (t["direction"] == "LONG" and price > t["entry"]) or
                        (t["direction"] == "SHORT" and price < t["entry"])
                    ) else "LOSS"
                else:
                    result = "WIN" if hit_tp else "LOSS"
            else:
                hit_sl = (
                    (t["direction"] == "LONG"  and price <= t["sl"]) or
                    (t["direction"] == "SHORT" and price >= t["sl"])
                )
                hit_tp = (
                    (t["direction"] == "LONG"  and price >= t["tp"]) or
                    (t["direction"] == "SHORT" and price <= t["tp"])
                )
                binance_closed = False
                binance_price  = price
                try:
                    positions = client.futures_position_information(symbol=symbol)
                    for pos in positions:
                        if pos["symbol"] == symbol and float(pos.get("positionAmt", 1)) == 0:
                            binance_closed = True
                            try:
                                hist = client.futures_account_trades(symbol=symbol, limit=10)
                                if hist:
                                    binance_price = float(hist[-1]["price"])
                            except:
                                pass
                except:
                    pass

                if not binance_closed and not hit_sl and not hit_tp and not force_close:
                    continue

                if binance_closed:
                    exit_price = binance_price
                    pnl_raw    = (exit_price - t["entry"]) if t["direction"] == "LONG" else (t["entry"] - exit_price)
                    result     = "WIN" if pnl_raw > 0 else "LOSS"
                elif force_close:
                    exit_price = price
                    pnl_raw    = (price - t["entry"]) if t["direction"] == "LONG" else (t["entry"] - price)
                    result     = "WIN" if pnl_raw > 0 else "LOSS"
                    try:
                        close_side = SIDE_SELL if t["direction"] == "LONG" else SIDE_BUY
                        client.futures_create_order(symbol=symbol, side=close_side,
                            type="MARKET", quantity=t["qty"], reduceOnly=True)
                    except Exception as e:
                        logger.error(f"Zaman bazlı çıkış emir hatası {symbol}: {e}")
                else:
                    exit_price = price
                    result     = "WIN" if hit_tp else "LOSS"

            analysis = tracker.remove_trade(symbol, exit_price)
            if analysis:
                save_analysis(analysis)
            to_close.append(symbol)

            # Gerçek exit_price ile PNL hesapla (risk bazlı değil)
            if t["direction"] == "LONG":
                gross_pnl = (exit_price - t["entry"]) * t["qty"]
            else:
                gross_pnl = (t["entry"] - exit_price) * t["qty"]
            # Komisyon: sadece kalan qty'nin round-trip maliyeti
            commission_cost = t["entry"] * t["qty"] * COMMISSION_RT
            # Kısmi çıkışlarda daha önce realize edilen PNL'i de dahil et
            partial_realized = t.get("partial_pnl_realized", 0)
            net_pnl = round(gross_pnl - commission_cost + partial_realized, 4)

            # Net toplam pozitifse WIN — kısmi çıkış kazancı dahil
            # (SL'e vursa bile kısmi realize ile artıda kalınabilir)
            if partial_realized != 0:
                result = "WIN" if net_pnl > 0 else "LOSS"

            # DB'ye kapat (net_pnl de kaydedilsin)
            close_trade(t["trade_id"], exit_price, result, net_pnl)

            if t.get("paper"):
                # partial_realized kısmi çıkışlarda zaten eklendi; sadece kalan pozisyon PNL'ini güncelle
                update_paper_balance(round(gross_pnl - commission_cost, 4))

            save_pattern(
                t.get("trade_data", {}), result, net_pnl,
                partial_exit=1 if t.get("partial_done") else 0,
                hold_minutes=round(hold_minutes, 1)
            )

            if result == "WIN":
                consecutive_losses = 0
            else:
                consecutive_losses += 1
                if consecutive_losses >= CIRCUIT_BREAKER_LOSSES:
                    trigger_circuit_breaker()

            # AI Brain: post-trade MAE/MFE analizi
            try:
                import sqlite3 as _sq
                _c  = _sq.connect(os.path.join(os.path.dirname(os.path.abspath(__file__)), "trading.db"))
                _c.row_factory = _sq.Row
                _row = _c.execute(
                    "SELECT id FROM trades WHERE symbol=? AND status!='OPEN' ORDER BY id DESC LIMIT 1",
                    (symbol,)
                ).fetchone()
                _c.close()
                if _row:
                    threading.Thread(
                        target=post_trade_analysis,
                        args=(_row["id"],),
                        daemon=True
                    ).start()
            except Exception:
                pass

            # Drawdown kontrolü — veri biriktirme aşamasında devre dışı
            # if tg_manager:
            #     tg_manager.check_drawdown(get_balance())

            actual_rr     = analysis.get("actual_rr", 0)      if analysis else 0
            max_rr        = analysis.get("max_rr_reached", 0) if analysis else 0
            insight       = analysis.get("insight", "")       if analysis else ""
            emoji         = "✅" if result == "WIN" else "❌"
            time_label    = f"⏱ {int(hold_minutes)}dk" + (" [ZAMAN BAZLI]" if force_close else "")
            partial_label = " | 💫 Kısmi çıkış yapıldı" if t.get("partial_done") else ""

            _bal_after = get_balance()  # DB'den güncel bakıye (update_paper_balance sonrası)
            partial_line = f"Kısmi Realize: <code>+{partial_realized:.3f}$</code>\n" if partial_realized else ""
            tg(
                f"{emoji} <b>{result}</b> — {symbol}\n"
                f"Çıkış: <code>{exit_price:.6f}</code>\n"
                f"Gross: <code>{'+'if gross_pnl>0 else ''}{gross_pnl:.3f}$</code>\n"
                f"Komisyon: <code>-{commission_cost:.3f}$</code>\n"
                f"{partial_line}"
                f"Net Toplam: <code>{'+'if net_pnl>0 else ''}{net_pnl:.3f}$</code>\n"
                f"RR: {actual_rr} | Max RR: {max_rr}\n"
                f"{time_label}{partial_label}\n"
                f"💡 {insight[:100] if insight else ''}\n\n"
                f"💰 Bakiye: ${_bal_after:.2f}"
            )
            logger.info(f"{result}: {symbol} @ {exit_price:.6f} | Net: {net_pnl:.3f}$ | Ardışık Kayıp: {consecutive_losses}")

            closed_trade_counter += 1
            if closed_trade_counter % AI_BRAIN_THRESHOLD == 0:
                run_ai_brain()
            # ML modeli yeniden eğit
            if closed_trade_counter % ML_RETRAIN_INTERVAL == 0:
                threading.Thread(target=run_ml_retrain, daemon=True).start()

        except Exception as e:
            logger.error(f"Monitor {symbol}: {e}")

    for s in to_close:
        open_trades.pop(s, None)

# =============================================================================
# ANA DÖNGÜ
# =============================================================================

PID_FILE = "/tmp/aurvex_scalp_bot.pid"

def _acquire_lock():
    """Tek instance garantisi. Zaten çalışan bir process varsa çık."""
    import signal as _sig
    if os.path.exists(PID_FILE):
        try:
            old_pid = int(open(PID_FILE).read().strip())
            # Process gerçekten yaşıyor mu?
            os.kill(old_pid, 0)
            # Yaşıyorsa: bu instance fazladan, çık
            logger.warning(f"Zaten çalışan bir instance var (PID {old_pid}). Bu process kapatılıyor.")
            raise SystemExit(0)
        except (ProcessLookupError, ValueError):
            # Eski PID dosyası kalıntısı, temizle
            os.remove(PID_FILE)
    # PID yaz
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    # Çıkışta PID dosyasını sil
    import atexit
    atexit.register(lambda: os.path.exists(PID_FILE) and os.remove(PID_FILE))

def main():
    _acquire_lock()
    init_db()
    init_tracker_tables()
    if PAPER_MODE:
        init_paper_account()
    load_futures_symbols()
    if PAPER_MODE:
        sync_open_paper_trades()
    else:
        sync_open_positions()
    tracker.start()

    # AI Brain bağlantısı
    if AI_BRAIN_AVAILABLE:
        set_client(client, tg)

    # TelegramManager başlat
    if tg_manager:
        tg_manager.start(
            get_balance_fn=get_balance,
            get_open_trades_fn=lambda: open_trades,
        )
        tg_manager.send_startup(
            get_balance(), get_params(),
            len(VALID_FUTURES_SYMBOLS), AI_BRAIN_AVAILABLE
        )
    else:
        # TelegramManager yoksa eski yöntemle gönder (fallback)
        tg(
            f"🟢 <b>AURVEX BOT AKTİF</b>\n"
            f"⏰ {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}\n\n"
            f"💰 Bakiye: ${get_balance():.2f}\n"
            f"🕐 7/24 tarama aktif\n"
            f"✅ {len(VALID_FUTURES_SYMBOLS)} sembol\n"
            f"⛔ Devre Kesici: 5 kayıp = 120dk"
        )

    # ML Scorer başlat
    if ML_SCORER_AVAILABLE:
        threading.Thread(target=run_ml_retrain, daemon=True).start()
        logger.info("ML Sinyal Skoru aktif")

    logger.info("Bot v4.3 başladı")

    last_scan_time = 0
    last_heartbeat = time.time()

    while True:
        try:
            monitor_trades()
            now = time.time()

            # Saatlik heartbeat
            if now - last_heartbeat >= 3600:
                tg(
                    f"💓 <b>BOT AKTİF</b> | "
                    f"{datetime.now(timezone.utc).strftime('%H:%M')} UTC | "
                    f"Tarama devam ediyor..."
                )
                last_heartbeat = now

            # Finish modu: tüm trade'ler kapandıysa bildirim gönder
            if tg_manager and tg_manager.is_finish_mode and len(open_trades) == 0:
                tg_manager.notify_finish_complete(get_balance())

            if now - last_scan_time >= SCAN_INTERVAL:
                last_scan_time = now

                # n8n/Flask üzerinden gelen kontrol komutlarını oku
                try:
                    ctrl = get_bot_control()
                    if tg_manager:
                        tg_manager._paused      = ctrl["paused"]
                        tg_manager._finish_mode = ctrl["finish_mode"]
                except Exception:
                    pass

                active_cb, remaining_cb = is_circuit_breaker_active()
                if active_cb:
                    logger.info(f"Devre kesici aktif — {remaining_cb} dakika kaldı")
                elif tg_manager and tg_manager.is_paused:
                    logger.info("Bot duraklatıldı — tarama atlandı")
                elif len(open_trades) < MAX_OPEN:
                    hot_coins.clear()
                    hot_coins.extend(get_hot_coins())

                    # ── GREEDY SINYAL SIRALAMA ──────────────────────────────
                    # Tüm adayları tara, geçerli sinyalleri topla
                    # ML skoru + momentum_3c + bb_width_chg ile sırala
                    # En yüksek beklenen değerli sinyali önce işle
                    pending_signals = []
                    for coin in hot_coins:
                        sym = coin["symbol"]
                        if sym in open_trades or sym in scan_cooldown:
                            continue
                        if len(open_trades) + len(pending_signals) >= MAX_OPEN:
                            break
                        sig = analyze(sym, coin)
                        if sig["direction"]:
                            # Greedy skoru: ML + momentum_3c (normalize) + bb_width_chg (normalize)
                            ml_s   = sig.get("ml_score", 50)
                            mom_s  = sig.get("momentum_3c", 0) * 10   # -30 ile +30
                            bb_s   = min(max(sig.get("bb_width_chg", 0) * 5, -15), 15)  # -15 ile +15
                            # Yöne göre momentum ve bb skoru: LONG için pozitif, SHORT için negatif tercih
                            if sig["direction"] == "LONG":
                                greedy = ml_s + mom_s + bb_s
                            else:
                                greedy = ml_s - mom_s + bb_s
                            sig["greedy_score"] = round(greedy, 2)
                            pending_signals.append(sig)

                    # Greedy skora göre sırala (en yüksek önce)
                    pending_signals.sort(key=lambda x: x["greedy_score"], reverse=True)

                    for sig in pending_signals:
                        sym = sig["symbol"]
                        if sym in open_trades or sym in scan_cooldown:
                            continue
                        if len(open_trades) >= MAX_OPEN:
                            break
                        logger.info(f"GREEDY: {sym} {sig['direction']} greedy={sig['greedy_score']} ml={sig.get('ml_score',50)}")
                        ok = place_order(sig)
                        if ok:
                            scan_cooldown[sym] = now + 300  # Cooldown: 5 dk

            expired = [s for s, t in scan_cooldown.items() if now > t]
            for s in expired:
                scan_cooldown.pop(s, None)

            time.sleep(MONITOR_TICK)

        except KeyboardInterrupt:
            logger.info("Bot durduruldu.")
            tracker.stop()
            if tg_manager:
                tg_manager.stop()
            break
        except Exception as e:
            logger.error(f"Ana döngü hata: {e}")
            time.sleep(30)

if __name__ == "__main__":
    main()
