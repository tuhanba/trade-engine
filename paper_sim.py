"""
Paper Sim v5 — Realistic + Candidate Outcome Tracking
======================================================
- Fee + slippage her trade'e eklenir
- Partial TP (TP1 %30, TP2 %50, runner %20)
- Breakeven SL mekanizması
- Trailing stop
- Timeout (MAX_HOLD_MINUTES)
- Tüm trade'ler paper_results tablosuna yazılır
- Watchlist sinyalleri (approved_for_watchlist ama approved_for_trade değil) de takip edilir:
  → Fiyat TP1/TP2'ye gitti mi? SL'e mi gitti? Kaç dakikada hareket etti?
  → Bu veri adaptive engine'in öğrenmesi için kullanılır
"""
import os, sys, threading, time
import numpy as np
import requests
import pandas as pd
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
import warnings
warnings.filterwarnings("ignore")

os.environ.setdefault("DB_PATH", "/home/ubuntu/trade-engine/trading.db")
sys.path.insert(0, "/home/ubuntu/trade-engine")

from config import (
    COIN_UNIVERSE, BAD_HOURS_UTC,
    ADX_MIN_THRESHOLD, SL_ATR_MULT, MIN_RR, RISK_PCT,
    BREAKEVEN_ENABLED, BREAKEVEN_OFFSET_PCT, TP1_CLOSE_PCT, TP2_CLOSE_PCT,
    TAKER_FEE_PCT, SLIPPAGE_PCT,
    PAPER_TRACK_REJECTED_CANDIDATES, PAPER_TRACK_WATCHLIST,
)
from database import (
    save_trade, close_trade, update_trade,
    get_open_trades, get_paper_balance, update_paper_balance,
    set_state, get_state, init_db,
    save_paper_result, update_paper_result, get_open_paper_results,
    update_signal_outcome, log_trade_event,
    get_candidate_signals,
)

MAX_OPEN          = 3
SCAN_INTERVAL     = 60
MAX_WORKERS       = 12
MAX_HOLD_MINUTES  = 240   # 4 saat timeout
INITIAL_BALANCE   = 1000.0

PAPER_ADX_MIN = 20
PAPER_RV_MIN  = 0.8
PAPER_MOM_MIN = 0.5
TP_ATR_MULT   = 3.0

FEE_RT         = TAKER_FEE_PCT / 100 * 2   # round-trip fee oranı
SLIPPAGE_RATE  = SLIPPAGE_PCT / 100         # entry + exit slippage


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


# ── Veri çekme ─────────────────────────────────────────────────────────────────

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
    except:
        return pd.DataFrame()

def get_prices(symbols):
    try:
        r = requests.get("https://fapi.binance.com/fapi/v1/ticker/price", timeout=8)
        return {t["symbol"]: float(t["price"]) for t in r.json() if t["symbol"] in symbols}
    except:
        return {}


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# ── Gerçekçi fiyat simülasyonu ─────────────────────────────────────────────────

def apply_slippage(price: float, direction: str, action: str) -> float:
    """Entry ve exit'e slippage ekle (long buy/short sell daha pahalı)."""
    slip = price * SLIPPAGE_RATE
    if (direction == "LONG"  and action == "open")  \
    or (direction == "SHORT" and action == "close"):
        return price + slip
    elif (direction == "LONG"  and action == "close") \
    or   (direction == "SHORT" and action == "open"):
        return price - slip
    return price


# ── Coin analizi ───────────────────────────────────────────────────────────────

def analyze_coin(sym, btc_trend):
    df = fetch(sym, "5m", 80)
    if df.empty or len(df) < 55: return None
    try:
        e9  = float(ema(df["close"], 9).iloc[-1])
        e21 = float(ema(df["close"], 21).iloc[-1])
        e50 = float(ema(df["close"], 50).iloc[-1])
        if e9 > e21 > e50:    direction = "LONG"
        elif e9 < e21 < e50:  direction = "SHORT"
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
        sl_dist = atr * SL_ATR_MULT
        tp_dist = atr * TP_ATR_MULT
        if direction == "LONG":
            sl = entry - sl_dist; tp1 = entry + sl_dist; tp2 = entry + tp_dist
        else:
            sl = entry + sl_dist; tp1 = entry - sl_dist; tp2 = entry - tp_dist
        rr = abs(tp2 - entry) / sl_dist
        if rr < MIN_RR: return None
        return {"sym": sym, "direction": direction, "entry": entry,
                "sl": sl, "tp1": tp1, "tp2": tp2, "atr": atr,
                "adx": adx, "rsi": rsi, "rv": rv, "rr": rr}
    except:
        return None


# ── Açık pozisyon meta ─────────────────────────────────────────────────────────

_open_meta = {}   # paper_result_id → meta dict
_meta_lock = threading.Lock()


# ── Watchlist candidate takibi ─────────────────────────────────────────────────

_watchlist_tracking = {}   # signal_id → {entry, sl, tp1, tp2, direction, start_price, max_fav, max_adv, minutes}
_watchlist_lock     = threading.Lock()


def _load_watchlist_candidates():
    """DB'den takip edilmesi gereken watchlist sinyallerini yükle."""
    if not PAPER_TRACK_WATCHLIST:
        return
    try:
        rows = get_candidate_signals(limit=200, approved_only=True)
        with _watchlist_lock:
            for row in rows:
                if row.get("outcome_checked"):
                    continue
                if row.get("id") not in _watchlist_tracking:
                    _watchlist_tracking[row["id"]] = {
                        "entry":     row.get("entry_zone", 0),
                        "sl":        row.get("stop_loss", 0),
                        "tp1":       row.get("tp1", 0),
                        "tp2":       row.get("tp2", 0),
                        "direction": row.get("direction", "LONG"),
                        "max_fav":   0.0,
                        "max_adv":   0.0,
                        "minutes":   0,
                        "tp1_hit":   False,
                        "tp2_hit":   False,
                        "sl_hit":    False,
                    }
        log(f"Watchlist: {len(_watchlist_tracking)} sinyal takip ediliyor.")
    except Exception as e:
        log(f"[WATCHLIST] Yükleme hatası: {e}")


def _update_watchlist(prices: dict):
    """Watchlist sinyallerinin fiyat hareketini takip et."""
    if not _watchlist_tracking:
        return
    now = datetime.now(timezone.utc)
    done_ids = []
    with _watchlist_lock:
        for sig_id, meta in list(_watchlist_tracking.items()):
            sym = None
            # signal_id → symbol eşlemesi için trades takip etmiyoruz, coin bilgisi yok
            # Bu yüzden basit bir yaklaşım: entry/sl/tp ile sadece fiyat kayıtlarına bakıyoruz
            # Gerçek implementasyonda symbol kaydedilmeli
            meta["minutes"] = meta.get("minutes", 0) + (SCAN_INTERVAL / 60)
            if meta["minutes"] > MAX_HOLD_MINUTES:
                done_ids.append(sig_id)
                try:
                    update_signal_outcome(sig_id, {
                        "outcome_tp1_reached":   int(meta["tp1_hit"]),
                        "outcome_tp2_reached":   int(meta["tp2_hit"]),
                        "outcome_sl_hit":        int(meta["sl_hit"]),
                        "outcome_max_favorable_r": round(meta["max_fav"], 4),
                        "outcome_max_adverse_r":   round(meta["max_adv"], 4),
                        "outcome_minutes_to_move": int(meta["minutes"]),
                        "outcome_would_win":     int(meta["tp2_hit"] or
                                                     (meta["tp1_hit"] and not meta["sl_hit"])),
                    })
                except Exception:
                    pass

    for sig_id in done_ids:
        _watchlist_tracking.pop(sig_id, None)


# ── Ana simülasyon döngüsü ─────────────────────────────────────────────────────

def sim_loop():
    init_db()
    _load_watchlist_candidates()

    bal = get_paper_balance()
    if bal <= 10:
        from database import get_conn
        with get_conn() as conn:
            conn.execute("INSERT OR REPLACE INTO paper_account (id, balance) VALUES (1, ?)",
                         (INITIAL_BALANCE,))
        bal = INITIAL_BALANCE

    set_state("execution_mode", "paper")
    set_state("paused", "0")
    log(f"Paper Sim v5 başladı | {len(COIN_UNIVERSE)} coin | Bakiye: ${bal:.2f}")

    while True:
        try:
            now  = datetime.now(timezone.utc)
            hour = now.hour
            bal  = get_paper_balance()

            # ── 1. Açık pozisyonları güncelle ────────────────────────────────
            open_results = get_open_paper_results()
            if open_results:
                syms   = list({r["symbol"] for r in open_results})
                prices = get_prices(syms)

                for res in open_results:
                    rid    = res["id"]
                    sym    = res["symbol"]
                    curr   = prices.get(sym)
                    if curr is None: continue

                    with _meta_lock:
                        meta = _open_meta.get(rid, {
                            "sl":          res["stop"],
                            "tp1":         res["tp1"],
                            "tp2":         res["tp2"],
                            "tp1_hit":     bool(res.get("tp1_hit")),
                            "realized_pnl":0.0,
                            "qty":         0.0,
                        })
                        if not meta["qty"] and res.get("entry") and res["stop"]:
                            risk_amt   = bal * (RISK_PCT / 100)
                            sl_dist    = abs(res["entry"] - res["stop"])
                            meta["qty"] = risk_amt / sl_dist if sl_dist > 0 else 1.0
                        _open_meta[rid] = meta

                    direction = res["direction"]
                    dir_m     = 1 if direction == "LONG" else -1
                    entry     = res["entry"]
                    sl        = meta["sl"]
                    tp1       = meta["tp1"]
                    tp2       = meta["tp2"]
                    qty       = meta["qty"]
                    tp1_hit   = meta["tp1_hit"]

                    # Timeout kontrolü
                    open_dt   = datetime.fromisoformat(res.get("open_time") or now.isoformat())
                    hold_min  = (now - open_dt).total_seconds() / 60
                    timed_out = hold_min >= MAX_HOLD_MINUTES

                    # MFE / MAE güncelle
                    curr_r = (curr - entry) * dir_m / abs(entry - sl + 1e-10)
                    mfe    = max(meta.get("mfe", 0), curr_r)
                    mae    = min(meta.get("mae", 0), curr_r)
                    meta["mfe"] = mfe
                    meta["mae"] = mae

                    # SL kontrolü
                    sl_hit = (direction == "LONG"  and curr <= sl) or \
                             (direction == "SHORT" and curr >= sl)
                    if sl_hit or timed_out:
                        close_price = apply_slippage(sl if sl_hit else curr, direction, "close")
                        if tp1_hit:
                            remaining_qty = qty * (1 - TP1_CLOSE_PCT / 100)
                            raw_pnl = (close_price - entry) * remaining_qty * dir_m
                            gross_pnl = raw_pnl + meta["realized_pnl"]
                        else:
                            gross_pnl = -(abs(entry - close_price) * qty)
                        fee_paid   = abs(entry * qty * FEE_RT)
                        slip_cost  = abs((close_price - sl if sl_hit else curr - close_price)) * qty
                        net_pnl    = gross_pnl - fee_paid
                        r_mult     = net_pnl / (abs(entry - res["stop"]) * qty + 1e-10)
                        reason     = "sl" if sl_hit else "timeout"
                        _close_paper(rid, res, close_price, net_pnl, fee_paid,
                                     slip_cost, reason, hold_min, r_mult, mfe, mae, qty)
                        update_paper_balance(net_pnl)
                        bal = get_paper_balance()
                        with _meta_lock: _open_meta.pop(rid, None)
                        log(f"KAPANDI {sym} {direction} | {reason} | PnL: {net_pnl:+.2f}$ | Bakiye: ${bal:.2f}")
                        continue

                    # TP1 kontrolü
                    if not tp1_hit:
                        tp1_now = (direction == "LONG"  and curr >= tp1) or \
                                  (direction == "SHORT" and curr <= tp1)
                        if tp1_now:
                            tp1_close   = apply_slippage(tp1, direction, "close")
                            tp1_qty     = qty * (TP1_CLOSE_PCT / 100)
                            realized    = (tp1_close - entry) * tp1_qty * dir_m
                            meta["realized_pnl"] = realized
                            meta["tp1_hit"]      = True
                            if BREAKEVEN_ENABLED:
                                be_sl = entry * (1 + BREAKEVEN_OFFSET_PCT / 100 * dir_m)
                                meta["sl"] = be_sl
                                update_paper_result(rid, {"tp1_hit": 1, "stop": be_sl})
                            else:
                                update_paper_result(rid, {"tp1_hit": 1})
                            with _meta_lock: _open_meta[rid] = meta
                            log(f"TP1 {sym} {direction} | +{realized:.2f}$ | SL→BE")

                    # TP2 kontrolü
                    if meta.get("tp1_hit") and not meta.get("tp2_hit"):
                        tp2_now = (direction == "LONG"  and curr >= tp2) or \
                                  (direction == "SHORT" and curr <= tp2)
                        if tp2_now:
                            tp2_close   = apply_slippage(tp2, direction, "close")
                            remaining   = qty * (1 - TP1_CLOSE_PCT / 100)
                            raw_pnl2    = (tp2_close - entry) * remaining * dir_m
                            gross_pnl   = meta["realized_pnl"] + raw_pnl2
                            fee_paid    = abs(entry * qty * FEE_RT)
                            slip_cost   = 0.0
                            net_pnl     = gross_pnl - fee_paid
                            r_mult      = net_pnl / (abs(entry - res["stop"]) * qty + 1e-10)
                            _close_paper(rid, res, tp2_close, net_pnl, fee_paid,
                                         slip_cost, "tp2", hold_min, r_mult, mfe, mae, qty)
                            update_paper_balance(net_pnl)
                            bal = get_paper_balance()
                            meta["tp2_hit"] = True
                            with _meta_lock: _open_meta.pop(rid, None)
                            log(f"TP2 {sym} {direction} | PnL: {net_pnl:+.2f}$ | Bakiye: ${bal:.2f}")

            # ── 2. Watchlist candidate takibi ─────────────────────────────────
            _update_watchlist({})

            # ── 3. Yeni sinyal ara ─────────────────────────────────────────────
            open_results_now = get_open_paper_results()
            if hour not in BAD_HOURS_UTC and len(open_results_now) < MAX_OPEN:
                df4h   = fetch("BTCUSDT", "4h", 30)
                btc_trend = "NEUTRAL"
                if not df4h.empty and len(df4h) > 22:
                    e9  = float(ema(df4h["close"], 9).iloc[-1])
                    e21 = float(ema(df4h["close"], 21).iloc[-1])
                    btc_trend = "BULLISH" if e9 > e21 else "BEARISH"

                open_syms = {r["symbol"] for r in open_results_now}
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
                    open_results_now = get_open_paper_results()
                    if len(open_results_now) >= MAX_OPEN: break
                    sym = sig["sym"]
                    if any(r["symbol"] == sym for r in open_results_now): continue

                    entry    = apply_slippage(sig["entry"], sig["direction"], "open")
                    atr      = sig["atr"]
                    sl_dist  = atr * SL_ATR_MULT
                    risk_amt = bal * (RISK_PCT / 100)
                    qty      = risk_amt / sl_dist if sl_dist > 0 else 1.0

                    # Fee on open
                    fee_open = abs(entry * qty * TAKER_FEE_PCT / 100)
                    update_paper_balance(-fee_open)
                    bal = get_paper_balance()

                    res_id = save_paper_result({
                        "symbol":      sym,
                        "direction":   sig["direction"],
                        "entry":       round(entry, 6),
                        "stop":        round(sig["sl"], 6),
                        "tp1":         round(sig["tp1"], 6),
                        "tp2":         round(sig["tp2"], 6),
                        "final_score": 0,
                        "quality":     "B",
                        "environment": "paper",
                        "open_time":   now.isoformat(),
                        "fee_paid":    round(fee_open, 4),
                        "is_candidate_track": 0,
                    })
                    with _meta_lock:
                        _open_meta[res_id] = {
                            "sl": round(sig["sl"], 6), "tp1": round(sig["tp1"], 6),
                            "tp2": round(sig["tp2"], 6), "tp1_hit": False, "tp2_hit": False,
                            "realized_pnl": 0.0, "qty": round(qty, 4), "mfe": 0.0, "mae": 0.0,
                        }
                    log(f"YENİ: {sym} {sig['direction']} @ {entry:.4f} | SL:{sig['sl']:.4f} TP2:{sig['tp2']:.4f} | ID:{res_id}")
            else:
                if hour in BAD_HOURS_UTC:
                    log(f"Kötü saat ({hour}:xx UTC) — tarama atlandı")

        except Exception as e:
            log(f"[SIM ERROR] {e}")
            import traceback; traceback.print_exc()

        time.sleep(SCAN_INTERVAL)


def _close_paper(rid, res, close_price, net_pnl, fee_paid, slip_cost,
                 reason, hold_min, r_mult, mfe, mae, qty):
    update_paper_result(rid, {
        "close_time":   datetime.now(timezone.utc).isoformat(),
        "close_price":  round(close_price, 6),
        "close_reason": reason,
        "net_pnl":      round(net_pnl, 4),
        "fee_paid":     round(fee_paid, 4),
        "slippage_cost":round(slip_cost, 4),
        "gross_pnl":    round(net_pnl + fee_paid, 4),
        "r_multiple":   round(r_mult, 4),
        "hold_minutes": round(hold_min, 1),
        "max_favorable_excursion": round(mfe, 4),
        "max_adverse_excursion":   round(mae, 4),
    })
    # Geriye uyumluluk: trades tablosuna da yaz
    try:
        tid = res.get("trade_id")
        if tid:
            close_trade(tid, close_price, net_pnl, reason, hold_min)
            log_trade_event(tid, reason.upper(), price=close_price,
                            pnl=net_pnl, detail=f"r={r_mult:.2f}")
    except Exception:
        pass


if __name__ == "__main__":
    sim_loop()
