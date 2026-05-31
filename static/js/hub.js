/**
 * AURVEXAI // Premium Real-time Dashboard Logic
 */

// Global state
const state = {
    activeTrades: [],
    closedTrades: [],
    funnel: {},
    stats: {},
    params: {},
    logs: [],
    balance: 0.00,
    dailyPnl: 0.00,
    winRate: 0.00,
    profitFactor: 0.00,
    executionMode: 'paper',
    humanMode: false,
    chart: null
};

// Formatting helpers
const formatUSD = (val) => '$' + parseFloat(val || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const formatPercent = (val) => parseFloat(val || 0).toFixed(1) + '%';
const formatTime = (ts) => {
    if (!ts) return '';
    try {
        const d = new Date(ts.replace(" ", "T") + "Z");
        return d.toLocaleTimeString('en-US', { hour12: false });
    } catch(e) {
        return ts;
    }
};

// Clock update
function updateClock() {
    const now = new Date();
    const utcStr = now.toUTCString().slice(17, 25);
    document.getElementById('headerClock').textContent = utcStr + ' UTC';
}

// ----------------------------------------------------
// 📊 CHART.JS EQUITY CURVE
// ----------------------------------------------------
async function initEquityChart() {
    try {
        const resp = await fetch('/api/equity-curve');
        if (!resp.ok) return;
        const data = await resp.json();
        
        const labels = [];
        const pnlData = [];
        
        const points = data.points || [];
        points.forEach(pt => {
            labels.push(pt.day || '');
            pnlData.push(pt.balance || 0);
        });

        // Dynamic header badge update
        const pctEl = document.getElementById('pctChangeText');
        const pct = data.pct_change || 0.00;
        pctEl.textContent = `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}% total growth`;
        if (pct >= 0) {
            pctEl.className = 'badge-flat';
        } else {
            pctEl.className = 'badge-flat negative';
        }

        const ctx = document.getElementById('equityChart').getContext('2d');
        
        // Destroy old instance if exists
        if (state.chart) {
            state.chart.destroy();
        }

        // Gradient configuration
        const gradient = ctx.createLinearGradient(0, 0, 0, 180);
        gradient.addColorStop(0, 'rgba(212, 168, 67, 0.12)');
        gradient.addColorStop(1, 'rgba(212, 168, 67, 0.00)');

        state.chart = new Chart(ctx, {
            type: 'line',
            data: {
                labels: labels.length ? labels : ['Starting'],
                datasets: [{
                    label: 'USDT Balance',
                    data: pnlData.length ? pnlData : [data.initial_balance || 2000.0],
                    borderColor: '#d4a843',
                    borderWidth: 2,
                    backgroundColor: gradient,
                    fill: true,
                    tension: 0.35,
                    pointRadius: labels.length > 15 ? 0 : 3,
                    pointBackgroundColor: '#d4a843',
                    pointHoverRadius: 5
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false }
                },
                scales: {
                    x: {
                        grid: { color: 'rgba(255, 255, 255, 0.02)' },
                        ticks: { color: '#6b7280', font: { size: 9 } }
                    },
                    y: {
                        grid: { color: 'rgba(255, 255, 255, 0.02)' },
                        ticks: { color: '#6b7280', font: { size: 9, family: 'JetBrains Mono' } }
                    }
                }
            }
        });

    } catch (e) {
        console.error("Failed to load equity chart:", e);
    }
}

// ----------------------------------------------------
// ⚙️ CONTROLS HANDLERS
// ----------------------------------------------------
function updateConfigUI(key, value) {
    if (key === 'tg_execution_mode') {
        const isLive = value === 'live';
        document.getElementById('btnModePaper').className = isLive ? 'toggle-btn' : 'toggle-btn active';
        document.getElementById('btnModeLive').className = isLive ? 'toggle-btn active' : 'toggle-btn';
        
        const modeBadge = document.getElementById('modeBadge');
        const modeText = document.getElementById('modeText');
        modeText.textContent = isLive ? 'LIVE MODE' : 'PAPER MODE';
        if (isLive) {
            modeBadge.querySelector('.badge-dot').className = 'badge-dot dot-gold';
        } else {
            modeBadge.querySelector('.badge-dot').className = 'badge-dot dot-green';
        }
    } else if (key === 'tg_human_mode') {
        const isHuman = value === true || value === 'True' || value === '1';
        document.getElementById('btnHumanFalse').className = isHuman ? 'toggle-btn' : 'toggle-btn active';
        document.getElementById('btnHumanTrue').className = isHuman ? 'toggle-btn active' : 'toggle-btn';
    } else if (key === 'trade_threshold') {
        document.getElementById('sliderTradeThreshold').value = value;
        document.getElementById('valTradeThreshold').textContent = parseFloat(value).toFixed(1);
    } else if (key === 'telegram_threshold') {
        document.getElementById('sliderTelegramThreshold').value = value;
        document.getElementById('valTelegramThreshold').textContent = parseFloat(value).toFixed(1);
    } else if (key === 'watchlist_threshold') {
        document.getElementById('sliderWatchlistThreshold').value = value;
        document.getElementById('valWatchlistThreshold').textContent = parseFloat(value).toFixed(1);
    } else if (key === 'data_threshold') {
        document.getElementById('sliderDataThreshold').value = value;
        document.getElementById('valDataThreshold').textContent = parseFloat(value).toFixed(1);
    }
}

async function postConfigUpdate(key, value) {
    try {
        const resp = await fetch('/api/config/update', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key, value })
        });
        const res = await resp.json();
        if (res.ok) {
            updateConfigUI(key, value);
            // Refresh logs
            addConsoleLog(`[Config] Successfully updated ${key} to ${value}`, 'success');
        } else {
            addConsoleLog(`[Error] Failed to update config: ${res.error}`, 'error');
        }
    } catch(e) {
        console.error("Config update error:", e);
    }
}

function bindControlElements() {
    // Mode Switch
    document.getElementById('btnModePaper').addEventListener('click', () => postConfigUpdate('tg_execution_mode', 'paper'));
    document.getElementById('btnModeLive').addEventListener('click', () => postConfigUpdate('tg_execution_mode', 'live'));
    
    // Human Mode Switch
    document.getElementById('btnHumanFalse').addEventListener('click', () => postConfigUpdate('tg_human_mode', false));
    document.getElementById('btnHumanTrue').addEventListener('click', () => postConfigUpdate('tg_human_mode', true));

    // Sliders
    const sliders = [
        { id: 'sliderTradeThreshold', key: 'trade_threshold', valId: 'valTradeThreshold' },
        { id: 'sliderTelegramThreshold', key: 'telegram_threshold', valId: 'valTelegramThreshold' },
        { id: 'sliderWatchlistThreshold', key: 'watchlist_threshold', valId: 'valWatchlistThreshold' },
        { id: 'sliderDataThreshold', key: 'data_threshold', valId: 'valDataThreshold' }
    ];

    sliders.forEach(slider => {
        const el = document.getElementById(slider.id);
        const valEl = document.getElementById(slider.valId);
        
        el.addEventListener('input', (e) => {
            valEl.textContent = parseFloat(e.target.value).toFixed(1);
        });

        el.addEventListener('change', (e) => {
            postConfigUpdate(slider.key, parseFloat(e.target.value));
        });
    });
}

// ----------------------------------------------------
// 📥 DATA RETRIEVAL & RENDERING
// ----------------------------------------------------
async function fetchConfigParams() {
    try {
        const resp = await fetch('/api/params');
        if (!resp.ok) return;
        const data = await resp.json();
        if (data.ok && data.data) {
            const p = data.data;
            updateConfigUI('tg_execution_mode', p.execution_mode);
            updateConfigUI('tg_human_mode', p.human_mode);
            updateConfigUI('trade_threshold', p.trade_threshold);
            updateConfigUI('telegram_threshold', p.telegram_threshold);
            updateConfigUI('watchlist_threshold', p.watchlist_threshold);
            updateConfigUI('data_threshold', p.data_threshold);
        }
    } catch(e) {}
}

async function fetchStatsAndFunnel() {
    try {
        // Stats
        const statsResp = await fetch('/api/stats');
        if (statsResp.ok) {
            const stats = await statsResp.json();
            if (stats.ok && stats.data) {
                const d = stats.data;
                document.getElementById('winRate').textContent = formatPercent(d.win_rate || d.winrate);
                document.getElementById('profitFactor').textContent = parseFloat(d.profit_factor || 0).toFixed(2);
                
                // Funnel Stages Update
                const f = d.funnel || {};
                const scanned = parseInt(f.scanned || 0);
                const eligible = parseInt(f.eligible || 0);
                const trend = parseInt(f.trend_ok || 0);
                const risk = parseInt(f.risk_ok || 0);
                const telegram = parseInt(f.telegram || 0);
                const executed = parseInt(f.executed || 0);

                document.getElementById('valScanned').textContent = scanned;
                document.getElementById('valEligible').textContent = eligible;
                document.getElementById('valTrend').textContent = trend;
                document.getElementById('valRisk').textContent = risk;
                document.getElementById('valTelegram').textContent = telegram;
                document.getElementById('valExecuted').textContent = executed;

                // Adjust Stage Progress Widths dynamically based on scanned volume
                const max = Math.max(scanned, 1);
                document.getElementById('barScanned').style.width = '100%';
                document.getElementById('barEligible').style.width = `${(eligible / max * 100).toFixed(1)}%`;
                document.getElementById('barTrend').style.width = `${(trend / max * 100).toFixed(1)}%`;
                document.getElementById('barRisk').style.width = `${(risk / max * 100).toFixed(1)}%`;
                document.getElementById('barTelegram').style.width = `${(telegram / max * 100).toFixed(1)}%`;
                document.getElementById('barExecuted').style.width = `${(executed / max * 100).toFixed(1)}%`;
            }
        }

        // Dashboard Data
        const dbResp = await fetch('/api/dashboard_data');
        if (dbResp.ok) {
            const dbData = await dbResp.json();
            
            // Balance & PnL
            const totalBal = parseFloat(dbData.total_balance || 0);
            const dailyPnl = parseFloat(dbData.daily_pnl || 0);
            
            document.getElementById('walletBalance').textContent = formatUSD(totalBal);
            
            const pnlEl = document.getElementById('dailyPnl');
            pnlEl.textContent = `${dailyPnl >= 0 ? '+' : ''}${formatUSD(dailyPnl)}`;
            
            const mainCard = pnlEl.closest('.stat-card');
            if (dailyPnl > 0) {
                pnlEl.className = 'stat-value text-pnl-positive';
            } else if (dailyPnl < 0) {
                pnlEl.className = 'stat-value text-pnl-negative';
            } else {
                pnlEl.className = 'stat-value';
            }

            // Positions Render
            renderActiveTrades(dbData.active_trades || []);
        }

    } catch(e) {}
}

function renderActiveTrades(positions) {
    const tbody = document.getElementById('activeTradesBody');
    document.getElementById('activeTradesCount').textContent = positions.length;
    
    if (positions.length === 0) {
        tbody.innerHTML = `<tr><td colspan="8" class="empty-row">No active positions. Scanning markets...</td></tr>`;
        return;
    }

    tbody.innerHTML = '';
    positions.forEach(t => {
        const row = document.createElement('tr');
        const direction = (t.direction || t.side || 'LONG').toUpperCase();
        const sideClass = direction === 'LONG' ? 'badge-side side-long' : 'badge-side side-short';
        
        const upnl = parseFloat(t.unrealized_pnl || 0);
        const rpnl = parseFloat(t.realized_pnl || 0);
        const totalPnl = upnl + rpnl;
        const pnlClass = totalPnl >= 0 ? 'text-pnl-positive' : 'text-pnl-negative';

        const entry = parseFloat(t.entry_price || t.entry || 0);
        const current = parseFloat(t.current_price || entry);
        
        row.innerHTML = `
            <td><strong>${t.symbol}</strong></td>
            <td><span class="${sideClass}">${direction}</span></td>
            <td class="mono">${entry.toFixed(4)}</td>
            <td class="mono">${current.toFixed(4)}</td>
            <td class="${pnlClass} mono">${totalPnl >= 0 ? '+' : ''}${totalPnl.toFixed(2)} USD</td>
            <td>
                <div style="font-size: 10px; color: var(--text-secondary)">
                    SL: <span class="mono">${parseFloat(t.stop_loss || t.sl || 0).toFixed(4)}</span><br>
                    TP1: <span class="mono">${parseFloat(t.tp1 || 0).toFixed(4)}</span>
                </div>
            </td>
            <td>
                <div style="font-size: 10px; color: var(--text-secondary)">
                    Size: <span class="mono">${parseFloat(t.qty || 0).toFixed(3)}</span><br>
                    Lev: <span class="mono">${t.leverage || 10}x</span>
                </div>
            </td>
            <td>
                <span class="status-pill">
                    <span class="badge-dot ${t.status === 'open' ? 'dot-green' : 'dot-gold'}"></span>
                    ${(t.status || 'open').toUpperCase()}
                </span>
            </td>
        `;
        tbody.appendChild(row);
    });
}

async function fetchHistory() {
    try {
        const resp = await fetch('/api/history?limit=10');
        if (!resp.ok) return;
        const json = await resp.json();
        if (json.ok && json.data) {
            const tbody = document.getElementById('closedTradesBody');
            const data = json.data;
            if (data.length === 0) {
                tbody.innerHTML = `<tr><td colspan="8" class="empty-row">No trade history found.</td></tr>`;
                return;
            }

            tbody.innerHTML = '';
            data.forEach(t => {
                const row = document.createElement('tr');
                const pnl = parseFloat(t.net_pnl || 0);
                const pnlClass = pnl >= 0 ? 'text-pnl-positive' : 'text-pnl-negative';
                const direction = (t.direction || t.side || 'LONG').toUpperCase();
                const sideClass = direction === 'LONG' ? 'badge-side side-long' : 'badge-side side-short';

                row.innerHTML = `
                    <td><strong>${t.symbol}</strong></td>
                    <td><span class="${sideClass}">${direction}</span></td>
                    <td class="mono">${parseFloat(t.entry || t.entry_price || 0).toFixed(4)}</td>
                    <td class="mono">${parseFloat(t.close_price || 0).toFixed(4)}</td>
                    <td class="${pnlClass} mono">${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)} USD</td>
                    <td class="mono">${t.duration_str || ''}</td>
                    <td><span style="font-weight: 500">${(t.close_reason || 'Manual').toUpperCase()}</span></td>
                    <td style="color: var(--text-secondary)">${formatTime(t.close_time)}</td>
                `;
                tbody.appendChild(row);
            });
        }
    } catch(e) {}
}

async function fetchLogs() {
    try {
        const resp = await fetch('/api/logs?n=50');
        if (!resp.ok) return;
        const json = await resp.json();
        if (json.ok && json.data && json.data.items) {
            const terminal = document.getElementById('logsTerminal');
            terminal.innerHTML = '';
            
            json.data.items.forEach(log => {
                const line = document.createElement('div');
                let cl = 'log-line';
                if (log.level === 'ERROR') cl += ' text-error';
                else if (log.level === 'WARNING') cl += ' text-warning';
                else if (log.level === 'TRADE') cl += ' text-success';
                else if (log.level === 'CLOSE') cl += ' text-muted';
                else cl += ' text-info';
                
                line.className = cl;
                line.textContent = log.text;
                terminal.appendChild(line);
            });
            terminal.scrollTop = terminal.scrollHeight;
        }
    } catch(e) {}
}

function addConsoleLog(text, level = 'info') {
    const terminal = document.getElementById('logsTerminal');
    const line = document.createElement('div');
    line.className = `log-line text-${level}`;
    const now = new Date();
    const timeStr = now.toLocaleTimeString('en-US', { hour12: false });
    line.innerHTML = `<span class="time">[${timeStr}]</span> ${text}`;
    terminal.appendChild(line);
    terminal.scrollTop = terminal.scrollHeight;
}

// ----------------------------------------------------
// ⚡ SOCKET.IO REALTIME EVENTS
// ----------------------------------------------------
function connectWebSocket() {
    try {
        const socket = io(window.location.origin, {
            reconnection: true,
            reconnectionDelay: 3000,
            reconnectionAttempts: 5,
            transports: ['websocket', 'polling']
        });

        socket.on('connect', () => {
            console.log('✓ Socket.IO Connected');
            addConsoleLog('WebSocket Live Stream Connected', 'success');
            socket.emit('dashboard_ready');
        });

        socket.on('disconnect', (reason) => {
            console.warn('✗ Socket.IO Disconnected:', reason);
            addConsoleLog('WebSocket disconnected, attempting reconnect...', 'warning');
        });

        socket.on('live_update', (data) => {
            if (data.positions) {
                renderActiveTrades(data.positions);
            }
        });

        socket.on('pnl_update', (data) => {
            if (data.balance !== undefined) {
                document.getElementById('walletBalance').textContent = formatUSD(data.balance);
            }
            if (data.realized_pnl !== undefined) {
                const daily = data.realized_pnl;
                const el = document.getElementById('dailyPnl');
                el.textContent = `${daily >= 0 ? '+' : ''}${formatUSD(daily)}`;
                el.className = daily >= 0 ? 'stat-value text-pnl-positive' : 'stat-value text-pnl-negative';
            }
        });

        socket.on('trade_closed', (data) => {
            addConsoleLog(`Trade Closed: ${data.symbol} ${data.direction} ${data.pnl >= 0 ? '+' : ''}${data.pnl.toFixed(2)} USD`, data.pnl >= 0 ? 'success' : 'error');
            fetchHistory();
            initEquityChart();
        });

        socket.on('signal_generated', (data) => {
            addConsoleLog(`New Signal Generated: ${data.symbol} ${data.direction} [Score: ${data.score}]`, 'info');
            fetchStatsAndFunnel();
        });

        socket.on('dashboard_refresh', () => {
            fetchStatsAndFunnel();
            fetchHistory();
            initEquityChart();
        });

    } catch (e) {
        console.error("SocketIO connection error:", e);
    }
}

// ----------------------------------------------------
// 🚀 INITIALIZATION
// ----------------------------------------------------
document.addEventListener('DOMContentLoaded', () => {
    updateClock();
    setInterval(updateClock, 1000);
    
    // Bind Controls
    bindControlElements();

    // Initial Fetch
    fetchConfigParams();
    fetchStatsAndFunnel();
    fetchHistory();
    fetchLogs();
    initEquityChart();

    // Setup WebSockets
    connectWebSocket();

    // Polling backup (refresh logs and active stats every 8 seconds)
    setInterval(() => {
        fetchStatsAndFunnel();
        fetchLogs();
    }, 8000);
});
