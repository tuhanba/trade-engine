"""
AURVEX.Ai — S Sınıfı Backtest
================================
Composite S sınıfı skorunu simüle ederek S vs A+ karşılaştırması yapar.
Config v2.5 parametreleri: SL=1.0, TP1R=0.8, TP2R=3.5, TP3R=4.5
"""
import requests
import pandas as pd
import numpy as np

BASE = "https://fapi.binance.com"

COINS = [
    "WIFUSDT","XMRUSDT","TRBUSDT","AXLUSDT","MANTRAUSDT","FLUIDUSDT",
    "XAUTUSDT","DRIFTUSDT","MAGMAUSDT","INJUSDT","SOLUSDT","BNBUSDT",
    "XRPUSDT","ADAUSDT","DOTUSDT","LINKUSDT","AVAXUSDT","SUIUSDT",
    "ARBUSDT","OPUSDT","HYPEUSDT","TAOUSDT","AAVEUSDT","UNIUSDT",
    "NEARUSDT","APTUSDT","ENAUSDT","WLDUSDT","TONUSDT","LTCUSDT",
    "BCHUSDT","ETCUSDT","ZECUSDT","HBARUSDT","POLUSDT","FILUSDT",
    "ORDIUSDT","PENDLEUSDT",
]
COINS = list(set(COINS))

BAD_HOURS = [4,5,6,10,11,12,13,14,16,19,20,21,23]
GOOD_HOURS = [0,1,2,3,7,8,9,15,17,18,22]
ADX_MIN   = 25

# Config v2.5 parametreleri
SL_MULT   = 1.0
TP1_R     = 0.8
TP2_R     = 3.5
TP3_R     = 4.5
TP1_PCT   = 0.20
TP2_PCT   = 0.40
RUNNER_PCT = 0.40

# Risk seviyeleri
RISK_S  = 2.0   # S sınıfı: %2.0
RISK_AP = 1.5   # A+ sınıfı: %1.5
BALANCE = 1000.0

def fetch(symbol):
    try:
        r = requests.get(f"{BASE}/fapi/v1/klines",
            params={"symbol": symbol, "interval": "5m", "limit": 500},
            timeout=10)
        if r.status_code != 200: return None
        data = r.json()
        df = pd.DataFrame(data, columns=[
            "time","open","high","low","close","volume",
            "ct","qav","nt","tbbav","tbqav","ignore"
        ])
        for c in ["open","high","low","close","volume"]:
            df[c] = df[c].astype(float)
        df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)
        df["hour"] = df["time"].dt.hour

        df["ema9"]  = df["close"].ewm(span=9,  adjust=False).mean()
        df["ema21"] = df["close"].ewm(span=21, adjust=False).mean()
        df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()

        h, l, c2 = df["high"], df["low"], df["close"]
        pdm = h.diff().clip(lower=0); mdm = (-l.diff()).clip(lower=0)
        pdm[pdm < mdm] = 0; mdm[mdm < pdm] = 0
        tr  = pd.concat([h-l,(h-c2.shift()).abs(),(l-c2.shift()).abs()],axis=1).max(axis=1)
        atr = tr.ewm(span=14,adjust=False).mean()
        pdi = 100*pdm.ewm(span=14,adjust=False).mean()/atr.replace(0,np.nan)
        mdi = 100*mdm.ewm(span=14,adjust=False).mean()/atr.replace(0,np.nan)
        dx  = (100*(pdi-mdi).abs()/(pdi+mdi).replace(0,np.nan))
        df["adx"] = dx.ewm(span=14,adjust=False).mean()
        df["pdi"] = pdi; df["mdi"] = mdi; df["atr"] = atr

        delta = df["close"].diff()
        gain  = delta.clip(lower=0).ewm(span=14,adjust=False).mean()
        loss  = (-delta.clip(upper=0)).ewm(span=14,adjust=False).mean()
        df["rsi"] = 100 - (100/(1+gain/loss.replace(0,np.nan)))
        df["vol_ma"] = df["volume"].rolling(20).mean()

        # MACD histogram
        ema12 = df["close"].ewm(span=12,adjust=False).mean()
        ema26 = df["close"].ewm(span=26,adjust=False).mean()
        macd  = ema12 - ema26
        sig   = macd.ewm(span=9,adjust=False).mean()
        df["macd_hist"] = macd - sig

        # Momentum 3c
        df["mom3c"] = df["close"].diff(3)

        return df
    except: return None

def classify(row, direction, adx_v, btc_bullish=True):
    """Composite S skoru hesapla ve kalite döndür."""
    rsi_v = row["rsi"]
    rv    = row["volume"] / row["vol_ma"] if row["vol_ma"] > 0 else 1.0
    hist  = row["macd_hist"]
    mom   = row["mom3c"]
    hour  = row["hour"]

    # Temel kalite skoru
    score = 5.0
    if rv > 2.0: score += 2.0; base_q = "B"
    elif rv > 1.5: score += 1.0; base_q = "C"
    else: base_q = "C"

    mom_abs = abs(mom)
    if direction == "LONG" and mom > 1.5: score += 2.0; base_q = "A" if base_q == "B" else base_q
    elif direction == "SHORT" and mom < -1.5: score += 2.0; base_q = "A" if base_q == "B" else base_q
    if (direction == "LONG" and hist > 0) or (direction == "SHORT" and hist < 0): score += 1.0
    if (direction == "LONG" and row["close"] > row["ema9"]): score += 1.0
    if (direction == "SHORT" and row["close"] < row["ema9"]): score += 1.0

    rsi_mid = abs(rsi_v - 50)
    if rsi_mid > 30: score -= 2.0

    if base_q == "A" and score >= 9.0: base_q = "A+"

    # Composite S skoru
    s = 0
    if adx_v >= 40:    s += 3
    elif adx_v >= 35:  s += 2
    elif adx_v >= 30:  s += 1

    rsi_dist = abs(rsi_v - 55)
    if rsi_dist <= 7:    s += 2
    elif rsi_dist <= 15: s += 1

    if rv >= 2.5:    s += 2
    elif rv >= 2.0:  s += 1

    if (direction == "LONG" and hist > 0) or (direction == "SHORT" and hist < 0): s += 1

    if mom_abs >= 2.5:   s += 2
    elif mom_abs >= 1.8: s += 1

    if btc_bullish and direction == "LONG": s += 1
    if not btc_bullish and direction == "SHORT": s += 1

    if hour in GOOD_HOURS: s += 1

    if s >= 10: return "S", score, s
    return base_q, score, s

def simulate(df, symbol):
    trades = []
    open_t = None

    for i in range(60, len(df)-101):
        row = df.iloc[i]
        if row["hour"] in BAD_HOURS: continue
        adx_v = row["adx"]
        if adx_v < ADX_MIN or pd.isna(adx_v): continue
        atr_v = row["atr"]
        if pd.isna(atr_v) or atr_v <= 0: continue

        ema_long  = row["ema9"]>row["ema21"]>row["ema50"]
        ema_short = row["ema9"]<row["ema21"]<row["ema50"]
        rsi_v = row["rsi"]

        if ema_long and row["pdi"]>row["mdi"] and 40<=rsi_v<=70:
            direction = "LONG"
        elif ema_short and row["mdi"]>row["pdi"] and 30<=rsi_v<=60:
            direction = "SHORT"
        else:
            continue

        quality, score, s_score = classify(row, direction, adx_v)
        if quality not in ["S", "A+"]: continue

        risk_usd = BALANCE * (RISK_S/100 if quality=="S" else RISK_AP/100)
        entry = row["close"]
        sl_d  = atr_v * SL_MULT
        sl    = entry - sl_d if direction=="LONG" else entry + sl_d
        tp1   = entry + sl_d*TP1_R if direction=="LONG" else entry - sl_d*TP1_R
        tp2   = entry + sl_d*TP2_R if direction=="LONG" else entry - sl_d*TP2_R
        tp3   = entry + sl_d*TP3_R if direction=="LONG" else entry - sl_d*TP3_R

        realized = 0.0; remaining = 1.0
        tp1_hit = tp2_hit = be_set = False
        result = None

        future = df.iloc[i+1:i+101]
        for _, fr in future.iterrows():
            h, l = fr["high"], fr["low"]
            if tp1_hit and not be_set:
                sl = entry*1.0005 if direction=="LONG" else entry*0.9995
                be_set = True
            sl_hit = (direction=="LONG" and l<=sl) or (direction=="SHORT" and h>=sl)
            if sl_hit:
                pnl_r = (sl-entry)/atr_v if direction=="LONG" else (entry-sl)/atr_v
                pnl = pnl_r*risk_usd*remaining + realized
                result = {"q": quality, "pnl": pnl, "w": pnl>0, "s_score": s_score}
                break
            if not tp1_hit:
                hit = (direction=="LONG" and h>=tp1) or (direction=="SHORT" and l<=tp1)
                if hit:
                    tp1_hit = True; realized += TP1_R*risk_usd*TP1_PCT; remaining = 1-TP1_PCT
            if tp1_hit and not tp2_hit:
                hit = (direction=="LONG" and h>=tp2) or (direction=="SHORT" and l<=tp2)
                if hit:
                    tp2_hit = True; realized += TP2_R*risk_usd*TP2_PCT; remaining = RUNNER_PCT
            if tp2_hit:
                hit = (direction=="LONG" and h>=tp3) or (direction=="SHORT" and l<=tp3)
                if hit:
                    pnl = realized + TP3_R*risk_usd*RUNNER_PCT
                    result = {"q": quality, "pnl": pnl, "w": True, "s_score": s_score}
                    break

        if result is None:
            close = future["close"].iloc[-1] if len(future)>0 else entry
            pnl_r = (close-entry)/atr_v if direction=="LONG" else (entry-close)/atr_v
            pnl = pnl_r*risk_usd*remaining + realized
            result = {"q": quality, "pnl": pnl, "w": pnl>0, "s_score": s_score}

        trades.append(result)

    return trades

def stats(trades, label):
    if not trades: return
    wins = [t for t in trades if t["w"]]
    losses = [t for t in trades if not t["w"]]
    wr = len(wins)/len(trades)*100
    gw = sum(t["pnl"] for t in wins)
    gl = abs(sum(t["pnl"] for t in losses))
    pf = gw/gl if gl>0 else 99
    net = gw - gl
    print(f"  {label:6s}: {len(trades):4d} trade | WR %{wr:.1f} | PF {pf:.2f}x | K/K {gw/gl:.2f}x | Net ${net:+.0f} | GW ${gw:.0f} GL ${gl:.0f}")

print("Veri çekiliyor...")
all_trades = []
from concurrent.futures import ThreadPoolExecutor, as_completed
dfs = {}
with ThreadPoolExecutor(max_workers=10) as ex:
    futs = {ex.submit(fetch, s): s for s in COINS}
    for f in as_completed(futs):
        sym = futs[f]
        df = f.result()
        if df is not None:
            dfs[sym] = df

print(f"{len(dfs)} coin verisi hazır. Simüle ediliyor...\n")
for sym, df in dfs.items():
    trades = simulate(df, sym)
    all_trades.extend(trades)

s_trades  = [t for t in all_trades if t["q"] == "S"]
ap_trades = [t for t in all_trades if t["q"] == "A+"]

print(f"{'='*65}")
print(f"S SINIFI BACKTEST — Config v2.5 (SL=1.0, TP1R=0.8, TP2R=3.5)")
print(f"{'='*65}")
stats(all_trades, "TOPLAM")
stats(s_trades,   "S")
stats(ap_trades,  "A+")
print(f"{'='*65}")
print(f"\nS sınıfı ortalama composite skor: {sum(t['s_score'] for t in s_trades)/len(s_trades):.1f}/14" if s_trades else "S sinyali yok")
print(f"S sınıfı risk: %{RISK_S} | A+ risk: %{RISK_AP}")
print(f"\nS sinyali başına ortalama PnL: ${sum(t['pnl'] for t in s_trades)/len(s_trades):.2f}" if s_trades else "")
print(f"A+ sinyali başına ortalama PnL: ${sum(t['pnl'] for t in ap_trades)/len(ap_trades):.2f}" if ap_trades else "")
