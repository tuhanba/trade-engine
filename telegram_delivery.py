"""
telegram_delivery.py – Telegram bildirim modülü.

Token/chat_id yoksa sadece log warning verir, crash olmaz.
Telegram API hatası botu durdurmaz.
"""

from __future__ import annotations

import logging
from typing import Any

import requests

import config

logger = logging.getLogger("ax.telegram")

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
_TIMEOUT = 10


class TelegramDelivery:
    """Güvenli Telegram bildirim gönderici."""

    def __init__(
        self,
        token: str = "",
        chat_id: str = "",
    ):
        self.token = token or config.TELEGRAM_BOT_TOKEN
        self.chat_id = chat_id or config.TELEGRAM_CHAT_ID

    # ── Durum ────────────────────────────────────────────────────

    def is_configured(self) -> bool:
        """Token ve chat_id tanımlı mı?"""
        return bool(self.token) and bool(self.chat_id)

    # ── Temel gönderim ───────────────────────────────────────────

    def send_message(self, text: str) -> bool:
        """
        Mesaj gönderir. Başarılıysa True döner.
        Yapılandırılmamışsa veya hata varsa False döner, crash olmaz.
        """
        if not self.is_configured():
            logger.warning("Telegram yapılandırılmamış – mesaj atlandı")
            return False

        url = _TELEGRAM_API.format(token=self.token)
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
        }

        try:
            resp = requests.post(url, json=payload, timeout=_TIMEOUT)
            if resp.status_code == 200:
                return True
            logger.warning(
                "Telegram yanıt hatası: %s – %s",
                resp.status_code,
                resp.text[:200],
            )
            return False
        except requests.RequestException as exc:
            logger.error("Telegram gönderim hatası: %s", exc)
            return False

    # ── Trade bildirimleri ───────────────────────────────────────

    def send_trade_open(self, trade: dict[str, Any]) -> bool:
        """Trade açılış mesajı gönderir."""
        text = (
            "📈 <b>Trade Açıldı</b>\n"
            f"Symbol : {trade.get('symbol', '?')}\n"
            f"Side   : {trade.get('side', '?')}\n"
            f"Entry  : {trade.get('entry_price', 0)}\n"
            f"SL     : {trade.get('stop_loss', 0)}\n"
            f"TP1    : {trade.get('tp1', 0)}\n"
            f"TP2    : {trade.get('tp2', 0)}\n"
            f"TP3    : {trade.get('tp3', 0)}\n"
            f"Lev    : {trade.get('leverage', 1)}x\n"
            f"Risk%  : {trade.get('risk_pct', 0)}%\n"
            f"RiskUSD: ${trade.get('risk_usd', 0)}\n"
            f"Margin : ${trade.get('margin_used', 0)}\n"
            f"Notional: ${trade.get('notional', 0)}"
        )
        return self.send_message(text)

    def send_trade_close(self, trade: dict[str, Any]) -> bool:
        """Trade kapanış mesajı gönderir."""
        pnl = trade.get("realized_pnl", 0)
        emoji = "✅" if pnl >= 0 else "❌"
        text = (
            f"{emoji} <b>Trade Kapandı</b>\n"
            f"Symbol : {trade.get('symbol', '?')}\n"
            f"Side   : {trade.get('side', '?')}\n"
            f"Exit   : {trade.get('exit_price', 0)}\n"
            f"PnL    : ${pnl}\n"
            f"Reason : {trade.get('close_reason', '')}"
        )
        return self.send_message(text)

    def send_error(self, title: str, error: Any) -> bool:
        """Hata bildirimi gönderir."""
        text = f"⚠️ <b>{title}</b>\n{str(error)[:500]}"
        return self.send_message(text)
