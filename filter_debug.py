"""
filter_debug.py — Hangi filtrede kaç coin düşüyor analiz et
"""
import sys, requests, pandas as pd, numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed
sys.path.insert(0, "/home/ubuntu/trade-engine")
from config import COIN_UNIVERSE, ADX_MIN_THRESHOLD, SL_ATR_MULT, MIN_RR

def ema(s, p): return s.ewm(span=p, adjust=False).mean()
def rsi_val(s, p=14):
    d = s.diff(); g = d.where(d>0,0).rolling(p).mean(); l = (-d.where(d<0,0)).rolling(p).mean()
    return float((100-(100/(1+g/(l+1e-10)))).iloc[-1])
def atr_val(df, p=14):
    hl=df["high"]-df["low"]; hc=(df["high"]-df["close"].shift()).abs(); lc=(df["low"]-df["close"].shift()).abs()
    return float(pd.concat([hl,hc,lc],axis=1).max(axis=1).rolling(p).mean().iloc[-1])
def adx_val(df, p=14):
    up=df["high"].diff(); dn=-df["low"].diff()
    pos=pd.Series(np.where((up>dn)&(up>0),up,0.0),index=df.index)
    neg=pd.Series(np.where((dn>up)&(dn>0),dn,0.0),index=df.index)
    hl=df["high"]-df["low"]; hc=(df["high"]-df["close"].shift()).abs(); lc=(df["low"]-df["close"].shift()).abs()
    tr=pd.concat([hl,hc,lc],axis=1).max(axis=1)
    pos_di=100*(pos.ewm(alpha=1/p,adjust=False).mean()/tr.ewm(alpha=1/p,adjust=False).mean())
    neg_di=100*(neg.ewm(alpha=1/p,adjust=False).mean()/tr.ewm(alpha=1/p,adjust=False).mean())
    dx=100*abs(pos_di-neg_di)/(pos_di+neg_di+1e-10)
    return float(dx.ewm(alpha=1/p,adjust=False).mean().iloc[-1])
def rv_val(df, p=20):
    avg=df["volume"].rolling(p).mean().shift(1)
    return float((df["volume"]/(avg+1e-10)).iloc[-1])
def mom3c_val(df):
    s=0.0
    for j in range(len(df)-3,len(df)):
        o,c=df["open"].iloc[j],df["close"].iloc[j]
        body=abs(c-o); rng=df["high"].iloc[j]-df["low"].iloc[j]
        st=body/(rng+1e-10); s+=st if c>o else -st
    return s
def fetch(symbol):
    try:
        r=requests.get("https://fapi.binance.com/fapi/v1/klines",
            params={"symbol":symbol,"interval":"5m","limit":80},timeout=8)
        r.raise_for_status()
        df=pd.DataFrame(r.json(),columns=["time","open","high","low","close","volume","ct","qav","nt","tbbav","tbqav","ignore"])
        for c in ["open","high","low","close","volume"]: df[c]=df[c].astype(float)
        return df
    except: return pd.DataFrame()

def check(sym):
    df=fetch(sym)
    if df.empty or len(df)<55: return {"sym":sym,"fail":"no_data","adx":0,"rv":0,"rsi":0,"mom":0,"dir":"—"}
    try:
        e9=float(ema(df["close"],9).iloc[-1]); e21=float(ema(df["close"],21).iloc[-1]); e50=float(ema(df["close"],50).iloc[-1])
        if e9>e21>e50: direction="LONG"
        elif e9<e21<e50: direction="SHORT"
        else: return {"sym":sym,"fail":"no_trend","adx":0,"rv":0,"rsi":0,"mom":0,"dir":"—"}
        adx=adx_val(df)
        if adx<ADX_MIN_THRESHOLD: return {"sym":sym,"fail":f"adx_low({adx:.1f}<{ADX_MIN_THRESHOLD})","adx":adx,"rv":0,"rsi":0,"mom":0,"dir":direction}
        rv=rv_val(df)
        if rv<1.2: return {"sym":sym,"fail":f"rv_low({rv:.2f}<1.2)","adx":adx,"rv":rv,"rsi":0,"mom":0,"dir":direction}
        rsi=rsi_val(df["close"])
        if direction=="LONG" and not (35<rsi<75): return {"sym":sym,"fail":f"rsi_bad({rsi:.1f})","adx":adx,"rv":rv,"rsi":rsi,"mom":0,"dir":direction}
        if direction=="SHORT" and not (25<rsi<65): return {"sym":sym,"fail":f"rsi_bad({rsi:.1f})","adx":adx,"rv":rv,"rsi":rsi,"mom":0,"dir":direction}
        mom=mom3c_val(df)
        if direction=="LONG" and mom<1.0: return {"sym":sym,"fail":f"mom_low({mom:.2f}<1.0)","adx":adx,"rv":rv,"rsi":rsi,"mom":mom,"dir":direction}
        if direction=="SHORT" and mom>-1.0: return {"sym":sym,"fail":f"mom_high({mom:.2f}>-1.0)","adx":adx,"rv":rv,"rsi":rsi,"mom":mom,"dir":direction}
        return {"sym":sym,"fail":"PASS","adx":adx,"rv":rv,"rsi":rsi,"mom":mom,"dir":direction}
    except Exception as e: return {"sym":sym,"fail":f"error:{e}","adx":0,"rv":0,"rsi":0,"mom":0,"dir":"—"}

print(f"92 coin analiz ediliyor...")
results = []
with ThreadPoolExecutor(max_workers=15) as ex:
    futures = {ex.submit(check, s): s for s in COIN_UNIVERSE}
    for fut in as_completed(futures):
        results.append(fut.result())

from collections import Counter
fail_counts = Counter()
pass_list = []
for r in results:
    if r["fail"] == "PASS":
        pass_list.append(r)
    else:
        # Sadece fail nedeninin kategorisini al
        cat = r["fail"].split("(")[0]
        fail_counts[cat] += 1

print(f"\n{'='*50}")
print(f"TOPLAM: {len(results)} coin")
print(f"PASS (sinyal üretebilir): {len(pass_list)}")
print(f"\nFILTRE DAĞILIMI:")
for cat, cnt in sorted(fail_counts.items(), key=lambda x: -x[1]):
    print(f"  {cat}: {cnt} coin")

if pass_list:
    print(f"\nSİNYAL ÜRETEBİLEN COİNLER:")
    for r in sorted(pass_list, key=lambda x: -x["adx"]):
        print(f"  {r['sym']:<20} {r['dir']:<6} ADX:{r['adx']:.1f} RV:{r['rv']:.2f} RSI:{r['rsi']:.1f} MOM:{r['mom']:.2f}")

# ADX dağılımı
adx_vals = [r["adx"] for r in results if r["adx"] > 0]
if adx_vals:
    print(f"\nADX DAĞILIMI (threshold={ADX_MIN_THRESHOLD}):")
    print(f"  Ortalama: {sum(adx_vals)/len(adx_vals):.1f}")
    print(f"  Max: {max(adx_vals):.1f}")
    print(f"  > {ADX_MIN_THRESHOLD}: {sum(1 for x in adx_vals if x > ADX_MIN_THRESHOLD)} coin")
    print(f"  > 20: {sum(1 for x in adx_vals if x > 20)} coin")
    print(f"  > 15: {sum(1 for x in adx_vals if x > 15)} coin")
