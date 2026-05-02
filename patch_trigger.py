"""trigger_engine.py'ye S sınıfı ekle"""
content = open('/home/ubuntu/trade-engine/core/trigger_engine.py').read()

old2 = '''        # A+ Kalite Yükseltmesi
        if quality == "A" and score >= 9.0:
            quality = "A+"

        # ── Kalite Filtresi'''

new2 = '''        # ── Composite S Sınıfı Skoru ─────────────────────────────────────────
        # S sınıfı: ADX, RSI, EMA, hacim, BTC korelasyonu ve coin geçmiş WR
        # birleştiren çok boyutlu skor — en güvenilir setup
        s_score = 0
        # ADX gücü (0-3 puan)
        if adx_val >= 40:    s_score += 3
        elif adx_val >= 35:  s_score += 2
        elif adx_val >= 30:  s_score += 1
        # RSI merkeze yakınlık (0-2 puan) — 48-62 ideal bölge
        rsi_dist = abs(rsi5 - 55)
        if rsi_dist <= 7:    s_score += 2
        elif rsi_dist <= 15: s_score += 1
        # Hacim spike (0-2 puan)
        if rv >= 2.5:        s_score += 2
        elif rv >= 2.0:      s_score += 1
        # MACD histogram yönü (0-1 puan)
        if (direction == "LONG" and hist > 0) or (direction == "SHORT" and hist < 0):
            s_score += 1
        # Momentum güçlü (0-2 puan)
        mom_abs = abs(mom3c)
        if mom_abs >= 2.5:   s_score += 2
        elif mom_abs >= 1.8: s_score += 1
        # Coin geçmiş win rate (0-2 puan)
        try:
            from coin_library import get_coin_params
            cp = get_coin_params(symbol)
            hist_wr = cp.get("win_rate", 0)
            if hist_wr >= 0.60:   s_score += 2
            elif hist_wr >= 0.50: s_score += 1
        except Exception:
            pass
        # BTC trend uyumu (0-1 puan)
        if (direction == "LONG" and btc_trend == "BULLISH") or \
           (direction == "SHORT" and btc_trend == "BEARISH"):
            s_score += 1
        # İyi saat bonusu (0-1 puan)
        if current_hour in GOOD_HOURS_UTC:
            s_score += 1
        # S sınıfı eşiği: 14 üzerinden en az 10 puan
        if s_score >= 10:
            quality = "S"
        # A+ Kalite Yükseltmesi
        elif quality == "A" and score >= 9.0:
            quality = "A+"

        # ── Kalite Filtresi'''

if old2 in content:
    content = content.replace(old2, new2)
    print('OK: S sinifi blogu eklendi')
else:
    print('HATA: Blok bulunamadi')
    import sys; sys.exit(1)

open('/home/ubuntu/trade-engine/core/trigger_engine.py', 'w').write(content)
print('trigger_engine.py kaydedildi.')
