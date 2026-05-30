"""
reset_paper_balance.py — Paper bakiyeyi config.INITIAL_PAPER_BALANCE'a sıfırlar.
Çalıştır: python3 scripts/reset_paper_balance.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from database import init_db, init_paper_account, get_conn

init_db()
init_paper_account(reset=True)

with get_conn() as conn:
    row = conn.execute("SELECT balance, initial_balance FROM paper_account WHERE id=1").fetchone()
    print(f"✅ Bakiye sıfırlandı: ${row[0]:.2f}  (initial_balance=${row[1]:.2f})")
