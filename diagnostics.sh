#!/bin/bash
# ======================================================================
# AURVEXAI // Advanced Server Diagnostics & System Auditor
# ======================================================================

BOLD='\033[1;37m'
GOLD='\033[1;33m'
GREEN='\033[1;32m'
RED='\033[1;31m'
BLUE='\033[1;34m'
RESET='\033[0m'

echo -e "${GOLD}======================================================================"
echo -e "          AURVEXAI SYSTEM AUDIT & DIAGNOSTICS TOOL"
echo -e "======================================================================${RESET}"

# 1. SYSTEM INFORMATION
echo -e "\n${BOLD}[1] System Info & Resources${RESET}"
echo -e "--------------------------------------------------"
echo -e "OS & Kernel:      $(uname -a)"
echo -e "Uptime:           $(uptime -p)"
echo -e "CPU Load:         $(top -bn1 | grep "Cpu(s)" | sed "s/.*, *\([0-9.]*\)%* id.*/\1/" | awk '{print 100 - $1"%"}') load"
echo -e "Memory Usage:     $(free -m | awk 'NR==2{printf "Total: %dMB, Used: %dMB (%.2f%%)", $2,$3,$3*100/$2}')"
echo -e "Disk Usage (/) :  $(df -h / | awk 'NR==2{print $3 " / " $2 " (" $5 " used)"}')"

# 2. PYTHON ENVIRONMENT & RUNNING PROCESSES
echo -e "\n${BOLD}[2] Python & Active Process Auditing${RESET}"
echo -e "--------------------------------------------------"
echo -e "Python Path:      $(which python3)"
echo -e "Python Version:   $(python3 --version 2>&1)"
echo -e "Active Processes:"
ps aux | grep -E "(python3?|python).*(app\.py|main\.py|async_scalp_engine\.py|service|engine)" | grep -v grep | awk '{
    ppid = "1"
    cmd = "ps -p " $2 " -o ppid="
    if ((cmd | getline ppid) <= 0) { ppid = "1" }
    close(cmd)
    
    pname = "systemd"
    cmd2 = "ps -p " ppid " -o comm="
    if ((cmd2 | getline pname) <= 0) { pname = "systemd" }
    close(cmd2)
    
    print "  PID: " $2 " (PPID: " ppid " [" pname "]) | CPU: " $3 "% | RAM: " $4 "% | Cmd: " $11 " " $12 " " $13
}' || echo -e "  ${RED}No active Python trading processes found.${RESET}"

# 3. GIT STATUS & FILES
echo -e "\n${BOLD}[3] Repository & Directory Structure Audit${RESET}"
echo -e "--------------------------------------------------"
echo -e "Current Branch:   $(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo 'Not a git repo')"
echo -e "Git Status:       "
git status -s 2>/dev/null || echo -e "  ${RED}Git status lookup failed.${RESET}"
echo -e "\nFile Count by Type:"
find . -maxdepth 3 -not -path '*/.*' -not -path './venv*' -not -path './__pycache__*' | sed -e 's/.*\.//' | sort | uniq -c | awk '{print "  " $2 ": " $1 " files"}'
echo -e "\nTop 5 Largest Files:"
find . -maxdepth 3 -not -path '*/.*' -not -path './venv*' -type f -exec du -h {} + 2>/dev/null | sort -hr | head -n 5 | sed 's/^/  /'

# 4. DATABASE INTEGRITY & SCHEMAS
echo -e "\n${BOLD}[4] Database Auditing (SQLite)${RESET}"
echo -e "--------------------------------------------------"
DB_FILE="db/trading.db"
if [ ! -f "$DB_FILE" ]; then
    DB_FILE="trading.db"
fi

if [ -f "$DB_FILE" ]; then
    DB_SIZE=$(du -h "$DB_FILE" | cut -f1)
    WAL_SIZE="0"
    if [ -f "$DB_FILE-wal" ]; then
        WAL_SIZE=$(du -h "$DB_FILE-wal" | cut -f1)
    fi
    echo -e "Database File:    $DB_FILE ($DB_SIZE)"
    echo -e "WAL Log Size:     $DB_FILE-wal ($WAL_SIZE)"
    
    echo -e "\nRow Counts by Key Tables:"
    sqlite3 "$DB_FILE" "
        SELECT '  trades             : ' || count(*) FROM trades;
        SELECT '  ghost_signals      : ' || count(*) FROM ghost_signals;
        SELECT '  paper_results      : ' || count(*) FROM paper_results;
        SELECT '  signal_events      : ' || count(*) FROM signal_events;
        SELECT '  ai_logs            : ' || count(*) FROM ai_logs;
    " 2>/dev/null || echo -e "  ${RED}Failed to query database table rows.${RESET}"
else
    echo -e "  ${RED}Database file ($DB_FILE) not found in working directory!${RESET}"
fi

# 5. SYSTEMD SERVICE MONITORING
echo -e "\n${BOLD}[5] Systemd Service Monitoring${RESET}"
echo -e "--------------------------------------------------"
SERVICES=("aurvex-bot" "aurvex-dashboard" "ax-bot" "ax-dashboard" "aurvex-watchdog" "ax-watchdog")
for SVC in "${SERVICES[@]}"; do
    if systemctl list-unit-files | grep -q "^${SVC}\.service"; then
        STATUS=$(systemctl is-active "$SVC")
        if [ "$STATUS" = "active" ]; then
            COLOR=$GREEN
        else
            COLOR=$RED
        fi
        echo -e "Service: ${BOLD}$SVC${RESET} -> Status: ${COLOR}$STATUS${RESET}"
        systemctl status "$SVC" --no-pager -n 3 | grep -E "Active:|Main PID:" | sed 's/^/  /'
    fi
done

# 6. LOG EXCERPTS
echo -e "\n${BOLD}[6] Last Log Lines (Auditing Exceptions & Warns)${RESET}"
echo -e "--------------------------------------------------"
# Check systemd journal for active services
for SVC in "${SERVICES[@]}"; do
    if systemctl list-unit-files | grep -q "^${SVC}\.service"; then
        echo -e "\n--- Systemd Journal: $SVC ---"
        journalctl -u "$SVC" -n 15 --no-pager | sed 's/^/  /'
    fi
done

# Check local log directories if any
if [ -d "logs" ]; then
    echo -e "\n--- Local Logs Folder Contents ---"
    ls -lh logs/ | sed 's/^/  /'
    for LOGFILE in logs/*.log; do
        if [ -f "$LOGFILE" ]; then
            echo -e "\n--- End of $LOGFILE ---"
            tail -n 15 "$LOGFILE" | sed 's/^/  /'
        fi
    done
fi

echo -e "\n${GOLD}======================================================================"
echo -e "                      DIAGNOSTICS COMPLETED"
echo -e "======================================================================${RESET}"
