"""
core/friday_decisions.py — Friday Karar Günlüğü + Sonuç Takibi (Faz 2.1)
=========================================================================
Friday'in uyguladığı her otonom karar `friday_decisions` tablosuna yazılır ve
24/72 saat sonra sonuçları (PnL/WR/expectancy delta) otomatik doldurulur.

NEDEN: Hesap verebilirlik — "Friday hangi kararı aldı, sonucu ne oldu?"
sorusunun cevabı olmadan otonom yetki genişletilemez. outcome_score geçmiş
karar tiplerinin işe yarayıp yaramadığını Friday'in kendi context'ine geri
besler (kapalı öğrenme döngüsü).

Kullanım:
    from core import friday_decisions
    friday_decisions.log_decision("SET_PARAM", param_key="trade_threshold",
                                  old_value="55", new_value="53",
                                  reasoning="...", ctx_snapshot={...})
    friday_decisions.fill_pending_outcomes()   # saatlik loop çağırır
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

import config
import database

logger = logging.getLogger("ax.friday_decisions")

# NEDEN: Şema hem init_db() hem migration scripti tarafından kullanılır —
# tek kaynaktan (bu sabitten) beslenirler, kopya sapması olmaz.
FRIDAY_DECISIONS_DDL = """
CREATE TABLE IF NOT EXISTS friday_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    decision_type TEXT NOT NULL,
    param_key TEXT,
    old_value TEXT,
    new_value TEXT,
    reasoning TEXT,
    ctx_snapshot TEXT,
    outcome_24h TEXT,
    outcome_72h TEXT,
    outcome_score REAL
)
"""

FRIDAY_DECISIONS_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_friday_decisions_created
ON friday_decisions (created_at)
"""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def log_decision(
    decision_type: str,
    param_key: Optional[str] = None,
    old_value: Optional[str] = None,
    new_value: Optional[str] = None,
    reasoning: str = "",
    ctx_snapshot: Optional[dict] = None,
) -> Optional[int]:
    """Uygulanan bir Friday kararını günlüğe yazar. Hata yutar (karar akışını bozmaz).

    decision_type: SET_PARAM / PAUSE / RESUME / COOLDOWN / RESTART / NOOP
                   (+ mevcut aksiyonlar: RETRAIN, TUNER, SELF_HEALING, ...)
    reasoning: LLM gerekçesi — ilk 500 karakter saklanır (plan 2.1 kuralı).
    """
    try:
        snapshot_json = None
        if ctx_snapshot is not None:
            try:
                snapshot_json = json.dumps(ctx_snapshot, default=str)[:4000]
            except Exception:
                snapshot_json = None
        with database.get_conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO friday_decisions
                    (created_at, decision_type, param_key, old_value, new_value, reasoning, ctx_snapshot)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _utcnow().isoformat(),
                    str(decision_type).upper(),
                    param_key,
                    str(old_value) if old_value is not None else None,
                    str(new_value) if new_value is not None else None,
                    (reasoning or "")[:500],
                    snapshot_json,
                ),
            )
            decision_id = cur.lastrowid
        logger.info("[FridayDecisions] Karar loglandı #%s: %s %s %s→%s",
                    decision_id, decision_type, param_key or "-", old_value, new_value)
        return decision_id
    except Exception as e:
        logger.error("[FridayDecisions] Karar loglanamadı: %s", e)
        return None


def build_ctx_snapshot(ctx: dict) -> dict:
    """get_system_context() çıktısından karar anı özet metriklerini süzer.

    NEDEN: Tam context çok büyük (open_trades detayları vb.) — karar anında
    yalnızca sonuç değerlendirmesi için gereken metrikler saklanır.
    """
    try:
        return {
            "balance": ctx.get("balance"),
            "today_pnl": ctx.get("today_pnl"),
            "regime": ctx.get("market_regime"),
            "open_trades": len(ctx.get("open_trades") or []),
            "today_trades": ctx.get("today_trades"),
            "expectancy_72h_r": _window_expectancy(
                (_utcnow() - timedelta(hours=72)).isoformat(),
                _utcnow().isoformat(),
            ).get("expectancy_r"),
        }
    except Exception:
        return {}


# ── Sonuç (outcome) hesaplama ────────────────────────────────────────────────

def _trade_r(row) -> Optional[float]:
    """Kapanmış trade'in R-multiple değerini döner; hesaplanamıyorsa None."""
    try:
        r = float(row["r_multiple"] or 0.0)
        if r != 0.0:
            return r
        risk_usd = float(row["risk_usd"] or 0.0)
        if risk_usd > 0:
            return float(row["net_pnl"] or 0.0) / risk_usd
    except Exception:
        pass
    return None


def _window_expectancy(start_iso: str, end_iso: str, environment: Optional[str] = None) -> dict:
    """Zaman penceresindeki kapanmış trade'lerden expectancy hesaplar.

    E = (WR × AvgWin_R) − ((1−WR) × |AvgLoss_R|)
    NOT: Faz 3.1'deki genel calculate_expectancy(days)'in karar-penceresi
    versiyonudur; Faz 3 geldiğinde formül aynı kalır.
    """
    env = environment or getattr(config, "EXECUTION_MODE", "paper")
    result = {"expectancy_r": None, "win_rate": None, "n": 0, "pnl_sum": 0.0}
    try:
        with database.get_conn() as conn:
            rows = conn.execute(
                """
                SELECT net_pnl, risk_usd, r_multiple FROM trades
                WHERE status = 'closed'
                  AND close_time >= ? AND close_time < ?
                  AND environment = ?
                  AND COALESCE(is_valid_for_stats, 1) = 1
                """,
                (start_iso, end_iso, env),
            ).fetchall()
        r_values = [r for r in (_trade_r(row) for row in rows) if r is not None]
        result["pnl_sum"] = round(sum(float(row["net_pnl"] or 0.0) for row in rows), 2)
        result["n"] = len(r_values)
        if not r_values:
            return result
        wins = [r for r in r_values if r > 0]
        losses = [r for r in r_values if r <= 0]
        wr = len(wins) / len(r_values)
        avg_win = (sum(wins) / len(wins)) if wins else 0.0
        avg_loss = abs(sum(losses) / len(losses)) if losses else 0.0
        result["win_rate"] = round(wr, 4)
        result["expectancy_r"] = round((wr * avg_win) - ((1.0 - wr) * avg_loss), 4)
        return result
    except Exception as e:
        logger.debug("[FridayDecisions] window expectancy hatası: %s", e)
        return result


def _trades_opened_count(start_iso: str, end_iso: str, environment: Optional[str] = None) -> int:
    env = environment or getattr(config, "EXECUTION_MODE", "paper")
    try:
        with database.get_conn() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) FROM trades
                WHERE open_time >= ? AND open_time < ? AND environment = ?
                """,
                (start_iso, end_iso, env),
            ).fetchone()
        return int(row[0] or 0)
    except Exception:
        return 0


def _compute_outcome(created_at: datetime, hours: int) -> dict:
    """Karar anına göre pre/post pencere metriklerini hesaplar."""
    pre_start = (created_at - timedelta(hours=hours)).isoformat()
    decision_iso = created_at.isoformat()
    post_end = (created_at + timedelta(hours=hours)).isoformat()

    pre = _window_expectancy(pre_start, decision_iso)
    post = _window_expectancy(decision_iso, post_end)

    wr_delta = None
    if pre["win_rate"] is not None and post["win_rate"] is not None:
        wr_delta = round(post["win_rate"] - pre["win_rate"], 4)

    return {
        "pnl_delta": post["pnl_sum"],
        "wr_delta": wr_delta,
        "trades_opened": _trades_opened_count(decision_iso, post_end),
        "expectancy_pre": pre["expectancy_r"],
        "expectancy_post": post["expectancy_r"],
        "n_pre": pre["n"],
        "n_post": post["n"],
    }


def fill_pending_outcomes(now: Optional[datetime] = None) -> int:
    """24h/72h dolmuş ama outcome'u boş kararları doldurur. Doldurulan alan sayısını döner.

    outcome_score = (karar sonrası 72h expectancy) − (karar öncesi 72h expectancy),
    [-1, +1] aralığına clamp edilir (plan 2.1 formülü).
    """
    now = now or _utcnow()
    filled = 0
    try:
        cutoff_24 = (now - timedelta(hours=24)).isoformat()
        cutoff_72 = (now - timedelta(hours=72)).isoformat()
        with database.get_conn() as conn:
            pending_24 = conn.execute(
                "SELECT id, created_at FROM friday_decisions "
                "WHERE outcome_24h IS NULL AND created_at < ?",
                (cutoff_24,),
            ).fetchall()
            pending_72 = conn.execute(
                "SELECT id, created_at FROM friday_decisions "
                "WHERE outcome_72h IS NULL AND created_at < ?",
                (cutoff_72,),
            ).fetchall()

        for row in pending_24:
            try:
                created = datetime.fromisoformat(str(row["created_at"]))
                outcome = _compute_outcome(created, 24)
                with database.get_conn() as conn:
                    conn.execute(
                        "UPDATE friday_decisions SET outcome_24h = ? WHERE id = ?",
                        (json.dumps(outcome), row["id"]),
                    )
                filled += 1
            except Exception as e:
                logger.error("[FridayDecisions] 24h outcome doldurulamadı #%s: %s", row["id"], e)

        for row in pending_72:
            try:
                created = datetime.fromisoformat(str(row["created_at"]))
                outcome = _compute_outcome(created, 72)
                score = None
                if outcome["expectancy_pre"] is not None and outcome["expectancy_post"] is not None:
                    raw = outcome["expectancy_post"] - outcome["expectancy_pre"]
                    score = max(-1.0, min(1.0, round(raw, 4)))
                with database.get_conn() as conn:
                    conn.execute(
                        "UPDATE friday_decisions SET outcome_72h = ?, outcome_score = ? WHERE id = ?",
                        (json.dumps(outcome), score, row["id"]),
                    )
                filled += 1
            except Exception as e:
                logger.error("[FridayDecisions] 72h outcome doldurulamadı #%s: %s", row["id"], e)
    except Exception as e:
        logger.error("[FridayDecisions] fill_pending_outcomes hatası: %s", e)
    return filled


# ── Okuma / sunum yardımcıları ───────────────────────────────────────────────

def get_recent_decisions(limit: int = 10) -> list[dict]:
    """Son N kararı (en yenisi önce) özet dict listesi olarak döner."""
    try:
        with database.get_conn() as conn:
            rows = conn.execute(
                """
                SELECT id, created_at, decision_type, param_key, old_value, new_value,
                       reasoning, outcome_score
                FROM friday_decisions
                ORDER BY id DESC LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.debug("[FridayDecisions] get_recent_decisions hatası: %s", e)
        return []


def summarize_for_context(limit: int = 10) -> list[dict]:
    """Friday'in system context'ine girecek kompakt karar özeti (Faz 2.1).

    NEDEN: LLM prompt'una tam satırlar değil, karar tipi + değişim +
    outcome_score yeterli — token tasarrufu ve sinyal yoğunluğu.
    """
    out = []
    for d in get_recent_decisions(limit):
        out.append({
            "type": d.get("decision_type"),
            "change": (
                f"{d.get('param_key')}: {d.get('old_value')}→{d.get('new_value')}"
                if d.get("param_key") else None
            ),
            "outcome_score": d.get("outcome_score"),
        })
    return out


def format_decisions_table(limit: int = 10) -> str:
    """/friday_decisions Telegram komutu çıktısı — son N karar + skorlar."""
    decisions = get_recent_decisions(limit)
    if not decisions:
        return "🤖 Friday henüz hiç karar loglamadı."
    lines = [f"🤖 <b>Friday Karar Günlüğü</b> (son {len(decisions)})", "━" * 22]
    for d in decisions:
        ts = str(d.get("created_at") or "")[:16].replace("T", " ")
        dtype = d.get("decision_type") or "?"
        change = ""
        if d.get("param_key"):
            change = f" {d['param_key']}: {d.get('old_value')}→{d.get('new_value')}"
        score = d.get("outcome_score")
        if score is None:
            score_str = "⏳"
        elif score > 0.05:
            score_str = f"🟢 {score:+.2f}"
        elif score < -0.05:
            score_str = f"🔴 {score:+.2f}"
        else:
            score_str = f"⚪ {score:+.2f}"
        lines.append(f"<code>{ts}</code> {dtype}{change} | {score_str}")
    lines.append("━" * 22)
    lines.append("⏳ = sonuç penceresi (72h) henüz dolmadı")
    return "\n".join(lines)
