"""
Human Mode — python3 hm.py [on|off|status]
Bot yeni trade açmaz, açık pozisyonlar izlenmeye devam eder.
"""
import sys, os, sqlite3
from database import set_bot_control, get_bot_control

def show():
    s = get_bot_control()
    mode = "HUMAN MODE (durduruldu)" if s["paused"] else "BOT AKTIF (otomatik)"
    fin  = " + FINISH MODE" if s["finish_mode"] else ""
    print(f"[hm] Durum: {mode}{fin}")

cmd = sys.argv[1].lower() if len(sys.argv) > 1 else "status"

if cmd == "on":
    set_bot_control(paused=True, updated_by="human_mode")
    print("[hm] Human Mode AÇIK — bot yeni trade açmıyor.")
elif cmd == "off":
    set_bot_control(paused=False, updated_by="human_mode")
    print("[hm] Human Mode KAPALI — bot tekrar aktif.")
else:
    show()
