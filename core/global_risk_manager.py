"""
core/global_risk_manager.py – Portfolio Circuit Breaker (Kill Switch)
=====================================================================
Kasanın genel durumunu sürekli izler. Belirlenen Drawdown (maksimum günlük kayıp)
oranı aşılırsa veya sistem hataları tavan yaparsa, tüm işlemleri durdurur ve
KILL_SWITCH_ACTIVATED sinyali gönderir.
"""

import asyncio
import logging
from datetime import datetime, timezone
import config
from database import get_paper_balance, get_dashboard_stats
from core.event_bus import event_bus
from core.event_types import Event, EventType

logger = logging.getLogger("ax.global_risk")

class GlobalRiskManager:
    def __init__(self, drawdown_limit_pct: float = 5.0):
        self.drawdown_limit_pct = drawdown_limit_pct
        self.running = False
        self.start_balance = 0.0
        self.is_killed = False

    async def start(self):
        """Risk izleme döngüsünü başlatır."""
        self.running = True
        
        # Gün başlangıcındaki bakiyeyi al (şimdilik anlık alıyoruz, daha iyisi günlük ilk açılışta almak)
        stats = get_dashboard_stats()
        self.start_balance = stats.get("balance", config.INITIAL_PAPER_BALANCE if hasattr(config, "INITIAL_PAPER_BALANCE") else 2000.0)
        
        logger.info(f"Global Risk Manager başlatıldı. Başlangıç Bakiyesi: ${self.start_balance}, DD Limiti: %{self.drawdown_limit_pct}")
        
        asyncio.create_task(self._monitor_loop())

    async def _monitor_loop(self):
        while self.running:
            try:
                if self.is_killed:
                    await asyncio.sleep(60)
                    continue

                current_balance = get_paper_balance()
                if self.start_balance > 0:
                    drawdown = ((self.start_balance - current_balance) / self.start_balance) * 100
                    
                    if drawdown >= self.drawdown_limit_pct:
                        logger.critical(f"KÜRESEL DEVRE KESİCİ (KILL SWITCH) TETİKLENDİ! Günlük Kayıp: %{drawdown:.2f}")
                        self.is_killed = True
                        await self.activate_kill_switch(drawdown, current_balance)
                
                await asyncio.sleep(10)  # Her 10 saniyede bir portföy kontrolü
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Global Risk Manager Error: {e}")
                await asyncio.sleep(5)

    async def activate_kill_switch(self, drawdown: float, balance: float):
        """Kill switch'i aktif eder, event_bus'a bildirir."""
        # Burada gerekirse CCXT üzerinden cancel_all_orders komutu atılabilir
        event = Event(
            type=EventType.KILL_SWITCH_ACTIVATED,
            payload={
                "drawdown_pct": drawdown,
                "current_balance": balance,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        )
        await event_bus.publish(event)
        
        # Ek olarak telegram vb. acil bildirim
        try:
            from telegram_delivery import TelegramDelivery
            tg = TelegramDelivery()
            tg.send_message(f"🚨 <b>KÜRESEL DEVRE KESİCİ AKTİF!</b> 🚨\nSistem kendini kilitledi.\nKayıp: %{drawdown:.2f}\nBakiye: ${balance:.2f}")
        except Exception as e:
            logger.debug(f"Telegram kill switch bildirimi atılamadı: {e}")

    def stop(self):
        self.running = False
