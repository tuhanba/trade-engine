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
        const ddData = [];
        
        const points = data.points || [];
        points.forEach(pt => {
            labels.push(pt.day || '');
            pnlData.push(pt.balance || 0);
            ddData.push(pt.drawdown || 0);
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

        // Gradient configurations
        const gradient = ctx.createLinearGradient(0, 0, 0, 180);
        gradient.addColorStop(0, 'rgba(212, 168, 67, 0.12)');
        gradient.addColorStop(1, 'rgba(212, 168, 67, 0.00)');

        const gradient_dd = ctx.createLinearGradient(0, 0, 0, 180);
        gradient_dd.addColorStop(0, 'rgba(239, 68, 68, 0.08)');
        gradient_dd.addColorStop(1, 'rgba(239, 68, 68, 0.00)');

        state.chart = new Chart(ctx, {
            type: 'line',
            data: {
                labels: labels.length ? labels : ['Starting'],
                datasets: [
                    {
                        label: 'USDT Balance',
                        data: pnlData.length ? pnlData : [data.initial_balance || 2000.0],
                        borderColor: '#d4a843',
                        borderWidth: 2,
                        backgroundColor: gradient,
                        fill: true,
                        tension: 0.35,
                        pointRadius: labels.length > 15 ? 0 : 3,
                        pointBackgroundColor: '#d4a843',
                        pointHoverRadius: 5,
                        yAxisID: 'y'
                    },
                    {
                        label: 'Drawdown %',
                        data: ddData.length ? ddData : [0.0],
                        borderColor: '#ef4444',
                        borderWidth: 1.5,
                        backgroundColor: gradient_dd,
                        fill: true,
                        tension: 0.35,
                        pointRadius: 0,
                        yAxisID: 'yDrawdown'
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: true, labels: { color: '#e5e7eb', font: { size: 9 } } }
                },
                scales: {
                    x: {
                        grid: { color: 'rgba(255, 255, 255, 0.02)' },
                        ticks: { color: '#6b7280', font: { size: 9 } }
                    },
                    y: {
                        type: 'linear',
                        position: 'left',
                        grid: { color: 'rgba(255, 255, 255, 0.02)' },
                        ticks: { color: '#6b7280', font: { size: 9, family: 'JetBrains Mono' } }
                    },
                    yDrawdown: {
                        type: 'linear',
                        position: 'right',
                        min: 0,
                        max: 15,
                        grid: { drawOnChartArea: false },
                        ticks: { 
                            color: '#ef4444', 
                            font: { size: 9, family: 'JetBrains Mono' },
                            callback: function(value) { return value + '%'; }
                        }
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
    } else if (key === 'MTF_TREND_ALIGN_ENABLED' || key === 'mtf_trend_align_enabled') {
        const isEnabled = value === true || value === 'True' || value === '1' || value === 1;
        document.getElementById('btnMtfFalse').className = isEnabled ? 'toggle-btn' : 'toggle-btn active';
        document.getElementById('btnMtfTrue').className = isEnabled ? 'toggle-btn active' : 'toggle-btn';
    } else if (key === 'EQUITY_CURVE_FILTER_ENABLED' || key === 'equity_curve_filter_enabled') {
        const isEnabled = value === true || value === 'True' || value === '1' || value === 1;
        document.getElementById('btnEquityCurveFalse').className = isEnabled ? 'toggle-btn' : 'toggle-btn active';
        document.getElementById('btnEquityCurveTrue').className = isEnabled ? 'toggle-btn active' : 'toggle-btn';
    } else if (key === 'AUTO_COMPOUNDING' || key === 'auto_compounding') {
        const isEnabled = value === true || value === 'True' || value === '1' || value === 1;
        document.getElementById('btnCompoundingFalse').className = isEnabled ? 'toggle-btn' : 'toggle-btn active';
        document.getElementById('btnCompoundingTrue').className = isEnabled ? 'toggle-btn active' : 'toggle-btn';
    } else if (key === 'DRAWDOWN_DEFENSIVE_PCT' || key === 'drawdown_defensive_pct') {
        document.getElementById('inputDrawdownDefensive').value = parseFloat(value).toFixed(1);
    } else if (key === 'DRAWDOWN_LOCK_PCT' || key === 'drawdown_lock_pct') {
        document.getElementById('inputDrawdownLock').value = parseFloat(value).toFixed(1);
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
            addConsoleLog(`[Config] Successfully updated ${key} to ${value}`, 'success');
        } else {
            addConsoleLog(`[Error] Failed to update config: ${res.error}`, 'error');
        }
    } catch(e) {
        console.error("Config update error:", e);
    }
}

async function postSettingsUpdate(key, value) {
    try {
        const resp = await fetch('/api/settings/update', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key, value })
        });
        const res = await resp.json();
        if (res.ok) {
            updateConfigUI(key, value);
            addConsoleLog(`[Settings] Successfully updated ${key} to ${value}`, 'success');
        } else {
            addConsoleLog(`[Error] Failed to update settings: ${res.error}`, 'error');
        }
    } catch(e) {
        console.error("Settings update error:", e);
    }
}

function bindControlElements() {
    // Mode Switch
    document.getElementById('btnModePaper').addEventListener('click', () => postConfigUpdate('tg_execution_mode', 'paper'));
    document.getElementById('btnModeLive').addEventListener('click', () => postConfigUpdate('tg_execution_mode', 'live'));
    
    // Human Mode Switch
    document.getElementById('btnHumanFalse').addEventListener('click', () => postConfigUpdate('tg_human_mode', false));
    document.getElementById('btnHumanTrue').addEventListener('click', () => postConfigUpdate('tg_human_mode', true));

    // MTF Trend Alignment
    document.getElementById('btnMtfFalse').addEventListener('click', () => postSettingsUpdate('MTF_TREND_ALIGN_ENABLED', false));
    document.getElementById('btnMtfTrue').addEventListener('click', () => postSettingsUpdate('MTF_TREND_ALIGN_ENABLED', true));

    // Equity Curve Filter
    document.getElementById('btnEquityCurveFalse').addEventListener('click', () => postSettingsUpdate('EQUITY_CURVE_FILTER_ENABLED', false));
    document.getElementById('btnEquityCurveTrue').addEventListener('click', () => postSettingsUpdate('EQUITY_CURVE_FILTER_ENABLED', true));

    // Auto Compounding
    document.getElementById('btnCompoundingFalse').addEventListener('click', () => postSettingsUpdate('AUTO_COMPOUNDING', false));
    document.getElementById('btnCompoundingTrue').addEventListener('click', () => postSettingsUpdate('AUTO_COMPOUNDING', true));

    // Drawdown inputs
    document.getElementById('inputDrawdownDefensive').addEventListener('change', (e) => {
        postSettingsUpdate('DRAWDOWN_DEFENSIVE_PCT', parseFloat(e.target.value));
    });
    document.getElementById('inputDrawdownLock').addEventListener('change', (e) => {
        postSettingsUpdate('DRAWDOWN_LOCK_PCT', parseFloat(e.target.value));
    });

    // System Maintenance
    const btnMaint = document.getElementById('btnMaintenance');
    if (btnMaint) {
        btnMaint.addEventListener('click', async () => {
            if (!confirm("Sistem bakımı çalıştırılsın mı? (Veritabanı optimize edilecek ve Redis temizlenecek)")) {
                return;
            }
            btnMaint.disabled = true;
            btnMaint.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Running Maintenance...`;
            try {
                const resp = await fetch('/api/system/maintenance', { method: 'POST' });
                const res = await resp.json();
                if (res.ok) {
                    addConsoleLog(res.message, 'success');
                    alert(res.message);
                } else {
                    addConsoleLog(`[Error] Maintenance failed: ${res.error}`, 'error');
                    alert(`Bakım hatası: ${res.error}`);
                }
            } catch(e) {
                console.error("Maintenance error:", e);
                addConsoleLog(`[Error] Network error during maintenance`, 'error');
            } finally {
                btnMaint.disabled = false;
                btnMaint.innerHTML = `<i class="fa-solid fa-screwdriver-wrench"></i> Run System Maintenance`;
            }
        });
    }

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

    // Backtest Run Button
    const btnRunBt = document.getElementById('btnRunBacktest');
    if (btnRunBt) {
        btnRunBt.addEventListener('click', runBacktest);
    }
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
            updateConfigUI('mtf_trend_align_enabled', p.mtf_trend_align_enabled);
            updateConfigUI('equity_curve_filter_enabled', p.equity_curve_filter_enabled);
            updateConfigUI('auto_compounding', p.auto_compounding);
            updateConfigUI('drawdown_defensive_pct', p.drawdown_defensive_pct);
            updateConfigUI('drawdown_lock_pct', p.drawdown_lock_pct);
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

function addExecutionMonitorLog(text, level = 'info') {
    const terminal = document.getElementById('executionMonitor');
    if (!terminal) return;
    const line = document.createElement('div');
    let cl = 'log-line';
    if (level === 'error') cl += ' text-error';
    else if (level === 'warning') cl += ' text-warning';
    else if (level === 'success') cl += ' text-success';
    else cl += ' text-info';
    
    line.className = cl;
    const now = new Date();
    const timeStr = now.toLocaleTimeString('en-US', { hour12: false });
    line.innerHTML = `<span class="time">[${timeStr}]</span> ${text}`;
    terminal.appendChild(line);
    terminal.scrollTop = terminal.scrollHeight;
    
    while (terminal.childElementCount > 100) {
        terminal.removeChild(terminal.firstChild);
    }
}

// ----------------------------------------------------
// ⚡ SOCKET.IO REALTIME EVENTS
// ----------------------------------------------------
function connectWebSocket() {
    try {
        const pin = localStorage.getItem("dashboard_pin") || "";
        const socket = io(window.location.origin, {
            reconnection: true,
            reconnectionDelay: 3000,
            reconnectionAttempts: 5,
            transports: ['websocket', 'polling'],
            query: { pin: pin }
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
            fetchHeatmap();
        });

        socket.on('signal_generated', (data) => {
            addConsoleLog(`New Signal Generated: ${data.symbol} ${data.direction} [Score: ${data.score}]`, 'info');
            fetchStatsAndFunnel();
        });

        socket.on('dashboard_refresh', () => {
            fetchStatsAndFunnel();
            fetchHistory();
            initEquityChart();
            fetchHeatmap();
        });

        socket.on('trailing_stop_updated', (data) => {
            const pctChange = ((data.new_sl - data.old_sl) / data.old_sl * 100);
            const directionSign = pctChange >= 0 ? '▲' : '▼';
            const msg = `🚨 <strong>[SL Update]</strong> ${data.symbol} (#${data.trade_id}): Trailing SL moved from <span class="mono">${data.old_sl.toFixed(4)}</span> to <span class="mono">${data.new_sl.toFixed(4)}</span> (${directionSign} ${pctChange.toFixed(2)}%) | Price: <span class="mono">${data.current_price.toFixed(4)}</span>`;
            addExecutionMonitorLog(msg, pctChange >= 0 ? 'success' : 'warning');
        });

        socket.on('limit_chase_progress', (data) => {
            const pctFilled = data.total_qty > 0 ? (data.filled_qty / data.total_qty * 100) : 0;
            let level = 'info';
            let statusEmoji = '⚡';
            if (data.status === 'COMPLETED') { level = 'success'; statusEmoji = '✓'; }
            else if (data.status === 'MARKET_FALLBACK') { level = 'warning'; statusEmoji = '🔀'; }
            else if (data.status === 'FAILED') { level = 'error'; statusEmoji = '✗'; }
            
            const msg = `${statusEmoji} <strong>[Limit Chase]</strong> ${data.side} ${data.symbol}: ${data.status} | Filled: <span class="mono">${data.filled_qty.toFixed(4)}/${data.total_qty.toFixed(4)}</span> (${pctFilled.toFixed(1)}%) @ <span class="mono">${data.price.toFixed(4)}</span>`;
            addExecutionMonitorLog(msg, level);
        });

        socket.on('agent_votes', (data) => {
            let level = 'info';
            if (data.decision === 'VETO') level = 'error';
            else if (data.decision === 'WATCH') level = 'warning';
            else if (data.decision === 'ALLOW') level = 'success';
            
            let agentDetails = [];
            for (const [agent, voteInfo] of Object.entries(data.votes)) {
                const colorClass = voteInfo.vote === 'VETO' ? 'text-error' : (voteInfo.vote === 'WATCH' ? 'text-warning' : 'text-success');
                agentDetails.push(`${agent}: <span class="${colorClass}">${voteInfo.vote}</span> (${voteInfo.score.toFixed(0)})`);
            }
            
            const msg = `🧠 <strong>[AI Consensus]</strong> ${data.symbol} ${data.direction} → <strong style="text-transform: uppercase;">${data.decision}</strong> (Score: ${data.adjusted_score.toFixed(1)})<br>&nbsp;&nbsp;&nbsp;&nbsp;↳ [${agentDetails.join(' | ')}]`;
            addExecutionMonitorLog(msg, level);
        });

    } catch (e) {
        console.error("SocketIO connection error:", e);
    }
}

// ----------------------------------------------------
// 🗂 TAB NAVIGATION & GHOST STATS
// ----------------------------------------------------
function switchTab(tabId) {
    document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
    
    const targetContent = document.getElementById(`tab-${tabId}`);
    if (targetContent) targetContent.classList.add('active');
    
    const btn = Array.from(document.querySelectorAll('.tab-btn')).find(b => {
        const attr = b.getAttribute('onclick');
        return attr && attr.includes(tabId);
    });
    if (btn) btn.classList.add('active');
    
    addConsoleLog(`Switched tab to ${tabId.toUpperCase()}`, 'info');
}
window.switchTab = switchTab;

async function fetchGhostStats() {
    try {
        const resp = await fetch('/api/ghost-stats');
        if (!resp.ok) return;
        const json = await resp.json();
        if (json.ok) {
            document.getElementById('ghostSimTotal').textContent = json.total || 0;
            document.getElementById('ghostWinRate').textContent = (json.ghost_win_rate || 0).toFixed(1) + '%';
            document.getElementById('ghostPnl').textContent = (json.ghost_pnl || 0).toFixed(2) + ' R';

            const patternsBody = document.getElementById('ghostPatternsBody');
            const patterns = json.top_patterns || [];
            if (patterns.length === 0) {
                patternsBody.innerHTML = `<li class="empty-pattern">No ghost insights gathered yet.</li>`;
                return;
            }

            patternsBody.innerHTML = '';
            patterns.forEach(p => {
                const li = document.createElement('li');
                li.className = 'ghost-pattern-item';
                li.innerHTML = `
                    <span class="ghost-pattern-name">${p.pattern}</span>
                    <span class="ghost-pattern-stats">${p.win_rate}% WR | ${p.avg_r >= 0 ? '+' : ''}${p.avg_r.toFixed(2)}R avg</span>
                `;
                patternsBody.appendChild(li);
            });
        }
    } catch(e) {
        console.error("Ghost stats fetch error:", e);
    }
}

// ----------------------------------------------------
// 🌡️ PORTFOLIO PNL HEATMAP
// ----------------------------------------------------
async function fetchHeatmap() {
    try {
        const resp = await fetch('/api/pnl_heatmap');
        if (!resp.ok) return;
        const res = await resp.json();
        if (!res.ok || !res.data) return;

        const container = document.getElementById('pnlHeatmap');
        if (!container) return;

        const data = res.data;
        
        // 1. Gather all unique coins and compile hourly PnL map
        const coinMap = {};
        data.forEach(item => {
            const sym = item.symbol;
            const hour = parseInt(item.hour);
            const pnl = parseFloat(item.total_pnl || 0);
            const count = parseInt(item.trade_count || 0);
            
            if (!coinMap[sym]) {
                coinMap[sym] = Array.from({ length: 24 }, () => ({ pnl: 0, count: 0 }));
            }
            coinMap[sym][hour] = { pnl, count };
        });

        const symbols = Object.keys(coinMap).sort();

        if (symbols.length === 0) {
            container.innerHTML = `<div class="empty-row" style="grid-column: span 25; text-align: center; width: 100%;">No closed trade history in the last 30 days.</div>`;
            return;
        }

        container.innerHTML = '';

        // 2. Render Header Row (Symbol + Hours 00 to 23)
        const headerRow = document.createElement('div');
        headerRow.className = 'heatmap-header-row';
        
        const symHeader = document.createElement('div');
        symHeader.className = 'heatmap-header-cell';
        symHeader.textContent = 'Symbol';
        headerRow.appendChild(symHeader);

        for (let h = 0; h < 24; h++) {
            const hourCell = document.createElement('div');
            hourCell.className = 'heatmap-header-cell';
            hourCell.textContent = h.toString().padStart(2, '0');
            headerRow.appendChild(hourCell);
        }
        container.appendChild(headerRow);

        // 3. Find max PnL for scaling cell intensity
        let maxVal = 0.01;
        data.forEach(item => {
            const absPnl = Math.abs(parseFloat(item.total_pnl || 0));
            if (absPnl > maxVal) maxVal = absPnl;
        });

        // 4. Render Coin Rows
        symbols.forEach(sym => {
            const row = document.createElement('div');
            row.className = 'heatmap-coin-row';

            const nameCell = document.createElement('div');
            nameCell.className = 'heatmap-coin-name';
            nameCell.textContent = sym.replace('USDT', '');
            nameCell.title = sym;
            row.appendChild(nameCell);

            const hoursData = coinMap[sym];
            for (let h = 0; h < 24; h++) {
                const cellData = hoursData[h];
                const cell = document.createElement('div');
                cell.className = 'heatmap-cell';

                const pnl = cellData.pnl;
                const count = cellData.count;

                if (count > 0) {
                    const ratio = Math.abs(pnl) / maxVal;
                    let intensity = 1;
                    if (ratio > 0.75) intensity = 4;
                    else if (ratio > 0.45) intensity = 3;
                    else if (ratio > 0.15) intensity = 2;

                    const prefix = pnl >= 0 ? 'positive' : 'negative';
                    cell.classList.add(`${prefix}-${intensity}`);

                    const tooltip = document.createElement('div');
                    tooltip.className = 'heatmap-tooltip';
                    tooltip.innerHTML = `
                        <strong>${sym}</strong> @ ${h.toString().padStart(2, '0')}:00 UTC<br>
                        Net PnL: <strong>${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}</strong><br>
                        Total Trades: <strong>${count}</strong>
                    `;
                    cell.appendChild(tooltip);
                }

                row.appendChild(cell);
            }
            container.appendChild(row);
        });

    } catch(e) {
        console.error("Heatmap load error:", e);
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
    fetchGhostStats();
    fetchHeatmap();
    checkActiveBacktest();
    fetchMlStatus();

    // Setup WebSockets
    connectWebSocket();

    // Polling backup (refresh logs and active stats every 8 seconds)
    setInterval(() => {
        fetchStatsAndFunnel();
        fetchLogs();
        fetchGhostStats();
        fetchHeatmap();
        fetchMlStatus();
    }, 8000);
});

// ----------------------------------------------------
// 🧪 HISTORICAL BACKTEST MANAGEMENT
// ----------------------------------------------------
let backtestPollInterval = null;

async function runBacktest() {
    const btn = document.getElementById('btnRunBacktest');
    const symbols = document.getElementById('btSymbols').value.trim();
    const days = parseInt(document.getElementById('btDays').value) || 3;
    const balance = parseFloat(document.getElementById('btBalance').value) || 2000;
    const offline = document.getElementById('btOffline').checked;

    if (!symbols) {
        alert("Please enter at least one symbol.");
        return;
    }

    btn.disabled = true;
    btn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Initializing...`;
    
    const progressCard = document.getElementById('btProgressCard');
    const progressBar = document.getElementById('btProgressBar');
    const progressPct = document.getElementById('btProgressPct');
    const progressStatus = document.getElementById('btProgressStatus');
    const resultsContainer = document.getElementById('btResultsContainer');

    progressCard.style.display = 'block';
    progressBar.style.width = '0%';
    progressPct.textContent = '0%';
    progressStatus.textContent = 'Starting backtest thread...';
    
    resultsContainer.innerHTML = `
        <div style="display: flex; justify-content: center; align-items: center; height: 100%; color: #888888; flex-direction: column; gap: 12px; min-height: 200px;">
            <i class="fa-solid fa-spinner fa-spin" style="font-size: 40px; color: var(--color-gold);"></i>
            <span>Simulating trade environment...</span>
        </div>
    `;

    try {
        const resp = await fetch('/api/backtest/run', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ symbols, days, balance, offline })
        });
        const data = await resp.json();
        
        if (!data.ok) {
            btn.disabled = false;
            btn.innerHTML = `<i class="fa-solid fa-play"></i> Run Historical Backtest`;
            progressCard.style.display = 'none';
            alert(`Error: ${data.error}`);
            addConsoleLog(`[Backtest] Run error: ${data.error}`, 'error');
            return;
        }

        addConsoleLog(`[Backtest] Thread launched for ${symbols}. Days: ${days}. Initial Balance: $${balance}`, 'info');

        if (backtestPollInterval) clearInterval(backtestPollInterval);
        backtestPollInterval = setInterval(pollBacktestStatus, 1000);
        
    } catch (e) {
        console.error("Backtest run error:", e);
        btn.disabled = false;
        btn.innerHTML = `<i class="fa-solid fa-play"></i> Run Historical Backtest`;
        progressCard.style.display = 'none';
        alert("Failed to start backtest. See console.");
    }
}

async function pollBacktestStatus() {
    try {
        const resp = await fetch('/api/backtest/status');
        if (!resp.ok) return;
        const data = await resp.json();
        if (!data.ok || !data.state) return;

        const state = data.state;
        const progressBar = document.getElementById('btProgressBar');
        const progressPct = document.getElementById('btProgressPct');
        const progressStatus = document.getElementById('btProgressStatus');
        const btn = document.getElementById('btnRunBacktest');

        const prog = parseFloat(state.progress) || 0;
        progressBar.style.width = `${prog}%`;
        progressPct.textContent = `${prog.toFixed(1)}%`;
        
        if (state.status === 'running') {
            progressStatus.textContent = `Processing candles... ${prog.toFixed(1)}%`;
        } else if (state.status === 'completed') {
            clearInterval(backtestPollInterval);
            backtestPollInterval = null;
            
            progressStatus.textContent = 'Backtest completed! Generating report...';
            document.getElementById('btProgressCard').style.display = 'none';
            btn.disabled = false;
            btn.innerHTML = `<i class="fa-solid fa-play"></i> Run Historical Backtest`;
            
            addConsoleLog(`[Backtest] Completed successfully!`, 'success');
            renderBacktestResults(state.results);
        } else if (state.status === 'failed') {
            clearInterval(backtestPollInterval);
            backtestPollInterval = null;
            
            progressStatus.textContent = `Failed: ${state.error}`;
            btn.disabled = false;
            btn.innerHTML = `<i class="fa-solid fa-play"></i> Run Historical Backtest`;
            
            addConsoleLog(`[Backtest] Simulation failed: ${state.error}`, 'error');
            
            document.getElementById('btResultsContainer').innerHTML = `
                <div style="display: flex; justify-content: center; align-items: center; height: 100%; color: var(--color-red); flex-direction: column; gap: 12px; min-height: 200px; padding: 20px;">
                    <i class="fa-solid fa-circle-xmark" style="font-size: 40px;"></i>
                    <strong style="font-size: 14px;">Simulation Failed</strong>
                    <span style="font-size: 12px; text-align: center; color: var(--text-secondary); max-width: 400px; word-break: break-word;">${state.error}</span>
                </div>
            `;
        }
    } catch (e) {
        console.error("Backtest poll error:", e);
    }
}

async function checkActiveBacktest() {
    try {
        const resp = await fetch('/api/backtest/status');
        if (!resp.ok) return;
        const data = await resp.json();
        if (data.ok && data.state) {
            if (data.state.status === 'running') {
                document.getElementById('btProgressCard').style.display = 'block';
                document.getElementById('btnRunBacktest').disabled = true;
                document.getElementById('btnRunBacktest').innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Simulating...`;
                addConsoleLog(`[Backtest] Found active background backtest. Resuming poll...`, 'info');
                if (backtestPollInterval) clearInterval(backtestPollInterval);
                backtestPollInterval = setInterval(pollBacktestStatus, 1000);
            } else if (data.state.status === 'completed' && data.state.results) {
                renderBacktestResults(data.state.results);
            }
        }
    } catch(e) {
        console.error("Failed to check active backtest:", e);
    }
}

function renderBacktestResults(results) {
    const container = document.getElementById('btResultsContainer');
    if (!container) return;

    if (!results || results.total_trades === undefined) {
        container.innerHTML = `
            <div style="display: flex; justify-content: center; align-items: center; height: 100%; color: #888888; flex-direction: column; gap: 12px;">
                <i class="fa-solid fa-triangle-exclamation" style="font-size: 40px; color: var(--color-gold);"></i>
                <span>Invalid or empty simulation results.</span>
            </div>
        `;
        return;
    }

    const netProfitClass = results.net_profit >= 0 ? 'text-pnl-positive' : 'text-pnl-negative';
    const profitSign = results.net_profit >= 0 ? '+' : '';
    const roiSign = results.roi >= 0 ? '+' : '';

    // 1. Grid of metrics
    let html = `
        <div class="stats-grid" style="grid-template-columns: repeat(3, 1fr); gap: 12px; margin-bottom: 16px;">
            <div class="stat-card glass-card">
                <div class="stat-icon" style="color: var(--color-gold); border-color: rgba(212,168,67,0.2);"><i class="fa-solid fa-wallet"></i></div>
                <div class="stat-info">
                    <span class="stat-label">Final Balance</span>
                    <span class="stat-value font-mono">$${results.final_balance.toFixed(2)}</span>
                </div>
            </div>
            <div class="stat-card glass-card">
                <div class="stat-icon" style="color: ${results.net_profit >= 0 ? 'var(--color-green)' : 'var(--color-red)'}; border-color: ${results.net_profit >= 0 ? 'rgba(16,185,129,0.2)' : 'rgba(239,68,68,0.2)'};">
                    <i class="fa-solid ${results.net_profit >= 0 ? 'fa-arrow-trend-up' : 'fa-arrow-trend-down'}"></i>
                </div>
                <div class="stat-info">
                    <span class="stat-label">Net Profit / ROI</span>
                    <span class="stat-value ${netProfitClass}">${profitSign}$${results.net_profit.toFixed(2)} (${roiSign}${results.roi.toFixed(2)}%)</span>
                </div>
            </div>
            <div class="stat-card glass-card">
                <div class="stat-icon" style="color: var(--color-blue); border-color: rgba(59,130,246,0.2);"><i class="fa-solid fa-percentage"></i></div>
                <div class="stat-info">
                    <span class="stat-label">Win Rate (W / L)</span>
                    <span class="stat-value">${results.win_rate.toFixed(1)}% <span style="font-size: 11px; color: var(--text-secondary); font-weight: normal;">(${results.win_count}W / ${results.loss_count}L)</span></span>
                </div>
            </div>
        </div>

        <div class="stats-grid" style="grid-template-columns: repeat(3, 1fr); gap: 12px; margin-bottom: 16px;">
            <div class="stat-card glass-card">
                <div class="stat-icon" style="color: var(--color-purple); border-color: rgba(139,92,246,0.2);"><i class="fa-solid fa-shuffle"></i></div>
                <div class="stat-info">
                    <span class="stat-label">Total Trades</span>
                    <span class="stat-value">${results.total_trades}</span>
                </div>
            </div>
            <div class="stat-card glass-card">
                <div class="stat-icon" style="color: var(--color-gold); border-color: rgba(212,168,67,0.2);"><i class="fa-solid fa-scale-balanced"></i></div>
                <div class="stat-info">
                    <span class="stat-label">Profit Factor</span>
                    <span class="stat-value">${results.profit_factor.toFixed(2)}</span>
                </div>
            </div>
            <div class="stat-card glass-card">
                <div class="stat-icon" style="color: var(--color-red); border-color: rgba(239,68,68,0.2);"><i class="fa-solid fa-chart-line-down"></i></div>
                <div class="stat-info">
                    <span class="stat-label">Max Drawdown</span>
                    <span class="stat-value text-pnl-negative">${results.max_dd_pct.toFixed(1)}%</span>
                </div>
            </div>
        </div>
    `;

    // Monte Carlo Risk Metrics (if available)
    if (results.monte_carlo) {
        const mc = results.monte_carlo;
        const ruinColor = mc.prob_ruin_pct > 5 ? 'var(--color-red)' : (mc.prob_ruin_pct > 0 ? 'var(--color-gold)' : 'var(--color-green)');
        html += `
            <div class="glass-card" style="padding: 16px; margin-bottom: 16px; display: flex; flex-direction: column; gap: 12px;">
                <div class="card-header" style="padding: 0 0 10px 0;">
                    <h3><i class="fa-solid fa-calculator"></i> Monte Carlo Risk Simulation (1,000 Shuffled Runs)</h3>
                </div>
                <div class="stats-grid" style="grid-template-columns: repeat(4, 1fr); gap: 12px; margin-top: 4px;">
                    <div style="background: rgba(255,255,255,0.01); border: 1px solid var(--border-glass); border-radius: 8px; padding: 10px; text-align: center;">
                        <div style="font-size: 8px; color: var(--text-secondary); text-transform: uppercase; margin-bottom: 4px;">Probability of Ruin</div>
                        <span class="badge-side" style="background: rgba(255,255,255,0.03); color: ${ruinColor}; border: 1px solid ${ruinColor}; font-size: 11px; font-weight: bold;">${mc.prob_ruin_pct.toFixed(1)}%</span>
                    </div>
                    <div style="background: rgba(255,255,255,0.01); border: 1px solid var(--border-glass); border-radius: 8px; padding: 10px; text-align: center;">
                        <div style="font-size: 8px; color: var(--text-secondary); text-transform: uppercase; margin-bottom: 4px;">Expected Avg Balance</div>
                        <div style="font-size: 13px; font-weight: bold; color: var(--text-primary);" class="font-mono">$${mc.avg_ending_balance.toFixed(2)}</div>
                    </div>
                    <div style="background: rgba(255,255,255,0.01); border: 1px solid var(--border-glass); border-radius: 8px; padding: 10px; text-align: center;">
                        <div style="font-size: 8px; color: var(--text-secondary); text-transform: uppercase; margin-bottom: 4px;">95% VaR Drawdown</div>
                        <div style="font-size: 13px; font-weight: bold; color: var(--color-gold);" class="font-mono">${mc.ninety_five_var_dd_pct.toFixed(1)}%</div>
                    </div>
                    <div style="background: rgba(255,255,255,0.01); border: 1px solid var(--border-glass); border-radius: 8px; padding: 10px; text-align: center;">
                        <div style="font-size: 8px; color: var(--text-secondary); text-transform: uppercase; margin-bottom: 4px;">Worst Case Drawdown</div>
                        <div style="font-size: 13px; font-weight: bold; color: var(--color-red);" class="font-mono">${mc.worst_case_dd_pct.toFixed(1)}%</div>
                    </div>
                </div>
            </div>
        `;
    }

    const fs = results.funnel_stats || {};
    const er = results.exit_reasons || {};
    
    html += `
        <div style="display: flex; gap: 16px; margin-bottom: 16px;">
            <!-- Rejection Funnel -->
            <div class="glass-card" style="flex: 1; padding: 16px; display: flex; flex-direction: column; gap: 12px;">
                <div class="card-header" style="padding: 0 0 10px 0;">
                    <h3><i class="fa-solid fa-filter"></i> Rejection Funnel</h3>
                </div>
                <table class="premium-table" style="font-size: 11px;">
                    <thead>
                        <tr>
                            <th>Funnel Step</th>
                            <th style="text-align: right;">Passed</th>
                            <th style="text-align: right;">Filtered</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr>
                            <td><strong>Total Scanned</strong></td>
                            <td style="text-align: right; font-weight: bold; color: var(--color-blue);">${fs.scanned || 0}</td>
                            <td style="text-align: right; color: var(--text-muted);">-</td>
                        </tr>
                        <tr>
                            <td>Trend Filter</td>
                            <td style="text-align: right; color: var(--color-green);">${fs.trend_ok || 0}</td>
                            <td style="text-align: right; color: var(--color-red);">${fs.trend_fail || 0}</td>
                        </tr>
                        <tr>
                            <td>Trigger Filter</td>
                            <td style="text-align: right; color: var(--color-green);">${fs.trigger_ok || 0}</td>
                            <td style="text-align: right; color: var(--color-red);">${fs.trigger_fail || 0}</td>
                        </tr>
                        <tr>
                            <td>Risk Filter</td>
                            <td style="text-align: right; color: var(--color-green);">${fs.risk_ok || 0}</td>
                            <td style="text-align: right; color: var(--color-red);">${fs.risk_fail || 0}</td>
                        </tr>
                        <tr>
                            <td>AI Scorer Filter</td>
                            <td style="text-align: right; color: var(--color-green);">${fs.ai_ok || 0}</td>
                            <td style="text-align: right; color: var(--color-red);">${(fs.ai_veto || 0) + (fs.ai_watch || 0)} <span style="font-size: 9px; color: var(--text-muted);">(${fs.ai_veto || 0} Veto / ${fs.ai_watch || 0} Watch)</span></td>
                        </tr>
                        <tr>
                            <td>Execution Gate</td>
                            <td style="text-align: right; color: var(--color-green);">${fs.exec_ok || 0}</td>
                            <td style="text-align: right; color: var(--color-red);">${(fs.exec_fail_score || 0) + (fs.exec_fail_quality || 0)} <span style="font-size: 9px; color: var(--text-muted);">(${fs.exec_fail_score || 0} Score / ${fs.exec_fail_quality || 0} Qual)</span></td>
                        </tr>
                    </tbody>
                </table>
            </div>

            <!-- Exit Reasons Breakdown -->
            <div class="glass-card" style="flex: 1; padding: 16px; display: flex; flex-direction: column; gap: 12px;">
                <div class="card-header" style="padding: 0 0 10px 0;">
                    <h3><i class="fa-solid fa-right-from-bracket"></i> Exit Reasons</h3>
                </div>
                <div class="table-container" style="max-height: 200px; padding: 0;">
                    <table class="premium-table" style="font-size: 11px;">
                        <thead>
                            <tr>
                                <th>Reason</th>
                                <th style="text-align: right;">Count</th>
                                <th style="text-align: right;">Percentage</th>
                            </tr>
                        </thead>
                        <tbody>
    `;

    const exitEntries = Object.entries(er);
    if (exitEntries.length === 0) {
        html += `<tr><td colspan="3" class="empty-row" style="padding: 20px !important;">No exits recorded.</td></tr>`;
    } else {
        exitEntries.forEach(([reason, count]) => {
            const pct = results.total_trades > 0 ? (count / results.total_trades * 100) : 0;
            html += `
                <tr>
                    <td><strong>${reason.toUpperCase()}</strong></td>
                    <td style="text-align: right;">${count}</td>
                    <td style="text-align: right; color: var(--color-gold); font-family: monospace;">${pct.toFixed(1)}%</td>
                </tr>
            `;
        });
    }

    html += `
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    `;

    html += `
        <div class="glass-card" style="padding: 16px; display: flex; flex-direction: column; gap: 12px; margin-bottom: 16px;">
            <div class="card-header" style="padding: 0 0 10px 0;">
                <h3><i class="fa-solid fa-coins"></i> Asset Performance</h3>
            </div>
            <div class="table-container" style="max-height: 250px; padding: 0;">
                <table class="premium-table" style="font-size: 11px;">
                    <thead>
                        <tr>
                            <th>Asset</th>
                            <th style="text-align: right;">Trades</th>
                            <th style="text-align: right;">Wins</th>
                            <th style="text-align: right;">Losses</th>
                            <th style="text-align: right;">Win Rate</th>
                            <th style="text-align: right;">Net PnL ($)</th>
                            <th style="text-align: right;">Return (%)</th>
                        </tr>
                    </thead>
                    <tbody>
    `;

    const coinPerfEntries = Object.entries(results.coin_perf || {}).sort((a, b) => b[1].net_pnl - a[1].net_pnl);
    if (coinPerfEntries.length === 0) {
        html += `<tr><td colspan="7" class="empty-row" style="padding: 20px !important;">No assets traded.</td></tr>`;
    } else {
        coinPerfEntries.forEach(([sym, data]) => {
            const wins = data.wins || 0;
            const trades = data.trades || 0;
            const losses = trades - wins;
            const wr = trades > 0 ? (wins / trades * 100) : 0;
            const pnl = data.net_pnl || 0;
            const ret = results.initial_balance > 0 ? (pnl / results.initial_balance * 100) : 0;
            const pnlClass = pnl >= 0 ? 'text-pnl-positive' : 'text-pnl-negative';
            const symClean = sym.replace('USDT', '');
            
            html += `
                <tr>
                    <td><strong>${symClean}</strong> <span style="font-size: 9px; color: var(--text-muted);">${sym}</span></td>
                    <td style="text-align: right;">${trades}</td>
                    <td style="text-align: right; color: var(--color-green);">${wins}</td>
                    <td style="text-align: right; color: var(--color-red);">${losses}</td>
                    <td style="text-align: right;">${wr.toFixed(1)}%</td>
                    <td style="text-align: right;" class="${pnlClass}">${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}</td>
                    <td style="text-align: right;" class="${pnlClass}">${ret >= 0 ? '+' : ''}${ret.toFixed(2)}%</td>
                </tr>
            `;
        });
    }

    html += `
                    </tbody>
                </table>
            </div>
        </div>
    `;

    html += `
        <div class="glass-card" style="padding: 16px; display: flex; flex-direction: column; gap: 12px;">
            <div class="card-header" style="padding: 0 0 10px 0;">
                <h3><i class="fa-solid fa-list"></i> Trade Logs Detail <span style="font-size: 10px; color: var(--text-muted); text-transform: none; font-weight: normal; margin-left: 6px;">(Showing last ${results.trades ? results.trades.length : 0} simulation trades)</span></h3>
            </div>
            <div class="table-container" style="max-height: 350px; padding: 0;">
                <table class="premium-table" style="font-size: 11px;">
                    <thead>
                        <tr>
                            <th>ID</th>
                            <th>Symbol</th>
                            <th>Direction</th>
                            <th style="text-align: right;">Entry Price</th>
                            <th style="text-align: right;">Exit Price</th>
                            <th style="text-align: right;">PnL</th>
                            <th>Exit Reason</th>
                            <th>Open Time</th>
                        </tr>
                    </thead>
                    <tbody>
    `;

    const tradesList = results.trades || [];
    if (tradesList.length === 0) {
        html += `<tr><td colspan="8" class="empty-row" style="padding: 20px !important;">No trades executed.</td></tr>`;
    } else {
        tradesList.forEach(t => {
            const sideClass = t.direction.toUpperCase() === 'LONG' ? 'side-long' : 'side-short';
            const pnlClass = t.realized_pnl >= 0 ? 'text-pnl-positive' : 'text-pnl-negative';
            const pnlVal = t.realized_pnl || 0;
            
            html += `
                <tr>
                    <td class="font-mono">#${t.id}</td>
                    <td><strong>${t.symbol.replace('USDT', '')}</strong> <span style="font-size: 9px; color: var(--text-muted);">${t.symbol}</span></td>
                    <td><span class="badge-side ${sideClass}">${t.direction}</span></td>
                    <td style="text-align: right;" class="font-mono">${(t.entry || 0).toFixed(4)}</td>
                    <td style="text-align: right;" class="font-mono">${(t.close_price || 0).toFixed(4)}</td>
                    <td style="text-align: right;" class="${pnlClass} font-mono">${pnlVal >= 0 ? '+' : ''}$${pnlVal.toFixed(2)}</td>
                    <td><span style="font-size: 10px; padding: 2px 6px; background: rgba(255,255,255,0.03); border-radius: 4px; border: 1px solid rgba(255,255,255,0.05); text-transform: uppercase;">${t.close_reason || 'unknown'}</span></td>
                    <td style="font-size: 10px; color: var(--text-secondary);">${t.open_time || ''}</td>
                </tr>
            `;
        });
    }

    html += `
                    </tbody>
                </table>
            </div>
        </div>
    `;

    container.innerHTML = html;
}

async function fetchMlStatus() {
    try {
        const resp = await fetch('/api/ml_status');
        if (!resp.ok) return;
        const res = await resp.json();
        if (!res.ok || !res.data) return;

        const data = res.data;
        document.getElementById('mlSamples').textContent = `${data.n_samples || 0} trades`;
        document.getElementById('mlAccuracy').textContent = data.cv_accuracy > 0 ? data.cv_accuracy.toFixed(3) : 'N/A';
        document.getElementById('mlPrecision').textContent = data.precision_at_70 > 0 ? data.precision_at_70.toFixed(3) : 'N/A';
        
        const statusEl = document.getElementById('mlStatusText');
        if (data.trained) {
            statusEl.textContent = 'Active (Ensemble)';
            statusEl.style.color = 'var(--color-green)';
        } else {
            statusEl.textContent = 'Untrained (Cold Start)';
            statusEl.style.color = 'var(--color-gold)';
        }

        if (data.last_train) {
            document.getElementById('mlLastTrain').textContent = data.last_train.split('.')[0].replace('T', ' ');
        } else {
            document.getElementById('mlLastTrain').textContent = 'N/A';
        }

        const featuresContainer = document.getElementById('mlFeatureImportance');
        if (!featuresContainer) return;

        const features = data.top_features || [];
        if (features.length === 0) {
            featuresContainer.innerHTML = `<span style="font-size: 11px; color: var(--text-muted); text-align: center; margin-top: 10px;">No weights loaded yet.</span>`;
            return;
        }

        let maxWeight = 0.01;
        features.forEach(f => {
            if (f[1] > maxWeight) maxWeight = f[1];
        });

        featuresContainer.innerHTML = '';
        features.forEach(f => {
            const name = f[0];
            const weight = f[1];
            const pct = (weight / maxWeight * 100).toFixed(0);
            const valPct = (weight * 100).toFixed(1);

            const item = document.createElement('div');
            item.style.display = 'flex';
            item.style.flexDirection = 'column';
            item.style.gap = '3px';
            item.innerHTML = `
                <div style="display: flex; justify-content: space-between; font-size: 10px;">
                    <span style="font-weight: 600; color: var(--text-primary); font-size: 10px;">${name}</span>
                    <span style="font-family: monospace; color: var(--color-gold); font-size: 10px;">${valPct}%</span>
                </div>
                <div style="width: 100%; background: rgba(255,255,255,0.03); height: 6px; border-radius: 3px; overflow: hidden; border: 1px solid rgba(255,255,255,0.02);">
                    <div style="width: ${pct}%; height: 100%; background: linear-gradient(90deg, var(--color-gold), var(--color-blue)); border-radius: 3px;"></div>
                </div>
            `;
            featuresContainer.appendChild(item);
        });

    } catch (e) {
        console.error("ML status fetch error:", e);
    }
}

