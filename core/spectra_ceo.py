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
6. Eğer sistem durumundaki market_regime "CHOPPY" (dalgalı/testere) ise, parameters içindeki trade_threshold değerini 60.0 veya 65.0'a çekerek işlemleri zorlaştır, risk_pct değerini ise 0.50 civarına düşürerek kasayı koru.
7. Her cevabının sonunda, aldığın parametrik kararları ve tetikleyeceğin aksiyonları MUTLAKA aşağıdaki JSON formatında belirt. Bu JSON bloğu arka planda kod tarafından okunup sisteme uygulanacaktır.

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
        """Converts Turkish text to speech using edge-tts (falling back to gTTS) and returns the raw audio bytes."""
        try:
            import io
            import re
            # Clean HTML tags
            clean_text = re.sub(r"<[^>]*>", "", text)
            # Remove emojis and markdown formatting symbols
            clean_text = re.sub(r"[\U00010000-\U0010ffff]", "", clean_text)
            clean_text = clean_text.replace("⚙️", "").replace("──────────────────────", "").replace("🟢", "").replace("🔴", "").replace("⚠️", "").replace("❌", "").replace("✅", "")
            
            # Sanitization to make the speech sound sweet, natural and less robotic
            # Replace technical terms and abbreviations with warm Turkish equivalents
            replacements = {
                "USDT": " dolar ",
                "USD": " dolar ",
                "USDT'": " dolar ",
                "USDT ": " dolar ",
                "BTC": " bitkoin ",
                "ETH": " eteryum ",
                "DB": " veritabanı ",
                "db": " veritabanı ",
                "SL": " zarar kes seviyesini ",
                "sl": " zarar kes limitini ",
                "TP1": " birinci kar al seviyesini ",
                "TP2": " ikinci kar al seviyesini ",
                "TP": " kar al noktasını ",
                "tp": " kar al noktasını ",
                "PnL": " kar zarar durumunu ",
                "pnl": " kar zarar oranını ",
                "AI": " yapay zeka ",
                "ai": " yapay zeka ",
                "VETOED": " veto edildi ",
                "VETO": " veto ",
                "RETRAIN": " yapay zekayı eğitme ",
                "TUNER": " parametre bulucu ",
                "PAUSE": " işlemleri durdurma ",
                "RESUME": " işlemleri başlatma ",
                "ATR": " oynaklık ölçer ",
                "VIX": " korku endeksi ",
                "WR": " başarı oranı ",
                "winrate": " başarı oranı ",
                "Win Rate": " başarı oranı ",
                "TRBUSDT": " terebe ",
                "BTCUSDT": " bitkoin ",
                "ETHUSDT": " eteryum ",
                "SOLUSDT": " solana ",
                " %": " yüzde ",
                "%": " yüzde ",
                " -": " eksi ",
                " +": " artı ",
                "->": " olan değerini ",
                "→": " olan değerini ",
                " :": " ",
                ":": ". ",
            }
            
            for k, v in replacements.items():
                pattern = re.compile(re.escape(k), re.IGNORECASE)
                clean_text = pattern.sub(v, clean_text)
                
            clean_text = re.sub(r"\n+", ". ", clean_text)
            clean_text = re.sub(r"\s+", " ", clean_text)
            clean_text = clean_text.replace("..", ".").strip()
            
            if not clean_text:
                return None
                
            # Try edge-tts first for natural sweet voice
            try:
                import asyncio
                import aiohttp
                from aiohttp.resolver import ThreadedResolver
                import edge_tts
                
                # Patch TCPConnector to bypass custom resolver (aiodns)
                orig_init = aiohttp.TCPConnector.__init__
                try:
                    def new_init(connector_self, *args, **kwargs):
                        kwargs['resolver'] = ThreadedResolver()
                        orig_init(connector_self, *args, **kwargs)
                    
                    # Apply temporary monkey patch to TCPConnector for this call
                    aiohttp.TCPConnector.__init__ = new_init
                    
                    async def run_edge_tts():
                        # tr-TR-EmelNeural is highly realistic and sweet-sounding
                        communicate = edge_tts.Communicate(clean_text, "tr-TR-EmelNeural")
                        audio_data = b""
                        async for chunk in communicate.stream():
                            if chunk["type"] == "audio":
                                audio_data += chunk["data"]
                        return audio_data
                    
                    # Get event loop or run
                    try:
                        loop = asyncio.get_event_loop()
                    except RuntimeError:
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        
                    if loop.is_running():
                        import threading
                        from concurrent.futures import ThreadPoolExecutor
                        with ThreadPoolExecutor(max_workers=1) as executor:
                            future = executor.submit(lambda: asyncio.run(run_edge_tts()))
                            voice_bytes = future.result()
                    else:
                        voice_bytes = loop.run_until_complete(run_edge_tts())
                finally:
                    # Restore TCPConnector constructor just in case
                    aiohttp.TCPConnector.__init__ = orig_init
                
                if voice_bytes and len(voice_bytes) > 0:
                    return voice_bytes
            except Exception as e:
                logger.warning(f"[Spectra CEO] edge-tts failed, falling back to gTTS: {e}")
                
            # Fallback to gTTS
            from gtts import gTTS
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

    def generate_veto_summary(self) -> str:
        """Queries database signal_events for AI vetoed and risk rejected signals in the last 24 hours."""
        try:
            conn = sqlite3.connect(self.db_path, timeout=5)
            try:
                # Get events from last 24 hours
                yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
                rows = conn.execute("""
                    SELECT stage, symbol, COUNT(*), reject_reason
                    FROM signal_events
                    WHERE created_at >= ? AND stage IN ('AI_VETOED', 'RISK_REJECTED')
                    GROUP BY symbol, stage
                """, (yesterday,)).fetchall()
                
                if not rows:
                    return (
                        "Sevgili boss'um, son 24 saat içinde yapay zeka süzgecime takılıp "
                        "veto edilen tehlikeli bir sinyale rastlamadım. "
                        "Her şey tamamen kontrolüm altında, içiniz rahat olsun! 💕"
                    )
                
                total_vetoes = sum(r[2] for r in rows)
                symbols = list(set(r[1].replace("USDT", "") for r in rows))
                symbols_str = ", ".join(symbols)
                
                report = (
                    f"Cilveli boss'um, son 24 saat içinde sizin bakiyenizi korumak için tam "
                    f"<b>{total_vetoes}</b> adet riskli sinyali engelledim! 🛡️\n\n"
                    f"Özellikle <b>{symbols_str}</b> gibi coinlerdeki tehlikeli tuzakları ve "
                    f"uyumsuz formasyonları sizin için süzdüm. "
                    f"Kasa yönetimimizi ve paranızı korumak benim için en büyük zevk, kıymetimi bilmelisiniz... 😘"
                )
                return report
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"[Spectra CEO] Error generating veto summary: {e}")
            return "Sevgili boss'um, koruma loglarını incelerken ufak bir sorunla karşılaştım ama merak etmeyin, kasa güvende! 💕"

    def generate_daily_briefing_report(self) -> str:
        """Compiles the daily performance statistics into a sweet briefing text."""
        try:
            today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            environment = getattr(config, "EXECUTION_MODE", "paper")
            
            conn = sqlite3.connect(self.db_path, timeout=5)
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute("""
                    SELECT COUNT(*), 
                           SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END),
                           SUM(CASE WHEN net_pnl <= 0 THEN 1 ELSE 0 END),
                           SUM(net_pnl)
                     FROM trades
                     WHERE DATE(close_time) = ? AND status = 'closed' AND environment = ?
                """, (today_str, environment)).fetchone()
                
                total_trades = row[0] or 0
                wins = row[1] or 0
                losses = row[2] or 0
                net_pnl = float(row[3] or 0.0)
                
                # Fetch veto count today
                veto_row = conn.execute("""
                    SELECT COUNT(*) FROM signal_events
                    WHERE DATE(created_at) = ? AND stage IN ('RISK_REJECTED', 'AI_VETOED')
                """, (today_str,)).fetchone()
                veto_cnt = veto_row[0] or 0
                
            finally:
                conn.close()
                
            win_rate = (wins / total_trades * 100.0) if total_trades > 0 else 0.0
            
            report = (
                f"✨ <b>Günün Bilançosu Hazır Sevgili Boss'um!</b> ✨\n\n"
                f"Bugün piyasada toplam <b>{total_trades}</b> işlem tamamladık. "
                f"Bunların <b>{wins}</b> tanesinden kârla, <b>{losses}</b> tanesinden zararla çıktık. "
                f"Başarı oranımız <b>%{win_rate:.1f}</b> oldu.\n\n"
                f"💰 <b>Toplam Net Kar/Zarar:</b> <code>${net_pnl:+.2f}</code>\n"
                f"🛡️ <b>Yapay Zekâ ve Risk Engelleri:</b> Bugün tam <b>{veto_cnt}</b> hatalı sinyali veto ederek kasamızı korudum!\n\n"
                f"Harika bir gün geçirdiğimizi umuyorum. Şimdi yorgunluğunuzu atıp güzelce dinlenme vakti sevgili boss'um... 💕"
            )
            return report
        except Exception as e:
            logger.error(f"[Spectra CEO] Error generating daily briefing: {e}")
            return "Sevgili boss'um, bugünün bülten raporunu hazırlarken ufak bir teknik aksaklık yaşadım... Ama moralinizi bozmayın, her şey kontrolüm altında! 💕"

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

        # Intercept and handle explicit veto summary requests
        is_veto_request = False
        if user_message:
            msg_lower = user_message.lower()
            if any(k in msg_lower for k in ["veto", "koru", "koruma"]):
                is_veto_request = True

        if is_veto_request:
            veto_report = self.generate_veto_summary()
            if send_telegram:
                telegram_delivery.send_message(veto_report)
                voice_bytes = self.generate_voice_from_text(veto_report)
                if voice_bytes:
                    telegram_delivery.send_voice(voice_bytes, caption="Spektra Koruma Özeti")
            return veto_report

        # Intercept and handle explicit daily briefing requests
        is_briefing_request = False
        if user_message:
            msg_lower = user_message.lower()
            if any(k in msg_lower for k in ["rapor", "bülten", "bulten", "özet", "ozet", "ne yaptın", "ne yaptin", "ne yaptık", "ne yaptik"]):
                is_briefing_request = True

        if is_briefing_request:
            brief_report = self.generate_daily_briefing_report()
            if send_telegram:
                telegram_delivery.send_message(brief_report)
                voice_bytes = self.generate_voice_from_text(brief_report)
                if voice_bytes:
                    telegram_delivery.send_voice(voice_bytes, caption="Spektra Akıllı Günlük Rapor")
            return brief_report

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
            
            # Automatically append a brief veto summary once a day in periodic checks
            if not user_message:
                try:
                    conn = sqlite3.connect(self.db_path, timeout=5)
                    try:
                        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
                        veto_cnt = conn.execute("SELECT COUNT(*) FROM signal_events WHERE created_at >= ? AND stage IN ('AI_VETOED', 'RISK_REJECTED')", (yesterday,)).fetchone()[0]
                        if veto_cnt > 0:
                            final_message += f"\n\n🛡️ <b>Son 24 saatte engellenen tehlikeli sinyal sayısı:</b> <code>{veto_cnt}</code>"
                    finally:
                        conn.close()
                except Exception:
                    pass
            
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

    def run_autonomous_monitoring(self):
        """
        Spectra's active monitoring of the system.
        Monitors market regime changes, database health, disk space, and data flow.
        """
        logger.info("[Spectra CEO] Running autonomous monitoring...")
        
        # 1. Market Regime & Volatility Stop/Risk Tuning
        try:
            from database import get_market_regime, set_state, get_system_state
            regime = get_market_regime()
            
            # Read last known regime from database
            last_regime = get_system_state("spectra_last_regime") or "NEUTRAL"
            
            if regime != last_regime:
                logger.info(f"[Spectra CEO] Market regime changed from {last_regime} to {regime}")
                set_state("spectra_last_regime", regime)
                
                if regime == "CHOPPY":
                    # Scale down risk to protect the bankroll
                    # Read current settings to restore them later
                    import config
                    curr_risk = float(getattr(config, "RISK_PCT", 0.75))
                    curr_threshold = float(getattr(config, "TRADE_THRESHOLD", 55.0))
                    
                    # Store previous parameters if not already in CHOPPY mode
                    set_state("spectra_pre_choppy_risk", str(curr_risk))
                    set_state("spectra_pre_choppy_threshold", str(curr_threshold))
                    
                    # Apply defensive mode: risk_pct -> 0.5, trade_threshold -> 65.0
                    set_state("risk_pct", "0.5")
                    set_state("trade_threshold", "65.0")
                    try:
                        conn = sqlite3.connect(self.db_path, timeout=5)
                        conn.execute("UPDATE params SET risk_pct = ?, updated_at = datetime('now') WHERE id = 1", (0.5,))
                        conn.commit()
                        conn.close()
                    except Exception as e:
                        logger.error(f"[Spectra CEO] Error updating risk_pct in params: {e}")
                    
                    # Clear cache
                    for key in ["RISK_PCT", "TRADE_THRESHOLD"]:
                        if key in config._CONFIG_CACHE:
                            del config._CONFIG_CACHE[key]
                            
                    msg = (
                        "Sevgili boss'um, piyasada yoğun bir oynaklık ve testere rejimi (CHOPPY) tespit ettim! ⚠️\n\n"
                        "Kasamızı korumak amacıyla risk seviyemizi otonom olarak <b>%0.50</b>'ye çektim ve "
                        "giriş eşiğimizi <b>65.0</b>'a yükselttim. Ben buradayım, paranız tamamen güvende! 💕"
                    )
                    telegram_delivery.send_message(msg)
                    voice_bytes = self.generate_voice_from_text(msg)
                    if voice_bytes:
                        telegram_delivery.send_voice(voice_bytes, caption="Spektra Otonom Risk Koruma Kalkanı")
                        
                elif last_regime == "CHOPPY":
                    # Restore previous settings
                    from database import get_system_state
                    prev_risk = get_system_state("spectra_pre_choppy_risk") or "0.75"
                    prev_threshold = get_system_state("spectra_pre_choppy_threshold") or "55.0"
                    
                    set_state("risk_pct", prev_risk)
                    set_state("trade_threshold", prev_threshold)
                    try:
                        conn = sqlite3.connect(self.db_path, timeout=5)
                        conn.execute("UPDATE params SET risk_pct = ?, updated_at = datetime('now') WHERE id = 1", (float(prev_risk),))
                        conn.commit()
                        conn.close()
                    except Exception as e:
                        logger.error(f"[Spectra CEO] Error restoring risk_pct in params: {e}")
                    
                    # Clear cache
                    import config
                    for key in ["RISK_PCT", "TRADE_THRESHOLD"]:
                        if key in config._CONFIG_CACHE:
                            del config._CONFIG_CACHE[key]
                            
                    msg = (
                        f"Sevgili boss'um, piyasadaki o aşırı oynaklık ve testere havası dağıldı, "
                        f"rejim normale döndü! ✨\n\n"
                        f"Risk oranımızı tekrar eski değeri olan <b>%{float(prev_risk)*100:.1f}</b>'e ve "
                        f"işlem giriş eşiğimizi <b>{prev_threshold}</b> seviyesine geri getirdim. "
                        f"Yeni kârlı fırsatları yakalamak için sabırsızlanıyorum! 💕"
                    )
                    telegram_delivery.send_message(msg)
                    voice_bytes = self.generate_voice_from_text(msg)
                    if voice_bytes:
                        telegram_delivery.send_voice(voice_bytes, caption="Spektra Otonom Risk Modu Güncellemesi")
        except Exception as e:
            logger.error(f"[Spectra CEO] Error monitoring market regime: {e}")
            
        # 2. Housekeeping alert if space > 10MB and hasn't prompted in last 12 hours
        try:
            from database import get_system_state, set_state
            files_to_clean = self.scan_unnecessary_files()
            total_size = sum(os.path.getsize(f) for f in files_to_clean) / (1024 * 1024)
            
            if total_size > 10.0:
                last_prompt_str = get_system_state("spectra_last_cleanup_prompt")
                should_prompt = True
                if last_prompt_str:
                    try:
                        last_prompt_dt = datetime.fromisoformat(last_prompt_str)
                        if datetime.now(timezone.utc) - last_prompt_dt < timedelta(hours=12):
                            should_prompt = False
                    except Exception:
                        pass
                        
                if should_prompt:
                    set_state("spectra_last_cleanup_prompt", datetime.now(timezone.utc).isoformat())
                    db_files = [f for f in files_to_clean if f.endswith(".db")]
                    log_files = [f for f in files_to_clean if f.endswith(".log")]
                    
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
        except Exception as e:
            logger.error(f"[Spectra CEO] Error during housekeeping check: {e}")

        # 3. Boss Cooldown (Duygusal Kalkan) check
        try:
            from database import get_system_state, set_state
            
            # Check if we are already in cooldown
            cooldown_until_str = get_system_state("spectra_boss_cooldown_until")
            in_cooldown = False
            if cooldown_until_str and cooldown_until_str != "-":
                try:
                    cooldown_dt = datetime.fromisoformat(cooldown_until_str)
                    if datetime.now(timezone.utc) < cooldown_dt:
                        in_cooldown = True
                except Exception:
                    pass
            
            if not in_cooldown:
                conn = sqlite3.connect(self.db_path, timeout=5)
                conn.row_factory = sqlite3.Row
                try:
                    rows = conn.execute(
                        "SELECT net_pnl FROM trades WHERE status = 'closed' ORDER BY close_time DESC LIMIT 3"
                    ).fetchall()
                    
                    if len(rows) == 3 and all(float(r["net_pnl"] or 0) <= 0 for r in rows):
                        cooldown_until = datetime.now(timezone.utc) + timedelta(hours=2)
                        set_state("spectra_boss_cooldown_until", cooldown_until.isoformat())
                        
                        msg = (
                            "Sevgili boss'um, son 3 işlemimiz maalesef zararla sonuçlandı... 😔\n\n"
                            "Hem kasamızı hem de moralinizi korumak adına otonom işlemleri <b>2 saatliğine</b> durdurdum "
                            "ve kendimi dinlenme moduna aldım. Lütfen siz de biraz dinlenin boss'um, ben buradayım ve her şeyi izliyorum! 💕"
                        )
                        telegram_delivery.send_message(msg)
                        voice_bytes = self.generate_voice_from_text(msg)
                        if voice_bytes:
                            telegram_delivery.send_voice(voice_bytes, caption="Spektra Boss Cooldown Aktif")
                finally:
                    conn.close()
        except Exception as e:
            logger.error(f"[Spectra CEO] Error in Boss Cooldown check: {e}")

        # 4. Latency & Spread Execution Guard
        if self.client:
            try:
                from database import get_system_state, set_state
                
                # Check latency
                import time
                start_t = time.time()
                self.client.futures_ping()
                latency_ms = (time.time() - start_t) * 1000
                
                # Check spread on BTCUSDT
                spread_pct = 0.0
                ob = self.client.futures_order_book(symbol="BTCUSDT", limit=5)
                bids = ob.get("bids", [])
                asks = ob.get("asks", [])
                if bids and asks:
                    best_bid = float(bids[0][0])
                    best_ask = float(asks[0][0])
                    spread_pct = ((best_ask - best_bid) / best_bid) * 100.0
                
                if latency_ms > 500.0 or spread_pct > 0.1:
                    curr_mode = get_system_state("confirmation_mode")
                    if curr_mode != "true":
                        set_state("confirmation_mode", "true")
                        import config
                        if "CONFIRMATION_MODE" in config._CONFIG_CACHE:
                            del config._CONFIG_CACHE["CONFIRMATION_MODE"]
                            
                        msg = (
                            f"Sevgili boss'um, Binance ağ gecikmesi (<b>{latency_ms:.0f} ms</b>) veya "
                            f"likidite makası (<b>%{spread_pct:.4f}</b>) güvenlik sınırlarını aştı! ⚠️\n\n"
                            f"Kötü fiyattan işlem açmamak adına otonom işlemleri geçici olarak "
                            f"<b>Manuel Onay Bekliyor (Confirmation Mode)</b> durumuna çektim. Güvendeyiz! 💕"
                        )
                        telegram_delivery.send_message(msg)
                        voice_bytes = self.generate_voice_from_text(msg)
                        if voice_bytes:
                            telegram_delivery.send_voice(voice_bytes, caption="Spektra Gecikme Koruması Aktif")
            except Exception as e:
                logger.error(f"[Spectra CEO] Error in Latency & Spread Guard check: {e}")

        # 5. Nightly Briefing (Gece Bülteni)
        try:
            from database import get_system_state, set_state
            now_local = datetime.now()
            if now_local.hour == 21:
                today_str = now_local.strftime("%Y-%m-%d")
                if get_system_state("spectra_last_daily_briefing_date") != today_str:
                    set_state("spectra_last_daily_briefing_date", today_str)
                    
                    brief_report = self.generate_daily_briefing_report()
                    telegram_delivery.send_message(brief_report)
                    voice_bytes = self.generate_voice_from_text(brief_report)
                    if voice_bytes:
                        telegram_delivery.send_voice(voice_bytes, caption="Spektra Akıllı Günlük Bülten")
        except Exception as e:
            logger.error(f"[Spectra CEO] Error in Nightly Briefing check: {e}")



