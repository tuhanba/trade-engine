"""P1-4: Telegram surfacing for profit_readiness + setup expectancy.

Formatters are pure (no TelegramManager instance / network), so we test them
directly and assert the command handlers are registered.
"""
from telegram_manager import (TelegramManager, format_profit_readiness,
                              format_setup_report)
from scripts.profit_readiness import compute
from scripts.setup_expectancy_report import compute_by_setup


def _pass_trades():
    coins = [f"C{i}USDT" for i in range(10)]
    setups = ["A", "B", "C", "D", "E", "F"]
    sessions = ["ASIA", "LONDON", "NY", "OFF"]
    out = []
    for i in range(300):
        win = (i % 5) < 3
        out.append({"r_multiple": 1.5 if win else -1.0,
                    "net_pnl": (1.5 if win else -1.0) * 10, "risk_usd": 10,
                    "symbol": coins[i % 10], "setup_type": setups[i % 6],
                    "session": sessions[i % 4]})
    return out


def test_format_profit_readiness_not_ready_empty():
    msg = format_profit_readiness(compute([]))
    assert "HAZIR DEĞİL" in msg


def test_format_profit_readiness_pass():
    msg = format_profit_readiness(compute(_pass_trades(), base_balance=100_000))
    assert "✅ HAZIR" in msg
    assert "Expectancy" in msg


def test_format_setup_report_empty():
    assert "Henüz setup verisi yok" in format_setup_report({"setups": {}})


def test_format_setup_report_lists_setups():
    trades = [{"setup_type": "MOMENTUM_EXPANSION_SCALP", "r_multiple": 1.0,
               "net_pnl": 10, "risk_usd": 10, "symbol": "X", "close_reason": "TP1"}] * 40
    msg = format_setup_report({"n_trades": 40, "setups": compute_by_setup(trades)})
    assert "MOMENTUM_EXPANSION_SCALP" in msg
    assert "Setup Expectancy" in msg


def test_command_handlers_exist():
    assert hasattr(TelegramManager, "_cmd_profit_readiness")
    assert hasattr(TelegramManager, "_cmd_setup_report")


def test_trade_open_card_shows_setup():
    from telegram_delivery import format_trade_open
    msg = format_trade_open({"symbol": "BTCUSDT", "direction": "LONG", "entry": 100,
                             "sl": 95, "tp1": 110, "rr": 2.0,
                             "setup_type": "LIQUIDITY_SWEEP_SFP_REVERSAL"})
    assert "LIQUIDITY_SWEEP_SFP_REVERSAL" in msg and "Setup" in msg


def test_trade_close_card_shows_setup():
    from telegram_delivery import format_trade_close
    msg = format_trade_close({"symbol": "BTCUSDT", "direction": "LONG", "entry": 100,
                              "close_price": 110, "setup_type": "MOMENTUM_EXPANSION_SCALP"},
                             pnl=12.5, reason="tp1")
    assert "MOMENTUM_EXPANSION_SCALP" in msg


def test_trade_open_card_defaults_unknown():
    from telegram_delivery import format_trade_open
    msg = format_trade_open({"symbol": "X", "direction": "LONG"})
    assert "UNKNOWN" in msg
