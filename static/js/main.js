/**
 * AURVEX.Ai Dashboard V2 Logic
 */

const API_BASE = '/api';

async function fetchDashboardData() {
    try {
        const response = await fetch(`${API_BASE}/dashboard_data`);
        if (!response.ok) return;
        const data = await response.json();
        
        updateHeaderStats(data);
        updatePortfolioCard(data);
        updatePerformanceCard(data);
        updateActiveTrades(data.active_trades || []);
        
    } catch (e) {
        console.error("Dashboard fetch error:", e);
    }
}

function updateHeaderStats(data) {
    // We assume backend might send these later, for now mock or use what's available
    document.getElementById('marketRegime').textContent = data.market_regime || "NEUTRAL";
    // For FNG we can just display the value
    document.getElementById('macroSentiment').textContent = data.macro_fng ? `${data.macro_fng} (Fear)` : "50 (Neutral)";
}

function updatePortfolioCard(data) {
    document.getElementById('totalBalance').textContent = parseFloat(data.total_balance || 0).toFixed(2);
    
    const dailyPnl = parseFloat(data.daily_pnl || 0);
    const pnlEl = document.getElementById('dailyPnl');
    pnlEl.textContent = `${dailyPnl >= 0 ? '+' : ''}${dailyPnl.toFixed(2)}`;
    
    if (dailyPnl > 0) {
        pnlEl.className = 'pnl-value pnl-positive';
    } else if (dailyPnl < 0) {
        pnlEl.className = 'pnl-value pnl-negative';
    } else {
        pnlEl.className = 'pnl-value';
    }
}

function updatePerformanceCard(data) {
    const stats = data.stats || {};
    document.getElementById('totalTrades').textContent = stats.total_trades || 0;
    
    const winRate = stats.win_rate || 0;
    document.getElementById('winRate').textContent = `${winRate.toFixed(1)}%`;
    
    document.getElementById('profitFactor').textContent = parseFloat(stats.profit_factor || 0).toFixed(2);
}

function updateActiveTrades(trades) {
    const tbody = document.getElementById('activeTradesBody');
    document.getElementById('activeTradesCount').textContent = trades.length;
    
    tbody.innerHTML = '';
    
    if (trades.length === 0) {
        tbody.innerHTML = `<tr><td colspan="5" style="text-align: center; color: var(--text-muted)">No active positions</td></tr>`;
        return;
    }
    
    trades.forEach(t => {
        const row = document.createElement('tr');
        
        const sideClass = t.direction === 'LONG' ? 'side-long' : 'side-short';
        const pnl = parseFloat(t.unrealized_pnl || 0);
        const pnlClass = pnl >= 0 ? 'pnl-positive' : 'pnl-negative';
        
        row.innerHTML = `
            <td><strong>${t.symbol}</strong></td>
            <td class="${sideClass}">${t.direction}</td>
            <td>${parseFloat(t.entry_price).toFixed(4)}</td>
            <td>${parseFloat(t.current_price || t.entry_price).toFixed(4)}</td>
            <td class="${pnlClass}">${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}</td>
        `;
        tbody.appendChild(row);
    });
}

// Simulated AI Brain Feed (could be fetched via WebSocket or API)
const mockLogs = [
    "[AI] Analyzing BTC 15m confluence... STRONG",
    "[Scanner] Found ETH setup, evaluating...",
    "[Macro] Fear & Greed = 45. Risk normal.",
    "[Trigger] ETHLONG Quality B. Sending to AI...",
    "[AI] Signal VETOED: Correlation Shield Active."
];

function addLogLine(text, type='normal') {
    const container = document.getElementById('aiLogsContainer');
    const line = document.createElement('div');
    line.className = `terminal-line ${type}`;
    
    const now = new Date();
    const timeStr = now.toISOString().split('T')[1].substring(0,8);
    
    line.innerHTML = `<span class="time">[${timeStr}]</span> ${text}`;
    container.appendChild(line);
    container.scrollTop = container.scrollHeight;
}

// Init
document.addEventListener('DOMContentLoaded', () => {
    fetchDashboardData();
    setInterval(fetchDashboardData, 3000); // 3 sec polling
    
    document.getElementById('refreshBtn').addEventListener('click', () => {
        fetchDashboardData();
        addLogLine("System forced refresh requested.", "success");
    });
    
    // Simulate AI logs for premium feel
    setInterval(() => {
        if(Math.random() > 0.7) {
            const randomLog = mockLogs[Math.floor(Math.random() * mockLogs.length)];
            let type = 'normal';
            if (randomLog.includes('VETO')) type = 'veto';
            if (randomLog.includes('STRONG')) type = 'success';
            addLogLine(randomLog, type);
        }
    }, 4000);
});
