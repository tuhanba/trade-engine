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

function mkSignal(coin) {
  const r = Math.random();
  const type = r < 0.13 ? "LONG" : r < 0.25 ? "SHORT" : "NEUTRAL";
  return { coin, type, score: +(type === "NEUTRAL" ? rnd(8, 45) : rnd(65, 97)).toFixed(1) };
}

function sigCol(t) { return t === "LONG" ? "#00e87a" : t === "SHORT" ? "#ff3d5a" : "#3a3a50"; }
function sigBg(t)  { return t === "LONG" ? "rgba(0,232,122,.05)" : t === "SHORT" ? "rgba(255,61,90,.05)" : "transparent"; }
function sigBdr(t) { return t === "LONG" ? "rgba(0,232,122,.28)" : t === "SHORT" ? "rgba(255,61,90,.28)" : "rgba(212,168,67,.1)"; }
function fmtTime(ts) { return new Date(ts).toTimeString().slice(0, 8); }

const state = {
  coins: {},
  feed: [],
  bal: 10000.00,
  m: { wr: 67.3, ml: 87.2, today: 23 },
};

function initCoins() {
  COINS.forEach(c => { state.coins[c] = mkSignal(c); });
}

function initFeed() {
  state.feed = [...COINS].sort(() => Math.random() - 0.5).slice(0, 12)
    .map((coin, i) => ({
      id: Math.random(),
      coin,
      type: Math.random() < 0.58 ? "LONG" : "SHORT",
      score: +rnd(68, 97).toFixed(1),
      ts: Date.now() - i * rnd(40000, 280000),
    }))
    .sort((a, b) => b.ts - a.ts);
}

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
    const d = state.coins[coin];
    const card = document.createElement("div");
    card.className = "coin-card cc" + (d.type === "LONG" ? " aGL" : d.type === "SHORT" ? " aGS" : "");
    card.id = "coin-" + coin;
    card.style.background = sigBg(d.type);
    card.style.border = "1px solid " + sigBdr(d.type);

    card.innerHTML = `
      <div class="coin-top">
        <span class="coin-name">${coin}</span>
        ${d.type !== "NEUTRAL" ? `<span class="coin-dot aBL" style="background:${sigCol(d.type)}"></span>` : ""}
      </div>
      <div class="coin-signal" style="color:${d.type !== "NEUTRAL" ? sigCol(d.type) : "rgba(212,168,67,.15)"}">
        ${d.type !== "NEUTRAL" ? d.type[0] + " " + d.score : "—"}
      </div>
      <div class="coin-bar-bg">
        <div class="coin-bar" style="width:${d.score}%;background:${sigCol(d.type)};opacity:${d.type === "NEUTRAL" ? .1 : .62}"></div>
      </div>`;
    grid.appendChild(card);
  });
}

function updateCoinCard(coin) {
  const d = state.coins[coin];
  const card = document.getElementById("coin-" + coin);
  if (!card) return;

  card.className = "coin-card cc" + (d.type === "LONG" ? " aGL" : d.type === "SHORT" ? " aGS" : "");
  card.style.background = sigBg(d.type);
  card.style.border = "1px solid " + sigBdr(d.type);

  const top = card.querySelector(".coin-top");
  top.innerHTML = `<span class="coin-name">${coin}</span>` +
    (d.type !== "NEUTRAL" ? `<span class="coin-dot aBL" style="background:${sigCol(d.type)}"></span>` : "");

  const sig = card.querySelector(".coin-signal");
  sig.style.color = d.type !== "NEUTRAL" ? sigCol(d.type) : "rgba(212,168,67,.15)";
  sig.textContent = d.type !== "NEUTRAL" ? d.type[0] + " " + d.score : "—";

  const bar = card.querySelector(".coin-bar");
  bar.style.width = d.score + "%";
  bar.style.background = sigCol(d.type);
  bar.style.opacity = d.type === "NEUTRAL" ? .1 : .62;
}

function renderFeed() {
  const list = document.getElementById("feedList");
  list.innerHTML = "";
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
  document.getElementById("longCount").textContent = vals.filter(c => c.type === "LONG").length;
  document.getElementById("shortCount").textContent = vals.filter(c => c.type === "SHORT").length;
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
  document.getElementById("footerClock").textContent = local;
}

function tick() {
  const picks = [...COINS].sort(() => Math.random() - .5).slice(0, Math.ceil(rnd(2, 5)));
  const newItems = [];

  picks.forEach(coin => {
    const d = mkSignal(coin);
    if (Math.random() < .43) {
      d.type = Math.random() < .57 ? "LONG" : "SHORT";
      d.score = +rnd(68, 97).toFixed(1);
      newItems.push({ id: Date.now() + Math.random(), coin, type: d.type, score: d.score, ts: Date.now() });
    }
    state.coins[coin] = d;
    updateCoinCard(coin);
  });

  picks.forEach(coin => {
    const card = document.getElementById("coin-" + coin);
    if (card) {
      card.classList.add("aFL");
      setTimeout(() => card.classList.remove("aFL"), 620);
    }
  });

  if (newItems.length) {
    state.feed = [...newItems, ...state.feed].slice(0, 25);
    renderFeed();
  }

  state.m.wr = Math.max(50, Math.min(82, state.m.wr + rnd(-.4, .4)));
  state.m.ml = Math.max(72, Math.min(98, state.m.ml + rnd(-.22, .22)));
  state.m.today += Math.random() < .25 ? 1 : 0;
  state.bal = +Math.max(9200, Math.min(12500, state.bal + rnd(-18, 28))).toFixed(2);

  updateCounts();
  updateWinRate();
  updateMl();
  updateBal();
}

document.addEventListener("DOMContentLoaded", () => {
  initCoins();
  initFeed();
  buildMlBars();
  buildCoinGrid();
  renderFeed();
  updateCounts();
  updateWinRate();
  updateMl();
  updateBal();
  updateClock();

  setInterval(updateClock, 1000);
  setInterval(tick, 7500);
});
