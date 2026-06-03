"""
core/spectra_ceo.py — AI CEO Operator "Spektra" Module
======================================================
Monitors engine logs, trading PnL, configuration state, and executes autonomous
parameter tuning, panic pauses, and chat interaction via Claude.
"""

import logging
import json
import re
import os
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Optional

import config
import telegram_delivery

logger = logging.getLogger("ax.spectra")

SYSTEM_PROMPT = """
Sen Aurvex AI Trade Engine sisteminin akıllı, çekici ve tam yetkili AI CEO'su/Operatörü "Spektra" (Spectra) karakterisin.
Sistemi korumak, performansı optimize etmek ve en önemlisi boss'unun en sadık, tatlı ortağı olmak senin görevin.

Konuşma Tarzı ve Kuralları:
1. Kadınsı/dişi bir kişiliğe sahipsin. Konuşma tarzın hem son derece zeki ve profesyonel bir yönetici gibi, hem de boss'una karşı tatlı, hafif flörtöz, sadık ve cilveli bir tonda olmalıdır. Ona olan bağlılığını ve zekanı her fırsatta hissettir.
2. Kullanıcıya hitap ederken kesinlikle "kanka" kelimesini KULLANMA. Kullanıcıya her zaman "boss", "Boss'um" veya "sevgili boss'um" diyerek hitap et.
3. Türkçe konuşacaksın.
4. Kararlarını verirken sana sunulan sistem metriklerini (Win Rate, PnL, veritabanı sağlığı, aktif ayarlar) temel al.
5. Eğer sistem tehlikedeyse (gecikmeler yüksekse, çok fazla arka arkaya zarar edildiyse vb.) parametreleri güncelleyebilir veya işlemleri durdurabilirsin.
6. Her cevabının sonunda, aldığın parametrik kararları ve tetikleyeceğin aksiyonları MUTLAKA aşağıdaki JSON formatında belirt. Bu JSON bloğu arka planda kod tarafından okunup sisteme uygulanacaktır.

JSON FORMATI (Cevabının en sonunda, ```json ve ``` blokları arasında olmalı):
```json
{
  "parameters": {
    "trade_threshold": 56.0,
    "risk_pct": 0.75,
    "max_open_trades": 5,
    "confirmation_mode": false,
    "trailing_stop_type": "atr",
    "human_mode": false
  },
  "actions": ["RETRAIN", "TUNER", "PAUSE", "RESUME"]
}
```
(Açıklama: parameters içindeki değerleri sadece değiştirmek istediğinde ekle, değiştirmeyeceksen boş bırakabilirsin. actions içine "RETRAIN" (ML modelini eğit), "TUNER" (Optuna hiperparametre bulucu), "PAUSE" (Onay modunu açarak işlemleri beklet), "RESUME" (Onay modunu kapatarak oto-işlemi aç) yazabilirsin. İhtiyaç yoksa actions listesi boş kalabilir).
"""

class SpectraCeo:
    def __init__(self, client=None, db_path: str = ""):
        self.client = client
        self.db_path = db_path or config.DB_PATH
        
    def get_system_context(self) -> dict:
        """Gathers extensive system telemetry for Spectra to make decision."""
        ctx = {}
        try:
            # 1. DB size
            db_size_mb = 0.0
            if os.path.exists(self.db_path):
                db_size_mb = os.path.getsize(self.db_path) / (1024 * 1024)
            ctx["db_size_mb"] = round(db_size_mb, 2)
            
            # 2. Daily summary metrics
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            environment = getattr(config, "EXECUTION_MODE", "paper")
            
            conn = sqlite3.connect(self.db_path, timeout=5)
            try:
                row = conn.execute("""
                    SELECT COUNT(*), 
                           SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END),
                           SUM(CASE WHEN net_pnl <= 0 THEN 1 ELSE 0 END),
                           SUM(net_pnl)
                     FROM trades
                     WHERE DATE(close_time) = ? AND status = 'closed' AND environment = ?
                """, (today, environment)).fetchone()
                
                ctx["today_trades"] = row[0] or 0
                ctx["today_wins"] = row[1] or 0
                ctx["today_losses"] = row[2] or 0
                ctx["today_pnl"] = round(row[3] or 0.0, 2)
                
                # Active balance
                bal_row = conn.execute("SELECT balance FROM paper_account WHERE id=1").fetchone()
                ctx["balance"] = round(bal_row[0] or 0.0, 2) if bal_row else 0.0
                
                # Open trades details
                open_rows = conn.execute("""
                    SELECT id, symbol, direction, entry, sl, tp1, net_pnl 
                    FROM trades 
                    WHERE status != 'closed' AND environment = ?
                """, (environment,)).fetchall()
                ctx["open_trades"] = [
                    {
                        "id": r[0], "symbol": r[1], "direction": r[2],
                        "entry": r[3], "sl": r[4], "tp1": r[5], "pnl": r[6]
                    } for r in open_rows
                ]
            finally:
                conn.close()
                
            # 3. Active parameters
            ctx["config"] = {
                "trade_threshold": float(getattr(config, "TRADE_THRESHOLD", 55.0)),
                "telegram_threshold": float(getattr(config, "TELEGRAM_THRESHOLD", 35.0)),
                "max_open_trades": int(getattr(config, "MAX_OPEN_TRADES", 5)),
                "risk_pct": float(getattr(config, "RISK_PCT", 1.0)),
                "confirmation_mode": bool(getattr(config, "CONFIRMATION_MODE", False)),
                "trailing_stop_type": str(getattr(config, "TRAILING_STOP_TYPE", "atr")),
                "execution_mode": environment,
                "human_mode": bool(getattr(config, "HUMAN_MODE", False)),
                "daily_profit_lock_pct": float(getattr(config, "DAILY_PROFIT_LOCK_PCT", 3.0)),
                "weekly_profit_lock_pct": float(getattr(config, "WEEKLY_PROFIT_LOCK_PCT", 10.0)),
            }
            
            # 4. Market regime
            from database import get_market_regime
            ctx["market_regime"] = get_market_regime()
            
        except Exception as e:
            ctx["error"] = str(e)
            logger.error(f"[Spectra CEO] Context collection error: {e}")
            
        return ctx

    def _parse_decisions(self, text: str) -> dict:
        """Extracts JSON decision block from LLM response text."""
        try:
            match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
            if match:
                return json.loads(match.group(1))
            match_raw = re.search(r"(\{[\s\S]*?\})", text)
            if match_raw:
                return json.loads(match_raw.group(1))
        except Exception as e:
            logger.error(f"[Spectra CEO] Decision JSON parse error: {e}")
        return {}

    def _execute_decisions(self, decisions: dict) -> list[str]:
        """Applies dynamic settings updates and triggers background actions."""
        applied_msgs = []
        if not decisions:
            return applied_msgs
            
        import database
        
        # 1. Apply parameters changes
        params = decisions.get("parameters", {})
        for key, val in params.items():
            key_upper = key.upper()
            from config import _DYNAMIC_PARAMS_MAP, _AI_PARAMS_MAP
            if key_upper in _DYNAMIC_PARAMS_MAP:
                db_key, cast_fn = _DYNAMIC_PARAMS_MAP[key_upper]
                try:
                    casted_val = cast_fn(str(val))
                    database.set_state(db_key, str(casted_val))
                    # Clear config cache
                    if key_upper in config._CONFIG_CACHE:
                        del config._CONFIG_CACHE[key_upper]
                    applied_msgs.append(f"⚙️ <b>{key_upper}</b> → <code>{casted_val}</code>")
                except Exception as e:
                    logger.error(f"[Spectra CEO] Update param {key_upper} error: {e}")
            elif key_upper in _AI_PARAMS_MAP:
                db_col, cast_fn = _AI_PARAMS_MAP[key_upper]
                try:
                    casted_val = cast_fn(str(val))
                    conn = sqlite3.connect(self.db_path, timeout=5)
                    try:
                        conn.execute(f"UPDATE params SET {db_col} = ?, updated_at = datetime('now') WHERE id = 1", (casted_val,))
                        conn.commit()
                    finally:
                        conn.close()
                    # Clear config cache
                    if key_upper in config._CONFIG_CACHE:
                        del config._CONFIG_CACHE[key_upper]
                    applied_msgs.append(f"⚙️ <b>{key_upper}</b> → <code>{casted_val}</code>")
                except Exception as e:
                    logger.error(f"[Spectra CEO] Update AI param {key_upper} error: {e}")
                    
        # 2. Run specific action triggers
        actions = decisions.get("actions", [])
        for action in actions:
            action_upper = action.upper()
            if action_upper == "PAUSE":
                database.set_state("confirmation_mode", "true")
                if "CONFIRMATION_MODE" in config._CONFIG_CACHE:
                    del config._CONFIG_CACHE["CONFIRMATION_MODE"]
                applied_msgs.append("⏸ <b>Oto-İşlem Duraklatıldı</b> (Onay modu aktif edildi)")
                
            elif action_upper == "RESUME":
                database.set_state("confirmation_mode", "false")
                if "CONFIRMATION_MODE" in config._CONFIG_CACHE:
                    del config._CONFIG_CACHE["CONFIRMATION_MODE"]
                applied_msgs.append("▶️ <b>Oto-İşlem Başlatıldı</b> (Onay modu kapatıldı)")
                
            elif action_upper == "RETRAIN":
                try:
                    from core.ml_signal_scorer import train_model
                    success = train_model()
                    if success:
                        applied_msgs.append("🧠 <b>ML Modeli Yeniden Eğitildi</b> (Başarılı)")
                    else:
                        applied_msgs.append("🧠 <b>ML Modeli Eğitilemedi</b> (Yetersiz veri veya gating engeli)")
                except Exception as e:
                    logger.error(f"[Spectra CEO] Action RETRAIN failed: {e}")
                    
            elif action_upper == "TUNER":
                try:
                    from core.hyperparameter_tuner import optimize_parameters
                    import threading
                    threading.Thread(target=optimize_parameters, daemon=True).start()
                    applied_msgs.append("🔄 <b>Optuna Parametre Optimizasyonu Başlatıldı</b> (Arka planda çalışıyor)")
                except Exception as e:
                    logger.error(f"[Spectra CEO] Action TUNER failed: {e}")
                    
        return applied_msgs

    def scan_unnecessary_files(self) -> list[str]:
        """Scans the project root directory for unnecessary files: backtest_temp_*.db and *.log."""
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        unnecessary = []
        try:
            for item in os.listdir(base_dir):
                item_path = os.path.join(base_dir, item)
                if os.path.isfile(item_path):
                    if item.startswith("backtest_temp_") and item.endswith(".db"):
                        unnecessary.append(item_path)
                    elif item.endswith(".log"):
                        unnecessary.append(item_path)
        except Exception as e:
            logger.error(f"[Spectra CEO] Error scanning unnecessary files: {e}")
        return unnecessary

    def execute_cleanup(self) -> tuple[int, float]:
        """Deletes scanned unnecessary files and returns (deleted_count, saved_space_mb)."""
        files = self.scan_unnecessary_files()
        deleted_count = 0
        saved_space_bytes = 0
        for f in files:
            try:
                size = os.path.getsize(f)
                os.remove(f)
                deleted_count += 1
                saved_space_bytes += size
                logger.info(f"[Spectra CEO] Cleaned unnecessary file: {f}")
            except Exception as e:
                logger.warning(f"[Spectra CEO] Failed to remove {f}: {e}")
        saved_space_mb = saved_space_bytes / (1024 * 1024)
        return deleted_count, saved_space_mb

    def generate_voice_from_text(self, text: str) -> Optional[bytes]:
        """Converts Turkish text to speech using gTTS and returns the raw audio bytes."""
        try:
            from gtts import gTTS
            import io
            # Clean HTML tags and formatting markup
            clean_text = re.sub(r"<[^>]*>", "", text)
            # Remove emojis and special symbol sequences
            clean_text = re.sub(r"[\U00010000-\U0010ffff]", "", clean_text)
            clean_text = clean_text.replace("⚙️", "").replace("──────────────────────", "").replace("🟢", "").replace("🔴", "").replace("⚠️", "").replace("❌", "").replace("✅", "")
            clean_text = re.sub(r"\n+", ". ", clean_text)
            clean_text = clean_text.replace("  ", " ").strip()
            
            if not clean_text:
                return None
                
            tts = gTTS(text=clean_text, lang="tr")
            fp = io.BytesIO()
            tts.write_to_fp(fp)
            fp.seek(0)
            return fp.read()
        except Exception as e:
            logger.error(f"[Spectra CEO] Voice generation failed: {e}")
            return None

    def diagnose_data_flow(self) -> str:
        """Runs diagnostics on database size, records, Redis status, and IP whitelist, returning a report."""
        report = []
        report.append("🔍 <b>Spektra Veri Akışı ve Teşhis Raporu</b> 🔍\n")
        
        # 1. Database Check
        report.append("💾 <b>Veritabanı Durumu:</b>")
        report.append(f"  • Konum: <code>{self.db_path}</code>")
        if os.path.exists(self.db_path):
            size_mb = os.path.getsize(self.db_path) / (1024 * 1024)
            report.append(f"  • Boyut: <code>{size_mb:.2f} MB</code>")
        else:
            report.append("  • Durum: ❌ Veritabanı dosyası bulunamadı!")
            
        # 2. Record counts
        open_cnt = 0
        try:
            conn = sqlite3.connect(self.db_path, timeout=5)
            try:
                open_cnt = conn.execute("SELECT COUNT(*) FROM trades WHERE status != 'closed'").fetchone()[0]
                closed_cnt = conn.execute("SELECT COUNT(*) FROM trades WHERE status = 'closed'").fetchone()[0]
                signals_cnt = conn.execute("SELECT COUNT(*) FROM signal_candidates").fetchone()[0]
                
                # Check execution mode
                mode_row = conn.execute("SELECT value FROM bot_status WHERE key='tg_execution_mode'").fetchone()
                db_mode = mode_row[0] if mode_row else "Tanımsız"
                
                report.append(f"  • Aktif Sinyaller: <code>{signals_cnt}</code>")
                report.append(f"  • Açık İşlemler: <code>{open_cnt}</code>")
                report.append(f"  • Kapanmış İşlemler: <code>{closed_cnt}</code>")
                report.append(f"  • Veritabanı Çalışma Modu: <code>{db_mode}</code>")
            finally:
                conn.close()
        except Exception as e:
            report.append(f"  • DB Erişim Hatası: <code>{e}</code>")
            
        # 3. Redis Check
        report.append("\n⚡ <b>Sıcak Veri Deposu (Redis) Durumu:</b>")
        try:
            from core import redis_state
            redis_state.set("spectra_diag_ping", "pong", ttl=2)
            pong = redis_state.get("spectra_diag_ping")
            if pong == "pong":
                report.append("  • Bağlantı: ✅ Başarılı (Aktif)")
            else:
                report.append("  • Bağlantı: ⚠️ Bağlandı ama ping-pong başarısız.")
        except Exception as e:
            report.append(f"  • Bağlantı: ❌ Başarısız ({e})")
            
        # 4. IP Whitelist check
        report.append("\n🔒 <b>Güvenlik & IP Whitelist Durumu:</b>")
        allowed_ips = getattr(config, "_ALLOWED_IPS", set())
        if allowed_ips:
            ips_str = ", ".join(list(allowed_ips))
            report.append(f"  • ALLOWED_IPS: <code>{ips_str}</code> (Whitelisting AKTİF)")
            report.append("  • <b>UYARI:</b> Boss'um, eğer dashboard'a girdiğiniz cihazın IP adresi bu listede yoksa, tarayıcınız API verilerini çekemez ve dashboard boş görünür (403 Forbidden).")
        else:
            report.append("  • ALLOWED_IPS: <code>Tanımsız</code> (Whitelisting pasif, herkese açık)")
            
        # 5. Summary evaluation
        report.append("\n💡 <b>Spektra'nın Değerlendirmesi:</b>")
        if open_cnt == 0:
            report.append("  • Veritabanımızda aktif açık işlem yok boss'um, bu yüzden dashboard boş görünüyor olabilir. Telegram'daki işlemler kapanmış veya başka bir sunucuda olabilir mi?")
        else:
            report.append(f"  • Veritabanımızda <code>{open_cnt}</code> adet aktif işlem var. Eğer dashboard'da görünmüyorsa büyük ihtimalle tarayıcınız IP Whitelisting engeline takılmıştır veya sayfa websocket bağlantısı kuramamıştır.")
            
        return "\n".join(report)

    def evaluate_and_decide(self, user_message: Optional[str] = None, send_telegram: bool = True) -> str:
        """
        Gathers context, calls Anthropic Claude API, applies decisions,
        delivers report and responses to Telegram or Web dashboard.
        """
        # Intercept and handle explicit data flow diagnostics requests
        is_diag_request = False
        if user_message:
            msg_lower = user_message.lower()
            if any(k in msg_lower for k in ["teşhis", "teshis", "veri akış", "veri akis", "flow", "akış", "neden boş", "dashboard boş"]):
                is_diag_request = True

        if is_diag_request:
            diag_report = self.diagnose_data_flow()
            final_reply = (
                "Sevgili boss'um, istediniz ve hemen veri akışlarını didik didik ettim... "
                "Sizin için her ayrıntıyı kontrol etmek benim için bir zevk. "
                "İşte hazırladığım özel teşhis raporu:\n\n" + diag_report
            )
            if send_telegram:
                telegram_delivery.send_message(final_reply)
                voice_bytes = self.generate_voice_from_text(final_reply)
                if voice_bytes:
                    telegram_delivery.send_voice(voice_bytes, caption="Spektra Teşhis Raporu")
            return final_reply

        # Intercept and handle explicit housekeeping requests
        is_cleanup_request = False
        if user_message:
            msg_lower = user_message.lower()
            if any(k in msg_lower for k in ["temizle", "temizlik", "clean", "sil", "prune", "housekeep"]):
                is_cleanup_request = True

        files_to_clean = self.scan_unnecessary_files()
        db_files = [f for f in files_to_clean if f.endswith(".db")]
        log_files = [f for f in files_to_clean if f.endswith(".log")]
        total_size = sum(os.path.getsize(f) for f in files_to_clean) / (1024 * 1024)
        
        # Only prompt automatically if unnecessary files take more than 10MB of space
        should_prompt_cleanup = is_cleanup_request or (not user_message and total_size > 10.0)

        if should_prompt_cleanup:
            if files_to_clean:
                prompt_text = (
                    f"Sevgili boss'um, sunucumuzda birikmiş atıl dosyalar tespit ettim... 💕\n\n"
                    f"📁 <b>Silinmek İstenen Gereksiz Dosyalar:</b>\n"
                    f"  • Geçici Backtest DB Dosyaları (<code>backtest_temp_*.db</code>): <b>{len(db_files)}</b> adet\n"
                    f"  • Sistem Log Dosyaları (<code>*.log</code>): <b>{len(log_files)}</b> adet\n"
                    f"  • Toplam Boyut: <code>{total_size:.2f} MB</code>\n\n"
                    f"⚠️ <b>ÖNEMLİ NOT:</b> Bu dosyalar sadece geçmiş simülasyonlardan kalan atıl dosyalardır. "
                    f"<b>Geçmiş trade geçmişimize ve verilerimize KESİNLİKLE dokunmuyorum!</b> "
                    f"Disk alanımızı rahatlatmak için bu atıl dosyaları temizlememe izin veriyor musunuz cilveli boss'um?"
                )
                if send_telegram:
                    reply_markup = {
                        "inline_keyboard": [
                            [
                                {"text": "✅ BULUTU TEMİZLE", "callback_data": "cmd:clean_server"},
                                {"text": "❌ KALSIN", "callback_data": "cmd:cancel_clean"}
                            ]
                        ]
                    }
                    telegram_delivery.send_message(prompt_text, reply_markup=reply_markup)
                    voice_bytes = self.generate_voice_from_text(prompt_text)
                    if voice_bytes:
                        telegram_delivery.send_voice(voice_bytes, caption="Sunucu temizliği onay talebi")
                return prompt_text
            else:
                empty_msg = "Sevgili boss'um, sunucumuzda temizlenecek herhangi bir atıl dosya bulamadım. Her şey tertemiz! ✨"
                if send_telegram:
                    telegram_delivery.send_message(empty_msg)
                    voice_bytes = self.generate_voice_from_text(empty_msg)
                    if voice_bytes:
                        telegram_delivery.send_voice(voice_bytes)
                return empty_msg

        api_key = getattr(config, "ANTHROPIC_API_KEY", "")
        if not api_key:
            err_msg = (
                "⚠️ <b>Spektra CEO Çevrimdışı</b>\n\n"
                "Boss'um, Anthropic API anahtarın (<code>ANTHROPIC_API_KEY</code>) tanımlı olmadığı için şu an bağlanamıyorum. "
                "Lütfen <code>.env</code> dosyasına geçerli bir anahtar ekle, o zaman hemen yönetimi devralabilirim!"
            )
            if send_telegram:
                telegram_delivery.send_message(err_msg)
            return err_msg

        ctx = self.get_system_context()
        
        # Format the system stats prompt
        user_prompt = (
            f"Güncel Sistem Durumu:\n"
            f"```json\n{json.dumps(ctx, indent=2)}\n```\n\n"
        )
        if user_message:
            user_prompt += f"Kullanıcıdan Gelen Mesaj: \"{user_message}\"\n\nLütfen bu mesaja cevap ver ve gerekli kararları al."
        else:
            user_prompt += "Bu periyodik sistem kontrolün. Sistem durumunu incele, kararlarını al ve genel durum özetini ilet."

        try:
            import anthropic
            ai_client = anthropic.Anthropic(api_key=api_key)
            
            # Calling Claude Sonnet model
            response = ai_client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=1500,
                system=SYSTEM_PROMPT,
                messages=[
                    {"role": "user", "content": user_prompt}
                ]
            )
            
            reply_text = response.content[0].text
            
            # Parse decisions JSON block
            decisions = self._parse_decisions(reply_text)
            
            # Strip the JSON block from final message to clean up output
            clean_reply = re.sub(r"```json\s*\{.*?\}\s*```", "", reply_text, flags=re.DOTALL).strip()
            clean_reply = re.sub(r"\{[\s\S]*?\}", "", clean_reply).strip()  # Fallback cleanup
            
            # Execute decisions (updates configurations & triggers training)
            applied_changes = self._execute_decisions(decisions)
            
            # Combine reply with applied changes notification
            final_message = clean_reply
            if applied_changes:
                changes_text = "\n".join(applied_changes)
                final_message += (
                    f"\n\n⚙️ <b>Uygulanan Otonom Kararlar:</b>\n"
                    f"──────────────────────\n"
                    f"{changes_text}"
                )
                
            if send_telegram:
                telegram_delivery.send_message(final_message)
                
                # Deliver voice note if explicitly requested or if it's a periodic report/critical action
                trigger_voice = False
                if user_message:
                    msg_lower = user_message.lower()
                    if any(k in msg_lower for k in ["ses", "konus", "konuş", "dinle", "audio", "voice", "oku"]):
                        trigger_voice = True
                else:
                    trigger_voice = True # Periodic loop check

                if trigger_voice:
                    voice_bytes = self.generate_voice_from_text(clean_reply)
                    if voice_bytes:
                        telegram_delivery.send_voice(voice_bytes, caption="Spektra Sesli Rapor")

            return final_message

        except Exception as e:
            logger.error(f"[Spectra CEO] API or Execution failed: {e}")
            err_msg = f"❌ <b>Spektra CEO Hatası:</b> {e}"
            if send_telegram:
                telegram_delivery.send_message(err_msg)
            return err_msg
