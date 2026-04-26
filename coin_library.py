"""
coin_library.py — AX Coin Kütüphanesi
=======================================
• 20 coinlik COIN_UNIVERSE (config.py'den)
• Her coin için başlangıç trading parametreleri
• Trade sonrası istatistik güncelleme
• Coin öğrenme: win_rate, avg_R, profit_factor, avg_mfe, avg_mae,
  best_session, preferred_direction, danger_score, fakeout_rate
"""

import logging
from datetime import datetime, timezone

from config import COIN_UNIVERSE, DB_PATH
from database import get_conn, upsert_coin_profile, get_coin_profile

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# PROFIL ŞABLONLARI
# ─────────────────────────────────────────────────────────────────────────────

_TEMPLATES = {
    "stable": {
        "sl_atr_mult": 1.0, "tp_atr_mult": 1.8, "risk_pct": 1.0,
        "max_leverage": 20, "min_adx": 18, "min_bb_width": 1.0,
        "min_volume_m": 20.0,
    },
    "normal": {
        "sl_atr_mult": 1.3, "tp_atr_mult": 2.0, "risk_pct": 1.0,
        "max_leverage": 15, "min_adx": 20, "min_bb_width": 1.3,
        "min_volume_m": 15.0,
    },
    "volatile": {
        "sl_atr_mult": 1.5, "tp_atr_mult": 2.2, "risk_pct": 0.8,
        "max_leverage": 10, "min_adx": 23, "min_bb_width": 1.8,
        "min_volume_m": 20.0,
    },
    "dangerous": {
        "sl_atr_mult": 1.8, "tp_atr_mult": 2.5, "risk_pct": 0.5,
        "max_leverage": 8,  "min_adx": 26, "min_bb_width": 2.2,
        "min_volume_m": 30.0,
    },
}

# Coin evreninin başlangıç trading parametreleri
# Bot öğrendikçe bunlar otomatik güncellenir
_COIN_PARAMS: dict[str, dict] = {
    "BTCUSDT":  {**_TEMPLATES["stable"],    "volatility_profile": "stable"},
    "ETHUSDT":  {**_TEMPLATES["stable"],    "volatility_profile": "stable"},
    "BNBUSDT":  {**_TEMPLATES["volatile"],  "volatility_profile": "volatile"},
    "XRPUSDT":  {**_TEMPLATES["normal"],    "volatility_profile": "normal"},
    "SOLUSDT":  {**_TEMPLATES["volatile"],  "volatility_profile": "volatile"},
    "DOGEUSDT": {**_TEMPLATES["volatile"],  "volatility_profile": "volatile"},
    "ADAUSDT":  {**_TEMPLATES["dangerous"], "volatility_profile": "dangerous"},
    "AVAXUSDT": {**_TEMPLATES["volatile"],  "volatility_profile": "volatile"},
    "LINKUSDT": {**_TEMPLATES["volatile"],  "volatility_profile": "volatile"},
    "NEARUSDT": {**_TEMPLATES["normal"],    "volatility_profile": "normal"},
    "OPUSDT":   {**_TEMPLATES["volatile"],  "volatility_profile": "volatile"},
    "ARBUSDT":  {**_TEMPLATES["volatile"],  "volatility_profile": "volatile"},
    "INJUSDT":  {**_TEMPLATES["volatile"],  "volatility_profile": "volatile"},
    "SUIUSDT":  {**_TEMPLATES["volatile"],  "volatility_profile": "volatile"},
    "FETUSDT":  {**_TEMPLATES["normal"],    "volatility_profile": "normal"},
    "RNDRUSDT": {**_TEMPLATES["normal"],    "volatility_profile": "normal"},
    "WIFUSDT":  {**_TEMPLATES["dangerous"], "volatility_profile": "dangerous"},
    "PEPEUSDT": {**_TEMPLATES["dangerous"], "volatility_profile": "dangerous"},
    "SEIUSDT":  {**_TEMPLATES["normal"],    "volatility_profile": "normal"},
    "LTCUSDT":  {**_TEMPLATES["normal"],    "volatility_profile": "normal"},
}

# ─────────────────────────────────────────────────────────────────────────────
# INIT
# ─────────────────────────────────────────────────────────────────────────────

def init_coin_library():
    """COIN_UNIVERSE coin_params tablosuna yükle (tablo database.py'de oluşturulur)."""
    with get_conn() as conn:
        for symbol in COIN_UNIVERSE:
            p = _COIN_PARAMS.get(symbol, _TEMPLATES["normal"])
            conn.execute(
                """INSERT OR IGNORE INTO coin_params
                   (symbol, volatility_profile, sl_atr_mult, tp_atr_mult, risk_pct,
                    max_leverage, min_adx, min_bb_width, min_volume_m)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (symbol,
                 p.get("volatility_profile", "normal"),
                 p["sl_atr_mult"], p["tp_atr_mult"], p["risk_pct"],
                 p["max_leverage"], p["min_adx"], p["min_bb_width"], p["min_volume_m"])
            )
    logger.info(f"[CoinLibrary] {len(COIN_UNIVERSE)} coin yüklendi.")


# ─────────────────────────────────────────────────────────────────────────────
# TRADING PARAMETRELERİ
# ─────────────────────────────────────────────────────────────────────────────

def get_coin_params(symbol: str) -> dict:
    """Trading parametrelerini döndür. Yoksa varsayılan normal profil."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM coin_params WHERE symbol=?", (symbol,)
        ).fetchone()
        if row:
            return dict(row)
    return {**_TEMPLATES["normal"], "symbol": symbol, "volatility_profile": "normal", "enabled": 1}

def is_coin_enabled(symbol: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT enabled FROM coin_params WHERE symbol=?", (symbol,)
        ).fetchone()
        if row:
            return bool(row["enabled"])
    return symbol in COIN_UNIVERSE

def disable_coin(symbol: str, reason: str = ""):
    with get_conn() as conn:
        conn.execute(
            "UPDATE coin_params SET enabled=0, updated_at=datetime('now') WHERE symbol=?",
            (symbol,)
        )
    logger.warning(f"[CoinLibrary] {symbol} devre dışı: {reason}")

def enable_coin(symbol: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE coin_params SET enabled=1, updated_at=datetime('now') WHERE symbol=?",
            (symbol,)
        )
    logger.info(f"[CoinLibrary] {symbol} aktif edildi.")

def get_active_coins() -> list:
    """Trade edilebilir aktif coin listesi."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT symbol FROM coin_params WHERE enabled=1 AND symbol IN (%s)"
            % ",".join("?" * len(COIN_UNIVERSE)),
            COIN_UNIVERSE
        ).fetchall()
        return [r["symbol"] for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# ÖĞRENME — İSTATİSTİK GÜNCELLEMESİ
# ─────────────────────────────────────────────────────────────────────────────

def update_coin_stats(symbol: str, result: str, net_pnl: float,
                      r_multiple: float = 0, mfe_r: float = 0, mae_r: float = 0,
                      session: str = None, direction: str = None):
    """
    Trade kapandığında coin öğrenme istatistiklerini güncelle.
    10+ trade varsa parametreler otomatik ayarlanır.
    """
    profile = get_coin_profile(symbol)

    tc   = profile.get("trade_count", 0) + 1
    wins = profile.get("win_count", 0) + (1 if result == "WIN" else 0)
    losses = profile.get("loss_count", 0) + (1 if result != "WIN" else 0)
    wr   = wins / tc

    # Kayan ortalamalar
    n = tc
    avg_r   = (profile.get("avg_r", 0) * (n - 1) + r_multiple) / n
    avg_mfe = (profile.get("avg_mfe", 0) * (n - 1) + mfe_r) / n
    avg_mae = (profile.get("avg_mae", 0) * (n - 1) + mae_r) / n

    # Profit factor (basit tahmin)
    gross_win  = wins * avg_r if wins > 0 else 0
    gross_loss = losses * abs(avg_r) if losses > 0 else 1
    pf = gross_win / gross_loss if gross_loss > 0 else 0

    # Fakeout rate — MAE > SL olan trade'ler
    fakeout_rate = profile.get("fakeout_rate", 0)
    if mae_r > 1.0 and result != "WIN":
        fakeout_rate = min(1.0, fakeout_rate + 0.05)

    # Danger score — kayıp + fakeout + düşük WR kombine
    danger_score = max(0.0, min(1.0,
        (1 - wr) * 0.4 + fakeout_rate * 0.3 + (1 - min(pf, 2) / 2) * 0.3
    ))

    # Best session güncelle
    best_session = profile.get("best_session")
    if session and result == "WIN":
        if not best_session:
            best_session = session

    # Preferred direction
    pref_dir = profile.get("preferred_direction")
    if tc >= 10 and direction:
        with get_conn() as conn:
            long_wins = conn.execute(
                "SELECT COUNT(*) FROM trades WHERE symbol=? AND direction='LONG' AND net_pnl>0",
                (symbol,)
            ).fetchone()[0]
            short_wins = conn.execute(
                "SELECT COUNT(*) FROM trades WHERE symbol=? AND direction='SHORT' AND net_pnl>0",
                (symbol,)
            ).fetchone()[0]
        pref_dir = "LONG" if long_wins >= short_wins else "SHORT"

    upsert_coin_profile(symbol, {
        "trade_count":         tc,
        "win_count":           wins,
        "loss_count":          losses,
        "win_rate":            round(wr, 4),
        "avg_r":               round(avg_r, 4),
        "profit_factor":       round(pf, 4),
        "avg_mfe":             round(avg_mfe, 4),
        "avg_mae":             round(avg_mae, 4),
        "best_session":        best_session,
        "preferred_direction": pref_dir,
        "danger_score":        round(danger_score, 4),
        "fakeout_rate":        round(fakeout_rate, 4),
        "volatility_profile":  profile.get("volatility_profile", "normal"),
    })

    # 10+ trade varsa trading parametrelerini otomatik ayarla
    if tc >= 10:
        _auto_adjust_params(symbol, wr, avg_mae)

    logger.debug(f"[CoinLibrary] {symbol} güncellendi: WR={wr:.0%} R={avg_r:.2f} PF={pf:.2f}")


def _auto_adjust_params(symbol: str, win_rate: float, avg_mae: float):
    """Win rate ve MAE'ye göre SL/risk parametrelerini dinamik ayarla."""
    params = get_coin_params(symbol)
    sl = params.get("sl_atr_mult", 1.3)
    rp = params.get("risk_pct", 1.0)
    changed = False

    if win_rate < 0.38:
        sl  = min(sl * 1.08, 2.5)
        rp  = max(rp * 0.90, 0.3)
        changed = True
        logger.info(f"[CoinLibrary] {symbol} WR düşük ({win_rate:.0%}) → SL={sl:.2f} risk={rp:.2f}")
    elif win_rate > 0.60 and avg_mae < 0.5:
        sl  = max(sl * 0.97, 0.8)
        rp  = min(rp * 1.05, 2.0)
        changed = True
        logger.info(f"[CoinLibrary] {symbol} WR yüksek ({win_rate:.0%}) → SL={sl:.2f} risk={rp:.2f}")

    if changed:
        with get_conn() as conn:
            conn.execute(
                "UPDATE coin_params SET sl_atr_mult=?, risk_pct=?, updated_at=datetime('now') WHERE symbol=?",
                (round(sl, 3), round(rp, 3), symbol)
            )


# ─────────────────────────────────────────────────────────────────────────────
# DASHBOARD / RAPOR
# ─────────────────────────────────────────────────────────────────────────────

def get_all_coin_stats() -> list:
    """Dashboard ve /coin komutu için tüm coin istatistikleri."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT cp.symbol, cp.enabled, cp.volatility_profile,
                      cp.sl_atr_mult, cp.risk_pct,
                      cf.trade_count, cf.win_rate, cf.avg_r, cf.profit_factor,
                      cf.avg_mfe, cf.avg_mae, cf.danger_score, cf.fakeout_rate,
                      cf.best_session, cf.preferred_direction
               FROM coin_params cp
               LEFT JOIN coin_profile cf ON cp.symbol = cf.symbol
               ORDER BY cf.trade_count DESC NULLS LAST"""
        ).fetchall()
        return [dict(r) for r in rows]

def get_best_coins(top_n: int = 3) -> list:
    """En yüksek avg_R'ye sahip coinler."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT symbol, avg_r, win_rate, trade_count FROM coin_profile
               WHERE trade_count >= 5
               ORDER BY avg_r DESC LIMIT ?""",
            (top_n,)
        ).fetchall()
        return [dict(r) for r in rows]

def get_worst_coins(top_n: int = 3) -> list:
    """En yüksek danger_score'a sahip coinler."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT symbol, danger_score, win_rate, trade_count FROM coin_profile
               WHERE trade_count >= 5
               ORDER BY danger_score DESC LIMIT ?""",
            (top_n,)
        ).fetchall()
        return [dict(r) for r in rows]
