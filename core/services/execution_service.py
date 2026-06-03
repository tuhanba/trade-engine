"""
core/services/execution_service.py — Execution Aşaması v6.0

Değişiklikler:
  - Score threshold altı kalınca signal_events'e EXECUTION_REJECTED yaz.
  - Trade başarıyla açılınca signal_events'e EXECUTED yaz.
  - candidate_id'yi her iki durumda da güncelle.
  - Monitoring loop: TRADE_CLOSED event'ini düzgün duration ile yayınla.
"""

import logging
import asyncio
import database
from core.event_bus import event_bus
from core.event_types import Event, EventType
from execution_engine import ExecutionEngine
from core.data_layer import SignalData

logger = logging.getLogger("ax.services.execution")


class ExecutionService:
    def __init__(self):
        self.execution_engine = ExecutionEngine()
        self._trade_lock  = asyncio.Lock()
        self._monitor_task = None
        event_bus.subscribe(EventType.AI_VALIDATED, self.handle_ai_validated)

    async def start(self):
        """Monitoring loop — async_scalp_engine.py'den çağrılır."""
        self._monitor_task = asyncio.create_task(self._monitoring_loop())

    # ── Monitoring Loop ───────────────────────────────────────────────────────

    async def _monitoring_loop(self):
        """Açık trade'leri izle, kapananları TRADE_CLOSED event'i ile yayınla."""
        prev_open_ids: set[int] = set()
        while True:
            try:
                await asyncio.to_thread(self.execution_engine.update_open_trades)

                current_open = await asyncio.to_thread(database.get_open_trades)
                current_ids  = {t["id"] for t in current_open}
                closed_ids   = prev_open_ids - current_ids

                for trade_id in closed_ids:
                    closed = await asyncio.to_thread(database.get_trade_by_id, trade_id)
                    if not closed:
                        continue

                    # Süre hesabı
                    duration_str = ""
                    try:
                        from datetime import datetime, timezone as _tz
                        from execution_engine import parse_utc_datetime
                        opened_dt = parse_utc_datetime(closed.get("open_time", ""))
                        closed_dt = parse_utc_datetime(closed.get("close_time", "") or
                                                       datetime.now(_tz.utc).isoformat())
                        mins = int((closed_dt - opened_dt).total_seconds() / 60)
                        duration_str = f"{mins}dk" if mins < 60 else f"{mins // 60}s{mins % 60}dk"
                    except Exception:
                        pass

                    _entry = float(closed.get("entry") or closed.get("entry_price") or 1)
                    _sl    = float(closed.get("sl") or closed.get("stop_loss") or 1)
                    _sl_d  = max(abs(_entry - _sl), 1e-8)
                    _pnl   = float(closed.get("net_pnl") or 0)
                    _risk_usd = float(closed.get("risk_usd") or 0)
                    if _risk_usd <= 0:
                        _qty = float(closed.get("qty") or closed.get("quantity") or 0)
                        _risk_usd = _qty * _sl_d if _qty > 0 else _sl_d
                    _r     = round(_pnl / _risk_usd, 3) if _risk_usd > 0 else 0

                    balance = await asyncio.to_thread(database.get_active_balance)

                    await event_bus.publish(Event(
                        type=EventType.TRADE_CLOSED,
                        payload={
                            "trade_id":     trade_id,
                            "symbol":       closed.get("symbol"),
                            "direction":    closed.get("direction"),
                            "net_pnl":      _pnl,
                            "reason":       closed.get("close_reason", "unknown"),
                            "r_multiple":   _r,
                            "duration":     duration_str,
                            "balance_after": balance,
                            "total_fee":    float(closed.get("total_fee") or 0),
                        }
                    ))
                    logger.info("[ExecutionService] TRADE_CLOSED #%d %s pnl=%.4f",
                                trade_id, closed.get("symbol"), _pnl)

                prev_open_ids = current_ids

            except Exception as e:
                logger.error("[ExecutionService] monitor loop: %s", e)

            await asyncio.sleep(1)

    # ── AI Validated Handler ──────────────────────────────────────────────────

    async def handle_ai_validated(self, event: Event):
        async with self._trade_lock:
            await self._execute_trade(event)

    async def _execute_trade(self, event: Event):
        payload      = event.payload
        symbol       = payload.get("symbol")
        signal_dict  = payload.get("signal_data")
        signal_id    = payload.get("signal_id")
        candidate_id = payload.get("candidate_id")

        try:
            import config
            is_scalp  = not getattr(config, "HUMAN_MODE", False)
            trade_thr = (
                config.HUMAN_TRADE_THRESHOLD if not is_scalp
                else getattr(config, "TRADE_THRESHOLD", 55.0)
            )

            sig = SignalData.from_dict(signal_dict)

            # ML bonus — scalp modunda yüksek ML skoru eşiği düşürür
            ml_score = float(getattr(sig, "ml_score", 50.0) or 50.0)
            if is_scalp and ml_score >= 65:
                trade_thr -= 3.0
                logger.debug("[ExecutionService] %s ML bonus → thr=%.1f", symbol, trade_thr)

            qualities = getattr(config, "EXECUTABLE_QUALITIES", ("S", "A+", "A", "B", "C"))

            if sig.final_score >= trade_thr and sig.setup_quality in qualities:
                is_high_quality = sig.setup_quality in ("S", "A+") or sig.final_score >= 75.0
                auto_exec_high  = getattr(config, "CONFIRMATION_AUTO_EXECUTE_HIGH_QUALITY", True)
                
                if getattr(config, "CONFIRMATION_MODE", False) and not (is_high_quality and auto_exec_high):
                    if not candidate_id:
                        try:
                            from database import get_conn
                            with get_conn() as conn:
                                row = conn.execute(
                                    "SELECT id FROM signal_candidates WHERE symbol = ? ORDER BY id DESC LIMIT 1",
                                    (symbol,)
                                ).fetchone()
                                if row:
                                    candidate_id = row[0]
                        except Exception:
                            pass

                    logger.info("[ExecutionService] %s confirmation gate active. Awaiting manual confirmation for candidate %s.", symbol, candidate_id)
                    
                    if candidate_id:
                        try:
                            from database import update_candidate_status
                            await asyncio.to_thread(
                                update_candidate_status,
                                candidate_id,
                                decision="PENDING_APPROVAL",
                                reject_reason="Awaiting manual confirmation",
                            )
                        except Exception as _e:
                            logger.error("[ExecutionService] update_candidate_status error: %s", _e)
                            
                    try:
                        from database import save_signal_event
                        await asyncio.to_thread(
                            save_signal_event, signal_id, "PENDING_APPROVAL",
                            symbol=symbol, reject_reason="Awaiting manual confirmation",
                        )
                    except Exception as _e:
                        logger.error("[ExecutionService] save_signal_event error: %s", _e)

                    try:
                        import telegram_delivery
                        quality_emoji = {"S": "⭐", "A+": "🔥", "A": "⚡", "B": "🔶", "C": "⚪"}.get(sig.setup_quality, "⚪")
                        dir_icon = "▲" if sig.direction == "LONG" else "▼"
                        
                        alert_text = (
                            f"⏳ <b>İŞLEM ONAY BEKLİYOR</b>\n"
                            f"──────────────────────\n"
                            f"{quality_emoji} <b>{sig.setup_quality} Sinyal Adayı: {symbol} ({sig.direction})</b>\n"
                            f"──────────────────────\n"
                            f"📍 Giriş  <code>{sig.entry_price:.4f}</code>\n"
                            f"🛑 Stop   <code>{sig.stop_loss:.4f}</code>\n"
                            f"🎯 TP1    <code>{sig.tp1:.4f}</code>\n"
                            f"🎯 TP2    <code>{sig.tp2:.4f}</code>\n"
                            f"🚀 TP3    <code>{sig.tp3:.4f}</code>\n"
                            f"──────────────────────\n"
                            f"📊 Skor   <b>{sig.final_score:.1f}p</b>  ·  RR  <b>{sig.rr:.2f}R</b>\n"
                            f"💡 Sebep  <i>{sig.reason}</i>\n"
                            f"──────────────────────\n"
                            f"<i>Lütfen bu işlemi onaylayın veya iptal edin:</i>"
                        )
                        
                        reply_markup = {
                            "inline_keyboard": [
                                [
                                    {"text": "✅ İŞLEME AL", "callback_data": f"cmd:appr_cand_{candidate_id}"},
                                    {"text": "❌ İPTAL ET (VETO)", "callback_data": f"cmd:veto_cand_{candidate_id}"}
                                ]
                            ]
                        }
                        
                        await asyncio.to_thread(
                            telegram_delivery.send_message,
                            alert_text,
                            reply_markup=reply_markup
                        )
                    except Exception as tg_e:
                        logger.error("[ExecutionService] Telegram confirmation send error: %s", tg_e)
                    return  # Intercepted! Do not open trade yet.
                        
                if getattr(config, "CONFIRMATION_MODE", False) and is_high_quality and auto_exec_high:
                    logger.info("[ExecutionService] %s confirmation gate bypassed due to high quality/score (%s, %.1fp).",
                                symbol, sig.setup_quality, sig.final_score)

                # ── Trade Aç ─────────────────────────────────────────────────
                if config.EXECUTION_MODE == "live":
                    if not hasattr(self, "live_execution_engine"):
                        from core.live_execution import LiveExecutionEngine
                        self.live_execution_engine = LiveExecutionEngine()
                    trade_id = await asyncio.to_thread(
                        self.live_execution_engine.open_live_trade, sig
                    )
                else:
                    trade_id = await asyncio.to_thread(
                        self.execution_engine.process_signal, sig
                    )

                if trade_id:
                    # signal_events — EXECUTED
                    try:
                        from database import save_signal_event
                        await asyncio.to_thread(
                            save_signal_event, signal_id, "EXECUTED",
                            symbol=symbol, reject_reason=f"trade_id={trade_id}",
                        )
                    except Exception as _e:
                        logger.debug("[ExecutionService] EXECUTED signal_event: %s", _e)

                    # Candidate güncelle
                    if candidate_id:
                        try:
                            from database import update_candidate_status
                            await asyncio.to_thread(
                                update_candidate_status,
                                candidate_id,
                                decision="EXECUTED",
                                linked_trade_id=trade_id,
                            )
                        except Exception as _e:
                            logger.debug("[ExecutionService] candidate link: %s", _e)

                    # TRADE_OPENED event — NotificationService bu event'i dinliyor
                    trade_payload = {
                        "trade_id":      trade_id,
                        "symbol":        symbol,
                        "signal_id":     getattr(sig, "id", None),
                        "direction":     sig.direction,
                        "entry":         getattr(sig, "entry_price", 0) or getattr(sig, "entry_zone", 0),
                        "sl":            sig.stop_loss,
                        "tp1":           sig.tp1,
                        "tp2":           sig.tp2,
                        "tp3":           sig.tp3,
                        "leverage":      sig.leverage_suggestion or getattr(sig, "leverage", 10),
                        "risk_usd":      sig.max_loss,
                        "setup_quality": sig.setup_quality,
                        "final_score":   sig.final_score,
                        "rr":            sig.rr,
                        "risk_pct":      getattr(sig, "risk_percent", 0) or getattr(sig, "risk_pct", 0),
                        "position_size": sig.position_size,
                        "notional":      sig.notional_size,
                    }
                    await event_bus.publish(
                        Event(type=EventType.TRADE_OPENED, payload=trade_payload)
                    )
                    logger.info("[ExecutionService] %s trade açıldı: #%d", symbol, trade_id)

                else:
                    logger.warning("[ExecutionService] %s trade açılamadı (engine None döndü).", symbol)

            else:
                # ── Eşik Altı — EXECUTION_REJECTED ────────────────────────
                reject_reason = (
                    f"score_{sig.final_score:.1f}_below_{trade_thr:.1f}"
                    if sig.final_score < trade_thr
                    else f"quality_{sig.setup_quality}_not_executable"
                )
                logger.debug("[ExecutionService] %s reddedildi: %s", symbol, reject_reason)

                try:
                    from database import save_signal_event
                    await asyncio.to_thread(
                        save_signal_event, signal_id, "EXECUTION_REJECTED",
                        symbol=symbol, reject_reason=reject_reason,
                    )
                except Exception as _e:
                    logger.debug("[ExecutionService] EXECUTION_REJECTED signal_event: %s", _e)

                if candidate_id:
                    try:
                        from database import update_candidate_status
                        await asyncio.to_thread(
                            update_candidate_status,
                            candidate_id,
                            decision="EXECUTION_REJECTED",
                            reject_reason=reject_reason,
                        )
                    except Exception as _e:
                        logger.debug("[ExecutionService] candidate reject: %s", _e)

        except Exception as e:
            logger.error("[ExecutionService] %s execute hatası: %s", symbol, e)
