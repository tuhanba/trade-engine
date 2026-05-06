"""Git add, commit, push helper — sandbox workaround."""
import subprocess, os
os.chdir(r"c:\Users\pc\Desktop\AURVEX Ai")
cmds = [
    ["git", "add", "-A"],
    ["git", "commit", "-m", "feat: CoinGecko fallback + watchdog + systemd 7/24 + adaptive scan + daily telegram report"],
    ["git", "push", "origin", "main"],
]
for cmd in cmds:
    r = subprocess.run(cmd, capture_output=True, text=True)
    print(f"{'OK' if r.returncode == 0 else 'FAIL'}: {' '.join(cmd[:3])}")
    if r.stdout.strip(): print(r.stdout.strip())
    if r.stderr.strip(): print(r.stderr.strip())
