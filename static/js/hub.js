const COINS = [
  "BTC","ETH","BNB","SOL","ADA","XRP","DOGE","DOT","AVAX","MATIC","LINK","UNI",
  "LTC","ATOM","FIL","AAVE","CRV","COMP","NEAR","FTM","ALGO","VET","XLM","TRX",
  "EOS","MANA","SAND","AXS","GALA","RUNE","GRT","DYDX","GMX","ENJ","BAT","LRC",
  "1INCH","SUSHI","MKR","YFI","OCEAN","ANKR","HBAR","ICP","EGLD","KSM","FLOW",
  "CHZ","HOT","IOTA","XTZ","ZEC","DASH","ETC","NEO","WAVES","ZIL","ICX","QTUM",
  "OMG","NMR","RLC","BNT","KNC","ANT","CELO","SKL","BAND","STORJ","API3","REN",
  "SRM","RAY","JOE","ILV","ALICE","ACH","BAKE","WIN","TLM","BEL","SNX","BAL",
  "REP","ZRX","CVX","CHR","ORN","VITE","FLM","HARD"
];

const CIRC = 2 * Math.PI * 36;

function rnd(a, b) { return a + Math.random() * (b - a); }

function sigCol(t) { return t === "LONG" ? "#00e87a" : t === "SHORT" ? "#ff3d5a" : "#3a3a50"; }
function sigBg(t)  { return t === "LONG" ? "rgba(0,232,122,.05)" : t === "SHORT" ? "rgba(255,61,90,.05)" : "transparent"; }
function sigBdr(t) { return t === "LONG" ? "rgba(0,232,122,.28)" : t === "SHORT" ? "rgba(255,61,90,.28)" : "rgba(212,168,67,.1)"; }
function fmtTime(ts) { return new Date(ts).toTimeString().slice(0, 8); }

const state = {
  coins: {},
  feed: [],
  bal: 2000.00,
  m: { wr: 50.0, ml: 87.2, today: 0 },
};

function buildMlBars() {
  const container = document.getElementById("mlBars");
  container.innerHTML = "";
  for (let i = 0; i < 10; i++) {
    const bar = document.createElement("div");
    bar.className = "ml-bar";
    bar.dataset.index = i;
    container.appendChild(bar);
  }
}

function updateMlBars() {
  const bars = document.querySelectorAll(".ml-bar");
  const filled = Math.round(state.m.ml / 10);
  bars.forEach((bar, i) => {
    if (i < filled) {
      bar.style.background = `rgba(212,168,67,${(.28 + i * .072).toFixed(3)})`;
      bar.style.boxShadow = "0 0 3px rgba(212,168,67,.28)";
    } else {
      bar.style.background = "rgba(255,255,255,.04)";
      bar.style.boxShadow = "none";
    }
  });
}

function buildCoinGrid() {
  const grid = document.getElementById("coinGrid");
  grid.innerHTML = "";
  document.getElementById("universeLabel").textContent = `◆ UNIVERSE — ${COINS.length} PAIRS`;

  COINS.forEach(coin => {
    const d = state.coins[coin] || { coin, type: "NEUTRAL", score: 0 };
    const card = document.createElement("div");
    const isActive = d.active;

    card.className = "coin-card cc" + (d.type === "LONG" ? " aGL" : d.type === "SHORT" ? " aGS" : "");
    card.id = "coin-" + coin;
    card.style.background = isActive ? "rgba(212,168,67,.06)" : sigBg(d.type);
    card.style.border = isActive ? "1px solid #d4a843" : sigBdr(d.type);
    if (isActive) {
      card.style.boxShadow = "0 0 10px rgba(212,168,67,.28)";
    }

    card.innerHTML = `
      <div class="coin-top">
        <span class="coin-name">${coin}</span>
        ${d.type !== "NEUTRAL" ? `<span class="coin-dot aBL" style="background:${isActive ? '#d4a843' : sigCol(d.type)}"></span>` : ""}
      </div>
      <div class="coin-signal" style="color:${isActive ? '#d4a843' : (d.type !== "NEUTRAL" ? sigCol(d.type) : "rgba(212,168,67,.15)")}">
        ${d.type !== "NEUTRAL" ? (isActive ? "★ ACTIVE" : d.type[0] + " " + d.score) : "—"}
      </div>
      <div class="coin-bar-bg">
        <div class="coin-bar" style="width:${d.score}%;background:${isActive ? '#d4a843' : sigCol(d.type)};opacity:${d.type === "NEUTRAL" ? .1 : .62}"></div>
      </div>`;
    grid.appendChild(card);
  });
}

function updateCoinCard(coin) {
  const d = state.coins[coin];
  if (!d) return;
  const card = document.getElementById("coin-" + coin);
  if (!card) return;

  const isActive = d.active;
  card.className = "coin-card cc" + (d.type === "LONG" ? " aGL" : d.type === "SHORT" ? " aGS" : "");
  card.style.background = isActive ? "rgba(212,168,67,.06)" : sigBg(d.type);
  card.style.border = isActive ? "1px solid #d4a843" : sigBdr(d.type);
  if (isActive) {
    card.style.boxShadow = "0 0 10px rgba(212,168,67,.28)";
  } else {
    card.style.boxShadow = "none";
  }

  const top = card.querySelector(".coin-top");
  top.innerHTML = `<span class="coin-name">${coin}</span>` +
    (d.type !== "NEUTRAL" ? `<span class="coin-dot aBL" style="background:${isActive ? '#d4a843' : sigCol(d.type)}"></span>` : "");

  const sig = card.querySelector(".coin-signal");
  sig.style.color = isActive ? '#d4a843' : (d.type !== "NEUTRAL" ? sigCol(d.type) : "rgba(212,168,67,.15)");
  sig.textContent = d.type !== "NEUTRAL" ? (isActive ? "★ ACTIVE" : d.type[0] + " " + d.score) : "—";

  const bar = card.querySelector(".coin-bar");
  bar.style.width = d.score + "%";
  bar.style.background = isActive ? '#d4a843' : sigCol(d.type);
  bar.style.opacity = d.type === "NEUTRAL" ? .1 : .62;
}

function renderFeed() {
  const list = document.getElementById("feedList");
  list.innerHTML = "";
  if (state.feed.length === 0) {
    list.innerHTML = `<div class="feed-item" style="text-align:center;color:rgba(212,168,67,.3)">NO RECENT SIGNALS</div>`;
    return;
  }
  state.feed.forEach(item => {
    const div = document.createElement("div");
    div.className = "feed-item aFI";
    div.style.background = sigBg(item.type);
    div.style.border = "1px solid " + sigBdr(item.type);
    div.innerHTML = `
      <span class="feed-coin">${item.coin}</span>
      <span class="feed-type" style="color:${sigCol(item.type)}">${item.type}</span>
      <span class="feed-score">${item.score}</span>
      <span class="feed-time">${fmtTime(item.ts)}</span>`;
    list.appendChild(div);
  });
}

function updateCounts() {
  const vals = Object.values(state.coins);
  document.getElementById("longCount").textContent = vals.filter(c => c.active && c.type === "LONG").length;
  document.getElementById("shortCount").textContent = vals.filter(c => c.active && c.type === "SHORT").length;
  document.getElementById("todayCount").textContent = state.m.today;
}

function updateWinRate() {
  const dashOff = CIRC * (1 - state.m.wr / 100);
  document.getElementById("wrArc").setAttribute("stroke-dashoffset", dashOff);
  document.getElementById("wrValue").textContent = state.m.wr.toFixed(1) + "%";
}

function updateMl() {
  document.getElementById("mlValue").innerHTML = state.m.ml.toFixed(1) + '<span class="ml-conf">CONF</span>';
  updateMlBars();
}

function updateBal() {
  document.getElementById("balValue").textContent =
    "$" + state.bal.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function updateClock() {
  const now = new Date();
  const utc = now.toUTCString().slice(17, 25);
  const local = now.toTimeString().slice(0, 8);
  document.getElementById("clock").textContent = utc + " UTC";
  const footerClockEl = document.getElementById("footerClock");
  if (footerClockEl) footerClockEl.textContent = local;
}

async function tick() {
  try {
    // 1. Fetch `/api/dashboard_data`
    const dbDataResp = await fetch('/api/dashboard_data');
    if (!dbDataResp.ok) return;
    const dbData = await dbDataResp.json();
    
    // 2. Fetch `/api/learning`
    let learning = null;
    try {
      const learningResp = await fetch('/api/learning');
      if (learningResp.ok) learning = await learningResp.json();
    } catch(e) {}
    
    // 3. Fetch `/api/signals`
    let signals = null;
    try {
      const signalsResp = await fetch('/api/signals');
      if (signalsResp.ok) signals = await signalsResp.json();
    } catch(e) {}
    
    // 4. Fetch `/api/ml_status`
    let mlStatus = null;
    try {
      const mlResp = await fetch('/api/ml_status');
      if (mlResp.ok) mlStatus = await mlResp.json();
    } catch(e) {}

    // 5. Fetch `/api/coin_profiles`
    let coinProfiles = null;
    try {
      const cpResp = await fetch('/api/coin_profiles');
      if (cpResp.ok) coinProfiles = await cpResp.json();
    } catch(e) {}

    // 6. Update state balance
    if (dbData && dbData.total_balance !== undefined) {
      state.bal = dbData.total_balance;
    }
    
    // 7. Update metrics (win rate)
    if (dbData && dbData.stats) {
      state.m.wr = dbData.stats.win_rate || 0.0;
    }
    
    if (mlStatus && mlStatus.data) {
      state.m.ml = (mlStatus.data.cv_accuracy || 0.87) * 100;
      const mlStatusText = `● ENSEMBLE · ${mlStatus.data.n_samples || 0} SAMPLES`;
      const mlStatusEl = document.querySelector(".ml-status");
      if (mlStatusEl) mlStatusEl.textContent = mlStatusText;
    }
    
    // 8. Process active signals / trades
    const activeTrades = dbData.active_trades || [];
    
    // Today's total closed trades count
    try {
      const statsResp = await fetch('/api/stats');
      const statsJson = await statsResp.json();
      if (statsJson.ok && statsJson.data) {
        state.m.today = statsJson.data.funnel ? statsJson.data.funnel.trade : statsJson.data.total_trades || 0;
      }
    } catch(e){}

    // Update state.coins
    // First, reset all coins to NEUTRAL
    COINS.forEach(c => {
      state.coins[c] = { coin: c, type: "NEUTRAL", score: 0 };
    });

    // Apply coin profiles
    if (coinProfiles && coinProfiles.data) {
      coinProfiles.data.forEach(p => {
        const base = p.symbol.replace("USDT", "");
        if (COINS.includes(base)) {
          const wr = p.win_rate_pct || 50;
          state.coins[base] = {
            coin: base,
            type: wr >= 55.0 ? "LONG" : (wr <= 45.0 ? "SHORT" : "NEUTRAL"),
            score: wr
          };
        }
      });
    }

    // Overlay active trades (highest priority)
    const pickedActiveCoins = [];
    activeTrades.forEach(t => {
      const base = t.symbol.replace("USDT", "");
      if (COINS.includes(base)) {
        state.coins[base] = {
          coin: base,
          type: t.direction,
          score: t.final_score || 75,
          active: true
        };
        pickedActiveCoins.push(base);
      }
    });

    // Rebuild coin grid to update UI
    buildCoinGrid();

    // Trigger visual highlight flash for active coins
    pickedActiveCoins.forEach(coin => {
      const card = document.getElementById("coin-" + coin);
      if (card) {
        card.classList.add("aFL");
        setTimeout(() => card.classList.remove("aFL"), 620);
      }
    });

    // Reprocess feed from recent signals
    if (signals && signals.data) {
      state.feed = signals.data.slice(0, 15).map(s => ({
        coin: s.symbol.replace("USDT", ""),
        type: s.direction,
        score: s.final_score || s.score || 50,
        ts: s.created_at ? new Date(s.created_at.replace(" ", "T") + "Z").getTime() : Date.now()
      }));
      renderFeed();
    }

    updateCounts();
    updateWinRate();
    updateMl();
    updateBal();

  } catch (error) {
    console.error("Error refreshing dashboard data in tick:", error);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  buildMlBars();
  updateClock();
  tick(); // Load real data immediately

  setInterval(updateClock, 1000);
  setInterval(tick, 5000); // Poll every 5 seconds for real-time updates
});
