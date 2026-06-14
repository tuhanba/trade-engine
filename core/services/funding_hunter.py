"""
core/services/funding_hunter.py — Funding-Rate Avcısı (Faz 6.7)
================================================================
Aşırı funding rate'lerde mean-reversion sinyali üreten BAĞIMSIZ strateji servisi.

Mantık: Aşırı POZİTİF funding = long'lar kalabalık (long'lar short'lara ödüyor)
→ mean-reversion SHORT eğilimi. Aşırı NEGATİF funding = short'lar kalabalık
→ mean-reversion LONG eğilimi.

NEDEN: Pipeline'a yalnız SCANNED event'iyle aday ENJEKTE eder — trend/trigger/
AI/risk servisleri tüm filtrelerini uygular, hiçbir guard bypass edilmez.
Varsayılan KAPALI (FUNDING_HUNTER_ENABLED). Mevcut akışı bozmaz.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import config

logger = logging.getLogger("ax.services.funding_hunter")

_BINANCE_PREMIUM_URL = "https://fapi.binance.com/fapi/v1/premiumIndex"


def detect_extreme_funding(rates: list[dict], threshold: float,
                           max_signals: int = 3,
                           open_symbols: Optional[set] = None) -> list[dict]:
    """Funding listesinden aşırı funding adaylarını mean-reversion bias ile süzer.

    rates: [{"symbol": "BTCUSDT", "lastFundingRate": "0.0012"}, ...]
    Returns: en aşırıdan başlayarak max_signals aday (SCANNED payload uyumlu).
    Pure fonksiyon — ağ erişimi yok, test edilebilir.
    """
    open_symbols = open_symbols or set()
    candidates = []
    for r in rates:
        sym = r.get("symbol", "")
        if not sym.endswith("USDT") or sym in open_symbols:
            continue
        try:
            fr = float(r.get("lastFundingRate"))
        except (TypeError, ValueError):
            continue
        if abs(fr) < threshold:
            continue
        # Mean-reversion: pozitif funding (long kalabalık) → SHORT; negatif → LONG
        bias = "SHORT" if fr > 0 else "LONG"
        candidates.append({
            "symbol": sym,
            "status": "Eligible",
            "source": "funding_hunter",
            "funding_rate": fr,
            "funding_bias": bias,
            # tradeability: aşırılık ne kadar büyükse o kadar yüksek (bilgi amaçlı)
            "tradeability_score": round(min(10.0, abs(fr) / threshold * 2.0), 2),
        })
    # En aşırı funding'den başlayarak sırala, max_signals ile sınırla
    candidates.sort(key=lambda c: abs(c["funding_rate"]), reverse=True)
    return candidates[:max_signals]


class FundingHunterService:
    """Aşırı funding tarayıp pipeline'a SCANNED adayı enjekte eden servis."""

    def __init__(self):
        self.interval = int(getattr(config, "FUNDING_HUNTER_INTERVAL", 900))
        self.threshold = float(getattr(config, "FUNDING_HUNTER_THRESHOLD", 0.0008))
        self.max_signals = int(getattr(config, "FUNDING_HUNTER_MAX_SIGNALS", 3))
        self._running = False

    def _fetch_funding_rates(self) -> list[dict]:
        """Binance public premiumIndex — tüm sembollerin funding rate'i."""
        import requests
        try:
            resp = requests.get(_BINANCE_PREMIUM_URL, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else [data]
        except Exception as e:
            logger.warning("[FundingHunter] funding rate alınamadı: %s", e)
            return []

    async def _scan_once(self):
        from core.event_bus import event_bus
        from core.event_types import Event, EventType
        from database import get_open_trades

        rates = await asyncio.to_thread(self._fetch_funding_rates)
        if not rates:
            return 0
        open_trades = await asyncio.to_thread(get_open_trades)
        open_symbols = {t["symbol"] for t in open_trades}

        candidates = detect_extreme_funding(rates, self.threshold, self.max_signals, open_symbols)
        for c in candidates:
            # NEDEN: SCANNED → pipeline aday olarak alır, kendi filtrelerini uygular.
            await event_bus.publish(Event(type=EventType.SCANNED, payload=c))
            try:
                from database import save_signal_event
                await asyncio.to_thread(
                    save_signal_event, None, "SCANNED",
                    symbol=c["symbol"],
                    reason=f"funding_hunter {c['funding_bias']} (fr={c['funding_rate']:.4%})",
                )
            except Exception:
                pass
        if candidates:
            logger.info("[FundingHunter] %d aşırı funding adayı enjekte edildi: %s",
                        len(candidates), [c["symbol"] for c in candidates])
        return len(candidates)

    async def start_background_task(self):
        """Servisi başlatır (config kapalıysa hiç çalışmaz)."""
        if not getattr(config, "FUNDING_HUNTER_ENABLED", False):
            logger.info("[FundingHunter] devre dışı (FUNDING_HUNTER_ENABLED=False) — başlatılmadı.")
            return
        self._running = True
        logger.info("[FundingHunter] Başladı (eşik=%.4f, periyot=%ds)", self.threshold, self.interval)
        # Açılışta kısa gecikme — diğer servisler otursun
        await asyncio.sleep(60)
        while self._running:
            try:
                await self._scan_once()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("[FundingHunter] döngü hatası: %s", e)
            await asyncio.sleep(self.interval)

    def stop(self):
        self._running = False


# Singleton — engine import edip start_background_task çağırır
funding_hunter = FundingHunterService()
