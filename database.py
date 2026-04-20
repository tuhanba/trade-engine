import sqlite3,os
from datetime import datetime,timezone

DB_PATH = os.path.join(os.path.dirname(__file__),"trading.db")

def get_conn():
    return sqlite3.connect(DB_PATH)

def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT,
        direction TEXT,
        entry REAL,
        exit_price REAL,
        sl REAL,
        tp REAL,
        qty REAL,
        leverage INTEGER DEFAULT 10,
        pnl_usdt REAL,
        pnl_pct REAL,
        r_multiple REAL,
        risk_usdt REAL,
        status TEXT DEFAULT 'OPEN',
        open_time TEXT,
        close_time TEXT,
        duration_min REAL,
        rsi5 REAL,
        rsi1 REAL,
        vol_ratio REAL,
        change_24h REAL,
        volume_m REAL,
        params_version INTEGER DEFAULT 1,
        result TEXT,
        net_pnl REAL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS params (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        version INTEGER,
        sl_atr_mult REAL DEFAULT 1.2,
        tp_atr_mult REAL DEFAULT 2.0,
        rsi5_min REAL DEFAULT 35,
        rsi5_max REAL DEFAULT 75,
        rsi1_min REAL DEFAULT 35,
        rsi1_max REAL DEFAULT 72,
        vol_ratio_min REAL DEFAULT 1.2,
        min_volume_m REAL DEFAULT 10.0,
        min_change_pct REAL DEFAULT 2.0,
        risk_pct REAL DEFAULT 1.5,
        updated_at TEXT,
        ai_reason TEXT
    );
    CREATE TABLE IF NOT EXISTS ai_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT,
        trades_analyzed INTEGER,
        win_rate REAL,
        avg_rr REAL,
        insight TEXT,
        changes TEXT
    );
    CREATE TABLE IF NOT EXISTS pattern_memory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT,
        direction TEXT,
        result TEXT,
        net_pnl REAL DEFAULT 0,
        adx REAL,
        rv REAL,
        rsi5 REAL,
        rsi1 REAL,
        funding_favorable INTEGER DEFAULT 1,
        bb_width_pct REAL,
        ob_ratio REAL,
        volume_m REAL,
        btc_trend TEXT DEFAULT 'NEUTRAL',
        session TEXT DEFAULT 'OFF',
        hold_minutes REAL DEFAULT 0,
        partial_exit INTEGER DEFAULT 0,
        bb_width_chg REAL DEFAULT 0,
        momentum_3c REAL DEFAULT 0,
        prev_result TEXT DEFAULT 'NONE',
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS paper_account (
        id INTEGER PRIMARY KEY DEFAULT 1,
        paper_balance REAL DEFAULT 250.0,
        updated_at TEXT
    );
    CREATE TABLE IF NOT EXISTS bot_control (
        id          INTEGER PRIMARY KEY DEFAULT 1,
        paused      INTEGER DEFAULT 0,
        finish_mode INTEGER DEFAULT 0,
        updated_at  TEXT DEFAULT (datetime('now')),
        updated_by  TEXT DEFAULT 'system'
    );
    CREATE TABLE IF NOT EXISTS portfolio_snapshots (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        balance      REAL,
        peak_balance REAL,
        drawdown_pct REAL,
        daily_pnl    REAL,
        win_streak   INTEGER DEFAULT 0,
        loss_streak  INTEGER DEFAULT 0,
        total_trades INTEGER DEFAULT 0,
        created_at   TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS rejected_signals (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol      TEXT,
        direction   TEXT,
        reason      TEXT,
        adx15       REAL,
        bb_width    REAL,
        rsi5        REAL,
        rsi1        REAL,
        rv          REAL,
        funding     REAL,
        atr5        REAL,
        volume_m    REAL,
        btc_trend   TEXT,
        trend_4h    TEXT,
        price       REAL,
        rejected_at TEXT DEFAULT (datetime('now')),
        labeled     INTEGER DEFAULT 0,
        outcome_5m  REAL,
        outcome_15m REAL,
        outcome_60m REAL,
        would_win   INTEGER
    );
    INSERT OR IGNORE INTO paper_account (id, paper_balance, updated_at)
    VALUES (1, 250.0, datetime('now'));
    INSERT OR IGNORE INTO bot_control (id, paused, finish_mode)
    VALUES (1, 0, 0);
    INSERT OR IGNORE INTO params (version,updated_at,ai_reason)
    VALUES (1,datetime('now'),'Initial params');
    """)
    conn.commit()
    conn.close()

def log_rejection(symbol: str, reason: str, features: dict):
    try:
        conn = get_conn()
        conn.execute("""
        INSERT INTO rejected_signals
          (symbol, direction, reason, adx15, bb_width, rsi5, rsi1, rv,
           funding, atr5, volume_m, btc_trend, trend_4h, price)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            symbol,
            features.get("direction"),
            reason,
            features.get("adx15"),
            features.get("bb_width"),
            features.get("rsi5"),
            features.get("rsi1"),
            features.get("rv"),
            features.get("funding"),
            features.get("atr5"),
            features.get("volume_m"),
            features.get("btc_trend"),
            features.get("trend_4h"),
            features.get("price"),
        ))
        conn.commit()
        conn.close()
    except Exception:
        pass

def get_bot_control():
    conn = get_conn()
    row = conn.execute("SELECT paused, finish_mode FROM bot_control WHERE id=1").fetchone()
    conn.close()
    return {"paused": bool(row[0]), "finish_mode": bool(row[1])} if row else {"paused": False, "finish_mode": False}

def set_bot_control(paused=None, finish_mode=None, updated_by="system"):
    conn = get_conn()
    if paused is not None and finish_mode is not None:
        conn.execute("UPDATE bot_control SET paused=?, finish_mode=?, updated_at=datetime('now'), updated_by=? WHERE id=1",
                     (int(paused), int(finish_mode), updated_by))
    elif paused is not None:
        conn.execute("UPDATE bot_control SET paused=?, updated_at=datetime('now'), updated_by=? WHERE id=1",
                     (int(paused), updated_by))
    elif finish_mode is not None:
        conn.execute("UPDATE bot_control SET finish_mode=?, updated_at=datetime('now'), updated_by=? WHERE id=1",
                     (int(finish_mode), updated_by))
    conn.commit()
    conn.close()

def save_trade(trade: dict) -> int:
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
    INSERT INTO trades (symbol,direction,entry,sl,tp,qty,leverage,risk_usdt,
    rsi5,rsi1,vol_ratio,change_24h,volume_m,open_time,status,params_version)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        trade["symbol"], trade["direction"], trade["entry"],
        trade["sl"], trade["tp"], trade["qty"], trade.get("leverage",10),
        trade["risk_usdt"], trade.get("rsi5",0), trade.get("rsi1",0),
        trade.get("vol_ratio",0), trade.get("change_24h",0),
        trade.get("volume_m",0),
        datetime.now(timezone.utc).isoformat(),
        "OPEN", trade.get("params_version",1)
    ))
    tid = c.lastrowid
    conn.commit()
    conn.close()
    return tid

def close_trade(trade_id: int, exit_price: float, status: str = "WIN", net_pnl: float = None):
    """
    Trade'i kapat. result ve net_pnl alanlarını da günceller.
    net_pnl: komisyon düşülmüş gerçek net kazanç/kayıp (scalp_bot'tan gelir).
             Verilmezse pnl_usdt'den hesaplanır (komisyon dahil değil).
    """
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT entry,sl,qty,direction,risk_usdt,open_time FROM trades WHERE id=?", (trade_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return
    entry, sl, qty, direction, risk_usdt, open_time = row
    sl_dist = abs(entry - sl)
    if direction == "LONG":
        pnl = (exit_price - entry) * qty
    else:
        pnl = (entry - exit_price) * qty
    r_mult = pnl / (sl_dist * qty + 1e-10)
    pnl_pct = pnl / (entry * qty + 1e-10) * 100
    # status'u otomatik belirle
    if status not in ("WIN","LOSS","MANUAL"):
        status = "WIN" if pnl > 0 else "LOSS"
    # result = status ile aynı
    result = status
    # net_pnl verilmemişse pnl_usdt kullan
    if net_pnl is None:
        net_pnl = round(pnl, 4)
    try:
        ot = datetime.fromisoformat(open_time.replace("Z","+00:00"))
        dur = (datetime.now(timezone.utc) - ot).total_seconds() / 60
    except:
        dur = 0
    c.execute("""
    UPDATE trades SET exit_price=?,pnl_usdt=?,pnl_pct=?,r_multiple=?,
    status=?,result=?,net_pnl=?,close_time=?,duration_min=? WHERE id=?
    """, (exit_price, round(pnl,4), round(pnl_pct,4),
          round(r_mult,4), status, result, round(net_pnl,4),
          datetime.now(timezone.utc).isoformat(),
          round(dur,1), trade_id))
    conn.commit()
    conn.close()

def get_trades(limit=100, status=None):
    conn = get_conn()
    c = conn.cursor()
    if status == "OPEN":
        c.execute("SELECT * FROM trades WHERE status=? ORDER BY id DESC LIMIT ?", ("OPEN", limit))
    elif status:
        # Kapalı tradeler: WIN, LOSS, MANUAL, CLOSED (eski veri uyumluluğu)
        c.execute("""SELECT * FROM trades
                     WHERE status IN ('WIN','LOSS','MANUAL','CLOSED')
                     ORDER BY id DESC LIMIT ?""", (limit,))
    else:
        c.execute("SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,))
    rows = c.fetchall()
    cols = [d[0] for d in c.description]
    conn.close()
    return [dict(zip(cols, r)) for r in rows]

def get_stats():
    conn = get_conn()
    c = conn.cursor()
    # status alanına göre WIN/LOSS say (result NULL olsa bile çalışır)
    c.execute("""
    SELECT COUNT(*) total,
           SUM(CASE WHEN status='WIN' THEN 1 ELSE 0 END) wins,
           SUM(CASE WHEN status='LOSS' THEN 1 ELSE 0 END) losses,
           ROUND(AVG(net_pnl),4) avg_pnl,
           ROUND(SUM(net_pnl),4) total_pnl,
           ROUND(AVG(r_multiple),4) avg_rr,
           ROUND(AVG(duration_min),1) avg_dur
    FROM trades WHERE status IN ('WIN','LOSS','MANUAL','CLOSED')
    """)
    row = c.fetchone()
    cols = [d[0] for d in c.description]
    stats = dict(zip(cols, row))
    stats["wins"]   = stats["wins"]   or 0
    stats["losses"] = stats["losses"] or 0
    stats["total"]  = stats["total"]  or 0
    stats["avg_pnl"]   = stats["avg_pnl"]   or 0
    stats["total_pnl"] = stats["total_pnl"] or 0
    stats["avg_rr"]    = stats["avg_rr"]    or 0
    stats["avg_dur"]   = stats["avg_dur"]   or 0
    stats["win_rate"] = round(stats["wins"]/(stats["total"]+1e-10)*100,1) if stats["total"] else 0
    # Profit factor — status alanına göre, net_pnl kullan
    c.execute("""
    SELECT ROUND(SUM(CASE WHEN status='WIN' THEN net_pnl ELSE 0 END),4),
           ROUND(ABS(SUM(CASE WHEN status='LOSS' THEN net_pnl ELSE 0 END)),4)
    FROM trades WHERE status IN ('WIN','LOSS','MANUAL','CLOSED')
    """)
    pf_row = c.fetchone()
    gross_profit = pf_row[0] or 0
    gross_loss   = pf_row[1] or 0
    if gross_loss > 0:
        stats["profit_factor"] = round(gross_profit / gross_loss, 2)
    elif gross_profit > 0:
        stats["profit_factor"] = round(gross_profit, 2)  # sadece kazanç var
    else:
        stats["profit_factor"] = 0.0
    # Best / worst / avg_win / avg_loss — net_pnl kullan
    c.execute("""
    SELECT MAX(net_pnl), MIN(net_pnl),
           AVG(CASE WHEN status='WIN' THEN net_pnl END),
           AVG(CASE WHEN status='LOSS' THEN net_pnl END)
    FROM trades WHERE status IN ('WIN','LOSS','MANUAL','CLOSED')
    """)
    perf_row = c.fetchone()
    stats["best_trade"]       = round(perf_row[0] or 0, 4)
    stats["worst_trade"]      = round(perf_row[1] or 0, 4)
    stats["avg_win"]          = round(perf_row[2] or 0, 4)
    stats["avg_loss"]         = round(perf_row[3] or 0, 4)
    stats["avg_duration_min"] = stats.get("avg_dur", 0)
    # Max drawdown (kümülatif net_pnl minimumu)
    c.execute("SELECT net_pnl FROM trades WHERE status IN ('WIN','LOSS','MANUAL','CLOSED') ORDER BY id")
    pnls = [r[0] or 0 for r in c.fetchall()]
    cum = 0; peak = 0; max_dd = 0
    for p in pnls:
        cum += p
        if cum > peak: peak = cum
        dd = cum - peak
        if dd < max_dd: max_dd = dd
    stats["max_drawdown"] = round(max_dd, 4)
    # Açık trade sayısı
    c.execute("SELECT COUNT(*) FROM trades WHERE status='OPEN'")
    stats["open_count"] = c.fetchone()[0]
    c.execute("SELECT * FROM params ORDER BY version DESC LIMIT 1")
    p = c.fetchone()
    pc = [d[0] for d in c.description]
    stats["params"] = dict(zip(pc, p)) if p else {}
    c.execute("SELECT * FROM ai_logs ORDER BY id DESC LIMIT 1")
    a = c.fetchone()
    ac = [d[0] for d in c.description]
    stats["last_ai"] = dict(zip(ac, a)) if a else {}
    conn.close()
    return stats

def get_current_params():
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM params ORDER BY version DESC LIMIT 1")
    row = c.fetchone()
    cols = [d[0] for d in c.description]
    conn.close()
    return dict(zip(cols, row)) if row else {}

def save_params(params: dict, reason: str):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT MAX(version) FROM params")
    v = (c.fetchone()[0] or 0) + 1
    c.execute("""
    INSERT INTO params (version,sl_atr_mult,tp_atr_mult,rsi5_min,rsi5_max,
    rsi1_min,rsi1_max,vol_ratio_min,min_volume_m,min_change_pct,risk_pct,
    updated_at,ai_reason)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (v, params["sl_atr_mult"], params["tp_atr_mult"],
          params["rsi5_min"], params["rsi5_max"],
          params["rsi1_min"], params["rsi1_max"],
          params["vol_ratio_min"], params["min_volume_m"],
          params["min_change_pct"], params["risk_pct"],
          datetime.now(timezone.utc).isoformat(), reason))
    conn.commit()
    conn.close()
    return v

def save_ai_log(log: dict):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
    INSERT INTO ai_logs (created_at,trades_analyzed,win_rate,avg_rr,insight,changes)
    VALUES (?,?,?,?,?,?)
    """, (datetime.now(timezone.utc).isoformat(),
          log["trades_analyzed"], log["win_rate"],
          log["avg_rr"], log["insight"], log["changes"]))
    conn.commit()
    conn.close()

def record_portfolio_snapshot():
    try:
        conn = get_conn()
        c = conn.cursor()
        # Current balance
        row = c.execute("SELECT paper_balance FROM paper_account WHERE id=1").fetchone()
        balance = row[0] if row else 250.0
        # Peak balance from snapshots or starting balance
        peak_row = c.execute("SELECT MAX(balance) FROM portfolio_snapshots").fetchone()
        peak = max(peak_row[0] or 250.0, balance, 250.0)
        drawdown_pct = round((balance - peak) / peak * 100, 2) if peak > 0 else 0.0
        # Today's PnL (UTC)
        today_row = c.execute("""
            SELECT ROUND(SUM(net_pnl), 4) FROM trades
            WHERE status IN ('WIN','LOSS','MANUAL')
            AND date(close_time) = date('now')
        """).fetchone()
        daily_pnl = today_row[0] or 0.0
        # Streak
        recent = c.execute("""
            SELECT status FROM trades
            WHERE status IN ('WIN','LOSS')
            ORDER BY id DESC LIMIT 20
        """).fetchall()
        win_streak = 0; loss_streak = 0
        for r in recent:
            if r[0] == 'WIN':
                if loss_streak == 0:
                    win_streak += 1
                else:
                    break
            else:
                if win_streak == 0:
                    loss_streak += 1
                else:
                    break
        total_row = c.execute(
            "SELECT COUNT(*) FROM trades WHERE status IN ('WIN','LOSS','MANUAL')"
        ).fetchone()
        total = total_row[0] or 0
        c.execute("""
            INSERT INTO portfolio_snapshots
              (balance, peak_balance, drawdown_pct, daily_pnl, win_streak, loss_streak, total_trades)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (balance, peak, drawdown_pct, daily_pnl, win_streak, loss_streak, total))
        conn.commit()
        conn.close()
    except Exception:
        pass

def get_portfolio_stats() -> dict:
    conn = get_conn()
    c = conn.cursor()
    # Latest snapshot
    snap = c.execute(
        "SELECT * FROM portfolio_snapshots ORDER BY id DESC LIMIT 1"
    ).fetchone()
    cols = [d[0] for d in c.description]
    snap_d = dict(zip(cols, snap)) if snap else {}
    # Balance history (last 50 for sparkline)
    hist = c.execute(
        "SELECT balance, created_at FROM portfolio_snapshots ORDER BY id DESC LIMIT 50"
    ).fetchall()
    history = [{"balance": r[0], "at": r[1]} for r in reversed(hist)]
    conn.close()
    return {
        "balance":      snap_d.get("balance", 250.0),
        "peak_balance": snap_d.get("peak_balance", 250.0),
        "drawdown_pct": snap_d.get("drawdown_pct", 0.0),
        "daily_pnl":    snap_d.get("daily_pnl", 0.0),
        "win_streak":   snap_d.get("win_streak", 0),
        "loss_streak":  snap_d.get("loss_streak", 0),
        "total_trades": snap_d.get("total_trades", 0),
        "history":      history,
    }
