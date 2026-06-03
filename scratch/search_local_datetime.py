import os
import re
import sys
sys.stdout.reconfigure(encoding='utf-8')

pattern = re.compile(r"^\s+(from datetime|import datetime)", re.MULTILINE)

for root, dirs, files in os.walk("."):
    if any(p in root for p in [".git", "__pycache__", ".venv", "backtest_data"]):
        continue
    for file in files:
        if not file.endswith(".py"):
            continue
        path = os.path.join(root, file)
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
                matches = pattern.finditer(content)
                for m in matches:
                    # Find line number
                    line_no = content[:m.start()].count("\n") + 1
                    lines = content.split("\n")
                    start_idx = max(0, line_no - 4)
                    end_idx = min(len(lines), line_no + 3)
                    print(f"=== {path}:{line_no} ===")
                    for i in range(start_idx, end_idx):
                        prefix = "--> " if i == line_no - 1 else "    "
                        print(f"{i+1:4d}:{prefix}{lines[i]}")
                    print()
        except Exception:
            pass
