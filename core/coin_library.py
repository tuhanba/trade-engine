"""
core/coin_library.py – Tradable sembol kütüphanesi ve filtre modülü.

USDT pariteleri hacim ve hareketle filtrelenir.
Düşük hacimli / istenmeyen pariteler elenir.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from typing import Any, Dict, List, Set

logger = logging.getLogger("ax.coin_library")

# İstenmeyen base coinler
_SKIP_BASES = {"BUSD", "USDC", "TUSD", "DAI", "FDUSD"}

# Leveraged / kaldıraçlı token etiketleri
_SKIP_TAGS = ("UP", "DOWN", "BULL", "BEAR")


def build_symbol_universe(
    tickers: list[dict],
    min_volume_usdt: float = 5_000_000.0,
    min_move_pct: float = 0.5,
) -> list[dict]:
    """
    Ticker listesinden tradable USDT sembollerini filtreler.

    Args:
        tickers: Binance ticker verisi listesi
        min_volume_usdt: Minimum 24h USDT hacmi
        min_move_pct: Minimum |priceChangePercent|

    Returns:
        Filtrelenmiş ticker listesi
    """
    universe = []
    for t in tickers:
        sym = t.get("symbol", "")
        if not sym.endswith("USDT"):
            continue

        base = sym.replace("USDT", "")
        if base in _SKIP_BASES:
            continue
        if any(tag in base for tag in _SKIP_TAGS):
            continue

        try:
            vol = float(t.get("quoteVolume", 0))
            change = abs(float(t.get("priceChangePercent", 0)))
        except (ValueError, TypeError):
            continue

        if vol >= min_volume_usdt and change >= min_move_pct:
            universe.append(t)

    logger.info(
        "Symbol universe: %d/%d sembol (vol>=%.0f, move>=%.1f%%)",
        len(universe), len(tickers), min_volume_usdt, min_move_pct,
    )
    return universe


def rank_symbols_by_activity(symbols: list[dict]) -> list[dict]:
    """
    Sembolleri hacim * hareket skoru ile sıralar (en aktif en üstte).
    """
    scored = []
    for s in symbols:
        try:
            vol = float(s.get("quoteVolume", 0))
            change = abs(float(s.get("priceChangePercent", 0)))
        except (ValueError, TypeError):
            vol, change = 0, 0
        scored.append((vol * change, s))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [s for _, s in scored]


def exclude_bad_symbols(symbols: list[dict]) -> list[dict]:
    """
    İstenmeyen / sorunlu sembolleri eler.
    Şu an: stablecoin base ve leveraged token filtreleme.
    İleride: blacklist, delist uyarısı gibi filtreler eklenebilir.
    """
    result = []
    for s in symbols:
        sym = s.get("symbol", "")
        base = sym.replace("USDT", "")
        if base in _SKIP_BASES:
            continue
        if any(tag in base for tag in _SKIP_TAGS):
            continue
        result.append(s)
    return result


class CoinLibrary:
    def __init__(self, client, db_path="trade_engine.db"):
        self.client = client
        self.db_path = db_path
        # Kalite Eşikleri
        self.MIN_VOLUME_24H = 15_000_000  # 15M USD altı 'ölü' kabul edilir
        self.MAX_VOLATILITY_24H = 40.0    # %40+ hareket manipülasyon riskidir
        self.MIN_VOLATILITY_24H = 1.5     # %1.5 altı 'hareketsiz' kabul edilir
        self.MAX_SPREAD_PCT = 0.15        # %0.15+ spread likidite sorunudur
        self.TOP_N_COINS = 100            # En iyi 100 coin'i takip et

    def refresh_universe(self) -> List[str]:
        """Binance'ten güncel verileri çeker, filtreler ve evreni günceller."""
        try:
            logger.info("Coin evreni güncelleniyor...")
            tickers = self.client.futures_ticker()
            exchange_info = self.client.futures_exchange_info()

            # Geçerli semboller (USDT Perpetual)
            valid_symbols = {
                s["symbol"]: s for s in exchange_info["symbols"]
                if s["quoteAsset"] == "USDT"
                and s["status"] == "TRADING"
                and s.get("contractType") == "PERPETUAL"
            }

            candidates = []
            for t in tickers:
                symbol = t["symbol"]
                if symbol not in valid_symbols:
                    continue

                volume = float(t["quoteVolume"])
                price_change = abs(float(t["priceChangePercent"]))

                # 1. Hacim Filtresi
                if volume < self.MIN_VOLUME_24H:
                    continue

                # 2. Volatilite Filtresi (Aşırı pump/dump veya ölü tahta)
                if price_change > self.MAX_VOLATILITY_24H or price_change < self.MIN_VOLATILITY_24H:
                    continue

                # 3. Skorlama (Hacim ve Volatilite ağırlıklı)
                score = (volume / 100_000_000) * 5.0 + (price_change / 10.0) * 5.0

                candidates.append({
                    "symbol": symbol,
                    "score": score,
                    "volume": volume,
                    "volatility": price_change
                })

            # Skora göre sırala ve ilk N tanesini al
            candidates.sort(key=lambda x: x["score"], reverse=True)
            top_coins = [c["symbol"] for c in candidates[:self.TOP_N_COINS]]

            # BTC ve ETH her zaman dahil olmalı
            for essential in ["BTCUSDT", "ETHUSDT"]:
                if essential not in top_coins:
                    top_coins.append(essential)

            logger.info(f"Yeni coin evreni oluşturuldu: {len(top_coins)} sembol.")
            self._update_db_universe(top_coins)
            return top_coins

        except Exception as e:
            logger.error(f"Coin evreni yenileme hatası: {e}")
            return []

    def _update_db_universe(self, symbols: List[str]):
        """Veritabanındaki aktif coin listesini günceller."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("CREATE TABLE IF NOT EXISTS active_universe (symbol TEXT PRIMARY KEY, last_updated REAL)")
                conn.execute("DELETE FROM active_universe")
                ts = time.time()
                conn.executemany(
                    "INSERT INTO active_universe (symbol, last_updated) VALUES (?, ?)",
                    [(s, ts) for s in symbols]
                )
        except Exception as e:
            logger.error(f"DB evren güncelleme hatası: {e}")

    def get_active_universe(self) -> List[str]:
        """Veritabanından veya önbellekten aktif evreni döner."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                rows = conn.execute("SELECT symbol FROM active_universe").fetchall()
                return [r[0] for r in rows]
        except Exception:
            return []


def get_coin_params(symbol: str) -> Dict:
    """Coin'e özel hassasiyet ve geçmiş performans verilerini döner."""
    base = {
        "win_rate": 0.55,
        "avg_profit": 0.012,
        "volatility_mult": 1.0,
        "cooldown_minutes": 30,
    }
    # coin_configs'ten coin-specific SL mult al
    try:
        sl_mult = get_coin_optimal_sl_mult(symbol)
        if sl_mult != 1.8:
            base["sl_atr_mult"] = sl_mult
    except Exception:
        pass
    return base


# ─────────────────────────────────────────────────────────────────────────────
# COIN LIBRARY v2 — Kapsamlı coin scoring
# ─────────────────────────────────────────────────────────────────────────────

import json
from datetime import datetime, timezone, timedelta


def build_coin_profiles(client, symbols: list) -> dict:
    """Her coin için kapsamlı profil oluşturur. Returns {symbol: profile_dict}."""
    profiles = {}
    for symbol in symbols:
        try:
            profile = _analyze_coin(client, symbol)
            profiles[symbol] = profile
            _save_coin_profile(symbol, profile)
        except Exception as e:
            logger.warning(f"[CoinLib] {symbol} profil hatası: {e}")
    return profiles


def _analyze_coin(client, symbol: str) -> dict:
    """Tek coin için kapsamlı analiz: ATR, ADX, trend tutarlılığı, funding."""
    import pandas as pd
    import numpy as np

    klines = client.futures_klines(symbol=symbol, interval="1h", limit=720)
    df = pd.DataFrame(klines, columns=[
        "time", "open", "high", "low", "close", "volume",
        "ct", "qav", "nt", "tbbav", "tbqav", "ignore"
    ])
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype(float)

    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift()).abs(),
        (df["low"] - df["close"].shift()).abs(),
    ], axis=1).max(axis=1)
    atr14 = tr.rolling(14).mean()
    current_price = df["close"].iloc[-1]
    atr_pct = float(atr14.iloc[-1] / current_price * 100)

    plus_dm  = (df["high"].diff()).where(df["high"].diff() > df["low"].diff().abs(), 0).clip(lower=0)
    minus_dm = (-df["low"].diff()).where(df["low"].diff().abs() > df["high"].diff(), 0).clip(lower=0)
    plus_di  = 100 * (plus_dm.rolling(14).mean() / (atr14 + 1e-10))
    minus_di = 100 * (minus_dm.rolling(14).mean() / (atr14 + 1e-10))
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-10)
    adx_series = dx.rolling(14).mean()
    adx = float(adx_series.iloc[-1])
    trend_consistency = float((adx_series > 25).mean() * 100)

    vol_cv = float(df["volume"].std() / (df["volume"].mean() + 1e-10) * 100)
    df_24h = df.tail(24)
    range_pct = float((df_24h["high"].max() - df_24h["low"].min()) / current_price * 100)

    try:
        funding_data = client.futures_funding_rate(symbol=symbol, limit=8)
        funding_rates = [float(f["fundingRate"]) for f in funding_data]
        avg_funding = sum(funding_rates) / len(funding_rates) if funding_rates else 0
        funding_trend = ("LONG_FAVOR" if avg_funding < -0.0001
                         else "SHORT_FAVOR" if avg_funding > 0.0001 else "NEUTRAL")
    except Exception:
        avg_funding = 0
        funding_trend = "NEUTRAL"

    score = 50.0
    if adx > 30:       score += 15
    elif adx > 20:     score += 5
    else:              score -= 10
    if trend_consistency > 60: score += 10
    elif trend_consistency > 40: score += 5
    if 0.8 <= atr_pct <= 3.0:  score += 10
    elif atr_pct < 0.5:        score -= 15
    elif atr_pct > 5.0:        score -= 10
    if vol_cv < 80:    score += 5
    elif vol_cv > 150: score -= 10
    if range_pct < 1.5: score -= 15
    score = max(0.0, min(100.0, score))

    if atr_pct < 1.0:   optimal_sl_mult = 2.5
    elif atr_pct < 2.0: optimal_sl_mult = 2.0
    else:               optimal_sl_mult = 1.8

    return {
        "symbol":            symbol,
        "coin_score":        round(score, 1),
        "atr_pct":           round(atr_pct, 3),
        "adx_current":       round(adx, 1),
        "trend_consistency": round(trend_consistency, 1),
        "volume_cv":         round(vol_cv, 1),
        "range_pct_24h":     round(range_pct, 2),
        "avg_funding":       round(avg_funding, 5),
        "funding_trend":     funding_trend,
        "optimal_sl_mult":   optimal_sl_mult,
        "recommended":       score >= 50,
        "avoid":             score < 30,
        "updated_at":        datetime.now(timezone.utc).isoformat(),
    }


def _save_coin_profile(symbol: str, profile: dict):
    """Coin profilini coin_configs tablosuna JSON olarak kaydet."""
    try:
        from database import get_conn
        with get_conn() as conn:
            now = profile["updated_at"]
            # Fetch existing config to merge and preserve YZ overrides
            row = conn.execute(
                "SELECT config_json FROM coin_configs WHERE coin = ?", (symbol,)
            ).fetchone()
            existing = json.loads(row[0]) if row and row[0] else {}
            existing.update(profile)
            
            config_str = json.dumps(existing, ensure_ascii=False)
            conn.execute("""
                UPDATE coin_configs SET config_json=?, updated_at=? WHERE coin=?
            """, (config_str, now, symbol))
            if conn.rowcount == 0:
                conn.execute("""
                    INSERT INTO coin_configs (coin, config_json, updated_at)
                    VALUES (?, ?, ?)
                """, (symbol, config_str, now))
    except Exception as e:
        logger.debug(f"[CoinLib] Profile save {symbol}: {e}")


def update_coin_stats(symbol: str, result: str, net_pnl: float,
                      r_multiple: float = 0.0, direction: str = "") -> None:
    """
    Kapanan trade sonrası coin istatistiklerini günceller.
    coin_configs tablosundaki config_json içine kazanç/kayıp sayaçları eklenir.
    """
    try:
        from database import get_conn
        with get_conn() as conn:
            row = conn.execute(
                "SELECT config_json FROM coin_configs WHERE coin=?", (symbol,)
            ).fetchone()
            data: dict = {}
            if row and row[0]:
                try:
                    data = json.loads(row[0])
                except Exception:
                    data = {}

            # İstatistik güncelle
            data["last_result"]  = result
            data["last_pnl"]     = round(float(net_pnl), 4)
            data["last_updated"] = datetime.now(timezone.utc).isoformat()
            data["total_trades"] = data.get("total_trades", 0) + 1
            if result == "WIN":
                data["wins"] = data.get("wins", 0) + 1
            else:
                data["losses"] = data.get("losses", 0) + 1
            total = data["total_trades"]
            wins  = data.get("wins", 0)
            data["win_rate"] = round(wins / total, 4) if total > 0 else 0.5
            # Coin score = 50 + (win_rate - 0.5) * 100 → 0-100
            data["coin_score"] = round(50 + (data["win_rate"] - 0.5) * 100, 1)
            data["avg_r"]      = round(
                (data.get("avg_r", 0) * (total - 1) + r_multiple) / total, 3
            )

            now_str = datetime.now(timezone.utc).isoformat()
            cur = conn.execute("""
                UPDATE coin_configs SET config_json=?, updated_at=? WHERE coin=?
            """, (json.dumps(data), now_str, symbol))
            if cur.rowcount == 0:
                conn.execute("""
                    INSERT INTO coin_configs (coin, config_json, updated_at)
                    VALUES (?, ?, ?)
                """, (symbol, json.dumps(data), now_str))
        logger.debug(f"[CoinLib] {symbol} stats updated: {result} wr={data.get('win_rate', 0):.2f}")
    except Exception as e:
        logger.warning(f"[CoinLib] update_coin_stats {symbol}: {e}")


def get_coin_score(symbol: str) -> float:
    """Coin skorunu DB'den getir. Yoksa nötr 50 döndür."""
    try:
        from database import get_conn
        with get_conn() as conn:
            row = conn.execute(
                "SELECT config_json FROM coin_configs WHERE coin=?", (symbol,)
            ).fetchone()
            if row and row[0]:
                data = json.loads(row[0])
                return float(data.get("coin_score", 50))
    except Exception:
        pass
    return 50.0


def get_coin_optimal_sl_mult(symbol: str) -> float:
    """Coin için optimal SL çarpanını getir."""
    try:
        from database import get_conn
        with get_conn() as conn:
            row = conn.execute(
                "SELECT config_json FROM coin_configs WHERE coin=?", (symbol,)
            ).fetchone()
            if row and row[0]:
                data = json.loads(row[0])
                return float(data.get("optimal_sl_mult", 1.8))
    except Exception:
        pass
    return 1.8


def get_active_universe_v2(min_score: float = 40.0) -> list:
    """Skoru min_score üstündeki aktif coinleri getir."""
    try:
        from database import get_conn
        with get_conn() as conn:
            # Primary key sütunu "coin" (not "symbol")
            rows = conn.execute(
                "SELECT coin, config_json FROM coin_configs"
            ).fetchall()
            result = []
            for sym, cfg in rows:
                try:
                    if cfg:
                        data = json.loads(cfg)
                        if data.get("coin_score", 50) >= min_score:
                            result.append(sym)
                    else:
                        result.append(sym)
                except Exception:
                    result.append(sym)
            return result
    except Exception:
        return []


def refresh_coin_universe(client, force: bool = False) -> list:
    """
    Coin evrenini yenile. Her 6 saatte bir otomatik çalışır.
    force=True ise zamanlama atlanır.
    """
    if not force:
        try:
            from database import get_conn
            with get_conn() as conn:
                row = conn.execute(
                    "SELECT value FROM system_state WHERE key='coin_universe_updated'"
                ).fetchone()
                if row:
                    last_update = datetime.fromisoformat(row[0])
                    if last_update.tzinfo is None:
                        last_update = last_update.replace(tzinfo=timezone.utc)
                    if (datetime.now(timezone.utc) - last_update).total_seconds() < 21600:
                        return get_active_universe_v2()
        except Exception:
            pass

    try:
        tickers = client.futures_ticker()
        from config import COIN_MIN_VOLUME_USDT, COIN_MIN_MOVE_PCT, COIN_UNIVERSE_LIMIT
        universe = build_symbol_universe(tickers,
                                         min_volume_usdt=COIN_MIN_VOLUME_USDT,
                                         min_move_pct=COIN_MIN_MOVE_PCT)
        ranked = rank_symbols_by_activity(universe)
        top_symbols = [t["symbol"] for t in ranked[:max(60, COIN_UNIVERSE_LIMIT)]]

        logger.info(f"[CoinLib] Universe refresh: {len(top_symbols)} coin")
        profiles = build_coin_profiles(client, top_symbols)

        try:
            from database import get_conn
            with get_conn() as conn:
                now_str = datetime.now(timezone.utc).isoformat()
                conn.execute("""
                    INSERT INTO system_state (key, value, updated_at)
                    VALUES ('coin_universe_updated', ?, datetime('now'))
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """, (now_str,))
        except Exception:
            pass

        good_coins = [s for s, p in profiles.items() if p.get("recommended", True)]
        logger.info(f"[CoinLib] {len(good_coins)}/{len(top_symbols)} coin önerildi")
        return good_coins

    except Exception as e:
        logger.error(f"[CoinLib] Universe refresh hatası: {e}")
        return get_active_universe_v2()
