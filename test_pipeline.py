"""
test_pipeline.py -- Pipeline Entegrasyon Testi
Proje kokunde calistir: python test_pipeline.py
"""
import asyncio
import sys
sys.path.insert(0, '.')

async def run_pipeline_test():
    print("=== Pipeline Entegrasyon Testi ===\n")
    errors = []
    
    # Reset trades table to ensure clean margin calculations
    try:
        from database import init_db, get_conn
        init_db()
        with get_conn() as conn:
            conn.execute("DELETE FROM trades")
            conn.commit()
    except Exception as _e:
        print(f"Warning: Failed to clean trades table: {_e}")

    d = {
        'symbol': 'ETHUSDT', 'direction': 'LONG',
        'entry_zone': 3500.0, 'stop_loss': 3450.0,
        'tp1': 3600.0, 'tp2': 3700.0,
        'final_score': 65.0, 'setup_quality': 'B',
        'leverage_suggestion': 10, 'risk_percent': 0.75,
        'confidence': 0.7, 'ml_score': 60.0,
        'trend_score': 7.0, 'trigger_score': 8.0,
    }

    # 1. SignalData.from_dict() testi
    try:
        from core.data_layer import SignalData
        sig = SignalData.from_dict(d)
        assert sig.entry_price == 3500.0, "entry_price yanlis"
        assert sig.direction == 'LONG', "direction yanlis"
        assert sig.final_score == 65.0, "final_score yanlis"
        print("[PASS] TEST 1: SignalData.from_dict()")
    except Exception as e:
        errors.append("[FAIL] TEST 1: SignalData.from_dict() -- " + str(e))

    # 2. AccountingEngine ile TradeData olusturma
    try:
        from core.data_layer import SignalData
        from core.accounting import build_trade_from_signal
        sig = SignalData.from_dict(d)
        trade = build_trade_from_signal(sig, 2000.0, 0.0004, 10)
        assert trade is not None, "trade None dondu"
        assert trade.quantity > 0, "quantity sifir: " + str(trade.quantity)
        print("[PASS] TEST 2: build_trade_from_signal() -- qty={:.4f}  margin={:.2f}$".format(trade.quantity, trade.margin_used))
    except Exception as e:
        errors.append("[FAIL] TEST 2: build_trade_from_signal() -- " + str(e))

    # 3. Execution Engine paper trade
    try:
        from database import init_db, init_paper_account
        await asyncio.to_thread(init_db)
        await asyncio.to_thread(init_paper_account)
        from execution_engine import ExecutionEngine
        from core.data_layer import SignalData
        sig = SignalData.from_dict(d)
        eng = ExecutionEngine()
        trade_id = eng.open_paper_trade(sig)
        assert trade_id is not None, "trade_id None"
        print("[PASS] TEST 3: ExecutionEngine.open_paper_trade() -- trade_id=" + str(trade_id))
    except Exception as e:
        errors.append("[FAIL] TEST 3: ExecutionEngine.open_paper_trade() -- " + str(e))

    # 4. Database open trades
    try:
        from database import get_open_trades
        trades = get_open_trades()
        print("[PASS] TEST 4: get_open_trades() -- " + str(len(trades)) + " acik trade")
    except Exception as e:
        errors.append("[FAIL] TEST 4: get_open_trades() -- " + str(e))

    # 5. Scanner import
    try:
        from core.async_market_scanner import AsyncMarketScanner
        s = AsyncMarketScanner()
        print("[PASS] TEST 5: AsyncMarketScanner() -- min_volume={:,.0f}  db={}".format(s.min_volume, s.db_path))
    except Exception as e:
        errors.append("[FAIL] TEST 5: AsyncMarketScanner() -- " + str(e))

    # 6. Ghost learning
    try:
        from core.ghost_learning import get_ghost_learning_stats
        stats = get_ghost_learning_stats()
        print("[PASS] TEST 6: Ghost Learning -- " + str(stats))
    except Exception as e:
        errors.append("[FAIL] TEST 6: Ghost Learning -- " + str(e))

    print()
    if errors:
        print("BASARISIZ TESTLER:")
        for err in errors:
            print("  " + err)
        sys.exit(1)
    else:
        print("[ALL PASSED] TUM TESTLER GECTI -- Sistem trade acmaya hazir")

if __name__ == "__main__":
    asyncio.run(run_pipeline_test())
