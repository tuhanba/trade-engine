"""
Market Scanner v2 — Profesyonel Coin Seçim + Veri Toplama
==========================================================
Sadece coin seçmez — her scan verisini DB'ye yazar.

Her coin için tradeability_score hesaplar:
  volume_score, volatility_score, spread_score, orderbook_depth_score,
  oi_score, funding_score, trend_cleanliness_score, pump_dump_penalty

scanner_status: ELIGIBLE | WATCH | AVOID
scanner_reason: neden elendi/geçti

Tüm geçen/elenen coinler scanned_coins tablosuna yazılır.
"""
import logging
import time
from typing import List, Dict

logger = logging.getLogger(__name__)

try:
    import sys as _sys, os as _os
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from config import (
        COIN_UNIVERSE as _COIN_UNIVERSE,
        MIN_VOLUME_USD, MIN_PRICE, MAX_PRICE_CHANGE,
        MAX_SPREAD_PCT,
    )
except ImportError:
    _COIN_UNIVERSE = []
    MIN_VOLUME_USD    = 10_000_000
    MIN_PRICE         = 0.001
    MAX_PRICE_CHANGE  = 30.0
    MAX_SPREAD_PCT    = 0.1

# Coin evreni dışındaki coinleri DB'ye kaydetme (çok fazla gürültü olur)
_SAVE_NON_UNIVERSE = False
# Her scan döngüsünde coin başına DB yazma (True=her scan, False=sadece ELIGIBLE)
_SAVE_ALL_SCANNED  = True


class MarketScanner:
    def __init__(self, client, db_path: str = "trade_engine.db"):
        self.client       = client
        self.db_path      = db_path
        self.min_volume   = MIN_VOLUME_USD
        self.min_price    = MIN_PRICE
        self.coin_universe = set(_COIN_UNIVERSE)

        # Exchange info cache: tick_size, step_size, min_notional
        self._exchange_cache: Dict[str, dict] = {}
        self._exchange_cache_ts: float = 0
        self._EXCHANGE_TTL = 3600  # 1 saat

    # ── Exchange Info Cache ────────────────────────────────────────────────────

    def _refresh_exchange_info(self):
        now = time.time()
        if now - self._exchange_cache_ts < self._EXCHANGE_TTL:
            return
        try:
            info = self.client.futures_exchange_info()
            for s in info.get("symbols", []):
                sym = s["symbol"]
                tick_size = step_size = min_notional = None
                for f in s.get("filters", []):
                    if f["filterType"] == "PRICE_FILTER":
                        tick_size = float(f["tickSize"])
                    elif f["filterType"] == "LOT_SIZE":
                        step_size = float(f["stepSize"])
                    elif f["filterType"] in ("MIN_NOTIONAL", "NOTIONAL"):
                        min_notional = float(f.get("notional") or f.get("minNotional") or 5)
                self._exchange_cache[sym] = {
                    "tick_size":    tick_size or 0.0001,
                    "step_size":    step_size or 0.001,
                    "min_notional": min_notional or 5.0,
                }
            self._exchange_cache_ts = now
        except Exception as e:
            logger.warning(f"Exchange info yüklenemedi: {e}")

    def get_symbol_info(self, symbol: str) -> dict:
        self._refresh_exchange_info()
        return self._exchange_cache.get(symbol, {
            "tick_size": 0.0001, "step_size": 0.001, "min_notional": 5.0
        })

    # ── Cooldown kontrolü ──────────────────────────────────────────────────────

    def _get_cooldown_coins(self) -> set:
        try:
            import sqlite3
            with sqlite3.connect(self.db_path) as conn:
                now_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
                rows = conn.execute(
                    "SELECT symbol FROM coin_cooldown WHERE until > ?", (now_iso,)
                ).fetchall()
                return {row[0] for row in rows}
        except Exception:
            return set()

    # ── Orderbook spread & depth ───────────────────────────────────────────────

    def _get_orderbook_metrics(self, symbol: str) -> dict:
        """Spread % ve orderbook depth skoru. Hata durumunda varsayılan döner."""
        try:
            ob = self.client.futures_order_book(symbol=symbol, limit=10)
            bids = ob.get("bids", [])
            asks = ob.get("asks", [])
            if not bids or not asks:
                return {"spread_pct": 0.5, "depth_score": 3.0}
            best_bid = float(bids[0][0])
            best_ask = float(asks[0][0])
            mid = (best_bid + best_ask) / 2
            spread_pct = (best_ask - best_bid) / mid * 100 if mid > 0 else 1.0
            bid_depth  = sum(float(b[0]) * float(b[1]) for b in bids[:5])
            ask_depth  = sum(float(a[0]) * float(a[1]) for a in asks[:5])
            depth_usd  = bid_depth + ask_depth
            depth_score = min(10.0, depth_usd / 50_000)
            return {"spread_pct": round(spread_pct, 4), "depth_score": round(depth_score, 2)}
        except Exception:
            return {"spread_pct": 0.5, "depth_score": 3.0}

    # ── Funding rate ───────────────────────────────────────────────────────────

    def _get_funding_rate(self, symbol: str) -> float:
        try:
            r = self.client.futures_funding_rate(symbol=symbol, limit=1)
            if r:
                return float(r[-1]["fundingRate"])
        except Exception:
            pass
        return 0.0

    # ── Open Interest değişimi ─────────────────────────────────────────────────

    def _get_oi_change(self, symbol: str) -> float:
        """Son 2 ölçüm arasındaki OI değişimi %."""
        try:
            r = self.client.futures_open_interest_hist(
                symbol=symbol, period="5m", limit=3
            )
            if len(r) >= 2:
                old = float(r[-2]["sumOpenInterestValue"])
                new = float(r[-1]["sumOpenInterestValue"])
                return round((new - old) / (old + 1e-10) * 100, 4) if old > 0 else 0.0
        except Exception:
            pass
        return 0.0

    # ── Tradeability Score ─────────────────────────────────────────────────────

    def _score_coin(self, ticker: dict, ob_metrics: dict,
                    funding_rate: float, oi_change: float) -> dict:
        """
        0–10 arası component skorlar hesapla.
        Toplam tradeability_score = ağırlıklı ortalama.
        """
        volume      = float(ticker.get("quoteVolume", 0))
        price_chg   = abs(float(ticker.get("priceChangePercent", 0)))
        price       = float(ticker.get("lastPrice", 0))
        spread_pct  = ob_metrics.get("spread_pct", 0.5)
        depth_score = ob_metrics.get("depth_score", 3.0)
        pump_dump_penalty = 0.0
        reject_reason = None

        # ── Volume score ──────────────────────────────────────────────────────
        if volume < self.min_volume:
            return {"status": "AVOID", "reason": "low_volume",
                    "volume_score": 0, "volatility_score": 0,
                    "spread_score": 0, "orderbook_depth_score": 0,
                    "oi_score": 0, "funding_score": 0,
                    "trend_cleanliness_score": 0, "pump_dump_penalty": 0,
                    "tradeability_score": 0}
        volume_score = min(10.0, (volume / 20_000_000) * 8 + 2)

        # ── Volatility score ──────────────────────────────────────────────────
        # Çok az hareket → boring, çok fazla → pump/dump
        if price_chg < 0.3:
            volatility_score = 2.0
            reject_reason = "low_movement"
        elif price_chg > MAX_PRICE_CHANGE:
            volatility_score = 0.0
            pump_dump_penalty = 5.0
            reject_reason = "pump_dump_risk"
        elif price_chg > 15.0:
            volatility_score = 4.0
            pump_dump_penalty = 2.0
        elif 1.0 <= price_chg <= 10.0:
            volatility_score = min(10.0, price_chg * 0.8 + 3)
        else:
            volatility_score = 5.0

        # ── Spread score ──────────────────────────────────────────────────────
        if spread_pct > MAX_SPREAD_PCT:
            return {"status": "AVOID", "reason": "bad_spread",
                    "volume_score": round(volume_score, 2),
                    "volatility_score": round(volatility_score, 2),
                    "spread_score": 0, "orderbook_depth_score": round(depth_score, 2),
                    "oi_score": 5, "funding_score": 5,
                    "trend_cleanliness_score": 5, "pump_dump_penalty": round(pump_dump_penalty, 2),
                    "tradeability_score": 0}
        spread_score = max(0.0, 10.0 - spread_pct * 50)

        # ── OI score ──────────────────────────────────────────────────────────
        # Pozitif OI değişimi = büyüyen pozisyonlar → iyi
        if oi_change > 5:
            oi_score = 8.0
        elif oi_change > 2:
            oi_score = 6.0
        elif oi_change > 0:
            oi_score = 5.0
        elif oi_change > -2:
            oi_score = 4.0
        else:
            oi_score = 2.0

        # ── Funding score ─────────────────────────────────────────────────────
        # Çok yüksek funding = long'lar aşırı yüklenmiş → short risk artıyor
        abs_funding = abs(funding_rate)
        if abs_funding > 0.002:
            funding_score = 2.0
            pump_dump_penalty += 1.0
            if not reject_reason:
                reject_reason = "high_funding"
        elif abs_funding > 0.001:
            funding_score = 5.0
        elif abs_funding < 0.0003:
            funding_score = 8.0
        else:
            funding_score = 6.5

        # ── Trend cleanliness (yaklaşık — ticker ile) ──────────────────────────
        # Hiç trend yoksa hem long hem short zor
        if price_chg < 0.5:
            trend_cleanliness_score = 3.0
        elif price_chg > 8 and abs_funding > 0.001:
            trend_cleanliness_score = 4.0  # overbought/oversold riski
        else:
            trend_cleanliness_score = min(10.0, price_chg * 0.6 + 4)

        # ── Toplam skor ───────────────────────────────────────────────────────
        raw_score = (
            volume_score             * 0.25 +
            volatility_score         * 0.20 +
            spread_score             * 0.15 +
            depth_score              * 0.10 +
            oi_score                 * 0.10 +
            funding_score            * 0.10 +
            trend_cleanliness_score  * 0.10
        ) - pump_dump_penalty * 0.3

        tradeability_score = round(max(0.0, min(10.0, raw_score)), 2)

        if tradeability_score >= 6.5:
            status = "ELIGIBLE"
            reason = reject_reason or "ok"
        elif tradeability_score >= 4.0:
            status = "WATCH"
            reason = reject_reason or "borderline"
        else:
            status = "AVOID"
            reason = reject_reason or "low_score"

        return {
            "status":                 status,
            "reason":                 reason,
            "volume_score":           round(volume_score, 2),
            "volatility_score":       round(volatility_score, 2),
            "spread_score":           round(spread_score, 2),
            "orderbook_depth_score":  round(depth_score, 2),
            "oi_score":               round(oi_score, 2),
            "funding_score":          round(funding_score, 2),
            "trend_cleanliness_score":round(trend_cleanliness_score, 2),
            "pump_dump_penalty":      round(pump_dump_penalty, 2),
            "tradeability_score":     tradeability_score,
        }

    # ── Ana tarama ─────────────────────────────────────────────────────────────

    def scan(self, save_to_db: bool = True) -> List[Dict]:
        """
        Tüm USDT paritelerini tara. Coin evreni filtresi uygulanır.

        Returns:
            ELIGIBLE ve WATCH statüsündeki coinlerin listesi, skora göre sıralı.
            Her eleman tradeability_score + scoring detaylarını içerir.
        """
        try:
            tickers = self.client.futures_ticker()
        except Exception as e:
            logger.error(f"futures_ticker hatası: {e}")
            return []

        cooldown_coins = self._get_cooldown_coins()
        ticker_map = {t["symbol"]: t for t in tickers}

        candidates = []
        avoid_count = 0
        save_batch  = []

        for symbol in self.coin_universe:
            t = ticker_map.get(symbol)
            if not t:
                continue

            if symbol in cooldown_coins:
                logger.debug(f"Cooldown: {symbol}")
                continue

            price = float(t.get("lastPrice", 0))
            if price < self.min_price:
                continue

            # Orderbook metrics (sadece USDT universe için maliyeti düşür: sadece top 10)
            ob_metrics  = self._get_orderbook_metrics(symbol)
            funding_rate = self._get_funding_rate(symbol)
            oi_change    = self._get_oi_change(symbol)

            scoring = self._score_coin(t, ob_metrics, funding_rate, oi_change)

            coin_data = {
                "symbol":               symbol,
                "price":                price,
                "volume_24h":           float(t.get("quoteVolume", 0)),
                "price_change_24h":     float(t.get("priceChangePercent", 0)),
                "atr_percent":          0.0,  # scanner'da ATR yok, engine hesaplar
                "spread_percent":       ob_metrics.get("spread_pct", 0),
                "funding_rate":         funding_rate,
                "open_interest_change": oi_change,
                "scanner_status":       scoring["status"],
                "scanner_reason":       scoring["reason"],
                **{k: v for k, v in scoring.items() if k not in ("status", "reason")},
            }

            if scoring["status"] == "AVOID":
                avoid_count += 1
                if save_to_db and _SAVE_ALL_SCANNED:
                    save_batch.append(coin_data)
                continue

            candidates.append(coin_data)
            if save_to_db:
                save_batch.append(coin_data)

        # Batch DB yazımı
        if save_to_db and save_batch:
            self._batch_save(save_batch)

        # Skora göre sırala
        candidates.sort(key=lambda x: x["tradeability_score"], reverse=True)
        logger.info(
            f"Market Scan: {len(candidates)} aday "
            f"({sum(1 for c in candidates if c['scanner_status']=='ELIGIBLE')} ELIGIBLE, "
            f"{sum(1 for c in candidates if c['scanner_status']=='WATCH')} WATCH), "
            f"{avoid_count} AVOID"
        )
        return candidates

    def _batch_save(self, batch: list):
        try:
            from database import save_scanned_coin
            for coin in batch:
                save_scanned_coin(coin)
        except Exception as e:
            logger.error(f"Scanner batch save hatası: {e}")

    def get_eligible(self, save_to_db: bool = True) -> List[Dict]:
        """Sadece ELIGIBLE coinleri döner."""
        return [c for c in self.scan(save_to_db=save_to_db)
                if c["scanner_status"] == "ELIGIBLE"]
