# 🧠 Aurvex AI Trading Engine — Friday AI CEO Operatörü

Bu doküman, sistemin en üst karar mercii olan otonom AI CEO operatörü **Friday**'i, Debate (konsensüs) mekanizmasını ve sesli raporlama entegrasyonunu açıklar.

---

## 👑 Friday AI CEO Rolü

Friday, Claude API'lerinden yararlanan, Aurvex ticaret motorunun otonom yöneticisidir. Sistemin parametrelerini canlı piyasa koşullarına göre analiz eder, optimize eder ve gerektiğinde durdurma/başlatma (Pause/Resume) yetkisine sahiptir.

### Temel Sorumlulukları
1. **Dinamik Parametre Optimizasyonu**: Piyasa oynaklığı yükseldiğinde filtreleri korumacı (Defensive) hale getirir, sakin piyasalarda ise agresiflik düzeyini artırır.
2. **Sağlık ve Disk Yönetimi**: Sunucu disk doluluk oranını izler, gereksiz log ve backtest dosyalarını otonom temizler.
3. **Karar Verme (Debate)**: Gelen sinyal adaylarını Baş Risk Yöneticisi (CRO) ve Baş Teknik Analist (CTA) rollerini canlandıran iki bağımsız model arasında tartıştırır.

---

## 🗣️ Ajanlar Arası Debate (Konsensüs) Mekanizması

Yeni bir sinyal oluştuğunda, Friday doğrudan işleme girmek yerine iki farklı yapay zeka ajanının katıldığı bir panel düzenler:

```
                  [Sinyal Adayı]
                        │
         ┌──────────────┴──────────────┐
         ▼                             ▼
   Baş Teknik Analist            Baş Risk Yöneticisi
        (CTA)                         (CRO)
   - İndikatör analizi           - Portföy marjı
   - Trend gücü                  - Drawdown durumu
   - Hacim doğrulaması           - Korelasyon analizi
         │                             │
         └──────────────┬──────────────┘
                        ▼
            [Konsensüs Karar Mekanizması]
             - ALLOW (İzin ver)
             - WATCH (İzleme listesi)
             - VETO (Reddet)
```

- **CTA (Chief Technical Analyst)**: Formasyonun gücüne, CVD trendine ve indikatörlerin doygunluğuna odaklanır. Amacı karlı sinyalleri yakalamaktır.
- **CRO (Chief Risk Officer)**: Kasa drawdown oranına, coin cooldown sürelerine ve toplam portföy marj limitlerine odaklanır. Amacı kasayı korumaktır.
- Her iki ajanın oylama ve skor analizleri sonucunda nihai bir karar verilir. Kararlar WebSocket üzerinden canlı dashboard ekranına yansıtılır.

---

## 🔊 edge-tts Doğal Ses Sentezi Entegrasyonu

Friday, aldığı önemli kararları, günlük PnL raporlarını ve veto gerekçelerini boss'una (operatöre) iletmek üzere doğal sesli mesajlar sentezler:

- **Ses Modeli**: Microsoft Edge TTS motorunun `tr-TR-EmelNeural` ses modeli kullanılır.
- **Karakter ve Akış**: Doğal, akıcı, kurumsal ancak hafif cilveli ve akıcı bir Türkçe tonu tercih edilmiştir.
- **Gönderim Kanalı**: Sentezlenen ses dosyaları (`.mp3` formatında), Telegram Queue üzerinden asenkron şekilde Telegram grubuna/özel sohbetine sesli not olarak gönderilir.

### Örnek Çalışma Akışı
1. Bir sinyal veto edildiğinde, Friday veto gerekçesini içeren kısa bir metin yazar.
2. `core/friday_ceo.py` içindeki `generate_voice_report()` metodu bu metni alır, `edge-tts` ile seslendirir.
3. Oluşan ses dosyası `telegram_delivery.py` modülündeki `send_voice` metoduyla Telegram kuyruğuna push edilir.
