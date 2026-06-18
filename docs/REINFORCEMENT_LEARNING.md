# 🧠 Aurvex AI Trading Engine — Takviyeli Öğrenme (RL) Piyasa Rejimi Switcher

Bu doküman, sistemin piyasa rejimlerini otonom kümeleyen Gaussian Mixture Model (GMM) yapısını ve Q-Learning meta-tuner katmanını detaylandırır.

---

## 📊 Gaussian Mixture Model (GMM) ile Piyasa Rejimi Sınıflandırma

Piyasalar sürekli aynı karakterde çalışmaz; trend olan piyasalar ile yatay (choppy) giden piyasalarda filtrelerin hassasiyetleri farklı olmalıdır. GMM rejim sınıflandırıcısı (`GMMRegimeClassifier`) bu dinamikleri otonom belirler:

- **Öznitelikler (Features)**: Son 100 saatlik mum verilerinden türetilen ATR volatilite oranı ve standart sapma trend gücü.
- **Kümeleme (Clustering)**: scikit-learn kütüphanesindeki `GaussianMixture` kullanılarak veri setinde 4 temel rejim kümesi oluşturulur:
  1. `TRENDING_HIGH_VOL` (Yüksek volatilite, Güçlü trend)
  2. `TRENDING_LOW_VOL` (Düşük volatilite, İstikrarlı trend)
  3. `CHOPPY_HIGH_VOL` (Yüksek volatilite, Yatay/Testere piyasa)
  4. `CHOPPY_LOW_VOL` (Düşük volatilite, Yatay/Sakin piyasa)
- GMM modeli periyodik olarak canlı piyasa verileriyle güncellenir.

---

## 🤖 Q-Learning Meta-Learner (Takviyeli Öğrenme)

Her piyasa rejiminde en ideal parametre kümesini bulmak için Q-Learning algoritması arka planda çalışır:

- **Durumlar (States)**: GMM tarafından sınıflandırılan 4 piyasa rejimi.
- **Eylemler (Actions)**:
  - `Action 0 (Neutral)`: Varsayılan filtre parametreleri.
  - `Action 1 (Defensive)`: Filtreler daha sıkı (RSI limitleri daraltılır, CVD filtresi sıkılaştırılır, trailing stop daha yakın takip eder).
  - `Action 2 (Aggressive)`: Filtreler daha esnek (Daha gevşek RSI ve CVD limitleri, daha uzak trailing stop ile hedeflere alan bırakılır).
- **Ödül (Reward)**: Kapatılan işlemlerden elde edilen net PnL (R-multiple cinsinden). Zararla kapanan işlemler negatif, karla kapananlar pozitif ödül üretir.

---

## 🛠️ Parametre Kaydırma (Parameter Shifting)

Q-Learning ajanı, aldığı aksiyonlara göre indikatör limitlerini dinamik olarak kaydırır (Shift):

| Aksiyon (Action) | RSI Limit Kayması | CVD Filtre Kayması | Trailing ATR Çarpan Kayması |
|---|---|---|---|
| **0: Neutral** | 0.0 | 0.0 | 0.0 |
| **1: Defensive** | +4.0 | +0.05 | +0.2 |
| **2: Aggressive** | -4.0 | -0.05 | -0.2 |

Bu kayma değerleri `config.py` içerisindeki parametre okuma süreçlerine `config.__getattr__` aracılığıyla dinamik olarak yansıtılarak, kod tabanını yeniden başlatmaya gerek kalmadan tüm sisteme anında uygulanır.
Model dosyaları (`rl_meta_learner.pkl`) `config.BASE_DIR` altında kalıcı olarak saklanır ve Docker restart'larında kaybolmaz.
