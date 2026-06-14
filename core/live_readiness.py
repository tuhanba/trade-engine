"""
core/live_readiness.py — Live-Readiness Protokolü (Faz 3.3)
============================================================
Canlı (live) işleme geçmeden önce 5 kapının HEPSİ yeşil olmalı:

  1. Paper'da son 30 günde ≥100 kapanmış trade
  2. 30 günlük expectancy > 0 VE son 14 gün expectancy > 0
  3. Max drawdown < %8 (son 30 gün, balance_ledger peak-to-trough)
  4. Sistem uptime ≥ %99 (heartbeat tazeliği + günlük özet kapsama analizi)
  5. P0/P1 açık bug yok (manuel onay state key: live_readiness_manual_ok)

check() -> {ready: bool, gates: [...]}  → /live komutu ve dashboard kartı kullanır.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger("ax.live_readiness")

# Kapı eşikleri (plan 3.3)
MIN_CLOSED_TRADES = 100
MAX_DRAWDOWN_PCT = 8.0
MIN_UPTIME_PCT = 99.0
PAPER_ENV = "paper"


def _gate(name: str, passed: bool, detail: str, value=None) -> dict:
    return {"name": name, "passed": bool(passed), "detail": detail, "value": value}


def _count_closed_trades(days: int = 30) -> int:
    import database
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        with database.get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM trades WHERE status='closed' AND close_time >= ? "
                "AND environment = ? AND COALESCE(is_valid_for_stats,1)=1",
                (cutoff, PAPER_ENV),
            ).fetchone()
        return int(row[0] or 0)
    except Exception as e:
        logger.debug("[LiveReadiness] trade sayımı hatası: %s", e)
        return 0


def _max_drawdown_pct(days: int = 30) -> float:
    """balance_ledger üzerinden son `days` günün peak-to-trough max drawdown %'si."""
    import database
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        with database.get_conn() as conn:
            rows = conn.execute(
                "SELECT balance_after FROM balance_ledger WHERE created_at >= ? ORDER BY id ASC",
                (cutoff,),
            ).fetchall()
        balances = [float(r[0] or 0) for r in rows if r[0] is not None]
        if len(balances) < 2:
            return 0.0
        peak = balances[0]
        max_dd = 0.0
        for b in balances:
            if b > peak:
                peak = b
            if peak > 0:
                dd = (peak - b) / peak * 100.0
                max_dd = max(max_dd, dd)
        return round(max_dd, 2)
    except Exception as e:
        logger.debug("[LiveReadiness] drawdown hesap hatası: %s", e)
        return 0.0


# Heartbeat örnekleme periyodu (engine ~5 dk'da bir yazar); bu sürenin 2 katından
# (10 dk) uzun boşluk "downtime" sayılır.
HEARTBEAT_INTERVAL_SEC = 300
HEARTBEAT_GAP_TOLERANCE_SEC = 2 * HEARTBEAT_INTERVAL_SEC


def _uptime_from_history(days: int = 30) -> tuple[float, str] | None:
    """heartbeat_history boşluk analizinden gerçek uptime%'si.

    Operasyonel pencere = ilk örnek → şimdi. 10 dk'dan uzun her boşluk downtime
    sayılır. <2 örnek varsa None döner (çağıran proxy'ye düşer).
    """
    import database
    samples = database.get_heartbeat_samples(days=days)
    if len(samples) < 2:
        return None
    try:
        ts = []
        for s in samples:
            dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            ts.append(dt)
        ts.sort()
        now = datetime.now(timezone.utc)
        # Operasyonel pencere: ilk örnekten şimdiye (sistemin var olmadığı süreyi cezalandırma)
        points = ts + [now]
        span = (now - ts[0]).total_seconds()
        if span <= 0:
            return None
        downtime = 0.0
        for a, b in zip(points[:-1], points[1:]):
            gap = (b - a).total_seconds()
            if gap > HEARTBEAT_GAP_TOLERANCE_SEC:
                downtime += gap
        uptime = max(0.0, (span - downtime) / span * 100.0)
        op_days = span / 86400.0
        return round(uptime, 2), f"boşluk analizi: {len(ts)} örnek, {op_days:.1f}g operasyonel pencere"
    except Exception:
        return None


def _uptime_pct(days: int = 30) -> tuple[float, str]:
    """Uptime: önce heartbeat_history boşluk analizi (gerçek ölçüm); yeterli
    örnek yoksa proxy (anlık tazelik + daily_summary kapsama) fallback.

    NEDEN (sertleştirme): Artık periyodik heartbeat örnekleri tutuluyor, gerçek
    boşluk analizi yapılabiliyor. Geçmiş yokken (yeni deploy) proxy korunur.
    """
    real = _uptime_from_history(days)
    if real is not None:
        return real

    import database
    # Fallback proxy — (a) anlık heartbeat tazeliği
    fresh = False
    try:
        hb = database.get_bot_status("heartbeat") or {}
        val = hb.get("value") or ""
        if val:
            hb_dt = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
            if hb_dt.tzinfo is None:
                hb_dt = hb_dt.replace(tzinfo=timezone.utc)
            fresh = (datetime.now(timezone.utc) - hb_dt).total_seconds() < 120
    except Exception:
        pass

    # (b) daily_summary kapsama oranı
    try:
        summaries = database.get_daily_summaries(days=days, environment=PAPER_ENV)
        covered = len({str(s.get("date")) for s in summaries})
    except Exception:
        covered = 0
    coverage_pct = (covered / max(1, days)) * 100.0

    uptime = coverage_pct if fresh else min(coverage_pct, 50.0)
    detail = f"proxy (geçmiş yok): heartbeat={'canlı' if fresh else 'BAYAT'}, günlük kapsama {covered}/{days}"
    return round(uptime, 1), detail


def check(environment: str | None = None) -> dict:
    """5 kapıyı çalıştırır. Returns: {ready, gates, summary}."""
    import database
    from core.accounting import calculate_expectancy

    gates = []

    # Kapı 1: ≥100 kapanmış paper trade (30 gün)
    n_trades = _count_closed_trades(30)
    gates.append(_gate(
        "min_trades", n_trades >= MIN_CLOSED_TRADES,
        f"Son 30g kapanmış paper işlem: {n_trades}/{MIN_CLOSED_TRADES}", n_trades,
    ))

    # Kapı 2: 30g expectancy > 0 VE son 14g expectancy > 0
    exp30 = calculate_expectancy(days=30, environment=PAPER_ENV)
    exp14 = calculate_expectancy(days=14, environment=PAPER_ENV)
    e30, e14 = exp30.get("expectancy_r", 0.0), exp14.get("expectancy_r", 0.0)
    gates.append(_gate(
        "positive_expectancy", e30 > 0 and e14 > 0,
        f"Expectancy 30g={e30:+.3f}R, 14g={e14:+.3f}R (ikisi de >0 olmalı)",
        {"e30": e30, "e14": e14},
    ))

    # Kapı 3: Max drawdown < %8
    dd = _max_drawdown_pct(30)
    gates.append(_gate(
        "drawdown", dd < MAX_DRAWDOWN_PCT,
        f"Max drawdown (30g): %{dd:.2f} (< %{MAX_DRAWDOWN_PCT:.0f} olmalı)", dd,
    ))

    # Kapı 4: Uptime ≥ %99
    uptime, up_detail = _uptime_pct(30)
    gates.append(_gate(
        "uptime", uptime >= MIN_UPTIME_PCT,
        f"Uptime ≈ %{uptime:.1f} (≥ %{MIN_UPTIME_PCT:.0f} olmalı) — {up_detail}", uptime,
    ))

    # Kapı 5: P0/P1 açık bug yok (manuel onay state key)
    manual_ok = str(database.get_system_state("live_readiness_manual_ok", default="false")).lower() in ("true", "1", "yes")
    gates.append(_gate(
        "no_critical_bugs", manual_ok,
        "P0/P1 açık bug yok — manuel onay: " + ("✓ onaylı" if manual_ok else "✗ bekliyor (/set live_readiness_manual_ok true)"),
        manual_ok,
    ))

    ready = all(g["passed"] for g in gates)
    passed_count = sum(1 for g in gates if g["passed"])
    return {
        "ready": ready,
        "gates": gates,
        "summary": f"{passed_count}/{len(gates)} kapı geçti",
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def format_report(result: dict | None = None) -> str:
    """check() çıktısını Telegram/insan-okunur metne çevirir."""
    if result is None:
        result = check()
    lines = ["🚦 <b>Live-Readiness Protokolü</b>", "━" * 22]
    for g in result["gates"]:
        icon = "🟢" if g["passed"] else "🔴"
        lines.append(f"{icon} {g['detail']}")
    lines.append("━" * 22)
    if result["ready"]:
        lines.append("✅ <b>TÜM KAPILAR YEŞİL</b> — canlıya geçişe hazır.")
    else:
        lines.append(f"⛔ <b>HENÜZ HAZIR DEĞİL</b> — {result['summary']}.")
    return "\n".join(lines)
