"""
core/live_execution.py — AX Live Execution Engine
=================================================
Gerçek parayla Binance Futures üzerinden işlem yapar.
HFT standartlarında tasarlanmıştır:
- TP'ler gizlidir (sunucu tarafında Trailing Engine ile takip edilir).
- SL borsaya anında (hard stop) olarak yerleştirilir.
- Tüm miktar/fiyat hesaplamaları Binance symbol precision'larına göre yuvarlanır.
"""

from __future__ import annotations

import logging
import time
import math
import json
from typing import Optional, Dict, Any, Tuple
from datetime import datetime, timezone

try:
    from binance.client import Client
    from binance.exceptions import BinanceAPIException
except ImportError:
    Client = None
    BinanceAPIException = Exception

import config
import database
from core.data_layer import SignalData, TradeData
from telegram_delivery import TelegramDelivery

logger = logging.getLogger("ax.live_execution")

class LiveExecutionEngine:
    def __init__(self):
        self.telegram = TelegramDelivery()
        self.client = self._init_client()
        self.exchange_info = {}
        self._last_exchange_info_fetch = 0
        self._cached_balance = 0.0
        self._last_balance_fetch = 0
        if self.client:
            self._update_exchange_info()

    def _init_client(self) -> Optional[Any]:
        if not Client:
            logger.error("python-binance kurulu degil. Live Execution calisamaz.")
            return None
        
        if not config.BINANCE_API_KEY or not config.BINANCE_API_SECRET:
            logger.error("BINANCE_API_KEY veya BINANCE_API_SECRET eksik.")
            return None
            
        try:
            return Client(config.BINANCE_API_KEY, config.BINANCE_API_SECRET)
        except Exception as e:
            logger.error(f"Binance Client baslatilamadi: {e}")
            return None

    def _update_exchange_info(self):
        """Binance symbol bilgilerini (precision) onbellege alir."""
        now = time.time()
        # Her 6 saatte bir guncelle
        if now - self._last_exchange_info_fetch < 21600 and self.exchange_info:
            return
            
        try:
            info = self.client.futures_exchange_info()
            for s in info['symbols']:
                symbol = s['symbol']
                price_precision = s['pricePrecision']
                quantity_precision = s['quantityPrecision']
                tick_size = 0.001
                step_size = 0.001
                
                for f in s['filters']:
                    if f['filterType'] == 'PRICE_FILTER':
                        tick_size = float(f['tickSize'])
                    elif f['filterType'] == 'LOT_SIZE':
                        step_size = float(f['stepSize'])
                        
                self.exchange_info[symbol] = {
                    'price_precision': price_precision,
                    'quantity_precision': quantity_precision,
                    'tick_size': tick_size,
                    'step_size': step_size
                }
            self._last_exchange_info_fetch = now
            logger.info("Binance Exchange Info basariyla guncellendi.")
        except Exception as e:
            logger.error(f"Exchange info alinamadi: {e}")

    def _format_price(self, symbol: str, price: float) -> str:
        """Fiyati Binance tick_size kurallarina gore formatlar."""
        info = self.exchange_info.get(symbol)
        if not info:
            return f"{price:.4f}"
            
        tick_size = info['tick_size']
        precision = info['price_precision']
        
        formatted_price = round(price / tick_size) * tick_size
        return f"{formatted_price:.{precision}f}"

    def _format_quantity(self, symbol: str, qty: float) -> str:
        """Miktari Binance step_size kurallarina gore formatlar."""
        info = self.exchange_info.get(symbol)
        if not info:
            return f"{qty:.3f}"
            
        step_size = info['step_size']
        precision = info['quantity_precision']
        
        formatted_qty = math.floor(qty / step_size) * step_size
        return f"{formatted_qty:.{precision}f}"

    def _get_account_balance(self) -> float:
        """Gercek vadeli islemler (USDT) bakiyesini alir (cache'li)."""
        if not self.client:
            return 0.0
        now = time.time()
        # Her 30 saniyede bir guncelle
        if now - self._last_balance_fetch < 30 and self._cached_balance > 0.0:
            return self._cached_balance
            
        try:
            account = self.client.futures_account()
            self._cached_balance = float(account.get('totalWalletBalance', 0.0))
            self._last_balance_fetch = now
            return self._cached_balance
        except Exception as e:
            logger.error(f"Bakiye okunamadi: {e}")
            if self._cached_balance > 0.0:
                return self._cached_balance
            return 0.0

    def open_live_trade(self, signal: SignalData) -> Optional[int]:
        """
        Gercek emirleri borsaya gonderir ve DB'ye isler.
        Returns: DB trade_id veya None
        """
        if not self.client:
            logger.error("Binance Client baglantisi yok. Islem acilamaz.")
            return None
            
        if not config.is_live_trading_allowed():
            logger.error("Canli islem ayarlardan kapali veya DRY_RUN acik!")
            return None

        symbol = signal.symbol
        direction = signal.direction
        side = "BUY" if direction == "LONG" else "SELL"
        
        self._update_exchange_info()
        
        # 1. Bakiye kontrolu
        live_balance = self._get_account_balance()
        
        # Auto-Compounding mantigi
        if getattr(config, 'AUTO_COMPOUNDING', True):
            balance = live_balance
            logger.info(f"[Auto-Compound] Bakiye: {balance} USDT")
        else:
            balance = getattr(config, 'BASE_ACCOUNT_SIZE', 1000.0)
            logger.info(f"[Fixed-Size] Bakiye: {balance} USDT (Live: {live_balance})")
            
        if live_balance < 10.0:
            logger.error(f"Gercek bakiye yetersiz: {live_balance} USDT")
            return None

        # 2. Risk hesaplama (Approved dynamic risk from SignalData)
        risk_pct = getattr(signal, "risk_pct", 0) or getattr(signal, "risk_percent", 0)
        if risk_pct <= 0:
            base_risk = config.RISK_PCT
            score = getattr(signal, "final_score", 75.0) or 75.0
            dynamic_risk = base_risk * (score / 75.0)
            risk_pct = max(base_risk * 0.5, min(dynamic_risk, base_risk * 1.5))
        
        risk_usd = balance * (risk_pct / 100.0)
        
        current_price = signal.entry_price # Piyasaya en yakin deger
        sl_dist = abs(current_price - signal.stop_loss)
        
        if sl_dist == 0:
            logger.error(f"SL mesafesi 0 olamaz. Symbol: {symbol}")
            return None
            
        qty = risk_usd / sl_dist
        
        # Format Qty
        qty_str = self._format_quantity(symbol, qty)
        qty_float = float(qty_str)
        
        if qty_float <= 0:
            logger.error(f"Hesaplanan miktar borsanin min limitinden kucuk. Bakiye yetersiz olabilir.")
            return None
            
        leverage = int(getattr(signal, "leverage", 0) or getattr(signal, "leverage_suggestion", 0) or config.MAX_LEVERAGE)
        leverage = min(leverage, config.MAX_LEVERAGE)
        if leverage <= 0:
            leverage = 10
            
        margin_used = (qty_float * current_price) / leverage

        # 3. Kaldirac ve Margin Modu Ayarlama
        try:
            self.client.futures_change_leverage(symbol=symbol, leverage=leverage)
        except BinanceAPIException as e:
            logger.debug(f"Kaldirac ayari (zaten ayni olabilir): {e}")
            
        try:
            self.client.futures_change_margin_type(symbol=symbol, marginType='ISOLATED')
        except BinanceAPIException as e:
            if 'No need to change margin type' not in str(e):
                logger.warning(f"Margin tipi degistirilemedi: {e}")

        # Dinamik Take-Profit Oranları
        regime = "TRENDING"
        if signal.metadata and "market_regime" in signal.metadata:
            regime = signal.metadata.get("market_regime", "TRENDING")
        
        if regime == "CHOPPY":
            pct_tp1, pct_tp2, pct_runner = 0.70, 0.30, 0.0
        else:
            pct_tp1, pct_tp2, pct_runner = 0.30, 0.20, 0.50

        # Slippage Guard (Kayma Kalkanı)
        try:
            from core.market_data import get_cached_ticker
            cached_tick = get_cached_ticker(symbol)
            if cached_tick:
                bid = float(cached_tick.get('bid') or 0)
                ask = float(cached_tick.get('ask') or 0)
                if bid > 0 and ask > 0:
                    spread_pct = (ask - bid) / bid * 100
                    max_spread = getattr(config, "MAX_SPREAD_PCT", 0.15)
                    if spread_pct > max_spread:
                        logger.warning(
                            f"[Slippage Guard] {symbol} spread oranı çok yüksek: {spread_pct:.3f}% > {max_spread}%. "
                            "Emir gönderimi reddedildi."
                        )
                        return None
        except Exception as e:
            logger.debug(f"[Slippage Guard] Kontrol hatası: {e}")

        # 4. Borsaya Emir Gonderimi (Entry - Smart Limit/Market)
        # TODO: Şimdilik piyasayı kaçırmamak için MARKET atıyoruz (Slippage Chase eklenebilir)
        logger.info(f"[LIVE] {symbol} {side} MARKET Emri gonderiliyor. Qty: {qty_str}")
        try:
            order = self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type='MARKET',
                quantity=qty_str
            )
            # Gerceklesme fiyatini al
            entry_price = float(order.get('avgPrice', current_price))
            if entry_price == 0:
                entry_price = current_price
        except BinanceAPIException as e:
            logger.error(f"[LIVE REJECTED] Market entry Binance tarafindan reddedildi {symbol}: {e.message} (Code: {e.code})")
            return None
        except Exception as e:
            logger.error(f"[LIVE ERROR] Market entry basarisiz {symbol}: {e}")
            return None

        # 5. Borsaya Stop Loss (Hard Stop) Yerlestirme
        sl_side = "SELL" if direction == "LONG" else "BUY"
        sl_price_str = self._format_price(symbol, signal.stop_loss)
        
        try:
            sl_order = self.client.futures_create_order(
                symbol=symbol,
                side=sl_side,
                type='STOP_MARKET',
                stopPrice=sl_price_str,
                closePosition='true',
                timeInForce='GTC'
            )
            sl_order_id = sl_order.get('orderId')
        except Exception as e:
            logger.critical(f"[LIVE FATAL] {symbol} SL EMRİ GÖNDERİLEMEDİ! Manuel kontrol edin! Hata: {e}")
            sl_order_id = "FAILED"

        # 5.5 Borsaya Take Profit (Limit) Emirleri Yerleştirme
        tp1_order_id, tp2_order_id = "NONE", "NONE"
        try:
            if (getattr(signal, 'tp1', None) or 0) > 0:
                qty_tp1_str = self._format_quantity(symbol, qty_float * pct_tp1)
                if float(qty_tp1_str) > 0:
                    tp1_order = self.client.futures_create_order(
                        symbol=symbol, side=sl_side, type='LIMIT', 
                        price=self._format_price(symbol, signal.tp1), quantity=qty_tp1_str, reduceOnly='true', timeInForce='GTC'
                    )
                    tp1_order_id = tp1_order.get('orderId', "UNKNOWN")
                    
            if (getattr(signal, 'tp2', None) or 0) > 0:
                qty_tp2_str = self._format_quantity(symbol, qty_float * pct_tp2)
                if float(qty_tp2_str) > 0:
                    tp2_order = self.client.futures_create_order(
                        symbol=symbol, side=sl_side, type='LIMIT', 
                        price=self._format_price(symbol, signal.tp2), quantity=qty_tp2_str, reduceOnly='true', timeInForce='GTC'
                    )
                    tp2_order_id = tp2_order.get('orderId', "UNKNOWN")
        except Exception as e:
            logger.error(f"[LIVE ERROR] TP emirleri gonderilemedi: {e}")

        # 6. DB Kaydi
        trade = TradeData(
            symbol=symbol,
            side=direction,
            entry_price=entry_price,
            quantity=qty_float,
            qty_tp1=qty_float * pct_tp1,
            qty_tp2=qty_float * pct_tp2,
            qty_runner=qty_float * pct_runner,
            stop_loss=signal.stop_loss,
            tp1=signal.tp1,
            tp2=signal.tp2,
            tp3=signal.tp3,
            leverage=leverage,
            margin_used=margin_used,
            notional=qty_float * entry_price,
            risk_pct=risk_pct,
            risk_usd=risk_usd,
            status="OPEN",
            close_reason=f"LIVE_ENTRY (SL Order: {sl_order_id})",
            setup_quality=signal.setup_quality,
            final_score=signal.final_score
        )
        
        # State'i paper engine ile uyumlu kaydet
        from core.trailing_engine import TradeExitState
        initial_state = TradeExitState(current_sl=signal.stop_loss, highest_price=entry_price)
        
        # Eger Binance siparis id'leri gerekiyorsa metadata icine gomulebilir
        meta_dict = initial_state.to_dict()
        meta_dict['binance_entry_order_id'] = order.get('orderId')
        meta_dict['binance_sl_order_id'] = sl_order_id
        if signal.metadata:
            meta_dict.update(signal.metadata)
        
        trade_id = database.create_trade(trade, metadata=json.dumps(meta_dict))
        
        if trade_id is None:
            logger.error(f"[LIVE] Islem borsada acildi ama DB'ye yazilamadi: {symbol}")
            return None
            
        logger.info(f"[LIVE SUCCESS] Trade basariyla acildi: #{trade_id} {symbol} @ {entry_price}")
        
        return trade_id

    def execute_live_close(self, symbol: str, direction: str, close_qty: float) -> Tuple[bool, float]:
        """
        Borsaya (Market) cikis emri gonderir. (Partial veya Full close icin)
        """
        if not self.client:
            return False, 0.0
            
        close_side = "SELL" if direction == "LONG" else "BUY"
        qty_str = self._format_quantity(symbol, close_qty)
        
        try:
            logger.info(f"[LIVE] {symbol} {close_side} MARKET (Close) gonderiliyor. Qty: {qty_str}")
            order = self.client.futures_create_order(
                symbol=symbol,
                side=close_side,
                type='MARKET',
                quantity=qty_str,
                reduceOnly='true'
            )
            # Cikis fiyatini dondurebiliriz, basitlemek icin anlik fiyati tahmini dondurelim
            # Gerekirse fills icinden alabiliriz.
            return True, 0.0
        except Exception as e:
            logger.error(f"[LIVE ERROR] Close emri basarisiz: {e}")
            return False, 0.0

    def cancel_all_orders(self, symbol: str) -> bool:
        """Coin icin acikta kalan emirleri (or: Stop Loss) iptal eder."""
        if not self.client:
            return False
        try:
            self.client.futures_cancel_all_open_orders(symbol=symbol)
            return True
        except Exception as e:
            logger.error(f"[LIVE ERROR] Emir iptal edilemedi {symbol}: {e}")
            return False

    def update_live_sl(self, symbol: str, direction: str, new_sl: float) -> str:
        """Binance uzerindeki SL emrini gunceller. Onceki STOP emirlerini iptal eder."""
        if not self.client: return "FAILED"
        try:
            # Sadece STOP_MARKET emirlerini iptal edelim (TP limitleri iptal olmasin)
            open_orders = self.client.futures_get_open_orders(symbol=symbol)
            for order in open_orders:
                if order['type'] == 'STOP_MARKET':
                    self.client.futures_cancel_order(symbol=symbol, orderId=order['orderId'])
            
            # Yeni SL gonder
            sl_side = "SELL" if direction == "LONG" else "BUY"
            sl_price_str = self._format_price(symbol, new_sl)
            sl_order = self.client.futures_create_order(
                symbol=symbol,
                side=sl_side,
                type='STOP_MARKET',
                stopPrice=sl_price_str,
                closePosition='true',
                timeInForce='GTC'
            )
            return str(sl_order.get('orderId'))
        except Exception as e:
            logger.error(f"[LIVE ERROR] SL guncellenemedi {symbol}: {e}")
            return "FAILED"
