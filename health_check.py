"""
health_check.py -- Aurvex System Health Check
Usage:
  python health_check.py             # Full check (39 tests)
  python health_check.py --fast      # Fast check (DB + heartbeat + port)
  python health_check.py --fast --role engine
  python health_check.py --fast --role dashboard
"""
import sys, os, argparse
sys.path.insert(0, '.')

parser = argparse.ArgumentParser()
parser.add_argument("--fast", action="store_true", help="Fast check only")
parser.add_argument("--role", default="engine", choices=["engine", "dashboard"])
args, _ = parser.parse_known_args()

results = []

def chk(name, fn):
    try:
        r = fn()
        results.append(('OK', name, str(r)[:120]))
    except Exception as e:
        results.append(('FAIL', name, str(e)[:120]))


if args.fast:
    # ── FAST MODE ─────────────────────────────────────────────────────
    import config
    import database as db
    import time

    # (a) DB accessible
    chk('DB.open', lambda: db.get_paper_balance())

    # (b) Heartbeat < 5 min
    def _check_heartbeat():
        hb = db.get_bot_status("heartbeat")
        if not hb:
            if args.role == "dashboard":
                return "dashboard: no heartbeat needed"
            raise ValueError("No heartbeat in bot_status")
        val = hb.get("updated_at") or hb.get("value", "")
        if not val:
            raise ValueError("Empty heartbeat")
        from datetime import datetime, timezone
        try:
            updated = datetime.fromisoformat(val.replace("Z", "+00:00"))
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - updated).total_seconds()
            if age > 300:
                raise ValueError(f"Heartbeat stale: {age:.0f}s old")
            return f"{age:.0f}s ago"
        except ValueError:
            raise
        except Exception:
            return "ok (unparseable)"

    chk('heartbeat', _check_heartbeat)

    # (c) Port check
    import socket
    def _check_port():
        port = 8000 if args.role == "engine" else 5000
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex(('127.0.0.1', port))
        sock.close()
        if result == 0:
            return f"port {port} open"
        return f"port {port} not yet bound (ok during startup)"

    chk(f'port_{args.role}', _check_port)

else:
    # ── FULL MODE ──────────────────────────────────────────────────────
    # 1. Config
    import config
    chk('config.DB_PATH',          lambda: config.DB_PATH)
    chk('config.EXECUTION_MODE',   lambda: config.EXECUTION_MODE)
    chk('config.TRADE_THRESHOLD',  lambda: config.TRADE_THRESHOLD)
    chk('config.TELEGRAM_TOKEN',   lambda: bool(config.TELEGRAM_BOT_TOKEN))
    chk('config.TELEGRAM_CHAT',    lambda: bool(config.TELEGRAM_CHAT_ID))

    # 2. Database
    import database as db
    chk('DB.init_db',              lambda: db.init_db())
    chk('DB.get_open_trades',      lambda: len(db.get_open_trades()))
    chk('DB.get_paper_balance',    lambda: db.get_paper_balance())

    # 3. DB Tables
    try:
        with db.get_conn() as conn:
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()]
        chk('DB.tables', lambda: sorted(tables))
    except Exception as e:
        results.append(('FAIL', 'DB.tables', str(e)))

    # 4. bot_status
    try:
        with db.get_conn() as conn:
            rows = conn.execute('SELECT key, value FROM bot_status ORDER BY key').fetchall()
        chk('DB.bot_status', lambda: {k: v for k, v in rows})
    except Exception as e:
        results.append(('FAIL', 'DB.bot_status', str(e)))

    # 5. Data Layer
    from core.data_layer import SignalData, data_layer
    test_d = {
        'symbol': 'BTCUSDT', 'direction': 'LONG',
        'entry_zone': 50000.0, 'stop_loss': 49000.0,
        'final_score': 70.0, 'setup_quality': 'B',
        'leverage_suggestion': 10, 'risk_percent': 0.75,
        'confidence': 0.7, 'ml_score': 60.0,
        'tp1': 52000.0, 'tp2': 54000.0,
    }
    chk('SignalData.from_dict.entry_price', lambda: SignalData.from_dict(test_d).entry_price)
    chk('SignalData.from_dict.tp1',         lambda: SignalData.from_dict(test_d).tp1)
    chk('SignalData.from_dict.leverage',    lambda: SignalData.from_dict(test_d).leverage)
    chk('SignalData.from_dict.final_score', lambda: SignalData.from_dict(test_d).final_score)

    # 6. Accounting
    from core.accounting import build_trade_from_signal
    sig = SignalData.from_dict(test_d)
    chk('accounting.build_trade',  lambda: build_trade_from_signal(sig, 2000.0, 0.0004, 10).quantity)

    # 7. ExecutionEngine
    import database
    from execution_engine import ExecutionEngine
    eng = ExecutionEngine()
    chk('ExecutionEngine.balance', lambda: database.get_paper_balance())
    chk('ExecutionEngine.open_trades', lambda: len(database.get_open_trades()))

    # 8. AI Decision Engine
    from core.ai_decision_engine import AIDecisionEngine
    ai = AIDecisionEngine()
    chk('AIDecisionEngine.init',   lambda: type(ai).__name__)
    chk('AIDecisionEngine.evaluate', lambda: ai.evaluate(sig).get('decision'))

    # 9. Ghost Learning
    import core.ghost_learning as gl
    chk('ghost_learning.stats',    lambda: gl.get_ghost_learning_stats())
    chk('ghost_learning.weight',   lambda: gl.calculate_dynamic_ghost_weight())

    # 10. ML Scorer
    from core.ml_signal_scorer import score_signal
    chk('ml_scorer.score_signal',  lambda: score_signal(test_d))

    # 11. Risk Engine
    from core.risk_engine import RiskEngine
    chk('RiskEngine.init',         lambda: type(RiskEngine(None)).__name__)

    # 12. Trend Engine
    from core.trend_engine import TrendEngine
    chk('TrendEngine.init',        lambda: type(TrendEngine(None)).__name__)

    # 13. Trailing Engine
    from core.trailing_engine import TrailingEngine
    chk('TrailingEngine.init',     lambda: type(TrailingEngine()).__name__)

    # 14. AsyncMarketScanner
    from core.async_market_scanner import AsyncMarketScanner
    s = AsyncMarketScanner()
    chk('AsyncMarketScanner.db_path',    lambda: s.db_path)
    chk('AsyncMarketScanner.min_volume', lambda: s.min_volume)

    # 15. Event Bus
    from core.event_bus import event_bus
    from core.event_types import EventType
    chk('EventBus.subscribe', lambda: event_bus.subscribe(EventType.SCANNED, lambda e: None) or 'ok')

    # 16. Telegram
    import telegram_delivery as td
    chk('telegram_delivery.queue',        lambda: type(td._queue).__name__)
    chk('telegram_delivery.configured',   lambda: td.TelegramDelivery().is_configured())

    # 17. Services
    from core.services.notification_service import NotificationService
    from core.services.execution_service import ExecutionService
    from core.services.ai_decision_service import AIDecisionService
    chk('NotificationService.init',  lambda: type(NotificationService()).__name__)
    chk('ExecutionService.init',     lambda: type(ExecutionService()).__name__)
    chk('AIDecisionService.init',    lambda: type(AIDecisionService()).__name__)

    # 18. WebSocket / Dashboard
    try:
        from websocket_events import event_manager
        chk('websocket_events.event_manager', lambda: type(event_manager).__name__)
        chk('websocket_events.broadcast_fn',  lambda: hasattr(event_manager, 'broadcast_live_update'))
    except Exception as e:
        results.append(('FAIL', 'websocket_events', str(e)))

    # 19. App (Flask dashboard)
    try:
        import app as flask_app
        chk('app.flask.init',     lambda: type(flask_app.app).__name__)
        rules = [str(r) for r in flask_app.app.url_map.iter_rules()]
        chk('app.routes.count',   lambda: len(rules))
        api_routes = [r for r in rules if '/api/' in r]
        chk('app.api_routes',     lambda: api_routes)
    except Exception as e:
        results.append(('FAIL', 'app.flask', str(e)))

    # 20. open trades detail
    try:
        open_trades = db.get_open_trades()
        chk('DB.open_trades_detail', lambda: [
            {k: v for k, v in t.items() if k in ('id','symbol','side','entry_price','tp1','leverage')}
            for t in open_trades[:3]
        ])
    except Exception as e:
        results.append(('FAIL', 'DB.open_trades_detail', str(e)))


# --- RAPOR ---
print()
print('=' * 70)
print('AURVEX AI -- SYSTEM HEALTH CHECK RAPORU')
print('=' * 70)
ok_list   = [r for r in results if r[0] == 'OK']
fail_list = [r for r in results if r[0] == 'FAIL']
print(f'TOPLAM: {len(results)}  |  OK: {len(ok_list)}  |  FAIL: {len(fail_list)}')
print()

if fail_list:
    print('=== BASARISIZ ===')
    for _, name, detail in fail_list:
        print(f'  [FAIL] {name}')
        print(f'         -> {detail}')
    print()

print('=== BASARILI ===')
for _, name, detail in ok_list:
    print(f'  [OK]  {name}: {detail}')

sys.exit(1 if fail_list else 0)
