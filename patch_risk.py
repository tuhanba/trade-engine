"""risk_engine.py'ye S sınıfı dinamik risk ekle"""
content = open('/home/ubuntu/trade-engine/core/risk_engine.py').read()

# Eski risk bloğu
old = '''        # ── Risk Ayarlaması ───────────────────────────────────────────────────
        risk_pct = base_risk
        if quality == "A+":   risk_pct *= 1.2   # A+ için %20 artır
        elif quality == "A":  risk_pct *= 1.0
        elif quality == "B":  risk_pct *= 0.5
        elif quality in ["C", "D"]: risk_pct = 0'''

# Yeni dinamik risk bloğu — S/A+/A/B/C hiyerarşisi
new = '''        # ── Dinamik Risk Yönetimi — Kalite Bazlı ────────────────────────────
        # S  : %2.0 risk — Composite skor ≥10, en güvenilir setup
        # A+ : %1.5 risk — Yüksek kalite, güçlü trend
        # A  : %1.0 risk — İyi kalite, standart risk
        # B  : %0.5 risk — Orta kalite, düşük risk
        # C/D: %0.0 risk — Trade yok
        risk_pct = base_risk
        if quality == "S":
            risk_pct = base_risk * 2.0    # S: 2x risk — en güvenilir setup
        elif quality == "A+":
            risk_pct = base_risk * 1.5    # A+: 1.5x risk
        elif quality == "A":
            risk_pct = base_risk * 1.0    # A: standart risk
        elif quality == "B":
            risk_pct = base_risk * 0.5    # B: yarı risk
        elif quality in ["C", "D"]:
            risk_pct = 0                  # C/D: trade yok
        # Risk üst sınırı: bakiyenin %3'ünü geçemez
        risk_pct = min(risk_pct, 3.0)'''

if old in content:
    content = content.replace(old, new)
    print('OK: Dinamik risk blogu guncellendi')
else:
    print('HATA: Blok bulunamadi')
    import sys; sys.exit(1)

open('/home/ubuntu/trade-engine/core/risk_engine.py', 'w').write(content)
print('risk_engine.py kaydedildi.')
