#!/usr/bin/env python3
"""funnel_report.py — read-only veto-funnel teşhisi. Hiçbir şey YAZMAZ.

Son N saatte sinyal funnel'ının nerede tıkandığını tek bakışta gösterir:
hangi stage en çok düşürüyor ve en sık reddetme sebepleri neler.

Kullanım:
    python scripts/funnel_report.py [SAAT]   # varsayılan 24

NEDEN (CLAUDE_ENGINE_LOOP1 §3): pipeline reject sebepleri zaten
`signal_events` tablosuna yazılıyor; eksik olan tek-kaynak özet. Bu script
sadece OKUR — engine davranışını değiştirmez (Loop 1 sözleşmesi).
"""
import os
import sys
from collections import Counter

# NEDEN: scripts/ altından çalıştırıldığında repo kökü sys.path'te değil
# (sibling script konvansiyonu — bkz. scripts/inspect_system.py).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import get_conn  # noqa: E402

HOURS = int(sys.argv[1]) if len(sys.argv) > 1 else 24


def main():
    # NEDEN: get_conn() @contextmanager — `with` ile kullanılmalı (template'teki
    # düz `conn = get_conn()` bu repoda çalışmaz).
    try:
        with get_conn() as conn:
            cur = conn.cursor()
            # NEDEN: gerçek şema kolonu `stage` (template'teki `event_type` DEĞİL —
            # o ad save_signal_event()'in parametresi, DB'ye `stage` olarak yazılır).
            # NEDEN: created_at ISO-8601 (.isoformat(), 'T' ayraçlı) saklanıyor;
            # datetime('now') boşluk ayraçlı üretir → ham string karşılaştırması 24s
            # sınırında kayar. datetime(created_at) ile normalize ederek doğru filtrele.
            cur.execute(
                "SELECT stage, reject_reason FROM signal_events "
                "WHERE datetime(created_at) >= datetime('now', ?)",
                (f'-{HOURS} hours',)
            )
            rows = cur.fetchall()
    except Exception as e:
        # NEDEN: teşhis aracı fresh/boş DB'de stack-trace ile patlamasın; engine
        # henüz hiç olay loglamadıysa tablo bulunmayabilir.
        if "no such table" in str(e).lower():
            print(f"=== FUNNEL (last {HOURS}h) — 0 events ===")
            print("signal_events tablosu yok: engine bu DB'de henüz olay loglamadı.")
            return
        raise

    stage = Counter(r[0] for r in rows)
    reasons = Counter((r[0], r[1]) for r in rows if r[1])

    print(f"=== FUNNEL (last {HOURS}h) — {len(rows)} events ===")
    for s, c in stage.most_common():
        print(f"{str(s):20s} {c}")

    print("\n=== TOP REJECT REASONS ===")
    for (s, reason), c in reasons.most_common(15):
        print(f"{c:5d}  {str(s):18s} {reason}")


if __name__ == "__main__":
    main()
