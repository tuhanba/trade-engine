"""
scripts/monitor_paper_run.py — Paper Run İzleme Aracı
======================================================
Paper run sırasında çalıştırılır. Sistem sağlığını, açık trade'leri,
bakiye hareketlerini ve anormallikleri raporlar.

Kullanım:
  python3 scripts/monitor_paper_run.py           # Tek seferlik
  python3 scripts/monitor_paper_run.py 60        # Her 60 saniyede bir
"""
import sys
import os
import sqlite3
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DB_PATH


def check():
    now = datetime.now(timezone.utc)
    print(f"\n{'='*55}")
    print(f"PAPER RUN İZLEME — {now.strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print(f"{'='*55}")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Bakiye
    try:
        bal = conn.execute(
            "SELECT balance, initial_balance FROM paper_account WHERE id=1"
        ).fetchone()
        if bal:
            change = float(bal["balance"]) - float(bal["initial_balance"])
            sign   = "+" if change >= 0 else ""
            print(f"Bakiye:        ${float(bal['balance']):.2f} ({sign}{change:.2f})")
        else:
            print("Bakiye:        kayıt yok")
            bal = None
    except Exception as e:
        print(f"Bakiye:        okunamadı ({e})")
        bal = None

    # Açık tradeler
    try:
        open_t = conn.execute(
            "SELECT id, symbol, direction, status, entry, sl, tp1, "
            "realized_pnl, unrealized_pnl, open_time FROM trades "
            "WHERE status NOT IN ('closed')"
        ).fetchall()
        print(f"Açık Trade:    {len(open_t)}")
        for t in open_t:
            elapsed = ""
            if t["open_time"]:
                try:
                    opened = datetime.fromisoformat(
                        str(t["open_time"]).replace("Z", "+00:00")
                    )
                    if opened.tzinfo is None:
                        opened = opened.replace(tzinfo=timezone.utc)
                    secs = int((now - opened).total_seconds())
                    elapsed = f"{secs//60}dk" if secs < 3600 else f"{secs//3600}sa"
                except Exception:
                    pass
            print(f"  #{t['id']} {t['symbol']} {t['direction']} {t['status']} "
                  f"realized={float(t['realized_pnl'] or 0):.4f} "
                  f"unrealized={float(t['unrealized_pnl'] or 0):.4f} {elapsed}")
    except Exception as e:
        print(f"Açık Trade:    okunamadı ({e})")

    # Bugünkü kapalı tradeler
    try:
        today = now.strftime("%Y-%m-%d")
        closed_today = conn.execute(
            "SELECT COUNT(*) as n, SUM(net_pnl) as pnl FROM trades "
            "WHERE status='closed' AND DATE(close_time)=?",
            (today,)
        ).fetchone()
        n   = closed_today["n"] or 0
        pnl = float(closed_today["pnl"] or 0)
        print(f"Bugün Kapanan: {n} trade, PnL={'+' if pnl >= 0 else ''}{pnl:.4f}")
    except Exception as e:
        print(f"Bugün Kapanan: okunamadı ({e})")

    # Ledger tutarlılığı
    try:
        ledger_sum = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM balance_ledger"
        ).fetchone()[0]
        initial  = float(bal["initial_balance"]) if bal else 250.0
        expected = initial + float(ledger_sum)
        actual   = float(bal["balance"]) if bal else 0
        if abs(expected - actual) > 0.01:
            print(f"UYARI: Ledger tutarsızlığı! beklenen={expected:.2f} gerçek={actual:.2f}")
        else:
            print(f"Ledger:        Tutarlı ({float(ledger_sum):.4f} toplam hareket)")
    except Exception as e:
        print(f"Ledger:        okunamadı ({e})")

    # Son sinyal
    try:
        last_sig = conn.execute(
            "SELECT symbol, decision, created_at FROM signal_candidates "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if last_sig:
            print(f"Son Sinyal:    {last_sig['symbol']} {last_sig['decision']} @ {last_sig['created_at']}")
        else:
            print("Son Sinyal:    henüz yok")
    except Exception as e:
        print(f"Son Sinyal:    okunamadı ({e})")

    # Bot heartbeat
    try:
        hb = conn.execute(
            "SELECT value FROM system_state WHERE key='bot_heartbeat_at'"
        ).fetchone()
        if hb:
            hb_dt = datetime.fromisoformat(hb["value"].replace("Z", "+00:00"))
            diff  = (now - hb_dt).total_seconds()
            status = "ÇALIŞIYOR" if diff < 120 else f"YANIT YOK ({int(diff)}sn)"
            print(f"Bot:           {status}")
        else:
            print("Bot:           Heartbeat kaydı yok")
    except Exception as e:
        print(f"Bot:           okunamadı ({e})")

    conn.close()
    print(f"{'='*55}")


if __name__ == "__main__":
    interval = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    if interval > 0:
        print(f"Her {interval} saniyede bir izleniyor. Çıkmak için Ctrl+C.")
        while True:
            check()
            time.sleep(interval)
    else:
        check()
