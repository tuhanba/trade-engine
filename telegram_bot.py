"""
telegram_bot.py — AURVEX.Ai Telegram Komut Botu v2.0
=====================================================
Komutlar:
  /status    → bot, dashboard, DB, Binance bağlantısı, son sinyal
  /live      → aktif trade/sinyal listesi
  /signals   → son scalp sinyalleri (DB'den)
  /watchlist → watchlist'e düşen ama açılmayan sinyaller
  /stats     → günlük/haftalık winrate, PnL, sinyal sayısı
  /last      → son kapanan trade
  /risk      → aktif risk ayarları
  /health    → servis sağlık kontrolü
  /restart_info → hangi servislerin aktif olduğunu göster
  /help      → tüm komutları açıkla

Veri kaynağı: database.py + config.py (dashboard ile aynı)
"""
import os
import time
import logging
import threading
import subprocess
import requests
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [TelegramBot] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# urllib3 / requests timeout loglarını sustur (long-poll normal davranışı)
logging.getLogger("urllib3").setLevel(logging.CRITICAL)
logging.getLogger("urllib3.connectionpool").setLevel(logging.CRITICAL)
logging.getLogger("requests").setLevel(logging.CRITICAL)

try:
    from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
except ImportError:
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

POLL_TIMEOUT = 25          # getUpdates long-poll süresi (saniye)
API_TIMEOUT  = 20          # sendMessage ve diger API cagrilari icin timeout
POLL_CONNECT_TIMEOUT = 10  # getUpdates baglanti timeout
_offset = 0
_running = False

# ─────────────────────────────────────────────────────────────────────────────
# TELEGRAM API
# ─────────────────────────────────────────────────────────────────────────────

def _api(method: str, payload: dict = None, is_poll: bool = False) -> dict:
    """Telegram API çağrısı. is_poll=True ise long-poll timeout kullanır."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"
    if is_poll:
        # getUpdates: connect_timeout + read_timeout (POLL_TIMEOUT + buffer)
        timeout = (POLL_CONNECT_TIMEOUT, POLL_TIMEOUT + 5)
    else:
        timeout = API_TIMEOUT
    try:
        r = requests.post(url, json=payload or {}, timeout=timeout)
        return r.json()
    except requests.exceptions.ReadTimeout:
        # Long-poll normal timeout — hata değil, sadece yeni mesaj yok
        if is_poll:
            return {"result": []}
        logger.debug(f"Telegram API timeout: {method}")
        return {}
    except requests.exceptions.ConnectTimeout:
        logger.debug(f"Telegram bağlantı timeout: {method}")
        return {"result": []} if is_poll else {}
    except requests.exceptions.ConnectionError:
        # Geçici ağ kesintisi — sessizce atla
        return {"result": []} if is_poll else {}
    except Exception as e:
        # Gerçek beklenmedik hata — sadece bunu logla
        if "timed out" not in str(e).lower() and "timeout" not in str(e).lower():
            logger.error(f"Telegram API hatası ({method}): {e}")
        return {"result": []} if is_poll else {}


def _send(chat_id, text: str):
    """HTML parse_mode ile mesaj gönder."""
    if not text:
        return
    # Telegram mesaj limiti 4096 karakter
    for chunk in [text[i:i+4000] for i in range(0, len(text), 4000)]:
        _api("sendMessage", {
            "chat_id":    chat_id,
            "text":       chunk,
            "parse_mode": "HTML",
        })
        time.sleep(0.3)


def _get_updates():
    global _offset
    result = _api("getUpdates", {
        "offset":  _offset,
        "timeout": POLL_TIMEOUT,
        "allowed_updates": ["message"],
    }, is_poll=True)
    return result.get("result", [])

# ─────────────────────────────────────────────────────────────────────────────
# VERİ YARDIMCILARI (aynı DB kaynağı)
# ─────────────────────────────────────────────────────────────────────────────

def _db():
    from database import get_conn
    return get_conn()


def _safe(val, fmt=None, fallback="—"):
    if val is None or val == "":
        return fallback
    if fmt:
        try:
            return fmt.format(val)
        except Exception:
            return str(val)
    return str(val)


def _fmt_trade(t: dict, show_pnl=False) -> str:
    dir_emoji = "📈" if t.get("direction") == "LONG" else "📉"
    result_emoji = ""
    if show_pnl:
        result = t.get("result") or ("WIN" if (t.get("net_pnl") or 0) > 0 else "LOSS")
        result_emoji = " 🟢 WIN" if result == "WIN" else " 🔴 LOSS"
    lines = [
        f"{dir_emoji} <b>{t.get('symbol','?')}</b> {t.get('direction','?')} "
        f"{_safe(t.get('leverage',10))}x{result_emoji}",
        f"  Giriş:  <code>{_safe(t.get('entry'), '{:.6f}')}</code>",
        f"  Stop:   <code>{_safe(t.get('sl'), '{:.6f}')}</code>",
        f"  TP1:    <code>{_safe(t.get('tp1'), '{:.6f}')}</code>",
    ]
    if t.get("tp2"):
        lines.append(f"  TP2:    <code>{_safe(t.get('tp2'), '{:.6f}')}</code>")
    if t.get("tp3"):
        lines.append(f"  TP3:    <code>{_safe(t.get('tp3'), '{:.6f}')}</code>")
    if t.get("current_price"):
        lines.append(f"  Fiyat:  <code>{_safe(t.get('current_price'), '{:.6f}')}</code>")
    if show_pnl and t.get("net_pnl") is not None:
        pnl = t.get("net_pnl", 0)
        lines.append(f"  PnL:    <b>{pnl:+.3f}$</b>")
    if t.get("close_reason"):
        lines.append(f"  Neden:  {t.get('close_reason')}")
    lines.append(f"  Durum:  {t.get('status','?')} | Kalite: {t.get('setup_quality','?')}")
    return "\n".join(lines)

# ─────────────────────────────────────────────────────────────────────────────
# KOMUT İŞLEYİCİLER
# ─────────────────────────────────────────────────────────────────────────────

def cmd_status(chat_id):
    try:
        from config import TELEGRAM_BOT_TOKEN as TBT
        # DB
        with _db() as conn:
            open_count = conn.execute(
                "SELECT COUNT(*) FROM trades WHERE status NOT IN "
                "('closed','closed_win','closed_loss','sl','trail','tp3','timeout')"
            ).fetchone()[0]
            last_sig = conn.execute(
                "SELECT created_at FROM signal_candidates ORDER BY id DESC LIMIT 1"
            ).fetchone()
            balance_row = conn.execute(
                "SELECT balance FROM paper_account WHERE id=1"
            ).fetchone()
        last_sig_time = last_sig[0] if last_sig else "—"
        balance = float(balance_row[0]) if balance_row else 0.0
        # Binance
        try:
            r = requests.get("https://fapi.binance.com/fapi/v1/ping", timeout=5)
            binance = "✅ OK" if r.status_code == 200 else f"❌ HTTP {r.status_code}"
        except Exception:
            binance = "❌ Bağlanamadı"
        # Telegram
        try:
            r2 = requests.get(f"https://api.telegram.org/bot{TBT}/getMe", timeout=5)
            tg_status = "✅ OK" if r2.status_code == 200 else f"❌ HTTP {r2.status_code}"
        except Exception:
            tg_status = "❌ Bağlanamadı"
        # Dashboard
        try:
            r3 = requests.get("http://localhost:5000/api/health", timeout=5)
            dash = "✅ OK" if r3.status_code == 200 else f"❌ HTTP {r3.status_code}"
        except Exception:
            dash = "❌ Çalışmıyor"
        msg = (
            f"🤖 <b>AURVEX.Ai DURUM</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 Açık Trade:   <b>{open_count}</b>\n"
            f"💰 Bakiye:       <b>{balance:.2f}$</b>\n"
            f"🔗 Binance:      {binance}\n"
            f"📱 Telegram:     {tg_status}\n"
            f"🖥 Dashboard:    {dash}\n"
            f"⏰ Son Sinyal:   {last_sig_time}\n"
            f"🕐 Zaman:        {datetime.now(timezone.utc).strftime('%H:%M UTC')}"
        )
        _send(chat_id, msg)
    except Exception as e:
        _send(chat_id, f"❌ /status hatası: {e}")


def cmd_live(chat_id):
    try:
        with _db() as conn:
            rows = conn.execute(
                "SELECT * FROM trades WHERE status NOT IN "
                "('closed','closed_win','closed_loss','sl','trail','tp3','timeout') "
                "ORDER BY id DESC"
            ).fetchall()
        if not rows:
            _send(chat_id, "📭 Şu an açık trade yok.")
            return
        lines = [f"📊 <b>AKTİF TRADELER ({len(rows)})</b>\n━━━━━━━━━━━━━━━━━━━━"]
        for t in rows:
            lines.append(_fmt_trade(dict(t)))
            lines.append("─────────────────────")
        _send(chat_id, "\n".join(lines))
    except Exception as e:
        _send(chat_id, f"❌ /live hatası: {e}")


def cmd_signals(chat_id):
    try:
        with _db() as conn:
            rows = conn.execute(
                "SELECT * FROM signal_candidates ORDER BY id DESC LIMIT 15"
            ).fetchall()
        if not rows:
            _send(chat_id, "📭 Henüz sinyal kaydı yok.")
            return
        lines = [f"🔍 <b>SON SCALP SİNYALLER ({len(rows)})</b>\n━━━━━━━━━━━━━━━━━━━━"]
        for r in rows:
            t = dict(r)
            dir_e = "📈" if t.get("direction") == "LONG" else "📉"
            dec = t.get("decision", "?")
            dec_e = {"ALLOW": "✅", "WATCH": "👁", "VETO": "🚫"}.get(dec, "❓")
            lines.append(
                f"{dir_e} <b>{t.get('symbol','?')}</b> {t.get('direction','?')} "
                f"| {dec_e} {dec} | Q:{t.get('setup_quality','?')} | "
                f"Skor:{t.get('score',0):.0f}"
            )
            if t.get("reject_reason") or t.get("ai_veto_reason"):
                reason = t.get("ai_veto_reason") or t.get("reject_reason") or ""
                lines.append(f"  ↳ {reason[:80]}")
        _send(chat_id, "\n".join(lines))
    except Exception as e:
        _send(chat_id, f"❌ /signals hatası: {e}")


def cmd_watchlist(chat_id):
    try:
        with _db() as conn:
            rows = conn.execute(
                "SELECT * FROM signal_candidates WHERE decision='WATCH' "
                "ORDER BY id DESC LIMIT 20"
            ).fetchall()
        if not rows:
            _send(chat_id, "📭 Watchlist boş.")
            return
        lines = [f"👁 <b>WATCHLİST ({len(rows)})</b>\n━━━━━━━━━━━━━━━━━━━━"]
        for r in rows:
            t = dict(r)
            dir_e = "📈" if t.get("direction") == "LONG" else "📉"
            lines.append(
                f"{dir_e} <b>{t.get('symbol','?')}</b> {t.get('direction','?')} "
                f"| Q:{t.get('setup_quality','?')} | Skor:{t.get('score',0):.0f}\n"
                f"  Giriş:<code>{_safe(t.get('entry'),'{:.6f}')}</code> "
                f"Stop:<code>{_safe(t.get('sl'),'{:.6f}')}</code> "
                f"TP1:<code>{_safe(t.get('tp1'),'{:.6f}')}</code>"
            )
        _send(chat_id, "\n".join(lines))
    except Exception as e:
        _send(chat_id, f"❌ /watchlist hatası: {e}")


def cmd_stats(chat_id):
    try:
        with _db() as conn:
            # Genel
            total_row = conn.execute(
                "SELECT COUNT(*), SUM(net_pnl), "
                "SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) "
                "FROM trades WHERE close_time IS NOT NULL"
            ).fetchone()
            # Bugün
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            today_row = conn.execute(
                "SELECT COUNT(*), SUM(net_pnl), "
                "SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) "
                "FROM trades WHERE close_time LIKE ? AND close_time IS NOT NULL",
                (f"{today}%",)
            ).fetchone()
            # Bakiye
            bal = conn.execute(
                "SELECT balance FROM paper_account WHERE id=1"
            ).fetchone()
            # Sinyal sayısı
            sig_row = conn.execute(
                "SELECT COUNT(*), "
                "SUM(CASE WHEN decision='ALLOW' THEN 1 ELSE 0 END), "
                "SUM(CASE WHEN decision='WATCH' THEN 1 ELSE 0 END), "
                "SUM(CASE WHEN decision='VETO' THEN 1 ELSE 0 END) "
                "FROM signal_candidates"
            ).fetchone()
        total = total_row[0] or 0
        total_pnl = total_row[1] or 0
        total_wins = total_row[2] or 0
        win_rate = round(100.0 * total_wins / total, 1) if total else 0
        today_total = today_row[0] or 0
        today_pnl = today_row[1] or 0
        today_wins = today_row[2] or 0
        today_wr = round(100.0 * today_wins / today_total, 1) if today_total else 0
        balance = float(bal[0]) if bal else 0.0
        sig_total = sig_row[0] or 0
        sig_allow = sig_row[1] or 0
        sig_watch = sig_row[2] or 0
        sig_veto  = sig_row[3] or 0
        msg = (
            f"📈 <b>İSTATİSTİKLER</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>Genel:</b>\n"
            f"  Trade:    {total} | Win: {total_wins} | WR: %{win_rate}\n"
            f"  Net PnL:  <b>{total_pnl:+.2f}$</b>\n"
            f"  Bakiye:   <b>{balance:.2f}$</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>Bugün ({today}):</b>\n"
            f"  Trade:    {today_total} | Win: {today_wins} | WR: %{today_wr}\n"
            f"  PnL:      <b>{today_pnl:+.2f}$</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>Sinyal Havuzu:</b>\n"
            f"  Toplam:   {sig_total}\n"
            f"  ✅ ALLOW: {sig_allow} | 👁 WATCH: {sig_watch} | 🚫 VETO: {sig_veto}"
        )
        _send(chat_id, msg)
    except Exception as e:
        _send(chat_id, f"❌ /stats hatası: {e}")


def cmd_last(chat_id):
    try:
        with _db() as conn:
            row = conn.execute(
                "SELECT * FROM trades WHERE close_time IS NOT NULL "
                "ORDER BY close_time DESC LIMIT 1"
            ).fetchone()
        if not row:
            _send(chat_id, "📭 Henüz kapanan trade yok.")
            return
        t = dict(row)
        t["result"] = t.get("result") or ("WIN" if (t.get("net_pnl") or 0) > 0 else "LOSS")
        header = "🟢 <b>SON KAPANAN TRADE — WIN</b>" if t["result"] == "WIN" \
            else "🔴 <b>SON KAPANAN TRADE — LOSS</b>"
        msg = header + "\n━━━━━━━━━━━━━━━━━━━━\n" + _fmt_trade(t, show_pnl=True)
        if t.get("close_time"):
            msg += f"\n  Kapanış: {t['close_time'][:19]}"
        if t.get("hold_minutes"):
            msg += f"\n  Süre:    {int(t['hold_minutes'])} dk"
        _send(chat_id, msg)
    except Exception as e:
        _send(chat_id, f"❌ /last hatası: {e}")


def cmd_risk(chat_id):
    try:
        from config import (
            RISK_PCT, MAX_OPEN_TRADES, DAILY_MAX_LOSS_PCT,
            CIRCUIT_BREAKER_LOSSES, CIRCUIT_BREAKER_MINUTES,
            PAPER_LEVERAGE, MAX_LEVERAGE, MIN_LEVERAGE,
            TRADE_THRESHOLD, TRADE_QUALITIES, WATCHLIST_QUALITIES,
            REJECT_QUALITIES, AI_MAX_DAILY_SIGNALS
        )
        with _db() as conn:
            open_count = conn.execute(
                "SELECT COUNT(*) FROM trades WHERE status NOT IN "
                "('closed','closed_win','closed_loss','sl','trail','tp3','timeout')"
            ).fetchone()[0]
        msg = (
            f"⚠️ <b>RİSK AYARLARI</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Risk/Trade:     %{RISK_PCT}\n"
            f"Max Açık:       {MAX_OPEN_TRADES} (şu an: {open_count})\n"
            f"Max Günlük Zarar: %{DAILY_MAX_LOSS_PCT}\n"
            f"Circuit Breaker: {CIRCUIT_BREAKER_LOSSES} zarar / {CIRCUIT_BREAKER_MINUTES} dk\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Kaldıraç:       {MIN_LEVERAGE}x–{MAX_LEVERAGE}x (varsayılan {PAPER_LEVERAGE}x)\n"
            f"Eşik:           {TRADE_THRESHOLD}\n"
            f"Trade Kalite:   {', '.join(TRADE_QUALITIES)}\n"
            f"Watchlist:      {', '.join(WATCHLIST_QUALITIES)}\n"
            f"Veto:           {', '.join(REJECT_QUALITIES)}\n"
            f"Max Sinyal/Gün: {AI_MAX_DAILY_SIGNALS}"
        )
        _send(chat_id, msg)
    except Exception as e:
        _send(chat_id, f"❌ /risk hatası: {e}")


def cmd_health(chat_id):
    try:
        r = requests.get("http://localhost:5000/api/health", timeout=8)
        if r.status_code == 200:
            d = r.json().get("data", {})
            msg = (
                f"🏥 <b>SERVİS SAĞLIĞI</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"DB:       {d.get('db_status','?')}\n"
                f"Binance:  {d.get('binance_status','?')}\n"
                f"Telegram: {d.get('telegram_status','?')}\n"
                f"Scanner:  {d.get('scanner_status','?')}\n"
                f"Açık Trade: {d.get('open_trade_count','?')}\n"
                f"Son Sinyal: {d.get('last_signal_time','—')}\n"
                f"Son Hata:   {d.get('last_error','—') or '—'}"
            )
        else:
            msg = f"❌ Dashboard yanıt vermedi (HTTP {r.status_code})"
        _send(chat_id, msg)
    except Exception as e:
        _send(chat_id, f"❌ /health hatası (dashboard çalışmıyor olabilir): {e}")


def cmd_restart_info(chat_id):
    """Servislerin durumunu göster, restart komutu verme."""
    services = ["aurvex-bot.service", "aurvex-dashboard.service", "aurvex-watchdog.service"]
    lines = ["🔧 <b>SERVİS DURUMU</b>\n━━━━━━━━━━━━━━━━━━━━"]
    for svc in services:
        try:
            result = subprocess.run(
                ["systemctl", "is-active", svc],
                capture_output=True, text=True, timeout=5
            )
            status = result.stdout.strip()
            emoji = "✅" if status == "active" else "❌"
            lines.append(f"{emoji} {svc}: {status}")
        except Exception:
            lines.append(f"❓ {svc}: kontrol edilemedi")
    lines.append("\n<i>Restart için sunucuda:</i>")
    lines.append("<code>sudo systemctl restart aurvex-bot.service</code>")
    _send(chat_id, "\n".join(lines))


def cmd_help(chat_id):
    msg = (
        "🤖 <b>AURVEX.Ai KOMUTLAR</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "/status       → Bot, dashboard, DB, Binance durumu\n"
        "/live         → Aktif açık tradeler\n"
        "/signals      → Son scalp sinyalleri (DB)\n"
        "/watchlist    → B kalite izleme listesi\n"
        "/stats        → Winrate, PnL, sinyal istatistikleri\n"
        "/last         → Son kapanan trade\n"
        "/risk         → Risk ayarları ve kalite filtreleri\n"
        "/health       → Servis sağlık kontrolü\n"
        "/restart_info → Servis durumları\n"
        "/help         → Bu yardım mesajı"
    )
    _send(chat_id, msg)

# ─────────────────────────────────────────────────────────────────────────────
# KOMUT YÖNLENDIRICI
# ─────────────────────────────────────────────────────────────────────────────

COMMANDS = {
    "/status":       cmd_status,
    "/live":         cmd_live,
    "/signals":      cmd_signals,
    "/watchlist":    cmd_watchlist,
    "/stats":        cmd_stats,
    "/last":         cmd_last,
    "/risk":         cmd_risk,
    "/health":       cmd_health,
    "/restart_info": cmd_restart_info,
    "/help":         cmd_help,
    "/start":        cmd_help,
}


def _handle_update(update: dict):
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return
    chat_id = msg.get("chat", {}).get("id")
    text = (msg.get("text") or "").strip()
    if not text or not chat_id:
        return
    # Komut al (@ suffix'ini temizle)
    cmd = text.split()[0].split("@")[0].lower()
    handler = COMMANDS.get(cmd)
    if handler:
        logger.info(f"Komut: {cmd} | chat_id={chat_id}")
        try:
            threading.Thread(target=handler, args=(chat_id,), daemon=True).start()
        except Exception as e:
            logger.error(f"Komut işleme hatası {cmd}: {e}")
    else:
        _send(chat_id, f"❓ Bilinmeyen komut: <code>{cmd}</code>\n/help ile komutları görün.")

# ─────────────────────────────────────────────────────────────────────────────
# ANA DÖNGÜ
# ─────────────────────────────────────────────────────────────────────────────

def run():
    global _offset, _running
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN tanımlı değil!")
        return
    _running = True
    logger.info("Telegram komut botu başladı.")
    # Bot bilgisini logla
    me = _api("getMe")
    if me.get("ok"):
        bot_name = me["result"].get("username", "?")
        logger.info(f"Bot: @{bot_name}")
    while _running:
        try:
            updates = _get_updates()
            for upd in updates:
                _offset = upd["update_id"] + 1
                _handle_update(upd)
        except Exception as e:
            logger.error(f"Polling hatası: {e}")
            time.sleep(5)


def stop():
    global _running
    _running = False


if __name__ == "__main__":
    run()
