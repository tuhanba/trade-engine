import logging
from datetime import datetime, timezone, timedelta
from database import get_conn

logger = logging.getLogger(__name__)

PROFILE_TEMPLATES = {
    "normal": {
        "volatility_profile": "normal",
        "spread_quality": "good",
        "danger_score": 2.0,
    },
    "volatile": {
        "volatility_profile": "volatile",
        "spread_quality": "medium",
        "danger_score": 5.0,
    },
    "dangerous": {
        "volatility_profile": "dangerous",
        "spread_quality": "poor",
        "danger_score": 8.0,
    },
    "stable": {
        "volatility_profile": "stable",
        "spread_quality": "good",
        "danger_score": 1.0,
    },
}

INITIAL_COIN_PROFILES = {
    "AAVEUSDT":  {**PROFILE_TEMPLATES["dangerous"]},
    "ADAUSDT":   {**PROFILE_TEMPLATES["dangerous"]},
    "PENGUUSDT": {**PROFILE_TEMPLATES["dangerous"]},
    "GUNUSDT":   {**PROFILE_TEMPLATES["dangerous"]},
    "ZECUSDT":   {**PROFILE_TEMPLATES["dangerous"]},
    "ENAUSDT":   {**PROFILE_TEMPLATES["dangerous"]},
    "TONUSDT":   {**PROFILE_TEMPLATES["dangerous"]},
    "HIGHUSDT":  {**PROFILE_TEMPLATES["dangerous"]},
    "SOLUSDT":   {**PROFILE_TEMPLATES["volatile"]},
    "BNBUSDT":   {**PROFILE_TEMPLATES["volatile"]},
    "DOGEUSDT":  {**PROFILE_TEMPLATES["volatile"]},
    "SHIBUSDT":  {**PROFILE_TEMPLATES["volatile"]},
    "PEPEUSDT":  {**PROFILE_TEMPLATES["volatile"]},
    "WIFUSDT":   {**PROFILE_TEMPLATES["volatile"]},
    "FLOKIUSDT": {**PROFILE_TEMPLATES["volatile"]},
    "AVAXUSDT":  {**PROFILE_TEMPLATES["volatile"]},
    "LINKUSDT":  {**PROFILE_TEMPLATES["volatile"]},
    "DOTUSDT":   {**PROFILE_TEMPLATES["volatile"]},
    "NEARUSDT":  {**PROFILE_TEMPLATES["volatile"]},
    "APTUSDT":   {**PROFILE_TEMPLATES["volatile"]},
    "SUIUSDT":   {**PROFILE_TEMPLATES["volatile"]},
    "ARBUSDT":   {**PROFILE_TEMPLATES["volatile"]},
    "OPUSDT":    {**PROFILE_TEMPLATES["volatile"]},
    "XRPUSDT":   {**PROFILE_TEMPLATES["normal"]},
    "LTCUSDT":   {**PROFILE_TEMPLATES["normal"]},
    "XLMUSDT":   {**PROFILE_TEMPLATES["normal"]},
    "TRXUSDT":   {**PROFILE_TEMPLATES["normal"]},
    "MATICUSDT": {**PROFILE_TEMPLATES["normal"]},
    "ATOMUSDT":  {**PROFILE_TEMPLATES["normal"]},
}


def seed_initial_profiles():
    """Başlangıç coin profillerini coin_profile tablosuna yükle (zaten varsa atla)."""
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
    logger.info("[CoinLibrary] Başlangıç profilleri yüklendi.")


def get_coin_profile(symbol: str) -> dict:
    """coin_profile tablosundan coin profilini döndür. Kayıt yoksa varsayılan."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM coin_profile WHERE symbol=?", (symbol,))
    row = c.fetchone()
    conn.close()
    if row:
        return dict(row)
    tpl = INITIAL_COIN_PROFILES.get(symbol, PROFILE_TEMPLATES["normal"])
    return {
        "symbol": symbol,
        "trade_count": 0,
        "win_rate": 0.0,
        "avg_r": 0.0,
        "profit_factor": 0.0,
        "avg_mfe": 0.0,
        "avg_mae": 0.0,
        "best_session": None,
        "worst_session": None,
        "preferred_direction": None,
        "best_rr_zone": None,
        "danger_score": tpl.get("danger_score", 2.0),
        "cooldown_status": 0,
        "volatility_profile": tpl.get("volatility_profile", "normal"),
        "spread_quality": tpl.get("spread_quality", "good"),
        "fakeout_rate": 0.0,
    }


def update_coin_profile(symbol: str, trade: dict):
    """
    Trade kapandıktan sonra coin profilini güncelle.
    trade dict: result, r_multiple, mfe, mae, session, direction
    """
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM coin_profile WHERE symbol=?", (symbol,))
    row = c.fetchone()

    if row:
        p = dict(row)
        n = p["trade_count"] + 1
        wins = round(p["win_rate"] * p["trade_count"])
        if trade.get("result") == "WIN":
            wins += 1
        win_rate = wins / n
        avg_r   = (p["avg_r"] * p["trade_count"] + trade.get("r_multiple", 0)) / n
        avg_mfe = (p["avg_mfe"] * p["trade_count"] + trade.get("mfe", 0)) / n
        avg_mae = (p["avg_mae"] * p["trade_count"] + trade.get("mae", 0)) / n

        c.execute("""
            UPDATE coin_profile
            SET trade_count=?, win_rate=?, avg_r=?, avg_mfe=?, avg_mae=?,
                preferred_direction=?, updated_at=?
            WHERE symbol=?
        """, (
            n,
            round(win_rate, 4),
            round(avg_r, 4),
            round(avg_mfe, 4),
            round(avg_mae, 4),
            trade.get("direction"),
            datetime.now(timezone.utc).isoformat(),
            symbol,
        ))
    else:
        tpl = INITIAL_COIN_PROFILES.get(symbol, PROFILE_TEMPLATES["normal"])
        win_rate = 1.0 if trade.get("result") == "WIN" else 0.0
        c.execute("""
            INSERT INTO coin_profile
                (symbol, trade_count, win_rate, avg_r, avg_mfe, avg_mae,
                 preferred_direction, volatility_profile, spread_quality,
                 danger_score, updated_at)
            VALUES (?,1,?,?,?,?,?,?,?,?,?)
        """, (
            symbol,
            win_rate,
            round(trade.get("r_multiple", 0), 4),
            round(trade.get("mfe", 0), 4),
            round(trade.get("mae", 0), 4),
            trade.get("direction"),
            tpl.get("volatility_profile", "normal"),
            tpl.get("spread_quality", "good"),
            tpl.get("danger_score", 2.0),
            datetime.now(timezone.utc).isoformat(),
        ))

    conn.commit()
    conn.close()
    logger.debug(f"[CoinLibrary] {symbol} profili güncellendi.")


def log_coin_memory(symbol: str, session: str, market_regime: str,
                    direction: str, result: str, r_multiple: float,
                    mfe: float, mae: float):
    """Her trade sonrası coin_market_memory tablosuna kayıt ekle."""
    conn = get_conn()
    conn.execute("""
        INSERT INTO coin_market_memory
            (symbol, session, market_regime, direction, result, r_multiple, mfe, mae)
        VALUES (?,?,?,?,?,?,?,?)
    """, (symbol, session, market_regime, direction, result,
          round(r_multiple, 4), round(mfe, 4), round(mae, 4)))
    conn.commit()
    conn.close()


def set_cooldown(symbol: str, minutes: int, reason: str = ""):
    """Coini belirtilen süre boyunca taramadan çıkar."""
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
    """Cooldown'u manuel kaldır."""
    conn = get_conn()
    conn.execute("DELETE FROM coin_cooldown WHERE symbol=?", (symbol,))
    conn.execute(
        "UPDATE coin_profile SET cooldown_status=0 WHERE symbol=?", (symbol,)
    )
    conn.commit()
    conn.close()


def is_in_cooldown(symbol: str) -> bool:
    """Coin hâlâ cooldown'da mı? Süresi geçmişse otomatik temizle."""
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
