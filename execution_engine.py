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
        balance = stats.get("balance", config.INITIAL_PAPER_BALANCE if hasattr(config, "INITIAL_PAPER_BALANCE") else 250.0)

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

        # Telegram bildirimi
        try:
            from telegram_delivery import send_trade_open as _tg_open
            _tg_open({
                "symbol":             trade.symbol,
                "direction":          trade.side,
                "entry":              trade.entry_price,
                "sl":                 trade.stop_loss,
                "tp1":                trade.tp1 or 0,
                "tp2":                trade.tp2 or 0,
                "tp3":                trade.tp3 or 0,
                "leverage":           trade.leverage,
                "risk_pct":           trade.risk_pct,
                "risk_usd":           trade.risk_usd,
                "margin_used":        trade.margin_used,
                "notional_size":      trade.notional,
                "setup_quality":      getattr(trade, "setup_quality", "-"),
                "final_score":        getattr(trade, "final_score", 0),
                "reason":             getattr(trade, "reason", "-"),
                "max_loss_after_fee": getattr(trade, "max_loss_after_fee", 0),
                "open_fee":           getattr(trade, "open_fee", 0),
            })
            logger.info("[Telegram] Trade açılış bildirimi gönderildi: %s", trade.symbol)
        except Exception as _tg_err:
            logger.warning("[Telegram] Trade açılış bildirimi hatası: %s", _tg_err)

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

        logger.info(
            "Trade kapatıldı: #%s %s %s → %s  PnL=%.4f (accumulated=%.4f + remaining=%.4f)",
            trade["id"], trade["symbol"], trade.get("direction") or trade.get("side", "?"), reason,
            total_pnl, accumulated, remaining_pnl,
        )

        try:
            from telegram_delivery import send_trade_close as _tg_close2
            from database import get_paper_balance as _gpb2
            _bal2 = _gpb2()
            _entry2 = float(trade.get("entry") or trade.get("entry_price") or 1)
            _sl2    = float(trade.get("sl") or trade.get("stop_loss") or 0)
            _sl_dist2 = abs(_entry2 - _sl2) if _sl2 else 1e-10
            _qty2     = float(trade.get("original_qty") or trade.get("qty") or 1)
            _r2       = round(total_pnl / (_sl_dist2 * _qty2 + 1e-10), 2) if _sl2 else 0
            _open2    = trade.get("open_time", "")
            _dur2     = ""
            try:
                from datetime import datetime, timezone as _tz
                _opened2 = datetime.fromisoformat(_open2.replace("Z", "+00:00"))
                _hold2   = (datetime.now(_tz.utc) - _opened2).total_seconds() / 60
                _dur2    = f"{int(_hold2 // 60)}s {int(_hold2 % 60)}dk" if _hold2 >= 60 else f"{int(_hold2)}dk"
            except Exception:
                pass
            _tg_close2(
                symbol=trade["symbol"],
                net_pnl=total_pnl,
                total_fee=float(trade.get("total_fee") or 0),
                reason=reason,
                duration_str=_dur2,
                direction=trade.get("direction") or trade.get("side", ""),
                r_multiple=_r2,
                balance_after=_bal2,
            )
            logger.info("[Telegram] Trade kapanış bildirimi gönderildi: %s", trade["symbol"])
        except Exception as _tg_err2:
            logger.warning("[Telegram] Trade kapanış bildirimi hatası: %s", _tg_err2)

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
            opened_dt = datetime.fromisoformat(opened.replace("Z", "+00:00"))
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
    try:
        ticker = client.futures_ticker(symbol=symbol)
        return float(ticker["lastPrice"])
    except Exception:
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
    from core.accounting import calculate_runner_unrealized_pnl
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
    remaining_qty = t.get("qty_runner") or (qty - (t.get("qty_tp1") or 0) - (t.get("qty_tp2") or 0))
    if status in ("runner",) and remaining_qty > 0:
        unreal = calculate_runner_unrealized_pnl(direction, entry, price, remaining_qty)
        update_trade(trade_id, {"unrealized_pnl": unreal, "current_price": price})
        if event_manager: event_manager.broadcast_pnl_update(get_paper_balance(), unreal, t.get("realized_pnl", 0))

    # ── SL Kontrolü ─────────────────────────────────────────────────────────
    sl_hit = (is_long and price <= sl) or (not is_long and price >= sl)
    if sl_hit:
        pnl = _calc_pnl(direction, entry, price, qty)
        save_trade_event(trade_id, "SL_HIT", f"price={price} pnl={pnl}")
        _finalize(trade_id, price, pnl, "sl", t)
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
                "stop_loss":    round(new_sl, 6),  # BUG FIX: DB kolonu
                "sl":           round(new_sl, 6),  # compat
            })
            update_paper_balance(pnl_tp1)
            save_trade_event(trade_id, "TP1_HIT", f"price={tp1} pnl={pnl_tp1:.4f} new_sl={new_sl:.6f}")
            try:
                from telegram_delivery import send_tp_hit
                _bal_tp1 = get_paper_balance()
                _remaining_tp1 = qty - qty_tp1
                send_tp_hit(symbol, 1, pnl_tp1, _remaining_tp1, _bal_tp1)
                logger.info(f"[Telegram] TP1 bildirimi: {symbol} +{pnl_tp1:.4f}$")
            except Exception as _tg_tp1:
                logger.debug(f"[Telegram] TP1 bildirim hatası: {_tg_tp1}")
            if event_manager: event_manager.broadcast_live_update(get_open_trades())
            if event_manager: event_manager.broadcast_pnl_update(get_paper_balance(), t.get("unrealized_pnl", 0), t.get("realized_pnl", 0) + pnl_tp1)

    # ── TP2 Kontrolü ────────────────────────────────────────────────────────
    if status == "tp1_hit":
        tp2_hit = (is_long and price >= tp2) or (not is_long and price <= tp2)
        if tp2_hit:
            pnl_tp2 = _calc_pnl(direction, entry, tp2, qty_tp2)
            realized = (t.get("realized_pnl") or 0) + pnl_tp2
            # Runner'ı başlat — trail stop koy
            atr_val = _get_atr(client, symbol)
            if is_long:
                new_trail = tp2 - atr_val * TRAIL_ATR_MULT
            else:
                new_trail = tp2 + atr_val * TRAIL_ATR_MULT
            update_trade(trade_id, {
                "status":       "runner",
                "tp2_hit":      1,
                "realized_pnl": realized,
                "trail_stop":   new_trail,
                "stop_loss":    entry,  # BUG FIX: DB kolonu
                "sl":           entry,  # compat
            })
            update_paper_balance(pnl_tp2)
            save_trade_event(trade_id, "TP2_HIT", f"price={tp2} pnl={pnl_tp2:.4f} trail={new_trail:.6f}")
            try:
                from telegram_delivery import send_tp_hit
                _bal_tp2 = get_paper_balance()
                _runner_qty = qty - qty_tp1 - qty_tp2
                send_tp_hit(symbol, 2, pnl_tp2, _runner_qty, _bal_tp2)
                logger.info(f"[Telegram] TP2 bildirimi: {symbol} +{pnl_tp2:.4f}$ → RUNNER")
            except Exception as _tg_tp2:
                logger.debug(f"[Telegram] TP2 bildirim hatası: {_tg_tp2}")
            if event_manager: event_manager.broadcast_live_update(get_open_trades())
            if event_manager: event_manager.broadcast_pnl_update(get_paper_balance(), t.get("unrealized_pnl", 0), realized)
            logger.info(f"[Execution] TP2 #{trade_id} {symbol} +{pnl_tp2:.3f}$ → RUNNER trail={new_trail:.6f}")
            return False

    return False


# ─────────────────────────────────────────────────────────────────────────────
# YARDIMCILAR
# ─────────────────────────────────────────────────────────────────────────────

def _calc_pnl(direction: str, entry: float, exit_price: float, qty: float) -> float:
    """Paper trade PnL hesabı (USD, kaldıraçsız)."""
    if direction == "LONG":
        pnl = (exit_price - entry) * qty
    else:
        pnl = (entry - exit_price) * qty
    return round(pnl, 4)


def _get_atr(client, symbol: str, interval: str = "5m", period: int = 14) -> float:
    """Trailing stop için anlık ATR."""
    try:
        import pandas as pd
        klines = client.futures_klines(symbol=symbol, interval=interval, limit=period + 5)
        df = pd.DataFrame(klines, columns=[
            "time","open","high","low","close","volume",
            "ct","qav","nt","tbbav","tbqav","ignore"
        ])
        for col in ("high","low","close"):
            df[col] = df[col].astype(float)
        tr = pd.concat([
            df["high"] - df["low"],
            (df["high"] - df["close"].shift()).abs(),
            (df["low"]  - df["close"].shift()).abs(),
        ], axis=1).max(axis=1)
        return float(tr.rolling(period).mean().iloc[-1])
    except Exception:
        return 0.01


def _finalize(trade_id: int, close_price: float, net_pnl: float,
              reason: str, t: dict):
    """Trade'i kapat, bakiyeyi güncelle."""
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
        opened   = datetime.fromisoformat(open_t.replace("Z", "+00:00"))
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
    update_paper_balance(net_pnl - (t.get("realized_pnl") or 0))
    _dir = t.get("direction") or t.get("side", "LONG")
    if event_manager: event_manager.broadcast_trade_closed(t["symbol"], _dir, net_pnl, reason)
    if event_manager: event_manager.broadcast_pnl_update(get_paper_balance(), 0, net_pnl)
    save_trade_event(trade_id, "CLOSE", f"reason={reason} close_price={close_price} net_pnl={net_pnl:.4f}")

    result = "WIN" if net_pnl > 0 else "LOSS"

    # Live Tracker Postmortem Analizi
    try:
        from live_tracker import record_close as _record_close
    except ImportError:
        def _record_close(*args, **kwargs): pass
    try:
        _record_close(trade_id, close_price, reason)
    except Exception as e:
        logger.warning(f"Live tracker record_close hatası: {e}")

    # ── AI Öğrenme Döngüsü — Eksik 2 Düzeltmesi ──────────────────────────────
    # Her kapanan trade AI'ın Markov, heatmap ve parametre optimizasyonunu besler
    try:
        from core.ai_decision_engine import AIDecisionEngine
        from config import DB_PATH
        ai_engine = AIDecisionEngine(db_path=DB_PATH)
        setup_quality = t.get("setup_quality") or t.get("quality") or "B"
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
        from coin_library import update_coin_stats as _update_coin_stats
    except ImportError:
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
    # ── AI Brain Postmortem Analizi ───────────────────────────────────────────
    try:
        import threading
        from ai_brain import post_trade_analysis
        threading.Thread(
            target=post_trade_analysis,
            args=(trade_id,),
            daemon=True
        ).start()
    except Exception as e:
        logger.warning(f"AI Brain post_trade_analysis hatası: {e}")
    try:
        from telegram_delivery import send_trade_close as _tg_close
        _dur_str = f"{int(hold_min // 60)}s {int(hold_min % 60)}dk" if hold_min >= 60 else f"{int(hold_min)}dk"
        _bal_close = get_paper_balance()
        _entry_p = float(t.get("entry") or t.get("entry_price") or 1)
        _sl_p    = float(t.get("sl") or t.get("stop_loss") or 0)
        _sl_dist = abs(_entry_p - _sl_p) if _sl_p else 1e-10
        _qty_all = float(t.get("original_qty") or t.get("qty") or 1)
        _r_mult  = round(net_pnl / (_sl_dist * _qty_all + 1e-10), 2) if _sl_p else 0
        _tg_close(
            symbol=t["symbol"],
            net_pnl=net_pnl,
            total_fee=float(t.get("total_fee") or t.get("fee") or 0),
            reason=reason,
            duration_str=_dur_str,
            direction=t.get("direction") or t.get("side", ""),
            r_multiple=_r_mult,
            balance_after=_bal_close,
        )
        logger.info(f"[Telegram] Kapanış bildirimi: {t['symbol']} {'+' if net_pnl >= 0 else ''}{net_pnl:.3f}$")
    except Exception as _tg_close_err:
        logger.warning(f"[Telegram] Kapanış bildirim hatası: {_tg_close_err}")
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
        balance  = database.get_paper_balance() or 250.0
        trade    = build_trade_from_signal(sig, balance, config.DEFAULT_FEE_RATE, config.MAX_LEVERAGE)
        if trade is None:
            return None
        trade_id = database.create_trade(trade)
        if trade_id:
            logger.info("open_trade: #%s %s %s @ %.4f", trade_id, sig.symbol, sig.side, sig.entry_price)
        return trade_id
    except Exception as e:
        logger.error("open_trade hata: %s", e)
        return None
