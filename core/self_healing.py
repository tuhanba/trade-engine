"""core/self_healing.py — P0-2: Self-Healing Optuna apply policy (report-first).

NEDEN (directive Section 20): Otonom parametre yazimi KANITSIZ uygulanamaz.
Eski `_self_healing_optuna_loop`, Optuna onerisini dogrudan
`update_system_state("rsi_limit"/"cvd_filter_val", ...)` ile yaziyordu —
param_gate'i (backtest kapisi) BYPASS ederek. Bu modul o yolu kapatir:

  - Varsayilan REPORT-FIRST: hicbir parametre yazilmaz, yalniz oneri dondurulur
    ve denetim icin friday_decisions'a loglanir.
  - SELF_HEALING_AUTO_APPLY=True: her degisiklik param_gate.validate_param_change
    (son 30g trade verisi uzerinde expectancy simulasyonu) onayindan gecerse
    uygulanir; gate reddederse uygulanmaz; veri yetersizse kucuk-adim degeri
    uygulanir (param_gate karari).

Fail-safe: herhangi bir hata => o parametre UYGULANMAZ.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("ax.self_healing")


def evaluate_self_healing(proposals: dict, auto_apply: Optional[bool] = None) -> dict:
    """Self-healing parametre onerisini guvenli isler.

    Args:
        proposals: {param_key: new_value} ( or. {"rsi_limit": 55.0, "cvd_filter_val": 0.12})
        auto_apply: None => config.SELF_HEALING_AUTO_APPLY kullan.

    Returns:
        {"applied": {k: v}, "proposed": {...}, "notes": [...], "auto_apply": bool}
        `applied` yalnizca gate-onayli, gercekten yazilan degisiklikleri icerir.
    """
    import config
    import database

    if auto_apply is None:
        auto_apply = bool(getattr(config, "SELF_HEALING_AUTO_APPLY", False))

    proposed = {str(k): v for k, v in (proposals or {}).items()}
    applied: dict = {}
    notes: list[str] = []

    if not auto_apply:
        notes.append("report-first: SELF_HEALING_AUTO_APPLY kapali — uygulanmadi")
    else:
        try:
            from core import param_gate
        except Exception as e:  # pragma: no cover - import guard
            param_gate = None
            notes.append(f"param_gate yuklenemedi — uygulanmadi: {e}")

        for key, new_val in proposed.items():
            try:
                old_raw = database.get_system_state(key, default=None)
                old_val = float(old_raw) if old_raw not in (None, "", "-") else None
                if old_val is None:
                    notes.append(f"{key}: eski deger yok — guvenlik icin uygulanmadi")
                    continue
                if param_gate is None:
                    notes.append(f"{key}: gate yok — uygulanmadi")
                    continue
                approved, report = param_gate.validate_param_change(key, old_val, float(new_val))
                if approved:
                    # param_gate veri yetersizse kucuk-adim degeri onerir (applied_value)
                    val_to_apply = report.get("applied_value", new_val)
                    database.update_system_state(key, str(val_to_apply))
                    applied[key] = val_to_apply
                    notes.append(f"{key}: gate ONAY -> {val_to_apply}")
                else:
                    notes.append(f"{key}: gate RED ({report.get('reason', '')})")
            except Exception as e:
                notes.append(f"{key}: hata — uygulanmadi: {e}")

    _audit(proposed, applied, notes, bool(auto_apply))

    logger.info(
        "[SelfHealing] %s | proposed=%s applied=%s",
        "APPLIED" if applied else "REPORT-FIRST", proposed, applied,
    )
    return {"applied": applied, "proposed": proposed, "notes": notes, "auto_apply": bool(auto_apply)}


def _audit(proposed: dict, applied: dict, notes: list, auto_apply: bool) -> None:
    """Denetim izi (best-effort). DB yoksa sessizce gecer."""
    try:
        from core.friday_decisions import log_decision
        log_decision(
            decision_type="SELF_HEALING",
            param_key=",".join(proposed.keys()) or None,
            old_value=f"auto_apply={auto_apply}",
            new_value=str(applied if applied else proposed),
            reasoning="Son 20 islem WR<%50 — ghost Optuna onerisi (report-first kapisi)",
            ctx_snapshot={
                "proposed": proposed, "applied": applied,
                "notes": notes, "auto_apply": auto_apply,
            },
        )
    except Exception as e:  # pragma: no cover - audit must never break flow
        logger.debug("[SelfHealing] denetim logu basarisiz: %s", e)
