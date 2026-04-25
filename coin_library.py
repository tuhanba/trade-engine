"""
AURVEX.Ai — Coin Library v1.0
Her coin için ayrı parametre profili.
Botun öğrendikçe güncellediği, coin-bazlı dinamik parametre sistemi.

Profil alanları:
  sl_atr_mult   — Stop loss ATR çarpanı
  tp_atr_mult   — Take profit ATR çarpanı
  risk_pct      — Maksimum risk yüzdesi
  max_leverage  — Maksimum kaldıraç
  min_adx       — Minimum ADX eşiği
  min_bb_width  — Minimum Bollinger Band genişliği
  min_volume_m  — Minimum hacim (milyon $)
  rsi5_min      — RSI 5m minimum
  rsi5_max      — RSI 5m maksimum
  enabled       — Bu coin trade edilsin mi
  profile       — Coin tipi: "normal" / "volatile" / "stable" / "dangerous"
  notes         — Açıklama
"""

import sqlite3, os, json, logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trading.db")

# =============================================================================
# VARSAYILAN PROFİLLER
# =============================================================================

# Temel profil şablonları
PROFILE_TEMPLATES = {
    "normal": {
        "sl_atr_mult": 1.2,
        "tp_atr_mult": 2.0,
        "risk_pct":    1.5,
        "max_leverage": 15,
        "min_adx":     20,
        "min_bb_width": 1.3,
        "min_volume_m": 10.0,
        "rsi5_min":    35,
        "rsi5_max":    75,
        "enabled":     True,
        "profile":     "normal",
    },
    "volatile": {
        "sl_atr_mult": 1.6,
        "tp_atr_mult": 2.8,
        "risk_pct":    0.8,
        "max_leverage": 10,
        "min_adx":     25,
        "min_bb_width": 2.0,
        "min_volume_m": 20.0,
        "rsi5_min":    30,
        "rsi5_max":    78,
        "enabled":     True,
        "profile":     "volatile",
    },
    "dangerous": {
        "sl_atr_mult": 1.8,
        "tp_atr_mult": 2.5,
        "risk_pct":    0.5,
        "max_leverage": 8,
        "min_adx":     28,
        "min_bb_width": 2.2,
        "min_volume_m": 30.0,
        "rsi5_min":    32,
        "rsi5_max":    72,
        "enabled":     True,
        "profile":     "dangerous",
    },
    "stable": {
        "sl_atr_mult": 1.0,
        "tp_atr_mult": 1.8,
        "risk_pct":    2.0,
        "max_leverage": 20,
        "min_adx":     18,
        "min_bb_width": 1.0,
        "min_volume_m": 5.0,
        "rsi5_min":    38,
        "rsi5_max":    72,
        "enabled":     True,
        "profile":     "stable",
    },
}

# Başlangıç coin profilleri — bot öğrendikçe bunlar güncellenir
INITIAL_COIN_PROFILES = {
    # Tehlikeli / yüksek volatilite
    "AAVEUSDT":   {**PROFILE_TEMPLATES["dangerous"], "notes": "Yüksek volatilite, ani dumplar"},
    "ADAUSDT":    {**PROFILE_TEMPLATES["dangerous"], "notes": "Manipülatif hareketler"},
    "PENGUUSDT":  {**PROFILE_TEMPLATES["dangerous"], "notes": "Düşük likidite, spike riski"},
    "GUNUSDT":    {**PROFILE_TEMPLATES["dangerous"], "notes": "Yeni token, yüksek risk"},
    "ZECUSDT":    {**PROFILE_TEMPLATES["dangerous"], "notes": "Düşük hacim dönemleri"},
    "ENAUSDT":    {**PROFILE_TEMPLATES["dangerous"], "notes": "Yüksek funding rate riski"},
    "TONUSDT":    {**PROFILE_TEMPLATES["dangerous"], "notes": "Ekosistem haberlere duyarlı"},
    "HIGHUSDT":   {**PROFILE_TEMPLATES["dangerous"], "notes": "Düşük likidite"},
    # Volatil ama trade edilebilir
    "SOLUSDT":    {**PROFILE_TEMPLATES["volatile"],  "notes": "Hızlı trend, iyi likidite"},
    "BNBUSDT":    {**PROFILE_TEMPLATES["volatile"],  "notes": "Exchange token, manipülasyon riski"},
    "DOGEUSDT":   {**PROFILE_TEMPLATES["volatile"],  "notes": "Sosyal medya duyarlı"},
    "SHIBUSDT":   {**PROFILE_TEMPLATES["volatile"],  "notes": "Meme coin, spike riski"},
    "PEPEUSDT":   {**PROFILE_TEMPLATES["volatile"],  "notes": "Meme coin, yüksek volatilite"},
    "WIFUSDT":    {**PROFILE_TEMPLATES["volatile"],  "notes": "Meme coin"},
    "FLOKIUSDT":  {**PROFILE_TEMPLATES["volatile"],  "notes": "Meme coin"},
    "AVAXUSDT":   {**PROFILE_TEMPLATES["volatile"],  "notes": "Ekosistem haberlere duyarlı"},
    "LINKUSDT":   {**PROFILE_TEMPLATES["volatile"],  "notes": "Oracle haberlere duyarlı"},
    "DOTUSDT":    {**PROFILE_TEMPLATES["volatile"],  "notes": "Parachain haberlere duyarlı"},
    "NEARUSDT":   {**PROFILE_TEMPLATES["volatile"],  "notes": "Layer 1, orta volatilite"},
    "APTUSDT":    {**PROFILE_TEMPLATES["volatile"],  "notes": "Yeni L1, yüksek beta"},
    "SUIUSDT":    {**PROFILE_TEMPLATES["volatile"],  "notes": "Yeni L1, yüksek beta"},
    "ARBUSDT":    {**PROFILE_TEMPLATES["volatile"],  "notes": "L2 token"},
    "OPUSDT":     {**PROFILE_TEMPLATES["volatile"],  "notes": "L2 token"},
    # Normal profil
    "XRPUSDT":    {**PROFILE_TEMPLATES["normal"],    "notes": "Yüksek likidite, iyi trend"},
    "LTCUSDT":    {**PROFILE_TEMPLATES["normal"],    "notes": "Stabil trend"},
    "XLMUSDT":    {**PROFILE_TEMPLATES["normal"],    "notes": "Orta volatilite"},
    "ALGOUSDT":   {**PROFILE_TEMPLATES["normal"],    "notes": "Düşük volatilite"},
    "TRXUSDT":    {**PROFILE_TEMPLATES["normal"],    "notes": "Stabil, iyi likidite"},
    "MATICUSDT":  {**PROFILE_TEMPLATES["normal"],    "notes": "Orta volatilite"},
    "ATOMUSDT":   {**PROFILE_TEMPLATES["normal"],    "notes": "Cosmos ekosistemi"},
    "FTMUSDT":    {**PROFILE_TEMPLATES["normal"],    "notes": "Fantom, orta risk"},
    "SANDUSDT":   {**PROFILE_TEMPLATES["normal"],    "notes": "Metaverse, orta volatilite"},
    "MANAUSDT":   {**PROFILE_TEMPLATES["normal"],    "notes": "Metaverse, orta volatilite"},
}


# =============================================================================
# VERİTABANI İŞLEMLERİ
# =============================================================================

def init_coin_library():
    """coin_params tablosunu oluştur ve başlangıç profillerini yükle."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS coin_params (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol      TEXT UNIQUE NOT NULL,
            profile     TEXT DEFAULT 'normal',
            sl_atr_mult REAL DEFAULT 1.2,
            tp_atr_mult REAL DEFAULT 2.0,
            risk_pct    REAL DEFAULT 1.5,
            max_leverage INTEGER DEFAULT 15,
            min_adx     REAL DEFAULT 20,
            min_bb_width REAL DEFAULT 1.3,
            min_volume_m REAL DEFAULT 10.0,
            rsi5_min    REAL DEFAULT 35,
            rsi5_max    REAL DEFAULT 75,
            enabled     INTEGER DEFAULT 1,
            total_trades INTEGER DEFAULT 0,
            wins        INTEGER DEFAULT 0,
            losses      INTEGER DEFAULT 0,
            win_rate    REAL DEFAULT 0.5,
            avg_pnl     REAL DEFAULT 0.0,
            last_updated TEXT,
            notes       TEXT DEFAULT ''
        )
    """)
    conn.commit()

    # Başlangıç profillerini yükle (zaten varsa atla)
    for symbol, profile in INITIAL_COIN_PROFILES.items():
        c.execute("SELECT id FROM coin_params WHERE symbol=?", (symbol,))
        if not c.fetchone():
            c.execute("""
                INSERT INTO coin_params
                (symbol, profile, sl_atr_mult, tp_atr_mult, risk_pct, max_leverage,
                 min_adx, min_bb_width, min_volume_m, rsi5_min, rsi5_max, enabled,
                 last_updated, notes)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                symbol,
                profile["profile"],
                profile["sl_atr_mult"],
                profile["tp_atr_mult"],
                profile["risk_pct"],
                profile["max_leverage"],
                profile["min_adx"],
                profile["min_bb_width"],
                profile["min_volume_m"],
                profile["rsi5_min"],
                profile["rsi5_max"],
                1 if profile["enabled"] else 0,
                datetime.now(timezone.utc).isoformat(),
                profile.get("notes", ""),
            ))
    conn.commit()
    conn.close()
    logger.info(f"[CoinLibrary] {len(INITIAL_COIN_PROFILES)} coin profili yüklendi.")


def get_coin_params(symbol: str) -> dict:
    """
    Bir coin için parametre profilini döndür.
    Veritabanında yoksa varsayılan 'normal' profili döndür.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM coin_params WHERE symbol=?", (symbol,))
        row = c.fetchone()
        conn.close()
        if row:
            return dict(row)
    except Exception as e:
        logger.error(f"[CoinLibrary] get_coin_params hata: {e}")

    # Varsayılan: normal profil
    return {**PROFILE_TEMPLATES["normal"], "symbol": symbol, "notes": "Otomatik — varsayılan profil"}


def update_coin_stats(symbol: str, result: str, net_pnl: float):
    """
    Trade kapandığında coin istatistiklerini güncelle.
    Win rate ve avg PNL otomatik hesaplanır.
    Yeterli veri birikince parametreler otomatik ayarlanır.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()

        # Mevcut istatistikleri al
        c.execute("""
            SELECT total_trades, wins, losses, avg_pnl, sl_atr_mult, tp_atr_mult,
                   risk_pct, profile
            FROM coin_params WHERE symbol=?
        """, (symbol,))
        row = c.fetchone()

        if not row:
            # Coin yoksa ekle
            c.execute("""
                INSERT INTO coin_params (symbol, profile, last_updated)
                VALUES (?, 'normal', ?)
            """, (symbol, datetime.now(timezone.utc).isoformat()))
            conn.commit()
            total, wins, losses, avg_pnl = 0, 0, 0, 0.0
            sl_mult, tp_mult, risk_pct, profile = 1.2, 2.0, 1.5, "normal"
        else:
            total, wins, losses, avg_pnl, sl_mult, tp_mult, risk_pct, profile = row

        # Güncelle
        total += 1
        if result == "WIN":
            wins += 1
        else:
            losses += 1
        win_rate = wins / total if total > 0 else 0.5
        avg_pnl  = (avg_pnl * (total - 1) + net_pnl) / total

        # Otomatik parametre ayarı — 10+ trade varsa
        new_sl, new_tp, new_risk = sl_mult, tp_mult, risk_pct
        if total >= 10:
            if win_rate < 0.40:
                # Düşük win rate: SL'yi genişlet, risk'i azalt
                new_sl   = min(sl_mult * 1.05, 2.5)
                new_risk = max(risk_pct * 0.95, 0.3)
                logger.info(f"[CoinLibrary] {symbol} WR={win_rate:.0%} düşük → SL={new_sl:.2f}, risk={new_risk:.2f}")
            elif win_rate > 0.65:
                # Yüksek win rate: TP'yi uzat, risk'i artır
                new_tp   = min(tp_mult * 1.05, 4.0)
                new_risk = min(risk_pct * 1.03, 3.0)
                logger.info(f"[CoinLibrary] {symbol} WR={win_rate:.0%} yüksek → TP={new_tp:.2f}, risk={new_risk:.2f}")

        c.execute("""
            UPDATE coin_params
            SET total_trades=?, wins=?, losses=?, win_rate=?, avg_pnl=?,
                sl_atr_mult=?, tp_atr_mult=?, risk_pct=?,
                last_updated=?
            WHERE symbol=?
        """, (
            total, wins, losses, round(win_rate, 4), round(avg_pnl, 4),
            round(new_sl, 3), round(new_tp, 3), round(new_risk, 3),
            datetime.now(timezone.utc).isoformat(),
            symbol,
        ))
        conn.commit()
        conn.close()
        logger.debug(f"[CoinLibrary] {symbol} güncellendi: WR={win_rate:.0%} total={total}")
    except Exception as e:
        logger.error(f"[CoinLibrary] update_coin_stats hata: {e}")


def get_all_coin_stats() -> list:
    """Dashboard için tüm coin istatistiklerini döndür."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("""
            SELECT symbol, profile, total_trades, wins, losses, win_rate,
                   avg_pnl, sl_atr_mult, tp_atr_mult, risk_pct, max_leverage,
                   enabled, last_updated, notes
            FROM coin_params
            ORDER BY total_trades DESC
        """)
        rows = [dict(r) for r in c.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        logger.error(f"[CoinLibrary] get_all_coin_stats hata: {e}")
        return []


def disable_coin(symbol: str, reason: str = ""):
    """Bir coini trade listesinden çıkar."""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            UPDATE coin_params SET enabled=0, notes=?, last_updated=?
            WHERE symbol=?
        """, (reason, datetime.now(timezone.utc).isoformat(), symbol))
        conn.commit()
        conn.close()
        logger.warning(f"[CoinLibrary] {symbol} devre dışı: {reason}")
    except Exception as e:
        logger.error(f"[CoinLibrary] disable_coin hata: {e}")


def enable_coin(symbol: str):
    """Bir coini tekrar aktif et."""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            UPDATE coin_params SET enabled=1, last_updated=?
            WHERE symbol=?
        """, (datetime.now(timezone.utc).isoformat(), symbol))
        conn.commit()
        conn.close()
        logger.info(f"[CoinLibrary] {symbol} aktif edildi.")
    except Exception as e:
        logger.error(f"[CoinLibrary] enable_coin hata: {e}")


def is_coin_enabled(symbol: str) -> bool:
    """Coin trade edilebilir mi?"""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT enabled FROM coin_params WHERE symbol=?", (symbol,))
        row = c.fetchone()
        conn.close()
        if row is not None:
            return bool(row[0])
    except:
        pass
    return True  # Kayıt yoksa varsayılan: aktif
