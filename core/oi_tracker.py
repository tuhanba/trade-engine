"""
core/oi_tracker.py — Open Interest Tracker v1.0
================================================
Binance Futures'dan gerçek OI çeker ve delta hesaplar.
market_scanner.py'deki hardcoded 0.0 değerini replace eder.

OI Sinyal Mantığı:
  OI+ Price+  → STRONG_BULL (yeni long pozisyonlar açılıyor)
  OI+ Price-  → STRONG_BEAR (yeni short pozisyonlar açılıyor)
  OI- Price+  → SHORT_SQUEEZE (short'lar kapanıyor = zayıf ralli)
  OI- Price-  → LONG_LIQUIDATION (long'lar kapanıyor = zayıf düşüş)

OI artış %5+ = anlamlı değişim
"""
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Cache: sembol → {ts, oi, price}
_oi_cache: dict = {}
_OI_TTL = 120  # 2 dakika cache


class OITracker:
    """
    Real-time Open Interest takipçisi.
    """

    def __init__(self, client):
        self.client = client
        self._prev_oi: dict = {}  # {symbol: (oi_value, timestamp)}

    def get_oi(self, symbol: str) -> Optional[float]:
        """Güncel OI değerini döner (USDT cinsinden)."""
        global _oi_cache
        now = time.time()
        cached = _oi_cache.get(symbol)
        if cached and (now - cached["ts"]) < _OI_TTL:
            return cached["oi"]

        try:
            result = self.client.futures_open_interest(symbol=symbol)
            oi = float(result["openInterest"])

            # Get current price for USDT conversion
            ticker = self.client.futures_symbol_ticker(symbol=symbol)
            cur_price = float(ticker["price"])
            oi_usdt = oi * cur_price

            _oi_cache[symbol] = {"ts": now, "oi": oi, "oi_usdt": oi_usdt, "price": cur_price}
            return oi
        except Exception as e:
            logger.debug(f"[OI] {symbol} fetch hatası: {e}")
            return None

    def analyze(self, symbol: str, current_price: float, direction: str) -> dict:
        """
        OI değişim analizini döner.

        Returns:
            oi_signal: "STRONG_BULL"|"STRONG_BEAR"|"SHORT_SQUEEZE"|"LONG_LIQ"|"NEUTRAL"
            oi_score_bonus: float (-2.0 to +2.0)
            oi_change_pct: float — son cache'den değişim yüzdesi
            oi_value: float — güncel OI (coin cinsinden)
        """
        global _oi_cache
        now = time.time()

        try:
            result = self.client.futures_open_interest(symbol=symbol)
            cur_oi = float(result["openInterest"])

            # Önceki OI ile karşılaştır
            prev = self._prev_oi.get(symbol)
            if prev is None:
                self._prev_oi[symbol] = (cur_oi, now)
                _oi_cache[symbol] = {"ts": now, "oi": cur_oi, "price": current_price}
                return _oi_neutral(cur_oi)

            prev_oi, prev_ts = prev
            age_minutes = (now - prev_ts) / 60

            # Çok eski kayıt → güncelle
            if age_minutes > 10:
                self._prev_oi[symbol] = (cur_oi, now)
                _oi_cache[symbol] = {"ts": now, "oi": cur_oi, "price": current_price}
                return _oi_neutral(cur_oi)

            # OI değişim yüzdesi
            oi_change_pct = (cur_oi - prev_oi) / (prev_oi + 1e-10) * 100

            # Fiyat değişimi (prev_ts'den beri yaklaşık — cache'den alıyoruz)
            cached = _oi_cache.get(symbol, {})
            prev_price = cached.get("price", current_price)
            price_change_pct = (current_price - prev_price) / (prev_price + 1e-10) * 100

            # Cache güncelle
            _oi_cache[symbol] = {"ts": now, "oi": cur_oi, "price": current_price, "oi_usdt": cur_oi * current_price}
            self._prev_oi[symbol] = (cur_oi, now)

            # Sinyal
            oi_up    = oi_change_pct > 2.0    # OI %2+ artış
            oi_dn    = oi_change_pct < -2.0   # OI %2+ düşüş
            price_up = price_change_pct > 0.3  # Fiyat %0.3+ artış
            price_dn = price_change_pct < -0.3

            if oi_up and price_up:
                signal = "STRONG_BULL"
                if direction == "LONG":
                    bonus = 1.5
                else:
                    bonus = -1.0
            elif oi_up and price_dn:
                signal = "STRONG_BEAR"
                if direction == "SHORT":
                    bonus = 1.5
                else:
                    bonus = -1.0
            elif oi_dn and price_up:
                signal = "SHORT_SQUEEZE"
                # Squeeze'ler geçici → dikkatli bonus
                if direction == "LONG":
                    bonus = 0.5
                else:
                    bonus = -0.5
            elif oi_dn and price_dn:
                signal = "LONG_LIQ"
                if direction == "SHORT":
                    bonus = 0.5
                else:
                    bonus = -0.5
            else:
                signal = "NEUTRAL"
                bonus = 0.0

            # Güçlü OI artışı ekstra bonus
            if abs(oi_change_pct) > 5.0:
                bonus = bonus * 1.3  # %30 daha güçlü sinyal

            result_dict = {
                "oi_signal":     signal,
                "oi_score_bonus": round(max(-2.0, min(2.0, bonus)), 2),
                "oi_change_pct": round(oi_change_pct, 2),
                "oi_value":      round(cur_oi, 2),
                "oi_usdt":       round(cur_oi * current_price, 0),
            }

            logger.debug(
                f"[OI] {symbol} {direction}: {signal} "
                f"oi_chg={oi_change_pct:+.1f}% price_chg={price_change_pct:+.2f}% bonus={bonus:+.1f}"
            )
            return result_dict

        except Exception as e:
            logger.debug(f"[OI] Analiz hatası {symbol}: {e}")
            return _oi_neutral(0.0)


def _oi_neutral(oi_val: float = 0.0) -> dict:
    return {
        "oi_signal":     "NEUTRAL",
        "oi_score_bonus": 0.0,
        "oi_change_pct": 0.0,
        "oi_value":      oi_val,
        "oi_usdt":       0.0,
    }


# ── Standalone fonksiyon (market_scanner için) ──────────────────────────────

_scanner_oi_cache: dict = {}
_SCANNER_OI_TTL = 300  # 5 dakika (scan loop'ta çok sık çağrılır)

def get_oi_for_scanner(client, symbol: str) -> dict:
    """
    Market scanner'da kullanmak için hafif OI çekici.
    Returns: {"open_interest": float, "open_interest_usdt": float, "open_interest_change": float}
    """
    global _scanner_oi_cache
    now = time.time()
    cached = _scanner_oi_cache.get(symbol)
    if cached and (now - cached["ts"]) < _SCANNER_OI_TTL:
        return cached["data"]

    try:
        result = client.futures_open_interest(symbol=symbol)
        cur_oi = float(result["openInterest"])

        ticker = client.futures_symbol_ticker(symbol=symbol)
        cur_price = float(ticker["price"])
        oi_usdt = cur_oi * cur_price

        # Değişim hesabı
        prev = _scanner_oi_cache.get(symbol, {}).get("data", {})
        prev_oi = prev.get("open_interest", cur_oi)
        oi_chg = (cur_oi - prev_oi) / (prev_oi + 1e-10) * 100

        data = {
            "open_interest":        round(cur_oi, 4),
            "open_interest_usdt":   round(oi_usdt, 0),
            "open_interest_change": round(oi_chg, 2),  # %
        }
        _scanner_oi_cache[symbol] = {"ts": now, "data": data}
        return data
    except Exception:
        return {"open_interest": 0.0, "open_interest_usdt": 0.0, "open_interest_change": 0.0}
