import sys
import logging
from pathlib import Path

# Add project root to path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from core.live_execution import LiveExecutionEngine
import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("verify_live")

def verify_live():
    print("=" * 60)
    print("   AURVEX LIVE EXECUTION VERIFIER")
    print("=" * 60)
    
    print(f"\n[1] Environment Config")
    print(f"  - EXECUTION_MODE: {config.EXECUTION_MODE}")
    print(f"  - LIVE_TRADING_ENABLED: {config.LIVE_TRADING_ENABLED}")
    print(f"  - CONFIRM_LIVE_TRADING: {config.CONFIRM_LIVE_TRADING}")
    print(f"  - is_live_trading_allowed(): {config.is_live_trading_allowed()}")
    
    print(f"\n[2] Initializing LiveExecutionEngine")
    try:
        le = LiveExecutionEngine()
        if le.client:
            print("  ✅ PASS: Binance Client initialized.")
        else:
            print("  ❌ FAIL: Binance Client could not be initialized. Check API keys.")
            return
            
        try:
            le.client.ping()
            print("  ✅ PASS: Binance API Ping successful.")
        except Exception as e:
            print(f"  ❌ FAIL: Binance API Ping failed: {e}")
            return
            
        balance = le._get_account_balance()
        print(f"  ✅ PASS: Account Balance check successful. Futures Wallet: ${balance:.2f} USDT")
        
    except Exception as e:
        print(f"  ❌ FAIL: Error initializing engine: {e}")
        return

    print(f"\n[3] Precision Formatting Test (BTCUSDT)")
    try:
        if 'BTCUSDT' in le.exchange_info:
            p_str = le._format_price('BTCUSDT', 65432.123456)
            q_str = le._format_quantity('BTCUSDT', 1.234567)
            print(f"  ✅ PASS: BTCUSDT Formatting:")
            print(f"     Price: 65432.123456 -> {p_str}")
            print(f"     Qty:   1.234567   -> {q_str}")
        else:
            print("  ⚠️ WARN: BTCUSDT not found in exchange info.")
    except Exception as e:
        print(f"  ❌ FAIL: Precision check failed: {e}")

    print("\n" + "=" * 60)
    print(" 🎉 SUCCESS: Live Execution Engine validation completed.")
    if config.is_live_trading_allowed():
        print(" ⚠️ WARNING: SYSTEM IS FULLY ARMED FOR REAL CAPITAL TRADING!")
    else:
        print(" ℹ️ Note: Live trading is currently disabled in config. You are safe.")
    print("=" * 60)

if __name__ == "__main__":
    verify_live()
