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
        self.telegram.send_trade_open({
            "symbol": trade.symbol,
            "side": trade.side,
            "entry_price": trade.entry_price,
            "stop_loss": trade.stop_loss,
            "tp1": trade.tp1 or 0,
            "tp2": trade.tp2 or 0,
            "tp3": trade.tp3 or 0,
            "leverage": trade.leverage,
            "risk_pct": trade.risk_pct,
            "risk_usd": trade.risk_usd,
            "margin_used": trade.margin_used,
            "notional": trade.notional,
        })

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
            side=trade["side"],
            entry_price=trade["entry_price"],
            current_price=current,
            quantity=trade["quantity"],
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
        if result.new_sl and result.new_sl != (state.current_sl or trade.get("stop_loss", 0)):
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
        qty_to_close = trade["quantity"] * close_pct

        partial_pnl = calculate_realized_pnl(
            side=trade["side"],
            entry_price=trade["entry_price"],
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
            f"#{trade_id} {symbol} {trade['side']}\n"
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
        rpnl = calculate_realized_pnl(
            side=trade["side"],
            entry_price=trade["entry_price"],
            exit_price=exit_price,
            quantity=trade["quantity"],
            fee_rate=config.DEFAULT_FEE_RATE,
        )

        # Eğer partial close'lar olduysa toplam PnL düzeltilir
        # (database.py zaten accumulated partial_pnl'i saklıyor)
        accumulated = trade.get("accumulated_pnl", 0.0) or 0.0
        remaining_qty_pct = trade.get("remaining_qty_pct", 100.0) or 100.0
        
        # Kalan kısım için PnL
        remaining_qty = trade["quantity"] * (remaining_qty_pct / 100.0)
        remaining_pnl = calculate_realized_pnl(
            side=trade["side"],
            entry_price=trade["entry_price"],
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
            trade["id"], trade["symbol"], trade["side"], reason,
            total_pnl, accumulated, remaining_pnl,
        )

        self.telegram.send_trade_close({
            "symbol": trade["symbol"],
            "side": trade["side"],
            "exit_price": exit_price,
            "realized_pnl": total_pnl,
            "close_reason": reason,
        })

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
            opened = trade.get("opened_at", "")
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
            current_sl=float(trade.get("stop_loss", 0) or 0),
            highest_price=float(trade.get("entry_price", 0) or 0),
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
            entry = float(trade.get("entry_price", 0))
            sl = float(trade.get("stop_loss", 0))
            if entry > 0 and sl > 0:
                return abs(entry - sl)
        except Exception:
            pass
        return None
