"""
dashboard_service.py — AX Dashboard Service v5.0 (Production)
==============================================================
Flask API'ye veri sağlar.
Crash olmaz, hata durumunda güvenli default döner.
Tüm veriler tek yerden yönetilir.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import config
import database
from telegram_delivery import TelegramDelivery

logger = logging.getLogger("ax.dashboard_service")

_telegram = None


def _safe_call(fn, default, *args, **kwargs):
    """Crash olmadan güvenli çağrı — exception'ı yakala, default döndür."""
    try:
        result = fn(*args, **kwargs)
        return result if result is not None else default
    except Exception as e:
        logger.error(f"[Dashboard] {fn.__name__} hata: {e}")
        return default

def _get_telegram() -> TelegramDelivery:
    global _telegram
    if _telegram is None:
        _telegram = TelegramDelivery()
    return _telegram


def get_health() -> dict:
    """Sistem sağlık durumu."""
    return _safe_call(_get_health_impl, {"ok": False, "error": "service_unavailable"})


def _get_health_impl() -> dict:
    bot_status = database.get_bot_status()
    telegram = _get_telegram()

    db_ok = False
    try:
        with database.get_conn() as conn:
            conn.execute("SELECT 1")
        db_ok = True
    except Exception:
        pass

    heartbeat = bot_status.get("heartbeat", {}).get("value", "")
    status = bot_status.get("status", {}).get("value", "unknown")
    last_error = bot_status.get("last_error", {}).get("value", "")

    # Bot son aktif mi?
    bot_alive = False
    elapsed_sec = None
    if heartbeat:
        try:
            hb_dt = datetime.fromisoformat(heartbeat.replace("Z", "+00:00"))
            elapsed_sec = (datetime.now(timezone.utc) - hb_dt).total_seconds()
            bot_alive = elapsed_sec < 120  # 2 dakika içinde heartbeat varsa alive
        except Exception:
            pass

    # Circuit breaker kontrolü
    cb_active = False
    try:
        cb_val = database.get_state("circuit_breaker_until")
        if cb_val:
            until = datetime.fromisoformat(cb_val)
            if until.tzinfo is None:
                until = until.replace(tzinfo=timezone.utc)
            cb_active = datetime.now(timezone.utc) < until
    except Exception:
        pass

    return {
        "ok": db_ok,
        "db_connected": db_ok,
        "execution_mode": database.get_state("tg_execution_mode") or bot_status.get("tg_execution_mode", {}).get("value") or getattr(config, "EXECUTION_MODE", "paper"),
        "ax_mode": getattr(config, "AX_MODE", "execute"),
        "human_mode": str(database.get_state("tg_human_mode") or bot_status.get("tg_human_mode", {}).get("value")) == "True" if (database.get_state("tg_human_mode") or bot_status.get("tg_human_mode", {}).get("value")) is not None else bool(getattr(config, "HUMAN_MODE", False)),
        "live_trading_enabled": getattr(config, "LIVE_TRADING_ENABLED", False),
        "dry_run": getattr(config, "DRY_RUN", False),
        "telegram_configured": telegram.is_configured(),
        "bot_status": status,
        "bot_alive": bot_alive,
        "last_seen_seconds": int(elapsed_sec) if elapsed_sec is not None else None,
        "last_heartbeat": heartbeat,
        "last_error": last_error,
        "circuit_breaker_active": cb_active,
        "trade_threshold": getattr(config, "TRADE_THRESHOLD", 55.0),
        "telegram_threshold": getattr(config, "TELEGRAM_THRESHOLD", 35.0),
        "server_time": datetime.now(timezone.utc).isoformat(),
    }


def get_live_trades(environment: str | None = None) -> list:
    """Açık trade'lerin detaylı listesi."""
    return _safe_call(_get_live_trades_impl, [], environment)


def _get_live_trades_impl(environment: str | None = None) -> list:
    trades = database.get_open_trades(environment)
    result = []
    for t in trades:
        # Metadata'dan exit state parse et
        exit_state = {}
        try:
            meta_raw = t.get("metadata", "")
            if meta_raw and meta_raw.strip().startswith("{"):
                import json
                exit_state = json.loads(meta_raw)
        except Exception:
            pass

        result.append({
            "id":            t.get("id"),
            "symbol":        t.get("symbol", "?"),
            "side":          t.get("direction") or t.get("side", "?"),
            "entry_price":   float(t.get("entry") or t.get("entry_price") or 0),
            "current_price": float(t.get("current_price") or 0),
            "stop_loss":     float(t.get("sl") or t.get("stop_loss") or 0),
            "tp1":           float(t.get("tp1") or 0),
            "tp2":           float(t.get("tp2") or 0),
            "tp3":           float(t.get("tp3") or 0),
            "leverage":      int(t.get("leverage") or 1),
            "qty":           float(t.get("qty") or t.get("quantity") or 0),
            "notional":      float(t.get("notional_size") or t.get("notional") or 0),
            "margin_used":   float(t.get("margin_used") or 0),
            "risk_usd":      float(t.get("risk_usd") or 0),
            "unrealized_pnl": float(t.get("unrealized_pnl") or 0),
            "realized_pnl":  float(t.get("realized_pnl") or 0),
            "accumulated_pnl": float(t.get("realized_pnl") or t.get("accumulated_pnl") or 0),
            "remaining_qty_pct": float(
                round((float(t.get("remaining_qty") or 0) / float(t.get("qty") or 1)) * 100, 1)
                if t.get("remaining_qty") is not None and float(t.get("qty") or 0) > 0
                else 100.0
            ),
            "total_pnl":     round(
                float(t.get("unrealized_pnl") or 0) +
                float(t.get("realized_pnl") or 0), 4
            ),
            "status":        t.get("status", "open"),
            "opened_at":     t.get("open_time") or t.get("opened_at", ""),
            "setup_quality": t.get("setup_quality", "-"),
            # Exit state — tp1/tp2 birincil kaynak: trades kolonları
            # trailing/breakeven yalnızca metadata'da var, oradan okunur
            "tp1_hit":          bool(t.get("tp1_hit") or exit_state.get("tp1_hit", False)),
            "tp2_hit":          bool(t.get("tp2_hit") or exit_state.get("tp2_hit", False)),
            "trailing_active":  exit_state.get("trailing_active", False),
            "breakeven_set":    exit_state.get("breakeven_set", False),
            "trailing_sl":      exit_state.get("current_sl", 0),
        })
    return result


def get_stats(environment: str | None = None) -> dict:
    """Özet istatistikler (genişletilmiş)."""
    stats = _safe_call(database.get_dashboard_stats, {}, environment)
    # NEDEN (Faz 3.1): Expectancy dashboard ana kartının veri kaynağı —
    # get_stats tek çağrıda kuzey yıldızı metriğini de döndürür.
    try:
        from core.accounting import calculate_expectancy
        stats["expectancy"] = calculate_expectancy(days=30, environment=environment)
    except Exception:
        stats["expectancy"] = {"expectancy_r": 0.0, "n": 0}
    return stats


def get_expectancy(days: int = 30, environment: str | None = None) -> dict:
    """Expectancy detay metriği — /api/expectancy endpoint'i için (Faz 3.1)."""
    from core.accounting import calculate_expectancy
    return _safe_call(calculate_expectancy, {"expectancy_r": 0.0, "n": 0}, days, environment)


def get_trades(limit: int = 100, environment: str | None = None) -> list[dict]:
    """Son trade listesi."""
    return database.get_recent_trades(limit, environment)


def get_signals(limit: int = 100) -> list[dict]:
    """Son sinyal adayları listesi."""
    return database.get_recent_signals(limit)


def get_ax_status() -> dict:
    """Bot durumu, mod, heartbeat, circuit breaker."""
    try:
        bot_status = database.get_bot_status()
        heartbeat  = bot_status.get("heartbeat", {}).get("value", "")
        status     = bot_status.get("status", {}).get("value", "unknown")
        bot_alive  = False
        last_seen  = None
        if heartbeat:
            try:
                hb = datetime.fromisoformat(heartbeat.replace("Z", "+00:00"))
                if hb.tzinfo is None:
                    hb = hb.replace(tzinfo=timezone.utc)
                delta = (datetime.now(timezone.utc) - hb).total_seconds()
                last_seen = int(delta)
                bot_alive = delta < 120
            except Exception:
                pass
        cb_until  = database.get_state("circuit_breaker_until") or ""
        cb_active = False
        if cb_until:
            try:
                cb = datetime.fromisoformat(cb_until.replace("Z", "+00:00"))
                if cb.tzinfo is None:
                    cb = cb.replace(tzinfo=timezone.utc)
                cb_active = cb > datetime.now(timezone.utc)
            except Exception:
                pass
        balance = 0.0
        try:
            balance = database.get_paper_balance() or 0.0
        except Exception:
            pass
        return {
            "bot_running": bot_alive, "bot_status": status,
            "heartbeat": heartbeat, "last_seen_seconds": last_seen,
            "execution_mode": database.get_state("tg_execution_mode") or bot_status.get("tg_execution_mode", {}).get("value") or getattr(config, "EXECUTION_MODE", "paper"),
            "ax_mode": getattr(config, "AX_MODE", "execute"),
            "human_mode": str(database.get_state("tg_human_mode") or bot_status.get("tg_human_mode", {}).get("value")) == "True" if (database.get_state("tg_human_mode") or bot_status.get("tg_human_mode", {}).get("value")) is not None else bool(getattr(config, "HUMAN_MODE", False)),
            "paper_mode": getattr(config, "PAPER_MODE", True),
            "circuit_breaker_active": cb_active, "circuit_breaker_until": cb_until,
            "paper_balance": round(balance, 2),
            "initial_balance": getattr(config, "INITIAL_PAPER_BALANCE", 2000.0),
        }
    except Exception as e:
        logger.error("get_ax_status hata: %s", e)
        return {"bot_running": False, "bot_status": "error", "paper_balance": 0.0, "initial_balance": 2000.0}


def get_calendar_data(days: int = 30) -> list:
    """Günlük PnL takvimi."""
    try:
        with database.get_conn() as conn:
            rows = conn.execute("""
                SELECT date(close_time) AS day,
                       COALESCE(SUM(net_pnl),0) AS pnl,
                       COUNT(*) AS trades,
                       SUM(CASE WHEN net_pnl>0 THEN 1 ELSE 0 END) AS wins
                FROM trades
                WHERE close_time IS NOT NULL AND close_time >= date('now',?)
                GROUP BY day ORDER BY day ASC
            """, (f"-{days} days",)).fetchall()
        return [{"day": r["day"], "pnl": round(float(r["pnl"]), 4),
                 "trades": r["trades"], "wins": r["wins"]} for r in rows]
    except Exception as e:
        logger.error("get_calendar_data hata: %s", e)
        return []


def get_weekly_data(weeks: int = 8) -> list:
    """Haftalık PnL özeti."""
    try:
        with database.get_conn() as conn:
            rows = conn.execute("""
                SELECT strftime('%Y-W%W', close_time) AS week,
                       COALESCE(SUM(net_pnl),0) AS pnl,
                       COUNT(*) AS trades,
                       SUM(CASE WHEN net_pnl>0 THEN 1 ELSE 0 END) AS wins
                FROM trades
                WHERE close_time IS NOT NULL AND close_time >= date('now',?)
                GROUP BY week ORDER BY week ASC
            """, (f"-{weeks*7} days",)).fetchall()
        return [{"week": r["week"], "pnl": round(float(r["pnl"]), 4),
                 "trades": r["trades"], "wins": r["wins"],
                 "winrate": round(r["wins"] / r["trades"] * 100, 1) if r["trades"] else 0.0}
                for r in rows]
    except Exception as e:
        logger.error("get_weekly_data hata: %s", e)
        return []


def get_learning_metrics(days: int = 14) -> dict:
    """Ghost + paper learning metrikleri."""
    try:
        with database.get_conn() as conn:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

            def q(sql, *args):
                return conn.execute(sql, args).fetchone()[0] or 0

            ghost_tp  = q("SELECT COUNT(*) FROM signal_candidates WHERE status='TP_HIT' AND created_at>=?", cutoff)
            ghost_sl  = q("SELECT COUNT(*) FROM signal_candidates WHERE status='SL_HIT' AND created_at>=?", cutoff)
            ghost_pnl = q("SELECT COALESCE(SUM(ghost_pnl),0) FROM signal_candidates WHERE status IN ('TP_HIT','SL_HIT') AND created_at>=?", cutoff)
            p_done    = q("SELECT COUNT(*) FROM paper_results WHERE status IN ('finalized', 'completed') AND created_at>=?", cutoff)
            p_wins    = q("SELECT COUNT(*) FROM paper_results WHERE would_have_won=1 AND status IN ('finalized', 'completed') AND created_at>=?", cutoff)

        res = ghost_tp + ghost_sl
        return {
            "days": days,
            "ghost_tp_hits": ghost_tp, "ghost_sl_hits": ghost_sl,
            "ghost_winrate": round(ghost_tp / res * 100, 1) if res else 0.0,
            "ghost_pnl": round(float(ghost_pnl), 4),
            "paper_completed": p_done, "paper_wins": p_wins,
            "paper_winrate": round(p_wins / p_done * 100, 1) if p_done else 0.0,
        }
    except Exception as e:
        logger.error("get_learning_metrics hata: %s", e)
        return {"days": days, "ghost_winrate": 0.0, "paper_winrate": 0.0}


# ═══════════════════════════════════════════════════════════════════════════════
# KOMUTA MERKEZİ (Faz 4) — tek çağrıda tüm katman verisi
# ═══════════════════════════════════════════════════════════════════════════════

def _hb_age_seconds(key: str = "heartbeat") -> float | None:
    """bot_status'taki bir zaman damgasının yaşını saniye cinsinden döner."""
    try:
        st = database.get_bot_status(key) or {}
        val = st.get("value") or ""
        if not val:
            return None
        dt = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds())
    except Exception:
        return None


def get_system_pulse() -> dict:
    """Katman 1 sol: birleşik sistem nabzı (heartbeat + WS + Redis + Telegram + DB).

    NEDEN: Dashboard ile engine AYRI süreçler — nabız yalnız paylaşılan duruma
    (SQLite/Redis) bakar. Tek dev gösterge: 🟢 ÇALIŞIYOR / 🟡 DEGRADE / 🔴 SORUN.
    """
    comp = {}

    # DB
    db_ok = False
    try:
        with database.get_conn() as conn:
            conn.execute("SELECT 1")
        db_ok = True
    except Exception:
        pass
    comp["db"] = "ok" if db_ok else "down"

    # Engine heartbeat
    hb = _hb_age_seconds("heartbeat")
    comp["engine"] = "ok" if (hb is not None and hb < 120) else ("warn" if (hb is not None and hb < 300) else "down")

    # WebSocket akışı (engine ws_heartbeat yazıyorsa) — yoksa engine'e devret
    ws = _hb_age_seconds("ws_heartbeat")
    if ws is None:
        comp["websocket"] = comp["engine"]  # ayrı sinyal yoksa engine durumu
    else:
        comp["websocket"] = "ok" if ws < 120 else ("warn" if ws < 300 else "down")

    # Redis (dashboard'ın kendi bağlantısı)
    try:
        from core import redis_state
        comp["redis"] = "ok" if redis_state.available() else "warn"
    except Exception:
        comp["redis"] = "warn"

    # Telegram
    try:
        comp["telegram"] = "ok" if _get_telegram().is_configured() else "warn"
    except Exception:
        comp["telegram"] = "warn"

    # Birleşik karar: kritik (db/engine) down → SORUN; ikincil warn/down → DEGRADE
    critical_down = comp["db"] == "down" or comp["engine"] == "down"
    any_warn = any(v in ("warn", "down") for v in comp.values())
    if critical_down:
        status, label = "down", "SORUN"
    elif any_warn:
        status, label = "degrade", "DEGRADE"
    else:
        status, label = "ok", "ÇALIŞIYOR"

    score = sum(1 for v in comp.values() if v == "ok")
    return {"status": status, "label": label, "score": score, "max": len(comp), "components": comp}


def get_funnel_with_rejects(hours: int = 24) -> dict:
    """Katman 2: huni sayıları + son `hours` saatin en sık 3 reddi (signal_events)."""
    funnel = {"scanned": 0, "candidate": 0, "watchlist": 0, "telegram": 0, "trade": 0, "rejects": []}
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        with database.get_conn() as conn:
            def status_count(key):
                try:
                    return int(conn.execute("SELECT value FROM bot_status WHERE key=?", (key,)).fetchone()[0])
                except Exception:
                    return 0

            funnel["scanned"] = status_count("pipeline_scanned")
            funnel["candidate"] = status_count("pipeline_candidate")
            funnel["watchlist"] = conn.execute(
                "SELECT COUNT(*) FROM signal_candidates WHERE status NOT IN ('NEW','rejected')"
            ).fetchone()[0] or 0
            funnel["telegram"] = conn.execute(
                "SELECT COUNT(*) FROM telegram_messages WHERE status IN ('queued','sent') AND created_at>=?",
                (cutoff,),
            ).fetchone()[0] or 0
            funnel["trade"] = conn.execute(
                "SELECT COUNT(*) FROM trades WHERE open_time>=?", (cutoff,)
            ).fetchone()[0] or 0

            # En sık 3 reddetme sebebi (son `hours` saat)
            rows = conn.execute(
                """
                SELECT reject_reason, COUNT(*) AS c FROM signal_events
                WHERE created_at >= ? AND reject_reason IS NOT NULL AND reject_reason != ''
                GROUP BY reject_reason ORDER BY c DESC LIMIT 3
                """,
                (cutoff,),
            ).fetchall()
            funnel["rejects"] = [{"reason": r[0], "count": r[1]} for r in rows]
    except Exception as e:
        logger.error("[CommandCenter] funnel hata: %s", e)
    return funnel


def get_friday_panel() -> dict:
    """Katman 3: Friday paneli — son 5 karar + 'şu an ne düşünüyor' + rejim."""
    panel = {"decisions": [], "thinking": "", "regime": "NEUTRAL"}
    try:
        from core import friday_decisions
        panel["decisions"] = friday_decisions.get_recent_decisions(5)
        # "Şu an ne düşünüyor": en son kararın gerekçesinin ilk cümlesi
        for d in panel["decisions"]:
            reason = (d.get("reasoning") or "").strip()
            if reason:
                first = reason.replace("\n", " ").split(". ")[0]
                panel["thinking"] = (first[:160] + "…") if len(first) > 160 else first
                break
    except Exception as e:
        logger.debug("[CommandCenter] friday panel hata: %s", e)
    try:
        panel["regime"] = database.get_market_regime() or "NEUTRAL"
    except Exception:
        pass
    return panel


def get_ghost_panel() -> dict:
    """Katman 3: Ghost Learner paneli — sanal WR, aktif override, son öneriler, skip doğruluğu."""
    panel = {"virtual_wr": 0.0, "active_overrides": 0, "recent": [], "skip_correctness": 0.0, "total": 0}
    try:
        from core.ghost_learning import get_ghost_learning_stats
        stats = get_ghost_learning_stats()
        panel["virtual_wr"] = round(float(stats.get("ghost_win_rate", 0)) * 100, 1)
        panel["total"] = stats.get("total", 0)
    except Exception:
        pass
    try:
        with database.get_conn() as conn:
            # Aktif override sayısı: threshold_overrides içeren coin_configs
            rows = conn.execute("SELECT config_json FROM coin_configs").fetchall()
            cnt = 0
            for r in rows:
                try:
                    import json
                    cfg = json.loads(r[0]) if r[0] else {}
                    if cfg.get("threshold_overrides"):
                        cnt += 1
                except Exception:
                    continue
            panel["active_overrides"] = cnt

            # Son 3 uygulanan öneri
            recent = conn.execute(
                "SELECT symbol, trigger_type, suggested_threshold, virtual_wr, created_at "
                "FROM ghost_suggestions WHERE applied=1 ORDER BY id DESC LIMIT 3"
            ).fetchall()
            panel["recent"] = [
                {"symbol": r[0], "trigger": r[1], "threshold": r[2], "wr": r[3], "at": r[4]}
                for r in recent
            ]

            # Ghost'un haklı çıkma oranı (skip_decision_correct ortalaması)
            row = conn.execute(
                "SELECT AVG(CASE WHEN skip_decision_correct=1 THEN 1.0 ELSE 0.0 END) "
                "FROM paper_results WHERE skip_decision_correct IS NOT NULL"
            ).fetchone()
            panel["skip_correctness"] = round(float(row[0] or 0) * 100, 1)
    except Exception as e:
        logger.debug("[CommandCenter] ghost panel hata: %s", e)
    return panel


def get_regime_band(hours: int = 24) -> dict:
    """Katman 3: mevcut rejim + son `hours` saat rejim şeridi (kapanan trade'lerden)."""
    band = {"current": "NEUTRAL", "band": []}
    try:
        band["current"] = database.get_market_regime() or "NEUTRAL"
    except Exception:
        pass
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        with database.get_conn() as conn:
            rows = conn.execute(
                "SELECT market_regime, close_time FROM trades "
                "WHERE close_time >= ? AND market_regime IS NOT NULL AND market_regime != '' "
                "ORDER BY close_time ASC",
                (cutoff,),
            ).fetchall()
        band["band"] = [{"regime": r[0], "at": r[1]} for r in rows]
    except Exception as e:
        logger.debug("[CommandCenter] regime band hata: %s", e)
    return band


def get_trade_setup_replay(trade_id: int) -> dict:
    """Setup Replay (Faz 6.2): 'bu trade neden açıldı' — giriş anı indikatör
    anlık görüntüsü + gerekçe + sonuç.

    NEDEN: signal.metadata giriş anında trade.metadata'ya gömülüyor (execution
    engine), yani anlık görüntü TÜM trade'ler için zaten kalıcı — ayrı yazım
    yoluna (market_snapshots) gerek yok, hot open path'e risk eklenmez.
    """
    import json
    out = {"trade_id": trade_id, "found": False, "indicators": {}, "rationale": "—"}
    try:
        trade = database.get_trade_by_id(trade_id)
        if not trade:
            return out
        out["found"] = True
        out.update({
            "symbol": trade.get("symbol"),
            "direction": trade.get("direction") or trade.get("side"),
            "entry": float(trade.get("entry") or trade.get("entry_price") or 0),
            "sl": float(trade.get("sl") or trade.get("stop_loss") or 0),
            "tp1": float(trade.get("tp1") or 0),
            "score": float(trade.get("final_score") or 0),
            "setup_quality": trade.get("setup_quality") or "-",
            "market_regime": trade.get("market_regime") or "-",
            "status": trade.get("status"),
            "net_pnl": float(trade.get("net_pnl") or 0),
            "close_reason": trade.get("close_reason") or "",
            "opened_at": trade.get("open_time") or "",
        })
        meta = trade.get("metadata")
        if isinstance(meta, str) and meta.strip().startswith("{"):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}
        if isinstance(meta, dict):
            # Giriş anı indikatör anlık görüntüsü — bilinen sayısal alanları süz
            indicator_keys = [
                ("ADX", "adx"), ("ADX 15m", "adx15"), ("RSI 5m", "rsi5"), ("RSI 1m", "rsi1"),
                ("BB Genişlik", "bb_width"), ("OB Oranı", "ob_ratio"), ("Hacim (M)", "volume_m"),
                ("CVD", "cvd_value"), ("OI Δ%", "oi_change_pct"), ("Funding", "funding_rate"),
                ("Momentum", "momentum_3c"),
            ]
            snap = {}
            for label, key in indicator_keys:
                if meta.get(key) is not None:
                    try:
                        snap[label] = round(float(meta[key]), 4)
                    except Exception:
                        snap[label] = meta[key]
            out["indicators"] = snap
            for rk in ("reason", "entry_reason", "setup_reason", "trigger_type", "setup"):
                if meta.get(rk):
                    out["rationale"] = str(meta[rk])[:200]
                    break
        return out
    except Exception as e:
        logger.error("[Dashboard] setup replay hatası: %s", e)
        out["error"] = str(e)
        return out


def get_correlation_matrix(environment: str | None = None) -> dict:
    """Açık pozisyonlar arası canlı Pearson korelasyon matrisi (Faz 6.3).

    NEDEN: portfolio_risk zaten hesaplıyor — burada açık pozisyon sembolleri
    için tam matris döndürülür (dashboard görselleştirmesi). On-demand çağrılır
    (klines fetch eder), 5sn polling'e dahil DEĞİL.
    """
    try:
        trades = get_live_trades(environment)
        symbols = sorted({t.get("symbol") for t in trades if t.get("symbol")})
        if len(symbols) < 2:
            return {"symbols": symbols, "matrix": [], "max_pair": None,
                    "note": "Korelasyon için en az 2 açık pozisyon gerekir."}
        from core.portfolio_risk import calculate_correlation_matrix
        return calculate_correlation_matrix(symbols)
    except Exception as e:
        logger.error("[Dashboard] korelasyon matrisi hatası: %s", e)
        return {"symbols": [], "matrix": [], "max_pair": None, "error": str(e)}


def get_profit_readiness_panel(environment: str | None = None) -> dict:
    """P1-5: go-live kâr-kanıt kapısının (directive §10) dashboard özeti."""
    try:
        from scripts.profit_readiness import collect
        r = collect(environment=environment or "paper")
    except Exception as e:
        return {"ready": False, "summary": f"hata: {e}", "metrics": {}, "gates": []}
    m = r.get("metrics", {}) or {}
    return {
        "ready": bool(r.get("ready")),
        "summary": r.get("summary", ""),
        "metrics": {
            "n_trades": m.get("n_trades", 0),
            "expectancy_r": m.get("expectancy_r", 0.0),
            "profit_factor": m.get("profit_factor", 0.0),
            "max_drawdown_pct": m.get("max_drawdown_pct", 0.0),
            "net_pnl": m.get("net_pnl", 0.0),
            "win_rate": m.get("win_rate", 0.0),
        },
        "gates": [
            {"gate": g.get("gate"), "pass": bool(g.get("pass")), "detail": g.get("detail", "")}
            for g in r.get("gates", [])
        ],
    }


def get_stale_warnings() -> dict:
    """P1-5: bayat veri uyarıları — engine heartbeat tazeliği (directive §6).

    NEDEN: Dashboard yalnız okur; engine süreci durmuşsa veriler bayatlar ve
    operatör 'sistem çalışıyor' sanabilir. Heartbeat >120sn ise yüksek-önem uyarı.
    """
    warnings: list[dict] = []
    try:
        ax = get_ax_status()
        last_seen = ax.get("last_seen_seconds")
        if last_seen is None:
            warnings.append({"kind": "heartbeat", "severity": "high",
                             "msg": "Engine heartbeat yok — motor süreci çalışmıyor olabilir"})
        elif last_seen > 120:
            warnings.append({"kind": "heartbeat", "severity": "high", "age_sec": int(last_seen),
                             "msg": f"Engine heartbeat {int(last_seen)}sn bayat (>120sn)"})
        if ax.get("circuit_breaker_active"):
            warnings.append({"kind": "circuit_breaker", "severity": "med",
                             "msg": "Circuit breaker aktif — yeni trade durduruldu"})
    except Exception as e:
        warnings.append({"kind": "error", "severity": "low", "msg": f"durum alınamadı: {e}"})
    return {"stale": any(w.get("severity") == "high" for w in warnings), "warnings": warnings}


def get_command_center(environment: str | None = None) -> dict:
    """Faz 4: Komuta Merkezi'nin tüm katman verisini TEK çağrıda döndürür.

    NEDEN: Dashboard 'tek sayfa dikey akış' — çok sayıda küçük istek yerine tek
    zengin endpoint hem ağ turunu hem JS karmaşıklığını azaltır. Her alt-bölüm
    kendi try/except'i ile güvenli (biri çökse diğerleri dolar).
    """
    env = environment or get_ax_status().get("execution_mode", "paper")
    out = {"environment": env, "ts": datetime.now(timezone.utc).isoformat()}

    # Katman 1
    out["pulse"] = _safe_call(get_system_pulse, {"status": "down", "label": "SORUN", "components": {}})
    out["expectancy"] = _safe_call(get_expectancy, {"expectancy_r": 0.0, "n": 0}, 30, env)
    # Sparkline: daily_summary'den günlük expectancy serisi
    try:
        summaries = database.get_daily_summaries(30, env)
        out["sparkline"] = [
            {"date": s.get("date"), "e": float(s.get("expectancy_r") or 0), "pnl": float(s.get("net_pnl") or 0)}
            for s in summaries
        ]
    except Exception:
        out["sparkline"] = []

    stats = _safe_call(get_stats, {}, env)
    try:
        bal = database.get_active_balance_details()
        balance = float(bal.get("total", 0))
    except Exception:
        balance = float(stats.get("balance", 0) or 0)
    today_pnl = float(stats.get("today_pnl", 0) or 0)
    base = balance - today_pnl
    out["wallet"] = {
        "balance": round(balance, 2),
        "today_pnl": round(today_pnl, 2),
        "today_pnl_pct": round((today_pnl / base * 100) if base > 0 else 0.0, 2),
        "open_count": len(_safe_call(get_live_trades, [], env)),
    }

    # Katman 2
    out["funnel"] = _safe_call(get_funnel_with_rejects, {}, 24)
    out["open_trades"] = _safe_call(get_live_trades, [], env)

    # Katman 3
    out["friday"] = _safe_call(get_friday_panel, {})
    out["ghost"] = _safe_call(get_ghost_panel, {})
    out["regime"] = _safe_call(get_regime_band, {}, 24)

    # Katman 4
    try:
        from core.live_readiness import check as _readiness_check
        out["readiness"] = _readiness_check()
    except Exception:
        out["readiness"] = {"ready": False, "gates": []}

    # P1-5: go-live kâr-kanıt kapısı + bayat-veri uyarıları
    out["profit_readiness"] = _safe_call(
        get_profit_readiness_panel,
        {"ready": False, "summary": "veri yok", "metrics": {}, "gates": []}, env)
    out["stale"] = _safe_call(get_stale_warnings, {"stale": False, "warnings": []})

    # Coin reputations for Phase G
    out["coin_reputations"] = _safe_call(get_coin_reputations, [])

    return out


def get_coin_reputations() -> list[dict]:
    """Exposes coin reputations and score details for the dashboard."""
    try:
        from database import get_conn
        import json
        with get_conn() as conn:
            rows = conn.execute("SELECT coin, config_json, updated_at FROM coin_configs").fetchall()
            result = []
            for r in rows:
                try:
                    data = json.loads(r["config_json"]) if r["config_json"] else {}
                    reputation = data.get("reputation", "Neutral")
                    win_rate = data.get("win_rate", 0.5)
                    total_trades = data.get("total_trades", 0)
                    coin_score = data.get("coin_score", 50.0)
                    result.append({
                        "coin": r["coin"],
                        "reputation": reputation,
                        "win_rate": win_rate,
                        "total_trades": total_trades,
                        "coin_score": coin_score,
                        "updated_at": r["updated_at"]
                    })
                except Exception:
                    pass
            return result
    except Exception as e:
        logger.error(f"[Dashboard] get_coin_reputations error: {e}")
        return []
