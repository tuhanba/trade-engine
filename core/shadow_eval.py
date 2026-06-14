"""
core/shadow_eval.py — Shadow-mode Parametre A/B (Faz 6.4)
=========================================================
Friday'in (param_gate tarafından) REDDEDİLEN parametre önerileri "gölge" olarak
kaydedilir; 72 saat sonra GÜNCEL veride yeniden simüle edilerek reddin hindsight'ta
doğru olup olmadığı değerlendirilir. "Uygulasaydık ne olurdu" verisi.

NEDEN: Reddedilen öneriler kör nokta olur — bazen gate fazla muhafazakâr olabilir.
Shadow A/B, gate'in karar kalitesini ölçülebilir kılar (öğrenme döngüsü).

Akış:
  1. param_gate reddi → record_shadow(...) (rejection anı sim E'leri ile)
  2. _friday_outcome_loop (saatlik) → evaluate_pending_shadows() 72h dolanları
     güncel veride yeniden simüle eder, verdict yazar:
       rejection_correct  : reddedilen config hâlâ daha kötü → gate haklıydı
       rejection_wrong    : reddedilen config artık daha iyi → kaçırılmış fırsat
       inconclusive       : veri yetersiz / belirsiz
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import database

logger = logging.getLogger("ax.shadow_eval")

SHADOW_DDL = """
CREATE TABLE IF NOT EXISTS shadow_evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    param_key TEXT NOT NULL,
    kept_value TEXT,
    rejected_value TEXT,
    sim_old_e REAL,
    sim_new_e REAL,
    evaluated_at TEXT,
    eval_old_e REAL,
    eval_new_e REAL,
    verdict TEXT
)
"""
SHADOW_INDEX_DDL = "CREATE INDEX IF NOT EXISTS idx_shadow_created ON shadow_evaluations (created_at)"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def record_shadow(param_key: str, kept_value, rejected_value,
                  sim_old_e: Optional[float], sim_new_e: Optional[float]) -> Optional[int]:
    """Reddedilen bir öneriyi gölge değerlendirme için kaydeder (hata yutar)."""
    try:
        with database.get_conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO shadow_evaluations
                    (created_at, param_key, kept_value, rejected_value, sim_old_e, sim_new_e)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (_utcnow().isoformat(), str(param_key),
                 str(kept_value), str(rejected_value), sim_old_e, sim_new_e),
            )
            sid = cur.lastrowid
        logger.info("[Shadow] kayıt #%s: %s kept=%s rejected=%s (sim E %.3f→%.3f)",
                    sid, param_key, kept_value, rejected_value,
                    sim_old_e or 0.0, sim_new_e or 0.0)
        return sid
    except Exception as e:
        logger.error("[Shadow] record hatası: %s", e)
        return None


def evaluate_pending_shadows(now: Optional[datetime] = None) -> int:
    """72h dolmuş gölge kayıtlarını güncel veride yeniden simüle eder, verdict yazar.

    Returns: değerlendirilen kayıt sayısı.
    """
    now = now or _utcnow()
    evaluated = 0
    try:
        cutoff = (now - timedelta(hours=72)).isoformat()
        with database.get_conn() as conn:
            rows = conn.execute(
                "SELECT id, param_key, kept_value, rejected_value FROM shadow_evaluations "
                "WHERE evaluated_at IS NULL AND created_at < ?",
                (cutoff,),
            ).fetchall()
        from core.param_gate import validate_param_change
        for r in rows:
            try:
                # Güncel veride: reddedilen öneri ŞİMDİ onaylanır mıydı?
                approved, rep = validate_param_change(
                    r["param_key"], float(r["kept_value"]), float(r["rejected_value"])
                )
                eo = rep.get("old_expectancy_r")
                en = rep.get("new_expectancy_r")
                if rep.get("insufficient_data") or eo is None or en is None:
                    verdict = "inconclusive"
                elif approved:
                    verdict = "rejection_wrong"   # reddedilen artık daha iyi — kaçırılmış fırsat
                else:
                    verdict = "rejection_correct"  # gate hâlâ haklı
                with database.get_conn() as conn:
                    conn.execute(
                        "UPDATE shadow_evaluations SET evaluated_at=?, eval_old_e=?, eval_new_e=?, verdict=? WHERE id=?",
                        (now.isoformat(), eo, en, verdict, r["id"]),
                    )
                evaluated += 1
            except Exception as e:
                logger.error("[Shadow] #%s değerlendirilemedi: %s", r["id"], e)
    except Exception as e:
        logger.error("[Shadow] evaluate hatası: %s", e)
    return evaluated


def summarize_shadows(limit: int = 20) -> dict:
    """Gölge değerlendirme özeti — gate karar kalitesi göstergesi."""
    out = {"total": 0, "correct": 0, "wrong": 0, "inconclusive": 0, "pending": 0, "recent": []}
    try:
        with database.get_conn() as conn:
            rows = conn.execute(
                "SELECT param_key, kept_value, rejected_value, sim_old_e, sim_new_e, "
                "verdict, created_at FROM shadow_evaluations ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            for v, in conn.execute("SELECT verdict FROM shadow_evaluations"):
                out["total"] += 1
                if v == "rejection_correct":
                    out["correct"] += 1
                elif v == "rejection_wrong":
                    out["wrong"] += 1
                elif v == "inconclusive":
                    out["inconclusive"] += 1
                else:
                    out["pending"] += 1
        out["recent"] = [dict(r) for r in rows]
        decided = out["correct"] + out["wrong"]
        out["gate_accuracy"] = round(out["correct"] / decided * 100, 1) if decided else None
    except Exception as e:
        logger.debug("[Shadow] summarize hatası: %s", e)
    return out


def format_report(limit: int = 10) -> str:
    """/shadow komutu çıktısı — gate karar kalitesi + son gölge değerlendirmeler."""
    s = summarize_shadows(limit)
    if s["total"] == 0:
        return "🌓 <b>Shadow A/B</b>\n\nHenüz reddedilmiş öneri kaydı yok."
    acc = f"%{s['gate_accuracy']}" if s.get("gate_accuracy") is not None else "—"
    lines = [
        "🌓 <b>Shadow A/B — Gate Karar Kalitesi</b>",
        "━━━━━━━━━━━━━━",
        f"Gate isabeti: <b>{acc}</b> "
        f"(✅ {s['correct']} doğru red / ❌ {s['wrong']} kaçırılmış / ⏳ {s['pending']} bekliyor)",
        "",
    ]
    for r in s["recent"][:limit]:
        v = r.get("verdict")
        icon = {"rejection_correct": "✅", "rejection_wrong": "❌", "inconclusive": "⚪"}.get(v, "⏳")
        lines.append(
            f"{icon} {r['param_key']}: {r['kept_value']}↔{r['rejected_value']} "
            f"(sim E {float(r['sim_old_e'] or 0):+.2f}→{float(r['sim_new_e'] or 0):+.2f})"
        )
    return "\n".join(lines)
