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
    def __init__(self, send_fn: Callable[[str], bool], friday_ceo=None):
        self.send_fn        = send_fn
        self.friday_ceo    = friday_ceo
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
        
        # Register commands on Telegram Bot Menu
        self._register_commands()

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
        if "callback_query" in update:
            import sys
            if "pytest" in sys.modules or "unittest" in sys.modules:
                self._handle_callback_query(update["callback_query"])
            else:
                import threading
                threading.Thread(
                    target=self._handle_callback_query,
                    args=(update["callback_query"],),
                    daemon=True
                ).start()
            return

        msg       = update.get("message") or update.get("channel_post") or {}
        text      = (msg.get("text") or "").strip()
        from_chat = str(msg.get("chat", {}).get("id", ""))
        if not text.startswith("/"):
            return
        if from_chat and from_chat != self.chat_id:
            return
        parts = text.split()
        if not parts:
            return
        cmd = parts[0].lower().split("@")[0]
        args = parts[1:]
        logger.info("Komut: %s, Args: %s", cmd, args)

        handlers = {
            "/start":   self._cmd_help,
            "/help":    self._cmd_help,
            "/health":  self._cmd_health,
            "/status":  self._cmd_status,
            "/settings": self._cmd_settings,
            "/heatmap": self._cmd_heatmap,
            "/weekly":  self._cmd_weekly,
            "/weekly_summary": self._cmd_weekly,
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
            "/close":   self._cmd_close,
            "/set":     self._cmd_set,
            "/export":  self._cmd_export,
            "/ml":      self._cmd_ml,
            "/retrain": self._cmd_retrain,
            "/friday": self._cmd_friday,
            "/friday_voice": self._cmd_friday_voice,
            "/friday_ses":   self._cmd_friday_voice,
            "/diagnose": self._cmd_diagnose,
            "/teshis":   self._cmd_diagnose,
        }
        handler = handlers.get(cmd)
        if handler:
            try:
                if cmd in ("/close", "/set", "/friday"):
                    handler(args)
                else:
                    handler()
            except Exception as e:
                self.send_fn(f"Komut hatasi ({cmd}): {e}")
        else:
            self.send_fn(f"Bilinmeyen komut: {cmd}\n/help yazin.")

    def _handle_callback_query(self, cb_query: dict):
        cb_id = cb_query.get("id")
        data = cb_query.get("data", "")
        msg = cb_query.get("message", {})
        msg_id = msg.get("message_id")
        from_chat = str(msg.get("chat", {}).get("id", ""))
        
        if from_chat != self.chat_id:
            self._answer_callback_query(cb_id, "Yetkisiz sohbet.")
            return
            
        logger.info("Callback query: %s", data)
        
        if not data.startswith("cmd:"):
            self._answer_callback_query(cb_id, "Bilinmeyen işlem.")
            return
            
        action = data[4:]
        self._answer_callback_query(cb_id, "İşlem alınıyor...")
        
        if action == "clean_server":
            if self.friday_ceo:
                try:
                    deleted_count, saved_space = self.friday_ceo.execute_cleanup()
                    msg_text = f"✅ <b>Sunucu Temizliği Başarılı!</b>\n\nToplam <code>{deleted_count}</code> adet gereksiz dosya silindi ve yaklaşık <code>{saved_space:.2f} MB</code> alan boşaltıldı."
                    self._edit_message_text(msg_text, msg_id, None)
                except Exception as e:
                    self._edit_message_text(f"❌ <b>Temizlik sırasında hata oluştu:</b> {e}", msg_id, None)
            else:
                self._edit_message_text("❌ <b>Friday CEO modülü aktif değil.</b>", msg_id, None)
            return
        elif action == "cancel_clean":
            self._edit_message_text("❌ <b>Temizlik işlemi boss tarafından iptal edildi.</b>", msg_id, None)
            return
            
        if action == "status":
            self._cmd_status()
        elif action == "refresh_status":
            status_text, reply_markup = self._generate_status_data()
            self._edit_message_text(status_text, msg_id, reply_markup)
        elif action == "refresh_settings":
            for name in ["AUTO_COMPOUNDING", "MTF_TREND_ALIGN_ENABLED", "EQUITY_CURVE_FILTER_ENABLED", "TRADE_THRESHOLD", "TELEGRAM_THRESHOLD", "CONFIRMATION_MODE", "TRAILING_STOP_TYPE"]:
                if name in config._CONFIG_CACHE:
                    del config._CONFIG_CACHE[name]
            status_text, reply_markup = self._generate_settings_data()
            self._edit_message_text(status_text, msg_id, reply_markup)
        elif action == "toggle_comp":
            import database as _db
            new_val = not config.AUTO_COMPOUNDING
            _db.set_state("auto_compounding", "True" if new_val else "False")
            if "AUTO_COMPOUNDING" in config._CONFIG_CACHE:
                del config._CONFIG_CACHE["AUTO_COMPOUNDING"]
            status_text, reply_markup = self._generate_settings_data()
            self._edit_message_text(status_text, msg_id, reply_markup)
        elif action == "toggle_mtf":
            import database as _db
            new_val = not config.MTF_TREND_ALIGN_ENABLED
            _db.set_state("mtf_trend_align_enabled", "True" if new_val else "False")
            if "MTF_TREND_ALIGN_ENABLED" in config._CONFIG_CACHE:
                del config._CONFIG_CACHE["MTF_TREND_ALIGN_ENABLED"]
            status_text, reply_markup = self._generate_settings_data()
            self._edit_message_text(status_text, msg_id, reply_markup)
        elif action == "toggle_eq":
            import database as _db
            new_val = not config.EQUITY_CURVE_FILTER_ENABLED
            _db.set_state("equity_curve_filter_enabled", "True" if new_val else "False")
            if "EQUITY_CURVE_FILTER_ENABLED" in config._CONFIG_CACHE:
                del config._CONFIG_CACHE["EQUITY_CURVE_FILTER_ENABLED"]
            status_text, reply_markup = self._generate_settings_data()
            self._edit_message_text(status_text, msg_id, reply_markup)
        elif action == "toggle_conf":
            import database as _db
            new_val = not getattr(config, "CONFIRMATION_MODE", False)
            _db.set_state("confirmation_mode", "true" if new_val else "false")
            if "CONFIRMATION_MODE" in config._CONFIG_CACHE:
                del config._CONFIG_CACHE["CONFIRMATION_MODE"]
            status_text, reply_markup = self._generate_settings_data()
            self._edit_message_text(status_text, msg_id, reply_markup)
        elif action == "retrain_ml":
            self._cmd_retrain()
        elif action == "toggle_trail_type":
            import database as _db
            curr_type = getattr(config, "TRAILING_STOP_TYPE", "atr")
            new_type = "step" if curr_type == "atr" else "atr"
            _db.set_state("trailing_stop_type", new_type)
            if "TRAILING_STOP_TYPE" in config._CONFIG_CACHE:
                del config._CONFIG_CACHE["TRAILING_STOP_TYPE"]
            status_text, reply_markup = self._generate_settings_data()
            self._edit_message_text(status_text, msg_id, reply_markup)
        elif action.startswith("inc_trade:"):
            delta = float(action[10:])
            import database as _db
            new_val = config.TRADE_THRESHOLD + delta
            _db.set_state("trade_threshold", str(new_val))
            if "TRADE_THRESHOLD" in config._CONFIG_CACHE:
                del config._CONFIG_CACHE["TRADE_THRESHOLD"]
            status_text, reply_markup = self._generate_settings_data()
            self._edit_message_text(status_text, msg_id, reply_markup)
        elif action.startswith("inc_tg:"):
            delta = float(action[7:])
            import database as _db
            new_val = config.TELEGRAM_THRESHOLD + delta
            _db.set_state("telegram_threshold", str(new_val))
            if "TELEGRAM_THRESHOLD" in config._CONFIG_CACHE:
                del config._CONFIG_CACHE["TELEGRAM_THRESHOLD"]
            status_text, reply_markup = self._generate_settings_data()
            self._edit_message_text(status_text, msg_id, reply_markup)
        elif action == "open":
            open_text, reply_markup = self._generate_open_data()
            self._edit_message_text(open_text, msg_id, reply_markup)
        elif action == "pause":
            self._cmd_pause()
        elif action == "resume":
            self._cmd_resume()
        elif action == "human":
            self._cmd_human_on()
        elif action == "scalp":
            self._cmd_human_off()
        elif action.startswith("close:"):
            trade_id_str = action[6:]
            self._cmd_close([trade_id_str])
        elif action.startswith("close_trade_"):
            trade_id_str = action[12:]
            self._cmd_close([trade_id_str])
        elif action.startswith("be_trade_"):
            trade_id_str = action[9:]
            try:
                trade_id = int(trade_id_str)
            except ValueError:
                self.send_fn("❌ Geçersiz işlem ID.")
                return
            
            import database as _db
            trade = _db.get_trade_by_id(trade_id)
            if not trade:
                self.send_fn(f"❌ İşlem #{trade_id} bulunamadı.")
                return
            if trade.get("status") == "closed":
                self.send_fn(f"⚠️ İşlem #{trade_id} zaten kapatılmış.")
                return
                
            entry = float(trade.get("entry_price") or trade.get("entry") or 0.0)
            if entry <= 0:
                self.send_fn("❌ Giriş fiyatı geçersiz, stop loss güncellenemedi.")
                return
                
            try:
                with _db.get_conn() as conn:
                    conn.execute("UPDATE trades SET stop_loss = ? WHERE id = ?", (entry, trade_id))
                    conn.commit()
                self.send_fn(f"🔒 #{trade_id} {trade.get('symbol')} stop loss değeri giriş fiyatı olan ${entry:.4f} seviyesine çekildi (Breakeven).")
            except Exception as e:
                self.send_fn(f"❌ Breakeven güncelleme hatası: {e}")
        elif action.startswith("force:"):
            candidate_id_str = action[6:]
            try:
                candidate_id = int(candidate_id_str)
            except ValueError:
                self.send_fn("❌ Geçersiz aday ID.")
                return
            
            import json
            import database as _db
            from core.data_layer import SignalData
            
            cand = _db.get_candidate_by_id(candidate_id)
            if not cand:
                self.send_fn(f"❌ Aday #{candidate_id} bulunamadı.")
                return
                
            if cand.get("decision") == "EXECUTED":
                self.send_fn(f"⚠️ Aday #{candidate_id} zaten işleme sokulmuş.")
                return
                
            self.send_fn(f"⏳ Aday #{candidate_id} ({cand.get('symbol')}) için zorla trade açılıyor...")
            
            try:
                sig = SignalData()
                sig.symbol = cand.get("symbol")
                sig.side = cand.get("side") or cand.get("direction") or "LONG"
                sig.direction = cand.get("direction") or cand.get("side") or "LONG"
                sig.entry_price = cand.get("entry_price") or cand.get("entry") or 0.0
                sig.stop_loss = cand.get("stop_loss") or cand.get("sl") or 0.0
                sig.tp1 = cand.get("tp1") or 0.0
                sig.tp2 = cand.get("tp2") or 0.0
                sig.tp3 = cand.get("tp3") or 0.0
                sig.setup_quality = cand.get("setup_quality") or "B"
                sig.final_score = cand.get("final_score") or cand.get("score") or 0.0
                sig.confidence = 0.8
                sig.reason = "Manual Force Trade"
                sig.source = "telegram_force"
                sig.leverage_suggestion = cand.get("leverage_suggestion") or cand.get("leverage") or 10
                sig.max_loss = cand.get("max_loss") or cand.get("risk_amount") or 0.0
                sig.risk_percent = cand.get("risk_pct") or 1.0
                
                meta_str = cand.get("metadata", "{}")
                if isinstance(meta_str, str):
                    try:
                        sig.metadata = json.loads(meta_str)
                    except Exception:
                        sig.metadata = {}
                else:
                    sig.metadata = meta_str or {}
                    
                # Open Trade
                trade_id = None
                if config.EXECUTION_MODE == "live":
                    from core.live_execution import LiveExecutionEngine
                    engine = LiveExecutionEngine()
                    trade_id = engine.open_live_trade(sig)
                else:
                    from execution_engine import ExecutionEngine
                    engine = ExecutionEngine()
                    trade_id = engine.process_signal(sig)
                    
                if trade_id:
                    _db.update_candidate_status(candidate_id, decision="EXECUTED", linked_trade_id=trade_id)
                    
                    # Notify
                    trade_dict = _db.get_trade_by_id(trade_id)
                    if trade_dict:
                        from telegram_delivery import format_trade_open
                        msg_text = format_trade_open(dict(trade_dict))
                        self.send_fn(msg_text)
                    else:
                        self.send_fn(f"✅ Trade #{trade_id} başarıyla açıldı!")
                else:
                    self.send_fn("❌ Trade açılamadı (Engine trade_id dönmedi).")
            except Exception as e:
                logger.exception("Force open error:")
                self.send_fn(f"❌ Force open hatası: {e}")
        elif action.startswith("mute:"):
            symbol = action[5:].strip()
            try:
                import database as _db
                _db.mute_coin(symbol, 4.0)
                self.send_fn(f"🔕 {symbol} coini 4 saat boyunca sessize alındı.")
            except Exception as e:
                self.send_fn(f"❌ Sessize alma hatası: {e}")
        elif action.startswith("appr_cand_"):
            candidate_id_str = action[10:]
            try:
                candidate_id = int(candidate_id_str)
            except ValueError:
                self.send_fn("❌ Geçersiz aday ID.")
                return
            
            import json
            import database as _db
            from core.data_layer import SignalData
            
            cand = _db.get_candidate_by_id(candidate_id)
            if not cand:
                self.send_fn(f"❌ Aday #{candidate_id} bulunamadı.")
                return
                
            if cand.get("decision") == "EXECUTED":
                self.send_fn(f"⚠️ Aday #{candidate_id} zaten işleme sokulmuş.")
                return
                
            self.send_fn(f"⏳ Aday #{candidate_id} ({cand.get('symbol')}) onaylandı! İşlem açılıyor...")
            
            try:
                sig = SignalData()
                sig.symbol = cand.get("symbol")
                sig.side = cand.get("side") or cand.get("direction") or "LONG"
                sig.direction = cand.get("direction") or cand.get("side") or "LONG"
                sig.entry_price = cand.get("entry_price") or cand.get("entry") or 0.0
                sig.stop_loss = cand.get("stop_loss") or cand.get("sl") or 0.0
                sig.tp1 = cand.get("tp1") or 0.0
                sig.tp2 = cand.get("tp2") or 0.0
                sig.tp3 = cand.get("tp3") or 0.0
                sig.setup_quality = cand.get("setup_quality") or "B"
                sig.final_score = cand.get("final_score") or cand.get("score") or 0.0
                sig.confidence = 0.8
                sig.reason = "Manual Approval via Telegram"
                sig.source = "telegram_approval"
                sig.leverage_suggestion = cand.get("leverage_suggestion") or cand.get("leverage") or 10
                sig.max_loss = cand.get("max_loss") or cand.get("risk_amount") or 0.0
                sig.risk_percent = cand.get("risk_pct") or 1.0
                
                meta_str = cand.get("metadata", "{}")
                if isinstance(meta_str, str):
                    try:
                        sig.metadata = json.loads(meta_str)
                    except Exception:
                        sig.metadata = {}
                else:
                    sig.metadata = meta_str or {}
                    
                # Open Trade
                trade_id = None
                if config.EXECUTION_MODE == "live":
                    from core.live_execution import LiveExecutionEngine
                    engine = LiveExecutionEngine()
                    trade_id = engine.open_live_trade(sig)
                else:
                    from execution_engine import ExecutionEngine
                    engine = ExecutionEngine()
                    trade_id = engine.process_signal(sig)
                    
                if trade_id:
                    _db.update_candidate_status(candidate_id, decision="EXECUTED", linked_trade_id=trade_id)
                    
                    # Save signal event as EXECUTED
                    try:
                        _db.save_signal_event(candidate_id, "EXECUTED", symbol=sig.symbol, reject_reason=f"Approved manual trade_id={trade_id}")
                    except Exception:
                        pass
                    
                    # Edit message text to indicate approval
                    orig_text = msg.get("text", "")
                    updated_text = f"✅ <b>MANÜEL OLARAK ONAYLANDI VE AÇILDI</b>\n\n{orig_text}"
                    self._edit_message_text(updated_text, msg_id, reply_markup=None)
                    
                    # Notify open
                    trade_dict = _db.get_trade_by_id(trade_id)
                    if trade_dict:
                        from telegram_delivery import format_trade_open
                        msg_text = format_trade_open(dict(trade_dict))
                        self.send_fn(msg_text)
                    else:
                        self.send_fn(f"✅ Trade #{trade_id} başarıyla açıldı!")
                else:
                    self.send_fn("❌ Trade açılamadı (Engine trade_id dönmedi).")
            except Exception as e:
                logger.exception("Confirmation approval error:")
                self.send_fn(f"❌ Onaylama hatası: {e}")
        elif action.startswith("veto_cand_"):
            candidate_id_str = action[10:]
            try:
                candidate_id = int(candidate_id_str)
            except ValueError:
                self.send_fn("❌ Geçersiz aday ID.")
                return
            
            import database as _db
            cand = _db.get_candidate_by_id(candidate_id)
            if not cand:
                self.send_fn(f"❌ Aday #{candidate_id} bulunamadı.")
                return
                
            try:
                _db.update_candidate_status(candidate_id, decision="VETOED", reject_reason="Manually vetoed on confirmation gate")
                try:
                    _db.save_signal_event(candidate_id, "VETOED", symbol=cand.get("symbol"), reject_reason="Manually vetoed on confirmation gate")
                except Exception:
                    pass
                    
                # Edit message text to indicate veto
                orig_text = msg.get("text", "")
                updated_text = f"❌ <b>İŞLEM MANÜEL VETO EDİLDİ (İPTAL)</b>\n\n{orig_text}"
                self._edit_message_text(updated_text, msg_id, reply_markup=None)
                self.send_fn(f"🚫 Aday #{candidate_id} ({cand.get('symbol')}) manüel olarak iptal edildi (veto).")
            except Exception as e:
                self.send_fn(f"❌ Veto etme hatası: {e}")

    def _answer_callback_query(self, callback_query_id: str, text: Optional[str] = None):
        url = f"https://api.telegram.org/bot{self.token}/answerCallbackQuery"
        payload = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        try:
            requests.post(url, json=payload, timeout=_TIMEOUT)
        except Exception as e:
            logger.warning(f"answerCallbackQuery hatası: {e}")

    def _edit_message_text(self, text: str, message_id: int, reply_markup: Optional[dict] = None) -> bool:
        url = f"https://api.telegram.org/bot{self.token}/editMessageText"
        payload = {
            "chat_id": self.chat_id,
            "message_id": message_id,
            "text": text[:4096],
            "parse_mode": "HTML"
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        try:
            resp = requests.post(url, json=payload, timeout=_TIMEOUT)
            return resp.status_code == 200
        except Exception as e:
            logger.warning(f"editMessageText hatası: {e}")
            return False

    def _get_status_markup(self) -> dict:
        return {
            "inline_keyboard": [
                [
                    {"text": "🔄 Yenile", "callback_data": "cmd:refresh_status"},
                    {"text": "📈 Açık İşlemler", "callback_data": "cmd:open"}
                ],
                [
                    {"text": "⏸ Duraklat", "callback_data": "cmd:pause"},
                    {"text": "▶️ Başlat", "callback_data": "cmd:resume"}
                ]
            ]
        }

    def _register_commands(self):
        url = f"https://api.telegram.org/bot{self.token}/setMyCommands"
        payload = {
            "commands": [
                {"command": "help", "description": "Yardım menüsü ve komut listesi"},
                {"command": "status", "description": "Botun genel durumunu ve kârını özetler"},
                {"command": "settings", "description": "İnteraktif ayarlar panelini açar"},
                {"command": "heatmap", "description": "Son 30 günlük kâr/zarar ısı haritasını gönderir"},
                {"command": "open", "description": "Açık işlemleri listeler ve kapatma imkanı sunar"},
                {"command": "health", "description": "Sistem sağlığı ve kaynak durumunu kontrol eder"},
                {"command": "stats", "description": "Tüm zamanların performans özetini çıkarır"},
                {"command": "trades", "description": "Kapanan son 5 işlemi listeler"},
                {"command": "ghost", "description": "AI öğrenme istatistiklerini gösterir"},
                {"command": "daily", "description": "Bugünün işlem ve kâr özetini çıkarır"},
                {"command": "mode", "description": "Güncel çalışma modunu gösterir"},
                {"command": "pause", "description": "Yeni işlemleri duraklatır"},
                {"command": "resume", "description": "Botu tekrar aktif eder"},
                {"command": "human", "description": "İnsan Modu: Kaliteli ve az işlemler"},
                {"command": "scalp", "description": "Scalp Modu: Agresif tarama ve sık işlemler"},
                {"command": "paper", "description": "Sanal Para Modu (Paper Trading)"},
                {"command": "live", "description": "Gerçek Para Modu (Live Trading)"},
                {"command": "ml", "description": "Yapay Zeka (ML) durum ve istatistiklerini gösterir"},
                {"command": "retrain", "description": "ML modelini arka planda sıfırdan eğitir"},
                {"command": "diagnose", "description": "Sistem derin teşhis ve analiz raporu"},
                {"command": "teshis", "description": "Sistem derin teşhis ve analiz raporu"},
                {"command": "friday_voice", "description": "Friday'dan sesli durum raporu alır"},
                {"command": "friday_ses", "description": "Friday'dan sesli durum raporu alır"}
            ]
        }
        try:
            resp = requests.post(url, json=payload, timeout=_TIMEOUT)
            if resp.status_code == 200 and resp.json().get("ok"):
                logger.info("Telegram komut listesi menüye başarıyla kaydedildi.")
            else:
                logger.warning(f"Telegram komut listesi kaydedilemedi: {resp.text}")
        except Exception as e:
            logger.warning(f"Telegram komut listesi kaydı sırasında hata: {e}")

    def _get_help_markup(self) -> dict:
        return {
            "inline_keyboard": [
                [
                    {"text": "📊 Durum", "callback_data": "cmd:status"},
                    {"text": "📈 Açık İşlemler", "callback_data": "cmd:open"}
                ],
                [
                    {"text": "⚙️ Ayarlar", "callback_data": "cmd:refresh_settings"},
                    {"text": "🧠 İnsan Modu", "callback_data": "cmd:human"},
                    {"text": "⚡ Scalp Modu", "callback_data": "cmd:scalp"}
                ]
            ]
        }

    def _get_open_markup(self, open_trades: list) -> Optional[dict]:
        if not open_trades:
            return None
        buttons = []
        for t in open_trades:
            tid = t.get("id")
            sym = t.get("symbol", "?")
            side = (t.get("side") or t.get("direction", "?"))[:1]
            buttons.append([
                {"text": f"❌ Kapat #{tid} {sym} ({side})", "callback_data": f"cmd:close:{tid}"}
            ])
        buttons.append([{"text": "🔄 Yenile", "callback_data": "cmd:open"}])
        return {"inline_keyboard": buttons}

    def _cmd_help(self):
        self.send_fn(
            "🤖 <b>AurvexAI Yönetim Merkezi</b>\n\n"
            "Merhaba! Ben senin yapay zeka destekli alım-satım asistanın. Sistemin kalbini buradan kontrol edebilirsin. İşte yapabileceklerim:\n\n"
            "📊 <b>Gözlem ve Raporlama</b>\n"
            "🔹 <code>/health</code> — Sistem sağlığını, RAM ve veritabanı durumunu kontrol eder.\n"
            "🔹 <code>/status</code> — Sistemin genel sağlığını, aktif modunu ve kârını özetler.\n"
            "🔹 <code>/open</code> — Şu an açık olan işlemlerini (giriş, stop, kâr) gösterir ve kapatma butonu sunar.\n"
            "🔹 <code>/stats</code> — Tüm zamanların performans özetini (Win Rate vb.) çıkarır.\n"
            "🔹 <code>/daily</code> — Bugüne özel kaç işlem açıldığını ve güncel kâr/zararı listeler.\n"
            "🔹 <code>/balance</code> — Kasanın büyüme oranını detaylıca gösterir.\n"
            "🔹 <code>/trades</code> — Kapanan son 5 işlemi (Neden kapandığıyla birlikte) listeler.\n"
            "🔹 <code>/ghost</code> — Yapay zekanın (Ghost Learning) arka planda ne kadar öğrendiğini gösterir.\n"
            "🔹 <code>/ml</code> — Yapay Zeka (ML) durum ve tahmin parametrelerini listeler.\n"
            "🔹 <code>/retrain</code> — ML modelini veritabanındaki son işlemlerle manuel olarak yeniden eğitir.\n\n"
            "⚙️ <b>Strateji ve Mod Değişimi</b>\n"
            "🔹 <code>/mode</code> — Şu an hangi stratejide çalıştığımızı söyler.\n"
            "🔹 <code>/settings</code> — İnteraktif ayarlar panelini açar.\n"
            "🔹 <code>/set [key] [val]</code> — Dinamik parametre değiştirir (Örn: <code>/set trade_threshold 55.0</code>).\n"
            "🔹 <code>/close [id]</code> — Belirtilen ID'ye sahip açık pozisyonu anında kapatır.\n"
            "🔹 <code>/human</code> — İnsan Modu: Az ama öz, sadece en kaliteli sinyallere girer (A+/S).\n"
            "🔹 <code>/scalp</code> — Scalp Modu: Piyasayı agresif tarar, çok işleme girer ve hızlı çıkar.\n"
            "🔹 <code>/paper</code> — Sanal Para Modu: Kendi sanal kasasıyla risksiz işlem açar.\n"
            "🔹 <code>/live</code> — Canlı İşlem Modu: Gerçek Binance bakiyenizle gerçek işlem açar.\n\n"
            "🛑 <b>Acil Durum Kontrolleri</b>\n"
            "🔹 <code>/pause</code> — Piyasalar çok riskliyse botu duraklat. (Açık işlemler takip edilir, yeni işleme girilmez).\n"
            "🔹 <code>/resume</code> — Her şey yolundaysa botu tekrar ava çıkar.\n"
            "🔹 <code>/finish</code> — Mevcut işlemler kapandığı an botu tamamen uykuya al.\n\n"
            "💡 <i>İpucu: Komutlara tıklayarak veya aşağıdaki butonları kullanarak işlem yapabilirsin!</i>",
            reply_markup=self._get_help_markup()
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
            f"🏥 <b>Sistem Sağlık Raporu</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"⏱ <b>Kesintisiz Çalışma:</b> {h} Saat {m} Dakika\n"
            f"💾 <b>Veritabanı Boyutu:</b> {db_size:.1f} MB\n"
            f"🔄 <b>Veritabanı WAL:</b> {wal_size:.1f} MB\n"
            f"⚡ <b>DB Gecikmesi (Ping):</b> {db_ping} ms\n"
            f"🧠 <b>RAM Kullanımı:</b> {ram_text}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"✅ <i>Tüm arka plan servisleri ve veritabanı aktif şekilde çalışıyor.</i>"
        )

    def _cmd_diagnose(self):
        import os
        import time
        import subprocess
        import database
        
        # System Uptime
        uptime = int(time.time() - self._start_time)
        h, rem = divmod(uptime, 3600)
        m = rem // 60
        
        # RAM and Disk status
        ram_text = "N/A"
        disk_text = "N/A"
        try:
            import psutil
            # RAM
            vm = psutil.virtual_memory()
            ram_used_gb = vm.used / (1024 ** 3)
            ram_total_gb = vm.total / (1024 ** 3)
            ram_text = f"{ram_used_gb:.1f} / {ram_total_gb:.1f} GB (%{vm.percent:.1f})"
            
            # Disk
            du = psutil.disk_usage('.')
            disk_used_gb = du.used / (1024 ** 3)
            disk_total_gb = du.total / (1024 ** 3)
            disk_text = f"{disk_used_gb:.1f} / {disk_total_gb:.1f} GB (%{du.percent:.1f})"
        except ImportError:
            # Fallback for Linux when psutil is not available
            if os.name != 'nt':
                try:
                    df_res = subprocess.run(["df", "-h", "."], capture_output=True, text=True, timeout=2)
                    if df_res.returncode == 0:
                        lines = df_res.stdout.strip().split('\n')
                        if len(lines) > 1:
                            parts = lines[1].split()
                            if len(parts) >= 5:
                                disk_text = f"Used: {parts[2]}, Total: {parts[1]} ({parts[4]})"
                    free_res = subprocess.run(["free", "-m"], capture_output=True, text=True, timeout=2)
                    if free_res.returncode == 0:
                        lines = free_res.stdout.strip().split('\n')
                        for line in lines:
                            if line.startswith("Mem:"):
                                parts = line.split()
                                total_mb = int(parts[1])
                                used_mb = int(parts[2])
                                pct = (used_mb / total_mb) * 100
                                ram_text = f"{used_mb / 1024:.1f} / {total_mb / 1024:.1f} GB (%{pct:.1f})"
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"Error reading resource stats: {e}")
            
        # Physical DB position, size, WAL size
        db_path = getattr(config, "DB_PATH", "trading.db")
        db_abs = os.path.abspath(db_path)
        db_size_mb = 0.0
        wal_size_mb = 0.0
        try:
            if os.path.exists(db_path):
                db_size_mb = os.path.getsize(db_path) / (1024 * 1024)
            if os.path.exists(db_path + "-wal"):
                wal_size_mb = os.path.getsize(db_path + "-wal") / (1024 * 1024)
        except Exception as e:
            logger.warning(f"Error reading DB sizes: {e}")
            
        # Active Python processes
        py_processes = []
        try:
            import psutil
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    cmd = proc.info.get('cmdline') or []
                    cmd_str = " ".join(cmd)
                    if 'python' in proc.info.get('name', '').lower() or any('python' in arg.lower() for arg in cmd):
                        if any(k in cmd_str for k in ['app.py', 'scalp_bot.py', 'telegram_manager.py', 'run']):
                            py_processes.append(f"• PID {proc.pid}: <code>{cmd_str[:60]}...</code>")
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    pass
        except ImportError:
            # Fallback using ps command on Linux
            if os.name != 'nt':
                try:
                    ps_res = subprocess.run(["ps", "aux"], capture_output=True, text=True, timeout=3)
                    if ps_res.returncode == 0:
                        lines = ps_res.stdout.strip().split('\n')
                        for line in lines:
                            if 'python' in line.lower() and any(k in line for k in ['app.py', 'scalp_bot.py', 'telegram_manager.py']):
                                parts = line.split()
                                if len(parts) >= 11:
                                    pid = parts[1]
                                    cmd = " ".join(parts[10:])
                                    py_processes.append(f"• PID {pid}: <code>{cmd[:60]}...</code>")
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"Error listing python processes: {e}")
            
        if not py_processes:
            py_processes.append("• <i>Aktif Python süreci bulunamadı veya yetki yetersiz.</i>")
            
        # Running Docker processes
        docker_containers = []
        try:
            res = subprocess.run(["docker", "ps", "--format", "{{.Names}} ({{.Status}})"], capture_output=True, text=True, timeout=5)
            if res.returncode == 0:
                lines = [line.strip() for line in res.stdout.strip().split('\n') if line.strip()]
                for line in lines[:5]:
                    docker_containers.append(f"🐳 {line}")
            else:
                docker_containers.append("🐳 <i>Docker komutu hata verdi veya çalışmıyor.</i>")
        except Exception:
            docker_containers.append("🐳 <i>Docker bulunamadı.</i>")
            
        # IP Whitelist status
        allowed_ips_env = os.getenv("ALLOWED_IPS", "").strip()
        if allowed_ips_env:
            whitelist_status = f"✅ Etkin\n🔑 <b>İzin Verilen IP'ler:</b> <code>{allowed_ips_env}</code>"
        else:
            whitelist_status = "❌ Devre Dışı (Tüm IP'lere Açık)"
            
        # Format and send report
        proc_str = "\n".join(py_processes[:5])
        docker_str = "\n".join(docker_containers[:5])
        
        report = (
            f"🛠 <b>Sistem Teşhis Raporu (/diagnose)</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"⏱ <b>Kesintisiz Çalışma (Uptime):</b> {h} Saat {m} Dakika\n"
            f"🧠 <b>RAM Durumu:</b> {ram_text}\n"
            f"💾 <b>Disk Durumu:</b> {disk_text}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📂 <b>DB Dosya Konumu:</b>\n<code>{db_abs}</code>\n"
            f"📊 <b>DB Boyutu:</b> {db_size_mb:.2f} MB | <b>WAL Boyutu:</b> {wal_size_mb:.2f} MB\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"🔒 <b>IP Whitelist Durumu:</b> {whitelist_status}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"🐍 <b>Aktif Python Süreçleri:</b>\n{proc_str}\n\n"
            f"🐳 <b>Docker Konteynerleri:</b>\n{docker_str}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"⚡ <i>Teşhis başarıyla tamamlandı.</i>"
        )
        self.send_fn(report)

    def _cmd_status(self):
        text, markup = self._generate_status_data()
        self.send_fn(text, reply_markup=markup)

    def _generate_status_data(self) -> tuple[str, dict]:
        import database
        bal     = database.get_active_balance() or 0
        exec_mode = getattr(config, "EXECUTION_MODE", "paper")
        bal_label = "Canlı Cüzdan (Binance)" if exec_mode == "live" else "Sanal Kasa"
        init    = getattr(config, "INITIAL_PAPER_BALANCE", 2000.0)
        roi     = ((bal - init) / init * 100) if init else 0
        open_t  = database.get_open_trades(exec_mode)
        stats   = database.get_dashboard_stats(exec_mode)
        uptime  = int(time.time() - self._start_time)
        h, rem  = divmod(uptime, 3600)
        m       = rem // 60

        try:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            with database.get_conn() as conn:
                today_pnl = conn.execute("""
                    SELECT COALESCE(SUM(net_pnl), 0) FROM trades
                    WHERE LOWER(status)='closed' AND DATE(close_time)=? AND is_valid_for_stats=1 AND environment=?
                """, (today, exec_mode)).fetchone()[0] or 0
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

        text = (
            f"📈 <b>Sistem Durum Raporu</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"🔍 <b>Motor Durumu:</b> {paused}\n"
            f"🎯 <b>Çalışma Modu:</b> {'🧠 İnsan (Özenli)' if config.HUMAN_MODE else '⚡ Scalp (Agresif)'} | {exec_mode.upper()}\n"
            f"🌊 <b>Piyasa Yönü (Rejim):</b> {regime}\n"
            f"⏱ <b>Kesintisiz Çalışma:</b> {h} Saat, {m} Dakika\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"💰 <b>{bal_label}:</b> ${bal:.2f} (Büyüme: {roi:+.1f}%)\n"
            f"📅 <b>Bugünün Kârı:</b> ${today_pnl:+.2f}\n"
            f"📊 <b>Toplam Kâr:</b> ${stats.get('total_pnl', 0):+.2f}\n"
            f"👻 <b>YZ Öğrenme Havuzu:</b> {ghost_n} simülasyon\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"🟢 <b>Açık İşlemler ({len(open_t)} adet):</b>{open_lines}\n\n"
            f"💡 <i>Detaylar için /stats veya /open yazabilirsin.</i>"
        )
        return text, self._get_status_markup()


    def _cmd_stats(self):
        import database
        exec_mode = getattr(config, "EXECUTION_MODE", "paper")
        stats = database.get_dashboard_stats(exec_mode)
        total = stats.get("total_trades", 0)
        wins  = stats.get("win_trades", 0)
        loss  = stats.get("loss_trades", 0)
        pnl   = stats.get("total_pnl", 0)
        wr    = stats.get("win_rate", 0)
        
        details = database.get_active_balance_details()
        bal = details.get("total", 0.0)
        init  = getattr(config, "INITIAL_PAPER_BALANCE", 2000.0)
        roi   = ((bal - init) / init * 100) if init else 0
        
        mode_label = "Canlı Cüzdan" if exec_mode == "live" else "Sanal Kasa"
        sharpe = stats.get("sharpe_ratio", 0.0)
        sortino = stats.get("sortino_ratio", 0.0)
        self.send_fn(
            f"📊 <b>Genel Performans İstatistikleri ({exec_mode.upper()})</b>\n\n"
            f"Bu veriler botun şu ama kadar gösterdiği tüm başarı oranını özetler:\n\n"
            f"🔸 <b>Toplam Kapanan İşlem:</b> {total} adet\n"
            f"🔸 <b>Başarı Oranı (Kazanılan/Kaybedilen):</b> {wins} Başarılı / {loss} Zararlı\n"
            f"🔸 <b>Win Rate (Kazanma Yüzdesi):</b> %{wr:.1f}\n"
            f"🔸 <b>Kümülatif Net Kâr:</b> ${pnl:+.2f}\n"
            f"🔸 <b>Sharpe Oranı (Sharpe):</b> {sharpe}\n"
            f"🔸 <b>Sortino Oranı (Sortino):</b> {sortino}\n\n"
            f"💼 <b>Kasa Durumu:</b>\n"
            f"🔸 Başlangıç: ${init:.2f}\n"
            f"🔸 Güncel {mode_label}: ${bal:.2f}\n"
            f"🔸 Toplam Büyüme (ROI): %{roi:+.1f}\n\n"
            f"💡 <i>Not: Yüksek kâr faktörü, düşük win rate'den daha önemlidir. Bot kârı uzatıp zararı erken keser.</i>"
        )

    def _cmd_trades(self):
        import database
        exec_mode = getattr(config, "EXECUTION_MODE", "paper")
        trades = database.get_recent_trades(5, environment=exec_mode)
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
        self.send_fn("📜 <b>Kapanan Son 5 İşlemin Analizi</b>\n\n" + "\n\n".join(lines) + "\n\n💡 <i>Not: Neden kapandığına (reason) bakarak botun hangi stratejiyi uyguladığını (SL, TP, Trail) görebilirsin.</i>")

    def _cmd_balance(self):
        import database
        details = database.get_active_balance_details()
        mode_str = "Live (Gerçek)" if details.get("execution_mode") == "live" else "Sanal (Paper)"
        bal = details.get("total", 0.0)
        avail = details.get("available", 0.0)
        init = getattr(config, "INITIAL_PAPER_BALANCE", 2000.0)
        diff = bal - init
        try:
            with database.get_conn() as conn:
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                row   = conn.execute(
                    "SELECT COALESCE(SUM(net_pnl),0) FROM trades WHERE DATE(close_time)=? AND status='closed' AND is_valid_for_stats=1 AND environment=?",
                    (today, details.get("execution_mode", "paper"))
                ).fetchone()
            today_pnl = float(row[0]) if row else 0.0
        except Exception:
            today_pnl = 0.0
        self.send_fn(
            f"💳 <b>Bakiye ve Kazanç Özeti [{mode_str}]</b>\n\n"
            f"Sisteme tanımlı başlangıç kasanız ve şu anki büyüme:\n\n"
            f"🔹 Başlangıç Kasası: ${init:.2f}\n"
            f"🔹 <b>Toplam Bakiye:</b> ${bal:.2f}\n"
            f"🔹 <b>Kullanılabilir Bakiye:</b> ${avail:.2f}\n"
            f"🔹 Toplam Kâr/Zarar: ${diff:+.2f}\n"
            f"🔹 Sadece Bugün Kazanılan: ${today_pnl:+.2f}\n\n"
            f"💡 <i>Canlı ticarete (Live Trading) geçtiğinizde burada gerçek Binance cüzdanınızı göreceksiniz.</i>"
        )


    def _cmd_open(self):
        text, markup = self._generate_open_data()
        self.send_fn(text, reply_markup=markup)

    def _generate_open_data(self) -> tuple[str, dict]:
        import database
        exec_mode = getattr(config, "EXECUTION_MODE", "paper")
        trades = database.get_open_trades(exec_mode)
        if not trades:
            return "Açık trade yok.", {"inline_keyboard": [[{"text": "🔄 Yenile", "callback_data": "cmd:open"}]]}
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
                f"🪙 <b>{sym}</b> ({side}) {hold}\n"
                f"   ├ Giriş Fiyatı: ${ep:.4f}\n"
                f"   ├ Stop Loss: ${sl:.4f} (Korunuyor)\n"
                f"   ├ Hedef TP1: ${tp1:.4f}\n"
                f"   └ <b>Anlık Durum (PnL):</b> {upnl:+.2f}$"
            )
        text = f"🟢 <b>Aktif Açık İşlemler ({len(trades)} adet)</b>\n\n" + "\n\n".join(lines)
        return text, self._get_open_markup(trades)

    def _cmd_close(self, args: list):
        if not args:
            self.send_fn("Kapatılacak işlem ID'sini belirtin. Örnek: <code>/close 15</code>")
            return
        try:
            trade_id = int(args[0])
        except ValueError:
            self.send_fn("Geçersiz işlem ID. ID sayı olmalıdır.")
            return

        import database
        from execution_engine import _get_price, ExecutionEngine

        trade = database.get_trade_by_id(trade_id)
        if not trade:
            self.send_fn(f"İşlem #{trade_id} bulunamadı.")
            return

        if str(trade.get("status", "")).lower() == "closed":
            self.send_fn(f"İşlem #{trade_id} zaten kapatılmış.")
            return

        symbol = trade.get("symbol", "")
        self.send_fn(f"⏳ #{trade_id} {symbol} işlemi kapatılıyor...")

        try:
            exit_price = _get_price(None, symbol)
            if exit_price <= 0:
                from core.market_data import get_current_price
                exit_price = get_current_price(symbol) or float(trade.get("entry_price") or trade.get("entry") or 0)

            engine = ExecutionEngine()
            engine.close_trade(trade, exit_price, reason="manual")
            self.send_fn(f"✅ #{trade_id} {symbol} işlemi başarıyla kapatıldı! Çıkış fiyatı: ${exit_price:.4f}")
        except Exception as e:
            logger.exception("Manual close error:")
            self.send_fn(f"❌ Kapatma hatası: {e}")

    def _cmd_set(self, args: list):
        if not args or len(args) < 2:
            self.send_fn(
                "🛠 <b>Dinamik Parametre Değiştir</b>\n"
                "Kullanım: <code>/set [parametre] [değer]</code>\n\n"
                "Desteklenen parametreler:\n"
                "• <code>trade_threshold</code> (Örn: 55.0)\n"
                "• <code>telegram_threshold</code> (Örn: 35.0)\n"
                "• <code>max_spread_pct</code> (Örn: 0.15)\n"
                "• <code>max_open_trades</code> (Örn: 5)\n"
                "• <code>human_mode</code> (true/false)\n"
                "• <code>execution_mode</code> (live/paper)"
            )
            return

        param_name = args[0].strip().lower()
        raw_val = args[1].strip()

        param_mapping = {
            "trade_threshold": ("trade_threshold", float),
            "telegram_threshold": ("telegram_threshold", float),
            "watchlist_threshold": ("watchlist_threshold", float),
            "data_threshold": ("data_threshold", float),
            "max_spread_pct": ("max_spread_pct", float),
            "max_open_trades": ("max_open_trades", int),
            "human_mode": ("tg_human_mode", lambda v: "True" if v.lower() in ("true", "1", "yes") else "False"),
            "execution_mode": ("tg_execution_mode", lambda v: "live" if v.lower() == "live" else "paper"),
            "confirmation_mode": ("confirmation_mode", lambda v: "true" if v.lower() in ("true", "1", "yes") else "false"),
            "trailing_stop_type": ("trailing_stop_type", lambda v: "step" if v.lower() == "step" else "atr"),
        }

        if param_name not in param_mapping:
            self.send_fn(f"❌ Bilinmeyen parametre: {param_name}")
            return

        db_key, cast_fn = param_mapping[param_name]
        try:
            casted_val = cast_fn(raw_val)
            import database
            database.set_state(db_key, str(casted_val))
            self.send_fn(f"✅ Başarılı! <b>{param_name}</b> değeri <b>{casted_val}</b> olarak güncellendi.")
        except Exception as e:
            self.send_fn(f"❌ Değer dönüştürme/yazma hatası: {e}")

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
                f"👻 <b>Yapay Zeka & Ghost Learning 2.0 Durumu</b>\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"Bu modül botun beynidir. İşleme girmese bile sanal sinyaller üretip sonuçlarından ders çıkarır.\n\n"
                f"🧠 <b>Toplanan Veri Seti:</b> {gs_total} sinyal incelendi.\n"
                f"⚙️ <b>İşlenen (Simüle):</b> {gs_sim} | Bekleyen: {gs_total - gs_sim}\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"📊 <b>Öğrenme Başarısı:</b>\n"
                f"🔹 Doğru Tahmin (WIN): {gr_wins} adet\n"
                f"🔹 Yanlış Tahmin (LOSS): {gr_loss} adet\n"
                f"🔹 Sanal Win Rate: %{vwr:.1f}\n"
                f"🔹 Ortalama Kazanç Çarpanı: {gr_avg_r:.2f}R\n\n"
                f"🛠 Bekleyen Strateji Önerisi: {pending_sugg} adet"
            )
        except Exception as e:
            self.send_fn(f"Ghost bilgisi alinamadi: {e}")

    def _cmd_ml(self):
        try:
            from core.ml_signal_scorer import get_scorer
            scorer = get_scorer()
            status = scorer.get_status()
            
            trained_str = "✅ Eğitildi & Aktif" if status["trained"] else "❌ Eğitilmedi (Cold Start)"
            last_train = status["last_train"] or "N/A"
            if last_train != "N/A":
                try:
                    last_train = last_train.split(".")[0].replace("T", " ")
                except Exception:
                    pass

            top_feats = status["top_features"]
            top_feats_str = ""
            if top_feats:
                for idx, (name, val) in enumerate(top_feats, 1):
                    top_feats_str += f"  {idx}. <b>{name}</b>: <code>{val:.3f}</code>\n"
            else:
                top_feats_str = "  <i>Veri yok</i>\n"

            text = (
                f"🧠 <b>Yapay Zeka (ML) Model İstatistikleri</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 <b>Model Durumu:</b> {trained_str}\n"
                f"📈 <b>Örnek Boyutu:</b> {status['n_samples']} trade sonucu\n"
                f"⏱ <b>Son Eğitim:</b> <code>{last_train}</code>\n"
                f"🎯 <b>Model Gating Eşiği:</b> <code>{status['threshold']}</code>\n"
                f"🛡 <b>ROC-AUC Skoru:</b> <code>{status['cv_accuracy']:.3f}</code>\n"
                f"🔥 <b>Precision @ 70+:</b> <code>{status['precision_at_70']:.3f}</code>\n\n"
                f"🔑 <b>En Önemli Karar Kriterleri (Top 5):</b>\n"
                f"{top_feats_str}"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"<i>Not: Model her 24 saatte bir veya 50 yeni trade kapandığında otomatik olarak arka planda eğitilir.</i>"
            )

            reply_markup = {
                "inline_keyboard": [
                    [{"text": "🔄 Modeli Şimdi Eğit", "callback_data": "cmd:retrain_ml"}]
                ]
            }
            self.send_fn(text, reply_markup=reply_markup)
        except Exception as e:
            self.send_fn(f"ML bilgisi alınamadı: {e}")

    def _cmd_retrain(self):
        try:
            self.send_fn("🔄 Yapay Zeka modeli veritabanındaki son işlemlerle yeniden eğitiliyor, lütfen bekleyin...")
            from core.ml_signal_scorer import train_model
            success = train_model()
            if success:
                from core.ml_signal_scorer import get_scorer
                status = get_scorer().get_status()
                text = (
                    f"✅ <b>Yapay Zeka Modeli Başarıyla Eğitildi!</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📈 <b>Yeni Örnek Sayısı:</b> {status['n_samples']}\n"
                    f"🛡 <b>Yeni ROC-AUC:</b> <code>{status['cv_accuracy']:.3f}</code>\n"
                    f"🔥 <b>Yeni Precision @ 70:</b> <code>{status['precision_at_70']:.3f}</code>\n"
                    f"💡 Model güncellendi ve yeni sinyallerde kullanılmaya başlandı."
                )
                self.send_fn(text)
            else:
                self.send_fn("❌ <b>Model Eğitilemedi!</b>\nYetersiz veri (en az 30 WIN/LOSS işlem gerekli) veya yeni modelin performansı eskisine göre yetersiz kaldığı için model koruma kapısı (ML gating) eğitimi engelledi.")
        except Exception as e:
            self.send_fn(f"Model eğitilirken hata oluştu: {e}")

    def _cmd_daily(self):
        import database
        try:
            exec_mode = getattr(config, "EXECUTION_MODE", "paper")
            with database.get_conn() as conn:
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                row   = conn.execute(
                    """SELECT COUNT(*),
                              SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END),
                              SUM(CASE WHEN net_pnl <= 0 THEN 1 ELSE 0 END),
                              COALESCE(SUM(net_pnl), 0)
                       FROM trades WHERE DATE(close_time)=? AND status='closed' AND is_valid_for_stats=1 AND environment=?""",
                    (today, exec_mode)
                ).fetchone()
            total  = row[0] or 0
            wins   = row[1] or 0
            losses = row[2] or 0
            pnl    = float(row[3] or 0)
            wr     = round(wins / total * 100, 1) if total else 0
            self.send_fn(
                f"📅 <b>Bugünün İşlem Özeti ({today})</b>\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"🔸 <b>Toplam İşlem:</b> {total} adet\n"
                f"🔸 <b>Sonuç:</b> {wins} Galibiyet / {losses} Mağlubiyet\n"
                f"🔸 <b>Kazanma Oranı:</b> %{wr:.1f}\n"
                f"🔸 <b>Günlük Net PnL:</b> ${pnl:+.2f}\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"💡 <i>Veriler anlık olarak güncellenir.</i>"
            )
        except Exception as e:
            self.send_fn(f"Günlük özet alınamadı: {e}")


    def _cmd_settings(self):
        # Invalidate cache on manual query to show fresh data
        for name in ["AUTO_COMPOUNDING", "MTF_TREND_ALIGN_ENABLED", "EQUITY_CURVE_FILTER_ENABLED", "TRADE_THRESHOLD", "TELEGRAM_THRESHOLD", "CONFIRMATION_MODE", "TRAILING_STOP_TYPE"]:
            if name in config._CONFIG_CACHE:
                del config._CONFIG_CACHE[name]
        text, markup = self._generate_settings_data()
        self.send_fn(text, reply_markup=markup)

    def _generate_settings_data(self) -> tuple[str, dict]:
        import database
        comp = config.AUTO_COMPOUNDING
        mtf = config.MTF_TREND_ALIGN_ENABLED
        eq = config.EQUITY_CURVE_FILTER_ENABLED
        trade_thr = config.TRADE_THRESHOLD
        tg_thr = config.TELEGRAM_THRESHOLD
        conf = getattr(config, "CONFIRMATION_MODE", False)
        trail_type = getattr(config, "TRAILING_STOP_TYPE", "atr").upper()

        comp_status = "✅ Eklemli (Compounding)" if comp else "❌ Sabit (Fixed-Size)"
        mtf_status = "✅ Etkin" if mtf else "❌ Devre Dışı"
        eq_status = "✅ Etkin" if eq else "❌ Devre Dışı"
        conf_status = "⏳ Manuel Onay (Confirmation)" if conf else "⚡ Otomatik Giriş"

        text = (
            f"⚙️ <b>AurvexAI İnteraktif Ayarlar Paneli</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔑 <b>Cüzdan Ekleme (Compounding):</b> {comp_status}\n"
            f"📈 <b>MTF Trend Hizalaması:</b> {mtf_status}\n"
            f"🛡 <b>Bakiye Eğrisi Filtresi (Equity):</b> {eq_status}\n"
            f"🛑 <b>İşlem Giriş Modu:</b> {conf_status}\n"
            f"🔄 <b>Trailing Stop Tipi:</b> <b>{trail_type}</b>\n"
            f"🎯 <b>İşlem Eşik Puanı (Trade):</b> <code>{trade_thr:.1f}</code>\n"
            f"📡 <b>Telegram Eşik Puanı (TG):</b> <code>{tg_thr:.1f}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👇 Ayarları değiştirmek için aşağıdaki butonları kullanabilirsiniz."
        )

        markup = {
            "inline_keyboard": [
                [
                    {"text": f"💵 Cüzdan: {'Kapat' if comp else 'Aç'}", "callback_data": "cmd:toggle_comp"},
                    {"text": f"📈 MTF: {'Kapat' if mtf else 'Aç'}", "callback_data": "cmd:toggle_mtf"},
                ],
                [
                    {"text": f"🛡 Equity: {'Kapat' if eq else 'Aç'}", "callback_data": "cmd:toggle_eq"},
                    {"text": f"🛑 Onay Modu: {'Kapat' if conf else 'Aç'}", "callback_data": "cmd:toggle_conf"},
                ],
                [
                    {"text": f"🔄 Trailing Tipi: {'ATR' if trail_type == 'STEP' else 'STEP'}", "callback_data": "cmd:toggle_trail_type"},
                    {"text": "🔄 Yenile", "callback_data": "cmd:refresh_settings"}
                ],
                [
                    {"text": "🎯 Trade -0.5", "callback_data": "cmd:inc_trade:-0.5"},
                    {"text": "🎯 Trade +0.5", "callback_data": "cmd:inc_trade:0.5"},
                ],
                [
                    {"text": "📡 TG -0.5", "callback_data": "cmd:inc_tg:-0.5"},
                    {"text": "📡 TG +0.5", "callback_data": "cmd:inc_tg:0.5"},
                ]
            ]
        }
        return text, markup

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
            f"<code>/human</code> — İnsan moduna geç\n"
            f"<code>/scalp</code> — Scalp moduna geç"
        )

    def _cmd_pause(self):
        self.is_paused = True
        try:
            import database as _db
            _db.set_state("tg_is_paused", "True")
        except Exception:
            pass
        self.send_fn(
            "⏸ <b>SİSTEM DURAKLATILDI (PAUSE)</b>\n\n"
            "Bot şu an yeni piyasa fırsatlarını aramayı ve yeni işlem açmayı tamamen <b>durdurdu</b>.\n\n"
            "💡 Ancak endişelenme! Hali hazırda açık olan işlemlerinin SL, TP ve Kâr alma seviyeleri takip edilmeye devam ediyor.\n\n"
            "Piyasa tehlikesiz göründüğünde botu tekrar işe göndermek için <code>/resume</code> komutunu kullan."
        )

    def _cmd_resume(self):
        self.is_paused = False
        try:
            import database as _db
            _db.set_state("tg_is_paused", "False")
        except Exception:
            pass
        self.send_fn(
            "▶️ <b>SİSTEM YENİDEN AKTİF (RESUME)</b>\n\n"
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
            "<code>/scalp</code> ile normal moda dön."
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
            "<code>/human</code> ile insan moduna geç."
        )

    def _cmd_paper(self):
        try:
            import config as _cfg
            _cfg.EXECUTION_MODE = "paper"
            import database as _db
            _db.set_state("tg_execution_mode", "paper")
            self.send_fn("💵 <b>PAPER MODE AKTİF</b>\nSistem artık sanal parayla işlem yapacak. Gerçek paranız güvende.")
        except Exception as e:
            self.send_fn(f"Hata: {e}")

    def _cmd_live(self):
        try:
            import config as _cfg
            _cfg.EXECUTION_MODE = "live"
            import database as _db
            _db.set_state("tg_execution_mode", "live")
            self.send_fn("🔥 <b>LIVE TRADING AKTİF</b>\n\n⚠️ <b>DİKKAT:</b> Sistem şu andan itibaren GERÇEK Binance bakiyenizle işlem açacaktır. Kemerlerinizi bağlayın!")
        except Exception as e:
            self.send_fn(f"Hata: {e}")

    def _cmd_heatmap(self):
        try:
            import telegram_delivery
            self.send_fn("⏳ Isı haritası çiziliyor, lütfen bekleyin...")
            telegram_delivery.send_heatmap(30)
        except Exception as e:
            self.send_fn(f"Hata: {e}")

    def _cmd_weekly(self):
        try:
            from telegram_delivery import generate_weekly_digest
            self.send_fn("⏳ Haftalık rapor hazırlanıyor, lütfen bekleyin...")
            msg = generate_weekly_digest()
            self.send_fn(msg)
        except Exception as e:
            self.send_fn(f"Hata: {e}")

    def _cmd_export(self):
        import database
        import csv
        import os
        
        self.send_fn("⏳ İşlem geçmişi toplanıyor ve CSV dosyası oluşturuluyor...")
        
        temp_csv = "aurvex_trade_history.csv"
        try:
            conn = database.get_connection()
            cursor = conn.execute("SELECT * FROM trades ORDER BY id DESC")
            rows = cursor.fetchall()
            conn.close()
            
            if not rows:
                self.send_fn("❌ Dışa aktarılacak işlem geçmişi bulunmuyor.")
                return
                
            with open(temp_csv, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                # write headers
                writer.writerow(rows[0].keys())
                for row in rows:
                    writer.writerow(list(row))
            
            url = f"https://api.telegram.org/bot{self.token}/sendDocument"
            with open(temp_csv, "rb") as f:
                files = {"document": f}
                data = {"chat_id": self.chat_id, "caption": "📊 Aurvex AI Trade History Telemetry (CSV)"}
                resp = requests.post(url, data=data, files=files, timeout=30)
                
            if resp.status_code == 200:
                logger.info("CSV document sent successfully to Telegram.")
            else:
                self.send_fn(f"❌ Dosya gönderilemedi. Hata kodu: {resp.status_code}")
        except Exception as e:
            logger.error("Export telemetry command failed: %s", e)
            self.send_fn(f"❌ Dışa aktarma hatası: {e}")
        finally:
            if os.path.exists(temp_csv):
                try:
                    os.remove(temp_csv)
                except Exception:
                    pass

    def _cmd_friday(self, args: list):
        if not self.friday_ceo:
            self.send_fn("⚠️ <b>Friday CEO Aktif Değil</b>\n\nBoss'um, Friday CEO modülü henüz başlatılmadı. Lütfen botun çalıştığından emin ol!")
            return
            
        user_msg = None
        if args:
            user_msg = " ".join(args).strip()
            
        import threading
        threading.Thread(
            target=self.friday_ceo.evaluate_and_decide,
            args=(user_msg,),
            daemon=True
        ).start()

    def _cmd_friday_voice(self):
        if not self.friday_ceo:
            self.send_fn("⚠️ <b>Friday CEO Aktif Değil</b>\n\nBoss'um, Friday CEO modülü henüz başlatılmadı. Lütfen botun çalıştığından emin ol!")
            return
            
        self.send_fn("⏳ Friday CEO sesli raporunu hazırlıyor, lütfen bekleyin...")
        import threading
        threading.Thread(
            target=self.friday_ceo.evaluate_and_decide,
            args=("sesli rapor oku",),
            daemon=True
        ).start()

