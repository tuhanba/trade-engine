import sys
sys.stdout.reconfigure(encoding='utf-8')

with open("database.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

found = False
for idx, line in enumerate(lines):
    if "INSERT INTO coin_cooldown" in line or "coin_cooldown" in line.lower():
        if "create table" in line.lower() or "def is_coin" in line.lower():
            continue
        print(f"Line {idx+1}: {line.strip()}")
        # Print surrounding context
        start = max(0, idx - 5)
        end = min(len(lines), idx + 10)
        for i in range(start, end):
            print(f"  {i+1}: {lines[i]}", end="")
        print("-" * 40)
        found = True

if not found:
    print("No other coin_cooldown queries found.")
