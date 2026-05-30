"""
core/recovery_service.py – State Recovery & Hata Toleransı
==========================================================
Bot yeniden başlatıldığında, açık olan işlemleri (DB'den) bulur, 
borsadaki gerçek pozisyonlarla eşleştirir (Reconciliation) ve
eğer borsa tarafında trade kapanmışsa veritabanını senkronize eder.
"""

import logging
import ccxt
import config

from database import get_open_trades, get_connection

logger = logging.getLogger("ax.recovery")

class RecoveryService:
    def __init__(self):
        self.mode = config.EXECUTION_MODE
        self.exchange = None
        if self.mode == "live":
            self.exchange = ccxt.binance({
                'apiKey': config.BINANCE_API_KEY,
                'secret': config.BINANCE_API_SECRET,
                'enableRateLimit': True,
                'options': {
                    'defaultType': 'future'
                }
            })

    async def perform_state_recovery(self):
        """Sistem başlatıldığında kurtarma işlemini çalıştırır."""
        logger.info("=== State Recovery (Durum Kurtarma) Başlatılıyor ===")
        
        open_trades = get_open_trades()
        if not open_trades:
            logger.info("Kurtarılacak açık trade bulunamadı. Sistem temiz.")
            return

        logger.info(f"DB'de {len(open_trades)} adet açık trade bulundu. Senkronize ediliyor...")

        if self.mode == "paper":
            logger.info("Paper modunda olunduğu için sadece DB durumu yüklendi. (Borsa eşleşmesi atlandı)")
            # Paper modda zaten monitor_trades döngüsü anlık fiyata bakıp eksikleri kapatır.
            return

        # LIVE Modu - Borsa Senkronizasyonu (Reconciliation)
        try:
            positions = self.exchange.fetch_positions()
            active_symbols_on_exchange = {
                p['symbol'].replace('/', '').replace(':USDT', '') 
                for p in positions if float(p['contracts']) > 0
            }
            
            conn = get_connection()
            for trade in open_trades:
                symbol = trade['symbol']
                trade_id = trade['id']
                
                # Eğer veritabanında açık ama borsada yoksa, borsa tarafında stop/tp olmuştur.
                if symbol not in active_symbols_on_exchange:
                    logger.warning(f"Reconciliation: #{trade_id} {symbol} borsada kapalı ama DB'de açık! DB güncelleniyor (State Recovery)...")
                    
                    # Güncel fiyatı alıp trade'i kapatalım
                    ticker = self.exchange.fetch_ticker(symbol.replace("USDT", "/USDT:USDT"))
                    close_price = float(ticker['last'])
                    
                    # Trade'i status='closed', reason='recovery_sync' olarak kaydet
                    # Basit sync update
                    conn.execute(
                        "UPDATE trades SET status='closed', close_reason='recovery_sync', close_price=?, last_update=datetime('now') WHERE id=?", 
                        (close_price, trade_id)
                    )
            
            conn.commit()
            conn.close()
            logger.info("=== State Recovery Tamamlandı ===")
            
        except Exception as e:
            logger.error(f"State recovery başarısız oldu (Live Mode): {e}")
