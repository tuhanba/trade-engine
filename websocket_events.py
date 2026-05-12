"""
websocket_events.py — Realtime WebSocket Event Emitter
========================================================

Dashboard'a gerçek zamanlı olayları gönderir.
Kullanım: app.py'da socketio instance'ı ile birlikte çalışır.

Events:
- live_update: Açık pozisyon güncellemesi
- pnl_update: PnL değişimi
- trade_closed: Trade kapatıldı
- signal_generated: Yeni sinyal
- dashboard_refresh: Tam dashboard yenilemesi
"""

import logging
from datetime import datetime, timezone
from typing import Dict, Any

logger = logging.getLogger(__name__)


class WebSocketEventManager:
    """Merkezi WebSocket event yöneticisi"""
    
    def __init__(self, socketio):
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
    
    def broadcast_live_update(self, positions: list):
        """Açık pozisyonları broadcast et"""
        try:
            self.socketio.emit('live_update', {
                'positions': positions,
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'count': len(positions),
            }, broadcast=True)
            logger.debug(f"[WebSocket] Live update gönderildi: {len(positions)} pozisyon")
        except Exception as e:
            logger.error(f"[WebSocket] Live update hatası: {e}")
    
    def broadcast_pnl_update(self, balance: float, unrealized_pnl: float, realized_pnl: float):
        """PnL güncellemesi broadcast et"""
        try:
            self.socketio.emit('pnl_update', {
                'balance': round(balance, 4),
                'unrealized_pnl': round(unrealized_pnl, 6),
                'realized_pnl': round(realized_pnl, 6),
                'total_pnl': round(unrealized_pnl + realized_pnl, 6),
                'timestamp': datetime.now(timezone.utc).isoformat(),
            }, broadcast=True)
            logger.debug(f"[WebSocket] PnL update gönderildi: {balance:.2f}$")
        except Exception as e:
            logger.error(f"[WebSocket] PnL update hatası: {e}")
    
    def broadcast_trade_closed(self, symbol: str, direction: str, pnl: float, status: str):
        """Trade kapatıldığında broadcast et"""
        try:
            self.socketio.emit('trade_closed', {
                'symbol': symbol,
                'direction': direction,
                'pnl': round(pnl, 6),
                'status': status,
                'timestamp': datetime.now(timezone.utc).isoformat(),
            }, broadcast=True)
            logger.debug(f"[WebSocket] Trade kapatıldı: {symbol} {direction} {pnl:.2f}$")
        except Exception as e:
            logger.error(f"[WebSocket] Trade closed hatası: {e}")
    
    def broadcast_signal_generated(self, symbol: str, direction: str, quality: str, score: float):
        """Yeni sinyal oluşturulduğunda broadcast et"""
        try:
            self.socketio.emit('signal_generated', {
                'symbol': symbol,
                'direction': direction,
                'quality': quality,
                'score': round(score, 4),
                'timestamp': datetime.now(timezone.utc).isoformat(),
            }, broadcast=True)
            logger.debug(f"[WebSocket] Sinyal: {symbol} {quality} {score:.2f}")
        except Exception as e:
            logger.error(f"[WebSocket] Signal generated hatası: {e}")
    
    def broadcast_dashboard_refresh(self):
        """Tam dashboard yenilemesi iste"""
        try:
            self.socketio.emit('dashboard_refresh', {
                'timestamp': datetime.now(timezone.utc).isoformat(),
            }, broadcast=True)
            logger.debug("[WebSocket] Dashboard refresh istendi")
        except Exception as e:
            logger.error(f"[WebSocket] Dashboard refresh hatası: {e}")
    
    def send_to_client(self, sid: str, event: str, data: Dict[str, Any]):
        """Belirli bir istemciye mesaj gönder"""
        try:
            self.socketio.emit(event, data, to=sid)
            logger.debug(f"[WebSocket] Mesaj gönderildi {sid}: {event}")
        except Exception as e:
            logger.error(f"[WebSocket] Send to client hatası: {e}")


# Global instance (app.py'da başlatılır)
event_manager: WebSocketEventManager = None


def initialize_websocket_events(socketio):
    """WebSocket event manager'ı başlat"""
    global event_manager
    event_manager = WebSocketEventManager(socketio)
    
    @socketio.on('connect')
    def on_connect():
        from flask import request
        sid = request.sid
        event_manager.register_client(sid)
        logger.info(f"[WebSocket] Bağlantı kuruldu: {sid}")
    
    @socketio.on('disconnect')
    def on_disconnect():
        from flask import request
        sid = request.sid
        event_manager.unregister_client(sid)
        logger.info(f"[WebSocket] Bağlantı koptu: {sid}")
    
    @socketio.on('dashboard_ready')
    def on_dashboard_ready():
        from flask import request
        logger.info(f"[WebSocket] Dashboard hazır: {request.sid}")
    
    @socketio.on('heartbeat')
    def on_heartbeat():
        from flask import request
        logger.debug(f"[WebSocket] Heartbeat: {request.sid}")
    
    return event_manager
