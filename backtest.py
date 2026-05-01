"""
backtest.py — AX Strateji Backtester
=====================================
Gerçek Binance kline verisiyle sinyal motoru mantığını replay eder.
Komisyon, kısmi çıkışlar (TP1/TP2/Runner) ve trailing stop simüle edilir.

Kullanım:
    python3 backtest.py --symbol BTCUSDT --days 30
    python3 backtest.py --symbol ETHUSDT --days 7 --interval 5m
    python3 backtest.py --all --days 14          # COIN_UNIVERSE tümü

Çıktı:
    Konsol raporu + backtest_results tablosuna kayıt
"""

import argparse
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

from config import (
    COIN_UNIVERSE, SL_ATR_MULT, TP1_R, TP2_R,
    TP1_CLOSE_PCT, TP2_CLOSE_PCT, TRAIL_ATR_MULT,
    RISK_PCT, MIN_RR,
)

TAKER_FEE        = 0.0004   # %0.04 per side
STARTING_BALANCE = 1000.0   # sanal başlangıç bakiyesi
MAX_HOLD_BARS    = 50        # 5m mum × 50 = ~4 saat timeout


# ─────────────────────────────────────────────────────────────────────────────
# VERİ ÇEKME
# ─────────────────────────────────────────────────────────────────────────────

def fetch_candles(client, symbol: str, interval: str = "5m",
                  days: int = 30) -> pd.DataFrame:
    """Binance'ten geçmişe dönük kline verisi çeker."""
    limit = min(1000, days * 288)   # 5m: günde 288 mum
    try:
        klines = client.futures_klines(symbol=symbol, interval=interval, limit=limit)
        df = pd.DataFrame(klines, columns=[
            "time", "open", "high", "low", "close", "volume",
            "ct", "qav", "nt", "tbbav", "tbqav", "ignore"
        ])
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = df[col].astype(float)
        df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)
        return df.reset_index(drop=True)
    except Exception as e:
        logger.error(f"[Backtest] Kline çekme hatası {symbol}: {e}")
        return pd.DataFrame()


# ─────────────────────────────────────────────────────────────────────────────
# TEKNİK İNDİKATÖRLER (signal_engine'dan bağımsız hesaplama)
# ─────────────────────────────────────────────────────────────────────────────

def _ema(s: pd.Series, p: int) -> pd.Series:
    return s.ewm(span=p, adjust=False).mean()

def _atr(df: pd.DataFrame, p: int = 14) -> pd.Series:
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift()).abs(),
        (df["low"]  - df["close"].shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(p).mean()

def _rsi(s: pd.Series, p: int = 14) -> pd.Series:
    d = s.diff()
    g = d.clip(lower=0).rolling(p).mean()
    l = (-d.clip(upper=0)).rolling(p).mean()
    return 100 - (100 / (1 + g / (l + 1e-10)))

def _adx(df: pd.DataFrame, p: int = 14) -> pd.Series:
    up   = df["high"].diff()
    down = -df["low"].diff()
    pdm  = (up.where((up > down) & (up > 0), 0)).rolling(p).mean()
    mdm  = (down.where((down > up) & (down > 0), 0)).rolling(p).mean()
    atr_ = _atr(df, p)
    pdi  = 100 * pdm / (atr_ + 1e-10)
    mdi  = 100 * mdm / (atr_ + 1e-10)
    dx   = 100 * (pdi - mdi).abs() / (pdi + mdi + 1e-10)
    return dx.rolling(p).mean()

def _bb_width(s: pd.Series, p: int = 20) -> pd.Series:
    mid  = s.rolling(p).mean()
    std  = s.rolling(p).std()
    return ((mid + 2 * std) - (mid - 2 * std)) / (mid + 1e-10) * 100

def _rv(vol: pd.Series, p: int = 20) -> pd.Series:
    return vol / (vol.rolling(p).mean() + 1e-10)


# ─────────────────────────────────────────────────────────────────────────────
# SİNYAL TESPİTİ (tek dataframe üzerinde vectorized)
# ─────────────────────────────────────────────────────────────────────────────

def detect_signals(df: pd.DataFrame,
                   sl_mult: float = SL_ATR_MULT,
                   min_rr:  float = MIN_RR,
                   rsi_lo:  float = 35,
                   rsi_hi:  float = 75,
                   rv_min:  float = 1.2) -> pd.DataFrame:
    """
    Her muma LONG/SHORT/None sinyali atar.
    Basitleştirilmiş versiyon — 15m/5m/1m cascade yok,
    tek dataframe üzerinde filtreler uygulanır.
    """
    df = df.copy()
    c = df["close"]

    df["e9"]   = _ema(c, 9)
    df["e21"]  = _ema(c, 21)
    df["e50"]  = _ema(c, 50)
    df["rsi"]  = _rsi(c, 14)
    df["adx"]  = _adx(df, 14)
    df["atr"]  = _atr(df, 14)
    df["bbw"]  = _bb_width(c, 20)
    df["rv"]   = _rv(df["volume"], 20)

    # Trend koşulları
    bull = (
        (df["e9"]  > df["e21"]) &
        (df["e21"] > df["e50"]) &
        (df["close"] > df["e21"]) &
        (df["adx"]  > 20) &
        (df["rsi"]  > rsi_lo) & (df["rsi"] < rsi_hi) &
        (df["bbw"]  > 1.3) &
        (df["rv"]   > rv_min)
    )
    bear = (
        (df["e9"]  < df["e21"]) &
        (df["e21"] < df["e50"]) &
        (df["close"] < df["e21"]) &
        (df["adx"]  > 20) &
        (df["rsi"]  < (100 - rsi_lo)) & (df["rsi"] > (100 - rsi_hi)) &
        (df["bbw"]  > 1.3) &
        (df["rv"]   > rv_min)
    )

    df["signal"] = None
    df.loc[bull, "signal"] = "LONG"
    df.loc[bear, "signal"] = "SHORT"

    # SL/TP hesapla (sinyal olan mumlara)
    sl_dist  = df["atr"] * sl_mult
    df["sl_long"]  = df["close"] - sl_dist
    df["tp1_long"] = df["close"] + sl_dist * TP1_R
    df["tp2_long"] = df["close"] + sl_dist * TP2_R
    df["sl_short"]  = df["close"] + sl_dist
    df["tp1_short"] = df["close"] - sl_dist * TP1_R
    df["tp2_short"] = df["close"] - sl_dist * TP2_R
    df["rr"]        = abs(df["tp2_long"] - df["close"]) / (sl_dist + 1e-10)

    # RR filtresi
    df.loc[df["rr"] < min_rr, "signal"] = None

    return df


# ─────────────────────────────────────────────────────────────────────────────
# TRADE SİMÜLASYONU
# ─────────────────────────────────────────────────────────────────────────────

def simulate_trades(df: pd.DataFrame, balance: float = STARTING_BALANCE,
                    risk_pct: float = RISK_PCT) -> list:
    """
    Sinyal sütunu bulunan dataframe üzerinde trade simülasyonu.
    TP1 → TP2 → Trailing stop mantığını taklit eder.
    """
    trades  = []
    in_trade = False
    bal = balance

    for i in range(50, len(df)):   # ilk 50 mum warmup
        row = df.iloc[i]
        if in_trade:
            t = trades[-1]
            hi = row["high"]; lo = row["low"]
            is_long = (t["direction"] == "LONG")

            # SL kontrolü
            sl_hit = (is_long and lo <= t["sl"]) or (not is_long and hi >= t["sl"])
            if sl_hit:
                exit_price = t["sl"]
                close_pnl  = (exit_price - t["entry"]) * t["rem_qty"] * (1 if is_long else -1)
                close_pnl -= TAKER_FEE * t["rem_qty"] * exit_price
                t["realized"] += close_pnl
                t["exit"] = exit_price; t["exit_bar"] = i
                t["result"] = "WIN" if t["realized"] > 0 else "LOSS"
                bal += t["realized"] - t["entry_fee"]
                in_trade = False
                continue

            # TP1
            if not t.get("tp1_hit"):
                tp1_hit = (is_long and hi >= t["tp1"]) or (not is_long and lo <= t["tp1"])
                if tp1_hit:
                    qty1 = t["qty"] * TP1_CLOSE_PCT / 100
                    pnl1 = (t["tp1"] - t["entry"]) * qty1 * (1 if is_long else -1)
                    pnl1 -= TAKER_FEE * qty1 * t["tp1"]
                    t["realized"] += pnl1
                    t["rem_qty"]  -= qty1
                    t["tp1_hit"]   = True
                    t["sl"]        = t["entry"]   # break-even'e çek
                    bal += pnl1

            # TP2
            if t.get("tp1_hit") and not t.get("tp2_hit"):
                tp2_hit = (is_long and hi >= t["tp2"]) or (not is_long and lo <= t["tp2"])
                if tp2_hit:
                    qty2  = t["qty"] * TP2_CLOSE_PCT / 100
                    pnl2  = (t["tp2"] - t["entry"]) * qty2 * (1 if is_long else -1)
                    pnl2 -= TAKER_FEE * qty2 * t["tp2"]
                    t["realized"] += pnl2
                    t["rem_qty"]  -= qty2
                    t["tp2_hit"]   = True
                    # Trail başlat
                    atr_v = row["atr"]
                    t["trail"] = (t["tp2"] - atr_v * TRAIL_ATR_MULT) if is_long else (t["tp2"] + atr_v * TRAIL_ATR_MULT)
                    bal += pnl2

            # Runner: trailing stop
            if t.get("tp2_hit"):
                atr_v = row["atr"]
                if is_long:
                    new_trail = row["close"] - atr_v * TRAIL_ATR_MULT
                    t["trail"] = max(t.get("trail", t["entry"]), new_trail)
                    if lo <= t["trail"]:
                        exit_price = t["trail"]
                        pnl_r = (exit_price - t["entry"]) * t["rem_qty"]
                        pnl_r -= TAKER_FEE * t["rem_qty"] * exit_price
                        t["realized"] += pnl_r
                        bal += pnl_r
                        t["exit"] = exit_price; t["exit_bar"] = i
                        t["result"] = "WIN" if t["realized"] > 0 else "LOSS"
                        in_trade = False
                        continue
                else:
                    new_trail = row["close"] + atr_v * TRAIL_ATR_MULT
                    t["trail"] = min(t.get("trail", t["entry"]), new_trail)
                    if hi >= t["trail"]:
                        exit_price = t["trail"]
                        pnl_r = (t["entry"] - exit_price) * t["rem_qty"]
                        pnl_r -= TAKER_FEE * t["rem_qty"] * exit_price
                        t["realized"] += pnl_r
                        bal += pnl_r
                        t["exit"] = exit_price; t["exit_bar"] = i
                        t["result"] = "WIN" if t["realized"] > 0 else "LOSS"
                        in_trade = False
                        continue

            # Timeout
            if (i - t["entry_bar"]) >= MAX_HOLD_BARS:
                exit_price = row["close"]
                pnl_to = (exit_price - t["entry"]) * t["rem_qty"] * (1 if is_long else -1)
                pnl_to -= TAKER_FEE * t["rem_qty"] * exit_price
                t["realized"] += pnl_to
                bal += pnl_to
                t["exit"] = exit_price; t["exit_bar"] = i
                t["result"] = "WIN" if t["realized"] > 0 else "LOSS"
                in_trade = False

        else:
            # Yeni sinyal kontrolü
            if row["signal"] is None:
                continue

            direction = row["signal"]
            entry     = row["close"]
            sl        = row["sl_long"]  if direction == "LONG" else row["sl_short"]
            tp1       = row["tp1_long"] if direction == "LONG" else row["tp1_short"]
            tp2       = row["tp2_long"] if direction == "LONG" else row["tp2_short"]

            sl_dist   = abs(entry - sl)
            risk_amt  = bal * risk_pct / 100
            qty       = risk_amt / (sl_dist + 1e-10)
            if qty <= 0 or bal < 10:
                continue

            entry_fee = TAKER_FEE * qty * entry
            bal      -= entry_fee   # giriş ücreti anında düş

            trades.append({
                "entry_bar": i,
                "entry_time": str(row["time"]),
                "symbol":     df.attrs.get("symbol", ""),
                "direction":  direction,
                "entry":      entry,
                "sl":         sl,
                "tp1":        tp1,
                "tp2":        tp2,
                "trail":      None,
                "qty":        qty,
                "rem_qty":    qty,
                "entry_fee":  entry_fee,
                "realized":   0.0,
                "tp1_hit":    False,
                "tp2_hit":    False,
                "exit":       None,
                "exit_bar":   None,
                "result":     "OPEN",
                "balance":    bal,
            })
            in_trade = True

    # Kapanmamış trade'leri son mumda kapat
    if in_trade and trades:
        t = trades[-1]
        if t["result"] == "OPEN":
            last = df.iloc[-1]
            ep = last["close"]
            is_long = (t["direction"] == "LONG")
            pnl = (ep - t["entry"]) * t["rem_qty"] * (1 if is_long else -1)
            pnl -= TAKER_FEE * t["rem_qty"] * ep
            t["realized"] += pnl
            t["exit"] = ep; t["exit_bar"] = len(df) - 1
            t["result"] = "WIN" if t["realized"] > 0 else "LOSS"
            bal += pnl

    return trades, bal


# ─────────────────────────────────────────────────────────────────────────────
# PERFORMANS RAPORU
# ─────────────────────────────────────────────────────────────────────────────

def calc_performance(trades: list, start_balance: float, end_balance: float) -> dict:
    closed = [t for t in trades if t["result"] in ("WIN", "LOSS")]
    if not closed:
        return {"total": 0, "win_rate": 0, "profit_factor": 0,
                "net_pnl": 0, "max_drawdown": 0, "avg_r": 0}

    wins   = [t for t in closed if t["result"] == "WIN"]
    losses = [t for t in closed if t["result"] == "LOSS"]

    total_win  = sum(t["realized"] for t in wins)
    total_loss = abs(sum(t["realized"] for t in losses))
    pf = total_win / (total_loss + 1e-10)

    # R-multiple per trade
    r_list = []
    for t in closed:
        sl_d = abs(t["entry"] - t["sl"])
        if sl_d > 0 and t["qty"] > 0:
            r_list.append(t["realized"] / (sl_d * t["qty"]))

    # Max Drawdown
    peak = start_balance; max_dd = 0
    running = start_balance
    for t in closed:
        running += t["realized"]
        if running > peak:
            peak = running
        dd = (peak - running) / (peak + 1e-10)
        if dd > max_dd:
            max_dd = dd

    return {
        "total":         len(closed),
        "wins":          len(wins),
        "losses":        len(losses),
        "win_rate":      round(len(wins) / len(closed) * 100, 1),
        "profit_factor": round(pf, 3),
        "net_pnl":       round(end_balance - start_balance, 3),
        "max_drawdown":  round(max_dd * 100, 2),
        "avg_r":         round(sum(r_list) / len(r_list), 3) if r_list else 0,
        "best_trade":    round(max((t["realized"] for t in closed), default=0), 3),
        "worst_trade":   round(min((t["realized"] for t in closed), default=0), 3),
        "end_balance":   round(end_balance, 2),
    }


def print_report(symbol: str, perf: dict, days: int, params: dict = None):
    sep = "─" * 52
    print(f"\n{sep}")
    print(f"  BACKTEST: {symbol}  |  {days} gün")
    if params:
        print(f"  SL×{params.get('sl_atr_mult', SL_ATR_MULT):.2f}  "
              f"RSI {params.get('rsi5_min',35)}–{params.get('rsi5_max',75)}  "
              f"RV≥{params.get('vol_ratio_min',1.2):.1f}")
    print(sep)
    print(f"  Trade Sayısı : {perf['total']}  ({perf['wins']}W / {perf['losses']}L)")
    print(f"  Win Rate     : {perf['win_rate']:.1f}%")
    print(f"  Profit Factor: {perf['profit_factor']:.2f}x")
    print(f"  Net PNL      : {perf['net_pnl']:+.3f}$  ({perf['end_balance']:.2f}$ son bakiye)")
    print(f"  Max Drawdown : {perf['max_drawdown']:.1f}%")
    print(f"  Avg R        : {perf['avg_r']:+.3f}R")
    print(f"  Best Trade   : {perf['best_trade']:+.3f}$")
    print(f"  Worst Trade  : {perf['worst_trade']:+.3f}$")
    print(sep)


# ─────────────────────────────────────────────────────────────────────────────
# VERİTABANI KAYIT
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_table():
    from database import get_conn
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS backtest_results (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol        TEXT,
                days          INTEGER,
                total_trades  INTEGER,
                win_rate      REAL,
                profit_factor REAL,
                net_pnl       REAL,
                max_drawdown  REAL,
                avg_r         REAL,
                params_json   TEXT,
                created_at    TEXT DEFAULT (datetime('now'))
            )
        """)


def save_result(symbol: str, days: int, perf: dict, params: dict = None):
    import json
    _ensure_table()
    from database import get_conn
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO backtest_results
                (symbol, days, total_trades, win_rate, profit_factor,
                 net_pnl, max_drawdown, avg_r, params_json)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (
            symbol, days, perf["total"], perf["win_rate"],
            perf["profit_factor"], perf["net_pnl"], perf["max_drawdown"],
            perf["avg_r"], json.dumps(params or {})
        ))


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API — AI Brain tarafından çağrılır
# ─────────────────────────────────────────────────────────────────────────────

def run_backtest(client, symbol: str, days: int = 14,
                 params: dict = None, silent: bool = False) -> dict:
    """
    Tek bir sembol için backtest çalıştırır.

    params: AI Brain'den gelen optimize parametreler (opsiyonel)
    silent: True ise konsol çıktısı yok (AI Brain kullanımı için)
    Returns: performans dict
    """
    p = params or {}
    sl_mult = p.get("sl_atr_mult", SL_ATR_MULT)
    rsi_lo  = p.get("rsi5_min", 35)
    rsi_hi  = p.get("rsi5_max", 75)
    rv_min  = p.get("vol_ratio_min", 1.2)
    min_rr_ = p.get("min_rr", MIN_RR)

    df = fetch_candles(client, symbol, interval="5m", days=days)
    if df.empty or len(df) < 100:
        return {"total": 0, "error": "Yetersiz veri"}

    df.attrs["symbol"] = symbol
    df = detect_signals(df, sl_mult=sl_mult, min_rr=min_rr_,
                        rsi_lo=rsi_lo, rsi_hi=rsi_hi, rv_min=rv_min)

    trades, end_balance = simulate_trades(df)
    perf = calc_performance(trades, STARTING_BALANCE, end_balance)

    if not silent:
        print_report(symbol, perf, days, params)

    try:
        save_result(symbol, days, perf, params)
    except Exception:
        pass

    return perf


def run_backtest_all(client, days: int = 14, params: dict = None) -> dict:
    """Tüm COIN_UNIVERSE için backtest, özet istatistik döndürür."""
    results = {}
    for symbol in COIN_UNIVERSE:
        try:
            perf = run_backtest(client, symbol, days=days, params=params, silent=True)
            results[symbol] = perf
            time.sleep(0.3)   # rate limit
        except Exception as e:
            results[symbol] = {"error": str(e)}

    # Özet
    valid = [v for v in results.values() if v.get("total", 0) > 0]
    if not valid:
        return results

    avg_wr  = sum(v["win_rate"]      for v in valid) / len(valid)
    avg_pf  = sum(v["profit_factor"] for v in valid) / len(valid)
    avg_r   = sum(v["avg_r"]         for v in valid) / len(valid)
    total_pnl = sum(v["net_pnl"]     for v in valid)

    print(f"\n{'═' * 52}")
    print(f"  BACKTEST ÖZET — {len(valid)} coin / {days} gün")
    print(f"  Ortalama Win Rate  : {avg_wr:.1f}%")
    print(f"  Ortalama PF        : {avg_pf:.2f}x")
    print(f"  Ortalama R         : {avg_r:+.3f}R")
    print(f"  Toplam PNL         : {total_pnl:+.2f}$")
    print(f"{'═' * 52}\n")

    return results


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main():
    logging.basicConfig(level=logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")

    parser = argparse.ArgumentParser(description="AX Backtest")
    parser.add_argument("--symbol",   default="BTCUSDT",  help="İşlem çifti")
    parser.add_argument("--days",     type=int, default=30, help="Kaç günlük veri")
    parser.add_argument("--all",      action="store_true",  help="Tüm COIN_UNIVERSE")
    parser.add_argument("--params",   default=None,         help="Parametre JSON string")
    args = parser.parse_args()

    import json as json_mod
    from binance.client import Client
    from config import BINANCE_API_KEY, BINANCE_API_SECRET

    client = Client(BINANCE_API_KEY, BINANCE_API_SECRET)
    params = json_mod.loads(args.params) if args.params else None

    if args.all:
        run_backtest_all(client, days=args.days, params=params)
    else:
        run_backtest(client, args.symbol, days=args.days, params=params)


if __name__ == "__main__":
    main()
