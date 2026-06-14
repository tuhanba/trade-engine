/* ════════════════════════════════════════════════════════════════════════
   command_center.js — AURVEX Komuta Merkezi (Faz 4)
   Tek endpoint (/api/command_center) → 4 katman render. 5 sn polling.
   Animasyon bütçesi: yalnız expectancy/PnL count-up + nabız pulse (CSS).
   ════════════════════════════════════════════════════════════════════════ */
(function () {
  "use strict";

  const POLL_MS = 5000;
  let _prevExp = null, _prevBal = null, _ccTimer = null;

  const $ = (id) => document.getElementById(id);
  const fmtMoney = (v) => "$" + Number(v || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const fmtR = (v) => (v >= 0 ? "+" : "") + Number(v || 0).toFixed(3) + "R";
  const fmtPct = (v) => (v >= 0 ? "+" : "") + Number(v || 0).toFixed(2) + "%";

  // Sayı count-up animasyonu (yalnız izinli animasyon)
  function countUp(el, from, to, fmt, ms = 600) {
    if (!el) return;
    if (from === null || from === undefined || isNaN(from)) { el.textContent = fmt(to); return; }
    const start = performance.now();
    function step(now) {
      const t = Math.min(1, (now - start) / ms);
      const eased = 1 - Math.pow(1 - t, 3);
      el.textContent = fmt(from + (to - from) * eased);
      if (t < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }

  // ── KATMAN 1: HERO ─────────────────────────────────────────────────
  function renderPulse(p) {
    if (!p) return;
    const dot = $("ccPulseDot"), label = $("ccPulseLabel"), sub = $("ccPulseSub"), comps = $("ccPulseComponents");
    if (dot) dot.className = "cc-pulse-dot " + (p.status || "down");
    if (label) {
      label.textContent = p.label || "SORUN";
      label.style.color = p.status === "ok" ? "var(--cc-health)" : (p.status === "degrade" ? "var(--cc-gold)" : "var(--cc-copper)");
    }
    if (sub) sub.textContent = `${p.score || 0}/${p.max || 5} bileşen sağlıklı`;
    if (comps && p.components) {
      comps.innerHTML = Object.entries(p.components).map(([k, v]) => {
        const cls = v === "ok" ? "ok" : (v === "warn" ? "warn" : "down");
        return `<span class="cc-chip ${cls}">${k}</span>`;
      }).join("");
    }
  }

  function renderSparkline(series) {
    const host = $("ccSpark");
    if (!host) return;
    if (!series || series.length < 2) { host.innerHTML = ""; return; }
    const vals = series.map((s) => Number(s.e || 0));
    const min = Math.min(...vals, 0), max = Math.max(...vals, 0);
    const range = (max - min) || 1;
    const W = 260, H = 34;
    const pts = vals.map((v, i) => {
      const x = (i / (vals.length - 1)) * W;
      const y = H - ((v - min) / range) * H;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(" ");
    const last = vals[vals.length - 1];
    const color = last >= 0 ? "var(--cc-gold)" : "var(--cc-copper)";
    const zeroY = (H - ((0 - min) / range) * H).toFixed(1);
    host.innerHTML =
      `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" width="100%" height="${H}">
         <line x1="0" y1="${zeroY}" x2="${W}" y2="${zeroY}" stroke="rgba(255,255,255,0.12)" stroke-dasharray="3 3"/>
         <polyline points="${pts}" fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round"/>
       </svg>`;
  }

  function renderHero(d) {
    renderPulse(d.pulse);
    // Expectancy — sayfanın en büyük sayısı
    const exp = d.expectancy || {};
    const er = Number(exp.expectancy_r || 0);
    const valEl = $("ccExpValue");
    if (valEl) {
      countUp(valEl.querySelector(".cc-exp-number"), _prevExp, er, (v) => (v >= 0 ? "+" : "") + v.toFixed(3));
      valEl.className = "cc-exp-value " + (er >= 0 ? "cc-pos" : "cc-neg");
    }
    _prevExp = er;
    const expSub = $("ccExpSub");
    if (expSub) {
      expSub.textContent = exp.n > 0
        ? `WR %${(exp.win_rate * 100).toFixed(0)} · ${exp.trades_per_day}/gün · ~${fmtR(exp.weekly_r_projection)}/hafta · n=${exp.n}`
        : "Henüz kapanmış işlem yok — veri birikiyor";
    }
    renderSparkline(d.sparkline);

    // Bakiye + bugünkü PnL
    const w = d.wallet || {};
    countUp($("ccBalance"), _prevBal, Number(w.balance || 0), fmtMoney);
    _prevBal = Number(w.balance || 0);
    const pnlEl = $("ccTodayPnl");
    if (pnlEl) {
      const pnl = Number(w.today_pnl || 0);
      const arrow = pnl > 0 ? "▲" : (pnl < 0 ? "▼" : "■");
      pnlEl.className = "cc-pnl-row " + (pnl >= 0 ? "cc-pos" : "cc-neg");
      pnlEl.innerHTML = `<span>${arrow}</span><span>${fmtMoney(pnl)}</span><span>(${fmtPct(w.today_pnl_pct)})</span>`;
    }
    const ob = $("ccOpenBadge");
    if (ob) ob.textContent = `${w.open_count || 0} açık pozisyon`;
  }

  // ── KATMAN 2: CANLI OPERASYON ──────────────────────────────────────
  function renderPositions(trades) {
    const host = $("ccPositions");
    if (!host) return;
    if (!trades || trades.length === 0) {
      host.innerHTML = `<div class="cc-empty">Açık pozisyon yok — sistem fırsat bekliyor.</div>`;
      return;
    }
    host.innerHTML = trades.map((t) => {
      const isLong = String(t.side).toUpperCase() === "LONG";
      const entry = Number(t.entry_price || 0), cur = Number(t.current_price || entry);
      const tp1 = Number(t.tp1 || 0), sl = Number(t.stop_loss || 0);
      // entry↔TP1 arası dolum (%); SL'e yaklaşınca kırmızılaşır
      let fillPct = 50, fillColor = "var(--cc-gold)";
      if (isLong && tp1 > entry) fillPct = Math.max(0, Math.min(100, ((cur - entry) / (tp1 - entry)) * 100));
      else if (!isLong && tp1 < entry && entry > tp1) fillPct = Math.max(0, Math.min(100, ((entry - cur) / (entry - tp1)) * 100));
      const pnl = Number(t.total_pnl || t.unrealized_pnl || 0);
      if (pnl < 0) fillColor = "var(--cc-copper)";
      const tpDots = [t.tp1_hit, t.tp2_hit, false].map((h) => `<span class="cc-tp-dot ${h ? "hit" : ""}"></span>`).join("");
      return `<div class="cc-pos-item">
        <div class="cc-pos-head">
          <span class="cc-pos-sym">${t.symbol}<span class="cc-side-tag ${isLong ? "cc-side-long" : "cc-side-short"}">${isLong ? "LONG" : "SHORT"}</span></span>
          <span class="cc-num ${pnl >= 0 ? "cc-pos" : "cc-neg"}">${fmtMoney(pnl)}</span>
        </div>
        <div class="cc-pnl-bar"><div class="cc-pnl-fill" style="width:${fillPct.toFixed(0)}%;background:${fillColor}"></div></div>
        <div class="cc-tp-dots">${tpDots}<span class="cc-num cc-muted" style="font-size:9px;margin-left:auto">x${t.leverage || 1}</span></div>
      </div>`;
    }).join("");
  }

  function renderFunnel(f) {
    const host = $("ccFunnel");
    if (!host) return;
    if (!f) { host.innerHTML = `<div class="cc-empty">Huni verisi yok.</div>`; return; }
    const steps = [
      ["Scanned", f.scanned], ["Candidate", f.candidate], ["Watchlist", f.watchlist],
      ["Telegram", f.telegram], ["Trade", f.trade],
    ];
    const maxV = Math.max(...steps.map((s) => s[1] || 0), 1);
    let html = steps.map(([name, v]) => {
      const w = 30 + ((v || 0) / maxV) * 70;
      return `<div class="cc-funnel-step">
        <span class="cc-funnel-name">${name}</span>
        <div class="cc-funnel-bar" style="width:${w}%"><span class="cc-funnel-count">${v || 0}</span></div>
      </div>`;
    }).join("");
    if (f.rejects && f.rejects.length) {
      html += `<div class="cc-rejects"><div class="cc-funnel-name" style="margin-bottom:4px">En sık red (24s)</div>` +
        f.rejects.map((r) => `<div class="cc-reject-item"><span>${r.reason}</span><span class="cc-num">${r.count}</span></div>`).join("") +
        `</div>`;
    } else {
      html += `<div class="cc-rejects"><div class="cc-reject-item cc-muted">Son 24s'te kayıtlı red sebebi yok.</div></div>`;
    }
    host.innerHTML = html;
  }

  // ── KATMAN 3: ZEKA KATMANI ─────────────────────────────────────────
  function renderFriday(fr) {
    const host = $("ccFridayDecisions");
    if (host) {
      const ds = (fr && fr.decisions) || [];
      if (!ds.length) host.innerHTML = `<div class="cc-empty">Henüz karar yok.</div>`;
      else host.innerHTML = ds.map((d) => {
        const sc = d.outcome_score;
        const dotCls = sc === null || sc === undefined ? "neutral" : (sc > 0.05 ? "good" : (sc < -0.05 ? "bad" : "neutral"));
        const chg = d.param_key ? ` ${d.param_key}: ${d.old_value}→${d.new_value}` : "";
        return `<div class="cc-decision"><span class="cc-dot ${dotCls}"></span><span>${d.decision_type}${chg}</span></div>`;
      }).join("");
    }
    const think = $("ccFridayThinking");
    if (think) think.textContent = (fr && fr.thinking) ? `"${fr.thinking}"` : "Friday gözlem modunda…";
  }

  function renderGhost(g) {
    const host = $("ccGhost");
    if (!host) return;
    if (!g) { host.innerHTML = `<div class="cc-empty">Ghost verisi yok.</div>`; return; }
    const recent = (g.recent || []).map((r) =>
      `<div class="cc-decision cc-muted" style="font-size:10px">👻 ${r.symbol} ${r.trigger || ""} → ${Number(r.threshold).toFixed(1)}</div>`
    ).join("") || `<div class="cc-empty" style="padding:6px">Uygulanan öneri yok.</div>`;
    host.innerHTML =
      `<div style="display:flex;justify-content:space-between;font-size:11px;margin-bottom:6px">
         <span class="cc-muted">Sanal WR</span><span class="cc-num cc-pos">%${g.virtual_wr || 0}</span></div>
       <div style="display:flex;justify-content:space-between;font-size:11px;margin-bottom:6px">
         <span class="cc-muted">Aktif override</span><span class="cc-num">${g.active_overrides || 0}</span></div>
       <div style="display:flex;justify-content:space-between;font-size:11px;margin-bottom:8px">
         <span class="cc-muted">Haklı çıkma</span><span class="cc-num cc-short">%${g.skip_correctness || 0}</span></div>
       ${recent}`;
  }

  function renderRegime(r) {
    const badge = $("ccRegimeBadge");
    if (badge) badge.textContent = (r && r.current) || "NEUTRAL";
    const band = $("ccRegimeBand");
    if (band) {
      const segs = (r && r.band) || [];
      if (!segs.length) { band.innerHTML = ""; return; }
      band.innerHTML = segs.map((s) => {
        const reg = String(s.regime || "").toUpperCase();
        let c = "var(--cc-silver-blue)";
        if (reg.includes("TREND")) c = "var(--cc-gold)";
        else if (reg.includes("CHOPPY")) c = "var(--cc-copper)";
        return `<span class="cc-regime-seg" style="background:${c}" title="${s.regime}"></span>`;
      }).join("");
    }
  }

  // ── KATMAN 4: ANALİZ — Live-Readiness ──────────────────────────────
  function renderReadiness(rd) {
    const host = $("ccReadiness");
    if (!host) return;
    if (!rd || !rd.gates || !rd.gates.length) { host.innerHTML = `<div class="cc-empty">Readiness verisi yok.</div>`; return; }
    const head = `<div style="font-weight:700;margin-bottom:8px;color:${rd.ready ? "var(--cc-health)" : "var(--cc-copper)"}">
      ${rd.ready ? "✅ TÜM KAPILAR YEŞİL" : "⛔ HAZIR DEĞİL"} — ${rd.summary || ""}</div>`;
    host.innerHTML = head + rd.gates.map((g) =>
      `<div class="cc-gate ${g.passed ? "pass" : "fail"}">
         <span class="cc-gate-icon">${g.passed ? "🟢" : "🔴"}</span><span>${g.detail}</span></div>`
    ).join("");
  }

  // ── Ana render + polling ───────────────────────────────────────────
  async function refresh() {
    try {
      const resp = await fetch("/api/command_center");
      if (!resp.ok) return;
      const json = await resp.json();
      const d = json.data || json;
      if (!d) return;
      renderHero(d);
      renderPositions(d.open_trades);
      renderFunnel(d.funnel);
      renderFriday(d.friday);
      renderGhost(d.ghost);
      renderRegime(d.regime);
      renderReadiness(d.readiness);
    } catch (e) {
      // sessiz — bir sonraki tur dener (ölü ekran hissi yok, eski veri durur)
    }
  }

  // Mini Friday chat (Katman 3)
  async function sendFridayMini() {
    const inp = $("ccFridayInput");
    if (!inp || !inp.value.trim()) return;
    const msg = inp.value.trim();
    inp.value = "";
    const think = $("ccFridayThinking");
    if (think) think.textContent = "Friday düşünüyor…";
    try {
      const resp = await fetch("/api/friday/chat", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: msg }),
      });
      const j = await resp.json();
      if (think && j.reply) think.textContent = `"${String(j.reply).replace(/<[^>]*>/g, "").slice(0, 200)}"`;
    } catch (e) {
      if (think) think.textContent = "Friday'e ulaşılamadı.";
    }
  }

  // ── KATMAN 4: Korelasyon ısı haritası (on-demand, Faz 6.3) ─────────
  function corrColor(v) {
    if (v === null || v === undefined) return "rgba(255,255,255,0.05)";
    // +1 bakır (riskli birlikte hareket), 0 nötr, -1 gümüş-mavi (çeşitlendirme)
    if (v >= 0) return `rgba(192,83,62,${(0.15 + 0.65 * v).toFixed(2)})`;
    return `rgba(126,156,192,${(0.15 + 0.65 * Math.abs(v)).toFixed(2)})`;
  }
  async function loadCorrelation() {
    const host = $("ccCorrelation");
    if (!host) return;
    host.innerHTML = `<div class="cc-empty">Pearson matrisi hesaplanıyor…</div>`;
    try {
      const resp = await fetch("/api/correlation_matrix");
      const d = (await resp.json()).data || {};
      const syms = d.symbols || [];
      if (!d.matrix || d.matrix.length === 0) {
        host.innerHTML = `<div class="cc-empty">${d.note || "Korelasyon için en az 2 açık pozisyon gerekir."}</div>`;
        return;
      }
      let html = '<table style="border-collapse:collapse;font-size:10px;width:100%"><tr><th></th>';
      html += syms.map((s) => `<th style="padding:3px;color:var(--cc-text-dim)">${s.replace("USDT", "")}</th>`).join("");
      html += "</tr>";
      d.matrix.forEach((row, i) => {
        html += `<tr><td style="padding:3px;color:var(--cc-text-dim)">${syms[i].replace("USDT", "")}</td>`;
        html += row.map((v) => {
          const txt = v === null ? "·" : v.toFixed(2);
          return `<td class="cc-num" style="padding:5px;text-align:center;background:${corrColor(v)}">${txt}</td>`;
        }).join("");
        html += "</tr>";
      });
      html += "</table>";
      if (d.max_pair) {
        html += `<div class="cc-pulse-sub" style="margin-top:8px">En yüksek: ${d.max_pair.a.replace("USDT", "")}↔${d.max_pair.b.replace("USDT", "")} = ${d.max_pair.corr.toFixed(2)}</div>`;
      }
      host.innerHTML = html;
    } catch (e) {
      host.innerHTML = `<div class="cc-empty">Korelasyon yüklenemedi.</div>`;
    }
  }

  function toggleAccordion(el) {
    if (el && el.parentElement) {
      el.parentElement.classList.toggle("open");
      // Korelasyon akordeonu açıldığında on-demand yükle (5sn poll'a dahil değil)
      if (el.parentElement.id === "ccCorrAccordion" && el.parentElement.classList.contains("open")) {
        loadCorrelation();
      }
    }
  }

  function init() {
    if (!$("cc-root")) return;
    refresh();
    if (_ccTimer) clearInterval(_ccTimer);
    _ccTimer = setInterval(refresh, POLL_MS);
    const btn = $("ccFridaySend");
    if (btn) btn.addEventListener("click", sendFridayMini);
    const inp = $("ccFridayInput");
    if (inp) inp.addEventListener("keydown", (e) => { if (e.key === "Enter") sendFridayMini(); });
    document.querySelectorAll(".cc-acc-head").forEach((h) => h.addEventListener("click", () => toggleAccordion(h)));
  }

  window.ccRefresh = refresh;
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
