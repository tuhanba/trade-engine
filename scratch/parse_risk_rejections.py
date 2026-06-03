with open(r"C:\Users\pc\.gemini\antigravity\brain\6f3e64ed-751b-4f8e-a089-2cb824ce2aec\.system_generated\tasks\task-1395.log", "r", encoding="utf-8") as f:
    lines = f.readlines()

for line in lines:
    if "Risk" in line or "risk" in line or "Correlation" in line or "leverage" in line:
        print(line.strip())
