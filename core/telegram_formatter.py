"""
Telegram Formatter — AX Scalp Engine
======================================
Tüm Telegram mesaj şablonları bu modülden üretilir.
Hiçbir mesaj sahte skor basmaz; skor bilinmiyorsa N/A gösterir.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from core.score_engine import score_bar, score_grade


def _fmt_price(v: Any) -> str:
    try:
        return f"{float(v):.6f}"
    except Exception:
        return "N/A"


def _fmt_pct(v: Any) -> str:
    try:
        return f"%{float(v):.2f}"
    except Exception:
        return "N/A"


def _direction_badge(direction: str) -> str:
    d = str(direction).upper()
    if d == "LONG":
        return "🟢 LONG"
    if d == "SHORT":
        return "🔴 SHORT"
    return "⚪ UNKNOWN"


def _quality_badge(q: Optional[str], final_score: Optional[float]) -> str:
    q = q or score_grade(final_score)
    icons = {
        "S": "💎 S",
        "A+": "🔥 A+",
        "A": "✅ A",
        "B": "🟡 B",
        "C": "⚠️ C",
        "D": "🧊 D",
    }
    return icons.get(str(q).upper(), f"⚪ {q}")


def format_trade_open(data: Dict[str, Any]) -> str:
    """
    Sinyal açılış mesajı — score breakdown, kaynak ve güven dahil.
    final_score None ise bar 'N/A' gösterir, asla 50 basmaz.
    """
    symbol = data.get("symbol", "UNKNOWN")
    direction = _direction_badge(data.get("direction", ""))
    final_score = data.get("final_score")
    quality = data.get("setup_quality") or score_grade(final_score if final_score is not None else None)
    score_source = data.get("score_source", "unknown")
    score_conf = data.get("score_confidence")
    mode = data.get("execution_mode", data.get("mode", "paper"))
    paper = str(mode).lower() != "live"

    # Skor bar: final_score gerçekten bilinmiyorsa None geç (N/A çıkar)
    bar_score = final_score if final_score is not None else None
    score_line = score_bar(bar_score)

    technical = data.get("technical_score")
    ml = data.get("ml_score")
    cold = data.get("cold_start_score")
    risk_score = data.get("risk_score")
    ai_score = data.get("ai_score")

    ml_text = "N/A"
    if ml is not None:
        ml_text = f"{float(ml):.1f}"
    elif cold is not None:
        ml_text = f"{float(cold):.1f} (cold)"

    risk_pct = data.get("risk_percent", data.get("risk_pct", 0))
    risk_usd = data.get("risk_amount", data.get("risk_usd", data.get("max_loss", 0)))
    leverage = data.get("leverage_suggestion", data.get("leverage", "N/A"))

    title_mode = "🧪 PAPER" if paper else "🚨 LIVE"

    reason = data.get("reason") or data.get("ai_reason") or "Pipeline onayladı."
    invalidation = data.get("invalidation_level") or data.get("stop_loss") or data.get("sl")

    conf_line = ""
    if score_conf is not None:
        conf_line = f" | Güven: %{float(score_conf) * 100:.0f}"

    return (
        f"{title_mode} | ⚡ AX SCALP SIGNAL\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{direction}  <b>{symbol}</b>\n"
        f"Kalite: {_quality_badge(quality, final_score)} | Skor: <b>{score_line}</b>\n"
        f"Kaynak: <code>{score_source}</code>{conf_line}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 Entry: <code>{_fmt_price(data.get('entry', data.get('entry_zone')))}</code>\n"
        f"🛑 Stop:  <code>{_fmt_price(data.get('stop_loss', data.get('sl')))}</code>\n"
        f"🥉 TP1:   <code>{_fmt_price(data.get('tp1'))}</code>\n"
        f"🥈 TP2:   <code>{_fmt_price(data.get('tp2'))}</code>\n"
        f"🥇 TP3:   <code>{_fmt_price(data.get('tp3', data.get('runner_target')))}</code>\n"
        f"⚖️ RR: <b>{float(data.get('rr', 0) or 0):.2f}R</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🧮 Skor Dağılımı\n"
        f"├ Teknik: <code>{float(technical or 0):.1f}</code>\n"
        f"├ AI:     <code>{float(ai_score or 0):.1f}</code>\n"
        f"├ Risk:   <code>{float(risk_score or 0):.1f}</code>\n"
        f"└ ML:     <code>{ml_text}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Risk: <code>{float(risk_usd or 0):.2f} USD</code> ({_fmt_pct(risk_pct)})\n"
        f"⚙️ Kaldıraç: <code>x{leverage}</code>\n"
        f"🚫 Invalidation: <code>{_fmt_price(invalidation)}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🧠 Not: {reason}\n"
        f"⏰ {datetime.now(timezone.utc).strftime('%H:%M UTC')}"
    )


def format_paper_open(data: Dict[str, Any]) -> str:
    direction = _direction_badge(data.get("direction", ""))
    symbol = data.get("symbol", "UNKNOWN")
    final_score = data.get("final_score")
    score_line = score_bar(final_score)

    return (
        f"🧪 PAPER OPENED\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{direction} <b>{symbol}</b>\n"
        f"Entry: <code>{_fmt_price(data.get('entry', data.get('entry_zone')))}</code>\n"
        f"Size: <code>{float(data.get('position_size', 0) or 0):.6f}</code>\n"
        f"Risk: <code>{float(data.get('risk_usd', data.get('max_loss', 0)) or 0):.2f} USD</code> "
        f"({_fmt_pct(data.get('risk_percent', 0))})\n"
        f"SL: <code>{_fmt_price(data.get('stop_loss', data.get('sl')))}</code> | "
        f"TP1: <code>{_fmt_price(data.get('tp1'))}</code> | "
        f"TP2: <code>{_fmt_price(data.get('tp2'))}</code> | "
        f"TP3: <code>{_fmt_price(data.get('tp3'))}</code>\n"
        f"Signal Score: <b>{score_line}</b>\n"
        f"⏰ {datetime.now(timezone.utc).strftime('%H:%M UTC')}"
    )


def format_tp_hit(symbol: str, tp_level: int, net_pnl: float,
                  remaining_qty: float, balance_after: float = 0) -> str:
    medals = {1: "🥉", 2: "🥈", 3: "🥇"}
    medal = medals.get(tp_level, "🎯")
    sign = "+" if net_pnl >= 0 else ""
    be_line = "\nSL moved: <b>Breakeven ✅</b>" if tp_level == 1 else ""
    runner_line = "\nRunner/Trailing başladı 🏃" if tp_level == 2 else ""

    remaining_pct = round(remaining_qty * 100, 1) if remaining_qty <= 1 else remaining_qty

    return (
        f"{medal} TP{tp_level} HIT — <b>{symbol}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Realized: <b>{sign}{net_pnl:.4f} USD</b>{be_line}{runner_line}\n"
        f"Remaining: <code>{remaining_pct}</code>\n"
        f"Balance: <code>${balance_after:.2f}</code>\n"
        f"⏰ {datetime.now(timezone.utc).strftime('%H:%M UTC')}"
    )


def format_trade_close(symbol: str, net_pnl: float, total_fee: float,
                       reason: str, duration_str: str,
                       direction: str = "", r_multiple: float = 0,
                       balance_after: float = 0) -> str:
    is_win = net_pnl > 0
    result_badge = "✅ PAPER CLOSED" if is_win else "❌ PAPER STOPPED"
    result_r = f"+{r_multiple:.2f}R" if r_multiple >= 0 else f"{r_multiple:.2f}R"
    sign = "+" if net_pnl >= 0 else ""

    reason_map = {
        "tp1": "TP1 Hit", "tp2": "TP2 Hit", "tp3": "TP3 Hit",
        "trail": "Trailing Stop", "sl": "Stop Loss",
        "timeout": "Zaman Aşımı",
    }
    reason_str = reason_map.get(str(reason).lower(), str(reason).upper() if reason else "?")

    learning_line = ""
    if not is_win:
        learning_line = "\nLearning: fakeout candidate recorded 🧠"

    return (
        f"{result_badge} — <b>{symbol}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Result: <b>{result_r}</b>\n"
        f"PnL: <b>{sign}{net_pnl:.4f} USD</b>\n"
        f"Fee: <code>{total_fee:.4f} USD</code>\n"
        f"Duration: <code>{duration_str}</code>\n"
        f"Exit Reason: <b>{reason_str}</b>{learning_line}\n"
        f"Balance: <code>${balance_after:.2f}</code>\n"
        f"⏰ {datetime.now(timezone.utc).strftime('%H:%M UTC')}"
    )
