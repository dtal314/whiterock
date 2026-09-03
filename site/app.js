/* WhiteRock dashboard. Plain JS, reads the JSON the pipeline writes to data/. */
(function () {
  "use strict";
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const pct = (p, d = 0) => (p == null || Number.isNaN(p)) ? "<span class='muted'>n/a</span>" : (100 * p).toFixed(d) + "%";
  const signed = (x, d = 1) => (x == null || Number.isNaN(x)) ? "<span class='muted'>n/a</span>" : `<span class="${x >= 0 ? "pos" : "neg"}">${x >= 0 ? "+" : ""}${(100 * x).toFixed(d)}%</span>`;
  const fmtDate = (s) => s ? s.slice(0, 10) : "";
  const TYPE = { RULE: "Final rule", PRORULE: "Proposed rule", PRESDOCU: "Presidential document", NOTICE: "Notice" };

  const state = { summary: null, actions: [], sectors: [], politicians: [], tickers: [], sectorName: {}, sort: {} };

  async function load() {
    const get = (f) => fetch(`data/${f}?t=${Date.now() >> 16}`).then((r) => { if (!r.ok) throw new Error(f); return r.json(); });
    try {
      [state.summary, state.actions, state.sectors, state.politicians, state.tickers] = await Promise.all([
        get("summary.json"), get("actions.json"), get("sectors.json"), get("politicians.json"), get("tickers.json")]);
    } catch (e) {
      $("#meta").textContent = "Data not built yet. Run the pipeline.";
      $("#actions-list").innerHTML = `<div class="empty">No data yet. The pipeline writes site/data/*.json.</div>`;
      return;
    }
    state.sectors.forEach((s) => (state.sectorName[s.id] = s.name));
    const c = state.summary.counts;
    $("#meta").innerHTML = `Updated ${esc(state.summary.generated_at.replace("T", " ").slice(0, 16))} UTC<br>${c.recent_actions} recent actions, ${c.transactions.toLocaleString()} disclosed transactions, prices through ${esc(c.latest_price_date || "n/a")}`;
    $("#disclaimer").textContent = state.summary.disclaimer;
    for (const sel of ["#f-sector", "#f-pol-sector"]) {
      state.sectors.forEach((s) => { const o = document.createElement("option"); o.value = s.id; o.textContent = s.name; $(sel).appendChild(o); });
    }
    renderActions(); renderCompanies(); renderPoliticians(); renderSectors(); renderModel();
  }

  /* ------------------------------------------------------------ tabs */
  $$(".tab").forEach((b) => b.addEventListener("click", () => {
    $$(".tab").forEach((x) => x.classList.toggle("active", x === b));
    $$(".panel").forEach((p) => p.classList.toggle("active", p.id === "panel-" + b.dataset.tab));
    location.hash = b.dataset.tab;
  }));
  if (location.hash) { const b = $(`.tab[data-tab="${location.hash.slice(1)}"]`); if (b) b.click(); }

  /* ------------------------------------------------------------ actions */
  function dirBadge(d, score) {
    const cls = d > 0 ? "up" : d < 0 ? "down" : "flat";
    const label = d > 0 ? "Likely benefit" : d < 0 ? "Likely suffer" : "Direction unclear";
    return `<span class="dir ${cls}" title="rule score ${score}">${label}</span>`;
  }
  function bar(p, cls) {
    if (p == null) return "<span class='muted'>n/a</span>";
    return `<span class="bar ${cls || ""}"><span style="width:${Math.round(100 * p)}%"></span></span>${(100 * p).toFixed(0)}%`;
  }
  function tickerTable(rows, h) {
    if (!rows || !rows.length) return `<div class="sub">No priced tickers for this sector.</div>`;
    const hs = state.summary.horizons;
    return `<table class="mini"><thead><tr><th>Ticker</th>${hs.map((x) => `<th class="num">P(beat ${x}d)</th>`).join("")}<th class="num">Since action</th></tr></thead><tbody>` +
      rows.map((r) => `<tr><td><span class="tk">${esc(r.ticker)}</span> <span class="sub">vs ${esc(r.benchmark)}</span></td>` +
        hs.map((x) => { const p = r.p_outperform[String(x)]; const real = r.realized_excess[String(x)];
          return `<td class="num">${real != null ? `${signed(real)} <span class="sub">real</span>` : bar(p, p > 0.5 ? "up" : "down")}</td>`; }).join("") +
        `<td class="num">${signed(r.realized_excess_to_date)} <span class="sub">${r.trading_days_elapsed}d</span></td></tr>`).join("") + `</tbody></table>`;
  }
  function polTable(rows) {
    if (!rows || !rows.length) return `<div class="sub">No member of Congress has disclosed trades in this sector since 2023.</div>`;
    return `<table class="mini"><thead><tr><th>Who</th><th class="num">P(buy)</th><th class="num">P(sell)</th><th class="num">History</th></tr></thead><tbody>` +
      rows.map((r) => `<tr><td><a href="#politicians" data-pol="${esc(r.id)}" class="pol-link">${esc(r.name)}</a> <span class="sub">${esc(r.party || "")} ${esc(r.state || "")} ${r.committee_relevant ? "· committee" : ""}</span></td>` +
        `<td class="num">${bar(r.p_buy, "up")}</td><td class="num">${bar(r.p_sell, "down")}</td>` +
        `<td class="num">${r.hist_buys}B / ${r.hist_sells}S <span class="sub">${r.last ? "last " + esc(r.last) : ""}</span></td></tr>`).join("") + `</tbody></table>`;
  }
  function renderActions() {
    const q = ($("#q-actions").value || "").toLowerCase();
    const fs = $("#f-sector").value, fd = $("#f-direction").value, forth = $("#f-forthcoming").checked;
    const rows = state.actions.filter((a) => {
      if (forth && !a.forthcoming) return false;
      const secs = a.sectors.filter((s) => (!fs || s.sector_id === fs) && (fd === "" || String(s.direction) === fd));
      if (!secs.length) return false;
      if (!q) return true;
      const hay = [a.title, a.abstract, a.agencies.join(" "), a.type, ...a.sectors.flatMap((s) => [s.sector, ...s.tickers.map((t) => t.ticker)])].join(" ").toLowerCase();
      return hay.includes(q);
    });
    $("#count-actions").textContent = `${rows.length} of ${state.actions.length} actions in the last ${state.summary.recent_action_days} days`;
    $("#actions-list").innerHTML = rows.length ? rows.map((a) => {
      const secs = a.sectors.filter((s) => (!fs || s.sector_id === fs) && (fd === "" || String(s.direction) === fd));
      return `<article class="card ${a.forthcoming ? "forthcoming" : ""}">
        <div class="card-head"><div>
          <div class="kicker">${a.forthcoming ? `<span class="pill forth">Forthcoming: on public inspection</span> ` : ""}<b>${esc(TYPE[a.type] || a.type || "Document")}</b> · ${esc(fmtDate(a.publication_date))} · ${esc(a.agencies.slice(0, 3).join(", "))}${a.significant ? ` <span class="pill sig">significant</span>` : ""}${a.eo_number ? ` <span class="pill">EO ${esc(a.eo_number)}</span>` : ""}</div>
          <h3><a href="${esc(a.url)}" target="_blank" rel="noopener">${esc(a.title)}</a></h3>
          ${a.abstract ? `<p class="abstract">${esc(a.abstract)}</p>` : ""}
        </div></div>
        ${secs.map((s) => `<div class="sector-row">
          <div><div class="sector-name">${esc(s.sector)}</div>${dirBadge(s.direction, s.direction_score)}<div class="why">relevance ${(100 * s.relevance).toFixed(0)}%<br>${s.why.keywords.length ? `<b>terms</b> ${esc(s.why.keywords.slice(0, 5).join(", "))}` : ""}${s.why.direction_terms.length ? `<br><b>direction</b> ${esc(s.why.direction_terms.slice(0, 4).join(", "))}` : ""}</div></div>
          <div>${tickerTable(s.tickers)}</div>
          <div>${polTable(s.politicians)}</div>
        </div>`).join("")}
      </article>`;
    }).join("") : `<div class="empty">Nothing matches these filters.</div>`;
    $$(".pol-link").forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); $(`.tab[data-tab="politicians"]`).click(); showPol(a.dataset.pol); }));
  }
  ["#q-actions", "#f-sector", "#f-direction", "#f-forthcoming"].forEach((s) => $(s).addEventListener("input", renderActions));

  /* ------------------------------------------------------------ generic sortable table */
  function table(el, cols, rows, key, onRow) {
    const st = state.sort[key] || { col: cols.find((c) => c.default)?.id || cols[0].id, dir: -1 };
    state.sort[key] = st;
    const col = cols.find((c) => c.id === st.col) || cols[0];
    const sorted = rows.slice().sort((a, b) => { const va = col.val(a), vb = col.val(b); if (va == null) return 1; if (vb == null) return -1; return (va > vb ? 1 : va < vb ? -1 : 0) * st.dir; });
    el.innerHTML = `<thead><tr>${cols.map((c) => `<th class="${c.num ? "num" : ""}" data-col="${c.id}">${esc(c.label)}${c.id === st.col ? `<span class="arrow">${st.dir > 0 ? "▲" : "▼"}</span>` : ""}</th>`).join("")}</tr></thead>` +
      `<tbody>${sorted.map((r) => `<tr class="${onRow ? "clickable" : ""}" data-id="${esc(r.id || r.ticker)}">${cols.map((c) => `<td class="${c.num ? "num" : ""}">${c.cell(r)}</td>`).join("")}</tr>`).join("")}</tbody>`;
    $$("th", el).forEach((th) => th.addEventListener("click", () => { const id = th.dataset.col; st.dir = st.col === id ? -st.dir : -1; st.col = id; table(el, cols, rows, key, onRow); }));
    if (onRow) $$("tbody tr", el).forEach((tr) => tr.addEventListener("click", () => onRow(tr.dataset.id)));
  }

  /* ------------------------------------------------------------ companies */
  function renderCompanies() {
    const q = ($("#q-companies").value || "").toLowerCase();
    const hs = state.summary.horizons;
    const rows = state.tickers.filter((t) => !q || t.ticker.toLowerCase().includes(q) || t.sectors.some((s) => (state.sectorName[s] || "").toLowerCase().includes(q)));
    $("#count-companies").textContent = `${rows.length} tickers in ${state.sectors.length} sectors`;
    const cols = [
      { id: "ticker", label: "Ticker", val: (t) => t.ticker, cell: (t) => `<span class="tk">${esc(t.ticker)}</span>` },
      { id: "sectors", label: "Sectors", val: (t) => t.sectors.join(), cell: (t) => t.sectors.map((s) => esc(state.sectorName[s] || s)).join(", ") },
      { id: "recent", label: "Recent actions", num: true, default: true, val: (t) => t.n_recent_actions, cell: (t) => t.n_recent_actions },
      ...hs.map((h) => ({ id: "p" + h, label: `P(beat ${h}d)`, num: true, val: (t) => t.latest_scores ? t.latest_scores.p_outperform[String(h)] : null, cell: (t) => t.latest_scores ? pct(t.latest_scores.p_outperform[String(h)]) : "<span class='muted'>no live action</span>" })),
      { id: "mom", label: "20d vs bench", num: true, val: (t) => t.latest_scores ? t.latest_scores.mom_20 : null, cell: (t) => t.latest_scores ? signed(t.latest_scores.mom_20) : "" },
      { id: "trades", label: "Disclosed trades", num: true, val: (t) => t.n_trades, cell: (t) => `${t.n_trades} <span class="sub">${t.n_buys}B / ${t.n_sells}S</span>` },
      { id: "traders", label: "Who traded it", val: (t) => t.traders.length, cell: (t) => t.traders.slice(0, 4).map((p) => `<a href="#politicians" class="pol-link" data-pol="${esc(p.id)}">${esc(p.name)}</a>`).join(", ") + (t.traders.length > 4 ? ` <span class="sub">+${t.traders.length - 4}</span>` : "") },
    ];
    table($("#companies-table"), cols, rows, "companies");
    $$("#companies-table .pol-link").forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); $(`.tab[data-tab="politicians"]`).click(); showPol(a.dataset.pol); }));
  }
  $("#q-companies").addEventListener("input", renderCompanies);

  /* ------------------------------------------------------------ politicians */
  function renderPoliticians() {
    const q = ($("#q-pols").value || "").toLowerCase(), ch = $("#f-chamber").value, sec = $("#f-pol-sector").value;
    const rows = state.politicians.filter((p) => (!ch || p.chamber === ch) && (!q || [p.name, p.state, p.party, ...(p.committees || [])].join(" ").toLowerCase().includes(q)) && (!sec || p.forecasts[sec] || p.by_sector[sec]));
    $("#count-pols").textContent = `${rows.length} people with disclosed transactions since 2023`;
    const f = (p, k) => sec && p.forecasts[sec] ? p.forecasts[sec][k] : null;
    const cols = [
      { id: "name", label: "Name", val: (p) => p.name, cell: (p) => `<b>${esc(p.name)}</b><br><span class="sub">${esc(p.chamber)} · ${esc(p.party || "")} ${esc(p.state || "")}${p.current === false ? " · former" : ""}</span>` },
      { id: "n", label: "Transactions", num: true, default: !sec, val: (p) => p.n_transactions, cell: (p) => `${p.n_transactions} <span class="sub">${p.n_buys}B / ${p.n_sells}S</span>` },
      { id: "univ", label: "In universe", num: true, val: (p) => p.n_in_universe, cell: (p) => p.n_in_universe },
      ...(sec ? [
        { id: "pbuy", label: "P(buy 60d)", num: true, default: true, val: (p) => f(p, "p_buy"), cell: (p) => bar(f(p, "p_buy"), "up") },
        { id: "psell", label: "P(sell 60d)", num: true, val: (p) => f(p, "p_sell"), cell: (p) => bar(f(p, "p_sell"), "down") },
        { id: "pnone", label: "P(none)", num: true, val: (p) => f(p, "p_none"), cell: (p) => pct(f(p, "p_none")) },
        { id: "hist", label: "Sector history", num: true, val: (p) => (p.by_sector[sec] ? p.by_sector[sec].buys + p.by_sector[sec].sells : 0), cell: (p) => p.by_sector[sec] ? `${p.by_sector[sec].buys}B / ${p.by_sector[sec].sells}S <span class="sub">last ${esc(p.by_sector[sec].last || "")}</span>` : "<span class='muted'>none</span>" },
      ] : [
        { id: "spouse", label: "Spouse share", num: true, val: (p) => p.spouse_share, cell: (p) => pct(p.spouse_share) },
        { id: "top", label: "Most traded", val: (p) => p.top_tickers.length, cell: (p) => p.top_tickers.slice(0, 5).map((t) => `<span class="tk">${esc(t.ticker)}</span><span class="sub">×${t.n}</span>`).join(" ") },
        { id: "last", label: "Last filing", val: (p) => p.last_filing, cell: (p) => esc(p.last_filing || "") },
      ]),
      { id: "comm", label: "Committees", val: (p) => (p.committees || []).length, cell: (p) => `<span class="sub">${esc((p.committees || []).slice(0, 3).join("; "))}</span>` },
    ];
    table($("#pols-table"), cols, rows, "pols" + (sec ? ":s" : ""), showPol);
  }
  ["#q-pols", "#f-chamber", "#f-pol-sector"].forEach((s) => $(s).addEventListener("input", renderPoliticians));

  function showPol(id) {
    const p = state.politicians.find((x) => x.id === id);
    const box = $("#pol-detail");
    if (!p) { box.hidden = true; return; }
    const secs = Object.entries(p.forecasts).map(([sid, f]) => ({ sid, ...f, hist: p.by_sector[sid] })).sort((a, b) => (b.p_buy + b.p_sell) - (a.p_buy + a.p_sell));
    box.hidden = false;
    box.innerHTML = `<button class="close" id="pol-close">Close</button>
      <h3>${esc(p.name)}</h3><div class="sub">${esc(p.chamber)} · ${esc(p.party || "")} ${esc(p.state || "")} · ${p.n_transactions} disclosed transactions ${esc(p.first_filing || "")} to ${esc(p.last_filing || "")} · spouse share ${pct(p.spouse_share)}</div>
      <div class="sub">${esc((p.committees || []).join("; "))}</div>
      <div class="two">
        <div><h4>Forecast for the next ${state.summary.disclosure_window_days} days, by sector</h4>
          <table class="mini"><thead><tr><th>Sector</th><th class="num">P(buy)</th><th class="num">P(sell)</th><th class="num">P(none)</th><th class="num">History</th></tr></thead><tbody>
          ${secs.slice(0, 12).map((s) => `<tr><td>${esc(state.sectorName[s.sid] || s.sid)}${s.committee_relevant ? " <span class='sub'>committee</span>" : ""}</td><td class="num">${bar(s.p_buy, "up")}</td><td class="num">${bar(s.p_sell, "down")}</td><td class="num">${pct(s.p_none)}</td><td class="num">${s.hist ? `${s.hist.buys}B / ${s.hist.sells}S` : "<span class='muted'>none</span>"}</td></tr>`).join("")}
          </tbody></table></div>
        <div><h4>Most traded tickers</h4>
          <table class="mini"><tbody>${p.top_tickers.map((t) => `<tr><td><span class="tk">${esc(t.ticker)}</span></td><td class="num">${t.n}</td></tr>`).join("") || "<tr><td class='muted'>none</td></tr>"}</tbody></table>
          <p class="sub">Counts come from the person's own STOCK Act filings. Every probability is a model estimate of what will be publicly disclosed later, not knowledge of any trade.</p>
        </div>
      </div>`;
    $("#pol-close").addEventListener("click", () => (box.hidden = true));
    box.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  /* ------------------------------------------------------------ sectors */
  function renderSectors() {
    $("#sectors-list").innerHTML = state.sectors.map((s) => `<article class="card">
      <div class="card-head"><div><h3>${esc(s.name)}</h3><div class="kicker">${s.n_actions_recent} actions in the last ${state.summary.recent_action_days} days · ${s.n_actions_total.toLocaleString()} since ${state.summary.models && "2019"} · benchmark <b>${esc(s.benchmark)}</b></div></div>
        <div>${s.recent_direction == null ? "" : dirBadge(s.recent_direction > 0.15 ? 1 : s.recent_direction < -0.15 ? -1 : 0, s.recent_direction)}</div></div>
      <div class="sector-row"><div><div class="sub">Tickers</div>${s.tickers.map((t) => `<span class="tk">${esc(t)}</span>`).join(" ")}<div class="sub" style="margin-top:8px">Agencies watched</div><div class="why">${esc(s.agencies.join(", "))}</div></div>
        <div><div class="sub">Members who traded this sector (buys / sells)</div>${s.top_traders.length ? `<table class="mini"><tbody>${s.top_traders.map((p) => `<tr><td><a href="#politicians" class="pol-link" data-pol="${esc(p.id)}">${esc(p.name)}</a></td><td class="num">${p.buys}B / ${p.sells}S</td><td class="sub">${esc(p.last || "")}</td></tr>`).join("")}</tbody></table>` : "<div class='sub'>none since 2023</div>"}</div>
        <div><div class="sub">Committee codes treated as relevant</div><div class="why">${esc(s.committees.join(", "))}</div></div></div>
    </article>`).join("");
    $$("#sectors-list .pol-link").forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); $(`.tab[data-tab="politicians"]`).click(); showPol(a.dataset.pol); }));
  }

  /* ------------------------------------------------------------ model and data */
  function calib(rows) {
    if (!rows || !rows.length) return "<p class='sub'>No holdout calibration available.</p>";
    return `<table class="mini"><thead><tr><th>Predicted band</th><th class="num">n</th><th class="num">Mean predicted</th><th class="num">Observed</th></tr></thead><tbody>${rows.map((r) => `<tr><td>${(100 * r.p_low).toFixed(1)}% to ${(100 * r.p_high).toFixed(1)}%</td><td class="num">${r.n}</td><td class="num">${(100 * r.mean_predicted).toFixed(1)}%</td><td class="num">${(100 * r.observed).toFixed(1)}%</td></tr>`).join("")}</tbody></table>`;
  }
  function renderModel() {
    const s = state.summary, c = s.counts, d = s.models.disclosure, o = s.models.outperformance;
    const kv = (obj) => `<div class="kv">${Object.entries(obj).map(([k, v]) => `<div><div class="k">${esc(k)}</div><div class="v">${esc(v == null ? "n/a" : typeof v === "number" ? v.toLocaleString() : v)}</div></div>`).join("")}</div>`;
    const dm = d.metrics || {};
    $("#model-content").innerHTML = `
      <h2>What WhiteRock does</h2>
      <p>It reads four kinds of public records, links each government action to the sectors and companies it plausibly touches, and asks two questions: will a member of Congress disclose a related purchase or sale in the next ${s.disclosure_window_days} days, and will the affected stocks beat their sector benchmark over ${s.horizons.join(", ")} trading days. Everything on this page is a forecast about future public disclosures and future prices. Nothing here sees a trade before its owner files it.</p>
      <h2>Data right now</h2>
      ${kv({ "Federal Register documents": c.documents, "Linked to a sector": c.linked_documents, "Recent actions": c.recent_actions, "Forthcoming (public inspection)": c.forthcoming, "Disclosed transactions": c.transactions, "In the ticker universe": c.transactions_in_universe, "People with trades": c.politicians_with_trades, "Outperformance events": c.outperformance_events, "Latest price": c.latest_price_date })}
      <h3>Sources</h3>
      <table class="mini"><thead><tr><th>Source</th><th>Status</th><th class="num">Records</th></tr></thead><tbody>
      ${Object.values(s.sources).map((x) => `<tr><td>${x.url ? `<a href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.name)}</a>` : esc(x.name)}</td><td class="${x.ok ? "ok" : "bad"}">${esc(x.detail)}${x.filings_indexed != null ? ` <span class="sub">(${x.filings_indexed} filings indexed, ${x.filings_paper_unparsed} paper filings not parsed)</span>` : ""}</td><td class="num">${x.count == null ? "" : x.count.toLocaleString()}</td></tr>`).join("")}
      </tbody></table>
      <h2>Model 1: disclosure likelihood</h2>
      <p>Unit: one member, one sector, one monthly reference date. Label: what that member disclosed in that sector during the following ${s.disclosure_window_days} days (none, purchase-dominant, or sale-dominant). Features are computed strictly from records filed before the reference date: the member's own trading history in the sector and overall, days since the last trade, committee relevance, spouse share, and the count and direction of government actions in the sector during the prior 30 and 90 days. Model: gradient-boosted trees, multinomial. Evaluation: a time-based holdout starting ${esc(dm.holdout_start || "n/a")}. Members with no disclosed trades since 2023 are not scored; their history is the honest answer.</p>
      ${kv({ "Train rows": d.metrics && d.metrics.note ? d.metrics.note : "", "Log loss (holdout)": dm.log_loss, "Log loss, base rate": dm.log_loss_base_rate, "AUC buy": dm.auc_buy, "AUC sell": dm.auc_sell, "Brier buy": dm.brier_buy, "Brier buy, base rate": dm.brier_buy_base_rate, "Brier sell": dm.brier_sell, "Brier sell, base rate": dm.brier_sell_base_rate })}
      <div class="two"><div><h3>Calibration: purchase</h3>${calib(dm.calibration_buy)}</div><div><h3>Calibration: sale</h3>${calib(dm.calibration_sell)}</div></div>
      <p class="sub">Base rates in training: ${Object.entries(d.base_rates || {}).map(([k, v]) => `${k} ${(100 * v).toFixed(1)}%`).join(", ")}.</p>
      <h2>Model 2: benchmark outperformance</h2>
      <p>Unit: one sector-day of government action, one ticker in that sector. Label: the ticker's log excess return over its sector ETF from the first close on or after publication to ${s.horizons.join(", ")} trading days later is positive. Features: the rule-based direction and relevance, document mix, sector identity, trailing 30-day action intensity, and the ticker's own 20 and 60 day excess momentum. Model: regularized logistic regression per horizon. Holdout: the most recent twelve months of events. Markets price public actions fast, so expect AUC only modestly above 0.5. The numbers below are shown so nobody over-reads a probability.</p>
      ${s.horizons.map((h) => { const m = o[String(h)]; if (!m) return `<h3>${h} trading days</h3><p class="sub">Not enough events to train.</p>`; return `<h3>${h} trading days</h3>${kv({ "Events (train)": m.n_train, "Events (holdout)": m.n_holdout, "AUC": m.auc, "Brier": m.brier, "Brier, base rate": m.brier_base_rate, "Base rate": m.base_rate_holdout, "Mean excess when P>55%": m.mean_excess_when_confident_up == null ? null : (100 * m.mean_excess_when_confident_up).toFixed(2) + "%", "Mean excess when P<45%": m.mean_excess_when_confident_down == null ? null : (100 * m.mean_excess_when_confident_down).toFixed(2) + "%" })}${calib(m.calibration)}`; }).join("")}
      <h2>How actions are linked to sectors</h2>
      <p>Each Federal Register document (final rules, proposed rules, presidential documents, and the public-inspection desk for forthcoming items) is matched to sectors by issuing agency and by keyword rules that are listed on the Sectors tab. Direction comes from sector-specific patterns first (for example, a tariff is a benefit for domestic steel and a cost for retailers), then a generic lexicon. The matched terms are printed under every link so the reasoning can be checked and corrected.</p>
      <h2>Limits</h2>
      <ul>
        <li>Executive-branch officials, including the President, file OGE Forms 278e and 278-T as PDFs behind a search form. This version has no automated crawler for them; a hand-maintained ledger with source links is supported and is currently ${c.transactions === 0 ? "empty" : "shown where present"}.</li>
        <li>Paper-filed congressional reports are scanned images and are counted but not parsed.</li>
        <li>Ticker mapping relies on the ticker printed in the filing. Funds, bonds and private assets are kept in the record but do not map to a sector.</li>
        <li>Legislation (bills, markups, hearings) is not yet ingested; the Federal Register covers executive and regulatory actions.</li>
        <li>All probabilities are estimates with the holdout error shown above. Do not treat them as recommendations.</li>
      </ul>
      <p class="sub">WhiteRock v${esc(s.version)}, generated ${esc(s.generated_at)}.</p>`;
  }

  load();
})();
