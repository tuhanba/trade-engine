"""
websocket_events.py — Realtime WebSocket Event Emitter v6.0
========================================================

Dashboard'a gerçek zamanlı olayları gönderir.
Farklı process'lerde çalışan Bot ve Flask süreçlerini birbirine
bağlamak için Redis Pub/Sub köprüsü kullanılır.
"""

import logging
import json
import threading
from datetime import datetime, timezone
from typing import Dict, Any

logger = logging.getLogger(__name__)

# SocketIO plugin check
SOCKETIO_AVAILABLE = True

_redis_client = None

def get_redis_client():
    """Redis bağlantısını döner."""
    global _redis_client
    if _redis_client is None:
        try:
            import redis
            import config
            _redis_client = redis.Redis(
                host=getattr(config, "REDIS_HOST", "127.0.0.1"),
                port=getattr(config, "REDIS_PORT", 6379),
                db=getattr(config, "REDIS_DB", 0),
                password=getattr(config, "REDIS_PASSWORD", None) or None,
                socket_connect_timeout=2,
                socket_timeout=2,
                decode_responses=True,
            )
            _redis_client.ping()
        except Exception as e:
            logger.debug(f"[WebSocket] Redis connection failed (fallback to local/no-op): {e}")
            _redis_client = False
    return _redis_client if _redis_client is not False else None


class WebSocketEventManager:
    """Merkezi WebSocket event yöneticisi (Redis Pub/Sub destekli)"""
    
    def __init__(self, socketio=None):
        self.socketio = socketio
        self.connected_clients = set()
    
    def register_client(self, sid):
        """İstemci bağlandığında kayıt et"""
        self.connected_clients.add(sid)
        logger.info(f"[WebSocket] İstemci bağlandı: {sid}")
    
    def unregister_client(self, sid):
        """İstemci ayrıldığında kayıt sil"""
        self.connected_clients.discard(sid)
        logger.info(f"[WebSocket] İstemci ayrıldı: {sid}")

    def _publish_event(self, event: str, payload: dict):
        """Event'i yerel socketio veya Redis pub/sub üzerinden yayınlar."""
        # 1. Eğer Flask process'indeysek (socketio aktif) doğrudan emit et:
        if self.socketio:
            try:
                self.socketio.emit(event, payload, broadcast=True)
                logger.debug(f"[WebSocket] Local emit: {event}")
                return
            except Exception as e:
                logger.error(f"[WebSocket] Local emit hatası: {e}")

        # 2. Bot process'indeysek Redis Pub/Sub üzerinden publish et:
        r = get_redis_client()
        if r:
            try:
                msg = json.dumps({"event": event, "payload": payload})
                r.publish("ax_websocket_events", msg)
                logger.debug(f"[WebSocket] Redis publish: {event}")
            except Exception as e:
                logger.error(f"[WebSocket] Redis publish hatası: {e}")
        else:
            logger.debug(f"[WebSocket] Event yutuldu (No SocketIO & No Redis): {event}")
    
    def broadcast_live_update(self, positions: list):
        """Açık pozisyonları broadcast et"""
        self._publish_event('live_update', {
            'positions': positions,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'count': len(positions),
        })
    
    def broadcast_pnl_update(self, balance: float, unrealized_pnl: float, realized_pnl: float):
        """PnL güncellemesi broadcast et"""
        self._publish_event('pnl_update', {
            'balance': round(balance, 4),
            'unrealized_pnl': round(unrealized_pnl, 6),
            'realized_pnl': round(realized_pnl, 6),
            'total_pnl': round(unrealized_pnl + realized_pnl, 6),
            'timestamp': datetime.now(timezone.utc).isoformat(),
        })
    
    def broadcast_trade_closed(self, symbol: str, direction: str, pnl: float, status: str):
        """Trade kapatıldığında broadcast et"""
        self._publish_event('trade_closed', {
            'symbol': symbol,
            'direction': direction,
            'pnl': round(pnl, 6),
            'status': status,
            'timestamp': datetime.now(timezone.utc).isoformat(),
        })
    
    def broadcast_signal_generated(self, symbol: str, direction: str, quality: str, score: float):
        """Yeni sinyal oluşturulduğunda broadcast et"""
        self._publish_event('signal_generated', {
            'symbol': symbol,
            'direction': direction,
            'quality': quality,
            'score': round(score, 4),
            'timestamp': datetime.now(timezone.utc).isoformat(),
        })
    
    def broadcast_dashboard_refresh(self):
        """Tam dashboard yenilemesi iste"""
        self._publish_event('dashboard_refresh', {
            'timestamp': datetime.now(timezone.utc).isoformat(),
        })

    def broadcast_signal_rejected(self, symbol: str, direction: str, reason: str):
        """Sinyal reddedildiğinde broadcast et"""
        self._publish_event('signal_rejected', {
            'symbol': symbol,
            'direction': direction,
            'reason': reason,
            'timestamp': datetime.now(timezone.utc).isoformat(),
        })

    def broadcast_trailing_stop_updated(self, symbol: str, trade_id: int, old_sl: float, new_sl: float, current_price: float):
        """Trailing stop güncellendiğinde broadcast et"""
        self._publish_event('trailing_stop_updated', {
            'symbol': symbol,
            'trade_id': trade_id,
            'old_sl': round(old_sl, 6),
            'new_sl': round(new_sl, 6),
            'current_price': round(current_price, 6),
            'timestamp': datetime.now(timezone.utc).isoformat(),
        })

    def broadcast_limit_chase_progress(self, symbol: str, side: str, status: str, filled_qty: float, total_qty: float, price: float):
        """Limit chase aşamalarını ve durumunu broadcast et"""
        self._publish_event('limit_chase_progress', {
            'symbol': symbol,
            'side': side,
            'status': status,
            'filled_qty': round(filled_qty, 6),
            'total_qty': round(total_qty, 6),
            'price': round(price, 6),
            'timestamp': datetime.now(timezone.utc).isoformat(),
        })

    def broadcast_agent_votes(self, symbol: str, direction: str, decision: str, votes: dict, adjusted_score: float, confidence: float, reason: str):
        """Agent oylamalarını ve konsensüs sonucunu broadcast et"""
        self._publish_event('agent_votes', {
            'symbol': symbol,
            'direction': direction,
            'decision': decision,
            'votes': votes,
            'adjusted_score': round(adjusted_score, 2),
            'confidence': round(confidence, 2),
            'reason': reason,
            'timestamp': datetime.now(timezone.utc).isoformat(),
        })

    
    def send_to_client(self, sid: str, event: str, data: Dict[str, Any]):
        """Belirli bir istemciye mesaj gönder (Sadece Flask/Local)"""
        if self.socketio:
            try:
                self.socketio.emit(event, data, to=sid)
                logger.debug(f"[WebSocket] Mesaj gönderildi {sid}: {event}")
            except Exception as e:
                logger.error(f"[WebSocket] Send to client hatası: {e}")


# Global instance (app.py veya execution_engine.py import eder)
event_manager: WebSocketEventManager = WebSocketEventManager()


def start_redis_listener(socketio):
    """Flask process'inde Redis Pub/Sub kanalını dinleyen thread'i başlatır."""
    def listener():
        r = get_redis_client()
        if not r:
            logger.warning("[WebSocket] Redis Pub/Sub dinleyicisi başlatılamadı (Redis bağlantısı yok)")
            return
            
        pubsub = r.pubsub()
        pubsub.subscribe("ax_websocket_events")
        logger.info("[WebSocket] Redis Pub/Sub dinleyicisi aktif (ax_websocket_events dinleniyor)")
        
        for message in pubsub.listen():
            if message['type'] == 'message':
                try:
                    data = json.loads(message['data'])
                    event_name = data.get("event")
                    payload = data.get("payload")
                    if event_name and payload:
                        # Flask SocketIO kullanarak tüm bağlı browser'lara ilet
                        socketio.emit(event_name, payload, broadcast=True)
                        logger.debug(f"[WebSocket] Redis'ten gelen event broadcast edildi: {event_name}")
                except Exception as e:
                    logger.error(f"[WebSocket] Redis listener mesaj işleme hatası: {e}")
                    
    thread = threading.Thread(target=listener, daemon=True, name="redis-ws-listener")
    thread.start()


def initialize_websocket_events(socketio):
    """WebSocket event manager'ı Flask process'inde başlat ve Redis dinleyicisini çalıştır"""
    global event_manager
    event_manager.socketio = socketio
    
    @socketio.on('connect')
    def on_connect():
        from flask import request
        sid = request.sid
        event_manager.register_client(sid)
        logger.info(f"[WebSocket] İstemci bağlandı (Local): {sid}")
    
    @socketio.on('disconnect')
    def on_disconnect():
        from flask import request
        sid = request.sid
        event_manager.unregister_client(sid)
        logger.info(f"[WebSocket] İstemci ayrıldı (Local): {sid}")
    
    @socketio.on('dashboard_ready')
    def on_dashboard_ready():
        from flask import request
        logger.info(f"[WebSocket] Dashboard hazır (Local): {request.sid}")
    
    @socketio.on('heartbeat')
    def on_heartbeat():
        from flask import request
        logger.debug(f"[WebSocket] Heartbeat (Local): {request.sid}")
    
    # Redis dinleyicisini başlat
    start_redis_listener(socketio)
    
    return event_manager

