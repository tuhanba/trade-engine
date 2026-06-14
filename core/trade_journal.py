"""
core/trade_journal.py — Haftalık Trade Journal Raporu (Faz 6.1)
================================================================
Haftanın tüm işlemlerini, giriş gerekçelerini (signal metadata), Friday
kararlarını ve ders çıkarımlarını (LLM özeti, varsa) tek Markdown dosyada
toplar. Her pazar Telegram'a gönderilir; /journal ile manuel üretilebilir.

NEDEN: Post-mortem disiplini — "bu hafta ne yaptık, ne öğrendik" tek bakışta.
SaaS'ta premium "Trade Journal" özelliği olur.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

import config
import database

logger = logging.getLogger("ax.trade_journal")


def _fmt_money(v) -> str:
    try:
        v = float(v or 0)
        return ("-" if v < 0 else "") + f"${abs(v):,.2f}"
    except Exception:
        return "$0.00"


def _fmt_money_signed(v) -> str:
    try:
        v = float(v or 0)
        return ("+" if v >= 0 else "-") + f"${abs(v):,.2f}"
    except Exception:
        return "+$0.00"


def _entry_rationale(metadata) -> str:
    """trade.metadata JSON'ından insan-okunur giriş gerekçesi süzer."""
    if not metadata:
        return "—"
    meta = metadata
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            return "—"
    if not isinstance(meta, dict):
        return "—"
    # Öncelik sırası: açık 'reason' alanı → trigger/setup → indikatör özeti
    for key in ("reason", "entry_reason", "setup_reason", "trigger_type", "setup"):
        v = meta.get(key)
        if v:
            return str(v)[:120]
    parts = []
    for label, key in (("ADX", "adx"), ("RSI", "rsi5"), ("CVD", "cvd_value"), ("OI%", "oi_change_pct")):
        if meta.get(key) is not None:
            try:
                parts.append(f"{label} {float(meta[key]):.1f}")
            except Exception:
                pass
    return ", ".join(parts) if parts else "—"


def _trade_r(row) -> float:
    try:
        r = float(row["r_multiple"] or 0)
        if r != 0:
            return r
        risk = float(row["risk_usd"] or 0)
        return (float(row["net_pnl"] or 0) / risk) if risk > 0 else 0.0
    except Exception:
        return 0.0


def collect_week_data(days: int = 7, environment: Optional[str] = None) -> dict:
    """Haftanın trade'leri + Friday kararları + özet metrikleri toplar."""
    env = environment or getattr(config, "EXECUTION_MODE", "paper")
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    data = {"trades": [], "decisions": [], "stats": {}, "environment": env, "days": days}

    try:
        with database.get_conn() as conn:
            rows = conn.execute(
                """
                SELECT symbol, direction, entry, close_price, net_pnl, risk_usd, r_multiple,
                       close_reason, final_score, market_regime, hold_minutes, metadata,
                       close_time, setup_quality
                FROM trades
                WHERE status='closed' AND close_time >= ? AND environment = ?
                  AND COALESCE(is_valid_for_stats,1)=1
                ORDER BY close_time ASC
                """,
                (cutoff, env),
            ).fetchall()
            data["trades"] = [dict(r) for r in rows]

            # Friday kararları (aynı pencere)
            try:
                drows = conn.execute(
                    "SELECT created_at, decision_type, param_key, old_value, new_value, "
                    "reasoning, outcome_score FROM friday_decisions "
                    "WHERE created_at >= ? ORDER BY created_at ASC",
                    (cutoff,),
                ).fetchall()
                data["decisions"] = [dict(r) for r in drows]
            except Exception:
                data["decisions"] = []
    except Exception as e:
        logger.error("[TradeJournal] veri toplama hatası: %s", e)

    # Özet metrikler
    trades = data["trades"]
    n = len(trades)
    wins = [t for t in trades if float(t["net_pnl"] or 0) > 0]
    losses = [t for t in trades if float(t["net_pnl"] or 0) <= 0]
    net = sum(float(t["net_pnl"] or 0) for t in trades)
    r_vals = [_trade_r(t) for t in trades]
    data["stats"] = {
        "n": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / n * 100, 1) if n else 0.0,
        "net_pnl": round(net, 2),
        "total_r": round(sum(r_vals), 2),
        "avg_r": round(sum(r_vals) / n, 3) if n else 0.0,
        "best": max(trades, key=lambda t: float(t["net_pnl"] or 0)) if trades else None,
        "worst": min(trades, key=lambda t: float(t["net_pnl"] or 0)) if trades else None,
    }
    return data


def _gemini_lessons(data: dict) -> str:
    """LLM varsa haftanın derslerini 2-3 cümlede özetler; yoksa kural tabanlı."""
    stats = data["stats"]
    # LLM dene (Friday altyapısını yeniden kullan)
    try:
        provider = ""
        if getattr(config, "GEMINI_API_KEY", ""):
            provider = "gemini"
        elif getattr(config, "ANTHROPIC_API_KEY", ""):
            provider = "anthropic"
        if provider and getattr(config, "FRIDAY_LLM_MODE", "offline").lower() != "offline":
            from core.friday_ceo import FridayCeo
            ceo = FridayCeo()
            summary = (
                f"Hafta: {stats['n']} işlem, {stats['wins']}W-{stats['losses']}L, "
                f"net {stats['net_pnl']}, toplam {stats['total_r']}R."
            )
            return ceo._generate_text(
                provider,
                "Sen bir trading koçusun. Haftalık performanstan 2-3 cümlelik Türkçe ders çıkar.",
                summary,
                "subagent",
            ).strip()
    except Exception as e:
        logger.debug("[TradeJournal] LLM özeti atlandı: %s", e)
    # Kural tabanlı fallback
    s = data["stats"]
    if s["n"] == 0:
        return "Bu hafta kapanmış işlem yok — sistem seçici kaldı veya piyasa uygun değildi."
    if s["total_r"] > 0:
        return (f"Pozitif hafta (+{s['total_r']}R). Win rate %{s['win_rate']}. "
                "Kazanan kurulumların disiplinini koru, kaybedenlerin ortak paydasını incele.")
    return (f"Negatif hafta ({s['total_r']}R). Win rate %{s['win_rate']}. "
            "Kayıpların rejim/saat dağılımını gözden geçir; eşik veya filtre sıkılaştırması değerlendir.")


def generate_markdown(days: int = 7, environment: Optional[str] = None) -> str:
    """Haftalık journal'ı Markdown metni olarak üretir."""
    data = collect_week_data(days, environment)
    s = data["stats"]
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=days)).strftime("%d %b")
    end = now.strftime("%d %b %Y")

    lines = [
        f"# 📓 AurvexAI Trade Journal — {start} → {end}",
        "",
        f"**Ortam:** `{data['environment']}`  ·  **İşlem:** {s['n']}  ·  "
        f"**W-L:** {s['wins']}-{s['losses']} (%{s['win_rate']})  ·  "
        f"**Net:** {_fmt_money_signed(s['net_pnl'])}  ·  **Toplam R:** {s['total_r']:+.2f}R",
        "",
    ]

    if s["best"]:
        b = s["best"]
        lines.append(f"🏆 **En iyi:** {b['symbol']} {_fmt_money_signed(b['net_pnl'])} ({_trade_r(b):+.2f}R)")
    if s["worst"] and s["n"] > 1:
        w = s["worst"]
        lines.append(f"💀 **En kötü:** {w['symbol']} {_fmt_money_signed(w['net_pnl'])} ({_trade_r(w):+.2f}R)")
    lines.append("")

    # İşlem tablosu
    lines.append("## İşlemler")
    lines.append("")
    if data["trades"]:
        lines.append("| # | Sembol | Yön | PnL | R | Sebep | Skor | Rejim | Süre | Giriş Gerekçesi |")
        lines.append("|--:|---|---|--:|--:|---|--:|---|--:|---|")
        for i, t in enumerate(data["trades"], 1):
            dur = f"{int(float(t.get('hold_minutes') or 0))}dk"
            lines.append(
                f"| {i} | {t['symbol']} | {t['direction']} | {_fmt_money_signed(t['net_pnl'])} | "
                f"{_trade_r(t):+.2f} | {t.get('close_reason') or '—'} | "
                f"{float(t.get('final_score') or 0):.0f} | {t.get('market_regime') or '—'} | "
                f"{dur} | {_entry_rationale(t.get('metadata'))} |"
            )
    else:
        lines.append("_Bu hafta kapanmış işlem yok._")
    lines.append("")

    # Friday kararları
    lines.append("## Friday Kararları")
    lines.append("")
    if data["decisions"]:
        lines.append("| Tarih | Tip | Değişim | Sonuç Skoru |")
        lines.append("|---|---|---|--:|")
        for d in data["decisions"]:
            chg = f"{d.get('param_key')}: {d.get('old_value')}→{d.get('new_value')}" if d.get("param_key") else "—"
            sc = d.get("outcome_score")
            sc_str = f"{sc:+.2f}" if sc is not None else "⏳"
            ts = str(d.get("created_at") or "")[:16].replace("T", " ")
            lines.append(f"| {ts} | {d.get('decision_type')} | {chg} | {sc_str} |")
    else:
        lines.append("_Bu hafta Friday kararı kaydedilmedi._")
    lines.append("")

    # Dersler
    lines.append("## 🎓 Ders Çıkarımları")
    lines.append("")
    lines.append(_gemini_lessons(data))
    lines.append("")
    lines.append(f"<sub>Otomatik üretildi · {now.strftime('%Y-%m-%d %H:%M UTC')}</sub>")
    return "\n".join(lines)


def write_journal_file(days: int = 7, environment: Optional[str] = None,
                       out_dir: str = "") -> str:
    """Journal Markdown'ını dosyaya yazar, dosya yolunu döner."""
    md = generate_markdown(days, environment)
    base = out_dir or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    fname = f"trade_journal_{datetime.now(timezone.utc).strftime('%Y%m%d')}.md"
    path = os.path.join(base, fname)
    with open(path, "w", encoding="utf-8") as f:
        f.write(md)
    logger.info("[TradeJournal] Rapor yazıldı: %s", path)
    return path
