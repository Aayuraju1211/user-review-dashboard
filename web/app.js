/* User Review Dashboard. Reads data/dashboard.json (built by backend/build.py) and renders
   five pages: Home (dashboard), Feedback and Support and Reviews (lists), Breakdowns (reporting).
   Every row opens the same slide-over detail panel. */
"use strict";

/* ------------------------------------------------------------------ state */
let D = null;
const S = {
  tab: "fix", q: "", f: {}, pop: false,
  group: "area", bperiod: "", chart: "rating",
  rview: "all", rfilter: {}, rchips: [], rpage: 0,
  spikes: true, lastFocus: null, update: 0,
};
try { const v = localStorage.getItem("av-spikes"); if (v !== null) S.spikes = v === "1"; } catch (e) { /* storage unavailable */ }

const PAGE_SIZE = 25;
const $ = s => document.querySelector(s);
const V = () => D.views[S.spikes ? "all" : "no_spikes"];
const REV = {};
const themeMap = () => Object.fromEntries(V().themes.map(t => [t.id, t]));
const areaName = k => (D.taxonomy.areas[k] || k);

/* ------------------------------------------------------------------ formatting */
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const MON_LONG = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fd = s => { if (!s) return "Unknown"; const [y, m, d] = s.split("-"); return `${+d} ${MON[+m - 1]} ${y}`; };
const fm = s => { const [y, m] = s.split("-"); return `${MON[+m - 1]} ${y.slice(2)}`; };
const fmLong = s => { const [y, m] = s.split("-"); return `${MON_LONG[+m - 1]} ${y}`; };
const plural = (n, w, p) => `${n} ${n === 1 ? w : (p || w + "s")}`;
const stars = n => `${n} star${n === 1 ? "" : "s"}`;
function replyIn(h) {
  if (h == null) return "Replied";
  if (h < 1) return `Replied in ${Math.max(1, Math.round(h * 60))} min`;
  if (h < 48) return `Replied in ${Math.round(h)} h`;
  return `Replied in ${Math.round(h / 24)} days`;
}
function toast(m) { const t = $("#toast"); t.textContent = m; t.hidden = false; clearTimeout(toast.t); toast.t = setTimeout(() => t.hidden = true, 2800); }

/* Highlight evidence inside text, tolerant of case, spacing and curly quotes. */
function evRegex(ev) {
  const src = ev.trim().replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/\s+/g, "\\s+")
    .replace(/['’‘]/g, "['’‘]").replace(/["“”]/g, '["“”]');
  return new RegExp(src, "i");
}
function markAll(text, evs) {
  const hits = [];
  (evs || []).forEach(ev => { if (!ev) return; const m = evRegex(ev).exec(text); if (m) hits.push([m.index, m.index + m[0].length]); });
  hits.sort((a, b) => a[0] - b[0]);
  let out = "", pos = 0;
  hits.forEach(([s, e]) => { if (s < pos) return; out += esc(text.slice(pos, s)) + "<mark>" + esc(text.slice(s, e)) + "</mark>"; pos = e; });
  return out + esc(text.slice(pos));
}
function excerpt(text, ev, max = 220) {
  if (text.length <= max) return markAll(text, [ev]);
  const m = ev ? evRegex(ev).exec(text) : null;
  let s = 0;
  if (m) s = Math.max(0, m.index - Math.floor((max - m[0].length) / 2));
  let e = Math.min(text.length, s + max);
  s = Math.max(0, e - max);
  // Snap to word boundaries so excerpts never start or end mid-word.
  if (s > 0) { const sp = text.indexOf(" ", s); if (sp > -1 && (!m || sp < m.index)) s = sp + 1; }
  if (e < text.length) { const sp = text.lastIndexOf(" ", e); if (sp > s && (!m || sp >= m.index + m[0].length)) e = sp; }
  const cut = text.slice(s, e);
  return (s > 0 ? "…" : "") + markAll(cut, [ev]) + (e < text.length ? "…" : "");
}

/* ------------------------------------------------------------------ periods
   Stored as "" (all time), "14" / "30" / "90" / "180" (days up to the data date), or "custom:YYYY-MM-DD:YYYY-MM-DD".
   Relative periods count back from the date the data was built, not the viewer's clock. */
const PERIOD_OPTS = [["", "All time"], ["14", "Last 2 weeks"], ["30", "Last 30 days"], ["90", "Last 90 days"], ["180", "Last 6 months"], ["custom", "Custom range"]];
const isoMinus = days => new Date(Date.parse(D.meta.as_of) - days * 864e5).toISOString().slice(0, 10);
function periodBounds(p) {
  if (!p) return null;
  if (p.startsWith("custom:")) { const [, a, b] = p.split(":"); return [a, b]; }
  return [isoMinus(+p), D.meta.as_of];
}
const inPeriod = (date, p) => { const b = periodBounds(p); return !b || (date >= b[0] && date <= b[1]); };
function periodLabel(p) {
  if (!p) return "All time";
  if (p.startsWith("custom:")) { const [, a, b] = p.split(":"); return `${fd(a)} to ${fd(b)}`; }
  return (PERIOD_OPTS.find(o => o[0] === p) || ["", p])[1];
}
function periodField(val, idp, label = "Period", extra = "") {
  const isCustom = (val || "").startsWith("custom:"), [, a, b] = isCustom ? val.split(":") : [];
  const min = D.views.all.totals.first_date, max = D.meta.as_of;
  return `<div class="field"><label for="${idp}">${label}</label><select id="${idp}" data-period>${PERIOD_OPTS.map(([v, l]) => `<option value="${v}" ${(isCustom ? "custom" : (val || "")) === v ? "selected" : ""}>${l}</option>`).join("")}</select></div>
  <div class="range-row" data-custom-for="${idp}" ${isCustom ? "" : "hidden"}><div class="field"><label for="${idp}From">From</label><input type="date" id="${idp}From" min="${min}" max="${max}" value="${a || ""}"></div><div class="field"><label for="${idp}To">To</label><input type="date" id="${idp}To" min="${min}" max="${max}" value="${b || ""}"></div>${extra}</div>`;
}
function readPeriod(idp) {
  const sel = document.getElementById(idp);
  if (!sel || sel.value !== "custom") return { ok: true, value: sel ? sel.value : "" };
  const a = document.getElementById(idp + "From").value, b = document.getElementById(idp + "To").value;
  if (!a || !b) return { ok: false, msg: "Choose both a start date and an end date." };
  if (a > b) return { ok: false, msg: "The start date needs to be on or before the end date." };
  return { ok: true, value: `custom:${a}:${b}` };
}

/* ------------------------------------------------------------------ small components */
const chevron = `<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><path d="M6 3.5 10.5 8 6 12.5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
const searchIcon = `<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><circle cx="7" cy="7" r="4.5" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="m10.5 10.5 3 3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>`;
const closeBtn = `<button class="icon-btn" data-close aria-label="Close panel"><svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true"><path d="m4.5 4.5 9 9m0-9-9 9" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg></button>`;

function quoteHTML(reviewId, ev, flag) {
  const r = REV[reviewId]; if (!r) return "";
  return `<div class="quote"><p>“${excerpt(r.text, ev)}”</p><span class="by">${esc(r.who)}, ${stars(r.stars)}, ${fd(r.date)}, ${esc(r.store)}${flag ? `. <span class="crit">${esc(flag)}</span>` : ""}</span></div>`;
}
function change(t) {
  if (t.prev90 === 0 && t.cur90 > 0 && t.new) return `<span class="chg"><span class="newtag">New</span><small>first seen ${fd(t.first)}</small></span>`;
  if (t.cur90 === t.prev90) return `<span class="chg">${t.cur90}<small>same as before</small></span>`;
  return `<span class="chg">${t.cur90}<small>${t.cur90 > t.prev90 ? "up" : "down"} from ${t.prev90}</small></span>`;
}
function spark(vals, color) {
  const W = 240, H = 36, max = Math.max(1, ...vals), x = i => i / Math.max(1, vals.length - 1) * (W - 6) + 3, y = v => H - 4 - (v / max) * (H - 10);
  const d = vals.map((v, i) => (i ? "L" : "M") + x(i).toFixed(1) + "," + y(v).toFixed(1)).join(""), last = vals.length - 1;
  return `<svg class="spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true"><path d="${d}L${x(last)},${H}L${x(0)},${H}Z" fill="${color}" opacity=".08"/><path d="${d}" fill="none" stroke="${color}" stroke-width="2" vector-effect="non-scaling-stroke" stroke-linejoin="round"/><circle cx="${x(last)}" cy="${y(vals[last])}" r="3" fill="${color}"/></svg>`;
}
function searchBox(label) {
  return `<label class="search">${searchIcon}<span class="lab">Search</span><input id="q" type="search" value="${esc(S.q)}" aria-label="${esc(label)}" autocomplete="off"><kbd>/</kbd></label>`;
}
function pager(total, page, size) {
  if (!size) return `<div class="pager"><span>Showing ${total ? 1 : 0} to ${total} of ${total}</span><div class="btns"><button class="btn" disabled>Previous</button><button class="btn" disabled>Next</button></div></div>`;
  const from = total ? page * size + 1 : 0, to = Math.min(total, (page + 1) * size);
  return `<div class="pager"><span>Showing ${from} to ${to} of ${total}</span><div class="btns"><button class="btn" data-pg="-1" ${page === 0 ? "disabled" : ""}>Previous</button><button class="btn" data-pg="1" ${to >= total ? "disabled" : ""}>Next</button></div></div>`;
}
function filtersPopover(fields, activeCount) {
  return `<div class="filters-wrap"><button class="btn" id="filtersBtn" aria-expanded="${S.pop}" aria-controls="filtersPop">Filters${activeCount ? ` (${activeCount})` : ""}</button>
    <div class="popover" id="filtersPop" role="dialog" aria-label="Filters" ${S.pop ? "" : "hidden"}>
      ${fields.map(([id, label, opts, val]) => id === "period" ? periodField(val, "fperiod") : `<div class="field"><label for="${id}">${label}</label><select id="${id}" data-fkey="${id}"><option value="">Any</option>${opts.map(([v, l]) => `<option value="${esc(v)}" ${String(val) === String(v) ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></div>`).join("")}
      <div class="pop-actions"><button class="btn" data-clear="1">Clear filters</button><button class="btn primary" id="applyF">Apply filters</button></div></div></div>`;
}
function chipsRow(chips) {
  if (!chips.length) return "";
  return `<div class="chips">${chips.map(([k, label]) => `<span class="chip">${esc(label)}<button data-unchip="${esc(k)}" aria-label="Remove filter ${esc(label)}">×</button></span>`).join("")}<button class="linkbtn" data-clear="1">Clear all</button></div>`;
}
/* ---- Copy as ticket: a plain Markdown summary any tracker accepts (Jira, Linear, ClickUp, GitHub) */
const plainText = html => { const d = document.createElement("div"); d.innerHTML = html; return d.textContent; };
function ticketMarkdown(id) {
  const t = themeMap()[id]; if (!t) return "";
  const kind = { problem: "Problem to fix", request: "Request to build", praise: "What users love" }[t.kind];
  const detail = t.kind === "problem" ? `${t.severity}. ${t.type}: ${t.sub.toLowerCase()}` : t.kind === "request" ? `${t.priority}. ${t.rtype}` : `Loved for ${t.reason.toLowerCase()}`;
  const lines = [
    `## ${t.name}`, "",
    `**Type:** ${kind}. ${detail}`,
    `**Product area:** ${areaName(t.area)}`,
    `**Reviews:** ${t.n} (last 90 days: ${t.cur90}, the 90 days before: ${t.prev90})`,
    `**First seen:** ${fd(t.first)}`,
    `**Last seen:** ${fd(t.last)}`,
    `**Average rating of these reviews:** ${t.avg_rating.toFixed(1)} out of 5` + (t.kind !== "praise" ? ` (${t.unhappy_pct}% from 1 to 3 star reviews)` : ""),
    "", `**Description:** ${t.definition || ""}`, "", "**What users say:**",
  ];
  t.quotes.forEach(q => { const r = REV[q.review_id]; if (r) lines.push(`> "${plainText(excerpt(r.text, q.evidence)).replace(/\s+/g, " ")}"`, ">", `> ${r.who}, ${stars(r.stars)}, ${fd(r.date)}, ${r.store}`, ""); });
  const phr = (t.phrasings || []).filter(p => !t.quotes.some(q => q.evidence === p));
  if (phr.length) { lines.push("**Also phrased as:**"); phr.forEach(p => lines.push(`- "${p}"`)); lines.push(""); }
  lines.push(`_Source: SuperKalam User Review Dashboard, data as of ${fd(D.meta.as_of)}._`);
  return lines.join("\n");
}
function copyText(text, okMsg) {
  const fallback = () => {
    const ta = document.createElement("textarea"); ta.value = text; ta.setAttribute("readonly", ""); ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.select();
    let ok = false; try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    ta.remove(); toast(ok ? okMsg : "Couldn't copy here. Use Download CSV instead.");
  };
  if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(text).then(() => toast(okMsg), fallback);
  else fallback();
}
function download(name, rows) {
  const csv = rows.map(r => r.map(v => `"${String(v ?? "").replace(/"/g, '""')}"`).join(",")).join("\r\n");
  const url = URL.createObjectURL(new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8" }));
  const a = document.createElement("a"); a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  toast(`Downloaded ${plural(rows.length - 1, "row")}`);
}

/* ------------------------------------------------------------------ chart */
function trendChart() {
  const M = V().monthly, W = 760, H = 230, L = 40, R = 12, T = 16, B = 32, iw = W - L - R, ih = H - T - B, bw = iw / M.length;
  let g = "", body = "", lbl = "";
  const step = Math.max(1, Math.ceil(M.length / 6));
  M.forEach((m, i) => { if (i % step === 0 || i === M.length - 1) lbl += `<text x="${L + i * bw + bw / 2}" y="${H - 8}" text-anchor="middle">${fm(m.month)}</text>`; });
  if (S.chart === "rating") {
    const lo = Math.min(3.5, Math.floor(Math.min(...M.map(m => m.avg_rating)) * 2) / 2), hi = 5, y = v => T + ih - (v - lo) / (hi - lo) * ih;
    for (let v = lo; v <= hi + 1e-9; v += 0.5) g += `<line class="grid" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/><text x="${L - 8}" y="${y(v) + 4}" text-anchor="end">${v.toFixed(1)}</text>`;
    const pts = M.map((m, i) => [L + i * bw + bw / 2, y(m.avg_rating)]);
    body += `<path d="${pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + "," + p[1].toFixed(1)).join("")}" fill="none" stroke="var(--s-line)" stroke-width="2" stroke-linejoin="round"/>`;
    M.forEach((m, i) => { body += `<circle cx="${pts[i][0]}" cy="${pts[i][1]}" r="4" fill="var(--s-line)" stroke="var(--l1)" stroke-width="2"/><rect class="hit" x="${L + i * bw}" y="${T}" width="${bw}" height="${ih}" data-tip="${fm(m.month)}: ${m.avg_rating.toFixed(2)} average from ${plural(m.reviews, "written review")}"/>`; });
    const low = M.reduce((a, m, i) => m.avg_rating < M[a].avg_rating ? i : a, 0);
    body += `<text x="${pts[low][0]}" y="${Math.min(H - 26, pts[low][1] + 20)}" text-anchor="middle" style="fill:var(--ink-2)">Lowest: ${M[low].avg_rating.toFixed(2)}</text>`;
  } else {
    const max = Math.ceil(Math.max(...M.map(m => m.reviews)) / 20) * 20 || 20, y = v => T + ih - v / max * ih;
    for (let v = 0; v <= max; v += max / 4) g += `<line class="${v ? "grid" : "axis"}" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/><text x="${L - 8}" y="${y(v) + 4}" text-anchor="end">${Math.round(v)}</text>`;
    M.forEach((m, i) => { const x = L + i * bw + bw * .2, w = bw * .6, top = y(m.reviews); body += `<path d="M${x},${y(0)}V${Math.min(y(0), top + 4)}Q${x},${top} ${x + 4},${top}H${x + w - 4}Q${x + w},${top} ${x + w},${Math.min(y(0), top + 4)}V${y(0)}Z" fill="var(--s-line)"/><rect class="hit" x="${L + i * bw}" y="${T}" width="${bw}" height="${ih}" data-tip="${fm(m.month)}: ${plural(m.reviews, "review")}${m.spike_reviews ? `, ${m.spike_reviews} on spike days` : ""}"/>`; });
  }
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${S.chart === "rating" ? "Average rating of written reviews per month" : "Reviews per month"}">${g}${body}${lbl}</svg>`;
}

/* ------------------------------------------------------------------ pages */
const NAV = [["home", "Home"], ["feedback", "Feedback"], ["breakdowns", "Breakdowns"], ["support", "Support"], ["reviews", "Reviews"]];
function renderNav(active) {
  const waiting = D.critical.filter(c => c.reply === "No reply").length;
  $("#nav").innerHTML = NAV.map(([id, l]) => `<a href="#${id}" ${active === id ? 'aria-current="page"' : ""}>${l}${id === "support" && waiting ? `<span class="count crit" aria-label="${waiting} without a reply">${waiting}</span>` : ""}</a>`).join("");
}

function pHome() {
  const v = V(), th = v.themes, tot = v.totals, u = v.updates[0], M = v.monthly.slice(-16);
  const byKind = k => th.filter(t => t.kind === k);
  const fix = byKind("problem"), build = byKind("request"), protect = byKind("praise");
  const newCount = a => a.filter(t => t.new).length;
  const waiting = D.critical.filter(c => c.reply === "No reply");
  const maxWait = Math.max(0, ...waiting.map(c => c.waiting_days || 0));
  const tile = (href, lbl, big, unit, sub, sp, color, top, more, cls = "") => `<a class="card kpi ${cls}" href="${href}"><span class="lbl">${lbl}</span><span class="big"><b class="num">${big}</b><span>${unit}</span></span><span class="sec">${sub}</span>${spark(sp, color)}<span class="top">${top}</span><span class="more">${more}</span></a>`;
  const areas = v.breakdowns.all.area.filter(a => a.problems > 0).sort((a, b) => b.problems - a.problems).slice(0, 6);
  const mx = Math.max(1, ...areas.map(a => a.problems));
  const notable = (u.notable || []).map(id => { const r = REV[id]; return r ? quoteHTML(id, (r.critical && r.critical.evidence) || (r.mentions[0] && r.mentions[0].evidence), r.critical ? "Critical" : "") : ""; }).join("");
  const delta = u.prev_avg_rating != null && u.avg_rating != null ? `, was ${u.prev_avg_rating.toFixed(2)}` : "";
  const unl = D.meta.unlabelled ? `<p class="note box">${plural(D.meta.unlabelled, "new review")} ${D.meta.unlabelled === 1 ? "is" : "are"} waiting to be sorted into themes. ${D.meta.unlabelled === 1 ? "It counts" : "They count"} in the rating but not yet in problems or requests.</p>` : "";
  return `<header class="phead"><div class="t"><h1>SuperKalam reviews</h1><p>What Google Play and App Store reviewers say, from ${fmLong(tot.first_date)} to ${fmLong(tot.last_date)}. Updated ${fd(D.meta.as_of)}.</p>${S.spikes ? "" : `<span class="sample">Spike-day reviews are left out of every number. Change this in Reviews.</span>`}</div></header>
  ${unl}
  <section class="card update" aria-label="Since the last update"><b>Since the last update, ${esc(u.label)}</b><div class="facts"><span><strong>${u.reviews}</strong> new ${u.reviews === 1 ? "review" : "reviews"}</span>${u.avg_rating != null ? `<span>Written-review rating <strong>${u.avg_rating.toFixed(2)}</strong>${delta}</span>` : ""}<span><strong>${u.critical}</strong> critical</span></div><button class="btn go" data-open="update">See what changed</button></section>
  <section class="kpis" aria-label="Summary">
    ${tile("#feedback-fix", "To fix", fix.length, "problems", `${newCount(fix)} new in the last 2 weeks`, M.map(m => m.problem), "var(--s-prob)", fix[0] ? `Largest: <b>${esc(fix[0].name)}</b>, ${plural(fix[0].n, "review")}` : "No problems reported", "View all problems")}
    ${tile("#feedback-build", "To build", build.length, "requests", `${newCount(build)} new in the last 2 weeks`, M.map(m => m.request), "var(--s-req)", build[0] ? `Largest: <b>${esc(build[0].name)}</b>, ${plural(build[0].n, "review")}` : "No requests yet", "View all requests")}
    ${tile("#feedback-protect", "To protect", protect.length, "loved features", `${plural(M.length ? M[M.length - 1].praise : 0, "review")} with praise this month`, M.map(m => m.praise), "var(--s-praise)", protect[0] ? `Most loved: <b>${esc(protect[0].name)}</b>` : "", "View what's working")}
    ${tile("#support", "Critical reviews", D.critical.length, "all time", waiting.length ? `<span class="new">${waiting.length} waiting ${maxWait} days for a reply</span>` : "All answered or on the App Store", M.map(m => m.critical), "var(--crit)", "Paid access, charges, lockouts and failed support", "Open support queue", waiting.length ? "crit" : "")}
  </section>
  <section class="card pad"><div class="card-h"><h2>How reviewers rate the app</h2><div class="seg" role="group" aria-label="Chart measure"><button aria-pressed="${S.chart === "rating"}" data-chart="rating">Written-review rating</button><button aria-pressed="${S.chart === "reviews"}" data-chart="reviews">Reviews per month</button></div></div><div class="chartwrap">${trendChart()}</div><p class="meta" style="margin-top:var(--sp-3)">Based on written reviews only. The store rating also counts star-only ratings, which have no text to analyse, so it can differ.</p></section>
  <div class="grid-2">
    <section class="card pad"><div class="card-h"><h2>Where problems come from</h2><a href="#breakdowns" class="sec">See breakdowns</a></div><div class="bars">${areas.map(a => `<a class="bar" href="#feedback-fix" data-area="${esc(a.key)}"><span>${esc(a.label)}</span><span class="track"><span class="fill" style="display:block;width:${a.problems / mx * 100}%"></span></span><span class="v num">${a.problems}</span></a>`).join("")}</div></section>
    <section class="card pad"><div class="card-h"><h2>Reviews worth reading</h2><a href="#reviews" class="sec">All reviews</a></div>${notable || `<p class="sec">No new reviews in the last two weeks.</p>`}</section>
  </div>`;
}

function feedbackRows() {
  const kind = { fix: "problem", build: "request", protect: "praise" }[S.tab];
  const q = S.q.trim().toLowerCase();
  return themesInPeriod(S.f.period).filter(t => t.kind === kind)
    .filter(t => !q || t.name.toLowerCase().includes(q) || areaName(t.area).toLowerCase().includes(q) || (t.phrasings || []).some(p => p.toLowerCase().includes(q)))
    .filter(t => !S.f.area || t.area === S.f.area).filter(t => !S.f.sev || t.severity === S.f.sev)
    .filter(t => !S.f.pri || t.priority === S.f.pri);
}
/* Themes with review counts (pn) limited to a period; themes with no reviews in the period drop out. */
function themesInPeriod(p) {
  return V().themes.map(t => ({ ...t, pn: p ? t.review_ids.filter(id => REV[id] && inPeriod(REV[id].date, p)).length : t.n }))
    .filter(t => t.pn > 0).sort((a, b) => b.pn - a.pn || a.name.localeCompare(b.name));
}
function pFeedback() {
  const kind = { fix: "problem", build: "request", protect: "praise" }[S.tab];
  const all = V().themes.filter(t => t.kind === kind), rows = feedbackRows(), inP = themesInPeriod(S.f.period);
  const counts = { fix: inP.filter(t => t.kind === "problem").length, build: inP.filter(t => t.kind === "request").length, protect: inP.filter(t => t.kind === "praise").length };
  const areas = [...new Set(all.map(t => t.area))].sort().map(a => [a, areaName(a)]);
  const fields = [["area", "Product area", areas, S.f.area || ""]];
  if (S.tab === "fix") fields.push(["sev", "Severity", [["Blocking", "Blocking"], ["Annoying", "Annoying"]], S.f.sev || ""]);
  if (S.tab === "build") fields.push(["pri", "Priority", [["Deal-breaker", "Deal-breaker"], ["Nice-to-have", "Nice-to-have"], ["Too few to tell", "Too few to tell"]], S.f.pri || ""]);
  const chips = [S.f.area && ["area", "Area: " + areaName(S.f.area)], S.f.sev && ["sev", "Severity: " + S.f.sev], S.f.pri && ["pri", "Priority: " + S.f.pri]].filter(Boolean);
  const rcol = S.f.period ? "Reviews in period" : "Reviews";
  const cols = S.tab === "fix" ? ["Problem", "Product area", rcol, "Last 90 days", "Severity", "Last seen"] : S.tab === "build" ? ["Request", "Product area", rcol, "Last 90 days", "Priority", "Last seen"] : ["What users love", "Product area", rcol, "Last 90 days", "Main reason"];
  const desc = { fix: "Complaints users raise, whatever their star rating. Each problem is counted once, even when users also ask for a fix.", build: "New features and offerings users ask for. Requests that are also complaints, like Hindi-medium content, are listed under Fix.", protect: "What users praise most. Keep these working when you change the product." }[S.tab];
  const body = rows.map(t => `<tr class="row" data-theme="${t.id}" tabindex="0"><td><button class="rowlink" data-theme="${t.id}">${esc(t.name)}${chevron}</button></td><td>${esc(areaName(t.area))}</td><td class="r num">${t.pn}</td><td>${change(t)}</td>${S.tab === "fix" ? `<td><span class="sev ${t.severity.toLowerCase()}"><i aria-hidden="true"></i>${t.severity}</span></td><td class="num">${fd(t.last)}</td>` : S.tab === "build" ? `<td>${esc(t.priority)}</td><td class="num">${fd(t.last)}</td>` : `<td>${esc(t.reason)}</td>`}</tr>`).join("");
  return `<header class="phead"><div class="t"><h1>Feedback</h1><p>${desc}</p></div></header>
  <div class="seg" role="tablist" aria-label="Feedback type">${[["fix", "Fix"], ["build", "Build"], ["protect", "Protect"]].map(([id, l]) => `<button role="tab" aria-selected="${S.tab === id}" data-tab="${id}">${l}<span class="n">${counts[id]}</span></button>`).join("")}</div>
  <div class="toolbar end"><div class="brange">${periodField(S.f.period || "", "fbperiod", "Period", `<button class="btn primary" id="applyFbRange">Apply range</button>`)}</div>${searchBox("Search " + (S.tab === "fix" ? "problems" : S.tab === "build" ? "requests" : "praise"))}${filtersPopover(fields, chips.length)}</div>
  ${chipsRow(chips)}
  <div class="tframe"><div class="tscroll"><table><thead><tr>${cols.map((c, i) => `<th scope="col" class="${i === 2 ? "r" : ""}" ${i === 2 ? 'aria-sort="descending"' : ""}>${c}${i === 2 ? " ↓" : ""}</th>`).join("")}</tr></thead><tbody>${body || `<tr><td colspan="${cols.length}"><div class="empty"><b>No matches</b><span class="sec">Nothing matches this search or these filters.</span><button class="btn" data-clear="1">Clear filters</button></div></td></tr>`}</tbody></table></div>${pager(rows.length)}</div>`;
}

/* Recompute breakdowns in the browser so any date range works. Mirrors backend/build.py breakdowns(). */
function breakdownRows() { return D.reviews.filter(r => r.labelled && (S.spikes || !r.spike) && inPeriod(r.date, S.bperiod)); }
function computeBreakdown(rows) {
  const tm = themeMap(), key = { problem: "problems", request: "requests", praise: "praise" };
  const groups = { area: new Map(), version: new Map(), segment: new Map() };
  const add = (m, k, label, r, kinds) => {
    let g = m.get(k); if (!g) m.set(k, g = { key: k, label, reviews: 0, problems: 0, requests: 0, praise: 0, sum: 0, ids: [] });
    g.reviews++; g.sum += r.stars; g.ids.push(r.id); kinds.forEach(x => g[key[x]]++);
  };
  rows.forEach(r => {
    const ts = r.themes.map(t => tm[t]).filter(Boolean), allKinds = new Set(ts.map(t => t.kind));
    [...new Set(ts.map(t => t.area))].forEach(a => add(groups.area, a, areaName(a), r, new Set(ts.filter(t => t.area === a).map(t => t.kind))));
    const v = r.version || "Unknown"; add(groups.version, v, v === "Unknown" ? "Unknown version" : "v" + v, r, allKinds);
    r.segments.forEach(s => add(groups.segment, s, D.taxonomy.segments[s], r, allKinds));
  });
  const fin = m => [...m.values()].map(g => ({ ...g, avg_rating: g.reviews ? g.sum / g.reviews : null }));
  const vk = k => k === "Unknown" ? [-1] : k.split(".").map(Number);
  const byVersion = (a, b) => { const x = vk(a.key), y = vk(b.key); for (let i = 0; i < Math.max(x.length, y.length); i++) { const d = (y[i] || 0) - (x[i] || 0); if (d) return d; } return 0; };
  const n = rows.length;
  return {
    area: fin(groups.area), version: fin(groups.version).sort(byVersion), segment: fin(groups.segment),
    totals: { reviews: n, avg_rating: n ? rows.reduce((a, r) => a + r.stars, 0) / n : null, mixed_or_negative_pct: n ? Math.round(100 * rows.filter(r => r.sentiment === "mixed" || r.sentiment === "negative").length / n) : 0 },
  };
}
/* ---- Drill-down: click a breakdown bar or row to see the statements behind the number */
const KIND_LABEL = { problem: "Problems", request: "Requests", praise: "Praise" };
function drillLabel(key) { return S.group === "area" ? areaName(key) : S.group === "version" ? (key === "Unknown" ? "Unknown version" : "Version " + key) : D.taxonomy.segments[key]; }
function drillReviews(key) {
  const tm = themeMap();
  return breakdownRows().filter(r => S.group === "area" ? r.themes.some(t => tm[t] && tm[t].area === key) : S.group === "version" ? (r.version || "Unknown") === key : r.segments.includes(key));
}
// A mention counts for the drill if its theme is in scope (for areas: the theme must belong to that area).
function drillInScope(key, tm) { return m => tm[m.theme] && (S.group !== "area" || tm[m.theme].area === key); }
function drillCount(rows, key, kind, tm) { const ok = drillInScope(key, tm); return rows.filter(r => r.mentions.some(m => ok(m) && tm[m.theme].kind === kind)).length; }
function firstKind(key) { const tm = themeMap(), rows = drillReviews(key); return ["problem", "request", "praise"].find(k => drillCount(rows, key, k, tm) > 0) || "problem"; }
function drillPanel() {
  const { key, kind } = S.drill, tm = themeMap(), rows = drillReviews(key), ok = drillInScope(key, tm);
  const groups = new Map();
  rows.forEach(r => {
    const seen = new Set();
    r.mentions.filter(m => ok(m) && tm[m.theme].kind === kind).forEach(m => {
      if (seen.has(m.theme)) return; seen.add(m.theme);
      if (!groups.has(m.theme)) groups.set(m.theme, []);
      groups.get(m.theme).push({ r, ev: m.evidence });
    });
  });
  const themes = [...groups.entries()].sort((a, b) => b[1].length - a[1].length || tm[a[0]].name.localeCompare(tm[b[0]].name));
  themes.forEach(([, items]) => items.sort((a, b) => b.r.date.localeCompare(a.r.date)));
  const n = drillCount(rows, key, kind, tm), noun = { problem: "problem", request: "request", praise: "praise" }[kind];
  return `<div class="over-h"><div class="t"><span class="meta">Breakdown, ${esc(periodLabel(S.bperiod).toLowerCase())}</span><h2 id="overTitle">${esc(drillLabel(key))}</h2><span class="sec">${plural(rows.length, "review")} in this group</span></div>${closeBtn}</div>
  <div class="over-b">
    <div class="seg" role="tablist" aria-label="Feedback type">${["problem", "request", "praise"].map(k => `<button role="tab" aria-selected="${k === kind}" data-drilltab="${k}">${KIND_LABEL[k]}<span class="n">${drillCount(rows, key, k, tm)}</span></button>`).join("")}</div>
    ${themes.length ? themes.map(([id, items]) => `<section class="drill-theme"><div class="drill-head"><button class="rowlink" data-theme="${id}">${esc(tm[id].name)}${chevron}</button><span class="sec num">${plural(items.length, "review")}</span></div>
      <ul class="statements">${items.slice(0, 5).map(({ r, ev }) => `<li><button class="stmt" data-review="${r.id}">“${esc(ev)}”</button><span class="meta">${esc(r.who)}, ${stars(r.stars)}, ${fd(r.date)}</span></li>`).join("")}</ul>
      ${items.length > 5 ? `<p class="meta">and ${items.length - 5} more. Open the theme to see them all.</p>` : ""}</section>`).join("")
      : `<div class="empty"><b>No ${noun === "praise" ? "praise" : noun + "s"} here</b><span class="sec">Nothing in this group for the selected period.</span></div>`}
  </div>
  <div class="over-f"><button class="btn primary" data-drill-open ${n ? "" : "disabled"}>Open ${plural(n, "review")} in the table</button></div>`;
}
function openDrillInTable() {
  const { key, kind } = S.drill;
  S.rview = "all"; S.rchips = []; S.rpage = 0; S.q = "";
  S.rfilter = { kind };
  if (S.bperiod) S.rfilter.period = S.bperiod;
  S.rfilter[S.group === "area" ? "area" : S.group === "version" ? "version" : "segment"] = key;
  closeOver(); route.last = "reviews"; location.hash = "reviews";
}

function competitorsIn(rows) {
  const g = new Map();
  rows.forEach(r => r.competitors.forEach(c => { if (!g.has(c.name)) g.set(c.name, []); g.get(c.name).push([r, c]); }));
  return [...g.entries()].map(([name, v]) => ({ name, mentions: v.length, contexts: v.reduce((o, [, c]) => (o[c.context] = (o[c.context] || 0) + 1, o), {}), quotes: v.slice(0, 2).map(([r, c]) => ({ review_id: r.id, evidence: c.evidence })) }))
    .sort((a, b) => b.mentions - a.mentions);
}
function pBreakdowns() {
  const src = breakdownRows(), B = computeBreakdown(src), labels = { area: "Product area", version: "App version", segment: "User segment" };
  const tm = themeMap();
  let rows = B[S.group].slice();
  if (S.group !== "version") rows.sort((a, b) => (b.problems + b.requests + b.praise) - (a.problems + a.requests + a.praise));
  S._brows = rows;
  const max = Math.max(1, ...rows.map(r => r.problems + r.requests + r.praise));
  const topProblems = r => { const c = {}; r.ids.forEach(id => REV[id].themes.forEach(t => { if (tm[t] && tm[t].kind === "problem") c[t] = (c[t] || 0) + 1; })); return Object.entries(c).sort((a, b) => b[1] - a[1]).slice(0, 2).map(([t]) => tm[t].name); };
  const extraHead = S.group === "version" ? "<th scope=\"col\">First reported on this version</th>" : S.group === "segment" ? "<th scope=\"col\">Top problems</th>" : "";
  const extraCell = r => S.group === "version" ? `<td class="sec">${esc(((V().versions[r.key] || {}).first_themes || []).map(t => (tm[t] || {}).name).filter(Boolean).join(", ") || "Nothing new")}</td>` : S.group === "segment" ? `<td class="sec">${esc(topProblems(r).join(", ") || "None reported")}</td>` : "";
  const comps = competitorsIn(src);
  return `<header class="phead"><div class="t"><h1>Breakdowns</h1><p>How problems, requests and praise split by product area, app version or type of user.</p></div><button class="btn" data-export="breakdown">Export CSV</button></header>
  <div class="toolbar"><div class="brange">${periodField(S.bperiod, "bperiod", "Date range", `<button class="btn primary" id="applyRange">Apply range</button>`)}</div>
    <div class="field"><span style="font-size:var(--t-sec);font-weight:500">Group by</span><div class="seg" role="group" aria-label="Group by">${Object.entries(labels).map(([id, l]) => `<button aria-pressed="${S.group === id}" data-group="${id}">${l}</button>`).join("")}</div></div></div>
  <section class="kpis" style="grid-template-columns:repeat(3,minmax(0,1fr))" aria-label="Totals"><div class="card kpi"><span class="lbl">Reviews</span><span class="big"><b class="num">${B.totals.reviews}</b></span></div><div class="card kpi"><span class="lbl">Written-review rating</span><span class="big"><b class="num">${B.totals.avg_rating != null ? B.totals.avg_rating.toFixed(2) : "–"}</b><span>out of 5</span></span></div><div class="card kpi"><span class="lbl">Mixed or negative</span><span class="big"><b class="num">${B.totals.mixed_or_negative_pct}%</b><span>of reviews</span></span></div></section>
  <section class="card pad"><div class="card-h"><h2>Feedback by ${labels[S.group].toLowerCase()}</h2><div class="legend"><span><i style="background:var(--s-prob)"></i>Problems</span><span><i style="background:var(--s-req)"></i>Requests</span><span><i style="background:var(--s-praise)"></i>Praise</span></div></div>
    ${rows.length ? `<div class="bars">${rows.slice(0, 12).map(r => `<div class="bar drill" role="button" tabindex="0" data-drill="${esc(r.key)}" aria-label="${esc(r.label)}: ${r.problems} problems, ${r.requests} requests, ${r.praise} praise. Open details"><span>${esc(r.label)}</span><span style="display:block"><span class="stack" style="width:${(r.problems + r.requests + r.praise) / max * 100}%">${[[r.problems, "var(--s-prob)", "problems", "problem"], [r.requests, "var(--s-req)", "requests", "request"], [r.praise, "var(--s-praise)", "praise", "praise"]].filter(s => s[0] > 0).map(s => `<span style="flex:${s[0]};background:${s[1]}" data-drill="${esc(r.key)}" data-drill-kind="${s[3]}" data-tip="${esc(r.label)}: ${s[0]} ${s[2]}. Click to see them"></span>`).join("")}</span></span><span class="v num">${r.problems + r.requests + r.praise}</span></div>`).join("")}</div>` : `<p class="sec">No reviews in this range.</p>`}</section>
  <div class="tframe"><div class="tscroll"><table><thead><tr><th scope="col">${labels[S.group]}</th><th class="r" scope="col">Reviews</th><th class="r" scope="col" ${S.group !== "version" ? 'aria-sort="descending"' : ""}>Problems</th><th class="r" scope="col">Requests</th><th class="r" scope="col">Praise</th><th class="r" scope="col">Written-review rating</th>${extraHead}</tr></thead><tbody>${rows.map(r => `<tr class="row" data-drill="${esc(r.key)}" tabindex="0"><td><button class="rowlink" data-drill="${esc(r.key)}">${esc(r.label)}${chevron}</button></td><td class="r num">${r.reviews}</td><td class="r num">${r.problems}</td><td class="r num">${r.requests}</td><td class="r num">${r.praise}</td><td class="r num">${r.avg_rating != null ? r.avg_rating.toFixed(1) : "–"}</td>${extraCell(r)}</tr>`).join("") || `<tr><td colspan="7"><div class="empty"><b>No reviews in this range</b></div></td></tr>`}</tbody></table></div>${pager(rows.length)}</div>
  <section class="card pad"><div class="card-h"><h2>Competitor mentions</h2><span class="sec">${plural(comps.reduce((a, c) => a + c.mentions, 0), "mention")}, ${esc(periodLabel(S.bperiod).toLowerCase())}</span></div>
    ${comps.map(c => `<div class="comp"><div class="row"><b>${esc(c.name)}</b><span class="sec">${Object.entries(c.contexts).map(([k, n]) => `${esc(D.taxonomy.competitor_contexts[k])}: ${n}`).join(", ")}</span></div>${c.quotes.slice(0, 2).map(q => quoteHTML(q.review_id, q.evidence)).join("")}</div>`).join("") || `<p class="sec">No competitors mentioned.</p>`}</section>`;
}

function critRows() {
  const q = S.q.trim().toLowerCase();
  return D.critical.filter(c => { const r = REV[c.review_id]; return !q || (r.text + r.who + c.category_label).toLowerCase().includes(q); })
    .filter(c => !S.f.cat || c.category === S.f.cat).filter(c => !S.f.store || c.store === S.f.store).filter(c => !S.f.reply || c.reply === S.f.reply)
    .filter(c => inPeriod(c.date, S.f.period));
}
function pSupport() {
  const rows = critRows(), rs = V().reply;
  const fields = [["period", "Period", null, S.f.period || ""], ["cat", "Category", Object.entries(D.taxonomy.critical_categories), S.f.cat || ""], ["store", "Store", [["Google Play", "Google Play"], ["App Store", "App Store"]], S.f.store || ""], ["reply", "Reply", [["No reply", "No reply"], ["Replied", "Replied"], ["Unknown", "Unknown"]], S.f.reply || ""]];
  const chips = [S.f.period && ["period", "Period: " + periodLabel(S.f.period)], S.f.cat && ["cat", "Category: " + D.taxonomy.critical_categories[S.f.cat]], S.f.store && ["store", "Store: " + S.f.store], S.f.reply && ["reply", "Reply: " + S.f.reply]].filter(Boolean);
  return `<header class="phead"><div class="t"><h1>Critical reviews</h1><p>Reviews that need escalation, whatever their rating. The developer replies to ${rs.reply_rate}% of Google Play reviews and ${rs.critical_reply_rate}% of critical ones, in a median of ${rs.median_reply_days} days.</p></div><button class="btn" data-open="email">Preview alert email</button></header>
  <div class="toolbar">${searchBox("Search critical reviews")}${filtersPopover(fields, chips.length)}<button class="icon-btn" data-export="support" aria-label="Download CSV" data-tip="Download CSV"><svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true"><path d="M9 3v8m0 0 3-3m-3 3L6 8M3 13v2h12v-2" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg></button></div>${chipsRow(chips)}
  <div class="tframe"><div class="tscroll"><table><thead><tr><th scope="col" aria-sort="descending">Waiting ↓</th><th scope="col">What they said</th><th scope="col">Category</th><th scope="col">Store</th><th scope="col">Reply</th><th scope="col">Alert</th></tr></thead><tbody>${rows.map(c => { const r = REV[c.review_id]; return `<tr class="row" data-review="${c.review_id}" tabindex="0"><td class="num" style="white-space:nowrap">${c.waiting_days != null ? `<b style="color:var(--crit);font-weight:600">${plural(c.waiting_days, "day")}</b>` : "–"}<div class="meta">${fd(c.date)}</div></td><td><button class="rowlink" data-review="${c.review_id}"><span class="clip">${excerpt(r.text, c.evidence, 160)}</span>${chevron}</button><div class="meta">${esc(r.who)}, ${stars(r.stars)}</div></td><td>${esc(c.category_label)}</td><td class="nowrap">${esc(c.store)}</td><td class="nowrap">${c.reply === "No reply" ? `<span class="sev blocking"><i aria-hidden="true"></i>No reply</span>` : c.reply === "Unknown" ? `<span class="sec">Unknown</span>` : esc(replyIn(c.reply_hours))}</td><td class="sec">${esc(c.alert)}</td></tr>`; }).join("") || `<tr><td colspan="6"><div class="empty"><b>No critical reviews match</b><button class="btn" data-clear="1">Clear filters</button></div></td></tr>`}</tbody></table></div>${pager(rows.length)}</div>
  <p class="note">App Store reviews show “Unknown” because Apple doesn't publish developer replies, so they are never included in alerts.</p>`;
}

function reviewRows() {
  const q = S.q.trim().toLowerCase(), f = S.rfilter, tm = themeMap();
  return D.reviews.filter(r => {
    if (!S.spikes && r.spike && S.rview !== "spike") return false;
    if (S.rview === "nothing" && !r.low_context) return false;
    if (S.rview === "spike" && !r.spike) return false;
    if (S.rview === "mismatch" && !r.mismatch) return false;
    if (f.period && !inPeriod(r.date, f.period)) return false;
    if (f.store && r.store !== f.store) return false;
    if (f.stars && String(r.stars) !== f.stars) return false;
    // Area and feedback type must match on the same mention, so "Study content + Problems" means a problem about study content.
    if ((f.area || f.kind) && !r.mentions.some(m => { const t = tm[m.theme]; return t && (!f.area || t.area === f.area) && (!f.kind || t.kind === f.kind); })) return false;
    if (f.version && (r.version || "Unknown") !== f.version) return false;
    if (f.segment && !r.segments.includes(f.segment)) return false;
    if (S.rchips.some(t => !r.themes.includes(t))) return false;
    if (q && !(r.text + " " + (r.title || "") + " " + r.who).toLowerCase().includes(q)) return false;
    return true;
  });
}
function pReviews() {
  const views = [["all", "All reviews"], ["nothing", "Says nothing specific"], ["spike", "Posted on a spike day"], ["mismatch", "Rating doesn't match text"]];
  const rows = reviewRows(), page = Math.min(S.rpage, Math.max(0, Math.ceil(rows.length / PAGE_SIZE) - 1)), shown = rows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
  const tm = themeMap();
  const spikeText = D.spike_days.map(s => `${fd(s.day)} (${s.n})`).join(", ");
  const note = { nothing: "These reviews give no reason for their rating, such as “best app” or “bad”. They count in the overall rating, but not in problems, requests or praise.", spike: `Spike days had ${D.meta.rules.spike_day_min_reviews} or more reviews, against about ${D.meta.avg_reviews_per_day} on a normal day: ${spikeText}. They may be genuine, such as a launch, or prompted. You decide whether to count them.`, mismatch: "The star rating contradicts what the review says. The dashboard follows the text." }[S.rview];
  const fields = [["period", "Period", null, S.rfilter.period || ""], ["store", "Store", [["Google Play", "Google Play"], ["App Store", "App Store"]], S.rfilter.store || ""], ["stars", "Rating", [5, 4, 3, 2, 1].map(n => [String(n), stars(n)]), S.rfilter.stars || ""], ["area", "Product area", Object.entries(D.taxonomy.areas), S.rfilter.area || ""], ["kind", "Feedback type", Object.entries(KIND_LABEL), S.rfilter.kind || ""]];
  const chips = [S.rfilter.period && ["period", "Period: " + periodLabel(S.rfilter.period)], S.rfilter.store && ["store", "Store: " + S.rfilter.store], S.rfilter.stars && ["stars", stars(+S.rfilter.stars)], S.rfilter.area && ["area", "Area: " + areaName(S.rfilter.area)], S.rfilter.kind && ["kind", "Type: " + KIND_LABEL[S.rfilter.kind]], S.rfilter.version && ["version", S.rfilter.version === "Unknown" ? "Unknown version" : "Version " + S.rfilter.version], S.rfilter.segment && ["segment", "Segment: " + D.taxonomy.segments[S.rfilter.segment]], ...S.rchips.map(t => ["theme:" + t, "Theme: " + ((tm[t] || {}).name || t)])].filter(Boolean);
  return `<header class="phead"><div class="t"><h1>Reviews</h1><p>Every review from both stores. Search, filter, or open a saved view.</p></div></header>
  <div class="seg" role="tablist" aria-label="Saved views">${views.map(([id, l]) => `<button role="tab" aria-selected="${S.rview === id}" data-rview="${id}">${l}</button>`).join("")}</div>
  ${note ? `<div class="note box">${note}${S.rview === "spike" ? `<div><label class="switch"><input type="checkbox" id="spikeT" ${S.spikes ? "checked" : ""}> Count spike-day reviews in all numbers</label></div>` : ""}</div>` : ""}
  <div class="toolbar">${searchBox("Search reviews")}${filtersPopover(fields, chips.length)}<button class="icon-btn" data-export="reviews" aria-label="Export CSV" data-tip="Export CSV"><svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true"><path d="M9 3v8m0 0 3-3m-3 3L6 8M3 13v2h12v-2" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg></button></div>${chipsRow(chips)}
  <div class="tframe"><div class="tscroll"><table><thead><tr><th scope="col" aria-sort="descending">Date ↓</th><th scope="col">Review</th><th scope="col">Rating</th><th scope="col">Store</th><th scope="col">Reply</th></tr></thead><tbody>${shown.map(r => `<tr class="row" data-review="${r.id}" tabindex="0"><td class="num" style="white-space:nowrap">${fd(r.date)}</td><td><button class="rowlink" data-review="${r.id}"><span class="clip">${r.title ? `<b>${esc(r.title)}.</b> ` : ""}${esc(r.text)}</span>${chevron}</button><div class="meta">${esc(r.who)}${r.critical ? ` <span style="color:var(--crit)">Critical</span>` : ""}</div></td><td class="num">${r.stars} of 5</td><td class="nowrap">${esc(r.store)}</td><td>${r.reply === "Unknown" ? `<span class="sec">Unknown</span>` : esc(r.reply)}</td></tr>`).join("") || `<tr><td colspan="5"><div class="empty"><b>No reviews match</b><span class="sec">Try another view or clear the filters.</span><button class="btn" data-clear="1">Clear filters</button></div></td></tr>`}</tbody></table></div>${pager(rows.length, page, PAGE_SIZE)}</div>`;
}

/* ------------------------------------------------------------------ slide-over panels */
/* Panels are a stack of render functions: opening from inside a panel pushes, Back pops. */
S.stack = [];
const backBtn = `<button class="icon-btn" data-back aria-label="Back to previous panel"><svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true"><path d="M11 4 6 9l5 5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg></button>`;
function openOver(render, trigger, push) {
  if (!push) { S.stack = []; S.lastFocus = trigger || document.activeElement; }
  S.stack.push(render); paintOver();
}
function paintOver() {
  const o = $("#over"); o.innerHTML = S.stack[S.stack.length - 1]();
  if (S.stack.length > 1) o.querySelector(".over-h")?.insertAdjacentHTML("afterbegin", backBtn);
  o.hidden = false; $("#scrim").hidden = false; o.scrollTop = 0;
  (o.querySelector("[data-back]") || o.querySelector("[data-close]"))?.focus();
}
function backOver() { if (S.stack.length > 1) { S.stack.pop(); paintOver(); } }
function closeOver() { if ($("#over").hidden) return; S.stack = []; $("#over").hidden = true; $("#scrim").hidden = true; if (S.lastFocus && document.contains(S.lastFocus)) S.lastFocus.focus(); }

function themePanel(id) {
  const t = themeMap()[id]; if (!t) return "";
  const kindLabel = { problem: "Problem to fix", request: "Request to build", praise: "What users love" }[t.kind];
  const meta = t.kind === "problem" ? `${t.severity} problem in ${areaName(t.area)}. ${t.type}: ${t.sub.toLowerCase()}.` : t.kind === "request" ? `${t.priority} ${t.rtype.toLowerCase()} request for ${areaName(t.area)}.` : `Loved for ${t.reason.toLowerCase()}, in ${areaName(t.area)}.`;
  const quoted = new Set(t.quotes.map(q => q.evidence));
  const phr = (t.phrasings || []).filter(p => !quoted.has(p));
  return `<div class="over-h"><div class="t"><span class="meta">${kindLabel}</span><h2 id="overTitle">${esc(t.name)}</h2><span class="sec">${esc(meta)}</span></div>${closeBtn}</div>
  <div class="over-b">
    <dl class="facts4"><div><dt>Reviews</dt><dd class="num">${t.n}</dd></div><div><dt>Last 90 days</dt><dd class="num">${t.cur90} <span class="sec">(was ${t.prev90})</span></dd></div><div><dt>First seen</dt><dd>${fd(t.first)}</dd></div><div><dt>Last seen</dt><dd>${fd(t.last)}</dd></div><div><dt>Average rating</dt><dd class="num">${t.avg_rating.toFixed(1)} <span class="sec">of these reviews</span></dd></div>${t.kind !== "praise" ? `<div><dt>From 1 to 3 star reviews</dt><dd class="num">${t.unhappy_pct}%</dd></div>` : `<div><dt>Seen in</dt><dd>${plural(t.months, "month")}</dd></div>`}</dl>
    ${t.also_request ? `<p class="callout">Users also ask for a fix as a feature. It is counted once, here under Fix.</p>` : ""}
    <section><h3 style="margin-bottom:var(--sp-3)">What users say</h3>${t.quotes.map(q => quoteHTML(q.review_id, q.evidence)).join("")}</section>
    ${phr.length ? `<section><h3 style="margin-bottom:var(--sp-2)">Also phrased as</h3><ul class="plain">${phr.map(p => `<li>“${esc(p)}”</li>`).join("")}</ul></section>` : ""}
    <section><h3 style="margin-bottom:var(--sp-2)">What counts here</h3><p class="sec">${esc(t.definition || "")}</p></section>
  </div>
  <div class="over-f"><button class="btn primary" data-reviews-for="${t.id}">View ${plural(t.n, "review")}</button><button class="btn" data-ticket="${t.id}">Copy as ticket</button><button class="btn" data-export="theme:${t.id}">Download CSV</button></div>`;
}
function reviewPanel(id) {
  const r = REV[id]; if (!r) return "";
  const tm = themeMap(), kindLabel = { problem: "Problem", request: "Request", praise: "Praise" };
  const evs = r.mentions.map(m => m.evidence).concat(r.critical ? [r.critical.evidence] : []);
  const flags = [r.critical && `<span style="color:var(--crit);font-weight:500">Critical: ${esc(D.taxonomy.critical_categories[r.critical.category])}</span>`, r.low_context && "Says nothing specific", r.spike && "Posted on a spike day", r.mismatch && "Rating doesn't match text"].filter(Boolean);
  const replyText = r.reply === "Replied" ? (r.reply_text ? esc(r.reply_text) : "The developer replied.") : r.reply === "No reply" ? "No reply yet." : "Unknown. Apple doesn't publish developer replies.";
  return `<div class="over-h"><div class="t"><span class="meta">${flags.length ? flags.join(". ") : "Review"}</span><h2 id="overTitle">${esc(r.who)}</h2><span class="sec">${stars(r.stars)}, ${fd(r.date)}, ${esc(r.store)}${r.version ? `, version ${esc(r.version)}` : ""}</span></div>${closeBtn}</div>
  <div class="over-b">
    <div>${r.title ? `<h3 style="margin-bottom:var(--sp-2)">${esc(r.title)}</h3>` : ""}<p class="review-text">${markAll(r.text, evs)}</p>${r.helpful ? `<p class="meta" style="margin-top:var(--sp-2)">${plural(r.helpful, "person", "people")} found this helpful</p>` : ""}</div>
    ${r.mentions.length ? `<section><h3 style="margin-bottom:var(--sp-3)">What this review mentions</h3>${r.mentions.map(m => { const t = tm[m.theme]; return `<div class="mention"><span class="kind ${t ? t.kind : ""}">${t ? kindLabel[t.kind] : ""}</span>${t ? `<button class="rowlink" data-theme="${t.id}">${esc(t.name)}${chevron}</button>` : ""}<span class="sec">“${esc(m.evidence)}”</span></div>`; }).join("")}</section>` : ""}
    ${r.segments.length || r.competitors.length ? `<section><h3 style="margin-bottom:var(--sp-2)">About the reviewer</h3><ul class="plain">${r.segments.map(s => `<li>${esc(D.taxonomy.segments[s])}</li>`).join("")}${r.competitors.map(c => `<li>Mentions ${esc(c.name)}: ${esc(D.taxonomy.competitor_contexts[c.context].toLowerCase())}</li>`).join("")}</ul></section>` : ""}
    <section><h3 style="margin-bottom:var(--sp-2)">Developer reply</h3><p class="sec review-text">${replyText}</p></section>
  </div>
  <div class="over-f"><span class="meta">${r.labelled ? (r.labelled_by === "hand-v1" ? "Sorted by hand" : "Sorted by model: " + esc(r.labelled_by)) : "Not sorted into themes yet"}</span></div>`;
}
function updatePanel() {
  const ups = V().updates, u = ups[S.update] || ups[0];
  return `<div class="over-h"><div class="t"><span class="meta">Update</span><h2 id="overTitle">What changed, ${esc(u.label)}</h2><span class="sec">Compared with the two weeks before</span></div>${closeBtn}</div>
  <div class="over-b"><div class="field"><label for="pastU">Update</label><select id="pastU">${ups.map((x, i) => `<option value="${i}" ${i === S.update ? "selected" : ""}>${esc(x.label)}${i === 0 ? " (latest)" : ""}</option>`).join("")}</select></div>
  <ul class="plain">${u.summary.map(s => `<li>${esc(s)}</li>`).join("")}</ul>
  ${u.notable.length ? `<section><h3 style="margin-bottom:var(--sp-3)">Notable reviews</h3>${u.notable.map(id => { const r = REV[id]; return quoteHTML(id, (r.critical && r.critical.evidence) || (r.mentions[0] && r.mentions[0].evidence), r.critical ? "Critical" : ""); }).join("")}</section>` : ""}</div>
  <div class="over-f"><a class="btn primary" href="#feedback-fix" data-close>Open Feedback</a></div>`;
}
function emailPanel() {
  const q = D.critical.filter(c => c.reply === "No reply" && c.store === "Google Play");
  return `<div class="over-h"><div class="t"><span class="meta">Alert email preview</span><h2 id="overTitle">SuperKalam reviews: ${plural(q.length, "critical review")} without a reply</h2><span class="sec">Sent after each refresh to the recipients set in configuration</span></div>${closeBtn}</div>
  <div class="over-b">${q.length ? `<p class="sec">${q.length === 1 ? "One critical Google Play review has" : `${q.length} critical Google Play reviews have`} no developer reply after the latest refresh.</p>${q.map(c => { const r = REV[c.review_id]; return `<div class="card pad" style="display:flex;flex-direction:column;gap:var(--sp-2)"><span class="meta" style="color:var(--crit);font-weight:500">${esc(c.category_label)}</span><p>“${excerpt(r.text, c.evidence)}”</p><span class="meta">${esc(r.who)}, ${stars(r.stars)}, ${fd(r.date)}. Waiting ${plural(c.waiting_days, "day")}.</span><button class="linkbtn" style="align-self:flex-start;padding-left:0" data-review="${c.review_id}">Open in dashboard</button></div>`; }).join("")}` : `<p class="sec">Nothing to send: every critical Google Play review has a reply. No email goes out.</p>`}</div>`;
}

/* ------------------------------------------------------------------ router */
function route() {
  let h = (location.hash || "#home").slice(1) || "home";
  if (h.startsWith("feedback")) {
    const t = h.split("-")[1];
    if (t && ["fix", "build", "protect"].includes(t) && t !== S.tab) { S.tab = t; S.f = {}; }
    h = "feedback";
  }
  const pages = { home: pHome, feedback: pFeedback, breakdowns: pBreakdowns, support: pSupport, reviews: pReviews };
  const key = pages[h] ? h : "home";
  if (route.last !== key) { S.q = ""; S.pop = false; S.f = {}; route.last = key; }
  $("#view").innerHTML = pages[key]();
  document.title = key === "home" ? "User Review Dashboard" : `${NAV.find(n => n[0] === key)[1]} | User Review Dashboard`;
  renderNav(key); $("#app").classList.remove("nav-open"); $("#menuBtn").setAttribute("aria-expanded", "false");
  bind();
}
function rerender() { const y = scrollY; route(); scrollTo(0, y); }
function bind() {
  const q = $("#q");
  if (q) q.addEventListener("input", e => { S.q = e.target.value; S.rpage = 0; const p = e.target.selectionStart; rerender(); const n = $("#q"); n.focus(); n.setSelectionRange(p, p); });
  const r = $("#bperiod"); if (r) r.addEventListener("change", e => { if (e.target.value !== "custom") { S.bperiod = e.target.value; rerender(); } else $("#bperiodFrom")?.focus(); });
  const fb = $("#fbperiod"); if (fb) fb.addEventListener("change", e => { if (e.target.value !== "custom") { if (e.target.value) S.f.period = e.target.value; else delete S.f.period; rerender(); } else $("#fbperiodFrom")?.focus(); });
  const st = $("#spikeT"); if (st) st.addEventListener("change", e => { S.spikes = e.target.checked; try { localStorage.setItem("av-spikes", S.spikes ? "1" : "0"); } catch (err) { /* ignore */ } rerender(); toast(S.spikes ? "Spike-day reviews are counted" : "Spike-day reviews are left out of all numbers"); });
}
function onReviewsPage() { return (location.hash || "").startsWith("#reviews"); }

/* ------------------------------------------------------------------ events */
document.addEventListener("click", e => {
  const t = e.target.closest("[data-tab],[data-theme],[data-review],[data-open],[data-close],[data-back],[data-drill],[data-drilltab],[data-drill-open],[data-chart],[data-group],[data-rview],[data-unchip],[data-clear],[data-reviews-for],[data-export],[data-ticket],[data-pg],#filtersBtn,#applyF,#applyRange,#applyFbRange,.bar[data-area]");
  if (!t) return;
  if (t.matches("[data-close]")) { closeOver(); return; }
  if (t.matches("[data-back]")) { backOver(); return; }
  const inPanel = !!t.closest("#over");
  if (t.dataset.drilltab) { S.drill.kind = t.dataset.drilltab; paintOver(); return; }
  if (t.matches("[data-drill-open]")) { openDrillInTable(); return; }
  if (t.dataset.drill) { S.drill = { key: t.dataset.drill, kind: t.dataset.drillKind || firstKind(t.dataset.drill) }; openOver(drillPanel, t); return; }
  if (t.dataset.tab) { S.tab = t.dataset.tab; S.f = {}; S.pop = false; history.replaceState(null, "", "#feedback-" + S.tab); rerender(); return; }
  if (t.dataset.theme) { const id = t.dataset.theme; openOver(() => themePanel(id), t, inPanel); return; }
  if (t.dataset.review) { const id = t.dataset.review; openOver(() => reviewPanel(id), t, inPanel); return; }
  if (t.dataset.open) { openOver(t.dataset.open === "update" ? updatePanel : emailPanel, t); return; }
  if (t.dataset.chart) { S.chart = t.dataset.chart; rerender(); return; }
  if (t.dataset.group) { S.group = t.dataset.group; rerender(); return; }
  if (t.dataset.rview) { S.rview = t.dataset.rview; S.rpage = 0; rerender(); return; }
  if (t.dataset.pg) { S.rpage = Math.max(0, S.rpage + Number(t.dataset.pg)); rerender(); $(".tframe")?.scrollIntoView({ block: "start" }); return; }
  if (t.dataset.unchip) {
    const k = t.dataset.unchip;
    if (onReviewsPage()) { if (k.startsWith("theme:")) S.rchips = S.rchips.filter(x => x !== k.slice(6)); else delete S.rfilter[k]; S.rpage = 0; }
    else delete S.f[k];
    rerender(); return;
  }
  if (t.dataset.clear) { if (onReviewsPage()) { S.rfilter = {}; S.rchips = []; S.rpage = 0; } else S.f = {}; S.q = ""; S.pop = false; rerender(); return; }
  if (t.dataset.reviewsFor) { closeOver(); S.rview = "all"; S.rfilter = {}; S.rchips = [t.dataset.reviewsFor]; S.rpage = 0; location.hash = "reviews"; return; }
  if (t.dataset.export) { exportCSV(t.dataset.export); return; }
  if (t.dataset.ticket) { const name = (themeMap()[t.dataset.ticket] || {}).name; copyText(ticketMarkdown(t.dataset.ticket), `Copied “${name}” as a ticket`); return; }
  if (t.id === "filtersBtn") { S.pop = !S.pop; rerender(); if (S.pop) $("#filtersPop select")?.focus(); return; }
  if (t.id === "applyF") {
    const target = onReviewsPage() ? S.rfilter : S.f;
    if ($("#fperiod")) { // only pages whose period lives inside Filters
      const p = readPeriod("fperiod");
      if (!p.ok) { toast(p.msg); $("#fperiodFrom")?.focus(); return; }
      if (p.value) target.period = p.value; else delete target.period;
    }
    document.querySelectorAll("#filtersPop select[data-fkey]").forEach(s => { if (s.value) target[s.dataset.fkey] = s.value; else delete target[s.dataset.fkey]; });
    S.pop = false; S.rpage = 0; rerender(); return;
  }
  if (t.id === "applyRange") {
    const p = readPeriod("bperiod");
    if (!p.ok) { toast(p.msg); return; }
    S.bperiod = p.value; rerender(); return;
  }
  if (t.id === "applyFbRange") {
    const p = readPeriod("fbperiod");
    if (!p.ok) { toast(p.msg); return; }
    if (p.value) S.f.period = p.value; else delete S.f.period;
    rerender(); return;
  }
  if (t.matches(".bar[data-area]")) { e.preventDefault(); S.tab = "fix"; S.f = { area: t.dataset.area }; route.last = "feedback"; location.hash = "feedback-fix"; if (location.hash === "#feedback-fix") rerender(); }
});
document.addEventListener("change", e => {
  if (e.target.id === "pastU") { S.update = +e.target.value; paintOver(); }
  if (e.target.matches("[data-period]")) {
    const box = document.querySelector(`[data-custom-for="${e.target.id}"]`);
    if (box) box.hidden = e.target.value !== "custom";
  }
});
document.addEventListener("keydown", e => {
  if (e.key === "Escape") { if (!$("#over").hidden) closeOver(); else if (S.pop) { S.pop = false; rerender(); $("#filtersBtn")?.focus(); } }
  if (e.key === "/" && !e.target.matches("input,select,textarea")) { const q = $("#q"); if (q) { e.preventDefault(); q.focus(); } }
  if (e.key === "Enter" && e.target.matches("tr.row")) e.target.querySelector(".rowlink")?.click();
  if ((e.key === "Enter" || e.key === " ") && e.target.matches(".bar.drill")) { e.preventDefault(); e.target.click(); }
  if (e.key === "Tab" && !$("#over").hidden) {
    const f = [...$("#over").querySelectorAll("button:not([disabled]),a[href],select,input")]; if (!f.length) return;
    const a = f[0], z = f[f.length - 1];
    if (e.shiftKey && document.activeElement === a) { e.preventDefault(); z.focus(); } else if (!e.shiftKey && document.activeElement === z) { e.preventDefault(); a.focus(); }
  }
});
function exportCSV(what) {
  if (what.startsWith("theme:")) {
    const t = themeMap()[what.slice(6)]; if (!t) return;
    const ev = id => (REV[id].mentions.find(m => m.theme === t.id) || {}).evidence || "";
    const rows = t.review_ids.map(id => REV[id]).sort((a, b) => b.date.localeCompare(a.date));
    download(`${t.id.replace(/_/g, "-")}-reviews.csv`, [["Theme", "Date", "Reviewer", "Rating", "Store", "Version", "What they said", "Full review", "Reply"]].concat(rows.map(r => [t.name, r.date, r.who, r.stars, r.store, r.version, ev(r.id), r.text, r.reply])));
    return;
  }
  if (what === "support") {
    download("critical-reviews.csv", [["Date", "Reviewer", "Rating", "Store", "Category", "Trigger phrase", "Full review", "Reply", "Waiting days", "Alert"]].concat(critRows().map(c => { const r = REV[c.review_id]; return [c.date, r.who, r.stars, c.store, c.category_label, c.evidence, r.text, c.reply === "Replied" ? replyIn(c.reply_hours) : c.reply, c.waiting_days ?? "", c.alert]; })));
    return;
  }
  if (what === "reviews") {
    const rows = reviewRows(), tm = themeMap();
    download("superkalam-reviews.csv", [["Date", "Reviewer", "Rating", "Store", "Version", "Title", "Review", "Themes", "Critical", "Reply"]].concat(rows.map(r => [r.date, r.who, r.stars, r.store, r.version, r.title, r.text, r.themes.map(t => (tm[t] || {}).name || t).join("; "), r.critical ? D.taxonomy.critical_categories[r.critical.category] : "", r.reply])));
  } else {
    const B = S._brows || [];
    download(`superkalam-breakdown-${S.group}-${S.bperiod ? S.bperiod.replace(/:/g, "_") : "all-time"}.csv`,[["Group", "Reviews", "Problems", "Requests", "Praise", "Written-review rating"]].concat(B.map(r => [r.label, r.reviews, r.problems, r.requests, r.praise, r.avg_rating])));
  }
}
$("#scrim").addEventListener("click", closeOver);
$("#menuBtn").addEventListener("click", () => { const o = $("#app").classList.toggle("nav-open"); $("#menuBtn").setAttribute("aria-expanded", String(o)); });
const tip = $("#tip");
document.addEventListener("mousemove", e => {
  const t = e.target.closest("[data-tip]"); if (!t) { tip.hidden = true; return; }
  tip.textContent = t.dataset.tip; tip.hidden = false;
  let x = e.clientX + 14, y = e.clientY + 14;
  if (x + tip.offsetWidth > innerWidth - 8) x = e.clientX - tip.offsetWidth - 14;
  if (y + tip.offsetHeight > innerHeight - 8) y = e.clientY - tip.offsetHeight - 14;
  tip.style.left = x + "px"; tip.style.top = y + "px";
});
window.addEventListener("hashchange", () => { closeOver(); route(); scrollTo(0, 0); });

/* ------------------------------------------------------------------ boot */
fetch("data/dashboard.json", { cache: "no-cache" })
  .then(r => { if (!r.ok) throw new Error(r.status); return r.json(); })
  .then(data => {
    D = data; D.reviews.forEach(r => REV[r.id] = r);
    $("#freshness").textContent = `Data updated ${fd(D.meta.as_of)}`;
    route();
  })
  .catch(() => { $("#view").innerHTML = `<div class="empty"><b>Couldn't load the review data</b><span class="sec">Check that data/dashboard.json was built and published with the site, then reload the page.</span><button class="btn" onclick="location.reload()">Reload</button></div>`; });
