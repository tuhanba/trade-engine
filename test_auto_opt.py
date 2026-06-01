import asyncio
import sys
import os
import json
import sqlite3
from datetime import datetime, timezone

sys.path.insert(0, '.')

from database import (
    init_db,
    save_ghost_suggestion,
    get_pending_ghost_suggestions,
    get_coin_config,
    save_coin_config
)
from core.ghost_learning import (
    generate_threshold_suggestions,
    apply_ghost_suggestions_v2
)
from core.ai_decision_engine import AIDecisionEngine
from core.data_layer import SignalData

async def run_tests():
    print("=== Auto-Optimization Integration Test ===\n")
    
    # 1. Initialize DB
    init_db()
    
    # Clean tables to ensure reproducible test state
    import database
    with database.get_conn() as conn:
        conn.execute("DELETE FROM ghost_suggestions")
        conn.execute("DELETE FROM ghost_threshold_suggestions")
        conn.execute("DELETE FROM coin_configs WHERE coin = 'TESTUSDT'")
        conn.execute("DELETE FROM trades")
    
    # 2. Insert mock suggestions to test save_ghost_suggestion
    print("[TEST 1] Testing save_ghost_suggestion...")
    mock_sug = {
        "coin": "TESTUSDT",
        "trigger_type": "TEST_TRIGGER",
        "action": "LOWER_THRESHOLD",
        "current_val": 70.0,
        "suggested_val": 65.0,
        "expected_trades": 5.0,
        "confidence": "HIGH",
        "virtual_wr": 80.0,
        "avg_virtual_r": 1.5,
        "sample_count": 40
    }
    save_ghost_suggestion(mock_sug)
    
    # Verify that save_ghost_suggestion populated both tables
    with database.get_conn() as conn:
        gs_count = conn.execute("SELECT COUNT(*) FROM ghost_suggestions WHERE symbol='TESTUSDT'").fetchone()[0]
        gts_count = conn.execute("SELECT COUNT(*) FROM ghost_threshold_suggestions WHERE coin='TESTUSDT'").fetchone()[0]
        
    assert gs_count == 1, "ghost_suggestions table not populated"
    assert gts_count == 1, "ghost_threshold_suggestions table not populated"
    print("[PASS] save_ghost_suggestion wrote to both tables successfully.")

    # 3. Test get_pending_ghost_suggestions
    print("[TEST 2] Testing get_pending_ghost_suggestions...")
    pending = get_pending_ghost_suggestions(min_confidence="MEDIUM")
    assert len(pending) == 1, f"Expected 1 pending suggestion, got {len(pending)}"
    sug = pending[0]
    assert sug["symbol"] == "TESTUSDT"
    assert sug["trigger_type"] == "TEST_TRIGGER"
    assert sug["suggested_threshold"] == 65.0
    print("[PASS] get_pending_ghost_suggestions retrieved suggestion correctly.")

    # 4. Initialize coin_config for TESTUSDT to mock initial state
    save_coin_config("TESTUSDT", {"confidence_cutoff": 0.65})

    # 5. Test apply_ghost_suggestions_v2
    print("[TEST 3] Testing apply_ghost_suggestions_v2...")
    applied = apply_ghost_suggestions_v2(min_confidence="MEDIUM")
    assert len(applied) == 1, f"Expected 1 applied suggestion, got {len(applied)}"
    assert "TESTUSDT" in applied[0]
    
    # Verify that suggestions were marked applied=1
    pending_after = get_pending_ghost_suggestions(min_confidence="MEDIUM")
    assert len(pending_after) == 0, f"Expected 0 pending suggestions after apply, got {len(pending_after)}"
    
    # Verify that the coin_config has the overrides key
    cfg = get_coin_config("TESTUSDT")
    assert "threshold_overrides" in cfg, "threshold_overrides missing from config_json"
    assert cfg["threshold_overrides"].get("TEST_TRIGGER") == 65.0, f"Expected override threshold to be 65.0, got {cfg['threshold_overrides'].get('TEST_TRIGGER')}"
    print("[PASS] apply_ghost_suggestions_v2 updated coin_configs and marked suggestions applied.")

    # 6. Test AIDecisionEngine integration
    print("[TEST 4] Testing AIDecisionEngine using override threshold...")
    engine = AIDecisionEngine()
    
    # We create a mock signal with score = 67.0
    # The default TRADE_THRESHOLD is 72.0. Under default rules, this signal would be WATCH/VETO.
    # But for TESTUSDT with TEST_TRIGGER, the threshold override is 65.0. So it should be ALLOWED!
    sig_data = {
        'symbol': 'TESTUSDT',
        'direction': 'LONG',
        'entry_price': 3000.0,
        'stop_loss': 2950.0,
        'tp1': 3075.0,  # RR = 1.5 (no bonus)
        'final_score': 67.0,  # Score is 67, which is < 72.0 but > 65.0
        'setup_quality': 'TEST_TRIGGER',
        'trigger_score': 6.7,
        'trend_score': 6.7,
        'risk_score': 6.7,
        'risk_percent': 1.0,
        'confidence': 0.5,
    }
    sig = SignalData.from_dict(sig_data)
    
    # Set the score explicitly
    sig.score = 67.0
    
    decision = engine.evaluate(sig)
    print("AI Decision details:", decision)
    assert decision["decision"] == "ALLOW", f"Expected ALLOW decision due to threshold override, got {decision['decision']}"
    assert "YZ otonom eşiği uygulandı: 65.0" in decision["reason"] or "Score yeterli" in decision["reason"], f"Unexpected reason: {decision['reason']}"
    print("[PASS] AIDecisionEngine successfully applied the override threshold from coin_configs.")

    # Clean up DB after test
    with database.get_conn() as conn:
        conn.execute("DELETE FROM ghost_suggestions WHERE symbol='TESTUSDT'")
        conn.execute("DELETE FROM ghost_threshold_suggestions WHERE coin='TESTUSDT'")
        conn.execute("DELETE FROM coin_configs WHERE coin='TESTUSDT'")
        
    print("\n[ALL PASSED] Auto-Optimization integration tests passed successfully!")

if __name__ == "__main__":
    asyncio.run(run_tests())
