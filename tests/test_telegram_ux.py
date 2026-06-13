"""
tests/test_telegram_ux.py — Faz 5.2 komut UX testleri.

Kategorili /help menüsü, tehlikeli komut onayları (/live, /close all,
/set risk_pct) ve /funnel komutu.
"""
import pytest


class _Capture:
    """send_fn yerine geçer — gönderilen metin + reply_markup'ı yakalar."""
    def __init__(self):
        self.calls = []

    def __call__(self, text, reply_markup=None, **kw):
        self.calls.append({"text": text, "markup": reply_markup})
        return True

    @property
    def last(self):
        return self.calls[-1] if self.calls else None


def _make_manager(cap):
    from telegram_manager import TelegramManager
    return TelegramManager(send_fn=cap, friday_ceo=None)


def _btn_callbacks(markup):
    out = []
    for row in (markup or {}).get("inline_keyboard", []):
        for b in row:
            out.append(b.get("callback_data"))
    return out


# ── Kategorili /help menüsü ───────────────────────────────────────────────────

def test_help_categorized_menu(test_db):
    cap = _Capture()
    mgr = _make_manager(cap)
    markup = mgr._get_help_markup()
    cbs = _btn_callbacks(markup)
    # 5 kategori (plan: Durum | İşlemler | Ghost | Friday | Ayarlar)
    assert set(cbs) == {"cmd:cat_status", "cmd:cat_trades", "cmd:cat_ghost",
                        "cmd:cat_friday", "cmd:cat_settings"}


def test_category_submenu_has_back(test_db):
    cap = _Capture()
    mgr = _make_manager(cap)
    for cat in ("status", "trades", "ghost", "friday", "settings"):
        title, markup = mgr._get_category_markup(cat)
        cbs = _btn_callbacks(markup)
        assert "cmd:help_main" in cbs, f"{cat}: geri butonu yok"
    # Durum kategorisi yeni analitik komutları içermeli
    _, m = mgr._get_category_markup("status")
    cbs = _btn_callbacks(m)
    assert "cmd:expectancy" in cbs and "cmd:readiness" in cbs and "cmd:funnel" in cbs


# ── Tehlikeli komut onayları ──────────────────────────────────────────────────

def test_close_all_requires_confirmation(test_db):
    cap = _Capture()
    mgr = _make_manager(cap)
    # Bir açık pozisyon ekle
    with test_db.get_conn() as conn:
        conn.execute(
            "INSERT INTO trades (symbol,direction,status,entry,sl,environment) "
            "VALUES ('BTCUSDT','LONG','open',60000,59000,'paper')"
        )
    mgr._cmd_close(["all"])
    last = cap.last
    cbs = _btn_callbacks(last["markup"])
    # Onay/Vazgeç butonları çıkmalı; doğrudan kapatma YAPILMAMALI
    assert "cmd:do_close_all" in cbs
    assert "cmd:cancel_action" in cbs
    assert "TAMAMI kapatılacak" in last["text"]
    # Pozisyon hâlâ açık (onay beklemede)
    with test_db.get_conn() as conn:
        n = conn.execute("SELECT COUNT(*) FROM trades WHERE status='open'").fetchone()[0]
    assert n == 1


def test_close_all_empty_no_confirm(test_db):
    cap = _Capture()
    mgr = _make_manager(cap)
    mgr._cmd_close(["all"])
    assert "açık işlem yok" in cap.last["text"].lower()
    assert cap.last["markup"] is None


def test_set_risk_pct_requires_confirmation(test_db):
    cap = _Capture()
    mgr = _make_manager(cap)
    mgr._cmd_set(["risk_pct", "0.5"])
    last = cap.last
    cbs = _btn_callbacks(last["markup"])
    assert any(c.startswith("cmd:do_set:risk_pct:0.5") for c in cbs)
    assert "cmd:cancel_action" in cbs
    # Değer henüz UYGULANMAMALI
    assert test_db.get_state("risk_pct") is None


def test_set_non_dangerous_applies_directly(test_db):
    cap = _Capture()
    mgr = _make_manager(cap)
    mgr._cmd_set(["trade_threshold", "52"])
    # Onay butonu YOK, doğrudan uygulanır
    assert cap.last["markup"] is None
    assert test_db.get_state("trade_threshold") == "52.0"


def test_do_set_applies_value(test_db):
    cap = _Capture()
    mgr = _make_manager(cap)
    mgr._do_set("risk_pct", "0.5")
    assert test_db.get_state("risk_pct") == "0.5"
    assert "Başarılı" in cap.last["text"]


# ── /funnel komutu ────────────────────────────────────────────────────────────

def test_funnel_command(test_db):
    cap = _Capture()
    mgr = _make_manager(cap)
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    with test_db.get_conn() as conn:
        for r in ["weak_trend", "weak_trend", "low_vol"]:
            conn.execute(
                "INSERT INTO signal_events (signal_id,stage,symbol,reject_reason,created_at) "
                "VALUES ('s','REJECTED','X',?,?)",
                (r, now.isoformat()),
            )
    mgr._cmd_funnel()
    text = cap.last["text"]
    assert "Sinyal Hunisi" in text
    assert "weak_trend" in text and "×2" in text
