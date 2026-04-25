"""
database.py — AX Merkezi Veritabanı
=====================================
WAL mode, tek writer, tüm tablolar burada.

Tablolar:
  trades               — Açık/kapanan trade'ler
  signal_candidates    — Her sinyal buraya kaydedilir
  paper_account        — Paper trading bakiyesi
  params               — Bot parametreleri
  ai_logs              — AX kararları ve gerekçeleri
  trade_postmortem     — Trade sonrası MFE/MAE analizi
  coin_profile         — Coin öğrenme profili
  coin_market_memory   — Coin piyasa hafızası
  coin_cooldown        — Coin bazlı bekleme
  daily_summary        — Günlük özet
  weekly_summary       — Haftalık özet
  dashboard_snapshots  — Dashboard geçmişi
  best_params          — En iyi parametre seti
  system_state         — Sistem durumu (CB, pause vb.)
  pattern_memory       — Genel pattern hafızası
"""

import sqlite3
import json
import logging
from datetime import datetime, timezone, date, timedelta
from config import DB_PATH

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# BAĞLANTI
# ─────────────────────────────────────────────────────────────────────────────

def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ─────────────────────────────────────────────────────────────────────────────
# INIT — TÜM TABLOLAR
# ─────────────────────────────────────────────────────────────────────────────

def init_db():
    with get_conn() as conn:
        conn.executescript("""

        -- ── trades ──────────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS trades (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol           TEXT NOT NULL,
            direction        TEXT NOT NULL,
            status           TEXT DEFAULT 'open',
            environment      TEXT DEFAULT 'paper',
            ax_mode          TEXT DEFAULT 'execute',

            entry            REAL,
            sl               REAL,
            tp1              REAL,
            tp2              REAL,
            trail_stop       REAL,

            qty              REAL,
            qty_tp1          REAL,
            qty_tp2          REAL,
            qty_runner       REAL,

            realized_pnl     REAL DEFAULT 0,
            net_pnl          REAL DEFAULT 0,
            r_multiple       REAL DEFAULT 0,

            tp1_hit          INTEGER DEFAULT 0,
            tp2_hit          INTEGER DEFAULT 0,

            linked_candidate_id INTEGER,
            open_time        TEXT,
            close_time       TEXT,
            close_price      REAL,
            close_reason     TEXT,
            hold_minutes     REAL DEFAULT 0
        );

        -- ── signal_candidates ────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS signal_candidates (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol           TEXT NOT NULL,
            direction        TEXT,
            entry            REAL,
            sl               REAL,
            tp1              REAL,
            tp2              REAL,
            runner_target    REAL,
            rr               REAL,
            expected_mfe_r   REAL,
            score            REAL DEFAULT 0,
            confidence       REAL DEFAULT 0,
            decision         TEXT DEFAULT 'PENDING',
            veto_reason      TEXT,
            session          TEXT,
            market_regime    TEXT,
            ax_mode          TEXT DEFAULT 'execute',
            execution_mode   TEXT DEFAULT 'paper',
            linked_trade_id  INTEGER,
            created_at       TEXT DEFAULT (datetime('now'))
        );

        -- ── paper_account ────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS paper_account (
            id              INTEGER PRIMARY KEY CHECK (id = 1),
            balance         REAL DEFAULT 250.0,
            initial_balance REAL DEFAULT 250.0,
            updated_at      TEXT DEFAULT (datetime('now'))
        );

        -- ── params ──────────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS params (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            data       TEXT NOT NULL,
            reason     TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        -- ── ai_logs ─────────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS ai_logs (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            event        TEXT,
            symbol       TEXT,
            decision     TEXT,
            score        REAL,
            confidence   REAL,
            reason       TEXT,
            data         TEXT,
            created_at   TEXT DEFAULT (datetime('now'))
        );

        -- ── trade_postmortem ─────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS trade_postmortem (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id        INTEGER UNIQUE,
            symbol          TEXT,
            direction       TEXT,
            mfe_r           REAL,
            mae_r           REAL,
            efficiency      REAL,
            missed_gain     REAL,
            sl_tightness    REAL,
            hold_minutes    REAL,
            exit_quality    REAL,
            setup_quality   REAL,
            notes           TEXT,
            created_at      TEXT DEFAULT (datetime('now'))
        );

        -- ── coin_profile ─────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS coin_profile (
            symbol              TEXT PRIMARY KEY,
            trade_count         INTEGER DEFAULT 0,
            win_count           INTEGER DEFAULT 0,
            loss_count          INTEGER DEFAULT 0,
            win_rate            REAL DEFAULT 0,
            avg_r               REAL DEFAULT 0,
            profit_factor       REAL DEFAULT 0,
            avg_mfe             REAL DEFAULT 0,
            avg_mae             REAL DEFAULT 0,
            best_session        TEXT,
            preferred_direction TEXT,
            danger_score        REAL DEFAULT 0,
            fakeout_rate        REAL DEFAULT 0,
            volatility_profile  TEXT DEFAULT 'normal',
            updated_at          TEXT DEFAULT (datetime('now'))
        );

        -- ── coin_market_memory ───────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS coin_market_memory (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol     TEXT NOT NULL,
            session    TEXT,
            regime     TEXT,
            direction  TEXT,
            result     TEXT,
            r_multiple REAL,
            created_at TEXT DEFAULT (datetime('now'))
        );

        -- ── coin_cooldown ────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS coin_cooldown (
            symbol      TEXT PRIMARY KEY,
            reason      TEXT,
            until       TEXT NOT NULL,
            created_at  TEXT DEFAULT (datetime('now'))
        );

        -- ── daily_summary ────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS daily_summary (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            date         TEXT UNIQUE,
            trade_count  INTEGER DEFAULT 0,
            win_count    INTEGER DEFAULT 0,
            loss_count   INTEGER DEFAULT 0,
            win_rate     REAL DEFAULT 0,
            gross_pnl    REAL DEFAULT 0,
            net_pnl      REAL DEFAULT 0,
            avg_r        REAL DEFAULT 0,
            max_drawdown REAL DEFAULT 0,
            balance_eod  REAL DEFAULT 0,
            created_at   TEXT DEFAULT (datetime('now'))
        );

        -- ── weekly_summary ───────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS weekly_summary (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            week_start   TEXT UNIQUE,
            trade_count  INTEGER DEFAULT 0,
            win_count    INTEGER DEFAULT 0,
            loss_count   INTEGER DEFAULT 0,
            win_rate     REAL DEFAULT 0,
            net_pnl      REAL DEFAULT 0,
            avg_r        REAL DEFAULT 0,
            best_day     TEXT,
            worst_day    TEXT,
            created_at   TEXT DEFAULT (datetime('now'))
        );

        -- ── dashboard_snapshots ──────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS dashboard_snapshots (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            note       TEXT,
            balance    REAL,
            data       TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        -- ── best_params ──────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS best_params (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            data        TEXT NOT NULL,
            win_rate    REAL,
            avg_r       REAL,
            pnl         REAL,
            trade_count INTEGER,
            created_at  TEXT DEFAULT (datetime('now'))
        );

        -- ── system_state ─────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS system_state (
            key        TEXT PRIMARY KEY,
            value      TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        );

        -- ── pattern_memory ───────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS pattern_memory (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol       TEXT,
            direction    TEXT,
            session      TEXT,
            result       TEXT,
            net_pnl      REAL,
            hold_minutes REAL,
            partial_exit INTEGER DEFAULT 0,
            created_at   TEXT DEFAULT (datetime('now'))
        );

        """)

    # ── Eski şemadan yeni kolonlara migration ─────────────────────────────────
    with get_conn() as conn:
        _migrate(conn)


# ─────────────────────────────────────────────────────────────────────────────
# MIGRATION
# ─────────────────────────────────────────────────────────────────────────────

def _migrate(conn):
    """Veritabanı şema değişikliklerini uygular."""
    # Örnek: _add_column(conn, 'trades', 'new_col', 'TEXT')
    pass

def _table_has_column(conn, table_name, column_name):
    cursor = conn.execute(f"PRAGMA table_info({table_name})")
    columns = [row['name'] for row in cursor.fetchall()]
    return column_name in columns

def _add_column(conn, table_name, column_name, column_type):
    if not _table_has_column(conn, table_name, column_name):
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
        logger.info(f"'{table_name}' tablosuna '{column_name}' kolonu eklendi.")


# ─────────────────────────────────────────────────────────────────────────────
# OKUMA / YAZMA
# ─────────────────────────────────────────────────────────────────────────────

def get_trades(limit=20, offset=0, status=None, symbol=None):
    """Trade listesini döndürür."""
    with get_conn() as conn:
        query = "SELECT * FROM trades WHERE 1=1"
        params = []
        if symbol:
            query += " AND symbol=?"
            params.append(symbol)
        if status:
            query += " AND status=?"
            params.append(status)
        query += " ORDER BY id DESC LIMIT ? OFFSET ?"
        params.append(limit)
        params.append(offset)
        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

def get_trade(trade_id: int):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
        return dict(row) if row else None

def save_trade(t: dict) -> int:
    sql = """
        INSERT INTO trades
            (symbol, direction, status, environment, ax_mode,
             entry, sl, tp1, tp2, trail_stop,
             qty, qty_tp1, qty_tp2, qty_runner,
             linked_candidate_id, open_time)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    params = (
        t['symbol'], t['direction'], t.get('status', 'open'), t.get('environment', 'paper'), t.get('ax_mode', 'execute'),
        t['entry'], t['sl'], t['tp1'], t['tp2'], t.get('trail_stop'),
        t['qty'], t['qty_tp1'], t['qty_tp2'], t['qty_runner'],
        t.get('linked_candidate_id'), t.get('open_time')
    )
    with get_conn() as conn:
        cursor = conn.execute(sql, params)
        conn.commit()
        return cursor.lastrowid

def update_trade(trade_id: int, updates: dict):
    """Bir trade'i günceller."""
    fields = ", ".join([f"{k}=?" for k in updates.keys()])
    values = list(updates.values()) + [trade_id]
    sql = f"UPDATE trades SET {fields} WHERE id=?"
    with get_conn() as conn:
        conn.execute(sql, values)
        conn.commit()

def get_open_trades(symbol: str = None):
    return get_trades(status='open', symbol=symbol, limit=1000)

def get_current_params() -> dict:
    """En son eklenen parametreleri döndürür."""
    with get_conn() as conn:
        row = conn.execute("SELECT data FROM params ORDER BY id DESC LIMIT 1").fetchone()
        if row:
            return json.loads(row["data"])
        return {}

def save_params(params: dict, reason: str):
    """Yeni parametre setini kaydeder."""
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO params (data, reason) VALUES (?, ?)",
            (json.dumps(params), reason)
        )
        conn.commit()

def get_last_ai_log():
    with get_conn() as conn:
        row = conn.execute("SELECT reason FROM ai_logs ORDER BY id DESC LIMIT 1").fetchone()
        return row["reason"] if row else "Henüz AI kararı yok."

def get_stats():
    """Genel dashboard istatistiklerini zenginleştirilmiş olarak hesaplar."""
    with get_conn() as conn:
        # Genel Trade İstatistikleri
        row = conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN status='closed' THEN 1 ELSE 0 END) as total_closed,
                SUM(CASE WHEN status='closed' THEN net_pnl ELSE 0 END) as total_pnl,
                SUM(CASE WHEN status='closed' AND net_pnl > 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN status='closed' AND net_pnl <= 0 THEN 1 ELSE 0 END) as losses,
                SUM(CASE WHEN status='closed' THEN r_multiple ELSE 0 END) as total_r,
                SUM(CASE WHEN status='closed' THEN hold_minutes ELSE 0 END) as total_duration
            FROM trades
        """).fetchone()

        total = row['total'] or 0
        total_closed = row['total_closed'] or 0
        total_pnl = row['total_pnl'] or 0
        wins = row['wins'] or 0
        losses = row['losses'] or 0
        total_r = row['total_r'] or 0
        total_duration = row['total_duration'] or 0

        # En iyi / en kötü tradeler
        best_trade_row = conn.execute("SELECT * FROM trades WHERE status='closed' ORDER BY net_pnl DESC LIMIT 1").fetchone()
        worst_trade_row = conn.execute("SELECT * FROM trades WHERE status='closed' ORDER BY net_pnl ASC LIMIT 1").fetchone()

        # Açık trade sayısı
        open_trades_row = conn.execute("SELECT COUNT(*) as count FROM trades WHERE status='open'").fetchone()

        # Son AI logu
        last_ai_log = get_last_ai_log()

        # Tüm pozitif ve negatif PnL'leri topla (profit factor için)
        all_closed_trades = get_trades(limit=10000, status="closed")
        positive_pnl = sum(t['net_pnl'] for t in all_closed_trades if t['net_pnl'] > 0)
        negative_pnl = abs(sum(t['net_pnl'] for t in all_closed_trades if t['net_pnl'] < 0))

        stats = {
            "total": total,
            "total_closed": total_closed,
            "total_pnl": round(total_pnl, 2),
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins * 100 / max(total_closed, 1), 1),
            "avg_pnl": round(total_pnl / max(total_closed, 1), 2),
            "avg_r": round(total_r / max(total_closed, 1), 2),
            "avg_rr": round(total_r / max(total_closed, 1), 2), # avg_r ile aynı, frontend avg_rr bekliyor
            "avg_dur": round(total_duration / max(total_closed, 1), 1),
            "profit_factor": round(positive_pnl / max(negative_pnl, 1), 2),
            "best_trade": dict(best_trade_row) if best_trade_row else None,
            "worst_trade": dict(worst_trade_row) if worst_trade_row else None,
            "open_count": open_trades_row['count'] or 0,
            "last_ai": last_ai_log,
            "params": get_current_params()
        }
        return stats

def get_session_stats():
    """Seans bazında (ASIA, LONDON, NEW_YORK) PnL ve trade sayılarını hesaplar."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT
                c.session,
                COUNT(t.id) as trade_count,
                SUM(t.net_pnl) as pnl
            FROM trades t
            JOIN signal_candidates c ON t.linked_candidate_id = c.id
            WHERE t.status = 'closed'
            GROUP BY c.session
        """).fetchall()

        # Frontend'in beklediği formatta bir dictionary oluştur
        # NEW_YORK -> NEWYORK dönüşümü de burada yapılır
        session_stats = {
            "ASIA": {"pnl": 0, "count": 0},
            "LONDON": {"pnl": 0, "count": 0},
            "NEWYORK": {"pnl": 0, "count": 0},
            "OFF": {"pnl": 0, "count": 0}
        }
        for row in rows:
            session_name = row['session']
            if session_name == 'NEW_YORK':
                session_name = 'NEWYORK'

            if session_name in session_stats:
                session_stats[session_name]['pnl'] = round(row['pnl'] or 0, 2)
                session_stats[session_name]['count'] = row['trade_count'] or 0

        return session_stats

def get_daily_summary(start_date: str = None, end_date: str = None):
    """Günlük özet verilerini döndürür."""
    with get_conn() as conn:
        query = "SELECT * FROM daily_summary WHERE 1=1"
        params = []
        if start_date:
            query += " AND date >= ?"
            params.append(start_date)
        if end_date:
            query += " AND date <= ?"
            params.append(end_date)
        query += " ORDER BY date ASC"
        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

def get_equity_curve():
    """Equity Curve için kümülatif PnL verilerini döndürür."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT
                id,
                open_time,
                close_time,
                net_pnl
            FROM trades
            WHERE status = 'closed'
            ORDER BY close_time ASC
        """).fetchall()

        equity_curve_data = []
        cumulative_pnl = 0
        for row in rows:
            cumulative_pnl += row['net_pnl']
            equity_curve_data.append({
                "trade_id": row['id'],
                "date": row['close_time'],
                "pnl": round(row['net_pnl'], 2),
                "cumulative_pnl": round(cumulative_pnl, 2)
            })
        return equity_curve_data

def get_coin_performance():
    """Coin bazlı performans metriklerini döndürür."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT
                symbol,
                COUNT(*) as trade_count,
                SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN net_pnl <= 0 THEN 1 ELSE 0 END) as losses,
                SUM(net_pnl) as total_pnl,
                AVG(r_multiple) as avg_r
            FROM trades
            WHERE status = 'closed'
            GROUP BY symbol
            ORDER BY total_pnl DESC
        """).fetchall()
        return [dict(row) for row in rows]

def get_veto_stats():
    """Veto nedenlerinin dağılımını döndürür."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT
                veto_reason,
                COUNT(*) as count
            FROM signal_candidates
            WHERE decision = 'VETOED'
            GROUP BY veto_reason
            ORDER BY count DESC
        """).fetchall()
        return [dict(row) for row in rows]

def get_system_health():
    """Sistem sağlığı kontrolü için gerekli bilgileri döndürür."""
    with get_conn() as conn:
        system_state = {}
        rows = conn.execute("SELECT key, value FROM system_state").fetchall()
        for row in rows:
            system_state[row['key']] = row['value']

        # Örnek olarak bazı varsayılan değerler
        health_status = {
            "bot_loop": system_state.get('bot_loop_status', 'UNKNOWN'),
            "database": "OK", # DB'ye erişebildiğimize göre OK
            "telegram": system_state.get('telegram_status', 'UNKNOWN'),
            "dashboard": system_state.get('dashboard_status', 'UNKNOWN'),
            "n8n": system_state.get('n8n_status', 'UNKNOWN'),
            "last_update": datetime.now(timezone.utc).isoformat()
        }
        return health_status

def get_all_params():
    """Tüm kaydedilmiş parametre setlerini döndürür."""
    with get_conn() as conn:
        rows = conn.execute("SELECT id, data, reason, created_at FROM params ORDER BY created_at DESC").fetchall()
        return [dict(row) for row in rows]

def get_params_by_id(param_id: int):
    """Belirli bir parametre setini ID'ye göre döndürür."""
    with get_conn() as conn:
        row = conn.execute("SELECT data FROM params WHERE id=?", (param_id,)).fetchone()
        return json.loads(row["data"]) if row else None

def get_signal_candidates(limit=20, offset=0, decision=None, symbol=None):
    """Sinyal adaylarını döndürür."""
    with get_conn() as conn:
        query = "SELECT * FROM signal_candidates WHERE 1=1"
        params = []
        if decision:
            query += " AND decision=?"
            params.append(decision)
        if symbol:
            query += " AND symbol=?"
            params.append(symbol)
        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.append(limit)
        params.append(offset)
        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

def save_ai_log(event: str, symbol: str, decision: str, score: float, confidence: float, reason: str, data: dict):
    """AI kararlarını loglar."""
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO ai_logs (event, symbol, decision, score, confidence, reason, data) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (event, symbol, decision, score, confidence, reason, json.dumps(data))
        )
        conn.commit()

def get_ai_logs(limit=20, offset=0, symbol=None, decision=None):
    """AI loglarını döndürür."""
    with get_conn() as conn:
        query = "SELECT * FROM ai_logs WHERE 1=1"
        params = []
        if symbol:
            query += " AND symbol=?"
            params.append(symbol)
        if decision:
            query += " AND decision=?"
            params.append(decision)
        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.append(limit)
        params.append(offset)
        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

def get_paper_account_balance():
    """Paper trading hesap bakiyesini döndürür."""
    with get_conn() as conn:
        row = conn.execute("SELECT balance FROM paper_account WHERE id=1").fetchone()
        return row["balance"] if row else 250.0

def update_paper_account_balance(new_balance: float):
    """Paper trading hesap bakiyesini günceller."""
    with get_conn() as conn:
        conn.execute("UPDATE paper_account SET balance=?, updated_at=datetime('now') WHERE id=1", (new_balance,))
        conn.commit()

def get_trade_postmortem(trade_id: int):
    """Bir trade'in post-mortem analizini döndürür."""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM trade_postmortem WHERE trade_id=?", (trade_id,)).fetchone()
        return dict(row) if row else None

def save_trade_postmortem(pm: dict):
    """Bir trade'in post-mortem analizini kaydeder."""
    sql = """
        INSERT INTO trade_postmortem
            (trade_id, symbol, direction, mfe_r, mae_r, efficiency, missed_gain, sl_tightness, hold_minutes, exit_quality, setup_quality, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    params = (
        pm['trade_id'], pm['symbol'], pm['direction'], pm['mfe_r'], pm['mae_r'], pm['efficiency'],
        pm['missed_gain'], pm['sl_tightness'], pm['hold_minutes'], pm['exit_quality'], pm['setup_quality'], pm['notes']
    )
    with get_conn() as conn:
        conn.execute(sql, params)
        conn.commit()

def get_coin_profile(symbol: str):
    """Bir coinin profilini döndürür."""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM coin_profile WHERE symbol=?", (symbol,)).fetchone()
        return dict(row) if row else None

def update_coin_profile(profile: dict):
    """Bir coinin profilini günceller veya oluşturur."""
    sql = """
        INSERT INTO coin_profile (symbol, trade_count, win_count, loss_count, win_rate, avg_r, profit_factor, avg_mfe, avg_mae, best_session, preferred_direction, danger_score, fakeout_rate, volatility_profile)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol) DO UPDATE SET
            trade_count=excluded.trade_count,
            win_count=excluded.win_count,
            loss_count=excluded.loss_count,
            win_rate=excluded.win_rate,
            avg_r=excluded.avg_r,
            profit_factor=excluded.profit_factor,
            avg_mfe=excluded.avg_mfe,
            avg_mae=excluded.avg_mae,
            best_session=excluded.best_session,
            preferred_direction=excluded.preferred_direction,
            danger_score=excluded.danger_score,
            fakeout_rate=excluded.fakeout_rate,
            volatility_profile=excluded.volatility_profile,
            updated_at=datetime('now')
    """
    params = (
        profile['symbol'], profile['trade_count'], profile['win_count'], profile['loss_count'], profile['win_rate'],
        profile['avg_r'], profile['profit_factor'], profile['avg_mfe'], profile['avg_mae'], profile['best_session'],
        profile['preferred_direction'], profile['danger_score'], profile['fakeout_rate'], profile['volatility_profile']
    )
    with get_conn() as conn:
        conn.execute(sql, params)
        conn.commit()

def get_coin_market_memory(symbol: str, session: str = None, regime: str = None):
    """Coin piyasa hafızasını döndürür."""
    with get_conn() as conn:
        query = "SELECT * FROM coin_market_memory WHERE symbol=?"
        params = [symbol]
        if session:
            query += " AND session=?"
            params.append(session)
        if regime:
            query += " AND regime=?"
            params.append(regime)
        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

def save_coin_market_memory(memory: dict):
    """Coin piyasa hafızasını kaydeder."""
    sql = """
        INSERT INTO coin_market_memory (symbol, session, regime, direction, result, r_multiple)
        VALUES (?, ?, ?, ?, ?, ?)
    """
    params = (
        memory['symbol'], memory['session'], memory['regime'], memory['direction'], memory['result'], memory['r_multiple']
    )
    with get_conn() as conn:
        conn.execute(sql, params)
        conn.commit()

def get_coin_cooldown(symbol: str):
    """Bir coinin cooldown durumunu döndürür."""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM coin_cooldown WHERE symbol=?", (symbol,)).fetchone()
        return dict(row) if row else None

def set_coin_cooldown(symbol: str, reason: str, until: datetime):
    """Bir coini cooldown'a alır."""
    sql = """
        INSERT INTO coin_cooldown (symbol, reason, until)
        VALUES (?, ?, ?)
        ON CONFLICT(symbol) DO UPDATE SET
            reason=excluded.reason,
            until=excluded.until,
            created_at=datetime('now')
    """
    with get_conn() as conn:
        conn.execute(sql, (symbol, reason, until.isoformat()))
        conn.commit()

def remove_coin_cooldown(symbol: str):
    """Bir coini cooldown'dan çıkarır."""
    with get_conn() as conn:
        conn.execute("DELETE FROM coin_cooldown WHERE symbol=?", (symbol,))
        conn.commit()

def save_daily_summary(summary: dict):
    """Günlük özeti kaydeder veya günceller."""
    sql = """
        INSERT INTO daily_summary (date, trade_count, win_count, loss_count, win_rate, gross_pnl, net_pnl, avg_r, max_drawdown, balance_eod)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(date) DO UPDATE SET
            trade_count=excluded.trade_count,
            win_count=excluded.win_count,
            loss_count=excluded.loss_count,
            win_rate=excluded.win_rate,
            gross_pnl=excluded.gross_pnl,
            net_pnl=excluded.net_pnl,
            avg_r=excluded.avg_r,
            max_drawdown=excluded.max_drawdown,
            balance_eod=excluded.balance_eod,
            created_at=datetime('now')
    """
    params = (
        summary['date'], summary['trade_count'], summary['win_count'], summary['loss_count'], summary['win_rate'],
        summary['gross_pnl'], summary['net_pnl'], summary['avg_r'], summary['max_drawdown'], summary['balance_eod']
    )
    with get_conn() as conn:
        conn.execute(sql, params)
        conn.commit()

def get_weekly_summary(week_start: str):
    """Haftalık özeti döndürür."""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM weekly_summary WHERE week_start=?", (week_start,)).fetchone()
        return dict(row) if row else None

def save_weekly_summary(summary: dict):
    """Haftalık özeti kaydeder veya günceller."""
    sql = """
        INSERT INTO weekly_summary (week_start, trade_count, win_count, loss_count, win_rate, net_pnl, avg_r, best_day, worst_day)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(week_start) DO UPDATE SET
            trade_count=excluded.trade_count,
            win_count=excluded.win_count,
            loss_count=excluded.loss_count,
            win_rate=excluded.win_rate,
            net_pnl=excluded.net_pnl,
            avg_r=excluded.avg_r,
            best_day=excluded.best_day,
            worst_day=excluded.worst_day,
            created_at=datetime('now')
    """
    params = (
        summary['week_start'], summary['trade_count'], summary['win_count'], summary['loss_count'], summary['win_rate'],
        summary['net_pnl'], summary['avg_r'], summary['best_day'], summary['worst_day']
    )
    with get_conn() as conn:
        conn.execute(sql, params)
        conn.commit()

def save_dashboard_snapshot(note: str, balance: float, data: dict):
    """Dashboard anlık görüntüsünü kaydeder."""
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO dashboard_snapshots (note, balance, data) VALUES (?, ?, ?)",
            (note, balance, json.dumps(data))
        )
        conn.commit()

def get_dashboard_snapshots(limit=20, offset=0):
    """Dashboard anlık görüntülerini döndürür."""
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM dashboard_snapshots ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
        return [dict(row) for row in rows]

def get_system_state_value(key: str):
    """Sistem durumu anahtar değerini döndürür."""
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM system_state WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

def set_system_state_value(key: str, value: str):
    """Sistem durumu anahtar değerini ayarlar."""
    sql = """
        INSERT INTO system_state (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET
            value=excluded.value,
            updated_at=datetime('now')
    """
    with get_conn() as conn:
        conn.execute(sql, (key, value))
        conn.commit()

def get_pattern_memory(symbol: str = None, session: str = None, result: str = None):
    """Pattern hafızasını döndürür."""
    with get_conn() as conn:
        query = "SELECT * FROM pattern_memory WHERE 1=1"
        params = []
        if symbol:
            query += " AND symbol=?"
            params.append(symbol)
        if session:
            query += " AND session=?"
            params.append(session)
        if result:
            query += " AND result=?"
            params.append(result)
        query += " ORDER BY created_at DESC"
        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

def save_pattern_memory(memory: dict):
    """Pattern hafızasını kaydeder."""
    sql = """
        INSERT INTO pattern_memory (symbol, direction, session, result, net_pnl, hold_minutes, partial_exit)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """
    params = (
        memory['symbol'], memory['direction'], memory['session'], memory['result'],
        memory['net_pnl'], memory['hold_minutes'], memory['partial_exit']
    )
    with get_conn() as conn:
        conn.execute(sql, params)
        conn.commit()
