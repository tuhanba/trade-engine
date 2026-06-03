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

    def _execute_chase_limit_order(self, symbol: str, side: str, qty_str: str, entry_price: float, max_chase_pct: float = 0.15) -> Optional[Dict[str, Any]]:
        """
        Slippage-Reducing Chase Limit Order:
        Submits an initial LIMIT order, chases it dynamically, and falls back to MARKET order if needed.
        """
        if not self.client:
            return None

        total_qty = float(qty_str)
        remaining_qty = total_qty
        max_duration = 3.0
        tick_interval = 0.25 # 250ms
        
        # Calculate chase limits
        if side == "BUY":
            limit_bound = entry_price * (1 + max_chase_pct / 100.0)
        else:
            limit_bound = entry_price * (1 - max_chase_pct / 100.0)

        logger.info(f"[Limit Chase] Symbol: {symbol}, Side: {side}, Qty: {qty_str}, Entry: {entry_price}, Limit Bound: {limit_bound}, Max Chase Pct: {max_chase_pct}%")

        order_ids = []
        filled_qty = 0.0
        cum_quote = 0.0 # total spend to calculate average price

        def emit_progress(status, price):
            try:
                from websocket_events import event_manager
                if event_manager:
                    event_manager.broadcast_limit_chase_progress(
                        symbol=symbol,
                        side=side,
                        status=status,
                        filled_qty=filled_qty,
                        total_qty=total_qty,
                        price=price
                    )
            except Exception as _e:
                logger.debug(f"[Limit Chase WS] Emit progress error: {_e}")

        emit_progress("STARTED", entry_price)
        start_time = time.time()

        while (time.time() - start_time) < max_duration and remaining_qty > 0:
            # 1. Fetch current orderbook
            try:
                ob = self.client.futures_order_book(symbol=symbol, limit=5)
                bids = ob.get('bids', [])
                asks = ob.get('asks', [])
                if not bids or not asks:
                    raise ValueError("Bos order book")
                
                best_bid = float(bids[0][0])
                best_ask = float(asks[0][0])
            except Exception as e:
                logger.warning(f"[Limit Chase] Ticker/Orderbook alinamadi: {e}. Fallback to entry_price.")
                best_bid = entry_price
                best_ask = entry_price

            # Determine limit price to place (Spread-Adaptive)
            spread_pct = (best_ask - best_bid) / best_bid * 100.0 if best_bid > 0 else 0.0
            if side == "BUY":
                # For buy, we place at best_bid to try to be maker, capped at limit_bound
                if spread_pct > 0.05:
                    target_price = min((best_bid + best_ask) / 2.0, limit_bound)
                else:
                    target_price = min(best_bid, limit_bound)
            else:
                # For sell, we place at best_ask or capped at limit_bound
                if spread_pct > 0.05:
                    target_price = max((best_bid + best_ask) / 2.0, limit_bound)
                else:
                    target_price = max(best_ask, limit_bound)

            # Check if price has already crossed the limit bound
            if side == "BUY" and best_ask > limit_bound:
                logger.warning(f"[Limit Chase] Fiyat siniri asti (Ask: {best_ask} > Bound: {limit_bound}). Market emrine geciliyor.")
                emit_progress("MARKET_FALLBACK", limit_bound)
                break
            elif side == "SELL" and best_bid < limit_bound:
                logger.warning(f"[Limit Chase] Fiyat siniri asti (Bid: {best_bid} < Bound: {limit_bound}). Market emrine geciliyor.")
                emit_progress("MARKET_FALLBACK", limit_bound)
                break

            price_str = self._format_price(symbol, target_price)
            current_qty_str = self._format_quantity(symbol, remaining_qty)
            if float(current_qty_str) <= 0:
                break

            logger.info(f"[Limit Chase] Limit emir yerlestiriliyor: Price={price_str}, Qty={current_qty_str}")
            try:
                order = self.client.futures_create_order(
                    symbol=symbol,
                    side=side,
                    type='LIMIT',
                    price=price_str,
                    quantity=current_qty_str,
                    timeInForce='GTC'
                )
                current_order_id = order.get('orderId')
                order_ids.append(current_order_id)
                emit_progress("CHASING", target_price)
            except Exception as e:
                logger.error(f"[Limit Chase] Limit emir yerlestirme basarisiz: {e}")
                break

            # Wait 250ms
            time.sleep(tick_interval)

            # Query order status
            try:
                status_res = self.client.futures_get_order(symbol=symbol, orderId=current_order_id)
                status = status_res.get('status')
                exec_qty = float(status_res.get('executedQty', 0.0))
                avg_price = float(status_res.get('avgPrice', 0.0))
                if avg_price == 0:
                    avg_price = float(status_res.get('price', 0.0))

                logger.info(f"[Limit Chase] Status: {status}, Executed: {exec_qty}/{current_qty_str}")

                if status == 'FILLED':
                    filled_qty += exec_qty
                    cum_quote += exec_qty * avg_price
                    remaining_qty = total_qty - filled_qty
                    emit_progress("CHASING", avg_price)
                    break
                elif status in ('PARTIALLY_FILLED', 'NEW'):
                    # Cancel order
                    try:
                        self.client.futures_cancel_order(symbol=symbol, orderId=current_order_id)
                    except Exception as ce:
                        logger.debug(f"[Limit Chase] Cancel hatasi (zaten dolmus olabilir): {ce}")

                    # Fetch final status after cancel
                    final_res = self.client.futures_get_order(symbol=symbol, orderId=current_order_id)
                    final_exec_qty = float(final_res.get('executedQty', 0.0))
                    final_avg_price = float(final_res.get('avgPrice', 0.0))
                    if final_avg_price == 0:
                        final_avg_price = target_price

                    # Calculate new fill
                    new_fill = final_exec_qty
                    filled_qty += new_fill
                    cum_quote += new_fill * final_avg_price
                    remaining_qty = total_qty - filled_qty
                    logger.info(f"[Limit Chase] Iptal sonrasi gerceklesen: {final_exec_qty}, Kalan: {remaining_qty}")
                    emit_progress("CHASING", final_avg_price)
                else:
                    exec_qty = float(status_res.get('executedQty', 0.0))
                    filled_qty += exec_qty
                    cum_quote += exec_qty * avg_price
                    remaining_qty = total_qty - filled_qty
                    emit_progress("CHASING", avg_price or target_price)
            except Exception as e:
                logger.error(f"[Limit Chase] Emir sorgulama/iptal hatasi: {e}")

        # Fallback to MARKET order for remaining qty if any
        if remaining_qty > 0:
            rem_qty_str = self._format_quantity(symbol, remaining_qty)
            if float(rem_qty_str) > 0:
                logger.info(f"[Limit Chase] Kalan miktar icin MARKET emri gonderiliyor: {rem_qty_str}")
                emit_progress("MARKET_FALLBACK", entry_price)
                try:
                    m_order = self.client.futures_create_order(
                        symbol=symbol,
                        side=side,
                        type='MARKET',
                        quantity=rem_qty_str
                    )
                    m_exec_qty = float(m_order.get('executedQty', remaining_qty))
                    m_avg_price = float(m_order.get('avgPrice', 0.0))
                    if m_avg_price == 0:
                        m_avg_price = entry_price
                    filled_qty += m_exec_qty
                    cum_quote += m_exec_qty * m_avg_price
                    remaining_qty = total_qty - filled_qty
                    if 'orderId' in m_order:
                        order_ids.append(m_order['orderId'])
                except Exception as e:
                    logger.error(f"[Limit Chase] Market fallback basarisiz: {e}")

        if filled_qty > 0:
            final_avg_price = cum_quote / filled_qty
            emit_progress("COMPLETED", final_avg_price)
            return {
                'avgPrice': final_avg_price,
                'executedQty': filled_qty,
                'orderId': order_ids[-1] if order_ids else None,
                'orderIds': order_ids
            }
        else:
            emit_progress("FAILED", entry_price)
            return None

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
            balance = min(getattr(config, 'BASE_ACCOUNT_SIZE', 1000.0), live_balance)
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

        # Binance Live Available Margin check
        try:
            account = self.client.futures_account()
            free_margin_val = account.get('availableBalance')
            if free_margin_val is not None:
                free_margin = float(free_margin_val)
                if margin_used > free_margin:
                    logger.error(f"[Live] Yetersiz kullanılabilir bakiye (Free Margin): Gerekli={margin_used:.2f} USDT, Mevcut={free_margin:.2f} USDT")
                    return None
        except Exception as _e:
            logger.warning(f"[Live] Binance kullanılabilir bakiye kontrolü yapılamadı: {_e}")

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

        # 4. Borsaya Emir Gonderimi (Entry - Smart Limit/Market Chase)
        max_chase_pct = getattr(signal, "max_chase_pct", None) or getattr(config, "MAX_CHASE_PCT", 0.15)
        start_time = time.time()
        chase_result = self._execute_chase_limit_order(
            symbol=symbol,
            side=side,
            qty_str=qty_str,
            entry_price=current_price,
            max_chase_pct=max_chase_pct
        )
        end_time = time.time()
        latency_ms = int((end_time - start_time) * 1000)

        if not chase_result:
            logger.error(f"[LIVE REJECTED] Limit chase entry basarisiz veya reddedildi {symbol}")
            return None

        entry_price = chase_result['avgPrice']
        qty_float = chase_result['executedQty']
        qty_str = self._format_quantity(symbol, qty_float)

        # Calculate slippage against target signal entry price
        slippage_val = 0.0
        if current_price > 0:
            if direction == "LONG":
                slippage_val = (entry_price - current_price) / current_price * 100.0
            else:
                slippage_val = (current_price - entry_price) / current_price * 100.0
        
        # Ensure slippage is positive/realistic or 0
        slippage_val = max(0.0, slippage_val)

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
            final_score=signal.final_score,
            slippage=slippage_val,
            latency_ms=latency_ms
        )
        
        # State'i paper engine ile uyumlu kaydet
        from core.trailing_engine import TradeExitState
        initial_state = TradeExitState(current_sl=signal.stop_loss, highest_price=entry_price)
        
        # Eger Binance siparis id'leri gerekiyorsa metadata icine gomulebilir
        meta_dict = initial_state.to_dict()
        meta_dict['binance_entry_order_id'] = chase_result.get('orderId')
        meta_dict['binance_entry_order_ids'] = chase_result.get('orderIds', [])
        meta_dict['binance_sl_order_id'] = sl_order_id
        if signal.metadata:
            meta_dict.update(signal.metadata)
        
        trade_id = database.create_trade(trade, metadata=json.dumps(meta_dict))
        
        if trade_id is None:
            logger.error(f"[LIVE] Islem borsada acildi ama DB'ye yazilamadi: {symbol}")
            return None
            
        logger.info(f"[LIVE SUCCESS] Trade basariyla acildi: #{trade_id} {symbol} @ {entry_price} (Slippage={slippage_val:.3f}%, Latency={latency_ms}ms)")
        
        return trade_id
        
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
