"""
news_engine.py — RSS Haber Duygu Analizi v1.0
===============================================
5 kripto haber kaynağından RSS beslemesi çeker.
Coin bazlı sentiment skoru üretir (-1.0 ile +1.0).
Harici bağımlılık yok — sadece stdlib (urllib, xml).

Kaynak önceliği: CoinTelegraph > CoinDesk > Decrypt > CryptoSlate > TheBlock
Cache: haberler 30 dakika, coin skorları 10 dakika
"""

import urllib.request
import xml.etree.ElementTree as ET
import re
import time
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# RSS KAYNAKLARI
# ─────────────────────────────────────────────────────────────────────────────
RSS_FEEDS = [
    ("CoinTelegraph", "https://cointelegraph.com/rss"),
    ("CoinDesk",      "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Decrypt",       "https://decrypt.co/feed"),
    ("CryptoSlate",   "https://cryptoslate.com/feed/"),
    ("TheBlock",      "https://www.theblock.co/rss.xml"),
]

NEWS_CACHE_SEC  = 1800   # 30 dakika
SCORE_CACHE_SEC = 600    # 10 dakika
FETCH_TIMEOUT   = 8      # saniye

# ─────────────────────────────────────────────────────────────────────────────
# DUYGU ANAHTAR KELİMELERİ
# ─────────────────────────────────────────────────────────────────────────────
POSITIVE = [
    "bullish", "surge", "rally", "gain", "pump", "breakout", "break out",
    "all-time high", "ath", "new high", "listing", "partnership", "upgrade",
    "launch", "adoption", "institutional", "buy signal", "support", "bounce",
    "recovery", "growth", "milestone", "record", "integration", "approved",
    "etf", "inflow", "accumulate", "bull run", "accumulation", "outperform",
    "undervalued", "positive", "optimistic", "uptrend", "momentum",
]

NEGATIVE = [
    "bearish", "crash", "dump", "plunge", "fall", "drop", "hack", "exploit",
    "scam", "fraud", "ban", "lawsuit", "sell off", "resistance", "warning",
    "risk", "fear", "fud", "rug pull", "investigation", "probe", "fine",
    "penalty", "delist", "outflow", "liquidation", "bankruptcy", "insolvency",
    "attack", "vulnerability", "breach", "halted", "suspended", "shutdown",
    "bearish", "downtrend", "overvalued", "pessimistic", "correction",
    "blood", "rekt", "bubble", "crisis", "collapse",
]

# ─────────────────────────────────────────────────────────────────────────────
# COIN ALIAS HARİTASI  (sembol → haber metinlerindeki adlar)
# ─────────────────────────────────────────────────────────────────────────────
COIN_ALIASES = {
    "btc":     ["bitcoin"],
    "eth":     ["ethereum", "ether"],
    "sol":     ["solana"],
    "bnb":     ["binance coin", "binancecoin", "bnb chain"],
    "xrp":     ["ripple"],
    "ada":     ["cardano"],
    "doge":    ["dogecoin"],
    "shib":    ["shiba inu", "shiba"],
    "avax":    ["avalanche"],
    "link":    ["chainlink"],
    "dot":     ["polkadot"],
    "ltc":     ["litecoin"],
    "atom":    ["cosmos"],
    "near":    ["near protocol"],
    "fil":     ["filecoin"],
    "apt":     ["aptos"],
    "arb":     ["arbitrum"],
    "op":      ["optimism"],
    "inj":     ["injective"],
    "sui":     ["sui network"],
    "sei":     ["sei network"],
    "ton":     ["toncoin", "the open network"],
    "xau":     ["gold", "xauusd", "gold price", "precious metal"],
    "xag":     ["silver", "xagusd", "silver price"],
    "xpt":     ["platinum"],
    "tao":     ["bittensor"],
    "render":  ["render network", "rendertoken"],
    "pendle":  ["pendle finance"],
    "jup":     ["jupiter"],
    "pyth":    ["pyth network"],
    "pengu":   ["pudgy penguins"],
    "grt":     ["the graph"],
    "crv":     ["curve", "curve finance"],
    "uni":     ["uniswap"],
    "aave":    ["aave protocol"],
    "snx":     ["synthetix"],
    "mkr":     ["maker", "makerdao"],
    "comp":    ["compound finance"],
    "cake":    ["pancakeswap"],
    "ordi":    ["ordinals", "bitcoin ordinals"],
    "rune":    ["thorchain"],
    "gala":    ["gala games"],
    "sand":    ["the sandbox"],
    "mana":    ["decentraland"],
    "ape":     ["apecoin", "bored ape"],
    "imx":     ["immutable", "immutable x"],
    "lrc":     ["loopring"],
    "fet":     ["fetch.ai", "fetch ai"],
    "agix":    ["singularitynet"],
    "ocean":   ["ocean protocol"],
    "stx":     ["stacks"],
    "algo":    ["algorand"],
    "vet":     ["vechain"],
    "theta":   ["theta network"],
    "egld":    ["elrond", "multiversx"],
    "ksm":     ["kusama"],
    "hnt":     ["helium"],
    "qnt":     ["quant network"],
    "ankr":    ["ankr network"],
    "mina":    ["mina protocol"],
    "cel":     ["celsius"],
    "hbar":    ["hedera", "hedera hashgraph"],
    "icp":     ["internet computer", "dfinity"],
    "flow":    ["flow blockchain", "dapper labs"],
    "ftm":     ["fantom"],
    "not":     ["notcoin"],
    "move":    ["movement labs"],
    "hmstr":   ["hamster kombat"],
    "cati":    ["catizen"],
    "wif":     ["dogwifhat"],
    "pepe":    ["pepe coin", "pepecoin"],
    "bonk":    ["bonk coin"],
    "floki":   ["floki inu"],
    "trump":   ["trump coin", "maga"],
    "aixbt":   ["aixbt"],
    "bome":    ["book of meme"],
}

# ─────────────────────────────────────────────────────────────────────────────
# CACHE
# ─────────────────────────────────────────────────────────────────────────────
_news_items: list  = []     # [(title_lower, summary_lower, pub_str)]
_last_fetch: float = 0.0
_score_cache: dict = {}     # symbol -> (score_dict, ts)


# ─────────────────────────────────────────────────────────────────────────────
# RSS ÇEKME
# ─────────────────────────────────────────────────────────────────────────────

def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text)


def _fetch_rss(name: str, url: str) -> list:
    """Tek bir RSS kaynağından haberleri çeker."""
    items = []
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "AurvexBot/1.0 (crypto trading)"}
        )
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
            raw = resp.read()
        root = ET.fromstring(raw)
        for item in root.iter("item"):
            title   = _strip_html(item.findtext("title",       default="") or "")
            summary = _strip_html(item.findtext("description", default="") or "")
            pub     = item.findtext("pubDate", default="") or ""
            items.append((title.lower(), summary.lower(), pub))
    except Exception as e:
        logger.debug(f"[News] {name} RSS hatası: {e}")
    return items


def _refresh():
    """Tüm kaynaklardan haberleri yenile (cache süresi dolmuşsa)."""
    global _news_items, _last_fetch
    now = time.time()
    if now - _last_fetch < NEWS_CACHE_SEC:
        return
    fetched = []
    for name, url in RSS_FEEDS:
        items = _fetch_rss(name, url)
        fetched.extend(items)
        if items:
            logger.debug(f"[News] {name}: {len(items)} haber")
    _news_items = fetched
    _last_fetch = now
    if fetched:
        logger.info(f"[News] {len(fetched)} haber yüklendi ({len(RSS_FEEDS)} kaynak)")
    else:
        logger.warning("[News] Hiçbir kaynaktan haber alınamadı (ağ sorununu kontrol et)")


# ─────────────────────────────────────────────────────────────────────────────
# DUYGU HESAPLAMA
# ─────────────────────────────────────────────────────────────────────────────

def _text_sentiment(text: str) -> float:
    """Metin için -1.0..+1.0 ham duygu skoru."""
    pos = sum(1 for w in POSITIVE if w in text)
    neg = sum(1 for w in NEGATIVE if w in text)
    total = pos + neg
    if total == 0:
        return 0.0
    return (pos - neg) / total


def _symbol_keywords(symbol: str) -> list:
    """BTCUSDT → ['btc', 'bitcoin']"""
    base = re.sub(r"^1000", "", symbol).replace("USDT", "").lower()
    aliases = COIN_ALIASES.get(base, [])
    return [base] + aliases


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────

def get_news_sentiment(symbol: str) -> dict:
    """
    Coin için haber duygu analizi.

    Returns:
        {
            "score":    float   # -1.0 (çok negatif) → +1.0 (çok pozitif)
            "signal":   str     # "BULLISH" | "BEARISH" | "NEUTRAL"
            "mentions": int     # kaç haberde geçiyor
            "positive": int
            "negative": int
            "source_count": int # kaç kaynaktan haber var
        }
    """
    # Score cache kontrolü
    cached = _score_cache.get(symbol)
    if cached and (time.time() - cached[1]) < SCORE_CACHE_SEC:
        return cached[0]

    _refresh()

    keywords = _symbol_keywords(symbol)
    pos_count = neg_count = neutral_count = 0

    for title, summary, _ in _news_items:
        combined = title + " " + summary
        if not any(kw in combined for kw in keywords):
            continue
        s = _text_sentiment(combined)
        if s > 0:
            pos_count += 1
        elif s < 0:
            neg_count += 1
        else:
            neutral_count += 1

    mentions = pos_count + neg_count + neutral_count
    total_sentiment = pos_count + neg_count

    if total_sentiment == 0:
        score = 0.0
    else:
        score = (pos_count - neg_count) / total_sentiment

    if mentions == 0:
        signal = "NEUTRAL"
    elif score >= 0.3:
        signal = "BULLISH"
    elif score <= -0.3:
        signal = "BEARISH"
    else:
        signal = "NEUTRAL"

    result = {
        "score":        round(score, 3),
        "signal":       signal,
        "mentions":     mentions,
        "positive":     pos_count,
        "negative":     neg_count,
        "source_count": len(RSS_FEEDS),
    }
    _score_cache[symbol] = (result, time.time())
    return result


def get_market_sentiment() -> dict:
    """
    Genel kripto piyasası duygu analizi.
    BTC, ETH ve genel kripto haberlerini analiz eder.
    """
    _refresh()
    scores = []
    market_keywords = ["crypto", "bitcoin", "btc", "ethereum", "market",
                       "defi", "blockchain", "web3", "altcoin"]
    for title, summary, _ in _news_items:
        combined = title + " " + summary
        if any(kw in combined for kw in market_keywords):
            s = _text_sentiment(combined)
            if s != 0.0:
                scores.append(s)

    if not scores:
        return {"score": 0.0, "signal": "NEUTRAL", "n": 0}

    avg = sum(scores) / len(scores)
    if avg >= 0.2:
        signal = "BULLISH"
    elif avg <= -0.2:
        signal = "BEARISH"
    else:
        signal = "NEUTRAL"

    return {"score": round(avg, 3), "signal": signal, "n": len(scores)}


def get_top_news(symbol: str = None, limit: int = 5) -> list:
    """
    Coin veya genel piyasa için güncel haber başlıkları.
    Returns: [(title, sentiment_float), ...]
    """
    _refresh()
    if symbol:
        keywords = _symbol_keywords(symbol)
        items = [(t, s) for t, s, _ in _news_items
                 if any(kw in t + " " + s for kw in keywords)]
    else:
        items = [(t, s) for t, s, _ in _news_items]

    scored = []
    for title, summary, _ in _news_items:
        combined = title + " " + summary
        if symbol:
            keywords = _symbol_keywords(symbol)
            if not any(kw in combined for kw in keywords):
                continue
        sentiment = _text_sentiment(combined)
        scored.append((title[:120], round(sentiment, 2)))

    return scored[:limit]


def force_refresh():
    """Cache'i sıfırla ve haberleri hemen yenile."""
    global _last_fetch
    _last_fetch = 0.0
    _score_cache.clear()
    _refresh()
