"""Git helper 2"""
import subprocess, os
os.chdir(r"c:\Users\pc\Desktop\AURVEX Ai")
cmds = [
    ["git", "add", "config.py", "execution_engine.py"],
    ["git", "commit", "-m", "feat: Strict paper mode guard, no private Binance calls in paper, triple safety check"],
    ["git", "push", "origin", "main"],
]
for cmd in cmds:
    r = subprocess.run(cmd, capture_output=True, text=True)
    print(f"{'OK' if r.returncode == 0 else 'FAIL'}: {' '.join(cmd[:3])}")
    if r.stdout.strip(): print(r.stdout.strip())
    if r.stderr.strip(): print(r.stderr.strip())
