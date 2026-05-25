/**
 * realtime.js — Realtime Dashboard WebSocket Integration
 * 
 * Amaç: Frontend'i WebSocket üzerinden gerçek zamanlı veri akışına bağlamak.
 * Fallback: WebSocket başarısız olursa polling'e dönüş yapılır.
 * 
 * Emitted Events:
 * - live_update: Açık pozisyon güncellemesi
 * - pnl_update: PnL değişimi
 * - trade_closed: Trade kapatıldı
 * - signal_generated: Yeni sinyal
 * - dashboard_refresh: Tam dashboard yenilemesi
 */

const REALTIME_CONFIG = {
  RECONNECT_DELAY: 3000,
  MAX_RECONNECT_ATTEMPTS: 5,
  HEARTBEAT_INTERVAL: 30000,
  FALLBACK_POLL_INTERVAL: 5000,
};

let socket = null;
let reconnectAttempts = 0;
let useWebSocket = true;
let fallbackPollTimer = null;

/**
 * WebSocket bağlantısını başlatır
 */
function initializeWebSocket() {
  try {
    socket = io(window.location.origin, {
      reconnection: true,
      reconnectionDelay: REALTIME_CONFIG.RECONNECT_DELAY,
      reconnectionAttempts: REALTIME_CONFIG.MAX_RECONNECT_ATTEMPTS,
      transports: ['websocket', 'polling'],
    });

    socket.on('connect', () => {
      console.log('✓ WebSocket bağlantısı kuruldu');
      reconnectAttempts = 0;
      useWebSocket = true;
      clearFallbackPolling();
      socket.emit('dashboard_ready');
    });

    socket.on('disconnect', (reason) => {
      console.warn('✗ WebSocket bağlantısı koptu:', reason);
      if (reason === 'io server disconnect') {
        reconnectAttempts++;
        if (reconnectAttempts >= REALTIME_CONFIG.MAX_RECONNECT_ATTEMPTS) {
          console.warn('⚠ WebSocket yeniden bağlanma başarısız, polling\'e dönüş yapılıyor');
          useWebSocket = false;
          startFallbackPolling();
        }
      }
    });

    socket.on('live_update', (data) => {
      console.debug('📡 Live update alındı:', data);
      handleLiveUpdate(data);
    });

    socket.on('pnl_update', (data) => {
      console.debug('💰 PnL update alındı:', data);
      handlePnLUpdate(data);
    });

    socket.on('trade_closed', (data) => {
      console.debug('🔒 Trade kapatıldı:', data);
      handleTradeClosed(data);
    });

    socket.on('signal_generated', (data) => {
      console.debug('⚡ Sinyal oluşturuldu:', data);
      handleSignalGenerated(data);
    });

    socket.on('dashboard_refresh', () => {
      console.debug('🔄 Tam dashboard yenilemesi istendi');
      if (typeof refreshAll === 'function') {
        refreshAll();
      }
    });

    socket.on('error', (error) => {
      console.error('❌ WebSocket hatası:', error);
    });

  } catch (error) {
    console.error('❌ WebSocket başlatılamadı:', error);
    useWebSocket = false;
    startFallbackPolling();
  }
}

/**
 * Fallback polling mekanizması
 */
function startFallbackPolling() {
  if (fallbackPollTimer) clearInterval(fallbackPollTimer);
  
  console.warn('⚠ Polling modu aktif edildi (WebSocket başarısız)');
  
  fallbackPollTimer = setInterval(async () => {
    try {
      // Açık pozisyonları güncelle
      if (typeof loadPositions === 'function') {
        await loadPositions();
      }
      
      // AX statusunu güncelle
      if (typeof loadAxStatus === 'function') {
        await loadAxStatus();
      }
      
      // İstatistikleri güncelle
      if (typeof loadStats === 'function') {
        await loadStats();
      }
    } catch (error) {
      console.error('Polling hatası:', error);
    }
  }, REALTIME_CONFIG.FALLBACK_POLL_INTERVAL);
}

function clearFallbackPolling() {
  if (fallbackPollTimer) {
    clearInterval(fallbackPollTimer);
    fallbackPollTimer = null;
  }
}

/**
 * Gerçek zamanlı veri işleyicileri
 */
function handleLiveUpdate(data) {
  // Açık pozisyonlar güncellenir
  if (data.positions && typeof loadPositions === 'function') {
    loadPositions();
  }
}

function handlePnLUpdate(data) {
  // PnL kartları güncellenir
  if (data.balance !== undefined) {
    const balEl = document.getElementById('v-bal');
    if (balEl) {
      balEl.textContent = '$' + (data.balance || 0).toFixed(2);
    }
  }

  if (data.unrealized_pnl !== undefined) {
    const urEl = document.getElementById('v-pnl-s');
    if (urEl) {
      const isPos = data.unrealized_pnl >= 0;
      urEl.textContent = 'Unrealized: ' + (isPos ? '+' : '') + '$' + data.unrealized_pnl.toFixed(2);
      urEl.style.color = isPos ? 'var(--em)' : 'var(--ru)';
    }
  }
}

function handleTradeClosed(data) {
  // Trade kapatıldığında history güncellenir
  if (typeof loadHistory === 'function') {
    loadHistory(1);
  }
  
  // Notification göster
  const pnl = data.pnl != null ? data.pnl : (data.net_pnl != null ? data.net_pnl : null);
  if (data.symbol && pnl !== null) {
    const win = pnl > 0;
    const msg = `${data.symbol} ${data.direction || ''} kapandı: ${win ? '+' : ''}${pnl.toFixed(2)}$`;
    showNotification(msg, win ? 'success' : 'error');
  }
}

function handleSignalGenerated(data) {
  // Yeni sinyal oluşturulduğunda
  if (typeof loadStats === 'function') {
    loadStats();
  }
  
  if (data.symbol && data.quality) {
    showNotification(`${data.symbol} ${data.quality} sinyali`, 'info');
  }
}

/**
 * Basit notification sistemi
 */
function showNotification(message, type = 'info') {
  const notifEl = document.createElement('div');
  notifEl.className = `notification notification-${type}`;
  notifEl.textContent = message;
  notifEl.style.cssText = `
    position: fixed;
    top: 20px;
    right: 20px;
    padding: 12px 16px;
    border-radius: 4px;
    font-size: 12px;
    z-index: 9999;
    animation: slideIn 0.3s ease;
    background: ${
      type === 'success' ? 'rgba(5, 150, 105, 0.9)' :
      type === 'error' ? 'rgba(185, 28, 28, 0.9)' :
      'rgba(0, 180, 255, 0.9)'
    };
    color: white;
  `;
  
  document.body.appendChild(notifEl);
  
  setTimeout(() => {
    notifEl.style.animation = 'slideOut 0.3s ease';
    setTimeout(() => notifEl.remove(), 300);
  }, 3000);
}

/**
 * Emit helpers
 */
function emitDashboardEvent(eventName, data = {}) {
  if (socket && socket.connected) {
    socket.emit(eventName, data);
  }
}

/**
 * Başlangıç
 */
document.addEventListener('DOMContentLoaded', () => {
  initializeWebSocket();

  // Heartbeat gönder
  setInterval(() => {
    if (socket && socket.connected) {
      socket.emit('heartbeat');
    }
  }, REALTIME_CONFIG.HEARTBEAT_INTERVAL);
});

// ── Bridge functions — index.html handler'larını WebSocket callback'leriyle bağlar ──
function loadPositions()  { if (typeof fLive === 'function') fLive(); }
function loadStats()      { if (typeof fStats === 'function') fStats(); }
function loadHistory()    { if (typeof fTrades === 'function') fTrades(); }
function loadAxStatus()   { if (typeof fHealthWithMode === 'function') fHealthWithMode(); }
function refreshAll() {
  if (typeof fHealthWithMode === 'function') fHealthWithMode();
  if (typeof fStats === 'function') fStats();
  if (typeof fLive === 'function') fLive();
  if (typeof fTrades === 'function') fTrades();
  if (typeof fSigs === 'function') fSigs();
}
