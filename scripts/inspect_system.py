import os
import sys
import sqlite3
import shutil
import platform
import subprocess

try:
    import redis
    REDIS_MODULE = True
except ImportError:
    REDIS_MODULE = False

# Add parent directory to path to import config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

def get_db_size():
    db_path = config.DB_PATH
    if not os.path.exists(db_path):
        return "Not found", 0
    size_bytes = os.path.getsize(db_path)
    size_mb = size_bytes / (1024 * 1024)
    
    # WAL file size
    wal_path = db_path + "-wal"
    wal_size = ""
    if os.path.exists(wal_path):
        wal_bytes = os.path.getsize(wal_path)
        wal_size = f" (WAL: {wal_bytes / (1024 * 1024):.2f} MB)"
        
    return f"{size_mb:.2f} MB{wal_size}", size_bytes

def get_row_counts():
    db_path = config.DB_PATH
    if not os.path.exists(db_path):
        return {}
    
    counts = {}
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        
        tables = ["trades", "signal_candidates", "signal_events", "telegram_messages", 
                  "ghost_signals", "ghost_results", "paper_results", "coin_profiles"]
                  
        db_tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        
        for t in tables:
            if t in db_tables:
                row = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()
                counts[t] = row[0]
            else:
                counts[t] = "N/A"
                
        conn.close()
    except Exception as e:
        counts["error"] = str(e)
    return counts

def check_redis():
    if not getattr(config, "REDIS_ENABLED", True):
        return "Disabled in config"
    
    if not REDIS_MODULE:
        return "Python redis library not installed"
        
    try:
        r = redis.Redis(
            host=config.REDIS_HOST,
            port=config.REDIS_PORT,
            db=config.REDIS_DB,
            password=config.REDIS_PASSWORD,
            socket_timeout=2
        )
        ping = r.ping()
        if ping:
            info = r.info()
            clients = info.get("connected_clients", "unknown")
            used_mem = info.get("used_memory_human", "unknown")
            return f"Connected (Clients: {clients}, Memory: {used_mem})"
    except Exception as e:
        return f"Connection failed: {e}"

def check_services():
    system = platform.system()
    if system != "Linux":
        return f"Not a Linux system (OS: {system}). Cannot query systemd."
        
    services = ["ax-bot", "ax-dashboard"]
    status = {}
    for s in services:
        try:
            res = subprocess.run(["systemctl", "is-active", s], capture_output=True, text=True)
            active = res.stdout.strip()
            
            res_sub = subprocess.run(["systemctl", "show", "-p", "ActiveState", "-p", "SubState", s], capture_output=True, text=True)
            sub = res_sub.stdout.strip().replace("\n", ", ")
            
            status[s] = f"{active} ({sub})"
        except Exception as e:
            status[s] = f"Error: {e}"
    return status

def get_system_resources():
    res = {}
    
    # Memory
    if platform.system() == "Linux":
        try:
            with open("/proc/meminfo", "r") as f:
                lines = f.readlines()
            mem_total = 0
            mem_free = 0
            mem_avail = 0
            for line in lines:
                if "MemTotal" in line:
                    mem_total = int(line.split()[1]) / 1024 # MB
                elif "MemFree" in line:
                    mem_free = int(line.split()[1]) / 1024 # MB
                elif "MemAvailable" in line:
                    mem_avail = int(line.split()[1]) / 1024 # MB
            res["memory"] = f"Total: {mem_total:.1f} MB, Available: {mem_avail:.1f} MB (Free: {mem_free:.1f} MB)"
        except Exception:
            res["memory"] = "N/A"
    else:
        # Windows/Mac basic memory fallback using psutil if available or shutil for disk
        res["memory"] = "N/A (psutil not installed)"
        try:
            import psutil
            mem = psutil.virtual_memory()
            res["memory"] = f"Total: {mem.total / (1024*1024):.1f} MB, Available: {mem.available / (1024*1024):.1f} MB"
        except ImportError:
            pass
            
    # Disk Usage
    try:
        total, used, free = shutil.disk_usage(".")
        res["disk"] = f"Total: {total / (1024**3):.2f} GB, Used: {used / (1024**3):.2f} GB, Free: {free / (1024**3):.2f} GB"
    except Exception as e:
        res["disk"] = f"Error: {e}"
        
    # CPU
    try:
        import psutil
        res["cpu"] = f"Logical Cores: {psutil.cpu_count()}, Usage: {psutil.cpu_percent(interval=0.5)}%"
    except ImportError:
        res["cpu"] = "N/A (psutil not installed)"
        
    return res

def run_diagnostics():
    print("=" * 60)
    print("           AURVEX SYSTEM DIAGNOSTICS & AUDIT            ")
    print("=" * 60)
    
    # 1. System Resources
    print("\n[1] System Resources:")
    resources = get_system_resources()
    for k, v in resources.items():
        print(f"  {k.capitalize()}: {v}")
        
    # 2. Services
    print("\n[2] Systemd Services:")
    services = check_services()
    if isinstance(services, dict):
        for k, v in services.items():
            print(f"  {k}: {v}")
    else:
        print(f"  {services}")
        
    # 3. Redis
    print("\n[3] Redis State:")
    print(f"  Status: {check_redis()}")
    
    # 4. Database Info
    print("\n[4] Database Metrics:")
    db_size_str, _ = get_db_size()
    print(f"  DB Path: {config.DB_PATH}")
    print(f"  DB Size: {db_size_str}")
    
    # 5. Row Counts
    print("\n[5] Database Table Counts:")
    counts = get_row_counts()
    for table, count in counts.items():
        print(f"  {table:20s} : {count}")
        
    print("\n" + "=" * 60)

if __name__ == "__main__":
    run_diagnostics()
