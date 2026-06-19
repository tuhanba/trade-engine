"""P1-1b: setup_type persists to the trades table (directive Section 9).

The critical check is that the modified create_trade fixed-column INSERT still
has matching column/placeholder/value counts AND writes setup_type.
"""
import config
from core.data_layer import TradeData


def test_create_trade_persists_setup_type(test_db):
    trade = TradeData(symbol="BTCUSDT", side="LONG", entry_price=100.0,
                      stop_loss=95.0, tp1=110.0, quantity=1.0, leverage=5)
    trade.setup_type = "MOMENTUM_EXPANSION_SCALP"
    tid = test_db.create_trade(trade)
    assert tid, "create_trade returned no id (INSERT column/placeholder mismatch?)"
    with test_db.get_conn() as conn:
        row = conn.execute("SELECT setup_type FROM trades WHERE id=?", (tid,)).fetchone()
    assert row is not None and row[0] == "MOMENTUM_EXPANSION_SCALP"


def test_create_trade_defaults_unknown(test_db):
    trade = TradeData(symbol="ETHUSDT", side="SHORT", entry_price=50.0,
                      stop_loss=52.0, quantity=2.0, leverage=3)
    tid = test_db.create_trade(trade)
    assert tid
    with test_db.get_conn() as conn:
        row = conn.execute("SELECT setup_type FROM trades WHERE id=?", (tid,)).fetchone()
    assert row[0] == "UNKNOWN"   # TradeData dataclass default


def test_migration_idempotent(test_db):
    from scripts.migrate_setup_type import migrate
    # test_db already has the columns (init_db DDL); migration must be a safe no-op.
    migrate(config.DB_PATH)
    migrate(config.DB_PATH)
    with test_db.get_conn() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(trades)").fetchall()}
    assert "setup_type" in cols and "setup_reason" in cols
