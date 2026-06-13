"""
core/param_gate.py — Parametre Değişikliği Doğrulama Kapısı (Faz 3.2)
=====================================================================
Amaç: Hiçbir OTONOM parametre değişikliği simülasyon kanıtı olmadan uygulanmaz.

validate_param_change(key, old, new) -> (approved: bool, report: dict)

Mantık:
  - Gate yalnızca şu key'ler için çalışır: trade_threshold, rsi_limit,
    cvd_filter_val, risk_pct, sl_atr_mult, tp_atr_mult. Diğerleri serbest geçer.
  - Son 30 günün kapanmış trade verisi üzerinde iki konfigürasyonu HIZLI simüle
    eder — scripts/backtest_engine.py'deki BacktestEngine altyapısını KULLANIR
    (yeniden yazmaz). Her config için expectancy (R) hesaplanır.
  - Yeni expectancy ≥ eski × 0.95 ise ONAY; değilse RED + Friday'e geri bildirim.
  - Veri yetersizse (<50 örnek): "küçük adım kuralı" — değişiklik max %2 adımla.

Manuel /set komutu bu gate'i BYPASS eder (boss kararı) — çağırmaz.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple

logger = logging.getLogger("ax.param_gate")

# Yalnız bu key'ler gate'ten geçer (plan 3.2). Diğerleri serbest.
GATED_KEYS = {
    "trade_threshold", "rsi_limit", "cvd_filter_val",
    "risk_pct", "sl_atr_mult", "tp_atr_mult",
}

# Exit mekaniğini değiştiren key'ler (mae/mfe ile yeniden simüle edilir)
_EXIT_KEYS = {"sl_atr_mult", "tp_atr_mult"}
# Giriş filtresini değiştiren key'ler (hangi işlemler alınırdı)
_FILTER_KEYS = {"trade_threshold", "rsi_limit", "cvd_filter_val"}

MIN_SAMPLES = 50          # plan: <50 örnek = veri yetersiz
SMALL_STEP_PCT = 0.02     # plan: veri yetersizse max %2 adım
APPROVAL_RATIO = 0.95     # plan: yeni E ≥ eski × 0.95


def _load_recent_trades(days: int = 30, environment: Optional[str] = None) -> list[dict]:
    """Son `days` günün kapanmış trade'lerini simülasyon için yükler."""
    import database
    import config as _cfg
    env = environment or getattr(_cfg, "EXECUTION_MODE", "paper")
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        with database.get_conn() as conn:
            rows = conn.execute(
                """
                SELECT symbol, direction, entry, sl, tp1, tp2, tp3, leverage,
                       net_pnl, realized_pnl, risk_usd, r_multiple, final_score,
                       mae, mfe, close_reason, metadata
                FROM trades
                WHERE status = 'closed' AND close_time >= ? AND environment = ?
                  AND COALESCE(is_valid_for_stats, 1) = 1 AND entry > 0 AND sl > 0
                """,
                (cutoff, env),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error("[ParamGate] Trade yükleme hatası: %s", e)
        return []


def _meta_field(trade: dict, *names) -> Optional[float]:
    """metadata JSON'ından ilk bulunan sayısal alanı döner."""
    import json
    meta = trade.get("metadata")
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            return None
    if not isinstance(meta, dict):
        return None
    for n in names:
        if n in meta and meta[n] is not None:
            try:
                return float(meta[n])
            except Exception:
                continue
    return None


def _passes_filter(trade: dict, key: str, value: float) -> bool:
    """Giriş filtresi: bu trade `value` eşiğinde alınır mıydı?

    NEDEN: Filtre key'leri (threshold/rsi/cvd) HANGİ işlemlerin alınacağını
    değiştirir. Eşiği geçemeyen trade simülasyondan düşer. Veride ilgili metrik
    yoksa filtre uygulanamaz → trade dahil edilir (eşit muamele, taraf tutmaz).
    """
    if key == "trade_threshold":
        score = trade.get("final_score")
        if score is None:
            return True
        return float(score or 0) >= value
    if key == "rsi_limit":
        rsi = _meta_field(trade, "rsi5", "rsi", "rsi1")
        if rsi is None:
            return True
        return rsi >= value
    if key == "cvd_filter_val":
        cvd = _meta_field(trade, "cvd_value", "cvd", "cvd_slope")
        if cvd is None:
            return True
        return cvd >= value
    return True


def _derive_outcome_from_history(trade: dict) -> str:
    """close_reason/net_pnl'den BacktestEngine outcome'u çıkarır (run_from_db ile aynı)."""
    cr = str(trade.get("close_reason") or "").upper()
    rpnl = float(trade.get("net_pnl") or trade.get("realized_pnl") or 0)
    if "TP3" in cr or "FULL" in cr:
        return "tp3"
    if "TP2" in cr:
        return "tp2"
    if "TP1" in cr or rpnl > 0:
        return "tp1"
    if "TIMEOUT" in cr:
        return "tp1" if rpnl > 0 else "sl"
    return "sl"


def _derive_outcome_from_mae_mfe(trade: dict, sl_mult: float, tp_mult: float,
                                 baseline_sl_mult: float) -> tuple:
    """Yeni SL/TP çarpanlarına göre outcome + yeniden kurulmuş SL/TP fiyatları.

    NEDEN: Exit key'leri (sl/tp_atr_mult) mae/mfe ile yeniden simüle edilir —
    optimize_parameters'taki kanıtlanmış mantığın aynısı.
    """
    entry = float(trade.get("entry") or 0)
    sl_hist = float(trade.get("sl") or 0)
    direction = str(trade.get("direction") or "LONG").upper()
    mae = float(trade.get("mae") or 0)
    mfe = float(trade.get("mfe") or 0)

    sl_dist = abs(entry - sl_hist)
    sl_pct_hist = (sl_dist / entry) if entry > 0 else 0.015
    atr_pct = (sl_pct_hist / baseline_sl_mult) if baseline_sl_mult > 0 else sl_pct_hist
    sim_sl_pct = atr_pct * sl_mult
    sim_tp1_pct = atr_pct * tp_mult
    sim_tp2_pct = sim_tp1_pct * 1.6
    sim_tp3_pct = sim_tp1_pct * 2.5

    if direction == "LONG":
        new_sl = entry * (1 - sim_sl_pct)
        new_tp1 = entry * (1 + sim_tp1_pct)
        new_tp2 = entry * (1 + sim_tp2_pct)
        new_tp3 = entry * (1 + sim_tp3_pct)
    else:
        new_sl = entry * (1 + sim_sl_pct)
        new_tp1 = entry * (1 - sim_tp1_pct)
        new_tp2 = entry * (1 - sim_tp2_pct)
        new_tp3 = entry * (1 - sim_tp3_pct)

    # mae/mfe pozitif fraksiyon varsayımı (optimize_parameters ile aynı)
    if mae >= sim_sl_pct and mae > 0:
        outcome = "sl"
    elif mfe >= sim_tp3_pct and mfe > 0:
        outcome = "tp3"
    elif mfe >= sim_tp2_pct and mfe > 0:
        outcome = "tp2"
    elif mfe >= sim_tp1_pct and mfe > 0:
        outcome = "tp1"
    else:
        # Ne SL ne TP net vuruldu → gerçek sonucun işaretini taklit et
        outcome = "tp1" if float(trade.get("net_pnl") or 0) > 0 else "sl"
    return outcome, new_sl, new_tp1, new_tp2, new_tp3


def _expectancy_r_from_results(results: list[dict]) -> Tuple[float, int]:
    """BacktestEngine.results'tan expectancy (R) ve örneklem sayısı hesaplar."""
    r_vals = [float(r.get("r_multiple") or 0) for r in results if not r.get("skipped")]
    n = len(r_vals)
    if n == 0:
        return 0.0, 0
    wins = [r for r in r_vals if r > 0]
    losses = [r for r in r_vals if r <= 0]
    wr = len(wins) / n
    awr = (sum(wins) / len(wins)) if wins else 0.0
    alr = abs(sum(losses) / len(losses)) if losses else 0.0
    return round((wr * awr) - ((1 - wr) * alr), 4), n


def _simulate_expectancy(trades: list[dict], key: str, value: float) -> Tuple[float, int]:
    """Verilen config (key=value) altında 30g veride expectancy (R) simüle eder.

    BacktestEngine.simulate_trade altyapısını kullanır (fee/slippage/partial close).
    """
    from scripts.backtest_engine import BacktestEngine
    import config as _cfg

    baseline_sl_mult = float(getattr(_cfg, "SL_ATR_MULT", 1.8)) or 1.8
    baseline_tp_mult = float(getattr(_cfg, "TP2_R", 2.5)) or 2.5

    engine = BacktestEngine()
    for t in trades:
        # 1) Giriş filtresi key'i ise: eşiği geçemeyen trade atlanır
        if key in _FILTER_KEYS and not _passes_filter(t, key, value):
            continue

        entry = float(t.get("entry") or 0)
        sl = float(t.get("sl") or 0)
        leverage = int(t.get("leverage") or 10)
        direction = str(t.get("direction") or "LONG").upper()
        if entry <= 0 or sl <= 0:
            continue

        if key in _EXIT_KEYS:
            sl_mult = value if key == "sl_atr_mult" else baseline_sl_mult
            tp_mult = value if key == "tp_atr_mult" else baseline_tp_mult
            outcome, new_sl, tp1, tp2, tp3 = _derive_outcome_from_mae_mfe(
                t, sl_mult, tp_mult, baseline_sl_mult
            )
            engine.simulate_trade(entry, new_sl, tp1, tp2, tp3, direction,
                                  leverage=leverage, outcome=outcome)
        else:
            # Filtre/risk key'leri: gerçek exit mekaniği korunur
            tp1 = float(t.get("tp1") or 0) or entry * 1.01
            tp2 = float(t.get("tp2") or 0) or tp1
            tp3 = float(t.get("tp3") or 0) or tp1
            outcome = _derive_outcome_from_history(t)
            engine.simulate_trade(entry, sl, tp1, tp2, tp3, direction,
                                  leverage=leverage, outcome=outcome)

    return _expectancy_r_from_results(engine.results)


def _cap_small_step(old: float, new: float) -> float:
    """Veri yetersizse değişikliği max %2 adımla sınırlar (plan kuralı)."""
    max_step = abs(old) * SMALL_STEP_PCT
    if max_step == 0:
        max_step = SMALL_STEP_PCT  # old=0 ise mutlak %2'lik küçük adım
    if abs(new - old) <= max_step:
        return new
    return round(old + (max_step if new > old else -max_step), 6)


def validate_param_change(key: str, old, new, days: int = 30,
                          environment: Optional[str] = None) -> Tuple[bool, dict]:
    """Parametre değişikliğini simülasyonla doğrular.

    Returns: (approved, report). report alanları:
        gated, key, old, new, reason, ve gate çalıştıysa:
        old_expectancy_r, new_expectancy_r, n_samples, approved;
        veri yetersizse: insufficient_data, applied_value.
    """
    key_l = str(key).lower()
    report = {"gated": False, "key": key_l, "old": old, "new": new}

    if key_l not in GATED_KEYS:
        report["reason"] = "key gate kapsamında değil — serbest geçti"
        return True, report

    try:
        old_f, new_f = float(old), float(new)
    except (TypeError, ValueError):
        report["reason"] = "sayısal olmayan değer — gate atlandı"
        return True, report

    report["gated"] = True

    if old_f == new_f:
        report["reason"] = "değişiklik yok"
        return True, report

    trades = _load_recent_trades(days=days, environment=environment)
    n = len(trades)
    report["n_samples"] = n

    # Veri yetersiz → küçük adım kuralı
    if n < MIN_SAMPLES:
        capped = _cap_small_step(old_f, new_f)
        report.update({
            "insufficient_data": True,
            "applied_value": capped,
            "reason": (
                f"Veri yetersiz ({n}<{MIN_SAMPLES} örnek). Küçük adım kuralı: "
                f"değişiklik max %{SMALL_STEP_PCT*100:.0f} adımla {old_f}→{capped} uygulandı."
            ),
        })
        # Onaylanır ama uygulanacak değer kısıtlanmıştır (çağıran applied_value kullanmalı)
        return True, report

    old_e, n_old = _simulate_expectancy(trades, key_l, old_f)
    new_e, n_new = _simulate_expectancy(trades, key_l, new_f)
    report.update({
        "old_expectancy_r": old_e, "new_expectancy_r": new_e,
        "n_sim_old": n_old, "n_sim_new": n_new,
    })

    # Onay eşiği: yeni E ≥ eski × 0.95 (eski negatifse, |eşik| genişler — iyileşmeye izin)
    threshold = old_e * APPROVAL_RATIO if old_e >= 0 else old_e / APPROVAL_RATIO
    approved = new_e >= threshold
    report["approved"] = approved

    if approved:
        report["reason"] = (
            f"Onaylandı: simülasyon E {old_e:+.3f}R → {new_e:+.3f}R "
            f"(eşik {threshold:+.3f}R, n={n})."
        )
    else:
        if abs(old_e) > 1e-9:
            pct_worse = (old_e - new_e) / abs(old_e) * 100.0
            report["reason"] = (
                f"Önerin simülasyonda %{pct_worse:.1f} daha kötü "
                f"(E: {old_e:+.3f}R → {new_e:+.3f}R). Karar reddedildi."
            )
        else:
            report["reason"] = (
                f"Önerin simülasyonda daha kötü (E: {old_e:+.3f}R → {new_e:+.3f}R). "
                f"Karar reddedildi."
            )
    return approved, report
