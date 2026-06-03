import re
import sys
sys.stdout.reconfigure(encoding='utf-8')

pattern = re.compile(r"@app\.route\((.*?)\)")

with open("app.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

for idx, line in enumerate(lines):
    match = pattern.search(line)
    if match:
        print(f"Line {idx+1}: {line.strip()}")
        # print function defined below it
        for i in range(idx + 1, min(len(lines), idx + 5)):
            if "def " in lines[i]:
                print(f"  Function: {lines[i].strip()}")
                break
