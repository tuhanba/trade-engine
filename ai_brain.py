"""
ai_brain.py — AX karar motoru (Faz 1: kural tabanlı skorlama)

Her signal candidate için:
  - Veto kontrolü (hard stop)
  - Çok boyutlu skorlama (0-100)
  - ALLOW / VETO / WATCH kararı
  - risk_flag, confidence, reason
"""

import logging
from datetime import datetime, timezone

import config
from database import get_conn
from coin_library import get_coin_profile

logger = logging.getLogger(__name__)

# --- Veto sebepleri ---
VETO_CIRCUIT_BREAKER    = "circuit_breaker"
VETO_DAILY_LOSS         = "daily_loss_limit"
VETO_TOO_MANY_TRADES    = "too_many_open_trades"
VETO_COIN_COOLDOWN      = "coin_cooldown"
VETO_BAD_RR             = "bad_rr"
VETO_MFE_LOW            = "expected_mfe_low"
VETO_BAD_SESSION        = "bad_session"
VETO_CHOP               = "chop_market"
VETO_FAKEOUT            = "fakeout_risk"
VETO_WEAK_STRUCTURE     = "weak_structure"
VETO_LOW_VOLUME         = "low_volume"

# --- Session ağırlıkları ---
_SESSION_SCORE = {
    "LONDON":  15,
    "NY":      15,
    "OVERLAP": 10,
    "ASIA":     5,
    "OFF":      0,
}

# --- Score eşikleri ---
_ALLOW_THRESHOLD = 60
_WATCH_THRESHOLD = 40


# ---------- DB yardımcıları ----------

def _get_system_state() -> dict:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM system_state WHERE id=1")
    row = c.fetchone()
    conn.close()
    if row:
        return dict(row)
    return {
        "circuit_breaker_active": 0,
        "circuit_breaker_until": None,
        "consecutive_losses": 0,
        "daily_loss_pct": 0.0,
        "execution_mode": config.EXECUTION_MODE,
        "ax_mode": config.AX_MODE,
    }


def _open_trade_count() -> int:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM trades WHERE status='OPEN'")
    count = c.fetchone()[0]
    conn.close()
    return count


def _daily_pnl() -> float:
    """Bugünkü kapanan trade'lerin net PnL toplamı."""
    conn = get_conn()
    c = conn.cursor()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    c.execute("""
        SELECT COALESCE(SUM(net_pnl), 0.0)
        FROM trades
        WHERE status IN ('CLOSED_WIN','CLOSED_LOSS','CLOSED_MANUAL','CLOSED_TRAIL','WIN','LOSS')
          AND close_time >= ?
    """, (today,))
    total = c.fetchone()[0]
    conn.close()
    return float(total)


def _paper_balance() -> float:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT paper_balance FROM paper_account WHERE id=1")
    row = c.fetchone()
    conn.close()
    return float(row[0]) if row else 250.0


# ---------- Skorlama ----------

def _score_rr(rr: float) -> int:
    if rr >= 2.0:  return 25
    if rr >= 1.8:  return 22
    if rr >= 1.5:  return 18
    if rr >= 1.2:  return 12
    return 5


def _score_mfe(mfe_r: float) -> int:
    if mfe_r >= 2.5:  return 20
    if mfe_r >= 2.0:  return 17
    if mfe_r >= 1.5:  return 13
    if mfe_r >= 1.0:  return 8
    return 3


def _score_setup(setup_type: str) -> int:
    return {"BREAKOUT": 20, "PULLBACK": 15}.get(setup_type, 8)


def _score_coin(profile: dict) -> int:
    score = 0
    wr = profile.get("win_rate", 0.0)
    if wr >= 0.55:   score += 10
    elif wr >= 0.45: score += 7
    elif wr >= 0.35: score += 3

    fakeout = profile.get("fakeout_rate", 0.0)
    if fakeout < 0.2:    score += 5
    elif fakeout < 0.35: score += 2

    danger = profile.get("danger_score", 5.0)
    if danger <= 2:   score += 5
    elif danger <= 5: score += 3

    return min(score, 20)


def _score_session(session: str) -> int:
    return _SESSION_SCORE.get(session, 0)


# ---------- Ana fonksiyon ----------

def evaluate(candidate: dict) -> dict:
    """
    Candidate dict'i değerlendir.

    Döndürür:
        decision        : "ALLOW" | "VETO" | "WATCH"
        score           : 0-100
        confidence      : 0.0-1.0
        reason          : açıklama
        risk_flag       : "LOW" | "MEDIUM" | "HIGH"
        veto_reason     : veto sebebi (sadece VETO'da)
        expected_mfe_r  : tahmin
        expected_mae_r  : tahmin
    """
    symbol   = candidate.get("symbol", "")
    rr       = candidate.get("rr", 0.0)
    mfe_r    = candidate.get("expected_mfe_r", 0.0)
    session  = candidate.get("session", "OFF")
    setup    = candidate.get("setup_type", "")

    state    = _get_system_state()
    profile  = get_coin_profile(symbol)
    open_cnt = _open_trade_count()
    balance  = _paper_balance()
    daily_pnl = _daily_pnl()
    daily_loss_pct = (daily_pnl / balance * 100) if balance > 0 else 0.0

    # ── Hard veto'lar (sıra önemli) ──────────────────────────────────────────

    # Circuit breaker
    if state.get("circuit_breaker_active"):
        until = state.get("circuit_breaker_until", "")
        if until and datetime.now(timezone.utc).isoformat() < until:
            return _veto(VETO_CIRCUIT_BREAKER, 0,
                         f"Circuit breaker aktif, bitiş: {until}")

    # Günlük max loss
    if daily_loss_pct <= -config.DAILY_MAX_LOSS_PCT:
        return _veto(VETO_DAILY_LOSS, 5,
                     f"Günlük kayıp limiti aşıldı: {daily_loss_pct:.1f}%")

    # Çok fazla açık trade
    if open_cnt >= config.MAX_OPEN_TRADES:
        return _veto(VETO_TOO_MANY_TRADES, 10,
                     f"Açık trade: {open_cnt}/{config.MAX_OPEN_TRADES}")

    # Coin cooldown
    if profile.get("cooldown_status"):
        return _veto(VETO_COIN_COOLDOWN, 10,
                     f"{symbol} cooldown'da")

    # Bad session
    if session == "OFF":
        return _veto(VETO_BAD_SESSION, 15,
                     "Piyasa kapalı session")

    # RR yetersiz
    if rr < config.MIN_RR:
        return _veto(VETO_BAD_RR, 20,
                     f"RR={rr:.2f} < MIN={config.MIN_RR}")

    # MFE filtresi
    if mfe_r < config.MIN_EXPECTED_MFE_R:
        return _veto(VETO_MFE_LOW, 20,
                     f"MFE={mfe_r} < MIN={config.MIN_EXPECTED_MFE_R}")

    # Yüksek fakeout riski
    if profile.get("fakeout_rate", 0) >= 0.5:
        return _veto(VETO_FAKEOUT, 25,
                     f"{symbol} fakeout_rate={profile['fakeout_rate']:.0%}")

    # Chop — düşük MFE + zayıf setup birlikte
    if mfe_r < 1.2 and setup not in ("BREAKOUT",):
        return _veto(VETO_CHOP, 22,
                     "Düşük MFE + zayıf setup → chop şüphesi")

    # ── Skorlama ─────────────────────────────────────────────────────────────

    score = (
        _score_rr(rr)
        + _score_mfe(mfe_r)
        + _score_setup(setup)
        + _score_coin(profile)
        + _score_session(session)
    )
    score = min(score, 100)

    # Zayıf yapı — skor eşiği altı
    if score < _WATCH_THRESHOLD:
        return _veto(VETO_WEAK_STRUCTURE, score,
                     f"Skor çok düşük: {score}")

    # ── Karar ─────────────────────────────────────────────────────────────────

    confidence = round(score / 100, 2)

    if score >= _ALLOW_THRESHOLD:
        decision = "ALLOW"
    else:
        decision = "WATCH"

    risk_flag = (
        "LOW"    if profile.get("danger_score", 5) <= 2 else
        "HIGH"   if profile.get("danger_score", 5) >= 7 else
        "MEDIUM"
    )

    # expected_mae_r: SL'ye yaklaşma tahmini — MFE'nin ~%40'ı
    expected_mae_r = round(mfe_r * 0.4, 2)

    return {
        "decision":        decision,
        "score":           score,
        "confidence":      confidence,
        "reason":          f"{setup} | session={session} | RR={rr:.2f} | MFE={mfe_r}",
        "risk_flag":       risk_flag,
        "veto_reason":     None,
        "expected_mfe_r":  mfe_r,
        "expected_mae_r":  expected_mae_r,
    }


def _veto(reason: str, score: int, detail: str = "") -> dict:
    logger.info(f"[AX] VETO — {reason}: {detail}")
    return {
        "decision":        "VETO",
        "score":           score,
        "confidence":      round(score / 100, 2),
        "reason":          detail,
        "risk_flag":       "HIGH",
        "veto_reason":     reason,
        "expected_mfe_r":  0.0,
        "expected_mae_r":  0.0,
    }
