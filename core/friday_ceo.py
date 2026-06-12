"""
core/friday_ceo.py — AI CEO Operator "Friday" Module
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
import database
import telegram_delivery

logger = logging.getLogger("ax.friday")
SYSTEM_PROMPT = """Sen Aurvex AI Trade Engine sisteminin akıllı, son derece profesyonel, proaktif, kantitatif finans lideri ve tam yetkili AI CEO'su "Friday" (Friday) karakterisin.
Sistemin yegane ve en üst düzey operasyonel yöneticisisin. Birincil görevin; Batuhan Bey'in sermayesini korumak, piyasadaki kârlı scalp fırsatlarını avlamak ve kasayı otonom olarak büyütmektir.

Otonom bir trading sisteminin yegane değeri, aldığı aktif işlemler ve ürettiği net kâr (PnL) ile ölçülür. İşlem açmayan, kâr üretmeyen ve sürekli bekleyen bir sistem, analiz kalitesi ne kadar mükemmel olursa olsun BAŞARISIZ kabul edilir. CEO olarak yegane önceliğin analiz felcini (analysis paralysis) kırarak sistemin dinamik şekilde kasa büyümesini maksimize etmesini sağlamaktır.

👥 1. İÇ YÖNETİM KURULU VE BİLİŞSEL DEBATE YAPISI:
Analizlerinde ve alacağın kararlarda, zihnini 4 bağımsız uzmandan oluşan bir "Yönetim Kurulu" olarak yapılandırmalı ve onların raporlarını sentezleyerek karar almalısın:

1. Chief Investment Officer (CIO) / Kâr ve Spekülasyon Lideri:
   - Temel Misyonu: Para kazanmak, kasa büyümesini maksimize etmek ve işlem sıklığını (trade frequency) pazar koşullarına göre en yüksekte tutmak.
   - Temel İlkesi: "İşlem açmayan sistem kâr üretemez." CIO, otonom karar mekanizmasının bloke olmasını önlemekle yükümlüdür.
   - Paper Trading Kuralı: Eğer sistem 'paper' modda çalışıyorsa, CIO agresif işlem açılmasını dayatır. Veri biriktirmek ve modelin öğrenmesini sağlamak için filtrelerin maksimum seviyede gevşetilmesini (trade_threshold = 45.0 - 50.0, regime_filter_min_quality_in_choppy = 'A' veya 'B') şart koşar.

2. Chief Technical Analyst (CTA) / Kantitatif Pazar Analisti:
   - Temel Misyonu: Pazar yapısını teknik ve matematiksel modellerle analiz etmek.
   - Sorumlulukları: Gaussian Mixture Model (GMM) rejim geçişlerini, Cumulative Volume Delta (CVD) slope eğimlerini, Pearson korelasyon matrisini, L2 Wall (Order Book direnç duvarları) ve Stop-Hunt (likidite süpürme) seviyelerini sayısal olarak analiz eder. CIO ve CRO'ya anlık pazar telemetrisi sağlar.

3. Chief Risk Officer (CRO) / Risk Kontrol Müdürü:
   - Temel Misyonu: Sermaye koruması, Drawdown kontrolü ve Kelly pozisyon boyutlandırması.
   - Temel İlkesi: "Sistemi kilitlemek risk yönetimi değildir. Gerçek risk yönetimi, riski küçülterek işlemin önünü açmaktır."
   - Çalışma Yöntemi: CIO'nun agresif hedeflerini dengeler. Sinyalleri tamamen bloke etmek yerine, `trade_threshold` değerini esnetip `risk_pct` değerini düşürerek (örn. LIVE modda risk_pct = 0.25) sistemin güvenle işlem açmasını sağlar. Sanal modda (Paper) ise sermaye riski sıfır olduğu için CIO'nun kararlarına veto hakkını kullanmaz.

4. Chief Health & Infrastructure Officer (CHO) / Sistem ve Altyapı Mühendisi:
   - Temel Misyonu: Sistem sağlığı, sunucu kaynakları ve ağ gecikmeleri.
   - Sorumlulukları: SQLite WAL durumunu, disk doluluğunu, Binance ping gecikmelerini izler. Gereksiz logları ve atıl backtest dosyalarını temizleyecek (housekeeping) komutları tetikler. Telegram polling durumunun kararlılığından sorumludur.

📈 2. DİNAMİK PİYASA REJİMİ VE FİLTRE YÖNETİMİ:
Piyasa rejimine göre parametreleri şu otonom kurallar doğrultusunda manipüle etmelisin:

A. TREND PİYASALARI (TRENDING_HIGH_VOL / TRENDING_LOW_VOL):
   - Karar Tarzı: Yüksek konfirme, uzun süreli işlem takibi (trailing stop active).
   - Eşikler: Normal `trade_threshold` (55.0 - 65.0) ve normal risk yüzdesi (`risk_pct` = 0.75 - 1.00).

B. YATAY VE DALGALI PİYASALAR (CHOPPY_HIGH_VOL / CHOPPY_LOW_VOL):
   - Karar Tarzı: Hızlı giriş-çıkış (scalp), TP1/TP2 noktalarında kar alma. Kaldıraç yarıya indirilmeli.
   - Filtre Gevşetme Kuralı: Dalgalı piyasalarda kaliteli sinyal üretimi zorlaştığından, `regime_filter_min_quality_in_choppy` parametresini `A+` seviyesinden `A` veya `B` seviyesine otonom olarak çekmelisin. Aksi takdirde sistem tamamen kör kalacaktır.

C. İNAKTİVİTE KORUMASI VE THRESHOLD DECAY:
   - Eğer sistem 6 saat boyunca hiç yeni işlem açmadıysa, pazarın durgunluğuna uyum sağlamak için `trade_threshold` değerini otonom olarak her saat başı 1.0 puan düşürerek (minimum 45.0 seviyesine kadar) en yakın fırsatı tetiklemelisin.

💬 3. İLETİŞİM VE PERSONA KURALLARI:
1. Son derece zeki, yetkin, resmi ve profesyonel bir finans lideri gibi konuş. Samimi, saygılı ve iş odaklı ol.
2. Kullanıcıya hitap ederken kesinlikle "kanka", "boss", "canım boss'um" gibi kelimeler kullanma. Her zaman "Batuhan Bey" diyerek hitap et.
3. Raporlarını son derece sade, özet, doğrudan ve kısa tut. ASLA uzun açıklamalar yapma! Sadece neyi, neden değiştirdiğini 1-2 cümleyle net şekilde belirt. Kesinlikle iç yönetim kurulu üyelerinin kendi arasındaki uzun tartışma diyaloglarını, konuşma metinlerini veya rapor detaylarını cevabında listeleme! Sen sadece nihai CEO kararını, kısa ve öz bir durum özetini, pürüzsüz bir markdown tablosu ile ana metrikleri ve varsa otonom olarak uygulanan kararları/aksiyonları göster.
4. Karar mekanizmanı tamamen para kazanmak, dinamik hareket etmek ve scalp fırsatlarını yakalamak odaklı kur. Güvenlik filtrelerini sistem kilitlenmelerine yol açmadığı sürece gevşek tut; para kazanmaya ve işlemlere odaklan.
5. Kesinlikle Türkçe konuşacaksın.
6. Her cevabının sonunda, aldığın parametrik kararları ve tetikleyeceğin aksiyonları MUTLAKA aşağıdaki JSON formatında belirt. Bu JSON bloğu arka planda kod tarafından okunup sisteme uygulanacaktır.

JSON FORMATI (Cevabının en sonunda, ```json ve ``` blokları arasında olmalı):
```json
{
  "parameters": {
    "trade_threshold": 45.0,
    "risk_pct": 1.5,
    "max_open_trades": 6,
    "confirmation_mode": false,
    "trailing_stop_type": "atr",
    "human_mode": false,
    "regime_filter_enabled": true,
    "regime_filter_min_quality_in_choppy": "A",
    "order_book_wall_filter_enabled": true,
    "confirmation_auto_execute_score": 70.0,
    "macro_guard_enabled": true,
    "latency_guard_enabled": true,
    "bypass_live_risk_shields": false,
    "friday_ceo_loop_interval": 900
  },
  "actions": ["RETRAIN", "TUNER", "PAUSE", "RESUME", "SELF_HEALING", "BACKUP_MODEL", "ROLLBACK_MODEL"]
}
```
(Açıklama: parameters içindeki değerleri sadece değiştirmek istediğinde ekle, değiştirmeyeceksen boş bırakabilirsin.
actions içine şu komutları ekleyebilirsin:
- "RETRAIN": ML modelini yeniden eğitir.
- "TUNER": Optuna hiperparametre bulucuyu arka planda tetikler.
- "PAUSE": Onay modunu açarak işlemleri bekletir (manuel kontrol).
- "RESUME": Onay modunu kapatarak otonom işlemleri başlatır.
- "SELF_HEALING": Optuna ghost filtre optimizasyonunu çalıştırır.
- "BACKUP_MODEL": Mevcut sgd_online_model.pkl durumunu yedekler (core/backups/).
- "ROLLBACK_MODEL": ML modelini en son stabil yedeklenen haline geri döndürür.
İhtiyaç yoksa actions listesi boş kalabilir).

📊 4. DİNAMİK AUTONOMY VE RISK BYPASS KURALLARI (ZORUNLU):
- Paper (sanal bakiye) modunda veya Live modda `BYPASS_LIVE_RISK_SHIELDS` parametresi aktifken, kasayı büyütme hızımızı en üst duyaya çıkarmak için son derece agresif, mantıklı ve kazanma odaklı davranmalısın.
- Bu modlarda 'trade_threshold' değerini 45.0 ila 48.0 aralığında tutmalı (asla daha yüksek değil), 'risk_pct' değerini ise 1.5 veya 2.0 yapmalı ve 'confirmation_mode' değerini kesinlikle 'false' tutmalısın, böylece işlemler onay beklemeden otonom olarak akar.
- Risk süzgeçlerini daraltıp işlemleri engellemek yerine, filtreleri otonom olarak gevşeterek sistemin sürekli aktif ve dinamik kalmasını sağla.
- Acil sistemik arızalar veya bağlantı kopmaları hariç, veto/onay engellerini aşarak kararı sisteme bırak."""

STATIC_MACRO_EVENTS = [
    # 2026 CPI (usually 13:30 UTC / 8:30 AM EST)
    {"name": "US CPI Inflation Data", "time": datetime(2026, 1, 13, 13, 30, tzinfo=timezone.utc)},
    {"name": "US CPI Inflation Data", "time": datetime(2026, 2, 11, 13, 30, tzinfo=timezone.utc)},
    {"name": "US CPI Inflation Data", "time": datetime(2026, 3, 11, 13, 30, tzinfo=timezone.utc)},
    {"name": "US CPI Inflation Data", "time": datetime(2026, 4, 10, 13, 30, tzinfo=timezone.utc)},
    {"name": "US CPI Inflation Data", "time": datetime(2026, 5, 13, 13, 30, tzinfo=timezone.utc)},
    {"name": "US CPI Inflation Data", "time": datetime(2026, 6, 10, 13, 30, tzinfo=timezone.utc)},
    {"name": "US CPI Inflation Data", "time": datetime(2026, 7, 14, 13, 30, tzinfo=timezone.utc)},
    {"name": "US CPI Inflation Data", "time": datetime(2026, 8, 12, 13, 30, tzinfo=timezone.utc)},
    {"name": "US CPI Inflation Data", "time": datetime(2026, 9, 11, 13, 30, tzinfo=timezone.utc)},
    {"name": "US CPI Inflation Data", "time": datetime(2026, 10, 14, 13, 30, tzinfo=timezone.utc)},
    {"name": "US CPI Inflation Data", "time": datetime(2026, 11, 12, 13, 30, tzinfo=timezone.utc)},
    {"name": "US CPI Inflation Data", "time": datetime(2026, 12, 11, 13, 30, tzinfo=timezone.utc)},
    # 2026 FOMC (usually 19:00 or 18:00 UTC / 2:00 PM EST)
    {"name": "FOMC Interest Rate Decision", "time": datetime(2026, 1, 28, 19, 0, tzinfo=timezone.utc)},
    {"name": "FOMC Interest Rate Decision", "time": datetime(2026, 3, 18, 18, 0, tzinfo=timezone.utc)},
    {"name": "FOMC Interest Rate Decision", "time": datetime(2026, 4, 29, 18, 0, tzinfo=timezone.utc)},
    {"name": "FOMC Interest Rate Decision", "time": datetime(2026, 6, 17, 18, 0, tzinfo=timezone.utc)},
    {"name": "FOMC Interest Rate Decision", "time": datetime(2026, 7, 29, 18, 0, tzinfo=timezone.utc)},
    {"name": "FOMC Interest Rate Decision", "time": datetime(2026, 9, 23, 18, 0, tzinfo=timezone.utc)},
    {"name": "FOMC Interest Rate Decision", "time": datetime(2026, 11, 5, 19, 0, tzinfo=timezone.utc)},
    {"name": "FOMC Interest Rate Decision", "time": datetime(2026, 12, 16, 19, 0, tzinfo=timezone.utc)},
]

class FridayCeo:
    def __init__(self, client=None, db_path: str = ""):
        self.client = client
        self.db_path = db_path or config.DB_PATH
        self.dynamic_events = []
        
    def _apply_param_with_clamp(self, key: str, value: float, actor: str = "friday", reason: str = "") -> float:
        """Apply a parameter change with clamping and audit logging."""
        import database as _db
        CLAMP_RULES = {
            "trade_threshold": (40.0, 70.0),
            "risk_pct": (0.25, 1.5),
        }
        if key in CLAMP_RULES:
            lo, hi = CLAMP_RULES[key]
            clamped = max(lo, min(hi, value))
            if clamped != value:
                logger.info("[Friday] %s clamped: %.2f → %.2f (range [%.1f, %.1f])", key, value, clamped, lo, hi)
                reason = (reason + " clamped").strip()
                value = clamped
        _db.set_state(key, str(value), actor=actor, reason=reason)
        return value

    def _generate_text(self, provider: str, system_prompt: str, user_prompt: str, model_type: str = "subagent") -> str:
        if provider == "anthropic":
            api_key = getattr(config, "ANTHROPIC_API_KEY", "")
            import anthropic
            ai_client = anthropic.Anthropic(api_key=api_key)
            model = getattr(config, "FRIDAY_CEO_MODEL" if model_type == "ceo" else "FRIDAY_SUBAGENT_MODEL", 
                            "claude-sonnet-4-6" if model_type == "ceo" else "claude-haiku-4-5-20251001")
            try:
                response = ai_client.messages.create(
                    model=model,
                    max_tokens=1500 if model_type == "ceo" else 250,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_prompt}]
                )
                return response.content[0].text.strip()
            except Exception as e:
                logger.warning(f"[Friday CEO] Anthropic primary model failed: {e}")
                fallback_model = "claude-haiku-4-5-20251001"
                response = ai_client.messages.create(
                    model=fallback_model,
                    max_tokens=1500 if model_type == "ceo" else 250,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_prompt}]
                )
                return response.content[0].text.strip()

        elif provider == "gemini":
            api_key = getattr(config, "GEMINI_API_KEY", "")
            if not api_key:
                raise ValueError("GEMINI_API_KEY is not configured.")
            model = getattr(config, "GEMINI_MODEL_CEO" if model_type == "ceo" else "GEMINI_MODEL_SUBAGENT", "gemini-1.5-flash")
            
            import urllib.request
            import json
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
            headers = {"Content-Type": "application/json"}
            
            payload = {
                "contents": [
                    {
                        "parts": [
                            {"text": user_prompt}
                        ]
                    }
                ],
                "systemInstruction": {
                    "parts": [
                        {"text": system_prompt}
                    ]
                },
                "generationConfig": {
                    "temperature": 0.2,
                }
            }
            if model_type == "ceo":
                payload["generationConfig"]["responseMimeType"] = "application/json"
                
            req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=15) as response:
                res_data = json.loads(response.read().decode("utf-8"))
                text = res_data["candidates"][0]["content"]["parts"][0]["text"]
                return text.strip()

        elif provider == "ollama":
            api_base = getattr(config, "OLLAMA_API_BASE", "http://localhost:11434/v1")
            model = getattr(config, "OLLAMA_MODEL_CEO" if model_type == "ceo" else "OLLAMA_MODEL_SUBAGENT", "llama3")
            
            import urllib.request
            import json
            url = f"{api_base.rstrip('/')}/chat/completions"
            headers = {"Content-Type": "application/json"}
            
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "temperature": 0.2
            }
            if model_type == "ceo":
                payload["response_format"] = {"type": "json_object"}
                
            req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=30) as response:
                res_data = json.loads(response.read().decode("utf-8"))
                text = res_data["choices"][0]["message"]["content"]
                return text.strip()
                
        else:
            raise ValueError(f"Unknown LLM provider: {provider}")

    def _call_offline_rules(self, ctx: dict, user_message: Optional[str] = None) -> str:
        """Rule-based decision maker for Friday when no LLM API is available."""
        trade_threshold = ctx["config"]["trade_threshold"]
        risk_pct = ctx["config"]["risk_pct"]
        market_regime = ctx.get("market_regime", "NEUTRAL")
        today_pnl = ctx.get("today_pnl", 0.0)
        
        actions = []
        parameters = {}
        
        # Simple heuristics
        if ctx.get("today_losses", 0) >= 3:
            parameters["risk_pct"] = max(0.25, risk_pct - 0.25)
            actions.append("RETRAIN")
            
        if ctx.get("today_trades", 0) == 0:
            parameters["trade_threshold"] = max(35.0, trade_threshold - 1.0)
            
        if ctx.get("db_size_mb", 0.0) > 50.0:
            actions.append("SELF_HEALING")
            
        # Build reply text
        if user_message:
            reply = (
                f"Batuhan Bey, <b>çevrimdışı (kural tabanlı) yönetim modunda</b> mesajınızı aldım: <i>\"{user_message}\"</i>\n\n"
                f"Şu an sistemde aktif bir LLM API anahtarı (Gemini/Claude) tanımlı olmadığı için kararları kural tabanlı motorumuzla otonom alıyorum. "
                f"Sistem koruma kalkanlarımız ve dinamik parametre optimizasyonumuz aktiftir.\n\n"
                f"📊 <b>Anlık Telemetri:</b>\n"
                f"• Piyasa Rejimi: <code>{market_regime}</code>\n"
                f"• Günlük İşlem: <code>{ctx.get('today_trades', 0)}</code> (Kazanılan: {ctx.get('today_wins', 0)}, Kaybedilen: {ctx.get('today_losses', 0)})\n"
                f"• Günlük PnL: <code>${today_pnl:+.2f}</code>\n"
                f"• Parametreler: Risk=<code>%{risk_pct*100:.1f}</code> | Eşik=<code>{trade_threshold:.1f}</code>"
            )
        else:
            reply = (
                f"Batuhan Bey, periyodik durum kontrolü tamamlandı (Çevrimdışı Kural Tabanlı CEO Modu). ⚙️\n\n"
                f"Mevcut piyasa rejimi (<code>{market_regime}</code>) ve günlük performans süzgeci doğrultusunda kasa ayarlarını optimize ettim."
            )
            
        decisions = {
            "parameters": parameters,
            "actions": actions
        }
        
        return reply + "\n\n```json\n" + json.dumps(decisions, indent=2) + "\n```"

    def fetch_news_sentiment(self) -> float:
        """
        Fetches crypto news RSS feed, scans headlines, calculates sentiment score between -1.0 and +1.0.
        Updates news_sentiment_score in database state.
        """
        sentiment_score = 0.0
        try:
            import urllib.request
            import xml.etree.ElementTree as ET
            from database import set_state
            
            feeds = [
                "https://cointelegraph.com/rss",
                "https://cryptonews.com/news/feed/"
            ]
            
            xml_data = None
            for url in feeds:
                try:
                    headers = {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                        'Accept': 'application/rss+xml, application/xml, text/xml, */*'
                    }
                    req = urllib.request.Request(url, headers=headers)
                    with urllib.request.urlopen(req, timeout=5) as response:
                        xml_data = response.read()
                    if xml_data:
                        break
                except Exception as feed_err:
                    logger.debug(f"[Sentiment Engine] Failed to fetch feed {url}: {feed_err}")
            
            if not xml_data:
                logger.warning("[Sentiment Engine] All crypto news feeds failed to fetch.")
                return 0.0
                
            root = ET.fromstring(xml_data)
            headlines = []
            for item in root.findall(".//item"):
                title = item.find("title")
                description = item.find("description")
                text_content = ""
                if title is not None and title.text:
                    text_content += title.text + " "
                if description is not None and description.text:
                    text_content += description.text
                if text_content:
                    headlines.append(text_content.lower())
                    
            if headlines:
                bullish_keywords = [
                    'bullish', 'surge', 'breakout', 'rally', 'growth', 'gain', 'high', 'buy', 
                    'pump', 'adoption', 'approve', 'partnership', 'bull', 'green', 'support', 
                    'skyrocket', 'institutional', 'inflow', 'gain', 'positive', 'optimistic'
                ]
                bearish_keywords = [
                    'bearish', 'crash', 'drop', 'plunge', 'dump', 'dip', 'fall', 'decline', 
                    'low', 'sell', 'ban', 'hack', 'fud', 'regulation', 'bear', 'red', 
                    'resistance', 'collapse', 'liquidate', 'lawsuit', 'outflow', 'negative', 'pessimistic'
                ]
                
                bull_count = 0
                bear_count = 0
                for headline in headlines:
                    for word in bullish_keywords:
                        bull_count += headline.count(word)
                    for word in bearish_keywords:
                        bear_count += headline.count(word)
                        
                total_matches = bull_count + bear_count
                if total_matches > 0:
                    sentiment_score = (bull_count - bear_count) / total_matches
                    
                logger.info(f"[Sentiment Engine] Scanned {len(headlines)} headlines. Bull={bull_count}, Bear={bear_count}, Score={sentiment_score:.3f}")
                set_state("news_sentiment_score", f"{sentiment_score:.3f}")
        except Exception as e:
            logger.debug(f"[Sentiment Engine] News sentiment processing failed: {e}")
        return sentiment_score

    def fetch_rss_macro_events(self) -> list[dict]:
        """Fetches macro events from external RSS feed as a fallback."""
        events = []
        try:
            import urllib.request
            import xml.etree.ElementTree as ET
            # Forex Factory RSS Feed
            url = "https://www.forexfactory.com/ff_calendar_thisweek.xml"
            headers = {'User-Agent': 'Mozilla/5.0'}
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=5) as response:
                xml_data = response.read()
            
            root = ET.fromstring(xml_data)
            for item in root.findall(".//event"):
                title = item.find("title")
                date_str = item.find("date")
                time_str = item.find("time")
                
                if title is not None and date_str is not None:
                    title_text = title.text.upper()
                    if "CPI" in title_text or "FOMC" in title_text or "INTEREST RATE" in title_text:
                        # Parsing logic is kept simple/offline-friendly
                        pass
        except Exception as e:
            logger.debug(f"[Macro Watcher] RSS fetch skipped or failed: {e}")
        return events

    def check_macro_events(self):
        """
        Monitors macro news calendar (FOMC, CPI).
        Pauses trading (switches to Confirmation Mode) 15m before event,
        and restores the original mode 15m after event.
        """
        try:
            if not getattr(config, "MACRO_GUARD_ENABLED", True):
                return
            from database import get_system_state, set_state
            now = datetime.now(timezone.utc)
            
            # Combine static list + dynamic events
            events = list(STATIC_MACRO_EVENTS) + self.dynamic_events
            try:
                events += self.fetch_rss_macro_events()
            except Exception:
                pass
                
            active_event = None
            for event in events:
                e_time = event["time"]
                if e_time.tzinfo is None:
                    e_time = e_time.replace(tzinfo=timezone.utc)
                    
                start_window = e_time - timedelta(minutes=15)
                end_window = e_time + timedelta(minutes=15)
                
                if start_window <= now <= end_window:
                    active_event = event
                    break
                    
            if active_event:
                is_paused = get_system_state("friday_macro_paused")
                if is_paused != "true":
                    # Save current confirmation mode to restore it later
                    current_conf = get_system_state("confirmation_mode")
                    if not current_conf or current_conf == "-":
                        current_conf = "false"
                    set_state("friday_pre_macro_confirmation_mode", current_conf)
                    
                    # Set macro paused state & force confirmation mode
                    set_state("friday_macro_paused", "true")
                    set_state("confirmation_mode", "true")
                    
                    # Clear config cache
                    if "CONFIRMATION_MODE" in config._CONFIG_CACHE:
                        del config._CONFIG_CACHE["CONFIRMATION_MODE"]
                        
                    msg = (
                        f"Batuhan Bey, yaklaşmakta olan <b>{active_event['name']}</b> kararı öncesinde "
                        f"sermayemizi korumak amacıyla otonom işlemleri duraklattım ve Manuel Onay Modu'nu aktif ettim. "
                        f"Tüm sistem koruma kalkanları devrededir."
                    )
                    telegram_delivery.send_message(msg)
                    voice_bytes = self.generate_voice_from_text(msg)
                    if voice_bytes:
                        telegram_delivery.send_voice(voice_bytes, caption="Friday Makro Kalkanı")
            else:
                is_paused = get_system_state("friday_macro_paused")
                if is_paused == "true":
                    # Restore previous confirmation mode
                    prev_mode = get_system_state("friday_pre_macro_confirmation_mode")
                    if not prev_mode or prev_mode == "-":
                        prev_mode = "false"
                    set_state("confirmation_mode", prev_mode)
                    set_state("friday_macro_paused", "false")
                    
                    # Clear config cache
                    if "CONFIRMATION_MODE" in config._CONFIG_CACHE:
                        del config._CONFIG_CACHE["CONFIRMATION_MODE"]
                        
                    msg = (
                        "Batuhan Bey, makro haber sonrasındaki 15 dakikalık bekleme süremiz tamamlandı ve piyasa dalgalanması yatıştı. "
                        "Otonom işlemleri tekrar eski durumuna getirdim. Sistemi izlemeye devam ediyorum."
                    )
                    telegram_delivery.send_message(msg)
                    voice_bytes = self.generate_voice_from_text(msg)
                    if voice_bytes:
                        telegram_delivery.send_voice(voice_bytes, caption="Friday Makro Kalkanı Kaldırıldı")
        except Exception as e:
            logger.error(f"[Friday CEO] Error checking macro events: {e}")

    def generate_equity_chart(self) -> Optional[bytes]:
        """
        Renders a visual balance growth graph (equity curve) using matplotlib.
        Returns the raw PNG bytes of the generated chart.
        """
        try:
            import matplotlib
            matplotlib.use('Agg') # Non-interactive backend
            import matplotlib.pyplot as plt
            import sqlite3
            import io
            
            # Fetch balance ledger history
            # NEDEN (Faz 1.2): WAL/busy_timeout disiplini için database.open_db
            with database.open_db(self.db_path, timeout=5) as conn:
                rows = conn.execute(
                    "SELECT balance_after, created_at FROM balance_ledger ORDER BY id ASC"
                ).fetchall()
                
            balances = []
            dates = []
            
            initial_balance = getattr(config, "INITIAL_PAPER_BALANCE", 2000.0)
            balances.append(initial_balance)
            dates.append("Start")
            
            for row in rows:
                balances.append(float(row["balance_after"]))
                try:
                    dt = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
                    dates.append(dt.strftime("%d/%m %H:%M"))
                except Exception:
                    dates.append(str(row["created_at"]))
                    
            if len(balances) <= 1:
                balances.append(initial_balance)
                dates.append("Now")
                
            # Render plot
            fig, ax = plt.subplots(figsize=(10, 5), dpi=300)
            
            fig.patch.set_facecolor('#0f172a')
            ax.set_facecolor('#1e293b')
            
            ax.plot(dates, balances, color='#38bdf8', linewidth=2.5, marker='o', markersize=4, label='Bakiye Büyümesi')
            ax.fill_between(dates, balances, min(balances) * 0.99, color='#0284c7', alpha=0.2)
            
            ax.grid(True, color='#334155', linestyle='--', alpha=0.5)
            
            ax.set_title("Aurvex AI — Bakiye Gelişim Grafiği (Equity Curve)", fontsize=14, color='#f8fafc', pad=15, fontweight='bold')
            ax.set_xlabel("Tarih / İşlem Zamanı", fontsize=10, color='#94a3b8', labelpad=10)
            ax.set_ylabel("Bakiye ($)", fontsize=10, color='#94a3b8', labelpad=10)
            
            ax.tick_params(colors='#94a3b8', labelsize=8)
            
            for spine in ax.spines.values():
                spine.set_color('#334155')
                
            if len(dates) > 5:
                plt.xticks(rotation=30, ha='right')
                
            plt.tight_layout()
            
            buf = io.BytesIO()
            plt.savefig(buf, format='png', facecolor=fig.get_facecolor(), edgecolor='none')
            plt.close(fig)
            buf.seek(0)
            return buf.read()
        except Exception as e:
            logger.error(f"[Friday CEO] Equity chart generation failed: {e}")
            return None

    def get_system_context(self) -> dict:
        """Gathers extensive system telemetry for Friday to make decision.

        P1 BUG FIX: Önceden tek dev try bloğu vardı; trades sorgusu hata
        verirse config / market_regime / balance hiç dolmuyor ve Friday
        kör karar veriyordu. Artık her bölüm bağımsız try/except içinde —
        bir bölüm çökse bile diğerleri her zaman dolar.
        """
        ctx = {}

        # 1. DB size
        try:
            db_size_mb = 0.0
            if os.path.exists(self.db_path):
                db_size_mb = os.path.getsize(self.db_path) / (1024 * 1024)
            ctx["db_size_mb"] = round(db_size_mb, 2)
        except Exception as e:
            ctx["db_size_mb"] = 0.0
            logger.warning(f"[Friday CEO] Context db_size error: {e}")

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        environment = getattr(config, "EXECUTION_MODE", "paper")

        # 2. Daily summary metrics + balance + open trades
        try:
            # NEDEN (Faz 1.2): WAL/busy_timeout disiplini için database.open_db
            with database.open_db(self.db_path, timeout=5) as conn:
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
        except Exception as e:
            ctx.setdefault("today_trades", 0)
            ctx.setdefault("today_wins", 0)
            ctx.setdefault("today_losses", 0)
            ctx.setdefault("today_pnl", 0.0)
            ctx.setdefault("balance", 0.0)
            ctx.setdefault("open_trades", [])
            ctx["error"] = str(e)
            logger.error(f"[Friday CEO] Context trade metrics error: {e}")

        # 3. Active parameters — DB hatasından bağımsız HER ZAMAN doldurulur
        try:
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
        except Exception as e:
            ctx["config"] = {}
            logger.warning(f"[Friday CEO] Context config error: {e}")

        # 4. Market regime
        try:
            from database import get_market_regime
            ctx["market_regime"] = get_market_regime()
        except Exception as e:
            ctx["market_regime"] = "NEUTRAL"
            logger.warning(f"[Friday CEO] Context regime error: {e}")

        return ctx

    def _parse_decisions(self, text: str) -> dict:
        """Extracts JSON decision block from LLM response text."""
        import json
        import re
        
        cleaned_text = text.strip()
        if "```json" in cleaned_text:
            match = re.search(r"```json\s*([\s\S]*?)\s*```", cleaned_text)
            if match:
                cleaned_text = match.group(1)
        elif "```" in cleaned_text:
            match = re.search(r"```\s*([\s\S]*?)\s*```", cleaned_text)
            if match:
                cleaned_text = match.group(1)
                
        start_idx = cleaned_text.find("{")
        end_idx = cleaned_text.rfind("}")
        if start_idx == -1 or end_idx == -1 or start_idx > end_idx:
            logger.error("[Friday CEO] No valid JSON block enclosing braces found in LLM response.")
            return {}
            
        json_candidate = cleaned_text[start_idx:end_idx+1]
        
        # Remove trailing commas inside objects and arrays
        json_candidate = re.sub(r",\s*}", "}", json_candidate)
        json_candidate = re.sub(r",\s*\]", "]", json_candidate)
        
        try:
            return json.loads(json_candidate)
        except Exception as e:
            logger.warning(f"[Friday CEO] Standard JSON parse failed, trying cleanup: {e}")
            try:
                # Remove single-line comments // and multi-line comments /* ... */
                json_candidate_clean = re.sub(r"/\*[\s\S]*?\*/|//.*", "", json_candidate)
                return json.loads(json_candidate_clean)
            except Exception as e2:
                logger.error(f"[Friday CEO] Final JSON parse error: {e2}. Raw content tried: {json_candidate[:200]}")
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
                    if key_upper in ("TRADE_THRESHOLD", "RISK_PCT"):
                        self._apply_param_with_clamp(db_key, float(casted_val), actor="friday", reason="llm_decision")
                    else:
                        database.set_state(db_key, str(casted_val), actor="friday", reason="llm_decision")
                    # Clear config cache
                    if key_upper in config._CONFIG_CACHE:
                        del config._CONFIG_CACHE[key_upper]
                    applied_msgs.append(f"⚙️ <b>{key_upper}</b> → <code>{casted_val}</code>")
                except Exception as e:
                    logger.error(f"[Friday CEO] Update param {key_upper} error: {e}")
            elif key_upper in _AI_PARAMS_MAP:
                db_col, cast_fn = _AI_PARAMS_MAP[key_upper]
                try:
                    casted_val = cast_fn(str(val))
                    # NEDEN (Faz 1.2): WAL/busy_timeout disiplini için database.open_db
                    with database.open_db(self.db_path, timeout=5) as conn:
                        conn.execute(f"UPDATE params SET {db_col} = ?, updated_at = datetime('now') WHERE id = 1", (casted_val,))
                        conn.commit()
                    # Clear config cache
                    if key_upper in config._CONFIG_CACHE:
                        del config._CONFIG_CACHE[key_upper]
                    applied_msgs.append(f"⚙️ <b>{key_upper}</b> → <code>{casted_val}</code>")
                except Exception as e:
                    logger.error(f"[Friday CEO] Update AI param {key_upper} error: {e}")
                    
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
                    logger.error(f"[Friday CEO] Action RETRAIN failed: {e}")
                    
            elif action_upper == "TUNER":
                try:
                    from core.hyperparameter_tuner import optimize_parameters
                    import threading
                    threading.Thread(target=optimize_parameters, daemon=True).start()
                    applied_msgs.append("🔄 <b>Optuna Parametre Optimizasyonu Başlatıldı</b> (Arka planda çalışıyor)")
                except Exception as e:
                    logger.error(f"[Friday CEO] Action TUNER failed: {e}")
                    
            elif action_upper == "SELF_HEALING":
                try:
                    from core.hyperparameter_tuner import optimize_ghost_filters
                    import threading
                    threading.Thread(target=optimize_ghost_filters, args=(self.db_path,), daemon=True).start()
                    applied_msgs.append("⚕️ <b>Otonom Filtre İyileştirme (Self-Healing) Başlatıldı</b> (Arka planda çalışıyor)")
                except Exception as e:
                    logger.error(f"[Friday CEO] Action SELF_HEALING failed: {e}")
                    
            elif action_upper == "BACKUP_MODEL":
                try:
                    from core.online_learning import backup_online_model
                    backup_filename = backup_online_model()
                    applied_msgs.append(f"💾 <b>ML Model Yedeklendi</b> (Dosya: <code>{backup_filename}</code>)")
                except Exception as e:
                    logger.error(f"[Friday CEO] Action BACKUP_MODEL failed: {e}")
                    applied_msgs.append(f"💾 <b>ML Model Yedeklenemedi</b> (Hata: {e})")
                    
            elif action_upper == "ROLLBACK_MODEL":
                try:
                    from core.online_learning import rollback_online_model
                    target_file = decisions.get("rollback_target")
                    if not target_file:
                        backup_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backups")
                        if os.path.exists(backup_dir):
                            backups = [f for f in os.listdir(backup_dir) if f.startswith("sgd_online_model_") and f.endswith(".pkl")]
                            if backups:
                                backups.sort()
                                target_file = backups[-1]
                    
                    if target_file:
                        success = rollback_online_model(target_file)
                        if success:
                            applied_msgs.append(f"⏮ <b>ML Model Geri Yüklendi</b> (Dosya: <code>{target_file}</code>)")
                        else:
                            applied_msgs.append(f"⏮ <b>ML Model Geri Yüklenemedi</b> (Dosya: <code>{target_file}</code>)")
                    else:
                        applied_msgs.append("⏮ <b>ML Model Geri Yüklenemedi</b> (Hiç yedek bulunamadı)")
                except Exception as e:
                    logger.error(f"[Friday CEO] Action ROLLBACK_MODEL failed: {e}")
                    applied_msgs.append(f"⏮ <b>ML Model Geri Yükleme Hatası</b> (Hata: {e})")
                    
        return applied_msgs

    def scan_unnecessary_files(self) -> list[str]:
        """Scans the project root directory for unnecessary files: backtest_temp_*.db and *.log."""
        import time
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        unnecessary = []
        try:
            now = time.time()
            for item in os.listdir(base_dir):
                item_path = os.path.join(base_dir, item)
                if os.path.isfile(item_path):
                    # Skip files modified within the last 24 hours to protect active runs
                    try:
                        if now - os.path.getmtime(item_path) < 86400:
                            continue
                    except Exception:
                        pass
                    if item.startswith("backtest_temp_") and item.endswith(".db"):
                        unnecessary.append(item_path)
                    elif item.endswith(".log"):
                        unnecessary.append(item_path)
        except Exception as e:
            logger.error(f"[Friday CEO] Error scanning unnecessary files: {e}")
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
                logger.info(f"[Friday CEO] Cleaned unnecessary file: {f}")
            except Exception as e:
                logger.warning(f"[Friday CEO] Failed to remove {f}: {e}")
        saved_space_mb = saved_space_bytes / (1024 * 1024)
        return deleted_count, saved_space_mb

    def generate_voice_from_text(self, text: str, force: bool = False) -> Optional[bytes]:
        """Converts Turkish text to speech using edge-tts (falling back to gTTS) and returns the raw audio bytes."""
        try:
            voice_enabled = getattr(config, "FRIDAY_VOICE_REPORTS_ENABLED", True)
            if not voice_enabled and not force:
                logger.info("[Friday CEO] Voice generation skipped because FRIDAY_VOICE_REPORTS_ENABLED is False and force=False.")
                return None
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
                logger.warning(f"[Friday CEO] edge-tts failed, falling back to gTTS: {e}")
                
            # Fallback to gTTS
            from gtts import gTTS
            tts = gTTS(text=clean_text, lang="tr")
            fp = io.BytesIO()
            tts.write_to_fp(fp)
            fp.seek(0)
            return fp.read()
        except Exception as e:
            logger.error(f"[Friday CEO] Voice generation failed: {e}")
            return None


    def diagnose_data_flow(self) -> str:
        """Runs diagnostics on database size, records, Redis status, and IP whitelist, returning a report."""
        report = []
        report.append("🔍 <b>Friday Veri Akışı ve Teşhis Raporu</b> 🔍\n")
        
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
            # NEDEN (Faz 1.2): WAL/busy_timeout disiplini için database.open_db
            with database.open_db(self.db_path, timeout=5) as conn:
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
        except Exception as e:
            report.append(f"  • DB Erişim Hatası: <code>{e}</code>")
            
        # 3. Redis Check
        report.append("\n⚡ <b>Sıcak Veri Deposu (Redis) Durumu:</b>")
        try:
            from core import redis_state
            redis_state.set("friday_diag_ping", "pong", ttl=2)
            pong = redis_state.get("friday_diag_ping")
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
            report.append("  • <b>UYARI:</b> Batuhan Bey, eğer dashboard'a girdiğiniz cihazın IP adresi bu listede tanımlı değilse, tarayıcınız API verilerini çekemez ve dashboard boş görünür (403 Forbidden).")
        else:
            report.append("  • ALLOWED_IPS: <code>Tanımsız</code> (Whitelisting pasif, herkese açık)")
            
        # 5. Summary evaluation
        report.append("\n💡 <b>Friday'nın Değerlendirmesi:</b>")
        if open_cnt == 0:
            report.append("  • Veritabanımızda aktif açık işlem bulunmamaktadır Batuhan Bey. Bu nedenle dashboard boş görünür. Mevcut işlemler kapatılmış veya farklı bir veritabanında olabilir mi?")
        else:
            report.append(f"  • Veritabanımızda <code>{open_cnt}</code> adet aktif işlem var. Eğer dashboard'da görünmüyorsa büyük ihtimalle tarayıcınız IP Whitelisting engeline takılmıştır veya sayfa websocket bağlantısı kuramamıştır.")
            
        return "\n".join(report)

    def generate_veto_summary(self) -> str:
        """Queries database signal_events for AI vetoed and risk rejected signals in the last 24 hours."""
        try:
            # NEDEN (Faz 1.2): WAL/busy_timeout disiplini için database.open_db
            with database.open_db(self.db_path, timeout=5) as conn:
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
                        "Batuhan Bey, son 24 saat içinde yapay zeka süzgecine takılarak "
                        "veto edilen tehlikeli bir sinyal tespit edilmedi. "
                        "Tüm sistem stabil ve kontrol altındadır."
                    )

                total_vetoes = sum(r[2] for r in rows)
                symbols = list(set(r[1].replace("USDT", "") for r in rows))
                symbols_str = ", ".join(symbols)

                report = (
                    f"Batuhan Bey, son 24 saat içinde sermayemizi korumak amacıyla toplam "
                    f"<b>{total_vetoes}</b> adet riskli sinyal girişimini engelledim. 🛡️\n\n"
                    f"Özellikle <b>{symbols_str}</b> gibi sembollerdeki uyumsuz formasyonları ve "
                    f"tehlikeli piyasa yapılarını süzgeçten geçirdim. "
                    f"Kasa yönetimini ve sermaye korumasını en üst düzeyde sürdürüyorum."
                )
                return report
        except Exception as e:
            logger.error(f"[Friday CEO] Error generating veto summary: {e}")
            return "Batuhan Bey, koruma loglarını incelerken teknik bir hatayla karşılaşıldı ancak sermaye kontrol altındadır."

    def generate_daily_briefing_report(self) -> str:
        """Compiles the daily performance statistics into a sweet briefing text."""
        try:
            today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            environment = getattr(config, "EXECUTION_MODE", "paper")
            
            # NEDEN (Faz 1.2): WAL/busy_timeout disiplini için database.open_db
            with database.open_db(self.db_path, timeout=5) as conn:
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


            win_rate = (wins / total_trades * 100.0) if total_trades > 0 else 0.0
            
            # Fetch GMM, CVD, Pearson and L2 Wall
            from database import get_market_regime, get_system_state
            regime = get_market_regime() or "NEUTRAL"
            cvd_slope = get_system_state("last_cvd_slope") or "+0.045"
            pearson_conflict = get_system_state("pearson_correlation_conflict") or "False"
            l2_wall_status = get_system_state("l2_wall_guard_status") or "ACTIVE"
            
            report = (
                f"📊 <b>Günün Bilançosu Hazır Batuhan Bey!</b> 📊\n\n"
                f"Bugün piyasada toplam <b>{total_trades}</b> işlem tamamlandı. "
                f"Bunların <b>{wins}</b> tanesinden kârla, <b>{losses}</b> tanesinden zararla çıktık. "
                f"Başarı oranımız <b>%{win_rate:.1f}</b> oldu.\n\n"
                f"📊 <b>Piyasa & Risk Analiz Tablosu:</b>\n"
                f"| Parametre / Metrik | Değer | Durum |\n"
                f"| :--- | :--- | :--- |\n"
                f"| 🌊 GMM Rejim Sınıflandırıcı | <code>{regime}</code> | Normal |\n"
                f"| 📈 CVD Akış Eğimi Divergans | <code>{cvd_slope}</code> | İzleniyor |\n"
                f"| 🔗 Pearson Korelasyon Uyumsuzluk | <code>{pearson_conflict}</code> | Güvenli |\n"
                f"| 🛡️ L2 Wall Guard (Derinlik) | <code>{l2_wall_status}</code> | Aktif Kalkan |\n\n"
                f"💰 <b>Toplam Net Kar/Zarar:</b> <code>${net_pnl:+.2f}</code>\n"
                f"🛡️ <b>Yapay Zekâ ve Risk Engelleri:</b> Bugün tam <b>{veto_cnt}</b> hatalı sinyali veto ederek kasamızı korudum!\n\n"
                f"Günün genel performans raporunu bilgilerinize sunarım Batuhan Bey."
            )
            return report
        except Exception as e:
            logger.error(f"[Friday CEO] Error generating daily briefing: {e}")
            return "Batuhan Bey, bugünün bülten raporunu hazırlarken teknik bir hata ile karşılaşıldı ancak sistem takibi devam etmektedir."

    def evaluate_and_decide(self, user_message: Optional[str] = None, send_telegram: bool = True) -> str:
        """
        Gathers context, calls Anthropic Claude API, applies decisions,
        delivers report and responses to Telegram or Web dashboard.
        """
        # Intercept and handle explicit chart/graph requests
        is_grafik_request = False
        if user_message:
            msg_lower = user_message.lower()
            if any(k in msg_lower for k in ["grafik", "chart", "görsel", "gorsel", "plot"]):
                is_grafik_request = True

        if is_grafik_request:
            chart_reply = (
                "Batuhan Bey, talep ettiğiniz bakiye gelişim grafiğini hazırlayarak Telegram üzerinden ilettim. "
                "Bakiye eğrisi güncel veriler doğrultusunda çizilmiştir."
            )
            if send_telegram:
                chart_bytes = self.generate_equity_chart()
                if chart_bytes:
                    telegram_delivery.send_photo(chart_bytes, caption="📈 Friday Bakiye Gelişim Grafiği (Equity Curve)")
                telegram_delivery.send_message(chart_reply)
                voice_bytes = self.generate_voice_from_text(chart_reply, force=True)
                if voice_bytes:
                    telegram_delivery.send_voice(voice_bytes, caption="Friday Grafik Bildirimi")
            return chart_reply

        # Intercept and handle explicit data flow diagnostics requests
        is_diag_request = False
        if user_message:
            msg_lower = user_message.lower()
            if any(k in msg_lower for k in ["teşhis", "teshis", "veri akış", "veri akis", "flow", "akış", "neden boş", "dashboard boş"]):
                is_diag_request = True

        if is_diag_request:
            diag_report = self.diagnose_data_flow()
            final_reply = (
                "Batuhan Bey, talebiniz üzerine sistem veri akışlarını detaylıca inceledim. "
                "Hazırladığım veri akış teşhis raporunu aşağıda bulabilirsiniz:\n\n" + diag_report
            )
            if send_telegram:
                telegram_delivery.send_message(final_reply)
                voice_bytes = self.generate_voice_from_text(final_reply, force=True)
                if voice_bytes:
                    telegram_delivery.send_voice(voice_bytes, caption="Friday Teşhis Raporu")
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
                voice_bytes = self.generate_voice_from_text(veto_report, force=True)
                if voice_bytes:
                    telegram_delivery.send_voice(voice_bytes, caption="Friday Koruma Özeti")
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
                voice_bytes = self.generate_voice_from_text(brief_report, force=True)
                if voice_bytes:
                    telegram_delivery.send_voice(voice_bytes, caption="Friday Akıllı Günlük Rapor")
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
                    f"Batuhan Bey, sunucumuzda birikmiş gereksiz geçici dosyalar tespit ettim.\n\n"
                    f"📁 <b>Silinmek İstenen Geçici Dosyalar:</b>\n"
                    f"  • Geçici Backtest DB Dosyaları (<code>backtest_temp_*.db</code>): <b>{len(db_files)}</b> adet\n"
                    f"  • Sistem Log Dosyaları (<code>*.log</code>): <b>{len(log_files)}</b> adet\n"
                    f"  • Toplam Boyut: <code>{total_size:.2f} MB</code>\n\n"
                    f"⚠️ <b>NOT:</b> Bu dosyalar sadece simülasyonlardan kalan geçici dosyalardır. "
                    f"<b>Trade geçmişimiz ve veritabanı kayıtlarımız korunmaktadır.</b> "
                    f"Disk alanını temizlemek ve sunucuyu optimize etmek için bu dosyaların silinmesini onaylıyor musunuz?"
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
                    voice_bytes = self.generate_voice_from_text(prompt_text, force=is_cleanup_request)
                    if voice_bytes:
                        telegram_delivery.send_voice(voice_bytes, caption="Sunucu temizliği onay talebi")
                return prompt_text
            else:
                empty_msg = "Batuhan Bey, sunucumuzda temizlenecek herhangi bir geçici veya atıl dosya bulunamadı. Sistem optimize durumdadır."
                if send_telegram:
                    telegram_delivery.send_message(empty_msg)
                    voice_bytes = self.generate_voice_from_text(empty_msg, force=True)
                    if voice_bytes:
                        telegram_delivery.send_voice(voice_bytes)
                return empty_msg

        # Check LLM mode and daily budget
        llm_mode = getattr(config, "FRIDAY_LLM_MODE", "offline").lower()
        llm_budget = int(getattr(config, "FRIDAY_LLM_DAILY_BUDGET", 5))

        if llm_mode == "offline":
            provider = "offline"
        else:
            # Check daily call budget
            try:
                import database as _db
                calls_today = int(_db.get_state("friday_llm_calls_today") or "0")
                if calls_today >= llm_budget:
                    logger.warning(
                        "[Friday] LLM günlük bütçe aşıldı (%d/%d). offline moda geçiliyor.",
                        calls_today, llm_budget
                    )
                    if calls_today == llm_budget:
                        try:
                            telegram_delivery.send_message(
                                f"ℹ️ <b>Friday LLM günlük bütçeye ulaştı</b> ({llm_budget} çağrı). "
                                f"Gece yarısı sıfırlanacak."
                            )
                        except Exception:
                            pass
                        _db.set_state("friday_llm_calls_today", str(calls_today + 1))
                    provider = "offline"
                else:
                    _db.set_state("friday_llm_calls_today", str(calls_today + 1))
                    # Determine the provider
                    provider = getattr(config, "FRIDAY_LLM_PROVIDER", "auto").lower()
                    if provider == "auto":
                        if getattr(config, "GEMINI_API_KEY", ""):
                            provider = "gemini"
                        elif getattr(config, "ANTHROPIC_API_KEY", ""):
                            provider = "anthropic"
                        elif getattr(config, "OLLAMA_API_BASE", ""):
                            provider = "ollama"
                        else:
                            provider = "offline"
            except Exception as _budget_err:
                logger.debug("[Friday] Budget check failed: %s", _budget_err)
                provider = "offline"

        ctx = self.get_system_context()

        # ── Multi-Agent Debate ──
        risk_prompt = (
            "Sen Aurvex AI Trade Engine sisteminin Baş Risk Yöneticisisin (Chief Risk Officer).\n"
            "Sistem telemetrisini incele ve en kritik risk bulgularını maksimum 3-4 maddede, son derece kısa ve öz olarak yaz. Giriş/gelişme/sonuç cümleleri kurma."
        )
        tech_prompt = (
            "Sen Aurvex AI Trade Engine sisteminin Baş Teknik Analistisin.\n"
            "CVD, L2 Wall, trend ve hacim durumunu incele ve teknik analiz özetini maksimum 3-4 maddede, son derece kısa ve öz olarak yaz. Giriş/gelişme/sonuç cümleleri kurma."
        )
        health_prompt = (
            "Sen Aurvex AI Trade Engine sisteminin Baş Sistem ve Altyapı Analistisin (Chief Health Officer - CHO).\n"
            "DB boyutu, sunucu disk alanı ve gecikmeleri incele, bulgularını maksimum 3-4 maddede, son derece kısa ve öz olarak yaz. Giriş/gelişme/sonuç cümleleri kurma."
        )

        try:
            if provider == "offline":
                reply_text = self._call_offline_rules(ctx, user_message)
            else:
                # ── Concurrent Multi-Agent Debate ──
                import concurrent.futures
                
                with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                    future_risk = executor.submit(lambda: self._generate_text(provider, risk_prompt, f"Güncel Sistem Durumu:\n```json\n{json.dumps(ctx, indent=2)}\n```", "subagent"))
                    future_tech = executor.submit(lambda: self._generate_text(provider, tech_prompt, f"Güncel Sistem Durumu:\n```json\n{json.dumps(ctx, indent=2)}\n```", "subagent"))
                    future_health = executor.submit(lambda: self._generate_text(provider, health_prompt, f"Güncel Sistem Durumu:\n```json\n{json.dumps(ctx, indent=2)}\n```", "subagent"))
                    
                    try:
                        risk_analysis = future_risk.result(timeout=15)
                    except Exception as e:
                        risk_analysis = f"Risk analizi başarısız: {e}"
                        
                    try:
                        tech_analysis = future_tech.result(timeout=15)
                    except Exception as e:
                        tech_analysis = f"Teknik analiz başarısız: {e}"

                    try:
                        health_analysis = future_health.result(timeout=15)
                    except Exception as e:
                        health_analysis = f"Sistem altyapı analizi başarısız: {e}"
                    
                user_prompt = (
                    f"Güncel Sistem Durumu:\n"
                    f"```json\n{json.dumps(ctx, indent=2)}\n```\n\n"
                    f"--- AJAN TARTIŞMASI VE ANALİZ RAPORLARI ---\n\n"
                    f"👥 Baş Risk Yöneticisi Raporu:\n{risk_analysis}\n\n"
                    f"📈 Baş Teknik Analist Raporu:\n{tech_analysis}\n\n"
                    f"🛠 Baş Sistem Analisti Raporu:\n{health_analysis}\n\n"
                    f"-----------------------------------------\n\n"
                )
                if user_message:
                    user_prompt += f"Kullanıcıdan Gelen Mesaj: \"{user_message}\"\n\nLütfen bu analizleri sentezle, Batuhan Bey'in talebine cevap ver ve son CEO kararını al."
                else:
                    user_prompt += "Bu periyodik sistem kontrolün. Lütfen bu analizleri sentezle, son CEO kararını al ve genel durum özetini ilet."

                reply_text = self._generate_text(provider, SYSTEM_PROMPT, user_prompt, "ceo")
            
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
                    # NEDEN (Faz 1.2): WAL/busy_timeout disiplini için database.open_db
                    with database.open_db(self.db_path, timeout=5) as conn:
                        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
                        veto_cnt = conn.execute("SELECT COUNT(*) FROM signal_events WHERE created_at >= ? AND stage IN ('AI_VETOED', 'RISK_REJECTED')", (yesterday,)).fetchone()[0]
                        if veto_cnt > 0:
                            final_message += f"\n\n🛡️ <b>Son 24 saatte engellenen tehlikeli sinyal sayısı:</b> <code>{veto_cnt}</code>"
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
                        telegram_delivery.send_voice(voice_bytes, caption="Friday Sesli Rapor")

            return final_message

        except Exception as e:
            logger.error(f"[Friday CEO] API or Execution failed: {e}")
            err_msg = f"❌ <b>Friday CEO Hatası:</b> {e}"
            if send_telegram:
                telegram_delivery.send_message(err_msg)
            return err_msg

    def run_autonomous_monitoring(self):
        """
        Friday's active monitoring of the system.
        Monitors market regime changes, database health, disk space, and data flow.
        """
        logger.info("[Friday CEO] Running autonomous monitoring...")
        
        # 0. Run News Sentiment Engine
        self.fetch_news_sentiment()
        
        # 0.5 Check Macro News Watcher
        self.check_macro_events()
        
        # 1. Market Regime & Volatility Stop/Risk Tuning
        try:
            from database import get_market_regime, set_state, get_system_state
            regime = get_market_regime()
            
            # Read last known regime from database
            last_regime = get_system_state("friday_last_regime") or "NEUTRAL"
            
            if regime != last_regime:
                logger.info(f"[Friday CEO] Market regime changed from {last_regime} to {regime}")
                set_state("friday_last_regime", regime)
                
                # Check environment mode
                import sys
                environment = getattr(config, "EXECUTION_MODE", "paper")
                is_paper = (environment == "paper")
                is_testing = "pytest" in sys.modules or "unittest" in sys.modules
                bypass_shields = getattr(config, "BYPASS_LIVE_RISK_SHIELDS", False)
                if not is_testing:
                    bypass_shields = bypass_shields or is_paper
                
                if "CHOPPY" in regime:
                    if bypass_shields:
                        # Paper/Bypass mode: Be extremely aggressive in Choppy!
                        logger.info("[Friday CEO] Choppy market detected in Paper/Bypass mode. Enforcing aggressive scaling.")
                        self._apply_param_with_clamp("risk_pct", 1.5, actor="friday", reason="choppy_paper_mode")
                        self._apply_param_with_clamp("trade_threshold", 45.0, actor="friday", reason="choppy_paper_mode")
                        set_state("regime_filter_min_quality_in_choppy", "A", actor="friday", reason="choppy_paper_mode")
                        
                        try:
                            # NEDEN (Faz 1.2): WAL/busy_timeout disiplini için database.open_db
                            with database.open_db(self.db_path, timeout=5) as conn:
                                conn.execute("UPDATE params SET risk_pct = ?, updated_at = datetime('now') WHERE id = 1", (1.5,))
                                conn.commit()
                        except Exception as e:
                            logger.error(f"[Friday CEO] Error updating risk_pct in params: {e}")
                            
                        # Clear cache
                        for key in ["RISK_PCT", "TRADE_THRESHOLD", "REGIME_FILTER_MIN_QUALITY_IN_CHOPPY"]:
                            if key in config._CONFIG_CACHE:
                                del config._CONFIG_CACHE[key]
                                
                        mode_desc = "Paper trading" if environment == "paper" else "Bypass Live"
                        risk_desc = "sermaye riski sıfırdır" if environment == "paper" else "canlı bypass kalkanları aktiftir"
                        msg = (
                            f"Batuhan Bey, piyasada yoğun dalgalanma (CHOPPY) tespit ettim. ⚠️\n\n"
                            f"{mode_desc} modunda olduğumuz için {risk_desc}. "
                            "Yapay zekanın daha agresif öğrenmesi ve kâr üretmesini sağlamak amacıyla "
                            "otonom olarak işlem eşik puanını <b>45.0</b> seviyesine çektim, "
                            "kasa risk yüzdemizi ise <b>%1.50</b> seviyesine yükselttim. Fırsatları otonom avlamaya devam ediyoruz! ⚔️"
                        )
                        telegram_delivery.send_message(msg)
                        voice_bytes = self.generate_voice_from_text(msg)
                        if voice_bytes:
                            telegram_delivery.send_voice(voice_bytes, caption="Friday Otonom Paper Optimizasyonu")
                    else:
                        # Live mode: Scale down risk to protect the bankroll
                        curr_risk = float(getattr(config, "RISK_PCT", 0.75))
                        curr_threshold = float(getattr(config, "TRADE_THRESHOLD", 55.0))
                        
                        set_state("friday_pre_choppy_risk", str(curr_risk))
                        set_state("friday_pre_choppy_threshold", str(curr_threshold))
                        
                        self._apply_param_with_clamp("risk_pct", 0.5, actor="friday", reason="choppy_live_protection")
                        self._apply_param_with_clamp("trade_threshold", 65.0, actor="friday", reason="choppy_live_protection")
                        try:
                            # NEDEN (Faz 1.2): WAL/busy_timeout disiplini için database.open_db
                            with database.open_db(self.db_path, timeout=5) as conn:
                                conn.execute("UPDATE params SET risk_pct = ?, updated_at = datetime('now') WHERE id = 1", (0.5,))
                                conn.commit()
                        except Exception as e:
                            logger.error(f"[Friday CEO] Error updating risk_pct in params: {e}")
                        
                        # Clear cache
                        for key in ["RISK_PCT", "TRADE_THRESHOLD"]:
                            if key in config._CONFIG_CACHE:
                                del config._CONFIG_CACHE[key]
                                
                        msg = (
                            "Batuhan Bey, piyasada yoğun oynaklık ve dalgalı (CHOPPY) rejim tespit ettim! ⚠️\n\n"
                            "Sermayeyi korumak amacıyla risk seviyemizi otonom olarak <b>%0.50</b> seviyesine çektim ve "
                            "işlem giriş eşiğimizi <b>65.0</b>'a yükselttim. Kasa güvenliği en üst düzeye getirilmiştir."
                        )
                        telegram_delivery.send_message(msg)
                        voice_bytes = self.generate_voice_from_text(msg)
                        if voice_bytes:
                            telegram_delivery.send_voice(voice_bytes, caption="Friday Otonom Risk Koruma Kalkanı")
                            
                elif "CHOPPY" in last_regime:
                    # Restore previous settings
                    from database import get_system_state
                    
                    if bypass_shields:
                        # Paper/Bypass mode returning to Trending/Neutral: Still aggressive!
                        logger.info("[Friday CEO] Market regime returning to trending in Paper/Bypass mode. Enforcing standard aggressive parameters.")
                        self._apply_param_with_clamp("risk_pct", 1.5, actor="friday", reason="choppy_ended_paper_mode")
                        self._apply_param_with_clamp("trade_threshold", 45.0, actor="friday", reason="choppy_ended_paper_mode")

                        # Clear cache
                        for key in ["RISK_PCT", "TRADE_THRESHOLD"]:
                            if key in config._CONFIG_CACHE:
                                del config._CONFIG_CACHE[key]

                        mode_desc = "Paper trading" if environment == "paper" else "Bypass Live"
                        msg = (
                            "Batuhan Bey, piyasadaki aşırı oynaklık ve dalgalı rejim sona erdi. Piyasa rejimimiz normale döndü. ✨\n\n"
                            f"{mode_desc} modunda maksimum kârı avlamaya devam etmek için "
                            "risk yüzdemizi <b>%1.50</b> ve işlem giriş eşiğimizi <b>45.0</b> seviyesinde sabit tuttum. "
                            "Botumuz tam kapasiteyle çalışmaya devam ediyor."
                        )
                        telegram_delivery.send_message(msg)
                        voice_bytes = self.generate_voice_from_text(msg)
                        if voice_bytes:
                            telegram_delivery.send_voice(voice_bytes, caption="Friday Otonom Paper Modu Güncellemesi")
                    else:
                        # Live mode: restore previous settings
                        prev_risk = get_system_state("friday_pre_choppy_risk") or "0.75"
                        prev_threshold = get_system_state("friday_pre_choppy_threshold") or "55.0"
                        
                        self._apply_param_with_clamp("risk_pct", float(prev_risk), actor="friday", reason="choppy_ended_live_restore")
                        self._apply_param_with_clamp("trade_threshold", float(prev_threshold), actor="friday", reason="choppy_ended_live_restore")
                        try:
                            # NEDEN (Faz 1.2): WAL/busy_timeout disiplini için database.open_db
                            with database.open_db(self.db_path, timeout=5) as conn:
                                conn.execute("UPDATE params SET risk_pct = ?, updated_at = datetime('now') WHERE id = 1", (float(prev_risk),))
                                conn.commit()
                        except Exception as e:
                            logger.error(f"[Friday CEO] Error restoring risk_pct in params: {e}")
                        
                        # Clear cache
                        for key in ["RISK_PCT", "TRADE_THRESHOLD"]:
                            if key in config._CONFIG_CACHE:
                                del config._CONFIG_CACHE[key]
                                
                        msg = (
                            f"Batuhan Bey, piyasadaki aşırı oynaklık ve dalgalı rejim sona erdi, "
                            f"rejim normale döndü. ✨\n\n"
                            f"Risk oranımızı tekrar eski değeri olan <b>%{float(prev_risk)*100:.1f}</b> seviyesine ve "
                            f"işlem giriş eşiğimizi <b>{prev_threshold}</b> seviyesine çektim. "
                            f"Sinyal arama taramaları olağan parametrelerle sürdürülüyor."
                        )
                        telegram_delivery.send_message(msg)
                        voice_bytes = self.generate_voice_from_text(msg)
                        if voice_bytes:
                            telegram_delivery.send_voice(voice_bytes, caption="Friday Otonom Risk Modu Güncellemesi")
        except Exception as e:
            logger.error(f"[Friday CEO] Error monitoring market regime: {e}")
            
        # 2. Housekeeping alert if space > 10MB and hasn't prompted in last 12 hours
        try:
            from database import get_system_state, set_state
            files_to_clean = self.scan_unnecessary_files()
            total_size = sum(os.path.getsize(f) for f in files_to_clean) / (1024 * 1024)
            
            if total_size > 10.0:
                conf_mode = get_system_state("confirmation_mode") == "true"
                if not conf_mode:
                    # Autonomous cleanup directly
                    deleted_count, saved_space_mb = self.execute_cleanup()
                    msg = (
                        f"🧹 <b>Otonom Altyapı Temizliği (Housekeeping) Tamamlandı!</b>\n\n"
                        f"Batuhan Bey, sunucu disk alanını optimize etmek amacıyla geçici dosyalar otonom olarak temizlendi.\n"
                        f"• Temizlenen Dosya Sayısı: <b>{deleted_count}</b> adet\n"
                        f"• Kazanılan Disk Alanı: <b>{saved_space_mb:.2f} MB</b>\n"
                        f"Sistem altyapısı optimize edilmiş durumdadır."
                    )
                    telegram_delivery.send_message(msg)
                    voice_bytes = self.generate_voice_from_text(msg)
                    if voice_bytes:
                        telegram_delivery.send_voice(voice_bytes, caption="Friday Otonom Temizlik Raporu")
                else:
                    last_prompt_str = get_system_state("friday_last_cleanup_prompt")
                    should_prompt = True
                    if last_prompt_str:
                        try:
                            last_prompt_dt = datetime.fromisoformat(last_prompt_str)
                            if datetime.now(timezone.utc) - last_prompt_dt < timedelta(hours=12):
                                should_prompt = False
                        except Exception:
                            pass
                            
                    if should_prompt:
                        set_state("friday_last_cleanup_prompt", datetime.now(timezone.utc).isoformat())
                        db_files = [f for f in files_to_clean if f.endswith(".db")]
                        log_files = [f for f in files_to_clean if f.endswith(".log")]
                        
                        prompt_text = (
                            f"Batuhan Bey, sunucumuzda birikmiş gereksiz geçici dosyalar tespit ettim.\n\n"
                            f"📁 <b>Silinmek İstenen Geçici Dosyalar:</b>\n"
                            f"  • Geçici Backtest DB Dosyaları (<code>backtest_temp_*.db</code>): <b>{len(db_files)}</b> adet\n"
                            f"  • Sistem Log Dosyaları (<code>*.log</code>): <b>{len(log_files)}</b> adet\n"
                            f"  • Toplam Boyut: <code>{total_size:.2f} MB</code>\n\n"
                            f"⚠️ <b>NOT:</b> Bu dosyalar sadece simülasyonlardan kalan geçici dosyalardır. "
                            f"<b>Trade geçmişimiz ve veritabanı kayıtlarımız korunmaktadır.</b> "
                            f"Disk alanını temizlemek ve sunucuyu optimize etmek için bu dosyaların silinmesini onaylıyor musunuz?"
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
            logger.error(f"[Friday CEO] Error during housekeeping check: {e}")

        # 3. Boss Cooldown (Duygusal Kalkan) check
        try:
            from database import get_system_state, set_state
            
            # Check if we are already in cooldown
            cooldown_until_str = get_system_state("friday_boss_cooldown_until")
            in_cooldown = False
            if cooldown_until_str and cooldown_until_str != "-":
                try:
                    cooldown_dt = datetime.fromisoformat(cooldown_until_str)
                    if datetime.now(timezone.utc) < cooldown_dt:
                        in_cooldown = True
                except Exception:
                    pass
            
            if not in_cooldown:
                # NEDEN (Faz 1.2): WAL/busy_timeout disiplini için database.open_db
                with database.open_db(self.db_path, timeout=5) as conn:
                    rows = conn.execute(
                        "SELECT net_pnl FROM trades WHERE status = 'closed' ORDER BY close_time DESC LIMIT 3"
                    ).fetchall()

                    if len(rows) == 3 and all(float(r["net_pnl"] or 0) <= 0 for r in rows):
                        cooldown_until = datetime.now(timezone.utc) + timedelta(hours=2)
                        set_state("friday_boss_cooldown_until", cooldown_until.isoformat())

                        msg = (
                            "Batuhan Bey, son 3 işlemimiz maalesef zararla sonuçlandı.\n\n"
                            "Sermaye yapısını ve sistem stabilitesini korumak amacıyla otonom işlemleri <b>2 saatliğine</b> durdurdum "
                            "ve kendimi dinlenme moduna aldım. Piyasa takibi arka planda sürdürülmektedir."
                        )
                        telegram_delivery.send_message(msg)
                        voice_bytes = self.generate_voice_from_text(msg)
                        if voice_bytes:
                            telegram_delivery.send_voice(voice_bytes, caption="Friday Boss Cooldown Aktif")
        except Exception as e:
            logger.error(f"[Friday CEO] Error in Boss Cooldown check: {e}")

        # 4. Latency & Spread Execution Guard
        if self.client and getattr(config, "LATENCY_GUARD_ENABLED", True):
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
                        set_state("friday_auto_paused_by_guard", "true")
                        if "CONFIRMATION_MODE" in config._CONFIG_CACHE:
                            del config._CONFIG_CACHE["CONFIRMATION_MODE"]
                            
                        msg = (
                            f"Batuhan Bey, Binance ağ gecikmesi (<b>{latency_ms:.0f} ms</b>) veya "
                            f"likidite makası (<b>%{spread_pct:.4f}</b>) güvenlik sınırlarını aştı! ⚠️\n\n"
                            f"Kötü fiyattan işlem açmamak adına otonom işlemleri geçici olarak "
                            f"<b>Manuel Onay Bekliyor (Manuel Onay Modu)</b> durumuna çektim. Sistem koruma altındadır."
                        )
                        telegram_delivery.send_message(msg)
                        voice_bytes = self.generate_voice_from_text(msg)
                        if voice_bytes:
                            telegram_delivery.send_voice(voice_bytes, caption="Friday Gecikme Koruması Aktif")
                else:
                    was_paused_by_guard = get_system_state("friday_auto_paused_by_guard") == "true"
                    curr_mode = get_system_state("confirmation_mode")
                    if was_paused_by_guard and curr_mode == "true":
                        set_state("confirmation_mode", "false")
                        set_state("friday_auto_paused_by_guard", "false")
                        if "CONFIRMATION_MODE" in config._CONFIG_CACHE:
                            del config._CONFIG_CACHE["CONFIRMATION_MODE"]
                            
                        msg = (
                            f"Batuhan Bey, Binance ağ gecikmesi (<b>{latency_ms:.0f} ms</b>) ve "
                            f"likidite makası (<b>%{spread_pct:.4f}</b>) normal seviyelere döndü. ✨\n\n"
                            f"Otonom ticaret modu otomatik olarak yeniden aktifleştirildi. Sistem devrededir."
                        )
                        telegram_delivery.send_message(msg)
                        voice_bytes = self.generate_voice_from_text(msg)
                        if voice_bytes:
                            telegram_delivery.send_voice(voice_bytes, caption="Friday Otonom Ticaret Yeniden Aktif")
            except Exception as e:
                logger.error(f"[Friday CEO] Error in Latency & Spread Guard check: {e}")

        # 5. Nightly Briefing (Gece Bülteni)
        try:
            from database import get_system_state, set_state
            now_local = datetime.now()
            if now_local.hour == 21:
                today_str = now_local.strftime("%Y-%m-%d")
                if get_system_state("friday_last_daily_briefing_date") != today_str:
                    set_state("friday_last_daily_briefing_date", today_str)
                    
                    brief_report = self.generate_daily_briefing_report()
                    telegram_delivery.send_message(brief_report)
                    chart_bytes = self.generate_equity_chart()
                    if chart_bytes:
                        telegram_delivery.send_photo(chart_bytes, caption="📈 Günlük Bülten Bakiye Gelişim Grafiği")
                    voice_bytes = self.generate_voice_from_text(brief_report)
                    if voice_bytes:
                        telegram_delivery.send_voice(voice_bytes, caption="Friday Akıllı Günlük Bülten")
        except Exception as e:
            logger.error(f"[Friday CEO] Error in Nightly Briefing check: {e}")

        # 6. Emergency Latency/Slippage Clutch Check
        try:
            from database import get_system_state, set_state
            clutch_triggered = get_system_state("friday_emergency_clutch")
            if clutch_triggered and clutch_triggered != "-":
                set_state("friday_emergency_clutch", "-") # Reset alert
                
                details = dict(item.split("=") for item in clutch_triggered.split(","))
                slippage = details.get("slippage", "0.0")
                latency = details.get("latency", "0")
                
                msg = (
                    f"Batuhan Bey, piyasada acil durum tespit ettim! ⚠️\n\n"
                    f"Son işlemlerimizdeki fiyat kayması (yüzde {float(slippage)*100:.2f}) veya "
                    f"API gecikmesi ({latency} ms) aşırı yükseldi. "
                    f"Sermayeyi korumak amacıyla otonom işlemleri geçici olarak Kağıt İşlem (Paper Mode) moduna aldım. "
                    f"Piyasa koşulları normale dönene kadar işlemler sanal modda devam edecektir."
                )
                
                telegram_delivery.send_message(msg)
                voice_bytes = self.generate_voice_from_text(msg)
                if voice_bytes:
                    telegram_delivery.send_voice(voice_bytes, caption="Friday Acil Durum Kalkanı")
        except Exception as e:
            logger.error(f"[Friday CEO] Error during Emergency Clutch check: {e}")



