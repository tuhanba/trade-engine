"""
n8n_bridge.py — n8n Webhook Entegrasyonu
=========================================
Trade olaylarını n8n workflow'larına iletir.
N8N_WEBHOOK_URL env değişkeni tanımlı değilse sessizce geçer.
"""
import os
import logging
import threading
import requests
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

N8N_WEBHOOK_URL = os.getenv("N8N_WEBHOOK_URL", "")
N8N_TIMEOUT = 8  # saniye

def _post(payload: dict):
    """Arka planda n8n webhook'a POST atar."""
    if not N8N_WEBHOOK_URL:
        logger.debug("[n8n] N8N_WEBHOOK_URL tanımlı değil, atlanıyor")
        return
    try:
        resp = requests.post(
            N8N_WEBHOOK_URL,
            json=payload,
            timeout=N8N_TIMEOUT,
            headers={"Content-Type": "application/json"}
        )
        if resp.status_code not in (200, 201, 202):
            logger.warning(f"[n8n] HTTP {resp.status_code}: {resp.text[:200]}")
        else:
            logger.debug(f"[n8n] Webhook gönderildi: {payload.get('event', '?')}")
    except requests.exceptions.ConnectionError:
        logger.debug("[n8n] Bağlantı kurulamadı (n8n çalışmıyor olabilir)")
    except Exception as e:
        logger.warning(f"[n8n] Webhook hatası: {e}")

def _send_async(payload: dict):
    """Thread'de gönder - ana akışı bloklamaz."""
    t = threading.Thread(target=_post, args=(payload,), daemon=True)
    t.start()

def notify_trade_open(trade: dict):
    """Yeni trade açıldığında n8n'e bildir."""
    _send_async({
        "event":     "trade_open",
        "trade_id":  trade.get("id"),
        "symbol":    trade.get("symbol"),
        "direction": trade.get("direction"),
        "entry":     trade.get("entry"),
        "sl":        trade.get("sl"),
        "tp1":       trade.get("tp1"),
        "tp2":       trade.get("tp2"),
        "tp3":       trade.get("tp3"),
        "qty":       trade.get("qty"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

def notify_trade_close(trade: dict, pnl: float = 0, reason: str = ""):
    """Trade kapandığında n8n'e bildir."""
    _send_async({
        "event":     "trade_close",
        "trade_id":  trade.get("id"),
        "symbol":    trade.get("symbol"),
        "direction": trade.get("direction"),
        "entry":     trade.get("entry"),
        "close_price": trade.get("current_price"),
        "net_pnl":   pnl,
        "reason":    reason,
        "status":    trade.get("status"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

def notify_tp_hit(trade: dict, tp_level: int, pnl: float = 0):
    """TP hit olduğunda n8n'e bildir."""
    _send_async({
        "event":     f"tp{tp_level}_hit",
        "trade_id":  trade.get("id"),
        "symbol":    trade.get("symbol"),
        "direction": trade.get("direction"),
        "tp_level":  tp_level,
        "pnl":       pnl,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

def notify_sl_hit(trade: dict, pnl: float = 0):
    """SL hit olduğunda n8n'e bildir."""
    _send_async({
        "event":     "sl_hit",
        "trade_id":  trade.get("id"),
        "symbol":    trade.get("symbol"),
        "direction": trade.get("direction"),
        "pnl":       pnl,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

def notify_signal(signal: dict):
    """Yeni sinyal oluştuğunda n8n'e bildir."""
    _send_async({
        "event":     "signal",
        "symbol":    signal.get("symbol"),
        "direction": signal.get("direction"),
        "entry":     signal.get("entry"),
        "tp1":       signal.get("tp1"),
        "tp2":       signal.get("tp2"),
        "tp3":       signal.get("tp3"),
        "sl":        signal.get("sl"),
        "score":     signal.get("score"),
        "quality":   signal.get("setup_quality"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
