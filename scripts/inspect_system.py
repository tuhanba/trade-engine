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
    
    # Check journal mode
    journal_mode = "unknown"
    try:
        conn = sqlite3.connect(db_path)
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
    except Exception:
        pass
        
    # WAL file size
    wal_path = db_path + "-wal"
    wal_size = ""
    if os.path.exists(wal_path):
        wal_bytes = os.path.getsize(wal_path)
        wal_size = f" (WAL: {wal_bytes / (1024 * 1024):.2f} MB)"
        
    return f"{size_mb:.2f} MB{wal_size} [Journal Mode: {journal_mode.upper()}]", size_bytes

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
    
    host = config.REDIS_HOST
    port = config.REDIS_PORT
    
    # Try connecting via socket (supports checking localhost from host machine when config is set to service name)
    import socket
    connected = False
    connected_host = host
    for test_host in [host, "127.0.0.1"]:
        if test_host == "redis" and test_host != host:
            # Skip if we are running in a context where "redis" hostname cannot resolve (like host)
            continue
        try:
            s = socket.create_connection((test_host, port), timeout=1)
            s.close()
            connected = True
            connected_host = test_host
            break
        except Exception:
            pass
            
    if not connected:
        return f"Connection failed (could not open port {port} on {host} / 127.0.0.1)"
        
    if not REDIS_MODULE:
        return f"Port {port} on {connected_host} is OPEN (Python redis library not installed on host)"
        
    try:
        r = redis.Redis(
            host=connected_host,
            port=port,
            db=config.REDIS_DB,
            password=config.REDIS_PASSWORD,
            socket_timeout=2
        )
        ping = r.ping()
        if ping:
            info = r.info()
            clients = info.get("connected_clients", "unknown")
            used_mem = info.get("used_memory_human", "unknown")
            return f"Connected to {connected_host}:{port} (Clients: {clients}, Memory: {used_mem})"
    except Exception as e:
        return f"Port {port} on {connected_host} is open, but Redis protocol ping failed: {e}"

def check_systemd_services():
    system = platform.system()
    if system != "Linux":
        return f"Not Linux system (OS: {system}). Bypassing systemd check."
        
    services = ["ax-bot", "ax-dashboard"]
    status = {}
    for s in services:
        try:
            res = subprocess.run(["systemctl", "is-active", s], capture_output=True, text=True)
            active = res.stdout.strip()
            
            res_sub = subprocess.run(["systemctl", "show", "-p", "ActiveState", "-p", "SubState", s], capture_output=True, text=True)
            sub = res_sub.stdout.strip().replace("\n", ", ")
            
            status[s] = f"{active} ({sub})"
        except Exception:
            status[s] = "Not found / Error"
    return status

def get_docker_status():
    status = {"active": False, "containers": [], "stats": "", "space": ""}
    try:
        # Check if docker command is available
        res = subprocess.run(["docker", "ps", "--format", "table {{.Names}}\t{{.Status}}\t{{.Ports}}"], capture_output=True, text=True, timeout=5)
        if res.returncode == 0:
            status["active"] = True
            status["containers"] = [line for line in res.stdout.strip().split("\n") if line]
            
            # Get resource stats (CPU/RAM flow)
            res_stats = subprocess.run(["docker", "stats", "--no-stream", "--format", "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.NetIO}}"], capture_output=True, text=True, timeout=5)
            status["stats"] = res_stats.stdout.strip()
            
            # Get docker disk space
            res_df = subprocess.run(["docker", "system", "df", "--format", "{{.Type}} total: {{.Total}}, active: {{.Active}}, size: {{.Size}}"], capture_output=True, text=True, timeout=5)
            status["space"] = res_df.stdout.strip()
    except Exception as e:
        status["error"] = str(e)
    return status

def get_docker_logs():
    logs = {}
    containers = ["aurvex_engine", "aurvex_dashboard", "aurvex_redis"]
    for c in containers:
        try:
            res = subprocess.run(["docker", "logs", "--tail", "20", c], capture_output=True, text=True, timeout=5)
            if res.returncode == 0:
                logs[c] = res.stdout.strip() or "(No logs / empty)"
            else:
                # Try fallback mapping for stack/compose auto-generated names
                res_fallback = subprocess.run(["docker", "ps", "--filter", f"name={c}", "--format", "{{.Names}}"], capture_output=True, text=True)
                actual_name = res_fallback.stdout.strip().split("\n")[0] if res_fallback.stdout.strip() else None
                if actual_name:
                    res_logs = subprocess.run(["docker", "logs", "--tail", "20", actual_name], capture_output=True, text=True)
                    logs[c] = res_logs.stdout.strip()
                else:
                    logs[c] = f"Container {c} not found or stopped."
        except Exception as e:
            logs[c] = f"Error reading logs: {e}"
    return logs

def get_system_resources():
    res = {}
    
    # Memory RAM Flow
    if platform.system() == "Linux":
        try:
            with open("/proc/meminfo", "r") as f:
                lines = f.readlines()
            mem_info = {}
            for line in lines:
                parts = line.split()
                if len(parts) >= 2:
                    mem_info[parts[0].replace(":", "")] = int(parts[1]) / 1024 # MB
            
            total = mem_info.get("MemTotal", 0)
            free = mem_info.get("MemFree", 0)
            avail = mem_info.get("MemAvailable", 0)
            cached = mem_info.get("Cached", 0)
            buffers = mem_info.get("Buffers", 0)
            sreclaim = mem_info.get("SReclaimable", 0)
            
            # RAM in active use by applications
            used = total - avail
            
            res["memory"] = (
                f"Total: {total:.1f} MB | Used (Apps): {used:.1f} MB | "
                f"Available: {avail:.1f} MB | Buffers/Cache: {buffers + cached + sreclaim:.1f} MB (Free: {free:.1f} MB)"
            )
        except Exception:
            res["memory"] = "N/A"
    else:
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
        res["cpu"] = "N/A"
        # Linux raw cpu calculation fallback
        if platform.system() == "Linux":
            try:
                res_cpu = subprocess.run(["lscpu"], capture_output=True, text=True)
                cores = 1
                for line in res_cpu.stdout.split("\n"):
                    if "CPU(s):" in line and not "NUMA" in line:
                        cores = line.split()[-1]
                        break
                res_load = subprocess.run(["uptime"], capture_output=True, text=True)
                res["cpu"] = f"Cores: {cores} | Load Avg: {res_load.stdout.strip().split('load average:')[-1].strip()}"
            except Exception:
                pass
        
    return res

def run_diagnostics():
    print("=" * 70)
    print("           AURVEX SYSTEM & DOCKER CONSOLE AUDIT            ")
    print("=" * 70)
    
    # 1. System Resources & RAM Flow
    print("\n[1] Host Resources & RAM Flow:")
    resources = get_system_resources()
    for k, v in resources.items():
        print(f"  {k.capitalize():8s}: {v}")
        
    # 2. Docker Containers CPU/RAM Flow
    print("\n[2] Docker Container Statuses:")
    docker = get_docker_status()
    if docker.get("active"):
        print("  Active Containers:")
        for c in docker["containers"]:
            print(f"    {c}")
        if docker["stats"]:
            print("\n  Containers CPU/RAM Flow (docker stats):")
            for line in docker["stats"].split("\n"):
                print(f"    {line}")
        if docker["space"]:
            print("\n  Docker Disk Footprint:")
            for line in docker["space"].split("\n"):
                print(f"    {line}")
    else:
        print("  Docker CLI not active or not running on host system.")
        
    # 3. Host Systemd Services (Legacy)
    print("\n[3] Host Systemd Services (Legacy check):")
    services = check_systemd_services()
    if isinstance(services, dict):
        for k, v in services.items():
            print(f"  {k}: {v}")
    else:
        print(f"  {services}")
        
    # 4. Redis connection status
    print("\n[4] Redis State (Host context):")
    print(f"  Status: {check_redis()}")
    
    # 5. Database Info
    print("\n[5] Database Sizing:")
    db_size_str, _ = get_db_size()
    print(f"  DB Path: {config.DB_PATH}")
    print(f"  DB Size: {db_size_str}")
    
    # 6. Row Counts
    print("\n[6] Database Record Counts:")
    counts = get_row_counts()
    for table, count in counts.items():
        print(f"  {table:20s} : {count}")
        
    # 7. Recent Docker Logs
    if docker.get("active"):
        print("\n" + "=" * 70)
        print("                 RECENT CONTAINER LOG TAILS                  ")
        print("=" * 70)
        logs = get_docker_logs()
        for name, log_content in logs.items():
            print(f"\n>>> Container: {name} (Last 20 lines) >>>")
            print("-" * 50)
            print(log_content)
            print("-" * 50)
            
    print("\n" + "=" * 70)

if __name__ == "__main__":
    run_diagnostics()
