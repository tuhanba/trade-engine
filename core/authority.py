"""
core/authority.py — Mod-duyarlı yetki kapısı (Fix I)
====================================================
Kural (CLAUDE.md / Friday rolü):
  - PAPER: Friday TAM YETKİLİ. Hiçbir parametre değişikliği insan onayı
    gerektirmez (paper sandbox; gerçek para yok) ve backtest-kanıt kapısı (param_gate)
    de uygulanmaz — Friday özerk dener ve öğrenir.
  - LIVE: Yalnız KRİTİK parametreler (risk/kaldıraç/strateji/sermaye/mod) insan
    (Telegram) onayı bekler — PENDING; onaylanana dek eski değer korunur.
    Kritik-olmayanlar kanıt-temelli param_gate'ten geçip otonom uygulanır.

Tek nokta: requires_approval(key, mode). friday_ceo._apply_param_with_clamp buradan
geçer; böylece "paper=tam yetki / live=kritik-onay" kuralı TEK yerde uygulanır.
"""
from __future__ import annotations

# Risk / kaldıraç / strateji / sermaye / işlem-modu etkileyen kritik anahtarlar.
CRITICAL_KEYS = {
    "risk_pct", "max_leverage", "leverage", "execution_mode",
    "trade_threshold", "max_open_trades",
    # strateji / sermaye etkileyen anahtarlar:
    "strategy_mode", "capital_allocation", "compounding", "auto_compounding",
}


def is_critical(key: str) -> bool:
    """Anahtar kritik mi (risk/kaldıraç/strateji/sermaye/mod)?"""
    return str(key).strip().lower() in CRITICAL_KEYS


def requires_approval(key: str, mode: str) -> bool:
    """Bu (key, mode) için insan (Telegram) onayı gerekli mi?

    PAPER → her zaman False (Friday tam yetki).
    LIVE  → yalnız kritik anahtarlarda True.
    """
    if str(mode).strip().lower() == "paper":
        return False
    return is_critical(key)
