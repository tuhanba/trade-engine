import sys
sys.stdout.reconfigure(encoding='utf-8')

with open("database.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

found = False
for idx, line in enumerate(lines):
    if "def is_coin_in_cooldown" in line:
        found = True
        start = max(0, idx - 2)
        end = min(len(lines), idx + 40)
        for i in range(start, end):
            print(f"{i+1}: {lines[i]}", end="")
        break

if not found:
    print("Function is_coin_in_cooldown not found.")
