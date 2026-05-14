import os
import sys
import logging
import pandas as pd
from binance.client import Client
from config import *
from core.market_scanner import MarketScanner
from core.trend_engine import TrendEngine
from core.trigger_engine import TriggerEngine
from core.risk_engine import RiskEngine
from core.ai_decision_engine import AIDecisionEngine

# Logları kapat ki temiz çıktı alalım
logging.basicConfig(level=logging.CRITICAL)

def diagnose():
    print("=== TRADE ENGINE TEŞHİS RAPORU ===")
    client = Client(BINANCE_API_KEY, BINANCE_API_SECRET)
    scanner = MarketScanner(client)
    trend_engine = TrendEngine(client)
    trigger_engine = TriggerEngine(client)
    risk_engine = RiskEngine(client)
    ai_engine = AIDecisionEngine()

    print(f"\n1. MARKET SCANNER ANALİZİ")
    print(f"Coin Universe: {len(COIN_UNIVERSE)} coin")
    candidates = scanner.scan()
    print(f"Tarama Sonucu: {len(candidates)} coin temel filtreleri geçti (Hacim > 10M, Fiyat > 0.001)")
    
    if not candidates:
        print("KRİTİK: Hiçbir coin temel taramayı geçemedi!")
        return

    stats = {
        "total": len(candidates),
        "trend_passed": 0,
        "trigger_passed": 0,
        "risk_passed": 0,
        "ai_passed": 0,
        "reasons": {}
    }

    print(f"\n2. FİLTRE ZİNCİRİ ANALİZİ (İlk 10 aday üzerinde)")
    for c in candidates[:10]:
        symbol = c["symbol"]
        print(f"\n--- {symbol} ---")
        
        # Trend Check
        trend = trend_engine.analyze(symbol)
        if trend["direction"] == "NO TRADE":
            adx_val = trend.get('adx15')
            reason = f"Trend: No Trend/ADX Low (ADX={adx_val})"
            stats["reasons"][reason] = stats["reasons"].get(reason, 0) + 1
            print(f"[ELENDİ] {reason}, Score={trend.get('score')}")
            continue
        stats["trend_passed"] += 1
        print(f"[GEÇTİ] Trend: {trend['direction']}, Score={trend['score']}")

        # Trigger Check
        trigger = trigger_engine.analyze(symbol, trend["direction"], trend["btc_trend"])
        if trigger["quality"] == "D":
            reason = f"Trigger: Quality D (ADX={trigger.get('adx')}, RSI5={trigger.get('rsi5')}, RSI1={trigger.get('rsi1')})"
            stats["reasons"][reason] = stats["reasons"].get(reason, 0) + 1
            print(f"[ELENDİ] {reason}, Score={trigger.get('score')}")
            continue
        stats["trigger_passed"] += 1
        print(f"[GEÇTİ] Trigger: Quality={trigger['quality']}, Score={trigger['score']}")

        # Risk Check
        risk = risk_engine.calculate(symbol, trend["direction"], trigger["entry"], trigger["quality"], 1000.0)
        if not risk["valid"]:
            reason = f"Risk: {risk.get('risk_reject_reason', 'Invalid')}"
            stats["reasons"][reason] = stats["reasons"].get(reason, 0) + 1
            print(f"[ELENDİ] Risk: RR={risk.get('rr')}, Reason={risk.get('risk_reject_reason')}")
            continue
        stats["risk_passed"] += 1
        print(f"[GEÇTİ] Risk: RR={risk['rr']}, Risk%={risk['risk_pct']}")

        # AI Check
        # Mocking signal_data for AI engine
        class MockSignal:
            def __init__(self, s, t, tr, r):
                self.symbol = s
                self.coin_score = 5.0
                self.trend_score = t["score"]
                self.trigger_score = tr["score"]
                self.risk_score = r["score"]
                self.setup_quality = tr["quality"]
            def is_valid(self): return True

        ai_decision = ai_engine.evaluate(MockSignal(symbol, trend, trigger, risk))
        if ai_decision["decision"] == "VETO":
            reason = f"AI: {ai_decision.get('reason', 'Veto')}"
            stats["reasons"][reason] = stats["reasons"].get(reason, 0) + 1
            print(f"[ELENDİ] AI: {ai_decision['reason']}")
            continue
        stats["ai_passed"] += 1
        print(f"[GEÇTİ] AI ONAYI!")

    print("\n=== ÖZET İSTATİSTİKLER ===")
    print(f"Toplam Aday: {stats['total']}")
    print(f"Trendi Geçen: {stats['trend_passed']}")
    print(f"Trigger'ı Geçen: {stats['trigger_passed']}")
    print(f"Riski Geçen: {stats['risk_passed']}")
    print(f"AI Onayı Alan: {stats['ai_passed']}")
    
    print("\nElenme Nedenleri Dağılımı:")
    for r, count in stats["reasons"].items():
        print(f"- {r}: {count}")

if __name__ == "__main__":
    diagnose()
