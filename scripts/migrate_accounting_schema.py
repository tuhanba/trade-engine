"""
scripts/migrate_accounting_schema.py – Migration helper.

init_db() ve migrate_db() çalıştırır, eksik kolon raporlar.
Veri silmez.
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database


def main():
    print("AX Trade Engine – DB Migration")
    print("=" * 40)

    # Tabloları oluştur
    database.init_db()
    print("✓ init_db() tamamlandı")

    # Eksik kolonları ekle
    added = database.migrate_db()

    if added:
        print(f"✓ {len(added)} kolon eklendi:")
        for col in added:
            print(f"  + {col}")
    else:
        print("✓ Eksik kolon yok – şema güncel")

    print("=" * 40)
    print("Migration tamamlandı. Veri silinmedi.")


if __name__ == "__main__":
    main()
