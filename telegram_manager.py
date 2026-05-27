"""
telegram_manager.py - AurvexAI Telegram Komut Merkezi v2.0
Komutlar: /help /status /stats /trades /balance /open /ghost /daily /mode /pause /resume /finish
"""
from __future__ import annotations
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Optional
import requests
import config

logger = logging.getLogger("ax.telegram_manager")
_POLL_URL = "https://api.telegram.org/bot{token}/getUpdates"
_TIMEOUT  = 10


class TelegramManager:
    def __init__(self, send_fn: Callable[[str], bool]):
        self.send_fn        = send_fn
        self.token          = config.TELEGRAM_BOT_TOKEN
        self.chat_id        = str(config.TELEGRAM_CHAT_ID)
        self.is_paused      = False
        self.is_finish_mode = False
        self.human_mode     = False
        self._running       = False
        self._thread: Optional[threading.Thread] = None
        self._last_update_id = 0
        self._start_time    = time.time()

    def _is_configured(self) -> bool:
        return bool(self.token) and bool(self.chat_id)

    def start(self):
        if not self._is_configured():
            logger.warning("TelegramManager: token/chat_id eksik")
            return
        # Önceki offset'i veritabanından yükle — restart sonrası eski komutları önle
        try:
            import database as _db
            saved = _db.get_state("tg_last_update_id")
            if saved:
                self._last_update_id = int(saved)
                logger.info(f"Telegram offset yüklendi: {self._last_update_id}")

            # Persist edilen durumlari yukle
            is_paused_val = _db.get_state("tg_is_paused")
            if is_paused_val is not None:
                self.is_paused = (is_paused_val == "True")
                logger.info(f"Telegram is_paused yüklendi: {self.is_paused}")

            is_finish_mode_val = _db.get_state("tg_is_finish_mode")
            if is_finish_mode_val is not None:
                self.is_finish_mode = (is_finish_mode_val == "True")
                logger.info(f"Telegram is_finish_mode yüklendi: {self.is_finish_mode}")

            human_mode_val = _db.get_state("tg_human_mode")
            if human_mode_val is not None:
                self.human_mode = (human_mode_val == "True")
                config.HUMAN_MODE = self.human_mode
                logger.info(f"Telegram human_mode yüklendi: {self.human_mode}")
                
            exec_mode_val = _db.get_state("tg_execution_mode")
            if exec_mode_val:
                config.EXECUTION_MODE = exec_mode_val
                logger.info(f"Telegram execution_mode yüklendi: {config.EXECUTION_MODE}")
        except Exception as e:
            logger.warning(f"Telegram states load hatası: {e}")
        self._running = True
        self._thread = threading.Thread(
            target=self._poll_loop, daemon=True, name="tg-manager"
        )
        self._thread.start()
        logger.info("TelegramManager basladi — /help yaz")

    def stop(self):
        self._running = False

    def _poll_loop(self):
        while self._running:
            try:
                self._poll_once()
            except Exception as e:
                logger.debug("Poll hatasi: %s", e)
            time.sleep(3)

    def _poll_once(self):
        url = _POLL_URL.format(token=self.token)
        params = {"timeout": 5, "offset": self._last_update_id + 1, "limit": 10}
        resp = requests.get(url, params=params, timeout=_TIMEOUT)
        if resp.status_code != 200:
            return
        data = resp.json()
        if not data.get("ok"):
            return
        for update in data.get("result", []):
            uid = update.get("update_id", 0)
            if uid > self._last_update_id:
                self._last_update_id = uid
                # Offset'i DB'ye kaydet — restart güvenliği
                try:
                    import database as _db
                    _db.set_state("tg_last_update_id", str(uid))
                except Exception:
                    pass
            else:
                continue  # eski update, atla
            self._handle_update(update)

    def _handle_update(self, update: dict):
        msg       = update.get("message") or update.get("channel_post") or {}
        text      = (msg.get("text") or "").strip()
        from_chat = str(msg.get("chat", {}).get("id", ""))
        if not text.startswith("/"):
            return
        if from_chat and from_chat != self.chat_id:
            return
        cmd = text.lower().split()[0].split("@")[0]
        logger.info("Komut: %s", cmd)
        handlers = {
            "/help":    self._cmd_help,
            "/health":  self._cmd_health,
            "/status":  self._cmd_status,
            "/stats":   self._cmd_stats,
            "/trades":  self._cmd_trades,
            "/balance": self._cmd_balance,
            "/open":    self._cmd_open,
            "/signal":  self._cmd_signal,
            "/ghost":   self._cmd_ghost,
            "/daily":   self._cmd_daily,
            "/mode":    self._cmd_mode,
            "/pause":   self._cmd_pause,
            "/resume":  self._cmd_resume,
            "/finish":  self._cmd_finish,
            "/human":   self._cmd_human_on,
            "/scalp":   self._cmd_human_off,
            "/insan":   self._cmd_human_on,
            "/paper":   self._cmd_paper,
            "/live":    self._cmd_live,
        }
        handler = handlers.get(cmd)
        if handler:
            try:
                handler()
            except Exception as e:
                self.send_fn(f"Komut hatasi ({cmd}): {e}")
        else:
            self.send_fn(f"Bilinmeyen komut: {cmd}\n/help yazin.")

    def _cmd_help(self):
        self.send_fn(
            "🤖 **AurvexAI Yönetim Merkezi**\n\n"
            "Merhaba! Ben senin yapay zeka destekli alım-satım asistanın. Sistemin kalbini buradan kontrol edebilirsin. İşte yapabileceklerim:\n\n"
            "📊 **Gözlem ve Raporlama**\n"
            "🔹 `/health` — Sistem sağlığını, RAM ve veritabanı durumunu kontrol eder.\n"
            "🔹 `/status` — Sistemin genel sağlığını, aktif modunu ve kârını özetler.\n"
            "🔹 `/open` — Şu an Binance'te açık olan işlemlerini (giriş, stop, kâr) gösterir.\n"
            "🔹 `/stats` — Tüm zamanların performans özetini (Win Rate vb.) çıkarır.\n"
            "🔹 `/daily` — Bugüne özel kaç işlem açıldığını ve güncel kâr/zararı listeler.\n"
            "🔹 `/balance` — Kasanın büyüme oranını detaylıca gösterir.\n"
            "🔹 `/trades` — Kapanan son 5 işlemi (Neden kapandığıyla birlikte) listeler.\n"
            "🔹 `/ghost` — Yapay zekanın (Ghost Learning) arka planda ne kadar öğrendiğini gösterir.\n\n"
            "⚙️ **Strateji ve Mod Değişimi**\n"
            "🔹 `/mode` — Şu an hangi stratejide çalıştığımızı söyler.\n"
            "🔹 `/human` — İnsan Modu: Az ama öz, sadece en kaliteli sinyallere girer (A+/S).\n"
            "🔹 `/scalp` — Scalp Modu: Piyasayı agresif tarar, çok işleme girer ve hızlı çıkar.\n\n"
            "🛑 **Acil Durum Kontrolleri**\n"
            "🔹 `/pause` — Piyasalar çok riskliyse botu duraklat. (Açık işlemler takip edilir, yeni işleme girilmez).\n"
            "🔹 `/resume` — Her şey yolundaysa botu tekrar ava çıkar.\n"
            "🔹 `/finish` — Mevcut işlemler kapandığı an botu tamamen uykuya al.\n\n"
            "💡 *İpucu: Herhangi bir komuta tıklayarak anında çalıştırabilirsin!*"
        )

    def _cmd_health(self):
        import os
        import time
        import database
        
        # System Uptime
        uptime = int(time.time() - self._start_time)
        h, rem = divmod(uptime, 3600)
        m = rem // 60
        
        # DB Size
        try:
            db_path = getattr(config, "DB_PATH", "trading.db")
            db_size = os.path.getsize(db_path) / (1024 * 1024) if os.path.exists(db_path) else 0
            wal_size = os.path.getsize(db_path + "-wal") / (1024 * 1024) if os.path.exists(db_path + "-wal") else 0
        except Exception:
            db_size, wal_size = 0, 0
            
        # RAM Usage
        try:
            import psutil
            ram = psutil.virtual_memory().percent
            ram_text = f"%{ram:.1f}"
        except ImportError:
            ram_text = "Ölçülemedi (psutil yok)"
            
        # DB Query check (Ping)
        t1 = time.time()
        open_trades = len(database.get_open_trades())
        t2 = time.time()
        db_ping = int((t2 - t1) * 1000)
        
        self.send_fn(
            f"🏥 **Sistem Sağlık Raporu**\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"⏱ **Kesintisiz Çalışma:** {h} Saat {m} Dakika\n"
            f"💾 **Veritabanı Boyutu:** {db_size:.1f} MB\n"
            f"🔄 **Veritabanı WAL:** {wal_size:.1f} MB\n"
            f"⚡ **DB Gecikmesi (Ping):** {db_ping} ms\n"
            f"🧠 **RAM Kullanımı:** {ram_text}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"✅ *Tüm arka plan servisleri ve veritabanı aktif şekilde çalışıyor.*"
        )

    def _cmd_status(self):
        import database
        bal     = database.get_paper_balance() or 0
        init    = getattr(config, "INITIAL_PAPER_BALANCE", 2000.0)
        roi     = ((bal - init) / init * 100) if init else 0
        open_t  = database.get_open_trades()
        stats   = database.get_dashboard_stats()
        uptime  = int(time.time() - self._start_time)
        h, rem  = divmod(uptime, 3600)
        m       = rem // 60

        try:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            with database.get_conn() as conn:
                today_pnl = conn.execute("""
                    SELECT COALESCE(SUM(net_pnl), 0) FROM trades
                    WHERE LOWER(status)='closed' AND DATE(close_time)=?
                """, (today,)).fetchone()[0] or 0
                ghost_n = conn.execute(
                    "SELECT COUNT(*) FROM ghost_signals"
                ).fetchone()[0]
        except Exception:
            today_pnl = 0
            ghost_n = 0

        regime = database.get_system_state("market_regime") or "NEUTRAL"
        paused = "⏸ DURAKLATILDI" if self.is_paused else "▶️ Aktif"

        open_lines = ""
        for t in open_t[:5]:
            sym   = t.get("symbol", "?")
            side  = (t.get("side") or t.get("direction", "?"))[:1]
            entry = float(t.get("entry_price") or t.get("entry") or 0)
            upnl  = float(t.get("unrealized_pnl") or 0)
            status = t.get("status", "open")
            tp_marker = " 🎯" if "tp1" in status else ""
            open_lines += f"\n  {sym} {side} @{entry:.4f} {upnl:+.2f}${tp_marker}"

        self.send_fn(
            f"📈 **Sistem Durum Raporu**\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"🔍 **Motor Durumu:** {paused}\n"
            f"🎯 **Çalışma Modu:** {'🧠 İnsan (Özenli)' if config.HUMAN_MODE else '⚡ Scalp (Agresif)'} | {config.EXECUTION_MODE.upper()}\n"
            f"🌊 **Piyasa Yönü (Rejim):** {regime}\n"
            f"⏱ **Kesintisiz Çalışma:** {h} Saat, {m} Dakika\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"💰 **Sanal Kasa:** ${bal:.2f} (Büyüme: {roi:+.1f}%)\n"
            f"📅 **Bugünün Kârı:** ${today_pnl:+.2f}\n"
            f"📊 **Toplam Kâr:** ${stats.get('total_pnl', 0):+.2f}\n"
            f"👻 **YZ Öğrenme Havuzu:** {ghost_n} simülasyon\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"🟢 **Açık İşlemler ({len(open_t)} adet):**{open_lines}\n\n"
            f"💡 *Detaylar için /stats veya /open yazabilirsin.*"
        )

    def _cmd_stats(self):
        import database
        stats = database.get_dashboard_stats()
        total = stats.get("total_trades", 0)
        wins  = stats.get("win_trades", 0)
        loss  = stats.get("loss_trades", 0)
        pnl   = stats.get("total_pnl", 0)
        wr    = stats.get("win_rate", 0)
        bal   = database.get_paper_balance() or 0
        init  = getattr(config, "INITIAL_PAPER_BALANCE", 2000.0)
        roi   = ((bal - init) / init * 100) if init else 0
        self.send_fn(
            f"📊 **Genel Performans İstatistikleri**\n\n"
            f"Bu veriler botun şu ana kadar gösterdiği tüm başarı oranını özetler:\n\n"
            f"🔸 **Toplam Kapanan İşlem:** {total} adet\n"
            f"🔸 **Başarı Oranı (Kazanılan/Kaybedilen):** {wins} Başarılı / {loss} Zararlı\n"
            f"🔸 **Win Rate (Kazanma Yüzdesi):** %{wr:.1f}\n"
            f"🔸 **Kümülatif Net Kâr:** ${pnl:+.2f}\n\n"
            f"💼 **Kasa Durumu:**\n"
            f"🔸 Başlangıç: ${init:.2f}\n"
            f"🔸 Güncel Bakiye: ${bal:.2f}\n"
            f"🔸 Toplam Büyüme (ROI): %{roi:+.1f}\n\n"
            f"💡 *Not: Yüksek kâr faktörü, düşük win rate'den daha önemlidir. Bot kârı uzatıp zararı erken keser.*"
        )

    def _cmd_trades(self):
        import database
        trades = database.get_recent_trades(5)
        if not trades:
            self.send_fn("Henuez kapatilmis trade yok.")
            return
        lines = []
        for t in trades:
            sym    = t.get("symbol", "?")
            side   = t.get("side") or t.get("direction", "?")
            pnl    = float(t.get("net_pnl") or t.get("realized_pnl") or 0)
            reason = t.get("close_reason") or "?"
            icon   = "✅ KÂR" if pnl > 0 else "❌ ZARAR"
            lines.append(f"{icon} | {sym} ({side})\n   └ Kâr: {pnl:+.3f}$ | Sebep: {reason}")
        self.send_fn("📜 **Kapanan Son 5 İşlemin Analizi**\n\n" + "\n\n".join(lines) + "\n\n💡 *Not: Neden kapandığına (reason) bakarak botun hangi stratejiyi uyguladığını (SL, TP, Trail) görebilirsin.*")

    def _cmd_balance(self):
        import database
        bal  = database.get_paper_balance() or 0
        init = getattr(config, "INITIAL_PAPER_BALANCE", 2000.0)
        diff = bal - init
        try:
            with database.get_conn() as conn:
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                row   = conn.execute(
                    "SELECT COALESCE(SUM(realized_pnl),0) FROM trades WHERE DATE(close_time)=? AND status='closed'",
                    (today,)
                ).fetchone()
            today_pnl = float(row[0]) if row else 0.0
        except Exception:
            today_pnl = 0.0
        self.send_fn(
            f"💳 **Bakiye ve Kazanç Özeti**\n\n"
            f"Sisteme tanımlı başlangıç kasan ve şu anki büyüme:\n\n"
            f"🔹 Başlangıç Kası: ${init:.2f}\n"
            f"🔹 **Şu Anki Kasa:** ${bal:.2f}\n"
            f"🔹 Toplam Kâr/Zarar: ${diff:+.2f}\n"
            f"🔹 Sadece Bugün Kazanılan: ${today_pnl:+.2f}\n\n"
            f"💡 *Canlı ticarete (Live Trading) geçtiğinde burada gerçek Binance cüzdanını göreceksin.*"
        )

    def _cmd_open(self):
        import database
        trades = database.get_open_trades()
        if not trades:
            self.send_fn("Acik trade yok.")
            return
        lines = []
        now = datetime.now(timezone.utc)
        for t in trades:
            sym  = t.get("symbol", "?")
            side = t.get("side") or t.get("direction", "?")
            ep   = float(t.get("entry_price") or t.get("entry") or 0)
            sl   = float(t.get("stop_loss") or t.get("sl") or 0)
            tp1  = float(t.get("tp1") or 0)
            upnl = float(t.get("unrealized_pnl") or 0)
            opened = t.get("opened_at", "")
            hold = ""
            if opened:
                try:
                    dt = datetime.fromisoformat(opened.replace("Z", "+00:00"))
                    mins = int((now - dt).total_seconds() / 60)
                    hold = f" {mins}dk"
                except Exception:
                    pass
            lines.append(
                f"🪙 **{sym}** ({side}) {hold}\n"
                f"   ├ Giriş Fiyatı: ${ep:.4f}\n"
                f"   ├ Stop Loss: ${sl:.4f} (Korunuyor)\n"
                f"   ├ Hedef TP1: ${tp1:.4f}\n"
                f"   └ **Anlık Durum (PnL):** {upnl:+.2f}$"
            )
        self.send_fn(f"🟢 **Aktif Açık İşlemler ({len(trades)} adet)**\n\n" + "\n\n".join(lines) + "\n\n💡 *Bot bunları canlı takip ediyor. Kârda olanları Trailing Stop ile koruyacaktır.*")

    def _cmd_signal(self):
        """Son 5 sinyal adayının özeti."""
        import database
        signals = database.get_recent_signals(5)
        if not signals:
            self.send_fn("Henüz sinyal yok.")
            return
        lines = ["📡 Son 5 Sinyal\n━━━━━━━━━━━━━━"]
        for s in signals:
            sym   = s.get("symbol", "?")
            side  = (s.get("direction") or s.get("side", "?"))
            score = s.get("final_score") or s.get("score", 0)
            dec   = s.get("decision", "?")
            t     = str(s.get("created_at", ""))[:16]
            emoji = "✅" if dec == "ALLOW" else "❌" if dec == "VETO" else "👁"
            lines.append(f"{emoji} {sym} {side} | {score:.0f} | {t}")
        self.send_fn("\n".join(lines))

    def _cmd_ghost(self):
        import database
        try:
            with database.get_conn() as conn:
                gs_total = conn.execute("SELECT COUNT(*) FROM ghost_signals").fetchone()[0]
                gs_sim   = conn.execute("SELECT COUNT(*) FROM ghost_signals WHERE simulated=1").fetchone()[0]
                gr_wins  = conn.execute("SELECT COUNT(*) FROM ghost_results WHERE virtual_outcome='WIN'").fetchone()[0]
                gr_loss  = conn.execute("SELECT COUNT(*) FROM ghost_results WHERE virtual_outcome='LOSS'").fetchone()[0]
                gr_avg_r = conn.execute(
                    "SELECT AVG(virtual_pnl_r) FROM ghost_results WHERE virtual_outcome IN ('WIN','LOSS')"
                ).fetchone()[0] or 0
                pending_sugg = conn.execute(
                    "SELECT COUNT(*) FROM ghost_suggestions WHERE applied=0"
                ).fetchone()[0]
            resolved = gr_wins + gr_loss
            vwr = round(gr_wins / resolved * 100, 1) if resolved > 0 else 0
            self.send_fn(
                f"👻 **Yapay Zeka & Ghost Learning 2.0 Durumu**\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"Bu modül botun beynidir. İşleme girmese bile sanal sinyaller üretip sonuçlarından ders çıkarır.\n\n"
                f"🧠 **Toplanan Veri Seti:** {gs_total} sinyal incelendi.\n"
                f"⚙️ **İşlenen (Simüle):** {gs_sim} | Bekleyen: {gs_total - gs_sim}\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"📊 **Öğrenme Başarısı:**\n"
                f"🔹 Doğru Tahmin (WIN): {gr_wins} adet\n"
                f"🔹 Yanlış Tahmin (LOSS): {gr_loss} adet\n"
                f"🔹 Sanal Win Rate: %{vwr:.1f}\n"
                f"🔹 Ortalama Kazanç Çarpanı: {gr_avg_r:.2f}R\n\n"
                f"🛠 Bekleyen Strateji Önerisi: {pending_sugg} adet"
            )
        except Exception as e:
            self.send_fn(f"Ghost bilgisi alinamadi: {e}")

    def _cmd_daily(self):
        import database
        try:
            with database.get_conn() as conn:
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                row   = conn.execute(
                    """SELECT COUNT(*),
                              SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END),
                              SUM(CASE WHEN realized_pnl <= 0 THEN 1 ELSE 0 END),
                              COALESCE(SUM(realized_pnl), 0)
                       FROM trades WHERE DATE(close_time)=? AND status='closed'""",
                    (today,)
                ).fetchone()
            total  = row[0] or 0
            wins   = row[1] or 0
            losses = row[2] or 0
            pnl    = float(row[3] or 0)
            wr     = round(wins / total * 100, 1) if total else 0
            self.send_fn(
                f"Bugun ({today})\n\n"
                f"Trade: {total} | {wins}W / {losses}L\n"
                f"Winrate: {wr:.1f}%\n"
                f"Gunluk PnL: ${pnl:+.2f}"
            )
        except Exception as e:
            self.send_fn(f"Gunluk ozet alinamadi: {e}")

    def _cmd_mode(self):
        is_human = config.HUMAN_MODE
        thr = config.HUMAN_TRADE_THRESHOLD if is_human else config.TRADE_THRESHOLD
        sl  = config.HUMAN_SL_ATR_MULT if is_human else config.SL_ATR_MULT
        mx  = config.HUMAN_MAX_OPEN_TRADES if is_human else config.MAX_OPEN_TRADES
        self.send_fn(
            f"⚙️ <b>Çalışma Modu</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"Aktif: {'🧠 HUMAN MODE' if is_human else '⚡ SCALP MODE'}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"Trade eşiği: {thr}\n"
            f"SL çarpanı: {sl}x ATR\n"
            f"Maks açık trade: {mx}\n"
            f"Execution: {config.EXECUTION_MODE.upper()}\n"
            f"AX Mode: {config.AX_MODE.upper()}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"/human — İnsan moduna geç\n"
            f"/scalp — Scalp moduna geç"
        )

    def _cmd_pause(self):
        self.is_paused = True
        try:
            import database as _db
            _db.set_state("tg_is_paused", "True")
        except Exception:
            pass
        self.send_fn(
            "⏸ **SİSTEM DURAKLATILDI (PAUSE)**\n\n"
            "Bot şu an yeni piyasa fırsatlarını aramayı ve yeni işlem açmayı tamamen **durdurdu**.\n\n"
            "💡 Ancak endişelenme! Hali hazırda açık olan işlemlerinin SL, TP ve Kâr alma seviyeleri takip edilmeye devam ediyor.\n\n"
            "Piyasa tehlikesiz göründüğünde botu tekrar işe göndermek için `/resume` komutunu kullan."
        )

    def _cmd_resume(self):
        self.is_paused = False
        try:
            import database as _db
            _db.set_state("tg_is_paused", "False")
        except Exception:
            pass
        self.send_fn(
            "▶️ **SİSTEM YENİDEN AKTİF (RESUME)**\n\n"
            "Bot uykudan uyandı! Yeniden piyasayı taramaya ve uygun sinyallerde işlem açmaya başlıyor."
        )

    def _cmd_finish(self):
        self.is_finish_mode = True
        try:
            import database as _db
            _db.set_state("tg_is_finish_mode", "True")
        except Exception:
            pass
        self.send_fn(
            "Finish modu aktif.\n"
            "Yeni sinyal alinmayacak.\n"
            "Acik tradeler kapaninca bot duracak."
        )

    def _cmd_human_on(self):
        """Human mode: Az ama güçlü setup, yüksek threshold."""
        self.human_mode = True
        try:
            import config as _cfg
            _cfg.HUMAN_MODE = True
            import database as _db
            _db.set_state("tg_human_mode", "True")
        except Exception:
            pass
        self.send_fn(
            "🧠 HUMAN MODE AKTİF\n"
            "━━━━━━━━━━━━━━━━\n"
            "SL: Geniş (2x ATR)\n"
            "TP: Uzak (1.5R-2.5R)\n"
            "Maks açık trade: 2\n"
            "Sadece A+/S kalite\n\n"
            "/scalp ile normal moda dön."
        )

    def _cmd_human_off(self):
        """Scalp mode: Çok trade, dar SL, hızlı TP."""
        self.human_mode = False
        try:
            import config as _cfg
            _cfg.HUMAN_MODE = False
            import database as _db
            _db.set_state("tg_human_mode", "False")
        except Exception:
            pass
        self.send_fn(
            "⚡ SCALP MODE AKTİF\n"
            "━━━━━━━━━━━━━━━━\n"
            "SL: 1.8x ATR (min %1.5)\n"
            "TP: 1.5R - 2.5R\n"
            "Min R:R: 1.5\n"
            "Maks açık trade: 5\n"
            "/human ile insan moduna geç."
        )

    def _cmd_paper(self):
        try:
            import config as _cfg
            _cfg.EXECUTION_MODE = "paper"
            import database as _db
            _db.set_state("tg_execution_mode", "paper")
            self.send_fn("💵 **PAPER MODE AKTİF**\nSistem artık sanal parayla işlem yapacak. Gerçek paranız güvende.")
        except Exception as e:
            self.send_fn(f"Hata: {e}")

    def _cmd_live(self):
        try:
            import config as _cfg
            _cfg.EXECUTION_MODE = "live"
            import database as _db
            _db.set_state("tg_execution_mode", "live")
            self.send_fn("🔥 **LIVE TRADING AKTİF**\n\n⚠️ **DİKKAT:** Sistem şu andan itibaren GERÇEK Binance bakiyenizle işlem açacaktır. Kemerlerinizi bağlayın!")
        except Exception as e:
            self.send_fn(f"Hata: {e}")
