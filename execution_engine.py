"""
execution_engine.py — AX Execution Engine v5.0 (Production)
=============================================================
Paper trade yaşam döngüsü yöneticisi.

Yenilikler:
  - Tam TP1/TP2/TP3 partial close desteği
  - TrailingEngine entegrasyonu (state-sync)
  - Breakeven otomasyonu
  - Max hold time timeout sistemi
  - Partial PnL hesabı ve balance güncelleme
  - Trade state metadata'da saklanır (DB crash-safe)
  - Gerçek emir gönderilmez — paper only
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import config
import database
from core.data_layer import SignalData, TradeData, TradeStatus
from core.accounting import (
    build_trade_from_signal,
    calculate_unrealized_pnl,
    calculate_realized_pnl,
)
from core.market_data import get_current_price
from core.trailing_engine import TrailingEngine, TradeExitState
from telegram_delivery import TelegramDelivery

logger = logging.getLogger("ax.execution")

def parse_utc_datetime(dt_str: str) -> datetime:
    """Zaman damgasini timezone-aware UTC datetime nesnesine donusturur."""
    dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt

# ── Config ───────────────────────────────────────────────────────────
try:
    MAX_HOLD_MINUTES = int(getattr(config, "MAX_HOLD_MINUTES", 240))
    TP1_CLOSE_PCT = float(getattr(config, "TP1_CLOSE_PCT", 40))
    TP2_CLOSE_PCT = float(getattr(config, "TP2_CLOSE_PCT", 30))
    RUNNER_CLOSE_PCT = float(getattr(config, "RUNNER_CLOSE_PCT", 30))
except Exception:
    MAX_HOLD_MINUTES = 240
    TP1_CLOSE_PCT = 40
    TP2_CLOSE_PCT = 30
    RUNNER_CLOSE_PCT = 30

# ── AIDecisionEngine modül önbelleği (her trade kapanışında yeniden init önlenir) ─
_cached_ai_engine = None

def _get_ai_engine():
    global _cached_ai_engine
    if _cached_ai_engine is None:
        try:
            from core.ai_decision_engine import AIDecisionEngine
            from config import DB_PATH
            _cached_ai_engine = AIDecisionEngine(db_path=DB_PATH)
        except Exception:
            pass
    return _cached_ai_engine


class ExecutionEngine:
    """Paper trade yaşam döngüsü yöneticisi."""

    def __init__(self):
        self.telegram = TelegramDelivery()
        self.trailing = TrailingEngine()

    # ── Paper trade açma ─────────────────────────────────────────

    def open_paper_trade(self, signal: SignalData) -> Optional[int]:
        """
        Sinyalden paper trade oluşturur ve DB'ye kaydeder.
        Returns: trade_id veya None
        """
        stats = database.get_dashboard_stats()
        balance = stats.get("balance", config.INITIAL_PAPER_BALANCE if hasattr(config, "INITIAL_PAPER_BALANCE") else 2000.0)

        trade = build_trade_from_signal(
            signal, balance, config.DEFAULT_FEE_RATE, config.MAX_LEVERAGE,
        )
        if trade is None:
            logger.warning("Trade oluşturulamadı: %s", signal.symbol)
            return None

        # Initial exit state metadata'ya gömülür
        initial_state = TradeExitState(
            current_sl=trade.stop_loss,
            highest_price=trade.entry_price,
        )
        state_json = json.dumps(initial_state.to_dict())

        trade_id = database.create_trade(trade, metadata=state_json)
        if trade_id is None:
            logger.error("Trade DB'ye yazılamadı: %s", signal.symbol)
            return None

        logger.info(
            "Paper trade açıldı: #%s %s %s @ %.4f  TP1=%.4f  SL=%.4f",
            trade_id, signal.symbol, signal.side,
            signal.entry_price, signal.tp1 or 0, signal.stop_loss,
        )

        return trade_id

    # ── Açık trade güncelleme ────────────────────────────────────

    def update_open_trades(self) -> None:
        """Tüm açık trade'lerin fiyatını, PnL'ini ve exit koşullarını günceller."""
        open_trades = database.get_open_trades()
        for trade in open_trades:
            try:
                self._process_single_trade(trade)
            except Exception as exc:
                logger.error(
                    "Trade güncelleme hatası [#%s %s]: %s",
                    trade.get("id"), trade.get("symbol"), exc,
                )

    def _process_single_trade(self, trade: dict) -> None:
        """Tek trade'i değerlendirir."""
        trade_id = trade["id"]
        symbol = trade["symbol"]

        # Güncel fiyat
        current = get_current_price(symbol)
        if current is None or current <= 0:
            logger.debug("Fiyat alınamadı: %s", symbol)
            return

        # Unrealized PnL
        upnl = calculate_unrealized_pnl(
            side=trade.get("direction") or trade.get("side", "LONG"),
            entry_price=trade.get("entry") or trade.get("entry_price", 0),
            current_price=current,
            quantity=trade.get("qty") or trade.get("quantity", 0),
            fee_rate=config.DEFAULT_FEE_RATE,
        )
        database.update_trade_price(trade_id, current, upnl)

        # Broadcast live PnL and trade updates to WebSocket clients
        try:
            from websocket_events import event_manager
            if event_manager:
                _bal = database.get_paper_balance() or 0.0
                event_manager.broadcast_pnl_update(_bal, upnl, trade.get("realized_pnl", 0))
                # Also broadcast the live trades so the UI table updates current price
                event_manager.broadcast_live_update(database.get_open_trades())
        except Exception as _e:
            logger.debug(f"Broadcast error in execution engine: {_e}")

        # Max hold time kontrolü
        if self._is_timeout(trade):
            logger.info(
                "[Execution] Timeout: #%s %s → kapatılıyor (max_hold=%dm)",
                trade_id, symbol, MAX_HOLD_MINUTES,
            )
            self.close_trade(trade, current, "MAX_HOLD_TIMEOUT")
            return

        # Exit state yükle (metadata'dan)
        state = self._load_exit_state(trade)

        # ATR tahminli (yoksa None)
        atr = self._estimate_atr(trade, current)

        # TrailingEngine değerlendirmesi
        result = self.trailing.evaluate(trade, current, state, atr)

        # ── Full close ──────────────────────────────────────────
        if result.should_full_close:
            self.close_trade(trade, result.close_at_price or current, result.full_close_reason)
            return

        # ── Partial close ────────────────────────────────────────
        if result.should_partial_close:
            self._handle_partial_close(trade, current, result, state)
            return

        # ── State güncelle (SL değişmişse) ───────────────────────
        if result.new_sl and result.new_sl != (state.current_sl or trade.get("sl") or trade.get("stop_loss", 0)):
            database.update_trade_sl(trade_id, result.new_sl)

        # Exit state'i DB'ye yaz
        self._save_exit_state(trade_id, state)

    def _handle_partial_close(
        self,
        trade: dict,
        current_price: float,
        result,
        state: TradeExitState,
    ) -> None:
        """Partial close işlemi."""
        trade_id = trade["id"]
        symbol = trade["symbol"]
        close_pct = result.close_pct / 100.0
        qty_to_close = (trade.get("qty") or trade.get("quantity", 0)) * close_pct
        
        # Eger LIVE modundaysak once borsaya gonder
        if config.EXECUTION_MODE == "live":
            try:
                from core.live_execution import LiveExecutionEngine
                le = LiveExecutionEngine()
                le.execute_live_close(symbol, trade.get("direction") or trade.get("side", "LONG"), qty_to_close)
            except Exception as e:
                logger.error(f"Live partial close error {symbol}: {e}")

        partial_pnl = calculate_realized_pnl(
            side=trade.get("direction") or trade.get("side", "LONG"),
            entry_price=trade.get("entry") or trade.get("entry_price", 0),
            exit_price=current_price,
            quantity=qty_to_close,
            fee_rate=config.DEFAULT_FEE_RATE,
        )

        # DB: partial close kaydı
        database.record_partial_close(
            trade_id=trade_id,
            close_qty=qty_to_close,
            close_pct=result.close_pct,
            close_price=current_price,
            partial_pnl=partial_pnl,
            reason=result.reason,
            new_sl=result.new_sl,
        )

        # Exit state kaydet
        self._save_exit_state(trade_id, state)

        logger.info(
            "[Execution] Partial close: #%s %s @ %.4f  reason=%s  qty=%.4f  pnl=%.4f",
            trade_id, symbol, current_price, result.reason, qty_to_close, partial_pnl,
        )

        # Telegram
        self.telegram.send_message(
            f"🔀 <b>Partial Close</b>\n"
            f"#{trade_id} {symbol} {trade.get('direction') or trade.get('side', '?')}\n"
            f"Reason: {result.reason}\n"
            f"Close: {result.close_pct:.0f}%  @ ${current_price:.4f}\n"
            f"PnL: ${partial_pnl:+.4f}\n"
            f"New SL: {result.new_sl:.4f}" if result.new_sl else ""
        )

    # ── Trade kapatma ────────────────────────────────────────────

    def close_trade(
        self, trade: dict, exit_price: float, reason: str,
    ) -> None:
        """Trade'i kapatır, realized PnL hesaplar, DB ve Telegram günceller."""
        _side = trade.get("direction") or trade.get("side", "LONG")
        _entry = trade.get("entry") or trade.get("entry_price", 0)
        _qty = trade.get("qty") or trade.get("quantity", 0)

        # Kalan miktar (yeni: remaining_qty absolute, eski: remaining_qty_pct yüzde)
        if trade.get("remaining_qty") is not None:
            remaining_qty = float(trade.get("remaining_qty") or _qty)
        else:
            remaining_qty_pct = trade.get("remaining_qty_pct", 100.0) or 100.0
            remaining_qty = _qty * (remaining_qty_pct / 100.0)

        # Eger LIVE modundaysak once borsaya gonder
        if config.EXECUTION_MODE == "live" and remaining_qty > 0:
            try:
                from core.live_execution import LiveExecutionEngine
                le = LiveExecutionEngine()
                le.execute_live_close(trade["symbol"], _side, remaining_qty)
                le.cancel_all_orders(trade["symbol"]) # Cancel hard stop loss
            except Exception as e:
                logger.error(f"Live full close error {trade['symbol']}: {e}")

        rpnl = calculate_realized_pnl(
            side=_side,
            entry_price=_entry,
            exit_price=exit_price,
            quantity=_qty,
            fee_rate=config.DEFAULT_FEE_RATE,
        )

        # Partial close'lardan birikmiş PnL (yeni: realized_pnl, eski: accumulated_pnl)
        accumulated = trade.get("realized_pnl", 0.0) or trade.get("accumulated_pnl", 0.0) or 0.0
        # Kalan miktar (yeni: remaining_qty absolute, eski: remaining_qty_pct yüzde)
        if trade.get("remaining_qty") is not None:
            remaining_qty = float(trade.get("remaining_qty") or _qty)
        else:
            remaining_qty_pct = trade.get("remaining_qty_pct", 100.0) or 100.0
            remaining_qty = _qty * (remaining_qty_pct / 100.0)

        remaining_pnl = calculate_realized_pnl(
            side=_side,
            entry_price=_entry,
            exit_price=exit_price,
            quantity=remaining_qty,
            fee_rate=config.DEFAULT_FEE_RATE,
        )
        total_pnl = round(accumulated + remaining_pnl, 6)

        database.close_trade(
            trade_id=trade["id"],
            exit_price=exit_price,
            realized_pnl=total_pnl,
            close_reason=reason,
        )

        # ── P0 BUG FIX #1: Bakiye güncellemesi ─────────────────────────────
        # _finalize() sadece eski scalp_bot pipeline'ında çağrılıyordu.
        # ExecutionEngine.close_trade() kendi güncellemeyi yapmalı.
        # Incremental delta: sadece bu kapatmada kazanılan/kaybedilen miktar.
        # Partial close'lardan birikmiş PnL (accumulated) zaten önceki
        # record_partial_close() çağrılarında bakiyeye EKLENMEDİ,
        # bu yüzden total_pnl'in tamamı delta olarak eklenir.
        try:
            database.update_paper_balance(total_pnl)
            logger.info(
                "[Balance] Bakiye güncellendi: #%s %s %s PnL=%.4f",
                trade["id"], trade["symbol"],
                trade.get("direction") or trade.get("side", "?"), total_pnl,
            )
        except Exception as _bal_err:
            logger.error("[Balance] Bakiye güncellenemedi [#%s]: %s", trade["id"], _bal_err)
        # ────────────────────────────────────────────────────────────────────

        logger.info(
            "Trade kapatıldı: #%s %s %s → %s  PnL=%.4f (accumulated=%.4f + remaining=%.4f)",
            trade["id"], trade["symbol"], trade.get("direction") or trade.get("side", "?"), reason,
            total_pnl, accumulated, remaining_pnl,
        )

        # ── P0 BUG FIX #3: pattern_memory INSERT (ML data pipeline) ────────
        # ML modeli pattern_memory tablosundan beslendiği için her kapanan
        # trade'in sinyal özelliklerini buraya yazıyoruz.
        try:
            _side   = trade.get("direction") or trade.get("side", "LONG")
            _entry  = float(trade.get("entry") or trade.get("entry_price", 0) or 0)
            _sl     = float(trade.get("sl") or trade.get("stop_loss", 0) or 0)
            _tp1    = float(trade.get("tp1", 0) or 0)
            # Sinyal özelliklerini metadata JSON'dan oku
            _meta = {}
            try:
                _meta_raw = trade.get("metadata", "{}")
                if _meta_raw and isinstance(_meta_raw, str) and "{" in _meta_raw:
                    import json as _json
                    _meta = _json.loads(_meta_raw)
            except Exception:
                pass
            _adx      = float(_meta.get("adx", 0) or 0)
            _rsi5     = float(_meta.get("rsi5", 50) or 50)
            _rsi1     = float(_meta.get("rsi1", 50) or 50)
            _ml_score = float(_meta.get("ml_score", 50) or 50)
            _rv       = float(_meta.get("rv", 1.0) or 1.0)
            _outcome  = 1 if total_pnl > 0 else 0
            # SL mesafesinden R-multiple hesapla
            _sl_dist  = abs(_entry - _sl) if _sl > 0 and _entry > 0 else 1e-10
            _qty      = float(trade.get("qty") or trade.get("quantity", 1) or 1)
            _r_mult   = round(total_pnl / (_sl_dist * _qty + 1e-10), 3)
            # Hash: ADX bant + RSI bant + yön + kalite
            _quality  = trade.get("setup_quality") or trade.get("quality", "B")
            _adx_band = int(_adx // 5) * 5  # 5'lik bantlar: 15,20,25...
            _rsi_band = int(_rsi5 // 10) * 10  # 10'luk bantlar: 20,30...
            import hashlib as _hl
            _hash = _hl.md5(
                f"{_adx_band}:{_rsi_band}:{_side}:{_quality}".encode()
            ).hexdigest()[:16]
            database.upsert_pattern_memory(
                pattern_hash = _hash,
                outcome      = _outcome,
                r_multiple   = _r_mult,
                features     = {
                    "adx": _adx, "rsi5": _rsi5, "rsi1": _rsi1,
                    "ml_score": _ml_score, "rv": _rv,
                    "side": _side, "quality": _quality,
                    "symbol": trade["symbol"],
                },
            )
            logger.debug(
                "[ML] pattern_memory güncellendi: %s hash=%s outcome=%d r=%.2f",
                trade["symbol"], _hash, _outcome, _r_mult,
            )
        except Exception as _pm_err:
            logger.warning("[ML] pattern_memory yazılamadı [#%s]: %s", trade["id"], _pm_err)
        # ────────────────────────────────────────────────────────────────────

        # ── WebSocket broadcast ──────────────────────────────────────────────
        try:
            from websocket_events import event_manager
            if event_manager:
                _dir = trade.get("direction") or trade.get("side", "LONG")
                event_manager.broadcast_trade_closed(trade["symbol"], _dir, total_pnl, reason)
                _new_bal = database.get_active_balance() or 0.0
                event_manager.broadcast_pnl_update(_new_bal, 0, total_pnl)
        except Exception as _ws_err:
            logger.debug("[WS] broadcast hatası: %s", _ws_err)
        # ────────────────────────────────────────────────────────────────────

        # =========================================================================
        # 🧠 AI Learning callback (Kesintisiz Öğrenme)
        # =========================================================================
        try:
            from core.ai_decision_engine import AIDecisionEngine as _AIDE
            _aide = _AIDE()
            _net = total_pnl  # final PnL
            _aide.learn_from_outcome(
                symbol=trade["symbol"],
                net_pnl=float(_net),
                reason=reason,
            )
            logger.debug(f"[AI Learn] {trade['symbol']} PnL={_net:.4f} reason={reason}")
        except Exception as _ale:
            logger.debug(f"[AI Learn] skip: {_ale}")
        # ─────────────────────────────────────────────────────────────

    # ── Sinyal işleme ────────────────────────────────────────────

    def process_signal(self, signal: SignalData) -> Optional[int]:
        """
        Sinyali alır, paper trade açar.
        Live trading kontrolü burada yapılır.
        Returns: trade_id veya None
        """
        if config.is_live_trading_allowed():
            logger.error("LIVE TRADING İSTENDİ AMA BU ENGINE SADECE PAPER MODE!")
            return None

        return self.open_paper_trade(signal)

    # ── Yardımcı metodlar ─────────────────────────────────────────

    def _is_timeout(self, trade: dict) -> bool:
        """Max hold time aşıldı mı?"""
        try:
            opened = trade.get("open_time", "") or trade.get("opened_at", "")
            if not opened:
                return False
            opened_dt = parse_utc_datetime(opened)
            now_dt = datetime.now(timezone.utc)
            elapsed = (now_dt - opened_dt).total_seconds() / 60.0
            return elapsed > MAX_HOLD_MINUTES
        except Exception:
            return False

    def _load_exit_state(self, trade: dict) -> TradeExitState:
        """Trade metadata'sından exit state yükler."""
        try:
            meta_raw = trade.get("metadata", "")
            if meta_raw and meta_raw.strip().startswith("{"):
                meta = json.loads(meta_raw)
                return TradeExitState.from_dict(meta)
        except Exception as exc:
            logger.debug("Exit state yüklenemedi: %s", exc)
        # Yeni state oluştur
        return TradeExitState(
            current_sl=float(trade.get("sl") or trade.get("stop_loss", 0) or 0),
            highest_price=float(trade.get("entry") or trade.get("entry_price", 0) or 0),
        )

    def _save_exit_state(self, trade_id: int, state: TradeExitState) -> None:
        """Exit state'i trade metadata'sına yazar."""
        try:
            database.update_trade_metadata(trade_id, json.dumps(state.to_dict()))
        except Exception as exc:
            logger.error("Exit state kaydedilemedi [#%s]: %s", trade_id, exc)

    def _estimate_atr(self, trade: dict, current_price: float) -> Optional[float]:
        """
        ATR tahmini — gerçek ATR yoksa entry-sl farkından hesaplanır.
        Trailing için kullanılır.
        """
        try:
            entry = float(trade.get("entry") or trade.get("entry_price", 0))
            sl = float(trade.get("sl") or trade.get("stop_loss", 0))
            if entry > 0 and sl > 0:
                return abs(entry - sl)
        except Exception:
            pass
        return None


# ─────────────────────────────────────────────────────────────────────────────
# TRADE MONİTÖRÜ
# ─────────────────────────────────────────────────────────────────────────────

def monitor_trades(client) -> list:
    """
    Tüm açık trade'leri kontrol et:
    - SL tetiklendi mi?
    - TP1 / TP2 tetiklendi mi?
    - Runner trailing stop güncelle

    Returns:
        Kapanan trade'lerin ID listesi.
    """
    from database import get_open_trades
    trades = get_open_trades()
    closed = []

    for t in trades:
        try:
            result = _check_trade(client, t)
            if result:
                closed.append(t["id"])
        except Exception as e:
            logger.error(f"[Execution] Monitor hata {t['id']}: {e}")

    return closed


def _get_price(client, symbol: str) -> float:
    # 1. CCXT yardimiyla fiyati almayı dene
    try:
        import ccxt
        exchange = ccxt.binance({'options': {'defaultType': 'future'}})
        # CCXT symbol format: BTC/USDT:USDT (Futures)
        ccxt_symbol = symbol.replace("USDT", "/USDT:USDT")
        ticker = exchange.fetch_ticker(ccxt_symbol)
        return float(ticker['last'])
    except Exception as e:
        logger.warning(f"[Execution] ccxt fetch_ticker failed for {symbol}: {e}. Trying public API fallback...")

    # 2. Hata durumunda public API fallback
    try:
        import requests
        r = requests.get("https://fapi.binance.com/fapi/v1/ticker/price", params={"symbol": symbol}, timeout=5)
        if r.status_code == 200:
            data = r.json()
            return float(data["price"])
        else:
            logger.error(f"[Execution] Fallback public API returned HTTP {r.status_code} for {symbol}")
    except Exception as fallback_err:
        logger.error(f"[Execution] Fallback public API failed for {symbol}: {fallback_err}")

    return 0.0


def _check_trade(client, t: dict) -> bool:
    """
    Tek trade'i kontrol et. Kapandıysa True döner.
    """
    from database import (
        update_trade, close_trade as db_close_trade,
        get_open_trades, update_paper_balance, get_paper_balance,
        save_trade_event,
    )
    from database import update_trade_stats
    try:
        from websocket_events import event_manager
    except Exception:
        event_manager = None

    try:
        TRAIL_ATR_MULT = float(getattr(config, "TRAIL_ATR_MULT", 1.5))
        BREAKEVEN_ENABLED = bool(getattr(config, "BREAKEVEN_ENABLED", True))
        BREAKEVEN_OFFSET_PCT = float(getattr(config, "BREAKEVEN_OFFSET_PCT", 0.1))
    except Exception:
        TRAIL_ATR_MULT = 1.5
        BREAKEVEN_ENABLED = True
        BREAKEVEN_OFFSET_PCT = 0.1

    trade_id  = t["id"]
    symbol    = t["symbol"]
    # BUG FIX: DB sutun ismi normalizasyonu
    direction = t.get("direction") or t.get("side", "LONG")
    # BUG FIX: status 'OPEN'→'open' normalize (TP1/TP2 icin kritik)
    status    = (t.get("status") or "OPEN").lower()
    entry     = float(t.get("entry") or t.get("entry_price") or 0)
    sl        = float(t.get("sl") or t.get("stop_loss") or 0)
    tp1       = float(t.get("tp1") or 0)
    tp2       = float(t.get("tp2") or 0)
    trail     = t.get("trail_stop") or t.get("trailing_sl")
    qty       = float(t.get("qty") or t.get("quantity") or 1)
    qty_tp1   = t.get("qty_tp1") or qty * TP1_CLOSE_PCT / 100
    qty_tp2   = t.get("qty_tp2") or qty * TP2_CLOSE_PCT / 100
    qty_runner= t.get("qty_runner") or qty - qty_tp1 - qty_tp2

    price = _get_price(client, symbol)
    if not price:
        return False

    is_long = direction == "LONG"

    # unrealized_pnl'i accounting modülü üzerinden güncelle
    rem_qty = qty
    if status == "tp1_hit":
        rem_qty = qty - float(qty_tp1 or 0)
    elif status == "runner":
        rem_qty = float(qty_runner or 0)

    if rem_qty > 0:
        unreal = _calc_pnl(direction, entry, price, rem_qty)
        update_trade(trade_id, {"unrealized_pnl": unreal, "current_price": price})
        if event_manager: event_manager.broadcast_pnl_update(get_paper_balance(), unreal, t.get("realized_pnl", 0))

    # ── MFE/MAE Tracking (Bug #4) ────────────────────────────────────────────
    if entry > 0:
        if is_long:
            favorable_pct = max(0.0, (price - entry) / entry)
            adverse_pct   = max(0.0, (entry - price) / entry)
        else:
            favorable_pct = max(0.0, (entry - price) / entry)
            adverse_pct   = max(0.0, (price - entry) / entry)
        current_mfe = max(float(t.get("mfe") or 0), favorable_pct)
        current_mae = max(float(t.get("mae") or 0), adverse_pct)
        if current_mfe != float(t.get("mfe") or 0) or current_mae != float(t.get("mae") or 0):
            update_trade_stats(trade_id, mfe=current_mfe, mae=current_mae)

    # ── SL Kontrolü (OPEN/TP1_HIT modları için — runner modda ayrı handle) ──
    if status != "runner":
        sl_hit = (is_long and price <= sl) or (not is_long and price >= sl)
        if sl_hit:
            # BUG FIX: tp1_hit modunda kalan miktar = qty - qty_tp1 (TP1 zaten kapatıldı)
            remaining_sl_qty = qty - float(qty_tp1 or 0) if status == "tp1_hit" else qty
            remaining_sl_pnl = _calc_pnl(direction, entry, price, remaining_sl_qty)
            accumulated_sl   = float(t.get("realized_pnl") or 0)
            total_sl_pnl     = accumulated_sl + remaining_sl_pnl
            save_trade_event(trade_id, "SL_HIT", f"price={price} pnl={total_sl_pnl}")
            _finalize(trade_id, price, total_sl_pnl, "sl", t)
            return True

    # ── TP1 Kontrolü ────────────────────────────────────────────────────────
    if status == "open":
        tp1_hit = (is_long and price >= tp1) or (not is_long and price <= tp1)
        if tp1_hit:
            pnl_tp1 = _calc_pnl(direction, entry, tp1, qty_tp1)
            # ── Breakeven SL — Backtest: Max Loss serisi 17, Loss→Loss %80.2 ──
            # TP1 tetiklenince SL entry + buffer'a çekilir (sıfır riskli runner)
            be_enabled = BREAKEVEN_ENABLED
            if be_enabled:
                offset = entry * BREAKEVEN_OFFSET_PCT / 100
                be_sl = (entry + offset) if is_long else (entry - offset)
            else:
                be_sl = entry
            new_sl = be_sl if be_enabled else entry
            update_trade(trade_id, {
                "status":       "tp1_hit",
                "tp1_hit":      1,
                "realized_pnl": pnl_tp1,
                "remaining_qty": qty - qty_tp1,
                "stop_loss":    round(new_sl, 6),  # BUG FIX: DB kolonu
                "sl":           round(new_sl, 6),  # compat
            })
            _update_live_sl_safe(symbol, direction, new_sl)
            update_paper_balance(pnl_tp1)
            save_trade_event(trade_id, "TP1_HIT", f"price={tp1} pnl={pnl_tp1:.4f} new_sl={new_sl:.6f}")
            if event_manager: event_manager.broadcast_live_update(get_open_trades())
            if event_manager: event_manager.broadcast_pnl_update(get_paper_balance(), t.get("unrealized_pnl", 0), t.get("realized_pnl", 0) + pnl_tp1)
            try:
                import asyncio as _asyncio
                from core.event_bus import event_bus as _ebus
                from core.event_types import Event as _Event, EventType as _ET
                _loop = _asyncio.get_event_loop()
                if _loop and _loop.is_running():
                    _asyncio.run_coroutine_threadsafe(
                        _ebus.publish(_Event(type=_ET.TP_OR_SL_TRIGGERED, payload={
                            "trade_id": trade_id, "symbol": symbol,
                            "direction": direction, "level": "TP1",
                            "price": tp1, "pnl": pnl_tp1,
                        })),
                        _loop
                    )
            except Exception as _ev_err:
                logger.debug("TP1 event publish hatası: %s", _ev_err)

    # ── TP2 Kontrolü ────────────────────────────────────────────────────────
    if status == "tp1_hit":
        tp2_hit = (is_long and price >= tp2) or (not is_long and price <= tp2)
        if tp2_hit:
            pnl_tp2 = _calc_pnl(direction, entry, tp2, qty_tp2)
            realized = (t.get("realized_pnl") or 0) + pnl_tp2
            # Runner'ı başlat — trail stop koy
            _cached_atr_val = _get_atr(client, symbol)
            if is_long:
                new_trail = tp2 - _cached_atr_val * TRAIL_ATR_MULT
            else:
                new_trail = tp2 + _cached_atr_val * TRAIL_ATR_MULT
            update_trade(trade_id, {
                "status":       "runner",
                "tp2_hit":      1,
                "realized_pnl": realized,
                "remaining_qty": qty - qty_tp1 - qty_tp2,
                "trail_stop":   new_trail,
                "stop_loss":    entry,  # BUG FIX: DB kolonu
                "sl":           entry,  # compat
            })
            _update_live_sl_safe(symbol, direction, entry)
            update_paper_balance(pnl_tp2)
            save_trade_event(trade_id, "TP2_HIT", f"price={tp2} pnl={pnl_tp2:.4f} trail={new_trail:.6f}")
            if event_manager: event_manager.broadcast_live_update(get_open_trades())
            if event_manager: event_manager.broadcast_pnl_update(get_paper_balance(), t.get("unrealized_pnl", 0), realized)
            try:
                import asyncio as _asyncio
                from core.event_bus import event_bus as _ebus
                from core.event_types import Event as _Event, EventType as _ET
                _loop = _asyncio.get_event_loop()
                if _loop and _loop.is_running():
                    _asyncio.run_coroutine_threadsafe(
                        _ebus.publish(_Event(type=_ET.TP_OR_SL_TRIGGERED, payload={
                            "trade_id": trade_id, "symbol": symbol,
                            "direction": direction, "level": "TP2",
                            "price": tp2, "pnl": pnl_tp2,
                        })),
                        _loop
                    )
            except Exception as _ev_err:
                logger.debug("TP2 event publish hatası: %s", _ev_err)
            logger.info(f"[Execution] TP2 #{trade_id} {symbol} +{pnl_tp2:.3f}$ → RUNNER trail={new_trail:.6f}")
            return False

    # ── Runner Trailing Stop Kontrolü ───────────────────────────────────────
    if status == "runner":
        trail_f = float(trail or 0)
        # trail_stop yoksa sl (breakeven) kullan
        active_stop = trail_f if trail_f > 0 else sl

        if trail_f > 0:
            # BUG FIX: _cached_atr_val TP2 bloğu çalışmadan RUNNER'a gelinirse NameError verir
            # locals().get() ile güvenli referans al
            _atr_cached = locals().get('_cached_atr_val')
            atr_val = float(_atr_cached) if _atr_cached else _get_atr(client, symbol)
            if atr_val > 0:
                if is_long:
                    new_trail_sl = price - atr_val * TRAIL_ATR_MULT
                    if new_trail_sl > trail_f:
                        diff_pct = ((new_trail_sl - trail_f) / trail_f * 100) if trail_f > 0 else 100.0
                        if diff_pct > 0.1: # Sadece %0.1'den büyük fark varsa Binance'i güncelle
                            trail_f = new_trail_sl
                            active_stop = trail_f
                            update_trade(trade_id, {"trail_stop": round(trail_f, 6)})
                            _update_live_sl_safe(symbol, direction, trail_f)
                            save_trade_event(trade_id, "TRAIL_UPDATED", f"trail={trail_f:.6f} price={price}")
                else:
                    new_trail_sl = price + atr_val * TRAIL_ATR_MULT
                    if new_trail_sl < trail_f or trail_f == 0.0:
                        diff_pct = ((trail_f - new_trail_sl) / trail_f * 100) if trail_f > 0 else 100.0
                        if diff_pct > 0.1: # Sadece %0.1'den büyük fark varsa Binance'i güncelle
                            trail_f = new_trail_sl
                            active_stop = trail_f
                            update_trade(trade_id, {"trail_stop": round(trail_f, 6)})
                            _update_live_sl_safe(symbol, direction, trail_f)
                            save_trade_event(trade_id, "TRAIL_UPDATED", f"trail={trail_f:.6f} price={price}")


        # Runner stop vuruldu mu? (trail_stop veya breakeven sl)
        if active_stop > 0:
            stop_hit = (is_long and price <= active_stop) or (not is_long and price >= active_stop)
            if stop_hit:
                runner_qty = float(t.get("qty_runner") or (qty - qty_tp1 - qty_tp2))
                pnl_runner = _calc_pnl(direction, entry, price, runner_qty)
                total_pnl = (t.get("realized_pnl") or 0) + pnl_runner
                close_reason = "trail" if trail_f > 0 else "breakeven"
                save_trade_event(trade_id, "TRAIL_HIT", f"price={price} stop={active_stop:.6f} runner_pnl={pnl_runner:.4f}")
                logger.info(f"[Execution] RUNNER STOP HIT #{trade_id} {symbol} stop={active_stop:.4f} reason={close_reason} total_pnl={total_pnl:.4f}")
                _finalize(trade_id, price, total_pnl, close_reason, t)
                return True

        # ── Max Hold Timeout (runner dahil tüm durumlar) ────────────────
        try:
            open_t = t.get("open_time", "") or t.get("opened_at", "")
            if open_t:
                opened_dt = parse_utc_datetime(open_t)
                elapsed_min = (datetime.now(timezone.utc) - opened_dt).total_seconds() / 60.0
                if elapsed_min > MAX_HOLD_MINUTES:
                    runner_qty = float(t.get("qty_runner") or (qty - qty_tp1 - qty_tp2))
                    pnl_runner = _calc_pnl(direction, entry, price, runner_qty)
                    total_pnl = (t.get("realized_pnl") or 0) + pnl_runner
                    save_trade_event(trade_id, "TIMEOUT", f"elapsed={elapsed_min:.0f}m max={MAX_HOLD_MINUTES}m")
                    logger.info(f"[Execution] MAX HOLD TIMEOUT #{trade_id} {symbol} elapsed={elapsed_min:.0f}dk")
                    _finalize(trade_id, price, total_pnl, "max_hold_timeout", t)
                    return True
        except Exception as _to_err:
            logger.debug(f"[Execution] Timeout kontrolü hatası: {_to_err}")

    # ── Max Hold Timeout — OPEN/TP1_HIT durumları için ──────────────────────
    if status in ("open", "tp1_hit"):
        try:
            open_t = t.get("open_time", "") or t.get("opened_at", "")
            if open_t:
                opened_dt = parse_utc_datetime(open_t)
                elapsed_min = (datetime.now(timezone.utc) - opened_dt).total_seconds() / 60.0
                if elapsed_min > MAX_HOLD_MINUTES:
                    # BUG FIX: tp1_hit modunda kalan miktar = qty - qty_tp1
                    remaining_to_qty = qty - float(qty_tp1 or 0) if status == "tp1_hit" else qty
                    pnl_remaining    = _calc_pnl(direction, entry, price, remaining_to_qty)
                    accumulated_to   = float(t.get("realized_pnl") or 0)
                    pnl_close        = accumulated_to + pnl_remaining
                    save_trade_event(trade_id, "TIMEOUT", f"elapsed={elapsed_min:.0f}m max={MAX_HOLD_MINUTES}m")
                    logger.info(f"[Execution] MAX HOLD TIMEOUT #{trade_id} {symbol} status={status} elapsed={elapsed_min:.0f}dk")
                    _finalize(trade_id, price, pnl_close, "max_hold_timeout", t)
                    return True
        except Exception as _to_err2:
            logger.debug(f"[Execution] Timeout kontrolü hatası: {_to_err2}")

    return False


# ─────────────────────────────────────────────────────────────────────────────
# YARDIMCILAR
# ─────────────────────────────────────────────────────────────────────────────

def _calc_pnl(direction: str, entry: float, exit_price: float, qty: float) -> float:
    """Paper trade PnL hesabı (Komisyonlar dahil)."""
    if direction == "LONG":
        gross_pnl = (exit_price - entry) * qty
    else:
        gross_pnl = (entry - exit_price) * qty
        
    # Binance Vadeli İşlem Komisyonu (Varsayılan: %0.04 Taker)
    open_fee = entry * qty * config.DEFAULT_FEE_RATE
    close_fee = exit_price * qty * config.DEFAULT_FEE_RATE
    total_fee = open_fee + close_fee
    
    net_pnl = gross_pnl - total_fee
    return round(net_pnl, 4)


def _get_atr(client, symbol: str, interval: str = "5m", period: int = 14) -> float:
    """Trailing stop için anlık ATR (CCXT ile)."""
    try:
        import pandas as pd
        import ccxt
        exchange = ccxt.binance({'options': {'defaultType': 'future'}})
        ccxt_symbol = symbol.replace("USDT", "/USDT:USDT")
        
        # CCXT OHLCV format: [timestamp, open, high, low, close, volume]
        klines = exchange.fetch_ohlcv(ccxt_symbol, timeframe=interval, limit=period + 5)
        
        df = pd.DataFrame(klines, columns=["time", "open", "high", "low", "close", "volume"])
        for col in ("high","low","close"):
            df[col] = df[col].astype(float)
        tr = pd.concat([
            df["high"] - df["low"],
            (df["high"] - df["close"].shift()).abs(),
            (df["low"]  - df["close"].shift()).abs(),
        ], axis=1).max(axis=1)
        return float(tr.rolling(period).mean().iloc[-1])
    except Exception as e:
        logger.error(f"ATR hesaplama hatası (CCXT) {symbol}: {e}")
        return 0.01


def _finalize(trade_id: int, close_price: float, net_pnl: float,
              reason: str, t: dict):
    """Trade'i kapat, bakiyeyi güncelle."""
    import database
    from database import (
        close_trade as db_close_trade,
        update_paper_balance, get_paper_balance,
        save_trade_event,
    )
    try:
        from websocket_events import event_manager
    except Exception:
        event_manager = None

    open_t = t.get("open_time", "")
    try:
        opened   = parse_utc_datetime(open_t)
        hold_min = (datetime.now(timezone.utc) - opened).total_seconds() / 60
    except Exception:
        hold_min = 0

    # BUG FIX: database.close_trade(id, exit_price, realized_pnl, close_reason)
    db_close_trade(
        trade_id,
        exit_price=close_price,
        realized_pnl=net_pnl,
        close_reason=reason,
    )
    # BUG FIX: TP1/TP2'de zaten eklendi — sadece delta (kalan) ekle
    # Örnek: net_pnl=10$, realized_pnl(TP1+TP2)=6$ → incremental=4$ (runner kârı)
    already_added   = float(t.get("realized_pnl") or 0)
    incremental_pnl = net_pnl - already_added
    update_paper_balance(incremental_pnl)
    _dir = t.get("direction") or t.get("side", "LONG")
    if event_manager: event_manager.broadcast_trade_closed(t["symbol"], _dir, net_pnl, reason)
    if event_manager: event_manager.broadcast_pnl_update(database.get_active_balance(), 0, net_pnl)
    save_trade_event(trade_id, "CLOSE", f"reason={reason} close_price={close_price} net_pnl={net_pnl:.4f}")

    result = "WIN" if net_pnl > 0 else "LOSS"

    # ── TRADE_CLOSED Event Bus Publish (Bug #3) ──────────────────────────────
    try:
        import asyncio as _asyncio
        from core.event_bus import event_bus as _ebus
        from core.event_types import Event as _Event, EventType as _ET
        _entry_p = float(t.get("entry") or t.get("entry_price") or 1)
        _sl_p    = float(t.get("sl") or t.get("stop_loss") or 1)
        _sl_dist = max(abs(_entry_p - _sl_p), 1e-8)
        _loop = _asyncio.get_event_loop()
        if _loop and _loop.is_running():
            _asyncio.run_coroutine_threadsafe(
                _ebus.publish(_Event(type=_ET.TRADE_CLOSED, payload={
                    "trade_id":      trade_id,
                    "symbol":        t["symbol"],
                    "direction":     t.get("direction", "LONG"),
                    "net_pnl":       net_pnl,
                    "reason":        reason,
                    "r_multiple":    round(net_pnl / _sl_dist, 3),
                    "balance_after": database.get_active_balance(),
                    "duration":      f"{hold_min:.0f}dk",
                })),
                _loop
            )
    except Exception as _ev_err:
        logger.debug("TRADE_CLOSED event publish hatası: %s", _ev_err)

    # ── AI Öğrenme Döngüsü — Eksik 2 Düzeltmesi ──────────────────────────────
    # Her kapanan trade AI'ın Markov, heatmap ve parametre optimizasyonunu besler
    try:
        ai_engine = _get_ai_engine()
        setup_quality = t.get("setup_quality") or t.get("quality") or "B"
        if ai_engine:
            ai_engine.learn_from_trade(
                symbol        = t["symbol"],
                result        = result,          # "WIN" | "LOSS"
                pnl           = net_pnl,
                setup_quality = setup_quality,
            )
            logger.info(
                f"[AI Learn] #{trade_id} {t['symbol']} {result} "
                f"pnl={net_pnl:+.3f}$ quality={setup_quality}"
            )
    except Exception as e:
        logger.warning(f"AI learn_from_trade hatası: {e}")

    # ── CoinLibrary Öğrenme Döngüsü ──────────────────────────────────────────
    try:
        from core.coin_library import update_coin_stats as _update_coin_stats
    except (ImportError, AttributeError):
        def _update_coin_stats(*args, **kwargs): pass
    try:
        entry_p = t.get("entry", 0)
        sl_p    = t.get("sl", 0)
        sl_dist = abs(entry_p - sl_p) if sl_p else 1e-10
        r_mult  = round(net_pnl / (sl_dist * t.get("qty", 1) + 1e-10), 3)
        _update_coin_stats(
            symbol    = t["symbol"],
            result    = result,
            net_pnl   = net_pnl,
            r_multiple= r_mult,
            direction = t.get("direction"),
        )
    except Exception as e:
        logger.warning(f"CoinLibrary update_coin_stats hatası: {e}")
    # ── AI Brain Postmortem Analizi (Bug #6: archive importu kaldırıldı) ──────
    try:
        ai_engine = _get_ai_engine()
        if ai_engine and hasattr(ai_engine, "learn_from_outcome"):
            ai_engine.learn_from_outcome(
                symbol=t["symbol"],
                net_pnl=net_pnl,
                reason=reason,
            )
    except Exception as e:
        logger.warning("learn_from_outcome hatası: %s", e)
    logger.info(
        f"[Execution] KAPANDI #{trade_id} {t['symbol']} {t['direction']} "
        f"{reason.upper()} pnl={net_pnl:+.3f}$ hold={hold_min:.0f}dk"
    )


def open_trade(client, signal_dict: dict, ax_result: dict):
    """scalp_bot.py için module-level wrapper."""
    try:
        from core.data_layer import SignalData
        sig = SignalData()
        sig.symbol      = signal_dict.get("symbol", "")
        sig.side        = signal_dict.get("direction", "LONG")
        sig.entry_price = float(signal_dict.get("entry", 0))
        sig.stop_loss   = float(signal_dict.get("sl", 0))
        sig.tp1         = float(signal_dict.get("tp1", 0))
        sig.tp2         = float(signal_dict.get("tp2", 0))
        sig.tp3         = float(signal_dict.get("runner_target", 0))
        sig.score       = float(signal_dict.get("score", ax_result.get("score", 0)))
        sig.leverage    = int(
            signal_dict.get("leverage")
            or signal_dict.get("leverage_hint")
            or signal_dict.get("leverage_suggestion")
            or 10
        )
        sig.risk_pct    = 1.0
        sig.source      = "scalp_bot"
        sig.metadata    = {
            "candidate_id":      signal_dict.get("candidate_id"),
            "adx":               signal_dict.get("adx", 0),
            "rv":                signal_dict.get("rv", 0),
            "rsi5":              signal_dict.get("rsi5", 50),
            "rsi1":              signal_dict.get("rsi1", 50),
            "btc_trend":         signal_dict.get("btc_trend", "NEUTRAL"),
            "bb_width_pct":      signal_dict.get("bb_width_pct", 0),
            "bb_width_chg":      signal_dict.get("bb_width_chg", 0),
            "momentum_3c":       signal_dict.get("momentum_3c", 0),
            "funding_favorable": signal_dict.get("funding_favorable", 1),
            "ml_score":          signal_dict.get("ml_score", 50),
        }
        # Auto-Compounding
        import config
        paper_balance = database.get_paper_balance() or 250.0
        if getattr(config, 'AUTO_COMPOUNDING', True):
            balance = paper_balance
        else:
            balance = getattr(config, 'BASE_ACCOUNT_SIZE', 1000.0)
            
        trade    = build_trade_from_signal(sig, balance, config.DEFAULT_FEE_RATE, config.MAX_LEVERAGE)
        if trade is None:
            return None
        trade_id = database.create_trade(trade)
        if trade_id:
            logger.info("open_trade: #%s %s %s @ %.4f", trade_id, sig.symbol, sig.side, sig.entry_price)
            # ── Telegram Trade Açılış Bildirimi ──────────────────────────
            try:
                logger.info("[Telegram] Trade açılış bildirimi gönderildi: %s", sig.symbol)
            except Exception as _tg_err:
                logger.warning("[Telegram] Trade açılış hatası: %s", _tg_err)
            # ─────────────────────────────────────────────────────────────
        return trade_id
    except Exception as e:
        logger.error("open_trade hata: %s", e)
        return None

def _update_live_sl_safe(symbol: str, direction: str, new_sl: float):
    import config
    if config.EXECUTION_MODE == "live":
        try:
            from core.live_execution import LiveExecutionEngine
            le = LiveExecutionEngine()
            le.update_live_sl(symbol, direction, new_sl)
        except Exception as e:
            import logging
            logging.getLogger("ax.execution").error(f"Live SL update failed {symbol}: {e}")
