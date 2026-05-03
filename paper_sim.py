"""
paper_sim.py — AX Scalp Engine Paper Trade Simülatörü v4
Mevcut app.py dashboard'una entegre çalışır.
trading.db'ye yazar — dashboard otomatik okur.
"""
import time, os, sys, threading

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

import numpy as np
import requests
import pandas as pd
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
import warnings
warnings.filterwarnings("ignore")
from config import (
    COIN_UNIVERSE, BAD_HOURS_UTC,
    ADX_MIN_THRESHOLD, SL_ATR_MULT, MIN_RR, RISK_PCT,
    BREAKEVEN_ENABLED, BREAKEVEN_OFFSET_PCT, TP1_CLOSE_PCT, TP2_CLOSE_PCT
)
from database import (
    save_trade, close_trade, update_trade,
    get_open_trades, get_paper_balance, update_paper_balance,
    set_state, get_state, init_db
)

TP_ATR_MULT     = 3.0
INITIAL_BALANCE = 1000.0
MAX_OPEN        = 3
SCAN_INTERVAL   = 60
MAX_WORKERS     = 12

# Paper sim için gevşetilmiş filtreler (canlıda config.py değerleri kullanılır)
PAPER_ADX_MIN   = 20    # config: 28
PAPER_RV_MIN    = 0.8   # config: 1.2
PAPER_MOM_MIN   = 0.5   # config: 1.0

# ── İndikatörler ──────────────────────────────────────────────────────────────
def ema(s, p): return s.ewm(span=p, adjust=False).mean()

def rsi_val(s, p=14):
    d = s.diff()
    g = d.where(d > 0, 0).rolling(p).mean()
    l = (-d.where(d < 0, 0)).rolling(p).mean()
    return float((100 - (100 / (1 + g / (l + 1e-10)))).iloc[-1])

def atr_val(df, p=14):
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"]  - df["close"].shift()).abs()
    return float(pd.concat([hl, hc, lc], axis=1).max(axis=1).rolling(p).mean().iloc[-1])

def adx_val(df, p=14):
    up = df["high"].diff(); dn = -df["low"].diff()
    pos = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    neg = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"]  - df["close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    pos_di = 100 * (pos.ewm(alpha=1/p, adjust=False).mean() / tr.ewm(alpha=1/p, adjust=False).mean())
    neg_di = 100 * (neg.ewm(alpha=1/p, adjust=False).mean() / tr.ewm(alpha=1/p, adjust=False).mean())
    dx = 100 * abs(pos_di - neg_di) / (pos_di + neg_di + 1e-10)
    return float(dx.ewm(alpha=1/p, adjust=False).mean().iloc[-1])

def rv_val(df, p=20):
    avg = df["volume"].rolling(p).mean().shift(1)
    return float((df["volume"] / (avg + 1e-10)).iloc[-1])

def mom3c_val(df):
    s = 0.0
    for j in range(len(df)-3, len(df)):
        o, c = df["open"].iloc[j], df["close"].iloc[j]
        body = abs(c - o); rng = df["high"].iloc[j] - df["low"].iloc[j]
        st = body / (rng + 1e-10)
        s += st if c > o else -st
    return s

# ── Veri Çekme ────────────────────────────────────────────────────────────────
def fetch(symbol, interval="5m", limit=80):
    try:
        r = requests.get("https://fapi.binance.com/fapi/v1/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit}, timeout=8)
        r.raise_for_status()
        df = pd.DataFrame(r.json(), columns=[
            "time","open","high","low","close","volume",
            "ct","qav","nt","tbbav","tbqav","ignore"])
        for c in ["open","high","low","close","volume"]: df[c] = df[c].astype(float)
        df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)
        return df
    except: return pd.DataFrame()

def get_prices(symbols):
    try:
        r = requests.get("https://fapi.binance.com/fapi/v1/ticker/price", timeout=8)
        return {t["symbol"]: float(t["price"]) for t in r.json() if t["symbol"] in symbols}
    except: return {}

# ── Log ───────────────────────────────────────────────────────────────────────
def log(msg):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

# ── Coin Analizi ─────────────────────────────────────────────────────────────
def analyze_coin(sym, btc_trend):
    df = fetch(sym, "5m", 80)
    if df.empty or len(df) < 55: return None
    try:
        e9  = float(ema(df["close"], 9).iloc[-1])
        e21 = float(ema(df["close"], 21).iloc[-1])
        e50 = float(ema(df["close"], 50).iloc[-1])
        if e9 > e21 > e50: direction = "LONG"
        elif e9 < e21 < e50: direction = "SHORT"
        else: return None
        if direction == "SHORT" and btc_trend == "BULLISH": return None
        if direction == "LONG"  and btc_trend == "BEARISH": return None
        adx = adx_val(df)
        if adx < PAPER_ADX_MIN: return None
        rv = rv_val(df)
        if rv < PAPER_RV_MIN: return None
        rsi = rsi_val(df["close"])
        if direction == "LONG"  and not (30 < rsi < 80): return None
        if direction == "SHORT" and not (20 < rsi < 70): return None
        mom = mom3c_val(df)
        if direction == "LONG"  and mom < PAPER_MOM_MIN: return None
        if direction == "SHORT" and mom > -PAPER_MOM_MIN: return None
        atr = atr_val(df)
        if np.isnan(atr) or atr == 0: return None
        entry = float(df["close"].iloc[-1])
        sl_dist = atr * SL_ATR_MULT; tp_dist = atr * TP_ATR_MULT
        if direction == "LONG":
            sl = entry - sl_dist; tp1 = entry + sl_dist; tp2 = entry + tp_dist
        else:
            sl = entry + sl_dist; tp1 = entry - sl_dist; tp2 = entry - tp_dist
        rr = abs(tp2 - entry) / sl_dist
        if rr < MIN_RR: return None
        return {"sym": sym, "direction": direction, "entry": entry,
                "sl": sl, "tp1": tp1, "tp2": tp2, "atr": atr,
                "adx": adx, "rsi": rsi, "rv": rv, "rr": rr}
    except: return None

# ── Açık Pozisyon Takibi (DB'den) ─────────────────────────────────────────────
# paper_sim kendi açtığı trade'leri DB'de takip eder
# trade_id → {"sl": ..., "tp1": ..., "tp2": ..., "tp1_hit": bool, "realized_pnl": float, "qty": float}
_open_meta = {}  # trade_id → meta dict
_meta_lock = threading.Lock()

def sim_loop():
    global _open_meta
    init_db()

    # Bakiyeyi başlat (ilk çalışmada)
    bal = get_paper_balance()
    if bal <= 10:
        # Sıfırdan başlat
        from database import get_conn
        with get_conn() as conn:
            conn.execute("INSERT OR REPLACE INTO paper_account (id, balance) VALUES (1, ?)", (INITIAL_BALANCE,))
        bal = INITIAL_BALANCE

    # Mod: paper
    set_state("execution_mode", "paper")
    set_state("paused", "0")

    log(f"Paper Sim v4 başlatıldı | {len(COIN_UNIVERSE)} coin | Bakiye: ${bal:.2f}")

    while True:
        try:
            now = datetime.now(timezone.utc)
            hour = now.hour
            bal = get_paper_balance()

            # ── 1. Açık Pozisyonları Güncelle ────────────────────────────────
            open_trades = get_open_trades()
            if open_trades:
                syms = [t["symbol"] for t in open_trades]
                prices = get_prices(syms)

                for t in open_trades:
                    trade_id = t["id"]
                    sym = t["symbol"]
                    curr = prices.get(sym)
                    if curr is None: continue

                    with _meta_lock:
                        meta = _open_meta.get(trade_id, {
                            "sl": t["sl"], "tp1": t["tp1"], "tp2": t["tp2"],
                            "tp1_hit": t["status"] in ("tp1_hit", "runner"),
                            "realized_pnl": 0.0,
                            "qty": t["qty"] or 1.0
                        })
                        _open_meta[trade_id] = meta

                    direction = t["direction"]
                    dir_m = 1 if direction == "LONG" else -1
                    sl = meta["sl"]; tp1 = meta["tp1"]; tp2 = meta["tp2"]
                    tp1_hit = meta["tp1_hit"]
                    qty = meta["qty"]
                    risk_amount = abs(t["entry"] - sl) * qty

                    # SL kontrolü
                    sl_hit = (direction == "LONG" and curr <= sl) or \
                             (direction == "SHORT" and curr >= sl)
                    if sl_hit:
                        if tp1_hit:
                            remaining_qty = qty * (1 - TP1_CLOSE_PCT / 100)
                            pnl = (sl - t["entry"]) * remaining_qty * dir_m
                            pnl += meta["realized_pnl"]
                            reason = "SL/BE"
                        else:
                            pnl = -risk_amount
                            reason = "SL"
                        hold_min = (now - datetime.fromisoformat(t["open_time"])).total_seconds() / 60
                        close_trade(trade_id, sl, round(pnl, 4), reason, hold_min)
                        update_paper_balance(pnl)
                        bal = get_paper_balance()
                        with _meta_lock:
                            _open_meta.pop(trade_id, None)
                        log(f"KAPANDI {sym} {direction} | {reason} | PnL: {pnl:+.2f}$ | Bakiye: ${bal:.2f}")
                        continue

                    # TP1 kontrolü
                    if not tp1_hit:
                        tp1_hit_now = (direction == "LONG" and curr >= tp1) or \
                                      (direction == "SHORT" and curr <= tp1)
                        if tp1_hit_now:
                            realized = (tp1 - t["entry"]) * qty * (TP1_CLOSE_PCT / 100) * dir_m
                            meta["realized_pnl"] = realized
                            meta["tp1_hit"] = True
                            if BREAKEVEN_ENABLED:
                                be_sl = t["entry"] * (1 + BREAKEVEN_OFFSET_PCT / 100 * dir_m)
                                meta["sl"] = be_sl
                                update_trade(trade_id, {"status": "tp1_hit", "sl": be_sl})
                            else:
                                update_trade(trade_id, {"status": "tp1_hit"})
                            with _meta_lock:
                                _open_meta[trade_id] = meta
                            log(f"TP1 VURDU {sym} {direction} | Kâr: {realized:+.2f}$ | SL → Breakeven")

                    # TP2 kontrolü
                    if meta.get("tp1_hit") and not meta.get("tp2_hit"):
                        tp2_hit_now = (direction == "LONG" and curr >= tp2) or \
                                      (direction == "SHORT" and curr <= tp2)
                        if tp2_hit_now:
                            remaining_qty = qty * (1 - TP1_CLOSE_PCT / 100)
                            realized2 = (tp2 - t["entry"]) * remaining_qty * dir_m
                            pnl = meta["realized_pnl"] + realized2
                            hold_min = (now - datetime.fromisoformat(t["open_time"])).total_seconds() / 60
                            close_trade(trade_id, tp2, round(pnl, 4), "TP2", hold_min)
                            update_paper_balance(pnl)
                            bal = get_paper_balance()
                            meta["tp2_hit"] = True
                            with _meta_lock:
                                _open_meta.pop(trade_id, None)
                            log(f"KAPANDI {sym} {direction} | TP2 | PnL: {pnl:+.2f}$ | Bakiye: ${bal:.2f}")

            # ── 2. Yeni Sinyal Ara ────────────────────────────────────────────
            open_trades_now = get_open_trades()
            if hour not in BAD_HOURS_UTC and len(open_trades_now) < MAX_OPEN:
                # BTC Trend
                df4h = fetch("BTCUSDT", "4h", 30)
                btc_trend = "NEUTRAL"
                if not df4h.empty and len(df4h) > 22:
                    e9  = float(ema(df4h["close"], 9).iloc[-1])
                    e21 = float(ema(df4h["close"], 21).iloc[-1])
                    btc_trend = "BULLISH" if e9 > e21 else "BEARISH"

                open_syms = {t["symbol"] for t in open_trades_now}
                scan_list = [s for s in COIN_UNIVERSE if s not in open_syms]

                log(f"Tarama | {len(scan_list)} coin | BTC: {btc_trend} | {hour}:xx UTC")

                signals = []
                with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
                    futures = {ex.submit(analyze_coin, s, btc_trend): s for s in scan_list}
                    for fut in as_completed(futures):
                        res = fut.result()
                        if res: signals.append(res)

                signals.sort(key=lambda x: x["adx"], reverse=True)
                log(f"Tarama tamamlandı | {len(signals)} sinyal")

                for sig in signals:
                    open_trades_now = get_open_trades()
                    if len(open_trades_now) >= MAX_OPEN: break

                    sym = sig["sym"]
                    # Aynı coin zaten açıksa atla
                    if any(t["symbol"] == sym for t in open_trades_now): continue

                    entry = sig["entry"]; atr = sig["atr"]
                    sl = sig["sl"]; tp1 = sig["tp1"]; tp2 = sig["tp2"]
                    risk_amt = bal * (RISK_PCT / 100)
                    qty = risk_amt / (atr * SL_ATR_MULT)

                    trade_data = {
                        "symbol": sym,
                        "direction": sig["direction"],
                        "entry": round(entry, 6),
                        "sl": round(sl, 6),
                        "tp1": round(tp1, 6),
                        "tp2": round(tp2, 6),
                        "qty": round(qty, 4),
                        "status": "open",
                        "environment": "paper",
                        "ax_mode": "paper_sim",
                        "open_time": now.isoformat(),
                    }
                    trade_id = save_trade(trade_data)

                    with _meta_lock:
                        _open_meta[trade_id] = {
                            "sl": round(sl, 6), "tp1": round(tp1, 6), "tp2": round(tp2, 6),
                            "tp1_hit": False, "tp2_hit": False,
                            "realized_pnl": 0.0, "qty": round(qty, 4)
                        }

                    log(f"YENİ: {sym} {sig['direction']} @ {entry:.4f} | SL:{sl:.4f} TP2:{tp2:.4f} | ADX:{sig['adx']:.0f} RV:{sig['rv']:.1f} | ID:{trade_id}")
            else:
                if hour in BAD_HOURS_UTC:
                    log(f"Kötü saat ({hour}:xx UTC) — tarama atlandı")

        except Exception as e:
            log(f"[SIM ERROR] {e}")
            import traceback; traceback.print_exc()

        time.sleep(SCAN_INTERVAL)

if __name__ == "__main__":
    sim_loop()
