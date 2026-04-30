"""
coin_library.py — Coin profil yönetimi ve öğrenme hafızası
"""

import logging
from datetime import datetime, timezone, timedelta
from database import get_conn

logger = logging.getLogger(__name__)

# ── Şablonlar ────────────────────────────────────────────────────────────────

PROFILE_TEMPLATES = {
    "stable": {
        "volatility_profile": "stable",
        "spread_quality":     "excellent",
        "danger_score":       1.0,
    },
    "normal": {
        "volatility_profile": "normal",
        "spread_quality":     "good",
        "danger_score":       2.5,
    },
    "volatile": {
        "volatility_profile": "volatile",
        "spread_quality":     "medium",
        "danger_score":       5.0,
    },
    "dangerous": {
        "volatility_profile": "dangerous",
        "spread_quality":     "poor",
        "danger_score":       8.0,
    },
}

# ── Başlangıç coin listesi (genişletilmiş) ────────────────────────────────────

INITIAL_COIN_PROFILES = {
    # ── Majors (en likit, düşük spread) ──────────────────────────────────────
    "BTCUSDT":    {**PROFILE_TEMPLATES["stable"]},
    "ETHUSDT":    {**PROFILE_TEMPLATES["stable"]},
    "BNBUSDT":    {**PROFILE_TEMPLATES["normal"]},
    "XRPUSDT":    {**PROFILE_TEMPLATES["normal"]},
    "LTCUSDT":    {**PROFILE_TEMPLATES["normal"]},
    "ADAUSDT":    {**PROFILE_TEMPLATES["normal"]},
    "MATICUSDT":  {**PROFILE_TEMPLATES["normal"]},
    "TRXUSDT":    {**PROFILE_TEMPLATES["normal"]},
    "XLMUSDT":    {**PROFILE_TEMPLATES["normal"]},
    "ATOMUSDT":   {**PROFILE_TEMPLATES["normal"]},

    # ── Layer-1 / hızlı hareket edenler ──────────────────────────────────────
    "SOLUSDT":    {**PROFILE_TEMPLATES["volatile"]},
    "AVAXUSDT":   {**PROFILE_TEMPLATES["volatile"]},
    "NEARUSDT":   {**PROFILE_TEMPLATES["volatile"]},
    "APTUSDT":    {**PROFILE_TEMPLATES["volatile"]},
    "SUIUSDT":    {**PROFILE_TEMPLATES["volatile"]},
    "SEIUSDT":    {**PROFILE_TEMPLATES["volatile"]},
    "INJUSDT":    {**PROFILE_TEMPLATES["volatile"]},
    "TIAUSDT":    {**PROFILE_TEMPLATES["volatile"]},
    "TAOUSDT":    {**PROFILE_TEMPLATES["volatile"]},

    # ── Layer-2 / DeFi ────────────────────────────────────────────────────────
    "ARBUSDT":    {**PROFILE_TEMPLATES["volatile"]},
    "OPUSDT":     {**PROFILE_TEMPLATES["volatile"]},
    "STRKUSDT":   {**PROFILE_TEMPLATES["volatile"]},
    "LDOUSDT":    {**PROFILE_TEMPLATES["volatile"]},
    "CRVUSDT":    {**PROFILE_TEMPLATES["volatile"]},
    "GMXUSDT":    {**PROFILE_TEMPLATES["volatile"]},
    "PENDLEUSDT": {**PROFILE_TEMPLATES["volatile"]},
    "JUPUSDT":    {**PROFILE_TEMPLATES["volatile"]},

    # ── Altyapı / Oracle / AI ─────────────────────────────────────────────────
    "LINKUSDT":   {**PROFILE_TEMPLATES["volatile"]},
    "DOTUSDT":    {**PROFILE_TEMPLATES["volatile"]},
    "FETUSDT":    {**PROFILE_TEMPLATES["volatile"]},
    "WLDUSDT":    {**PROFILE_TEMPLATES["volatile"]},
    "ONDOUSDT":   {**PROFILE_TEMPLATES["volatile"]},
    "RUNEUSDT":   {**PROFILE_TEMPLATES["volatile"]},

    # ── Meme coins ───────────────────────────────────────────────────────────
    "DOGEUSDT":   {**PROFILE_TEMPLATES["volatile"]},
    "SHIBUSDT":   {**PROFILE_TEMPLATES["volatile"]},
    "PEPEUSDT":   {**PROFILE_TEMPLATES["volatile"]},
    "WIFUSDT":    {**PROFILE_TEMPLATES["volatile"]},
    "FLOKIUSDT":  {**PROFILE_TEMPLATES["volatile"]},
    "BLURUSDT":   {**PROFILE_TEMPLATES["volatile"]},

    # ── Yüksek riskli ────────────────────────────────────────────────────────
    "AAVEUSDT":   {**PROFILE_TEMPLATES["dangerous"]},
    "ENAUSDT":    {**PROFILE_TEMPLATES["dangerous"]},
    "TONUSDT":    {**PROFILE_TEMPLATES["dangerous"]},
    "HIGHUSDT":   {**PROFILE_TEMPLATES["dangerous"]},
    "PENGUUSDT":  {**PROFILE_TEMPLATES["dangerous"]},
    "ZECUSDT":    {**PROFILE_TEMPLATES["dangerous"]},
    "SNXUSDT":    {**PROFILE_TEMPLATES["dangerous"]},
    "GUNUSDT":    {**PROFILE_TEMPLATES["dangerous"]},
}


# ── Başlangıç yükle ──────────────────────────────────────────────────────────

def seed_initial_profiles():
    conn = get_conn()
    c = conn.cursor()
    for symbol, tpl in INITIAL_COIN_PROFILES.items():
        c.execute("SELECT id FROM coin_profile WHERE symbol=?", (symbol,))
        if not c.fetchone():
            c.execute("""
                INSERT INTO coin_profile
                    (symbol, volatility_profile, spread_quality, danger_score, updated_at)
                VALUES (?, ?, ?, ?, ?)
            """, (
                symbol,
                tpl["volatility_profile"],
                tpl["spread_quality"],
                tpl["danger_score"],
                datetime.now(timezone.utc).isoformat(),
            ))
    conn.commit()
    conn.close()
    logger.info(f"[CoinLibrary] {len(INITIAL_COIN_PROFILES)} coin profili hazır.")


# ── Profil getir ─────────────────────────────────────────────────────────────

def get_coin_profile(symbol: str) -> dict:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM coin_profile WHERE symbol=?", (symbol,))
    row = c.fetchone()
    conn.close()
    if row:
        d = dict(row)
        # Süresi dolmuş cooldown'u temizle
        if d.get("cooldown_status"):
            if not is_in_cooldown(symbol):
                d["cooldown_status"] = 0
        return d
    tpl = INITIAL_COIN_PROFILES.get(symbol, PROFILE_TEMPLATES["normal"])
    return {
        "symbol":             symbol,
        "trade_count":        0,
        "win_rate":           0.0,
        "avg_r":              0.0,
        "profit_factor":      0.0,
        "avg_mfe":            0.0,
        "avg_mae":            0.0,
        "best_session":       None,
        "worst_session":      None,
        "preferred_direction": None,
        "long_count":         0,
        "short_count":        0,
        "best_rr_zone":       None,
        "danger_score":       tpl.get("danger_score", 2.0),
        "cooldown_status":    0,
        "volatility_profile": tpl.get("volatility_profile", "normal"),
        "spread_quality":     tpl.get("spread_quality", "good"),
        "fakeout_rate":       0.0,
        "total_pnl":          0.0,
    }


# ── Profil güncelle ───────────────────────────────────────────────────────────

def update_coin_profile(symbol: str, trade: dict):
    """Trade kapandıktan sonra coin profilini güncelle."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM coin_profile WHERE symbol=?", (symbol,))
    row = c.fetchone()

    result    = trade.get("result", "LOSS")
    r_mult    = float(trade.get("r_multiple", 0) or 0)
    mfe       = float(trade.get("mfe", 0) or 0)
    mae       = float(trade.get("mae", 0) or 0)
    direction = trade.get("direction", "")
    is_win    = result == "WIN"

    now = datetime.now(timezone.utc).isoformat()

    if row:
        p = dict(row)
        n      = p["trade_count"] + 1
        wins   = round((p.get("win_rate") or 0) * p["trade_count"])
        longs  = int(p.get("long_count") or 0)
        shorts = int(p.get("short_count") or 0)

        if is_win:
            wins += 1
        if direction == "LONG":
            longs += 1
        elif direction == "SHORT":
            shorts += 1

        win_rate = wins / n
        avg_r    = ((p.get("avg_r") or 0) * p["trade_count"] + r_mult) / n
        avg_mfe  = ((p.get("avg_mfe") or 0) * p["trade_count"] + mfe) / n
        avg_mae  = ((p.get("avg_mae") or 0) * p["trade_count"] + mae) / n

        # Profit factor: DB'den hesapla
        pf = _calc_profit_factor(symbol, conn)

        # Preferred direction (count bazlı)
        pref = "LONG" if longs >= shorts else "SHORT"

        # Danger score: çok kayıp varsa otomatik arttır
        danger = float(p.get("danger_score") or 2.5)
        if n >= 5:
            if win_rate < 0.30:
                danger = min(danger + 0.5, 10.0)
            elif win_rate >= 0.55:
                danger = max(danger - 0.2, 1.0)

        # total_pnl: trades tablosundan gerçek toplam
        c2 = conn.cursor()
        c2.execute("""
            SELECT COALESCE(SUM(net_pnl), 0)
            FROM trades WHERE symbol=?
              AND status NOT IN ('OPEN','TP1_HIT','TP2_HIT','RUNNER_ACTIVE')
        """, (symbol,))
        total_pnl = round(float(c2.fetchone()[0] or 0), 4)

        c.execute("""
            UPDATE coin_profile
            SET trade_count=?, win_rate=?, avg_r=?, avg_mfe=?, avg_mae=?,
                profit_factor=?, preferred_direction=?,
                long_count=?, short_count=?,
                danger_score=?, total_pnl=?, updated_at=?
            WHERE symbol=?
        """, (
            n, round(win_rate, 4), round(avg_r, 4),
            round(avg_mfe, 4), round(avg_mae, 4),
            round(pf, 3), pref,
            longs, shorts,
            round(danger, 2), total_pnl, now,
            symbol,
        ))
    else:
        tpl = INITIAL_COIN_PROFILES.get(symbol, PROFILE_TEMPLATES["normal"])
        c.execute("""
            INSERT INTO coin_profile
                (symbol, trade_count, win_rate, avg_r, avg_mfe, avg_mae,
                 profit_factor, preferred_direction, long_count, short_count,
                 volatility_profile, spread_quality, danger_score, updated_at)
            VALUES (?,1,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            symbol,
            1.0 if is_win else 0.0,
            round(r_mult, 4), round(mfe, 4), round(mae, 4),
            1.0 if is_win else 0.0,
            direction,
            1 if direction == "LONG" else 0,
            1 if direction == "SHORT" else 0,
            tpl.get("volatility_profile", "normal"),
            tpl.get("spread_quality", "good"),
            tpl.get("danger_score", 2.0),
            now,
        ))

    conn.commit()
    conn.close()
    logger.debug(f"[CoinLibrary] {symbol} profili güncellendi. n={row['trade_count']+1 if row else 1}")


def _calc_profit_factor(symbol: str, conn) -> float:
    c = conn.cursor()
    c.execute("""
        SELECT
            COALESCE(SUM(CASE WHEN result='WIN' THEN net_pnl ELSE 0 END), 0),
            COALESCE(ABS(SUM(CASE WHEN result='LOSS' THEN net_pnl ELSE 0 END)), 0)
        FROM trades
        WHERE symbol=?
          AND status NOT IN ('OPEN','TP1_HIT','TP2_HIT','RUNNER_ACTIVE')
    """, (symbol,))
    row = c.fetchone()
    gw = float(row[0] or 0)
    gl = float(row[1] or 0)
    return round(gw / gl, 3) if gl > 0 else round(gw, 3)


# ── Coin hafıza kaydı ─────────────────────────────────────────────────────────

def log_coin_memory(symbol: str, session: str, market_regime: str,
                    direction: str, result: str, r_multiple: float,
                    mfe: float, mae: float):
    conn = get_conn()
    conn.execute("""
        INSERT INTO coin_market_memory
            (symbol, session, market_regime, direction, result, r_multiple, mfe, mae)
        VALUES (?,?,?,?,?,?,?,?)
    """, (symbol, session, market_regime, direction, result,
          round(r_multiple, 4), round(mfe, 4), round(mae, 4)))
    conn.commit()
    conn.close()


# ── Cooldown yönetimi ─────────────────────────────────────────────────────────

def set_cooldown(symbol: str, minutes: int, reason: str = ""):
    until = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()
    conn = get_conn()
    conn.execute("""
        INSERT INTO coin_cooldown (symbol, reason, cooldown_until)
        VALUES (?,?,?)
        ON CONFLICT(symbol) DO UPDATE SET reason=excluded.reason,
            cooldown_until=excluded.cooldown_until,
            created_at=datetime('now')
    """, (symbol, reason, until))
    conn.execute(
        "UPDATE coin_profile SET cooldown_status=1 WHERE symbol=?", (symbol,)
    )
    conn.commit()
    conn.close()
    logger.warning(f"[CoinLibrary] {symbol} cooldown {minutes}dk: {reason}")


def clear_cooldown(symbol: str):
    conn = get_conn()
    conn.execute("DELETE FROM coin_cooldown WHERE symbol=?", (symbol,))
    conn.execute("UPDATE coin_profile SET cooldown_status=0 WHERE symbol=?", (symbol,))
    conn.commit()
    conn.close()


def is_in_cooldown(symbol: str) -> bool:
    """Cooldown aktif mi? Süresi geçmişse otomatik temizle."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT cooldown_until FROM coin_cooldown WHERE symbol=?", (symbol,))
    row = c.fetchone()
    conn.close()
    if not row:
        return False
    until = row["cooldown_until"] if isinstance(row, dict) else row[0]
    if datetime.now(timezone.utc).isoformat() > until:
        clear_cooldown(symbol)
        return False
    return True
