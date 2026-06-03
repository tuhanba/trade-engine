import os
import re
import sys
sys.stdout.reconfigure(encoding='utf-8')

pattern = re.compile(r"coin_cooldown|cooldown", re.IGNORECASE)

for root, dirs, files in os.walk("."):
    if any(p in root for p in [".git", "__pycache__", ".venv", "backtest_data"]):
        continue
    for file in files:
        if not file.endswith(".py"):
            continue
        path = os.path.join(root, file)
        try:
            with open(path, "r", encoding="utf-8") as f:
                for idx, line in enumerate(f):
                    if pattern.search(line):
                        print(f"{path}:{idx+1}: {line.strip()}")
        except Exception:
            pass
