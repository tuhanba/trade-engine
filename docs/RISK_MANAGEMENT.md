# 🛡️ Aurvex AI Trading Engine — Risk Yönetimi ve Pozisyon Bütçeleme

Bu doküman, Aurvex platformunun sermaye koruma kalkanlarını, Kelly pozisyon bütçelemesini, Portföy VaR limitlerini ve drawdown devre kesicilerini detaylandırır.

---

## 📐 Kelly Kriteri ile Dinamik Pozisyon Büyüklüğü

Sistem, her işleme sabit bir yüzdeyle girmek yerine niceliksel olasılık temelli **Kelly Kriteri**'ni kullanır:

- **Formül**: 
  $$f^* = \frac{p \cdot (b + 1) - 1}{b}$$
  - $p$: Coinin tarihsel kazanma oranı (Win Rate).
  - $b$: Risk-Reward (Kâr-Zarar) oranı (Payoff Ratio).
- **Multi-Asset Kelly Matrix (Faz K)**: Birden fazla coin açıkken işlemlerin birbiriyle olan korelasyonu hesaba katılarak Ridge Regularization (Ledoit-Wolf) uygulanmış bir Kelly matrisi çözülür:
  $$f^* = C^{-1} \cdot m$$
  - $C$: Korelasyon matrisi.
  - $m$: Beklenen getiri vektörü.
- **Half-Kelly Katsayısı**: Güvenlik amacıyla hesaplanan Kelly büyüklüğünün yarısı (Half-Kelly, katsayı 0.5) pozisyon açma büyüklüğü olarak baz alınır. Minimum %0.5, maksimum %3 limitleri uygulanır.

---

## 📊 Portföy Value-at-Risk (%99 VaR)

Sistem, tüm açık pozisyonlar ve yeni eklenmek istenen pozisyon adayının birleşiminden oluşan toplam portföyün **Parametrik Value-at-Risk (VaR %99)** değerini hesaplar:

- Fiyat getirilerinin standart sapmaları ve kovaryans matrisi kullanılarak portföyün varyansı ve oynaklığı çıkarılır:
  $$\text{Portfolio Variance} = W^T \cdot \Sigma \cdot W$$
  - $W$: Pozisyon ağırlık vektörü.
  - $\Sigma$: Getirilerin kovaryans matrisi.
- **Limit Ölçeklendirme**: Eğer toplam portföy VaR değeri `config.PORTFOLIO_VAR_LIMIT` (örn. %5 limit) sınırını aşarsa, yeni pozisyonun büyüklüğü risk limitine sığacak şekilde otomatik olarak kısılır (Scale-down) veya sıfırlanır.
- **NaN Güvenliği**: Fiyatların sabit kalması durumunda oluşabilecek `NaN` varyans çıktıları otonom yakalanır ve risk motorunun kilitlenmesi önlenir.

---

## 🔗 Pearson Korelasyon Kalkanı

Aynı anda birbirine benzer hareket eden coinlere (örn: LDO ve SSV, OP ve ARB) girilerek riskin tek bir yöne yığılmasını önlemek için **Pearson Korelasyon Kalkanı** devrededir:

- Açık işlemler ile yeni sinyal arasındaki fiyat getirilerinin 50 saatlik korelasyon katsayıları hesaplanır.
- Katsayısı **> 0.75** olan ve aynı yönde (Long-Long) açılmak istenen yeni pozisyonlar risk motoru tarafından doğrudan veto edilir.

---

## 🛑 Drawdown Devre Kesicileri (Circuit Breakers)

Kasanın erimesini (Drawdown) engellemek amacıyla iki aşamalı bir devre kesici sistemi kurulmuştur:

1. **Daily Drawdown Guard**: Günlük kümülatif zarar katsayısı limitleri (örn. günlük %5 kayıp) aşıldığında sistem yeni işlem açmayı otonom olarak durdurur.
2. **Equity Curve Filter (EMA)**: Toplam kasa bakiyesi gelişim eğrisi, kendi 20 günlük üstel ortalamasının (EMA) altına indiğinde, sistem otomatik olarak "Defensive" moda geçer ve tüm işlem büyüklüklerini (leverage/size) yarı yarıya (%50) düşürerek drawdown dönemini en az hasarla atlatmayı hedefler.
