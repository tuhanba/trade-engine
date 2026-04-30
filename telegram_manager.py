"""
telegram_manager.py — AX Telegram entegrasyonu

Bölüm 1: Gönderici + kuyruk
Bölüm 2: Komut handler'ları
Bölüm 3: Bildirim fonksiyonları (spam filtreli)
"""

import logging
import threading
import time
from collections import deque
from datetime import datetime, timezone

import requests

import config

logger = logging.getLogger(__name__)

_BASE = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}"
_CHAT = config.TELEGRAM_CHAT_ID


# ═══════════════════════════════════════════════════════════
# BÖLÜM 1 — GÖNDERİCİ
# ═══════════════════════════════════════════════════════════

def _send_raw(text: str, parse_mode: str = "HTML") -> bool:
    """Tek deneme — 429 retry_after'ı yakala."""
    if not config.TELEGRAM_TOKEN or not config.TELEGRAM_CHAT_ID:
        return False
    try:
        resp = requests.post(
            f"{_BASE}/sendMessage",
            json={
                "chat_id":    _CHAT,
                "text":       text,
                "parse_mode": parse_mode,
            },
            timeout=8,
        )
        if resp.status_code == 200:
            return True
        if resp.status_code == 429:
            wait = resp.json().get("parameters", {}).get("retry_after", 5)
            logger.warning(f"[TG] Rate limit — {wait}s bekleniyor")
            time.sleep(wait)
            return False
        logger.warning(f"[TG] HTTP {resp.status_code}: {resp.text[:120]}")
    except requests.Timeout:
        logger.warning("[TG] Timeout")
    except Exception as e:
        logger.warning(f"[TG] Hata: {e}")
    return False


def _send_with_retry(text: str, parse_mode: str = "HTML", retries: int = 3):
    for attempt in range(1, retries + 1):
        if _send_raw(text, parse_mode):
            return
        if attempt < retries:
            time.sleep(2 ** attempt)


def _split_send(text: str, parse_mode: str = "HTML"):
    """4096 karakter limitini aş; gerekirse parçalara böl."""
    limit = 4096
    if len(text) <= limit:
        _send_with_retry(text, parse_mode)
        return
    parts = [text[i:i + limit] for i in range(0, len(text), limit)]
    for part in parts:
        _send_with_retry(part, parse_mode)
        time.sleep(0.3)


# Kuyruk — ana thread'i bloke etmez
class _Queue:
    def __init__(self):
        self._q      = deque()
        self._lock   = threading.Lock()
        self._event  = threading.Event()
        t = threading.Thread(target=self._worker, daemon=True)
        t.start()

    def push(self, text: str, parse_mode: str = "HTML"):
        with self._lock:
            self._q.append((text, parse_mode))
        self._event.set()

    def _worker(self):
        while True:
            self._event.wait()
            self._event.clear()
            while True:
                with self._lock:
                    if not self._q:
                        break
                    text, pm = self._q.popleft()
                _split_send(text, pm)
                time.sleep(0.1)


_q = _Queue()


def send(text: str, parse_mode: str = "HTML"):
    """Public send — kuyruğa ekle."""
    _q.push(text, parse_mode)


# ═══════════════════════════════════════════════════════════
# BÖLÜM 2 — KOMUT HANDLER'LARI
# ═══════════════════════════════════════════════════════════

def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M UTC")


def _cmd_status():
    from database import get_conn
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM system_state WHERE id=1")
    s = dict(c.fetchone() or {})
    c.execute("SELECT COUNT(*) FROM trades WHERE status='OPEN'")
    open_cnt = c.fetchone()[0]
    c.execute("SELECT paper_balance FROM paper_account WHERE id=1")
    bal_row = c.fetchone()
    conn.close()

    bal  = float(bal_row[0]) if bal_row else 0.0
    cb   = "🔴 AKTİF" if s.get("circuit_breaker_active") else "🟢 Kapalı"
    mode = s.get("execution_mode", config.EXECUTION_MODE).upper()
    stat = s.get("bot_status", "?").upper()

    send(
        f"🤖 <b>AX Bot Durumu</b>\n\n"
        f"Mod: <b>{mode}</b> | Durum: <b>{stat}</b>\n"
        f"Circuit Breaker: {cb}\n"
        f"Açık trade: {open_cnt}\n"
        f"Bakiye: <b>${bal:.2f}</b>\n"
        f"⏰ {_now_str()}"
    )


def _cmd_pause():
    from database import get_conn
    conn = get_conn()
    conn.execute(
        "UPDATE system_state SET bot_status='paused', updated_at=datetime('now') WHERE id=1"
    )
    conn.commit()
    conn.close()
    send("⏸ <b>Bot duraklatıldı.</b> Devam için /resume")


def _cmd_resume():
    from database import get_conn
    conn = get_conn()
    conn.execute(
        "UPDATE system_state SET bot_status='running', updated_at=datetime('now') WHERE id=1"
    )
    conn.commit()
    conn.close()
    send("▶️ <b>Bot devam ediyor.</b>")


def _cmd_finish():
    from database import get_conn, get_conn as _gc
    conn = get_conn()
    conn.execute(
        "UPDATE system_state SET bot_status='finish', updated_at=datetime('now') WHERE id=1"
    )
    conn.commit()
    conn.close()
    send("🏁 <b>Finish modu aktif.</b>\nAçık trade'ler kapanınca bot durur.")


def _cmd_balance():
    from database import get_conn
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT paper_balance FROM paper_account WHERE id=1")
    row = c.fetchone()
    conn.close()
    bal = float(row[0]) if row else 0.0
    send(f"💰 <b>Bakiye:</b> ${bal:.2f}")


def _cmd_trades():
    from database import get_conn
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT symbol, direction, entry, sl, tp1, status FROM trades "
        "WHERE status NOT IN ('CLOSED_WIN','CLOSED_LOSS','CLOSED_MANUAL','CLOSED_TRAIL','WIN','LOSS') "
        "ORDER BY id DESC LIMIT 10"
    )
    rows = [dict(r) for r in c.fetchall()]
    conn.close()

    if not rows:
        send("📭 Açık pozisyon yok.")
        return

    lines = ["📊 <b>Açık Pozisyonlar</b>\n"]
    for t in rows:
        lines.append(
            f"• {t['symbol']} {t['direction']} "
            f"@ {t['entry']} | SL:{t['sl']} | {t['status']}"
        )
    send("\n".join(lines))


def _cmd_ax():
    from database import get_conn
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT COUNT(*) total,
               SUM(CASE WHEN decision='ALLOW' THEN 1 ELSE 0 END) allow,
               SUM(CASE WHEN decision='VETO'  THEN 1 ELSE 0 END) veto,
               SUM(CASE WHEN decision='WATCH' THEN 1 ELSE 0 END) watch
        FROM signal_candidates
        WHERE created_at >= date('now')
    """)
    r = dict(c.fetchone() or {})
    c.execute(
        "SELECT veto_reason, COUNT(*) n FROM signal_candidates "
        "WHERE veto_reason IS NOT NULL AND created_at >= date('now') "
        "GROUP BY veto_reason ORDER BY n DESC LIMIT 5"
    )
    vetos = c.fetchall()
    conn.close()

    lines = [
        "🧠 <b>AX Bugün</b>\n",
        f"Toplam sinyal: {r.get('total', 0)}",
        f"ALLOW: {r.get('allow', 0)} | VETO: {r.get('veto', 0)} | WATCH: {r.get('watch', 0)}",
    ]
    if vetos:
        lines.append("\n<b>Top Veto Sebepleri:</b>")
        for v in vetos:
            lines.append(f"  • {v[0] or '?'}: {v[1]}x")
    send("\n".join(lines))


def _cmd_report():
    from database import get_conn
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT COUNT(*) total,
               SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) wins,
               ROUND(SUM(net_pnl),4) pnl,
               ROUND(AVG(r_multiple),3) avg_r
        FROM trades
        WHERE status NOT IN ('OPEN','TP1_HIT','TP2_HIT','RUNNER_ACTIVE')
          AND close_time >= date('now')
    """)
    r = dict(c.fetchone() or {})
    c.execute("SELECT paper_balance FROM paper_account WHERE id=1")
    bal = float((c.fetchone() or [250])[0])
    conn.close()

    total = r.get("total") or 0
    wins  = r.get("wins") or 0
    wr    = round(wins / total * 100, 1) if total else 0

    send(
        f"📈 <b>Günlük Rapor</b> — {datetime.now(timezone.utc).strftime('%d %b')}\n\n"
        f"Trade: {total} | Win: {wins} | WR: {wr}%\n"
        f"PnL: ${r.get('pnl') or 0:+.2f} | Avg R: {r.get('avg_r') or 0}\n"
        f"Bakiye: ${bal:.2f}"
    )


def _cmd_calendar():
    from database import get_conn
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT date, total_pnl, total_trades, win_rate
        FROM daily_summary
        ORDER BY date DESC LIMIT 14
    """)
    rows = c.fetchall()
    conn.close()

    if not rows:
        send("📅 Henüz günlük özet yok.")
        return

    lines = ["📅 <b>Son 14 Gün</b>\n"]
    for r in rows:
        emoji = "🟢" if (r[1] or 0) >= 0 else "🔴"
        lines.append(
            f"{emoji} {r[0]} | ${r[1] or 0:+.2f} "
            f"| {r[2] or 0} trade | WR:{round((r[3] or 0)*100)}%"
        )
    send("\n".join(lines))


def _cmd_weekly():
    from database import get_conn
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT week_start, total_pnl, total_trades, win_rate, profit_factor
        FROM weekly_summary ORDER BY week_start DESC LIMIT 4
    """)
    rows = c.fetchall()
    conn.close()

    if not rows:
        send("📊 Henüz haftalık özet yok.")
        return

    lines = ["📊 <b>Son 4 Hafta</b>\n"]
    for r in rows:
        emoji = "🟢" if (r[1] or 0) >= 0 else "🔴"
        lines.append(
            f"{emoji} {r[0]} | ${r[1] or 0:+.2f} "
            f"| {r[2] or 0} trade | PF:{r[4] or 0}"
        )
    send("\n".join(lines))


def _cmd_coin(symbol: str):
    from coin_library import get_coin_profile
    from coin_library import is_in_cooldown
    if not symbol:
        send("Kullanım: /coin BTCUSDT")
        return
    p = get_coin_profile(symbol.upper())
    cd = "🔴 Cooldown" if is_in_cooldown(symbol.upper()) else "🟢 Aktif"
    send(
        f"🪙 <b>{symbol.upper()}</b>\n\n"
        f"Trade: {p.get('trade_count',0)} | WR: {round((p.get('win_rate',0))*100,1)}%\n"
        f"Avg R: {p.get('avg_r',0)} | PF: {p.get('profit_factor',0)}\n"
        f"Fakeout: {round((p.get('fakeout_rate',0))*100,1)}% | Danger: {p.get('danger_score',0)}\n"
        f"Volatilite: {p.get('volatility_profile','?')}\n"
        f"Durum: {cd}"
    )


def _cmd_mode():
    send(
        f"⚙️ <b>Mod</b>\n"
        f"AX_MODE: {config.AX_MODE}\n"
        f"EXECUTION_MODE: {config.EXECUTION_MODE}"
    )


def _cmd_signal():
    from database import get_conn
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT symbol, direction, rr, expected_mfe_r, decision, score
        FROM signal_candidates
        ORDER BY id DESC LIMIT 5
    """)
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    if not rows:
        send("Henüz sinyal yok.")
        return
    lines = ["📡 <b>Son 5 Sinyal</b>\n"]
    for r in rows:
        icon = "✅" if r["decision"] == "ALLOW" else "❌"
        lines.append(
            f"{icon} {r['symbol']} {r['direction']} "
            f"RR:{r['rr']} MFE:{r['expected_mfe_r']} skor:{r['score']}"
        )
    send("\n".join(lines))


def _cmd_help():
    send(
        "📋 <b>AX Komutları</b>\n\n"
        "/status — Bot durumu\n"
        "/pause — Duraklat\n"
        "/resume — Devam ettir\n"
        "/finish — Finish modu\n"
        "/balance — Bakiye\n"
        "/trades — Açık pozisyonlar\n"
        "/ax — AX sinyal özeti\n"
        "/report — Günlük rapor\n"
        "/calendar — Son 14 gün\n"
        "/weekly — Son 4 hafta\n"
        "/coin BTCUSDT — Coin profili\n"
        "/mode — Aktif mod\n"
        "/signal — Son sinyaller\n"
        "/help — Bu liste"
    )


# ═══════════════════════════════════════════════════════════
# BÖLÜM 3 — BİLDİRİM FONKSİYONLARI (spam filtreli)
# ═══════════════════════════════════════════════════════════

def _get_balance() -> float:
    try:
        from database import get_conn
        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT paper_balance FROM paper_account WHERE id=1")
        row = c.fetchone()
        conn.close()
        return float(row[0]) if row else 0.0
    except Exception:
        return 0.0


def notify_trade_open(trade: dict, candidate: dict = None):
    env    = (trade.get("environment") or "paper").upper()
    sym    = trade.get("symbol", "?")
    direc  = trade.get("direction", "?")
    entry  = float(trade.get("entry") or 0)
    sl     = float(trade.get("sl") or 0)
    tp1    = float(trade.get("tp1") or 0)
    tp2    = float(trade.get("tp2") or 0)
    bal    = _get_balance()
    icon   = "🟢" if direc == "LONG" else "🔴"

    rr_str    = ""
    score_str = ""
    if candidate:
        rr    = candidate.get("rr") or 0
        score = candidate.get("score") or 0
        rr_str    = f"\n📐 RR: <b>{rr:.2f}</b> | Skor: <b>{score}</b>"
        score_str = f" | Skor: {score}"

    send(
        f"✏️ [{env}] {icon} <b>{sym} {direc} AÇILDI</b>\n"
        f"\n"
        f"📍 Giriş: <code>{entry:.5g}</code>\n"
        f"🛡 SL: <code>{sl:.5g}</code>  🎯 TP1: <code>{tp1:.5g}</code>\n"
        f"🎯 TP2: <code>{tp2:.5g}</code>"
        f"{rr_str}\n"
        f"💰 Bakiye: <b>${bal:.2f}</b>"
    )


def notify_tp_hit(trade_id: int, level: str, price: float, partial_pnl: float):
    bal = _get_balance()
    icon = "🎯" if level == "TP1" else "🏆"
    send(
        f"{icon} <b>{level} HİT</b> — #{trade_id}\n"
        f"Fiyat: <code>{price:.5g}</code>\n"
        f"Kısmi PnL: <b>${partial_pnl:+.4f}</b>\n"
        f"💰 Bakiye: <b>${bal:.2f}</b>"
    )


def notify_trade_close(trade: dict):
    pnl    = float(trade.get("net_pnl") or 0)
    r      = float(trade.get("r_multiple") or 0)
    dur    = float(trade.get("duration_min") or 0)
    status = trade.get("status", "?")
    sym    = trade.get("symbol", "?")
    direc  = trade.get("direction", "?")
    entry  = float(trade.get("entry") or 0)
    exit_p = float(trade.get("exit_price") or 0)
    bal    = _get_balance()

    h, m = divmod(int(dur), 60)
    dur_str = f"{h}s {m}dk" if h else f"{m}dk"

    if pnl > 0:
        emoji  = "✅"
        result = "WIN"
    else:
        emoji  = "❌"
        result = "LOSS"

    reason_map = {
        "CLOSED_TRAIL": "🏃 Trailing stop",
        "CLOSED_LOSS":  "🛡 SL",
        "CLOSED_MANUAL":"🖐 Manuel",
    }
    close_reason = reason_map.get(status, status)

    send(
        f"{emoji} <b>{result}</b> — {sym} {direc} #{trade.get('id')}\n"
        f"\n"
        f"📍 <code>{entry:.5g}</code> → <code>{exit_p:.5g}</code>\n"
        f"💵 Net PnL: <b>${pnl:+.4f}</b>  R: <b>{r:+.3f}</b>\n"
        f"⏱ Süre: {dur_str}  |  {close_reason}\n"
        f"💰 Bakiye: <b>${bal:.2f}</b>"
    )


def notify_circuit_breaker(until: str):
    send(
        f"🔴 <b>CIRCUIT BREAKER AKTİF</b>\n"
        f"{config.CIRCUIT_BREAKER_LOSSES} ardışık kayıp\n"
        f"Bitiş: {until}"
    )


def notify_daily_report():
    _cmd_report()


def notify_weekly_report():
    _cmd_weekly()


def notify_critical(msg: str):
    send(f"🚨 <b>KRİTİK HATA</b>\n{msg}")


# ═══════════════════════════════════════════════════════════
# POLLING
# ═══════════════════════════════════════════════════════════

_COMMANDS = {
    "status":   lambda _: _cmd_status(),
    "pause":    lambda _: _cmd_pause(),
    "resume":   lambda _: _cmd_resume(),
    "finish":   lambda _: _cmd_finish(),
    "balance":  lambda _: _cmd_balance(),
    "trades":   lambda _: _cmd_trades(),
    "ax":       lambda _: _cmd_ax(),
    "report":   lambda _: _cmd_report(),
    "calendar": lambda _: _cmd_calendar(),
    "weekly":   lambda _: _cmd_weekly(),
    "coin":     lambda args: _cmd_coin(args.strip()),
    "mode":     lambda _: _cmd_mode(),
    "signal":   lambda _: _cmd_signal(),
    "execute":  lambda _: send("execute modu: /live veya /paper ile değiştir"),
    "paper":    lambda _: send("PAPER mod aktif (config'den değiştirin)."),
    "live":     lambda _: send("⚠️ LIVE mod — config.py üzerinden aktifleştirin."),
    "help":     lambda _: _cmd_help(),
}

_offset = 0


def _fetch_once():
    global _offset
    if not config.TELEGRAM_TOKEN or not config.TELEGRAM_CHAT_ID:
        return
    try:
        resp = requests.get(
            f"{_BASE}/getUpdates",
            params={"offset": _offset, "timeout": 10, "limit": 10},
            timeout=15,
        )
        if resp.status_code != 200:
            return
        for upd in resp.json().get("result", []):
            _offset = upd["update_id"] + 1
            msg = upd.get("message") or upd.get("edited_message")
            if not msg:
                continue
            if str(msg.get("chat", {}).get("id", "")) != str(_CHAT):
                continue
            text = msg.get("text", "").strip()
            if not text.startswith("/"):
                continue
            parts = text.split(None, 1)
            cmd   = parts[0].lstrip("/").split("@")[0].lower()
            args  = parts[1] if len(parts) > 1 else ""
            handler = _COMMANDS.get(cmd)
            if handler:
                try:
                    handler(args)
                except Exception as e:
                    logger.error(f"[TG] komut hatası /{cmd}: {e}")
            else:
                send(f"❓ Bilinmeyen komut: /{cmd} — /help")
    except Exception as e:
        logger.debug(f"[TG] poll hata: {e}")


def start_polling():
    """Polling thread'i başlat."""
    def _loop():
        while True:
            _fetch_once()
            time.sleep(1)
    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    logger.info("[TG] Polling başladı.")
