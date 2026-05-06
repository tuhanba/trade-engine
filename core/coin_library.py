"""
core/coin_library.py — AX Coin Kütüphanesi v4.8
===============================================
Aşama 8: Tüm USDT-M Futures coinlerini çeker ve filtreler.
"""
import logging
import time

logger = logging.getLogger(__name__)

class CoinLibrary:
    def __init__(self, client):
        self.client = client
        self.coins = {}
        self.last_update = 0

    def update_coin_info(self):
        """
        Binance'den tüm USDT-M Futures coinlerini çeker.
        """
        try:
            info = self.client.futures_exchange_info()
            new_coins = {}
            for s in info['symbols']:
                if s['quoteAsset'] == 'USDT' and s['status'] == 'TRADING':
                    symbol = s['symbol']
                    filters = {f['filterType']: f for f in s['filters']}
                    
                    new_coins[symbol] = {
                        'min_qty': float(filters['LOT_SIZE']['minQty']),
                        'step_size': float(filters['LOT_SIZE']['stepSize']),
                        'tick_size': float(filters['PRICE_FILTER']['tickSize']),
                        'min_notional': float(filters.get('MIN_NOTIONAL', {}).get('notional', 5.0)),
                        'status': s['status']
                    }
            
            self.coins = new_coins
            self.last_update = time.time()
            logger.info(f"Coin Library güncellendi: {len(self.coins)} aktif coin.")
        except Exception as e:
            logger.error(f"Coin Library güncelleme hatası: {e}")

    def get_active_coins(self):
        if time.time() - self.last_update > 3600: # Saatlik güncelle
            self.update_coin_info()
        return list(self.coins.keys())

    def get_coin_filters(self, symbol):
        return self.coins.get(symbol)
