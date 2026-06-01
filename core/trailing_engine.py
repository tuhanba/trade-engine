"""
core/trailing_engine.py — AX Trailing Engine v5.0 (Production)
=================================================================
Production-grade multi-TP trailing stop sistemi.

Özellikler:
  - TP1/TP2/TP3 kademeli partial close
  - ATR-bazlı ve callback-rate-bazlı trailing
  - Breakeven otomasyonu (TP1 vurulunca SL → entry)
  - State-sync: her trade kendi bağımsız durumunu taşır
  - Partial close PnL hesabı (accounting.py ile entegre)
  - Crash-safe: her hata loglanır, trade kapatılmaz
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime, timezone

logger = logging.getLogger("ax.trailing_engine")


# ── Config defaults (config.py'den override edilir) ──────────────────

try:
    from config import (
        TP1_CLOSE_PCT,
        TP2_CLOSE_PCT,
        RUNNER_CLOSE_PCT,
        TRAIL_ATR_MULT,
        BREAKEVEN_ENABLED,
        BREAKEVEN_OFFSET_PCT,
    )
except ImportError:
    TP1_CLOSE_PCT = 40
    TP2_CLOSE_PCT = 30
    RUNNER_CLOSE_PCT = 30
    TRAIL_ATR_MULT = 1.5
    BREAKEVEN_ENABLED = True
    BREAKEVEN_OFFSET_PCT = 0.05


# ── Trade Exit State ─────────────────────────────────────────────────

@dataclass
class TradeExitState:
    """
    Bir trade'in çıkış durumunu takip eder.
    DB'de metadata JSON alanında saklanır.
    """
    tp1_hit: bool = False
    tp2_hit: bool = False
    tp3_hit: bool = False
    breakeven_set: bool = False
    trailing_active: bool = False
    current_sl: float = 0.0      # Güncel trailing SL seviyesi
    highest_price: float = 0.0   # LONG için en yüksek, SHORT için en düşük
    qty_remaining_pct: float = 100.0  # Kalan pozisyon yüzdesi
    initial_sl: float = 0.0      # İlk stop loss seviyesi
    is_scalp: bool = False       # Scalp trade olup olmadığı

    def to_dict(self) -> dict:
        return {
            "tp1_hit": self.tp1_hit,
            "tp2_hit": self.tp2_hit,
            "tp3_hit": self.tp3_hit,
            "breakeven_set": self.breakeven_set,
            "trailing_active": self.trailing_active,
            "current_sl": self.current_sl,
            "highest_price": self.highest_price,
            "qty_remaining_pct": self.qty_remaining_pct,
            "initial_sl": self.initial_sl,
            "is_scalp": self.is_scalp,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TradeExitState":
        return cls(
            tp1_hit=d.get("tp1_hit", False),
            tp2_hit=d.get("tp2_hit", False),
            tp3_hit=d.get("tp3_hit", False),
            breakeven_set=d.get("breakeven_set", False),
            trailing_active=d.get("trailing_active", False),
            current_sl=float(d.get("current_sl", 0.0)),
            highest_price=float(d.get("highest_price", 0.0)),
            qty_remaining_pct=float(d.get("qty_remaining_pct", 100.0)),
            initial_sl=float(d.get("initial_sl", 0.0)),
            is_scalp=d.get("is_scalp", False),
        )


# ── Partial Close Result ─────────────────────────────────────────────

@dataclass
class PartialCloseResult:
    """Partial close işleminin sonucu."""
    should_partial_close: bool = False
    close_pct: float = 0.0          # Kapatılacak pozisyon yüzdesi
    close_at_price: float = 0.0
    reason: str = ""
    new_sl: Optional[float] = None  # Yeni SL seviyesi (varsa)
    should_full_close: bool = False  # Tam kapatma gerekiyor mu?
    full_close_reason: str = ""


# ── Ana Trailing Engine ──────────────────────────────────────────────

class TrailingEngine:
    """
    Production-grade multi-TP trailing stop motoru.
    
    Kullanım:
        engine = TrailingEngine()
        state = TradeExitState(current_sl=float(trade.get("sl") or trade.get("stop_loss") or 0))
        result = engine.evaluate(trade, current_price, state, atr)
        if result.should_partial_close:
            # Partial close işlemi yap
        if result.should_full_close:
            # Full close işlemi yap
    """

    def __init__(
        self,
        tp1_close_pct: float = TP1_CLOSE_PCT,
        tp2_close_pct: float = TP2_CLOSE_PCT,
        runner_close_pct: float = RUNNER_CLOSE_PCT,
        trail_atr_mult: float = TRAIL_ATR_MULT,
        breakeven_enabled: bool = BREAKEVEN_ENABLED,
        breakeven_offset_pct: float = BREAKEVEN_OFFSET_PCT,
    ):
        self.tp1_close_pct = tp1_close_pct
        self.tp2_close_pct = tp2_close_pct
        self.runner_close_pct = runner_close_pct
        self.trail_atr_mult = trail_atr_mult
        self.breakeven_enabled = breakeven_enabled
        self.breakeven_offset_pct = breakeven_offset_pct / 100.0

    def evaluate(
        self,
        trade: dict,
        current_price: float,
        state: TradeExitState,
        atr: Optional[float] = None,
    ) -> PartialCloseResult:
        """
        Trade durumunu değerlendirir ve çıkış aksiyonu belirler.
        
        Args:
            trade: DB'den gelen trade dict
            current_price: Güncel piyasa fiyatı
            state: Trade'in mevcut exit state'i
            atr: Güncel ATR değeri (trailing için kullanılır)
        
        Returns:
            PartialCloseResult ile aksiyon talimatları
        """
        try:
            return self._evaluate_internal(trade, current_price, state, atr)
        except Exception as exc:
            logger.error(
                "TrailingEngine evaluate hatası [#%s %s]: %s",
                trade.get("id"), trade.get("symbol"), exc,
            )
            return PartialCloseResult()

    def _evaluate_internal(
        self,
        trade: dict,
        current_price: float,
        state: TradeExitState,
        atr: Optional[float],
    ) -> PartialCloseResult:
        side = (trade.get("direction") or trade.get("side", "LONG")).upper()
        entry = float(trade.get("entry") or trade.get("entry_price") or 0)
        sl = float(trade.get("sl") or trade.get("stop_loss") or 0)
        tp1 = float(trade.get("tp1") or 0)
        tp2 = float(trade.get("tp2") or 0)
        tp3 = float(trade.get("tp3") or 0)
        trade_id = trade.get("id")
        symbol = trade.get("symbol", "?")

        if current_price <= 0 or entry <= 0:
            return PartialCloseResult()

        # Kullanılacak SL: state'deki güncel SL (trailing hareket etmiş olabilir)
        active_sl = state.current_sl if state.current_sl > 0 else sl

        # ── Time-Decay Stop Loss Tightening ─────────────────────────
        import config
        if getattr(config, "TIME_DECAY_ENABLED", True):
            opened = trade.get("open_time", "") or trade.get("opened_at", "")
            if opened:
                try:
                    if isinstance(opened, str):
                        dt = datetime.fromisoformat(opened.replace("Z", "+00:00"))
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        opened_dt = dt
                    else:
                        opened_dt = opened
                    
                    now_dt = datetime.now(timezone.utc)
                    elapsed = (now_dt - opened_dt).total_seconds() / 60.0
                    
                    if state.is_scalp:
                        decay_start = getattr(config, "SCALP_TIME_DECAY_START_MINUTES", 5)
                        decay_be = getattr(config, "SCALP_TIME_DECAY_BREAKEVEN_MINUTES", 15)
                    else:
                        decay_start = getattr(config, "TIME_DECAY_START_MINUTES", 45)
                        decay_be = getattr(config, "TIME_DECAY_BREAKEVEN_MINUTES", 105)
                        
                    if elapsed > decay_start:
                        initial_sl = state.initial_sl
                        if initial_sl == 0.0:
                            initial_sl = sl
                            state.initial_sl = initial_sl
                            
                        if initial_sl > 0 and entry > 0:
                            duration = float(decay_be - decay_start)
                            if duration > 0:
                                decay_factor = min(1.0, (elapsed - decay_start) / duration)
                                computed_sl = initial_sl + (entry - initial_sl) * decay_factor
                                
                                if side == "LONG":
                                    if computed_sl > active_sl:
                                        logger.info(
                                            "[Trailing] Time Decay SL (LONG): #%s %s elapsed=%.1fm, "
                                            "Initial SL=%.4f, Entry=%.4f, Active SL=%.4f -> Decayed SL=%.4f",
                                            trade_id, symbol, elapsed, initial_sl, entry, active_sl, computed_sl
                                        )
                                        active_sl = computed_sl
                                        state.current_sl = computed_sl
                                else:  # SHORT
                                    if active_sl == 0 or computed_sl < active_sl:
                                        logger.info(
                                            "[Trailing] Time Decay SL (SHORT): #%s %s elapsed=%.1fm, "
                                            "Initial SL=%.4f, Entry=%.4f, Active SL=%.4f -> Decayed SL=%.4f",
                                            trade_id, symbol, elapsed, initial_sl, entry, active_sl, computed_sl
                                        )
                                        active_sl = computed_sl
                                        state.current_sl = computed_sl
                except Exception as _e:
                    logger.error(f"[Trailing] Error in Time-Decay calculations: {_e}")

        # ── 1. SL vuruldu mu? ────────────────────────────────────────
        if self._sl_hit(side, current_price, active_sl):
            logger.info(
                "[Trailing] SL vuruldu: #%s %s @ %.4f (SL=%.4f)",
                trade_id, symbol, current_price, active_sl,
            )
            return PartialCloseResult(
                should_full_close=True,
                close_at_price=current_price,
                full_close_reason="STOP_LOSS",
            )

        # ── 2. Highest price güncelle ────────────────────────────────
        if side == "LONG":
            if current_price > state.highest_price:
                state.highest_price = current_price
        else:
            if state.highest_price == 0 or current_price < state.highest_price:
                state.highest_price = current_price

        # ── 3. TP1 kontrolü ─────────────────────────────────────────
        if tp1 > 0 and not state.tp1_hit and self._tp_hit(side, current_price, tp1):
            state.tp1_hit = True
            new_sl = active_sl  # Default: SL değişmez

            # Breakeven: TP1 vurulunca SL entry'e çekilir
            if self.breakeven_enabled and entry > 0:
                be_offset = entry * self.breakeven_offset_pct
                if side == "LONG":
                    new_sl = max(active_sl, entry + be_offset)
                else:
                    new_sl = min(active_sl, entry - be_offset) if active_sl > 0 else entry - be_offset
                state.breakeven_set = True
                logger.info(
                    "[Trailing] TP1 hit → Breakeven SL: #%s %s  SL=%.4f → %.4f",
                    trade_id, symbol, active_sl, new_sl,
                )

            state.current_sl = new_sl
            state.qty_remaining_pct -= self.tp1_close_pct

            logger.info(
                "[Trailing] TP1 vuruldu: #%s %s @ %.4f  Partial close %s%%",
                trade_id, symbol, current_price, self.tp1_close_pct,
            )
            return PartialCloseResult(
                should_partial_close=True,
                close_pct=self.tp1_close_pct,
                close_at_price=current_price,
                reason="TP1_HIT",
                new_sl=new_sl,
            )

        # ── 4. TP2 kontrolü ─────────────────────────────────────────
        if tp2 > 0 and state.tp1_hit and not state.tp2_hit and self._tp_hit(side, current_price, tp2):
            state.tp2_hit = True
            state.trailing_active = True  # TP2 sonrası trailing başlar

            # ATR-bazlı trailing stop
            if atr and atr > 0:
                trail_distance = atr * self.trail_atr_mult
                if side == "LONG":
                    new_sl = max(state.current_sl, current_price - trail_distance)
                else:
                    new_sl = min(state.current_sl, current_price + trail_distance) if state.current_sl > 0 else current_price + trail_distance
            else:
                new_sl = state.current_sl

            state.current_sl = new_sl
            state.qty_remaining_pct -= self.tp2_close_pct

            logger.info(
                "[Trailing] TP2 vuruldu: #%s %s @ %.4f  Trailing aktif, SL=%.4f",
                trade_id, symbol, current_price, new_sl,
            )
            return PartialCloseResult(
                should_partial_close=True,
                close_pct=self.tp2_close_pct,
                close_at_price=current_price,
                reason="TP2_HIT",
                new_sl=new_sl,
            )

        # ── 5. TP3 / Runner kontrolü ─────────────────────────────────
        if tp3 > 0 and state.tp2_hit and not state.tp3_hit and self._tp_hit(side, current_price, tp3):
            state.tp3_hit = True
            state.qty_remaining_pct -= self.runner_close_pct

            logger.info(
                "[Trailing] TP3 vuruldu: #%s %s @ %.4f  Runner kapatılıyor",
                trade_id, symbol, current_price,
            )
            return PartialCloseResult(
                should_full_close=True,
                close_at_price=current_price,
                full_close_reason="TP3_HIT",
            )

        # ── 6. Aktif Trailing Stop güncellemesi ──────────────────────
        if state.trailing_active and atr and atr > 0:
            trail_distance = atr * self.trail_atr_mult
            if side == "LONG":
                new_trail_sl = current_price - trail_distance
                if new_trail_sl > state.current_sl:
                    state.current_sl = new_trail_sl
                    logger.debug(
                        "[Trailing] SL güncellendi: #%s %.4f → %.4f",
                        trade_id, active_sl, new_trail_sl,
                    )
            else:
                new_trail_sl = current_price + trail_distance
                if state.current_sl == 0 or new_trail_sl < state.current_sl:
                    state.current_sl = new_trail_sl

        # ── 7. Runner SL vuruldu mu? (trailing devredeyken) ─────────
        if state.trailing_active and state.current_sl > 0:
            if self._sl_hit(side, current_price, state.current_sl):
                logger.info(
                    "[Trailing] Trailing SL vuruldu: #%s %s @ %.4f (TSL=%.4f)",
                    trade_id, symbol, current_price, state.current_sl,
                )
                return PartialCloseResult(
                    should_full_close=True,
                    close_at_price=current_price,
                    full_close_reason="TRAILING_SL",
                )

        # ── 8. Max hold time kontrolü (timeout) ──────────────────────
        # Bu kontrol execution_engine.py tarafından yapılır.

        if state.current_sl > 0 and state.current_sl != sl:
            return PartialCloseResult(new_sl=state.current_sl)

        return PartialCloseResult()  # Aksiyon yok

    # ── Yardımcı metodlar ────────────────────────────────────────────

    @staticmethod
    def _tp_hit(side: str, price: float, tp: float) -> bool:
        """TP hedefine ulaşıldı mı?"""
        if tp <= 0:
            return False
        if side == "LONG":
            return price >= tp
        return price <= tp

    @staticmethod
    def _sl_hit(side: str, price: float, sl: float) -> bool:
        """SL seviyesi vuruldu mu?"""
        if sl <= 0:
            return False
        if side == "LONG":
            return price <= sl
        return price >= sl

    @staticmethod
    def calculate_trailing_stop(
        current_price: float,
        direction: str,
        entry_price: float,
        current_sl: float,
        activation_price: float,
        callback_rate: float = 0.01,
    ) -> float:
        """
        Callback-rate bazlı trailing stop hesaplar (eski API uyumu).
        ATR yoksa bu kullanılır.
        """
        if direction == "LONG":
            if current_price >= activation_price:
                new_sl = current_price * (1 - callback_rate)
                return max(current_sl, new_sl)
        else:
            if current_price <= activation_price:
                new_sl = current_price * (1 + callback_rate)
                return min(current_sl, new_sl) if current_sl > 0 else new_sl
        return current_sl

    @staticmethod
    def check_tp_hit(current_price: float, direction: str, tp_price: float) -> bool:
        """TP kontrolü (eski API uyumu)."""
        if direction == "LONG":
            return current_price >= tp_price
        return current_price <= tp_price
