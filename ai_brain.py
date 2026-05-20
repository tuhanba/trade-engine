"""
ai_brain.py — AURVEX AI Brain v3.1
=====================================
Tam öğrenme döngüsü:
  1. Her trade kapandığında MAE/MFE/verimlilik analizi (post_trade_analysis)
  2. Postmortem geçmişinden gerçek parametre öğrenmesi (postmortem_insights)
  3. Piyasa rejimi, seans, saat heatmap, coin soğuma, aşırı trade tespiti
  4. En iyi parametre setini hafızada tutma ve kötü dönemde geri dönme
  5. Coin kişilik analizi — her coinin davranış profilini öğrenme
  6. Günlük EOD özeti

scalp_bot.py entegrasyon:
  from ai_brain import analyze_and_adapt, post_trade_analysis, set_client
  set_client(client, send_message)

  Trade kapanınca:
  threading.Thread(target=post_trade_analysis, args=(trade_id,), daemon=True).start()
"""

import sqlite3
import json
import logging
import threading
from datetime import datetime, timezone, timedelta
from collections import defaultdict

from config import (
    DB_PATH, MIN_RR, MIN_EXPECTED_MFE_R,
    MAX_OPEN_TRADES, DAILY_MAX_LOSS_PCT,
    CIRCUIT_BREAKER_LOSSES,
)
from database import (
    get_conn, save_postmortem, save_ai_log,
    is_coin_in_cooldown, get_stats,
    get_coin_config, save_coin_config,
    get_pending_ghost_suggestions, mark_ghost_suggestion_applied,
)

logger = logging.getLogger(__name__)

PARAM_BOUNDS = {
    "sl_atr_mult":    (0.8,  2.5),
    "tp_atr_mult":    (1.2,  4.5),
    "rsi5_min":       (22,   48),
    "rsi5_max":       (58,   82),
    "rsi1_min":       (22,   48),
    "rsi1_max":       (58,   82),
    "vol_ratio_min":  (0.8,  2.8),
    "min_volume_m":   (2.0,  25.0),
    "min_change_pct": (0.3,  5.0),
    "risk_pct":       (0.5,  3.0),
}


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


# ═══════════════════════════════════════════════════════════
# DEPRECATED — evaluate_signal()
# Yetkili karar motoru: core/ai_decision_engine.py::AIDecisionEngine.evaluate()
# scalp_bot.py bu fonksiyonu çağırmıyor; geriye dönük uyumluluk için bırakıldı.
# ═══════════════════════════════════════════════════════════

def evaluate_signal(signal: dict, open_trades: list, balance: float,
                    consecutive_losses: int = 0,
                    circuit_breaker_active: bool = False,
                    session: str = "UNKNOWN",
                    market_regime: str = "NEUTRAL") -> dict:
    """
    DEPRECATED — use AIDecisionEngine.evaluate() from core/ai_decision_engine.py.
    scalp_bot.py uses ai_engine.evaluate(sig) exclusively.
    This wrapper exists only for legacy callers.
    """
    logger.warning(
        "[ai_brain] evaluate_signal() is deprecated. "
        "Use AIDecisionEngine.evaluate() instead."
    )
    try:
        from core.ai_decision_engine import AIDecisionEngine
        from core.data_layer import SignalData
        sig_obj = SignalData(
            symbol=signal.get("symbol", ""),
            side=signal.get("direction", "LONG"),
            entry_price=float(signal.get("entry") or signal.get("entry_zone") or 0),
            stop_loss=float(signal.get("sl") or signal.get("stop_loss") or 0),
            score=float(signal.get("score", 50)),
            tp1=float(signal.get("tp1") or 0),
        )
        engine = AIDecisionEngine()
        result = engine.evaluate(sig_obj)
        return {
            "decision":      result.get("decision", "VETO"),
            "score":         result.get("final_score", signal.get("score", 50)),
            "confidence":    result.get("confidence", 0.5),
            "veto_reason":   None if result.get("decision") == "ALLOW" else result.get("reason"),
            "expected_mfe_r": signal.get("expected_mfe_r", 0),
            "expected_mae_r": 0,
        }
    except Exception as e:
        logger.error(f"[ai_brain] evaluate_signal wrapper hatası: {e}")
        return {
            "decision": "VETO", "score": 0, "confidence": 0,
            "veto_reason": f"wrapper_error: {e}",
            "expected_mfe_r": 0, "expected_mae_r": 0,
        }


# ═══════════════════════════════════════════════════════════
# VERİTABANI BAĞLANTISI VE TABLO KURULUMU
# ═══════════════════════════════════════════════════════════

def db():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-10000")
    conn.execute("""CREATE TABLE IF NOT EXISTS coin_cooldown (
        symbol TEXT PRIMARY KEY,
        until TEXT,
        reason TEXT,
        consec_losses INTEGER DEFAULT 0)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS daily_summary (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT UNIQUE,
        trade_count INTEGER DEFAULT 0, win_count INTEGER DEFAULT 0,
        loss_count INTEGER DEFAULT 0, win_rate REAL DEFAULT 0,
        net_pnl REAL DEFAULT 0,
        best_coin TEXT, worst_coin TEXT,
        sent INTEGER DEFAULT 0)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS trade_postmortem (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_id INTEGER UNIQUE,
        symbol TEXT, direction TEXT,
        entry REAL, exit_price REAL, sl REAL, tp REAL,
        mfe REAL, mae REAL,
        efficiency REAL, sl_tightness REAL,
        opt_tp REAL, missed_gain REAL, actual_pnl REAL,
        created_at TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS best_params (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        params_json TEXT,
        win_rate REAL, profit_factor REAL, total_pnl REAL,
        saved_at TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS coin_profile (
        symbol TEXT PRIMARY KEY,
        trade_count INTEGER DEFAULT 0,
        win_rate REAL DEFAULT 0,
        avg_rr REAL DEFAULT 0,
        avg_efficiency REAL DEFAULT 0,
        avg_hold_min REAL DEFAULT 0,
        best_rsi_min REAL DEFAULT 30,
        best_rsi_max REAL DEFAULT 70,
        best_rv_min REAL DEFAULT 1.2,
        danger_score REAL DEFAULT 0,
        sl_tight_rate REAL DEFAULT 0,
        long_wr REAL DEFAULT 0,
        short_wr REAL DEFAULT 0,
        preferred_direction TEXT DEFAULT 'BOTH',
        last_updated TEXT)""")
    conn.commit()
    return conn


# ═══════════════════════════════════════════════════════════
# VERİ OKUMA
# ═══════════════════════════════════════════════════════════

def get_current_params(conn):
    r = conn.execute("SELECT * FROM params ORDER BY id DESC LIMIT 1").fetchone()
    return dict(r) if r else {}

def get_trades(conn, hours=48, limit=300, symbol=None):
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    if symbol:
        rows = conn.execute(
            "SELECT * FROM trades WHERE status!='OPEN' AND symbol=? "
            "AND close_time>=? ORDER BY close_time DESC LIMIT ?",
            (symbol, cutoff, limit)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM trades WHERE status!='OPEN' AND close_time>=? "
            "ORDER BY close_time DESC LIMIT ?",
            (cutoff, limit)).fetchall()
    return [dict(r) for r in rows]

def get_all_trades(conn, limit=800):
    rows = conn.execute(
        "SELECT * FROM trades WHERE status!='OPEN' ORDER BY close_time DESC LIMIT ?",
        (limit,)).fetchall()
    return [dict(r) for r in rows]

def get_last_n_trades(conn, n=20):
    rows = conn.execute(
        "SELECT * FROM trades WHERE status!='OPEN' ORDER BY close_time DESC LIMIT ?",
        (n,)).fetchall()
    return [dict(r) for r in rows]

def get_today_trades(conn):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows  = conn.execute(
        "SELECT * FROM trades WHERE status!='OPEN' AND close_time LIKE ? "
        "ORDER BY close_time DESC", (f"{today}%",)).fetchall()
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════
# İSTATİSTİK MOTORU
# ═══════════════════════════════════════════════════════════

def calc_stats(trades):
    if not trades:
        return None
    total   = len(trades)
    wins    = [t for t in trades if (t.get("net_pnl") or 0) > 0]
    losses  = [t for t in trades if (t.get("net_pnl") or 0) <= 0]
    wr      = len(wins) / total
    tpnl    = sum(t.get("net_pnl", 0) for t in trades)
    gwin    = sum(t.get("net_pnl", 0) for t in wins)
    gloss   = abs(sum(t.get("net_pnl", 0) for t in losses))
    pf      = gwin / gloss if gloss > 0 else 0
    rrs     = [t.get("r_multiple", 0) for t in trades if t.get("r_multiple")]
    avg_rr  = sum(rrs) / len(rrs) if rrs else 0
    durs    = [t.get("duration_min", 0) for t in trades if t.get("duration_min")]
    avg_dur = sum(durs) / len(durs) if durs else 0
    avg_win  = gwin / len(wins)     if wins   else 0
    avg_loss = -gloss / len(losses) if losses else 0

    sorted_t = sorted(trades, key=lambda x: x.get("close_time", ""))
    running, peak, max_dd = 0, 0, 0
    for t in sorted_t:
        running += t.get("net_pnl", 0)
        if running > peak:
            peak = running
        dd = peak - running
        if dd > max_dd:
            max_dd = dd

    max_cl, cur_cl = 0, 0
    for t in sorted_t:
        if (t.get("net_pnl", 0) or 0) <= 0:
            cur_cl += 1
            max_cl  = max(max_cl, cur_cl)
        else:
            cur_cl = 0

    sl_hits  = len([t for t in trades if (t.get("status", "") or "").upper() in ("LOSS", "SL")])
    longs    = [t for t in trades if t.get("direction", "").upper() == "LONG"]
    shorts   = [t for t in trades if t.get("direction", "").upper() == "SHORT"]
    long_wr  = len([t for t in longs  if t.get("net_pnl", 0) > 0]) / len(longs)  if longs  else 0
    short_wr = len([t for t in shorts if t.get("net_pnl", 0) > 0]) / len(shorts) if shorts else 0

    return {
        "total": total, "wins": len(wins), "losses": len(losses),
        "win_rate": wr, "total_pnl": tpnl,
        "avg_win": avg_win, "avg_loss": avg_loss,
        "profit_factor": pf, "avg_rr": avg_rr,
        "avg_dur": avg_dur, "max_drawdown": max_dd,
        "max_consec_loss": max_cl,
        "sl_hit_ratio": sl_hits / total if total > 0 else 0,
        "long_wr": long_wr, "short_wr": short_wr,
        "long_count": len(longs), "short_count": len(shorts),
    }

def calc_symbol_stats(trades):
    by_sym = defaultdict(list)
    for t in trades:
        by_sym[t.get("symbol", "?")].append(t)
    result = {}
    for sym, st in by_sym.items():
        s = calc_stats(st)
        if not s:
            continue
        s["symbol"]      = sym
        s["trade_count"] = len(st)
        win_rsi = [t.get("rsi5", 50) for t in st
                   if (t.get("net_pnl", 0) or 0) > 0 and t.get("rsi5")]
        s["best_rsi"] = sum(win_rsi) / len(win_rsi) if win_rsi else 50
        recent_sym = sorted(st, key=lambda x: x.get("close_time", ""), reverse=True)[:5]
        cl = 0
        for t in recent_sym:
            if (t.get("net_pnl", 0) or 0) <= 0:
                cl += 1
            else:
                break
        s["recent_consec_loss"] = cl
        result[sym] = s
    return result


# ═══════════════════════════════════════════════════════════
# KOİN KİŞİLİK ANALİZİ
# ═══════════════════════════════════════════════════════════

def update_coin_profiles(conn, all_trades):
    """
    Her coin için geçmiş trade verilerinden davranış profili çıkarır.
    Hangi RSI aralığında kazandığını, hangi hacim oranında güvenilir
    olduğunu, SL'e ne kadar yatkın olduğunu, long mu short mu tercih
    ettiğini hesaplar ve coin_profile tablosuna kaydeder.
    """
    by_sym = defaultdict(list)
    for t in all_trades:
        by_sym[t.get("symbol", "?")].append(t)

    for sym, trades in by_sym.items():
        if len(trades) < 3:
            continue

        wins   = [t for t in trades if (t.get("net_pnl") or 0) > 0]
        losses = [t for t in trades if (t.get("net_pnl") or 0) <= 0]
        wr     = len(wins) / len(trades)

        # RSI aralığı analizi: kazanılan trade'lerdeki RSI değerleri
        win_rsi5 = [t.get("rsi5", 50) for t in wins if t.get("rsi5")]
        if win_rsi5:
            best_rsi_min = max(22, min(win_rsi5) - 3)
            best_rsi_max = min(82, max(win_rsi5) + 3)
        else:
            best_rsi_min, best_rsi_max = 30, 70

        # Hacim oranı analizi: kazanılan trade'lerdeki vol_ratio
        win_rv = [t.get("vol_ratio", 1.5) for t in wins if t.get("vol_ratio")]
        best_rv_min = max(0.8, (sum(win_rv) / len(win_rv)) * 0.8) if win_rv else 1.2

        # SL sıkılığı: kaybedilen trade'lerde SL ne kadar yakındı
        sl_hits = len([t for t in losses if (t.get("status", "") or "").upper() in ("LOSS", "SL")])
        sl_tight_rate = sl_hits / len(losses) if losses else 0

        # Yön tercihi
        longs  = [t for t in trades if t.get("direction", "").upper() == "LONG"]
        shorts = [t for t in trades if t.get("direction", "").upper() == "SHORT"]
        long_wr  = len([t for t in longs  if t.get("net_pnl", 0) > 0]) / len(longs)  if longs  else 0
        short_wr = len([t for t in shorts if t.get("net_pnl", 0) > 0]) / len(shorts) if shorts else 0

        if len(longs) >= 3 and len(shorts) >= 3:
            if long_wr > short_wr + 0.2:
                preferred = "LONG"
            elif short_wr > long_wr + 0.2:
                preferred = "SHORT"
            else:
                preferred = "BOTH"
        else:
            preferred = "BOTH"

        # RR ve süre
        rrs  = [t.get("r_multiple", 0) for t in trades if t.get("r_multiple")]
        durs = [t.get("duration_min", 0) for t in trades if t.get("duration_min")]
        avg_rr  = sum(rrs)  / len(rrs)  if rrs  else 0
        avg_dur = sum(durs) / len(durs) if durs else 0

        # Verimlilik (postmortem tablosundan)
        try:
            pm_rows = conn.execute(
                "SELECT efficiency FROM trade_postmortem WHERE symbol=? ORDER BY created_at DESC LIMIT 20",
                (sym,)).fetchall()
            effs = [r["efficiency"] for r in pm_rows if r["efficiency"] is not None]
            avg_eff = sum(effs) / len(effs) if effs else 50.0
        except Exception:
            avg_eff = 50.0

        # Tehlike skoru: düşük WR + yüksek SL sıkılığı + düşük RR
        danger = 0
        if wr < 0.35:      danger += 3
        elif wr < 0.45:    danger += 1
        if sl_tight_rate > 0.6: danger += 2
        if avg_rr < 0.7:   danger += 2
        if avg_eff < 40:   danger += 1

        conn.execute("""INSERT OR REPLACE INTO coin_profile
            (symbol, trade_count, win_rate, avg_rr, avg_efficiency, avg_hold_min,
             best_rsi_min, best_rsi_max, best_rv_min, danger_score,
             sl_tight_rate, long_wr, short_wr, preferred_direction, last_updated)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            sym, len(trades), wr, avg_rr, avg_eff, avg_dur,
            best_rsi_min, best_rsi_max, best_rv_min, danger,
            sl_tight_rate, long_wr, short_wr, preferred,
            datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        ))

    conn.commit()

def get_coin_profiles(conn, min_trades=3):
    """Veritabanındaki coin profillerini döndürür."""
    rows = conn.execute(
        "SELECT * FROM coin_profile WHERE trade_count>=? ORDER BY danger_score DESC",
        (min_trades,)).fetchall()
    return [dict(r) for r in rows]

def get_dangerous_coins(conn, danger_threshold=4):
    """Tehlike skoru yüksek coinleri döndürür."""
    rows = conn.execute(
        "SELECT symbol, danger_score, win_rate, avg_rr FROM coin_profile "
        "WHERE danger_score>=? AND trade_count>=3 ORDER BY danger_score DESC",
        (danger_threshold,)).fetchall()
    return [dict(r) for r in rows]

def get_best_coins(conn):
    """Win rate ve RR bazında en iyi coinleri döndürür."""
    rows = conn.execute(
        "SELECT symbol, win_rate, avg_rr, preferred_direction FROM coin_profile "
        "WHERE trade_count>=3 AND danger_score<=2 AND win_rate>=0.5 "
        "ORDER BY win_rate DESC, avg_rr DESC LIMIT 5").fetchall()
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════
# SAATLİK HEATMAP — 24 SAAT ÖĞRENME
# ═══════════════════════════════════════════════════════════

def calc_hourly_heatmap(trades):
    """
    24 saatlik win rate haritası.
    Bot artık 7/24 çalıştığından bu analiz kritik önem taşır:
    hangi UTC saatlerinde kazanma olasılığı yüksek, hangilerinde düşük.
    """
    by_hour = defaultdict(list)
    for t in trades:
        ct = t.get("close_time", "")
        if ct and len(ct) >= 13:
            try:
                by_hour[int(ct[11:13])].append(t)
            except Exception:
                pass
    result = {}
    for h, ts in by_hour.items():
        if len(ts) < 2:
            continue
        result[h] = {
            "wr":    len([t for t in ts if t.get("net_pnl", 0) > 0]) / len(ts),
            "pnl":   sum(t.get("net_pnl", 0) for t in ts),
            "count": len(ts),
        }
    return result

def get_bad_hours(heatmap, threshold=0.30):
    """Win rate'i threshold'un altında olan saatleri döndürür."""
    return [h for h, v in heatmap.items() if v["wr"] < threshold and v["count"] >= 3]

def best_worst_hours(heatmap):
    if not heatmap:
        return None, None
    good = [(h, v) for h, v in heatmap.items() if v["count"] >= 3]
    if not good:
        return None, None
    return max(good, key=lambda x: x[1]["wr"]), min(good, key=lambda x: x[1]["wr"])


# ═══════════════════════════════════════════════════════════
# PİYASA REJİMİ
# ═══════════════════════════════════════════════════════════

def get_market_regime(trades):
    if not trades or len(trades) < 8:
        return "UNKNOWN"
    recent   = sorted(trades, key=lambda x: x.get("close_time", ""), reverse=True)[:20]
    longs    = [t for t in recent if t.get("direction", "").upper() == "LONG"]
    shorts   = [t for t in recent if t.get("direction", "").upper() == "SHORT"]
    long_wr  = len([t for t in longs  if t.get("net_pnl", 0) > 0]) / len(longs)  if longs  else 0
    short_wr = len([t for t in shorts if t.get("net_pnl", 0) > 0]) / len(shorts) if shorts else 0
    if long_wr  > 0.58 and short_wr < 0.42: return "BULLISH"
    if short_wr > 0.58 and long_wr  < 0.42: return "BEARISH"
    if long_wr  < 0.32 and short_wr < 0.32: return "CHOPPY"
    return "NEUTRAL"


# ═══════════════════════════════════════════════════════════
# DİNAMİK KOİN SOĞUMA
# ═══════════════════════════════════════════════════════════

def update_coin_cooldowns(conn, sym_stats):
    now = datetime.now(timezone.utc)
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    for sym, s in sym_stats.items():
        if s.get("recent_consec_loss", 0) >= 3:
            until_str = (now + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
            conn.execute("""INSERT OR REPLACE INTO coin_cooldown
                (symbol, until, reason, consec_losses)
                VALUES (?, ?, ?, ?)""",
                (sym, until_str, "3+ ardisik kayip", s["recent_consec_loss"]))
    conn.execute("DELETE FROM coin_cooldown WHERE until < ?", (now_str,))
    conn.commit()
    rows = conn.execute(
        "SELECT symbol, consec_losses FROM coin_cooldown").fetchall()
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════
# DURUM TESPİTLERİ
# ═══════════════════════════════════════════════════════════

def is_drought(conn, hours=3):
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    r = conn.execute(
        "SELECT COUNT(*) as c FROM trades WHERE open_time>=?", (cutoff,)).fetchone()
    return (r["c"] if r else 0) == 0

def is_loss_streak(trades, n=4):
    recent = sorted(trades, key=lambda x: x.get("close_time", ""), reverse=True)[:10]
    if len(recent) < n:
        return False
    c = 0
    for t in recent:
        if (t.get("net_pnl", 0) or 0) <= 0:
            c += 1
        else:
            break
    return c >= n

def is_win_streak(trades, n=3):
    recent = sorted(trades, key=lambda x: x.get("close_time", ""), reverse=True)[:8]
    if len(recent) < n:
        return False
    c = 0
    for t in recent:
        if (t.get("net_pnl", 0) or 0) > 0:
            c += 1
        else:
            break
    return c >= n

def is_overtrading(conn, limit=8):
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    r = conn.execute(
        "SELECT COUNT(*) as c FROM trades WHERE open_time>=?", (cutoff,)).fetchone()
    count = r["c"] if r else 0
    return count >= limit, count


# ═══════════════════════════════════════════════════════════
# POSTMORTEM GERİBİLDİRİM
# ═══════════════════════════════════════════════════════════

def postmortem_insights(conn, limit=200):
    rows = conn.execute(
        "SELECT * FROM trade_postmortem ORDER BY created_at DESC LIMIT ?",
        (limit,)).fetchall()
    if not rows or len(rows) < 5:
        return None

    data = [dict(r) for r in rows]
    efficiencies   = [r["efficiency"]   for r in data if r["efficiency"]   is not None]
    sl_tightnesses = [r["sl_tightness"] for r in data if r["sl_tightness"] is not None]
    missed_gains   = [r["missed_gain"]  for r in data if r["missed_gain"]  is not None]

    avg_eff      = sum(efficiencies)   / len(efficiencies)   if efficiencies   else 50.0
    avg_sl_tight = sum(sl_tightnesses) / len(sl_tightnesses) if sl_tightnesses else 50.0
    avg_missed   = sum(missed_gains)   / len(missed_gains)   if missed_gains   else 0.0

    early_exit_rate = len([e for e in efficiencies if e < 50]) / len(efficiencies) if efficiencies else 0.0
    tight_sl_losses = [r for r in data if (r["sl_tightness"] or 0) > 80 and (r["actual_pnl"] or 0) < 0]
    tight_sl_rate   = len(tight_sl_losses) / len(data)

    tp_mult_adj = +0.25 if avg_eff < 45 else +0.12 if avg_eff < 55 else (-0.05 if avg_eff > 80 else 0.0)
    sl_mult_adj = +0.10 if tight_sl_rate > 0.35 else (-0.08 if avg_sl_tight < 20 and tight_sl_rate < 0.10 else 0.0)

    return {
        "sample_count":     len(data),
        "avg_efficiency":   avg_eff,
        "avg_sl_tightness": avg_sl_tight,
        "avg_missed_gain":  avg_missed,
        "early_exit_rate":  early_exit_rate,
        "tight_sl_rate":    tight_sl_rate,
        "tp_mult_adj":      tp_mult_adj,
        "sl_mult_adj":      sl_mult_adj,
    }


# ════════════════════════════════# ═══════════════════════════
# MARKOV GEÇİŞ MATRİSİ
# ═══════════════════════════

def calc_markov_matrix(trades, symbol=None):
    """
    Markov Geçiş Matrisi: Bir önceki trade sonucunun bir sonraki
    trade sonucunu etkileme olasılığını hesaplar.

    Döndürür:
    {
        'WIN->WIN':  0.65,   # WIN sonrası WIN olasılığı
        'WIN->LOSS': 0.35,
        'LOSS->WIN': 0.42,
        'LOSS->LOSS':0.58,
        'hot_streak': True,  # Son 3 trade WIN mi?
        'cold_streak': False,
        'sample': 45,
    }
    """
    if not trades or len(trades) < 5:
        return None

    # Coin bazında filtrele
    if symbol:
        filtered = [t for t in trades if t.get("symbol") == symbol]
    else:
        filtered = trades

    # Zamana göre sırala (eski → yeni)
    sorted_t = sorted(filtered, key=lambda x: x.get("close_time", ""))

    # Geçiş sayıları
    transitions = {
        ("WIN",  "WIN"):  0,
        ("WIN",  "LOSS"): 0,
        ("LOSS", "WIN"):  0,
        ("LOSS", "LOSS"): 0,
    }

    for i in range(1, len(sorted_t)):
        prev_r = (sorted_t[i-1].get("result") or sorted_t[i-1].get("status") or "").upper()
        curr_r = (sorted_t[i].get("result")   or sorted_t[i].get("status")   or "").upper()
        # Sadece WIN ve LOSS say
        if prev_r not in ("WIN", "LOSS") or curr_r not in ("WIN", "LOSS"):
            continue
        key = (prev_r, curr_r)
        if key in transitions:
            transitions[key] += 1

    total_from_win  = transitions[("WIN",  "WIN")]  + transitions[("WIN",  "LOSS")]
    total_from_loss = transitions[("LOSS", "WIN")]  + transitions[("LOSS", "LOSS")]

    ww = transitions[("WIN",  "WIN")]  / max(total_from_win,  1)
    wl = transitions[("WIN",  "LOSS")] / max(total_from_win,  1)
    lw = transitions[("LOSS", "WIN")]  / max(total_from_loss, 1)
    ll = transitions[("LOSS", "LOSS")] / max(total_from_loss, 1)

    # Son 3 trade
    last3 = sorted_t[-3:]
    last3_results = [(t.get("result") or t.get("status") or "").upper() for t in last3]
    hot_streak  = all(r == "WIN"  for r in last3_results) and len(last3_results) == 3
    cold_streak = all(r == "LOSS" for r in last3_results) and len(last3_results) == 3

    return {
        "WIN->WIN":   round(ww, 3),
        "WIN->LOSS":  round(wl, 3),
        "LOSS->WIN":  round(lw, 3),
        "LOSS->LOSS": round(ll, 3),
        "hot_streak":  hot_streak,
        "cold_streak": cold_streak,
        "sample":      len(sorted_t),
        "transitions": transitions,
    }


def markov_insight(matrix, regime):
    """
    Markov matrisinden parametre önerisi üretir.
    """
    if not matrix or matrix["sample"] < 8:
        return None, []

    changes = []
    adj     = {}

    # Soğuk seri: ardışık kayıptan sonra risk düşür
    if matrix["cold_streak"]:
        adj["risk_pct"] = -0.20
        changes.append(
            f"🧊 Markov: 3 ardışık LOSS (LOSS→LOSS:{matrix['LOSS->LOSS']:.0%}) → Risk düşürüldü")

    # Sıcak seri: ardışık kazancın ardından risk artır
    elif matrix["hot_streak"] and regime in ("BULLISH", "BEARISH", "NEUTRAL"):
        adj["risk_pct"] = +0.15
        changes.append(
            f"🔥 Markov: 3 ardışık WIN (WIN→WIN:{matrix['WIN->WIN']:.0%}) → Risk artırıldı")

    # Yüksek LOSS→LOSS olasılığı: giriş kriterlerini sıkılaştır
    if matrix["LOSS->LOSS"] > 0.65 and matrix["sample"] >= 12:
        adj["vol_ratio_min"] = +0.10
        changes.append(
            f"⚠️ Markov: LOSS→LOSS olasılığı yüksek ({matrix['LOSS->LOSS']:.0%}) → Hacim filtresi sıkılaştırıldı")

    # Yüksek WIN→WIN: TP büyüt
    if matrix["WIN->WIN"] > 0.65 and matrix["sample"] >= 12:
        adj["tp_atr_mult"] = +0.10
        changes.append(
            f"✨ Markov: WIN→WIN olasılığı yüksek ({matrix['WIN->WIN']:.0%}) → TP büyütüldü")

    return adj, changes


# ═══════════════════════════
# EN İYİ PARAMETRE HAFIZASI VE GERİ DÖNÜŞ
# ═════════════════════════════════════════════════════════

def save_best_params_if_better(conn, current_params, stats):
    if not stats or stats["total"] < 10:
        return
    wr = stats["win_rate"]
    pf = stats["profit_factor"]
    if wr < 0.40 or pf < 1.0:
        return
    best = conn.execute(
        "SELECT * FROM best_params ORDER BY profit_factor DESC LIMIT 1").fetchone()
    if best is None or (pf > best["profit_factor"] and wr > best["win_rate"]):
        conn.execute("""INSERT INTO best_params
            (params_json, win_rate, profit_factor, total_pnl, saved_at)
            VALUES (?, ?, ?, ?, ?)""", (
            json.dumps(current_params), wr, pf, stats["total_pnl"],
            datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()

def try_rollback_to_best(conn, last20_stats):
    if not last20_stats or last20_stats["total"] < 8:
        return False, None
    if last20_stats["win_rate"] >= 0.30:
        return False, None
    best = conn.execute(
        "SELECT * FROM best_params ORDER BY profit_factor DESC LIMIT 1").fetchone()
    if not best:
        return False, None
    best_p = json.loads(best["params_json"])
    reason = (f"ROLLBACK: Win rate {last20_stats['win_rate']:.0%} "
              f"en iyi parametrelere geri donuldu (PF:{best['profit_factor']:.2f})")
    save_params(conn, best_p, reason, [reason])
    return True, best_p


# ═══════════════════════════════════════════════════════════
# PARAMETRE OPTİMİZASYON ENJİNİ
# ═══════════════════════════════════════════════════════════

def optimize(current, stats, sym_stats, drought, loss_streak, win_streak,
             regime, overtrading, pm_insights=None):
    p = dict(current)
    changes = []

    if not stats or stats["total"] < 5:
        return p, changes

    wr       = stats["win_rate"]
    pf       = stats["profit_factor"]
    rr       = stats["avg_rr"]
    sl_r     = stats["sl_hit_ratio"]
    mcl      = stats["max_consec_loss"]
    long_wr  = stats.get("long_wr",  0.5)
    short_wr = stats.get("short_wr", 0.5)

    # Postmortem geri bildirimi
    if pm_insights and pm_insights["sample_count"] >= 5:
        tp_adj = pm_insights["tp_mult_adj"]
        sl_adj = pm_insights["sl_mult_adj"]
        if abs(tp_adj) > 0.01:
            p["tp_atr_mult"] = clamp(p["tp_atr_mult"] + tp_adj, *PARAM_BOUNDS["tp_atr_mult"])
            d = "artırıldı" if tp_adj > 0 else "azaltıldı"
            changes.append(
                f"🎯 Postmortem: Ort. verimlilik {pm_insights['avg_efficiency']:.0f}%, "
                f"erken çıkış {pm_insights['early_exit_rate']:.0%} → TP çarpanı {d} ({tp_adj:+.2f})")
        if abs(sl_adj) > 0.01:
            p["sl_atr_mult"] = clamp(p["sl_atr_mult"] + sl_adj, *PARAM_BOUNDS["sl_atr_mult"])
            d = "genişletildi" if sl_adj > 0 else "daraltıldı"
            changes.append(
                f"🛑 Postmortem: Sıkı SL kaybı {pm_insights['tight_sl_rate']:.0%} "
                f"→ SL çarpanı {d} ({sl_adj:+.2f})")

    # Piyasa rejimi
    if regime == "BULLISH":
        p["rsi1_min"] = clamp(p["rsi1_min"] - 2, *PARAM_BOUNDS["rsi1_min"])
        changes.append("📈 Piyasa BULLISH → RSI long-dostu ayarlandı")
    elif regime == "BEARISH":
        p["rsi1_max"] = clamp(p["rsi1_max"] + 2, *PARAM_BOUNDS["rsi1_max"])
        changes.append("📉 Piyasa BEARISH → RSI short-dostu ayarlandı")
    elif regime == "CHOPPY":
        p["vol_ratio_min"]  = clamp(p["vol_ratio_min"]  + 0.10, *PARAM_BOUNDS["vol_ratio_min"])
        p["min_change_pct"] = clamp(p["min_change_pct"] + 0.20, *PARAM_BOUNDS["min_change_pct"])
        changes.append("🌀 Piyasa CHOPPY → Filtreler sıkılaştırıldı")

    if overtrading:
        p["vol_ratio_min"]  = clamp(p["vol_ratio_min"]  + 0.20, *PARAM_BOUNDS["vol_ratio_min"])
        p["min_change_pct"] = clamp(p["min_change_pct"] + 0.30, *PARAM_BOUNDS["min_change_pct"])
        changes.append("⚡ Aşırı trade → Giriş kriterleri sıkılaştırıldı")

    if drought:
        p["vol_ratio_min"]  = clamp(p["vol_ratio_min"]  - 0.15, *PARAM_BOUNDS["vol_ratio_min"])
        p["min_change_pct"] = clamp(p["min_change_pct"] - 0.15, *PARAM_BOUNDS["min_change_pct"])
        p["rsi5_min"]       = clamp(p["rsi5_min"]       - 3,    *PARAM_BOUNDS["rsi5_min"])
        p["rsi5_max"]       = clamp(p["rsi5_max"]       + 3,    *PARAM_BOUNDS["rsi5_max"])
        changes.append("🌵 Sinyal kuralığı → Giriş filtreleri gevşetildi")

    if loss_streak:
        p["sl_atr_mult"] = clamp(p["sl_atr_mult"] - 0.12, *PARAM_BOUNDS["sl_atr_mult"])
        p["risk_pct"]    = clamp(p["risk_pct"]    - 0.25, *PARAM_BOUNDS["risk_pct"])
        p["rsi5_min"]    = clamp(p["rsi5_min"]    + 2,    *PARAM_BOUNDS["rsi5_min"])
        p["rsi5_max"]    = clamp(p["rsi5_max"]    - 2,    *PARAM_BOUNDS["rsi5_max"])
        changes.append(f"🔴 {mcl} kayıp serisi → SL daraltıldı, risk düşürüldü")

    if win_streak and wr > 0.55:
        p["risk_pct"]    = clamp(p["risk_pct"]    + 0.15, *PARAM_BOUNDS["risk_pct"])
        p["tp_atr_mult"] = clamp(p["tp_atr_mult"] + 0.10, *PARAM_BOUNDS["tp_atr_mult"])
        changes.append("🟢 Kazanç serisi → Risk ve TP artırıldı")

    if wr < 0.32 and not loss_streak:
        p["vol_ratio_min"]  = clamp(p["vol_ratio_min"]  + 0.15, *PARAM_BOUNDS["vol_ratio_min"])
        p["min_change_pct"] = clamp(p["min_change_pct"] + 0.20, *PARAM_BOUNDS["min_change_pct"])
        p["sl_atr_mult"]    = clamp(p["sl_atr_mult"]    - 0.08, *PARAM_BOUNDS["sl_atr_mult"])
        changes.append(f"⚠️ Win rate kritik ({wr:.0%}) → Giriş kriterleri sıkılaştırıldı")
    elif wr > 0.60 and pf > 1.8:
        p["risk_pct"]    = clamp(p["risk_pct"]    + 0.10, *PARAM_BOUNDS["risk_pct"])
        p["tp_atr_mult"] = clamp(p["tp_atr_mult"] + 0.15, *PARAM_BOUNDS["tp_atr_mult"])
        changes.append(f"✅ Güçlü performans WR:{wr:.0%} PF:{pf:.2f} → TP ve risk artırıldı")

    if rr < 0.85 and not drought:
        p["tp_atr_mult"] = clamp(p["tp_atr_mult"] + 0.20, *PARAM_BOUNDS["tp_atr_mult"])
        changes.append(f"📐 Avg RR düşük ({rr:.2f}) → TP hedefi büyütüldü")

    if sl_r > 0.70 and stats["total"] >= 8:
        p["sl_atr_mult"] = clamp(p["sl_atr_mult"] + 0.10, *PARAM_BOUNDS["sl_atr_mult"])
        changes.append(f"🛑 SL hit oranı yüksek ({sl_r:.0%}) → SL genişletildi")

    if pf < 0.9 and not loss_streak:
        p["sl_atr_mult"]   = clamp(p["sl_atr_mult"]   - 0.10, *PARAM_BOUNDS["sl_atr_mult"])
        p["vol_ratio_min"] = clamp(p["vol_ratio_min"] + 0.10,  *PARAM_BOUNDS["vol_ratio_min"])
        changes.append(f"📉 PF zayıf ({pf:.2f}) → Filtreler optimize edildi")

    lc = stats.get("long_count", 0)
    sc = stats.get("short_count", 0)
    if abs(long_wr - short_wr) > 0.25 and lc >= 3 and sc >= 3:
        if long_wr > short_wr:
            changes.append(f"⬆️ Long WR ({long_wr:.0%}) > Short WR ({short_wr:.0%}) → Long fırsatlarına odaklan")
        else:
            changes.append(f"⬇️ Short WR ({short_wr:.0%}) > Long WR ({long_wr:.0%}) → Short fırsatlarına odaklan")

    return p, changes


# ═══════════════════════════════════════════════════════════
# KALDIRAÇ ÖNERİSİ
# ═══════════════════════════════════════════════════════════

def suggest_leverage(sym_stats, sym):
    if sym not in sym_stats:
        return 20
    s  = sym_stats[sym]
    wr = s["win_rate"]
    pf = s["profit_factor"]
    if s["trade_count"] < 3:  return 20
    if wr > 0.65 and pf > 2.0: return 25
    if wr > 0.55 and pf > 1.5: return 20
    if wr < 0.30 or pf < 0.70: return 10
    return 15


# ═══════════════════════════════════════════════════════════
# KAYIT FONKSİYONLARI
# ═══════════════════════════════════════════════════════════

def save_params(conn, p, reason, changes):
    conn.execute("""
        INSERT INTO params (
            version, sl_atr_mult, tp_atr_mult,
            rsi5_min, rsi5_max, rsi1_min, rsi1_max,
            vol_ratio_min, min_volume_m, min_change_pct,
            risk_pct, updated_at, ai_reason
        ) VALUES (
            (SELECT COALESCE(MAX(version), 0) + 1 FROM params),
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )""", (
        p.get("sl_atr_mult",   1.2), p.get("tp_atr_mult",   2.0),
        p.get("rsi5_min",       35), p.get("rsi5_max",       75),
        p.get("rsi1_min",       35), p.get("rsi1_max",       72),
        p.get("vol_ratio_min",  1.2), p.get("min_volume_m", 10.0),
        p.get("min_change_pct", 2.0), p.get("risk_pct",     1.5),
        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        (reason or "AI Brain")[:500]
    ))
    conn.commit()

def log_analysis(conn, stats, insight, changes):
    if not stats:
        return
    conn.execute("""INSERT INTO ai_logs
        (created_at, trades_analyzed, win_rate, avg_rr, insight, changes)
        VALUES (?, ?, ?, ?, ?, ?)""", (
        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        stats.get("total", 0), stats.get("win_rate", 0),
        stats.get("avg_rr",   0), insight[:1000],
        json.dumps(changes, ensure_ascii=False)
    ))
    conn.commit()


# ═══════════════════════════════════════════════════════════
# GÜNLÜK EOD ÖZETİ
# ═══════════════════════════════════════════════════════════

def check_eod_summary(conn, today_trades):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if datetime.now(timezone.utc).hour not in (21, 22, 23):
        return None
    already = conn.execute(
        "SELECT sent FROM daily_summary WHERE date=?", (today,)).fetchone()
    if already and already["sent"]:
        return None
    if not today_trades:
        return None
    s = calc_stats(today_trades)
    if not s:
        return None

    by_sym    = defaultdict(list)
    for t in today_trades:
        by_sym[t.get("symbol", "?")].append(t)
    sym_pnl   = {sym: sum(t.get("net_pnl", 0) for t in ts) for sym, ts in by_sym.items()}
    best_sym  = max(sym_pnl, key=sym_pnl.get) if sym_pnl else "—"
    worst_sym = min(sym_pnl, key=sym_pnl.get) if sym_pnl else "—"

    conn.execute("""INSERT OR REPLACE INTO daily_summary
        (date, trade_count, win_count, loss_count, net_pnl, win_rate,
         best_coin, worst_coin, sent)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)""",
        (today, s["total"], s["wins"], s["losses"], s["total_pnl"],
         s["win_rate"], best_sym, worst_sym))
    conn.commit()

    mood = ("🏆 Harika gün!" if s["total_pnl"] > 5  else
            "💪 İyi gün"    if s["total_pnl"] > 0  else
            "😤 Zor gün"    if s["total_pnl"] > -5 else "🔴 Kötü gün")
    we   = "✅" if s["win_rate"] >= 0.50 else "⚠️" if s["win_rate"] >= 0.35 else "❌"

    return "\n".join([
        f"📅 <b>GÜNLÜK ÖZET — {today}</b>",
        f"{mood}", "",
        f"{we} Win Rate: {s['win_rate']:.1%}  ({s['wins']}W / {s['losses']}L)",
        f"💰 Günlük PNL: {s['total_pnl']:+.3f}$",
        f"⚖️ Avg RR: {s['avg_rr']:.2f}  |  PF: {s['profit_factor']:.2f}",
        f"📉 Max DD: {s['max_drawdown']:.3f}$  |  Toplam: {s['total']} trade",
        f"🏆 En iyi coin:  <b>{best_sym}</b> ({sym_pnl.get(best_sym, 0):+.3f}$)",
        f"❌ En kötü coin: <b>{worst_sym}</b> ({sym_pnl.get(worst_sym, 0):+.3f}$)", "",
        "🤖 AI Brain yarın da seninle.",
    ])


# ═══════════════════════════════════════════════════════════
# ANA RAPOR OLUŞTURUCU
# ═══════════════════════════════════════════════════════════

def build_report(stats, sym_stats, changes, drought, loss_streak, win_streak,
                 new_params, current, regime, heatmap, cooldowns,
                 ot_count, pm_insights, rolled_back,
                 dangerous_coins, best_coins, bad_hours,
                 markov_matrix=None):
    lines = ["🧠 <b>AI BEYİN RAPORU v3.2</b>\n"]

    if rolled_back:
        lines.append("🔄 <b>ROLLBACK UYGULANDII</b> — En iyi parametre setine geri dönüldü.\n")

    if stats and stats["total"] > 0:
        wr = stats["win_rate"]
        we = "✅" if wr >= 0.50 else "⚠️" if wr >= 0.35 else "❌"
        lines += [
            f"📊 <b>Son 48 Saat</b> ({stats['total']} trade)",
            f"{we} Win Rate: {wr:.1%}  ({stats['wins']}W / {stats['losses']}L)",
            f"💰 PNL: {stats['total_pnl']:+.3f}$  |  Avg Kazanç: {stats['avg_win']:+.3f}$",
            f"⚖️ Avg RR: {stats['avg_rr']:.2f}  |  PF: {stats['profit_factor']:.2f}",
            f"📉 Max DD: {stats['max_drawdown']:.3f}$  |  SL Hit: {stats['sl_hit_ratio']:.0%}",
            f"⬆️ Long WR: {stats.get('long_wr',0):.0%} ({stats.get('long_count',0)})"
            f"   ⬇️ Short WR: {stats.get('short_wr',0):.0%} ({stats.get('short_count',0)})",
        ]
    else:
        lines.append("📊 Henüz yeterli trade verisi yok.")

    if pm_insights and pm_insights["sample_count"] >= 5:
        lines.append(
            f"\n🔬 <b>Postmortem ({pm_insights['sample_count']} trade):</b> "
            f"Ort. verimlilik {pm_insights['avg_efficiency']:.0f}% | "
            f"Erken çıkış {pm_insights['early_exit_rate']:.0%} | "
            f"Sıkı SL kaybı {pm_insights['tight_sl_rate']:.0%}")

    regime_emoji = {"BULLISH":"📈","BEARISH":"📉","CHOPPY":"🌀","NEUTRAL":"➡️","UNKNOWN":"❓"}
    _re_icon = regime_emoji.get(regime, '❓')
    lines.append(f"\n{_re_icon} <b>Piyasa Rejimi:</b> {regime}")
    regime_effects = {
        "BULLISH": "⬆️ LONG sinyaller favori — SHORT eşiği yükseltildi",
        "BEARISH": "⬇️ SHORT sinyaller favori — LONG eşiği yükseltildi",
        "CHOPPY":  "🚫 Sadece A+ / S kalite sinyaller geçiyor",
        "NEUTRAL": "↔️ Standart filtreler aktif",
        "UNKNOWN": "❓ Yeterli veri yok",
    }
    lines.append(f"   {regime_effects.get(regime, '')}")

    # Markov geçiş matrisi raporu
    if markov_matrix and markov_matrix["sample"] >= 8:
        m = markov_matrix
        streak_tag = ""
        if m["hot_streak"]:
            streak_tag = " 🔥 Sıcak Seri!"
        elif m["cold_streak"]:
            streak_tag = " 🧊 Soğuk Seri!"
        lines.append(
            f"\n🔗 <b>Markov Matrisi</b> ({m['sample']} trade){streak_tag}\n"
            f"  WIN→WIN: {m['WIN->WIN']:.0%}  |  WIN→LOSS: {m['WIN->LOSS']:.0%}\n"
            f"  LOSS→WIN: {m['LOSS->WIN']:.0%}  |  LOSS→LOSS: {m['LOSS->LOSS']:.0%}"
        )

    flags = []
    if drought:       flags.append("🌵 Sinyal kuralığı")
    if loss_streak:   flags.append("🔴 Kayıp serisi")
    if win_streak:    flags.append("🟢 Kazanç serisi")
    if ot_count >= 8: flags.append(f"⚡ Aşırı trade ({ot_count}/saat)")
    if flags:
        lines.append(" | ".join(flags))

    # Saatlik analiz — 24 saat öğrenme
    if heatmap:
        good_hours = sorted(
            [(h, v) for h, v in heatmap.items() if v["count"] >= 3 and v["wr"] >= 0.55],
            key=lambda x: x[1]["wr"], reverse=True)[:3]
        if good_hours:
            lines.append("\n⏰ <b>EN İYİ UTC SAATLERİ:</b>")
            for h, v in good_hours:
                lines.append(f"  ✅ {h:02d}:00 | WR:{v['wr']:.0%} | {v['pnl']:+.2f}$ | {v['count']} trade")
        if bad_hours:
            bad_str = ", ".join(f"{h:02d}:00" for h in bad_hours[:4])
            lines.append(f"  ⚠️ Dikkatli saatler: {bad_str}")

    # Coin soğuma
    if cooldowns:
        lines.append(f"\n🚫 <b>Geçici Kaçın (2 saat):</b> {', '.join(c['symbol'] for c in cooldowns)}")

    # Tehlikeli coinler
    if dangerous_coins:
        lines.append("\n☠️ <b>TEHLİKELİ COİNLER:</b>")
        for c in dangerous_coins[:4]:
            lines.append(
                f"  ⛔ <b>{c['symbol']}</b>: WR:{c['win_rate']:.0%} | "
                f"RR:{c['avg_rr']:.2f} | Tehlike:{c['danger_score']:.0f}/8")

    # En iyi coinler
    if best_coins:
        lines.append("\n🏆 <b>GÜÇLÜ COİNLER:</b>")
        for c in best_coins[:4]:
            dir_str = f" | Tercih: {c['preferred_direction']}" if c['preferred_direction'] != "BOTH" else ""
            lines.append(f"  ✅ <b>{c['symbol']}</b>: WR:{c['win_rate']:.0%} | RR:{c['avg_rr']:.2f}{dir_str}")

    # Genel sembol performansı
    if sym_stats:
        lines.append("\n🪙 <b>KOİN PERFORMANSI (Son 48 Saat):</b>")
        top   = sorted(sym_stats.items(), key=lambda x: x[1]["total_pnl"], reverse=True)
        shown = [s for s in top if s[1]["trade_count"] >= 2][:5]
        for sym, s in shown:
            e   = "✅" if s["total_pnl"] > 0 else "❌"
            lev = suggest_leverage(sym_stats, sym)
            lines.append(
                f"{e} <b>{sym}</b>: {s['total_pnl']:+.3f}$ | "
                f"WR:{s['win_rate']:.0%} | RR:{s['avg_rr']:.2f} | x{lev}")

    # Parametre değişiklikleri
    if changes:
        lines.append("\n🔧 <b>YAPILAN DEĞİŞİKLİKLER:</b>")
        for c in changes:
            lines.append(f"• {c}")
        lines.append(
            f"\n📌 SL:{new_params.get('sl_atr_mult',0):.2f} | "
            f"TP:{new_params.get('tp_atr_mult',0):.2f} | "
            f"Risk:{new_params.get('risk_pct',0):.1f}% | "
            f"Vol:{new_params.get('vol_ratio_min',0):.2f}")
    else:
        lines.append("\n✔️ Parametreler optimal, değişiklik yapılmadı.")

    # Akıllı öneriler
    lines.append("\n💡 <b>AKILLI ÖNERİLER:</b>")
    recs = []
    if stats:
        if stats["avg_rr"] < 0.8:
            recs.append("TP hedefleri çok yakın — artırılmalı")
        if stats["sl_hit_ratio"] > 0.65:
            recs.append("SL çok sık tetikleniyor — giriş kalitesi artırılmalı")
        if stats["win_rate"] > 0.55 and stats["profit_factor"] > 1.5:
            recs.append("Güçlü dönem — risk artırımı değerlendirilebilir")
        if stats["max_consec_loss"] >= 5:
            recs.append("Uzun kayıp serisi — piyasa değişmiş olabilir, bekle")
        if regime == "CHOPPY":
            recs.append("Dalgalı piyasa — daha az ve seçici trade yap")
    if pm_insights:
        if pm_insights["early_exit_rate"] > 0.50:
            recs.append("Trade'lerin yarısından fazlasında erken çıkılıyor — TP büyütülmeli")
        if pm_insights["tight_sl_rate"] > 0.30:
            recs.append("SL'ler gürültüye takılıyor — SL'yi biraz geniş tut")
    if dangerous_coins:
        names = ", ".join(c["symbol"] for c in dangerous_coins[:2])
        recs.append(f"Dikkat: {names} geçmişte sürekli zarar üretiyor")
    if best_coins:
        recs.append(f"{best_coins[0]['symbol']} en güçlü profil — bu coinde daha agresif girilebilir")
    if not recs:
        recs.append("Her şey dengede, strateji devam ediyor.")
    for r in recs:
        lines.append(f"• {r}")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════
# POST-TRADE ANALİZİ — MAE / MFE / OPTİMAL ÇIKIŞ
# ═══════════════════════════════════════════════════════════

_client_ref  = None
_tg_send_ref = None


def set_client(client, tg_send_fn=None):
    global _client_ref, _tg_send_ref
    _client_ref  = client
    _tg_send_ref = tg_send_fn


def post_trade_analysis(trade_id, client_ref=None, tg_fn=None):
    try:
        import pandas as pd

        cli = client_ref or _client_ref
        tg  = tg_fn      or _tg_send_ref
        if not cli:
            return

        conn = db()
        if conn.execute(
                "SELECT id FROM trade_postmortem WHERE trade_id=?",
                (trade_id,)).fetchone():
            conn.close()
            return

        row = conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
        if not row:
            conn.close()
            return

        t          = dict(row)
        sym        = t.get("symbol",      "")
        direction  = (t.get("direction",  "") or "").upper()
        entry      = t.get("entry",        0) or 0
        sl         = t.get("sl",           0) or 0
        tp         = t.get("tp",           0) or 0
        actual_pnl = t.get("net_pnl",     0) or 0
        open_t     = t.get("open_time",   "")
        close_t    = t.get("close_time",  "")
        dur_min    = t.get("duration_min", 0) or 0
        exit_p     = t.get("exit_price",   0) or 0
        status     = (t.get("status",     "") or "").upper()
        leverage   = t.get("leverage",    20) or 20

        if not sym or not entry or not direction:
            conn.close()
            return

        extra  = max(int(dur_min * 1.5 / 5) + 10, 20)
        try:
            klines = cli.futures_klines(symbol=sym, interval="5m", limit=min(extra, 100))
        except Exception:
            klines = cli.get_klines(symbol=sym, interval="5m", limit=min(extra, 100))

        df = pd.DataFrame(klines, columns=[
            "time", "open", "high", "low", "close", "volume",
            "ct", "qav", "nt", "tbbav", "tbqav", "ignore"
        ])
        for col in ["open", "high", "low", "close"]:
            df[col] = df[col].astype(float)

        open_ms  = pd.Timestamp(open_t).timestamp()  * 1000 if open_t  else 0
        close_ms = pd.Timestamp(close_t).timestamp() * 1000 if close_t else df["time"].iloc[-1]

        trade_df = df[(df["time"] >= open_ms) & (df["time"] <= close_ms)]
        after_df = df[df["time"]  > close_ms]

        if trade_df.empty:
            conn.close()
            return

        is_long = direction == "LONG"

        if is_long:
            mfe_price = trade_df["high"].max()
            mae_price = trade_df["low"].min()
            mfe_dist  = max(0, mfe_price - entry)
            mae_dist  = max(0, entry - mae_price)
            opt_tp    = entry + mfe_dist * 0.80
            after_ext = after_df["high"].max() if not after_df.empty else exit_p
            missed    = max(0, after_ext - exit_p) if status in ("WIN", "TP") else max(0, after_ext - entry)
        else:
            mfe_price = trade_df["low"].min()
            mae_price = trade_df["high"].max()
            mfe_dist  = max(0, entry - mfe_price)
            mae_dist  = max(0, mae_price - entry)
            opt_tp    = entry - mfe_dist * 0.80
            after_ext = after_df["low"].min() if not after_df.empty else exit_p
            missed    = max(0, exit_p - after_ext) if status in ("WIN", "TP") else max(0, entry - after_ext)

        sl_dist    = abs(entry - sl) if sl else 0
        efficiency = (abs(exit_p - entry) / mfe_dist * 100) if mfe_dist > 0 else 0
        sl_tight   = (mae_dist / sl_dist * 100)              if sl_dist  > 0 else 0

        conn.execute("""INSERT OR IGNORE INTO trade_postmortem
            (trade_id, symbol, direction, entry, exit_price, sl, tp,
             mfe, mae, efficiency, sl_tightness, opt_tp, missed_gain,
             actual_pnl, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
            trade_id, sym, direction, entry, exit_p, sl, tp,
            mfe_price, mae_price, efficiency, sl_tight,
            opt_tp, missed, actual_pnl,
            datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        ))
        conn.commit()
        conn.close()

        # Yeni database.py standardına da kaydet
        sl_dist_r = abs(entry - sl) if sl else 1e-10
        save_postmortem({
            "trade_id":      trade_id,
            "symbol":        sym,
            "direction":     direction,
            "mfe_r":         round(mfe_dist / sl_dist_r, 3) if sl_dist_r else 0,
            "mae_r":         round(mae_dist / sl_dist_r, 3) if sl_dist_r else 0,
            "efficiency":    round(efficiency, 2),
            "missed_gain":   round(missed, 6),
            "sl_tightness":  round(sl_tight, 2),
            "hold_minutes":  dur_min,
            "exit_quality":  round(efficiency / 100, 3),
            "setup_quality": None,
            "notes":         None,
        })

        if not tg:
            return

        re = "✅" if actual_pnl > 0 else "❌"
        timing = ("⚠️ Çok erken çıkıldı" if efficiency < 40
                  else "💡 Biraz daha tutulabilirdi" if efficiency < 70
                  else "✅ İyi zamanlama")

        lines = [
            f"🔬 <b>TRADE ANALİZİ — {sym}</b>",
            f"{re} {direction} x{leverage} | Sonuç: {actual_pnl:+.3f}$", "",
            f"📍 Giriş: {entry:.5f}   🎯 Çıkış: {exit_p:.5f} ({status})",
            f"🛑 SL: {sl:.5f}   🏁 TP: {tp:.5f}", "",
            f"📈 MFE: {mfe_price:.5f}  (lehimize {mfe_dist:.5f} / {mfe_dist/entry*100:.2f}%)",
            f"📉 MAE: {mae_price:.5f}  (aleyhimize {mae_dist:.5f} / {mae_dist/entry*100:.2f}%)", "",
            f"⚡ Verimlilik: {efficiency:.0f}%  {timing}",
            f"🎯 Optimal TP önerisi: {opt_tp:.5f}  (MFE %80'i)",
        ]

        if sl_tight > 85:
            lines.append(f"⚠️ SL {sl_tight:.0f}% yaklaştı → biraz geniş tutulabilirdi")
        if missed > 0.001:
            suffix = "→ SL çok erkendi" if status in ("LOSS", "SL") else "→ trailing stop düşünülebilir"
            lines.append(f"💰 Kapanış sonrası {missed:.5f} daha hareket etti {suffix}")

        lines.append("\n🧠 Öğrenme notu:")
        if actual_pnl > 0 and efficiency < 50:
            lines.append("• Kazandın ama potansiyelin yarısını aldın. TP büyütülmeli.")
        elif actual_pnl < 0 and sl_tight > 80:
            lines.append("• SL gürültüye takıldı. Daha geniş SL dene.")
        elif actual_pnl < 0 and missed > sl_dist * 0.5:
            lines.append("• SL sonrası doğru yönde devam etti. Daha geniş SL dene.")
        elif actual_pnl > 0 and efficiency > 75:
            lines.append("• Mükemmel trade yönetimi. Bu parametreleri koru.")
        else:
            lines.append("• Analiz tamamlandı, öğrenme havuzuna eklendi.")

        tg("\n".join(lines))

    except Exception:
        pass


# ═══════════════════════════════════════════════════════════
# ANA GİRİŞ NOKTASI
# ═══════════════════════════════════════════════════════════
# NIGHTLY PER-COIN OPTİMİZER
# Her gece 03:00 UTC — coin başına RSI/ATR/confidence parametreleri
# ═══════════════════════════════════════════════════════════

_COIN_PARAM_BOUNDS = {
    "confidence_cutoff": (0.45, 0.90),
    "sl_atr_mult":       (0.8,  2.5),
    "tp_atr_mult":       (1.2,  4.5),
    "leverage":          (3,    10),
}


def _clamp_coin(v, key):
    lo, hi = _COIN_PARAM_BOUNDS[key]
    return max(lo, min(hi, v))


def _get_coin_trade_stats(conn, days=30, min_trades=20):
    """
    Son N gün içindeki CLOSED trade'lerden coin başına istatistik çıkarır.
    min_trades karşılanmayan coinler döndürülmez.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    rows = conn.execute("""
        SELECT
            symbol,
            COUNT(*)                                            AS total,
            SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END)      AS wins,
            AVG(r_multiple)                                     AS avg_r,
            AVG(CASE WHEN mfe IS NOT NULL THEN mfe ELSE 0 END) AS avg_mfe,
            AVG(CASE WHEN mae IS NOT NULL THEN mae ELSE 0 END) AS avg_mae,
            AVG(sl)                                             AS avg_sl,
            AVG(entry)                                          AS avg_entry
        FROM trades
        WHERE status = 'CLOSED'
          AND close_time >= ?
        GROUP BY symbol
        HAVING COUNT(*) >= ?
    """, (cutoff, min_trades)).fetchall()

    result = {}
    for r in rows:
        sym = r[0]
        total = r[1] or 1
        result[sym] = {
            "total":       r[1],
            "wins":        r[2] or 0,
            "win_rate":    (r[2] or 0) / total,
            "avg_r":       r[3] or 0.0,
            "avg_mfe":     r[4] or 0.0,
            "avg_mae":     r[5] or 0.0,
            "avg_sl":      r[6] or 0.0,
            "avg_entry":   r[7] or 0.0,
        }
    return result


def _counterfactual_sl(conn, symbol, sl_mults=(1.5, 2.0, 2.5), days=30, min_trades=5):
    """
    'SL daha geniş olsaydı bu trade WIN olur muydu?' counterfactual analizi.
    mae < virtual_sl_dist ise o çarpanla POTENTIAL_WIN sayılır.
    MAE bilgisi trade tablosundan gelir.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    rows = conn.execute("""
        SELECT entry, sl, mae, net_pnl
        FROM trades
        WHERE symbol = ?
          AND status = 'CLOSED'
          AND close_time >= ?
          AND entry > 0 AND sl > 0 AND mae IS NOT NULL
    """, (symbol, cutoff)).fetchall()

    if len(rows) < min_trades:
        return {}

    results = {}
    for mult in sl_mults:
        rescued = 0
        applicable = 0
        for entry, sl, mae, pnl in rows:
            if pnl >= 0:
                continue  # sadece kayıpları analiz et
            sl_dist = abs(entry - sl)
            virtual_sl_dist = sl_dist * mult
            applicable += 1
            if mae < virtual_sl_dist:
                rescued += 1
        rescue_rate = rescued / applicable if applicable > 0 else 0
        results[mult] = {
            "rescue_rate":  round(rescue_rate, 3),
            "rescued":      rescued,
            "applicable":   applicable,
        }
    return results


def _optimize_coin_params(coin, stats, current_config, cf_results=None):
    """
    Tek bir coin için yeni parametreleri hesaplar.
    Döndürür: (new_config_dict, changes_list)
    """
    p = dict(current_config)
    if "confidence_cutoff" not in p:
        p["confidence_cutoff"] = 0.65
    if "sl_atr_mult" not in p:
        p["sl_atr_mult"] = 1.2
    if "tp_atr_mult" not in p:
        p["tp_atr_mult"] = 2.0
    if "leverage" not in p:
        p["leverage"] = 10

    changes = []
    wr   = stats["win_rate"]
    avgr = stats["avg_r"]

    # ── Confidence cutoff ────────────────────────────────────────────────
    if wr > 0.60 and avgr > 0.8:
        new_conf = _clamp_coin(p["confidence_cutoff"] - 0.02, "confidence_cutoff")
        if new_conf != p["confidence_cutoff"]:
            changes.append(f"conf ↓ {p['confidence_cutoff']:.2f}→{new_conf:.2f} (WR:{wr:.0%} R:{avgr:.2f})")
            p["confidence_cutoff"] = new_conf
    elif wr < 0.40:
        new_conf = _clamp_coin(p["confidence_cutoff"] + 0.05, "confidence_cutoff")
        if new_conf != p["confidence_cutoff"]:
            changes.append(f"conf ↑ {p['confidence_cutoff']:.2f}→{new_conf:.2f} (WR:{wr:.0%})")
            p["confidence_cutoff"] = new_conf

    # ── TP çarpanı — MFE/MAE oranına göre ───────────────────────────────
    avg_mfe = stats["avg_mfe"]
    avg_mae = stats["avg_mae"]
    if avg_mae > 0:
        ratio = avg_mfe / avg_mae
        if ratio > 2.5:
            new_tp = _clamp_coin(p["tp_atr_mult"] * 1.10, "tp_atr_mult")
            if round(new_tp, 3) != round(p["tp_atr_mult"], 3):
                changes.append(f"tp ↑ {p['tp_atr_mult']:.2f}→{new_tp:.2f} (MFE/MAE={ratio:.1f})")
                p["tp_atr_mult"] = round(new_tp, 3)
        elif ratio < 1.2:
            new_tp = _clamp_coin(p["tp_atr_mult"] * 0.95, "tp_atr_mult")
            if round(new_tp, 3) != round(p["tp_atr_mult"], 3):
                changes.append(f"tp ↓ {p['tp_atr_mult']:.2f}→{new_tp:.2f} (MFE/MAE={ratio:.1f})")
                p["tp_atr_mult"] = round(new_tp, 3)

    # ── SL çarpanı — counterfactual ─────────────────────────────────────
    if cf_results:
        best_mult = None
        best_rescue = 0
        for mult, data in cf_results.items():
            if data["rescue_rate"] > best_rescue and data["applicable"] >= 3:
                best_rescue = data["rescue_rate"]
                best_mult = mult
        if best_mult and best_rescue > 0.40:
            # Çok kayıp SL gürültüsünden — biraz genişlet
            new_sl = _clamp_coin(p["sl_atr_mult"] + 0.10, "sl_atr_mult")
            if round(new_sl, 3) != round(p["sl_atr_mult"], 3):
                changes.append(
                    f"sl ↑ {p['sl_atr_mult']:.2f}→{new_sl:.2f} "
                    f"(cf rescue {best_rescue:.0%} @{best_mult}x)"
                )
                p["sl_atr_mult"] = round(new_sl, 3)

    # ── Leverage — yalnızca performans güçlüyse, ASLA 10x üstüne ────────
    if wr > 0.65 and avgr > 1.2:
        new_lev = _clamp_coin(p["leverage"] + 1, "leverage")
        if new_lev != p["leverage"]:
            changes.append(f"lev ↑ {p['leverage']}→{new_lev} (WR:{wr:.0%} R:{avgr:.2f})")
            p["leverage"] = new_lev
    elif wr < 0.35 or avgr < -0.5:
        new_lev = _clamp_coin(p["leverage"] - 1, "leverage")
        if new_lev != p["leverage"]:
            changes.append(f"lev ↓ {p['leverage']}→{new_lev} (WR:{wr:.0%})")
            p["leverage"] = new_lev

    p["win_rate"]  = round(wr, 4)
    p["avg_r"]     = round(avgr, 4)
    p["sample_n"]  = stats["total"]
    p["optimized_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return p, changes


def apply_ghost_suggestions(min_confidence: str = "MEDIUM") -> list:
    """
    ghost_suggestions tablosundaki uygulanmamış önerileri okur,
    ilgili coin'in confidence_cutoff değerini düşürür (daha fazla sinyal geçer),
    öneriyi 'applied' olarak işaretler.

    Returns: uygulanan değişikliklerin string listesi (Telegram raporu için)
    """
    applied_changes = []
    try:
        suggestions = get_pending_ghost_suggestions(min_confidence=min_confidence)
        if not suggestions:
            logger.info("[GhostApply] Uygulanacak öneri yok.")
            return []

        # Aynı coin için birden fazla öneri varsa en yüksek avg_r'yi seç
        best_per_coin = {}
        for s in suggestions:
            sym = s["symbol"]
            if sym not in best_per_coin or s["avg_virtual_r"] > best_per_coin[sym]["avg_virtual_r"]:
                best_per_coin[sym] = s

        for sym, s in best_per_coin.items():
            current_cfg = get_coin_config(sym)
            old_conf = current_cfg.get("confidence_cutoff", s["current_threshold"] or 0.65)
            new_conf = s["suggested_threshold"]

            # Güvenlik: tek seferde max 0.10 düşür
            if old_conf - new_conf > 0.10:
                new_conf = round(old_conf - 0.10, 3)

            if new_conf >= old_conf:
                mark_ghost_suggestion_applied(s["id"])
                continue

            current_cfg["confidence_cutoff"] = new_conf
            current_cfg["ghost_applied_at"]  = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            current_cfg["ghost_virtual_wr"]  = s["virtual_wr"]
            current_cfg["ghost_avg_r"]       = s["avg_virtual_r"]
            current_cfg["ghost_sample"]      = s["sample_count"]

            save_coin_config(sym, current_cfg)
            mark_ghost_suggestion_applied(s["id"])

            msg = (
                f"👻 {sym}: conf {old_conf:.2f}→{new_conf:.2f} "
                f"[{s['trigger_type']}  VWR:{s['virtual_wr']:.0%}  "
                f"R:{s['avg_virtual_r']:.2f}  n={s['sample_count']}  {s['confidence']}]"
            )
            applied_changes.append(msg)
            logger.info("[GhostApply] %s", msg)

        logger.info("[GhostApply] %d coin güncellendi.", len(applied_changes))
        return applied_changes

    except Exception as exc:
        logger.error("[GhostApply] Hata: %s", exc)
        return []


def nightly_optimize_coins(tg_fn=None):
    """
    Per-coin nightly optimizer — 03:00 UTC'de çalışır.
    coin_configs tablosunu günceller, Telegram raporu gönderir.
    """
    try:
        conn = db()
        coin_stats = _get_coin_trade_stats(conn, days=30, min_trades=20)
        if not coin_stats:
            conn.close()
            logger.info("[NightlyOpt] Yeterli trade verisi yok (min 20/coin/30gün).")
            return "Yeterli veri yok."

        updated = []
        conf_raised = 0
        conf_lowered = 0
        paused = []
        top_performers = []
        underperformers = []

        for coin, stats in sorted(coin_stats.items()):
            current_cfg = get_coin_config(coin)
            cf = _counterfactual_sl(conn, coin)
            new_cfg, changes = _optimize_coin_params(coin, stats, current_cfg, cf)
            save_coin_config(coin, new_cfg)
            updated.append(coin)

            wr   = stats["win_rate"]
            avgr = stats["avg_r"]

            if wr >= 0.58 and avgr > 0.5:
                top_performers.append((coin, wr, avgr))
            elif wr < 0.35 or (wr < 0.45 and avgr < -0.3):
                underperformers.append((coin, wr, avgr))
                paused.append(coin)

            for c in changes:
                if "conf ↑" in c:
                    conf_raised += 1
                elif "conf ↓" in c:
                    conf_lowered += 1

        conn.close()

        top_performers.sort(key=lambda x: x[1], reverse=True)
        underperformers.sort(key=lambda x: x[1])

        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines = [
            "🧠 <b>AI Brain Nightly Report</b>",
            "━━━━━━━━━━━━━━━━━━━━━━",
            f"📅 {now_str}",
            f"📊 Analyzed: {sum(s['total'] for s in coin_stats.values())} trades / {len(coin_stats)} coins",
            "",
        ]

        if top_performers:
            lines.append("🏆 <b>Top Performers</b>")
            for sym, wr, avgr in top_performers[:5]:
                lines.append(f"  {sym:<12} WR:{wr:.0%} R:{avgr:.1f} ✅")
            lines.append("")

        if underperformers:
            lines.append("⚠️  <b>Underperformers (conf artırıldı)</b>")
            for sym, wr, avgr in underperformers[:5]:
                lines.append(f"  {sym:<12} WR:{wr:.0%} R:{avgr:.1f} 🔴")
            lines.append("")

        lines += [
            f"🔧 Config Updates: {len(updated)} coins",
            f"⬆️  Confidence raised: {conf_raised}",
            f"⬇️  Confidence lowered: {conf_lowered}",
            "━━━━━━━━━━━━━━━━━━━━━━",
        ]

        # ── Ghost Learning önerilerini uygula ────────────────────
        ghost_changes = apply_ghost_suggestions(min_confidence="MEDIUM")
        if ghost_changes:
            lines.append("")
            lines.append("👻 <b>Ghost Learning Uygulandı</b>")
            for gc in ghost_changes[:8]:
                lines.append(f"  {gc}")
        # ─────────────────────────────────────────────────────────

        report = "\n".join(lines)
        logger.info("[NightlyOpt] %s", report.replace("\n", " | "))

        if tg_fn:
            try:
                tg_fn(report)
            except Exception as e:
                logger.warning(f"[NightlyOpt] Telegram gönderilemedi: {e}")

        return report

    except Exception as e:
        import traceback
        msg = f"[NightlyOpt] Hata: {e}\n{traceback.format_exc()[:400]}"
        logger.error(msg)
        return msg


# ═══════════════════════════════════════════════════════════

_EOD_CACHE = [None]


def analyze_and_adapt():
    try:
        conn    = db()
        current = get_current_params(conn)
        if not current:
            conn.close()
            return "Parametre bulunamadı."

        recent        = get_trades(conn, hours=48, limit=300)
        all_t         = get_all_trades(conn, limit=800)
        today_t       = get_today_trades(conn)
        last20        = get_last_n_trades(conn, 20)

        stats         = calc_stats(recent)
        last20_stats  = calc_stats(last20)
        sym_stats     = calc_symbol_stats(all_t)
        regime        = get_market_regime(recent)
        # Regime'i DB'ye yaz — ai_decision_engine okuyacak
        try:
            from database import set_market_regime as _set_regime
            _set_regime(regime)
        except Exception as _re:
            logger.debug(f"Regime yazılamadı: {_re}")
        heatmap       = calc_hourly_heatmap(all_t)
        bad_hours     = get_bad_hours(heatmap, threshold=0.30)
        drought       = is_drought(conn, hours=3)
        l_streak      = is_loss_streak(last20, 4)
        w_streak      = is_win_streak(last20, 3)
        ot, ot_count  = is_overtrading(conn, 8)
        cooldowns     = update_coin_cooldowns(conn, sym_stats)
        pm_insights   = postmortem_insights(conn, limit=200)

        # Markov geçiş matrisi
        markov_matrix = calc_markov_matrix(all_t)
        markov_adj, markov_changes = markov_insight(markov_matrix, regime)

        # Coin kişilik profillerini güncelle
        update_coin_profiles(conn, all_t)
        dangerous_coins = get_dangerous_coins(conn, danger_threshold=4)
        best_coins      = get_best_coins(conn)

        # En iyi parametre setini kaydet
        if stats:
            save_best_params_if_better(conn, current, stats)

        # Acil rollback kontrolü
        rolled_back, rollback_p = try_rollback_to_best(conn, last20_stats)

        if rolled_back:
            new_params = rollback_p
            changes    = [
                f"🔄 ROLLBACK: Win rate {last20_stats['win_rate']:.0%} "
                f"→ En iyi parametrelere geri dönüldü"]
        else:
            new_params, changes = optimize(
                current, stats, sym_stats,
                drought, l_streak, w_streak,
                regime, ot, pm_insights)

            # Markov ayarlarını uygula (optimize üstüne ek düzeltme)
            if markov_adj and markov_changes:
                for key, delta in markov_adj.items():
                    if key in PARAM_BOUNDS:
                        new_params[key] = clamp(
                            new_params.get(key, current.get(key, 0)) + delta,
                            *PARAM_BOUNDS[key])
                changes.extend(markov_changes)

            if changes:
                save_params(conn, new_params, " | ".join(changes), changes)

        insight = build_report(
            stats, sym_stats, changes,
            drought, l_streak, w_streak,
            new_params, current, regime,
            heatmap, cooldowns,
            ot_count, pm_insights, rolled_back,
            dangerous_coins, best_coins, bad_hours,
            markov_matrix=markov_matrix)

        log_analysis(conn, stats, insight, changes)

        eod = check_eod_summary(conn, today_t)
        _EOD_CACHE[0] = eod
        conn.close()

        if eod:
            return insight + "\n\n" + "─" * 30 + "\n\n" + eod
        return insight

    except Exception as e:
        import traceback
        return f"AI Brain hata: {e}\n{traceback.format_exc()[:500]}"


def get_eod_report():
    return _EOD_CACHE[0]
