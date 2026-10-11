/* Nibble front end. No framework: one page, one globe, a handful of fetches. */
const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
};
const money = (n) => (n == null ? "--" : "$" + Number(n).toFixed(2));
const safeUrl = (u) => (typeof u === "string" && /^https?:\/\//i.test(u) ? u : null);
const link = (url, text) => {
  const href = safeUrl(url);
  if (!href) return document.createTextNode(text);
  const a = el("a", null, text);
  a.href = href; a.target = "_blank"; a.rel = "noopener noreferrer";
  return a;
};

const state = { status: null, home: null, world: [], news: [], story: null, trace: null, stores: [], activeItem: null,
  atlas: null, layers: { food: true, routes: true, markets: true, news: true, events: true } };

async function api(path, options) {
  const res = await fetch(path, options);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail ? JSON.stringify(body.detail).slice(0, 200) : `Request failed (${res.status})`);
  return body;
}
const post = (path, data) => api(path, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(data) });

/* Start a background job and poll it. onTick gets the running job each second. */
async function runJob(start, onTick) {
  const { job } = await start;
  for (;;) {
    await new Promise((r) => setTimeout(r, 1000));
    const j = await api(`/api/jobs/${job}`);
    if (j.status === "done") return j.result;
    if (j.status === "failed") throw new Error(j.error || "The agent run failed");
    if (onTick) onTick(j);
  }
}

/* ------------------------------------------------------------------ globe */
/* One colour per kind of dot, the same in the legend and on the globe. */
const LAYERS = [
  { id: "food", label: "Where food comes from", color: "#3ddc97" },
  { id: "routes", label: "Shipping routes", color: "#5ad1e6" },
  { id: "markets", label: "Money markets", color: "#b9a7ff" },
  { id: "news", label: "Today's news", color: "#ff6b9d" },
  { id: "events", label: "Quakes, storms, fires", color: "#ff7a45" },
];
const COLOR = Object.fromEntries(LAYERS.map((l) => [l.id, l.color]));
const CAT_COLOR = { markets: "#b9a7ff", food: "#3ddc97", energy: "#ff9d3c", shipping: "#5ad1e6", trade: "#ff6b9d", weather: "#7cc4ff", labor: "#ffd166", tech: "#b9a7ff", other: "#8fa6b3" };
let globe = null;

function initGlobe() {
  if (typeof Globe !== "function") return;
  const node = $("#globe");
  globe = Globe()(node)
    .globeImageUrl("/vendor/earth-night.jpg")
    .bumpImageUrl("/vendor/earth-topology.png")
    .backgroundColor("#06131c")
    .atmosphereColor("#4aa3c7")
    .atmosphereAltitude(0.17)
    .pointAltitude(0.012)
    .pointRadius((d) => d.r)
    .pointColor((d) => d.color)
    .pointLabel((d) => tip(d.title))
    .onPointClick((d) => (d.act ? d.act() : d.url && window.open(d.url, "_blank", "noopener")))
    .onPointHover((d) => { node.style.cursor = d ? "pointer" : ""; })
    .onLabelClick((d) => d.act && d.act())
    .ringColor((d) => (t) => `rgba(${d.rgb},${1 - t})`)
    .ringMaxRadius((d) => d.max)
    .ringPropagationSpeed(2.2)
    .ringRepeatPeriod((d) => d.period)
    .arcColor((d) => d.color || ["#ffb224", "#fff3d6"])
    .arcStroke((d) => d.stroke || 0.55)
    .arcAltitudeAutoScale(0.42)
    .arcDashLength((d) => (d.solid ? 1 : 0.45))
    .arcDashGap((d) => (d.solid ? 0 : 0.12))
    .arcDashAnimateTime((d) => (d.solid ? 0 : 2600))
    .labelText("text")
    .labelSize((d) => d.size)
    .labelDotRadius(0.35)
    .labelColor((d) => d.color)
    .labelAltitude(0.015)
    .labelResolution(2);
  const controls = globe.controls();
  controls.autoRotate = !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  controls.autoRotateSpeed = 0.28;
  node.addEventListener("pointerdown", () => { controls.autoRotate = false; });
  const fit = () => globe.width(node.clientWidth).height(node.clientHeight);
  window.addEventListener("resize", fit);
  fit();
  globe.pointOfView({ lat: 28, lng: -60, altitude: 2.3 }, 0);
}

function tip(text) {
  const d = el("div", "globe-tip", text);
  return d.outerHTML;
}

function drawGlobe() {
  if (!globe) return;
  const on = state.layers;
  const points = [], rings = [], labels = [], arcs = [];
  const atlas = state.atlas || { origins: [], routes: [], markets: [] };

  if (on.food) {
    for (const o of atlas.origins) {
      const act = () => showPlace("food", o);
      points.push({ lat: o.lat, lng: o.lng, r: o.yours ? 0.34 : 0.22, color: COLOR.food, title: `${o.item}: ${o.place}. Click to learn more.`, act });
      if (o.yours) {
        labels.push({ lat: o.lat, lng: o.lng, text: o.item, size: 0.8, color: COLOR.food, act });
        // A faint line from where your food starts to where you live.
        if (state.home && !state.story && !state.trace) {
          arcs.push({ startLat: o.lat, startLng: o.lng, endLat: state.home.lat, endLng: state.home.lng, color: "rgba(61,220,151,0.38)", stroke: 0.22, solid: true });
        }
      }
    }
  }
  if (on.routes) {
    for (const r of atlas.routes) points.push({ lat: r.lat, lng: r.lng, r: 0.2, color: COLOR.routes, title: `${r.name}. Click to learn more.`, act: () => showPlace("routes", r) });
  }
  if (on.markets) {
    for (const m of atlas.markets) points.push({ lat: m.lat, lng: m.lng, r: 0.24, color: COLOR.markets, title: `${m.name}. Click to learn more.`, act: () => showPlace("markets", m) });
  }
  if (on.events) {
    for (const e of state.world) {
      points.push({ lat: e.lat, lng: e.lng, r: e.kind === "earthquake" ? 0.14 + (e.size - 4.5) * 0.12 : 0.2,
        color: COLOR.events, title: `${e.title}. Click to learn more.`, act: () => showPlace("events", e) });
    }
  }
  if (on.news) {
    for (const h of state.news) {
      if (h.lat == null || h.lng == null) continue;
      rings.push({ lat: h.lat, lng: h.lng, rgb: "255,107,157", max: 2.6, period: 2400 });
      points.push({ lat: h.lat, lng: h.lng, r: 0.3, color: COLOR.news, title: `${h.title}. Click to open.`, act: () => showHeadline(h) });
    }
  }
  if (state.home) {
    labels.push({ lat: state.home.lat, lng: state.home.lng, text: `You: ${state.home.label}`, size: 0.9, color: "#e6eef2" });
    rings.push({ lat: state.home.lat, lng: state.home.lng, rgb: "230,238,242", max: 2, period: 1800 });
  }
  if (state.story && state.home) {
    for (const d of state.story.drivers || []) {
      if (d.lat == null || d.lng == null) continue;
      arcs.push({ startLat: d.lat, startLng: d.lng, endLat: state.home.lat, endLng: state.home.lng });
      rings.push({ lat: d.lat, lng: d.lng, rgb: "255,178,36", max: 5, period: 900 });
      labels.push({ lat: d.lat, lng: d.lng, text: d.location_name || d.title, size: 1.05, color: "#ffb224" });
    }
  }
  if (state.trace) {
    // The chain of reasoning: step 1 -> step 2 -> ... -> your town.
    const stops = (state.trace.chain || []).map((c, i) => ({ ...c, n: i + 1 })).filter((c) => c.lat != null && c.lng != null);
    stops.forEach((c, i) => {
      rings.push({ lat: c.lat, lng: c.lng, rgb: "255,178,36", max: 4, period: 1100 });
      const atHome = state.home && Math.abs(c.lat - state.home.lat) < 1.5 && Math.abs(c.lng - state.home.lng) < 1.5;
      labels.push({ lat: c.lat, lng: c.lng, text: atHome ? `${c.n}.` : `${c.n}. ${String(c.place || "").slice(0, 30)}`, size: 0.95, color: "#ffb224" });
      const next = stops[i + 1] || (state.home ? { lat: state.home.lat, lng: state.home.lng } : null);
      if (next && (Math.abs(next.lat - c.lat) > 0.3 || Math.abs(next.lng - c.lng) > 0.3)) {
        arcs.push({ startLat: c.lat, startLng: c.lng, endLat: next.lat, endLng: next.lng });
      }
    });
  }
  for (const st of state.stores) {
    if (st.lat == null || st.lng == null) continue;
    points.push({ lat: st.lat, lng: st.lng, r: 0.05, color: "#f2f4f1", title: `${st.name}${st.rating ? ", " + st.rating + " stars" : ""}`, url: safeUrl(st.url) });
  }
  globe.pointsData(points).ringsData(rings).arcsData(arcs).labelsData(labels);
}

function drawLegend() {
  const box = $("#legend");
  box.replaceChildren(el("span", "legend-title", "On the globe:"));
  for (const l of LAYERS) {
    const b = el("button", "key");
    b.type = "button";
    b.setAttribute("aria-pressed", String(state.layers[l.id]));
    b.title = state.layers[l.id] ? "Click to hide these dots" : "Click to show these dots";
    const dot = el("i"); dot.style.background = l.color;
    b.append(dot, document.createTextNode(l.label));
    b.addEventListener("click", () => { state.layers[l.id] = !state.layers[l.id]; drawLegend(); drawGlobe(); });
    box.append(b);
  }
}

async function loadAtlas() {
  const items = $("#list").value.split("\n").map((x) => x.trim()).filter(Boolean).slice(0, 15).join(",");
  try { state.atlas = await api(`/api/atlas?items=${encodeURIComponent(items)}`); drawGlobe(); } catch { /* the fixed layers are optional */ }
}

/* Open the right-hand panel with a small label and a title. */
function openPanel(key, kicker, title) {
  state.activeItem = key;
  document.querySelectorAll(".why[aria-pressed=true]").forEach((b) => b.setAttribute("aria-pressed", "false"));
  const panel = $("#story");
  panel.hidden = false;
  panel.replaceChildren(el("p", "item", kicker), el("h2", null, title));
  panel.scrollTop = 0;
  return panel;
}
function actionRow(panel, pairs) {
  const row = el("div", "story-actions");
  for (const [text, fn, strong] of pairs) {
    const b = el("button", strong ? "mini strong" : "mini", text);
    b.type = "button";
    b.addEventListener("click", fn);
    row.append(b);
  }
  panel.append(row);
  return row;
}

/* A dot on the globe was clicked: say what it is and what you can ask about it. */
function showPlace(kind, d) {
  flyTo(d.lat, d.lng, 1.5);
  if (kind === "food") {
    const panel = openPanel(`place:${d.place}`, `Where ${d.item} comes from`, d.place);
    panel.append(el("p", "summary", d.note));
    if (d.yours) panel.append(el("p", "tip", `${d.item[0].toUpperCase()}${d.item.slice(1)} is on your shopping list. What happens here can reach your bill.`));
    actionRow(panel, [
      [`Why does ${d.item} cost what it does?`, () => askWhy(d.item), true],
      ["What is happening here right now?", () => showAnswer(`What is happening with ${d.item} production in ${d.place} right now, and could it change prices?`)],
    ]);
  } else if (kind === "routes") {
    const panel = openPanel(`place:${d.name}`, "Shipping route", d.name);
    panel.append(el("p", "summary", d.note));
    panel.append(el("p", "change", "Most of what you buy spends time on a ship. When a route like this slows down, deliveries take longer and cost more."));
    actionRow(panel, [["Is anything slowing it down right now?", () => showAnswer(`Is anything disrupting shipping through the ${d.name} right now, and could it affect prices in the United States?`), true]]);
  } else if (kind === "markets") {
    const panel = openPanel(`place:${d.name}`, "Money market", d.name);
    panel.append(el("p", "summary", d.note));
    actionRow(panel, [
      ["What happened here this week?", () => showAnswer(`In plain language, what were the most important things that happened at the ${d.name} this week?`), true],
      ["Teach me the basics", () => showCards(d.name)],
    ]);
  } else {
    const panel = openPanel(`place:${d.title}`, `Happening now: ${d.kind}`, d.title);
    if (safeUrl(d.url)) { const p = el("p", "change"); p.append(link(d.url, "Open the official report")); panel.append(p); }
    panel.append(el("p", "summary", "Reported by a public monitoring service (the US Geological Survey or NASA). Events like this can interrupt farms, ports and power."));
    actionRow(panel, [["Could this affect prices?", () => showAnswer(`Could this event affect food, fuel or shipping prices? ${d.title}`), true]]);
  }
  drawGlobe();
}

/* A headline was picked: offer the two things you can do with it. */
function showHeadline(h) {
  if (h.lat != null) flyTo(h.lat, h.lng, 1.4);
  const panel = openPanel(`headline:${h.title}`, "In the news", h.title);
  const meta = el("p", "change");
  meta.append(document.createTextNode([h.source, h.published].filter(Boolean).join(", ") + " "));
  if (safeUrl(h.url)) meta.append(link(h.url, "Read the original"));
  panel.append(meta);
  if (h.wallet_impact) panel.append(el("p", "summary", h.wallet_impact));
  if ((h.in_your_basket || []).length) panel.append(el("p", "tip", `On your shopping list: ${h.in_your_basket.join(", ")}`));
  panel.append(choice("Check if it is true", "See who else reports it and whether an official source backs it up.", () => traceHeadline(h)));
  panel.append(choice("Learn from it", "A short lesson: why it matters, who it affects and what experts expect next.", () => showLesson(h)));
}
function choice(title, text, fn) {
  const b = el("button", "choice");
  b.type = "button";
  b.append(el("strong", null, title), el("span", null, text));
  b.addEventListener("click", fn);
  return b;
}

function flyTo(lat, lng, altitude = 1.6) {
  if (!globe || lat == null || lng == null) return;
  globe.controls().autoRotate = false;
  globe.pointOfView({ lat, lng, altitude }, 1600);
}

/* Frame a cause and home together: look at the point between them. */
function frameStory() {
  const d = (state.story?.drivers || state.trace?.chain || []).find((x) => x.lat != null && x.lng != null);
  if (!d || !state.home) return;
  let dLng = d.lng - state.home.lng;
  if (dLng > 180) dLng -= 360;
  if (dLng < -180) dLng += 360;
  const spread = Math.hypot(d.lat - state.home.lat, dLng);
  flyTo((d.lat + state.home.lat) / 2, state.home.lng + dLng / 2, Math.min(2.8, 1.3 + spread / 70));
}

/* ----------------------------------------------------------------- status */
const CONF_WORD = { high: "Well supported", medium: "Partly supported", low: "Weakly supported" };
const confWord = (c) => CONF_WORD[c] || c;

function aiLine(s) {
  if (s.hosted && s.hosted !== "off") {
    const backup = [s.local === "live" ? "the model on this computer" : "", s.gemini === "live" ? "Gemini" : ""].filter(Boolean).join(" and ");
    return [`${s.hosted} from ${s.hosted_by}${backup ? `, with ${backup} as backup` : ""}`, "live"];
  }
  if (s.local === "live") return [`A small model on this computer (${s.local_model})${s.gemini === "live" ? ", with Gemini as backup" : ""}`, "live"];
  if (s.local_model && s.gemini === "live") return [`Gemini. The model on this computer (${s.local_model}) is ${s.local}`, "live"];
  if (s.gemini === "live") return ["Gemini", "live"];
  if (s.local_model) return [`The model on this computer (${s.local_model}) is ${s.local}`, ""];
  return ["Off. Nibble uses simple rules instead", ""];
}

function drawStatus() {
  const s = state.status;
  const box = $("#services");
  box.replaceChildren();
  const add = (job, who, mode) => {
    const li = el("li", mode);
    li.append(el("i"), el("b", null, job), el("span", null, who));
    box.append(li);
  };
  add("Prices and news", s.nimble === "live" ? "Nimble, reading the live web" : "Sample data (no Nimble key yet)", s.nimble === "live" ? "live" : "sample");
  add("Reading and explaining", ...aiLine(s));
  add("Watching for errors", s.sentry === "live" ? "Sentry, recording every step" : "Off (no Sentry key yet)", s.sentry === "live" ? "live" : "");
  $("#fineprint").textContent = s.nimble === "live"
    ? `Live shelf prices through Nimble (${s.stores.join(", ")}). Prices change; check the store before you buy.`
    : "Sample data. Add a Nimble key to .env for live prices.";
}

/* --------------------------------------------------------------- receipt */
function busy(on, label) {
  $("#go").disabled = on; $("#scan").disabled = on;
  $("#go").textContent = on ? label || "Checking stores..." : "Find the cheapest basket";
  $("#pulse").classList.toggle("busy", on);
}

async function findBasket() {
  const items = $("#list").value.split("\n").map((x) => x.trim()).filter(Boolean).slice(0, 15);
  if (!items.length) { $("#list").focus(); return; }
  busy(true);
  try {
    const result = await runJob(post("/api/basket", { items, zip: $("#zip").value }), (j) => busy(true, j.phase && j.phase !== "queued" ? j.phase : "Checking stores..."));
    drawBasket(result, null);
  } catch (err) {
    showReceiptError(err.message);
  } finally { busy(false); }
}

async function readReceipt(file) {
  busy(true, "Reading the receipt...");
  try {
    const form = new FormData();
    form.append("zip", $("#zip").value);
    if (file) form.append("photo", file);
    const result = await runJob(api("/api/receipt", { method: "POST", body: form }), (j) => busy(true, j.phase && j.phase !== "queued" ? j.phase : "Reading the receipt..."));
    if (!result.basket) { showReceiptError((result.notes || ["Nothing could be read from that photo."])[0]); return; }
    drawBasket(result.basket, result);
    drawHabits(result.persona);
  } catch (err) {
    showReceiptError(err.message);
  } finally { busy(false); }
}

function showReceiptError(message) {
  const box = $("#result");
  box.hidden = false;
  box.replaceChildren(el("p", "none", message));
}

function drawBasket(b, receipt) {
  state.home = b.home; $("#place").textContent = b.home.label;
  const box = $("#result");
  box.hidden = false; $("#compose").hidden = true;
  box.replaceChildren();
  if (b.sample || receipt?.receipt_sample) box.append(el("p", "stamp", receipt?.receipt_sample ? "Sample receipt, sample prices" : "Sample prices"));
  if (receipt?.store) box.append(el("p", "paid", `You shopped at ${receipt.store}`));

  b.items.forEach((row, i) => {
    const line = el("div", "line");
    const name = el("div", "line-name");
    name.append(el("span", null, row.item));
    line.append(name);
    const paid = receipt?.lines?.[i];
    if (paid && paid.paid != null) {
      const p = el("p", "paid", `You paid ${money(paid.paid)}`);
      if (paid.difference > 0) { p.append(document.createTextNode(", ")); p.append(el("b", null, `${money(paid.difference)} over the best price`)); }
      line.append(p);
    }
    if (!row.picks.length) line.append(el("p", "none", "No matching product found"));
    row.picks.forEach((p, k) => {
      const offer = el("div", "offer" + (k === 0 ? " best" : ""));
      offer.append(el("span", "store", p.store), el("span", "price", money(p.price)));
      const what = el("span", "what");
      what.append(link(p.url, p.name));
      what.title = p.name;
      offer.append(what);
      const per = perUnit(p);
      const where = p.priced_for ? `priced at ${p.priced_for}` : "";
      const extra = [per, where, p.note].filter(Boolean).join(", ");
      if (extra) offer.append(el("span", "per", extra));
      line.append(offer);
    });
    const why = el("button", "why", "Why this price?");
    why.type = "button";
    why.addEventListener("click", () => askWhy(row.item, why));
    line.append(why);
    box.append(line);
  });

  const t = b.totals;
  const totals = el("div", "totals");
  const rowOf = (label, value, cls) => { const d = el("div", cls); d.append(el("span", null, label), el("span", null, value)); return d; };
  if (receipt?.paid_total != null) totals.append(rowOf("You paid", money(receipt.paid_total)));
  totals.append(rowOf("Cheapest basket", money(t.split), "grand"));
  for (const s of t.by_store) totals.append(rowOf(s.complete ? `All at ${s.store}` : `${s.store} (${s.items_found} items)`, money(s.total)));
  if (receipt && receipt.overpaid > 0) totals.append(rowOf("You overpaid", money(receipt.overpaid), "lost"));
  if (t.saved_vs_priciest_store > 0) totals.append(rowOf("Saved by splitting the trip", money(t.saved_vs_priciest_store), "keep"));
  box.append(totals);
  if (b.missing?.length) box.append(el("p", "fineprint", `Not found: ${b.missing.join(", ")}`));
  if (b.notes?.length) box.append(el("p", "fineprint", b.notes.join(". ")));
  box.append(el("p", "fineprint", `Matched by ${b.matched_by}. Shopping for: ${b.persona}.`));
  const again = el("button", "again", "Edit the list");
  again.type = "button";
  again.addEventListener("click", () => { box.hidden = true; $("#compose").hidden = false; });
  box.append(again);
  const near = el("div", "near");
  box.append(near);
  loadStores(b.home.zip, near);
  api("/api/persona").then(drawProfile).catch(() => {});
  api("/api/news").then(drawNews).catch(() => {});
  loadAtlas();
  drawGlobe();
  flyTo(b.home.lat, b.home.lng, 1.9);
}

async function loadStores(zip, box) {
  try {
    const res = await api(`/api/stores?zip=${encodeURIComponent(zip)}`);
    state.stores = res.stores || [];
    if (!state.stores.length) return;
    box.append(el("p", "near-title", `Grocery stores near ${zip}, from Google Maps through Nimble`));
    for (const st of state.stores.slice(0, 5)) {
      const row = el("div", "near-row");
      const name = el("span");
      name.append(link(st.url, st.name));
      row.append(name, el("span", null, st.rating ? `${st.rating} stars` : ""));
      box.append(row);
    }
    drawGlobe();
  } catch { /* the list is optional */ }
}

function perUnit(p) {
  // Price per unit in the pack's own unit: per count, per lb, per fl oz.
  if (p.pack_quantity && p.pack_unit) {
    const each = p.price / p.pack_quantity;
    const amount = each < 1 ? (each * 100).toFixed(1) + "\u00a2" : "$" + each.toFixed(2);
    return `${amount} per ${p.pack_unit === "l" ? "liter" : p.pack_unit}`;
  }
  return typeof p.shelf_unit_price === "string" ? p.shelf_unit_price : "";
}

function drawHabits(persona) { drawProfile(persona); }

/* What Nibble has learned about this shopper, from counts over past baskets. */
function drawProfile(persona) {
  const node = $("#habits");
  const pat = persona?.patterns;
  node.replaceChildren();
  if (!pat || (!pat.baskets && !(pat.habits || []).length)) { node.hidden = true; return; }
  node.hidden = false;
  node.append(el("strong", null, `Learned from ${pat.baskets} basket${pat.baskets === 1 ? "" : "s"}`));
  const lines = [];
  if (pat.staples.length) lines.push(`You buy most: ${pat.staples.slice(0, 6).join(", ")}`);
  if (pat.cheapest_store) lines.push(`${pat.cheapest_store.store} was cheapest for ${pat.cheapest_store.wins} of ${pat.cheapest_store.of} items`);
  if (pat.saved_so_far > 0) lines.push(`Splitting trips has saved ${money(pat.saved_so_far)}`);
  if ((pat.habits || []).length) lines.push(`Habits on your receipts: ${pat.habits.join(", ")}`);
  for (const line of lines) node.append(el("span", null, line));
  const forget = el("button", "again", "Forget what you learned");
  forget.type = "button";
  forget.addEventListener("click", async () => { drawProfile(await api("/api/persona", { method: "DELETE" })); api("/api/news").then(drawNews).catch(() => {}); });
  node.append(forget);
}

/* ----------------------------------------------------------------- story */
async function askWhy(item, button, effort, lens) {
  document.querySelectorAll(".why[aria-pressed=true]").forEach((b) => b.setAttribute("aria-pressed", "false"));
  if (button) button.setAttribute("aria-pressed", "true");
  state.activeItem = item;
  const panel = $("#story");
  panel.hidden = false;
  panel.replaceChildren(el("p", "item", lens ? `Market lens: ${item}` : item),
    el("h2", null, lens ? "Finding what this touches" : "Tracing this price back to its cause"));
  const wait = el("p", "wait");
  const label = el("span", null, `The ${lens ? "Market Lens" : "Price Detective"} agent is starting`);
  wait.append(el("i"), label);
  panel.append(wait);
  $("#pulse").classList.add("busy");
  try {
    const start = lens ? post("/api/lens", { topic: item, zip: $("#zip").value, effort }) : post("/api/why", { item, zip: $("#zip").value, effort });
    const story = await runJob(start, (j) => {
      label.textContent = state.status.nimble === "live"
        ? `Nimble agent is ${j.phase === "queued" ? "queued" : "reading the web"} (${Math.round(j.elapsed)} s)`
        : "Loading the sample explanation";
    });
    if (state.activeItem !== item) return;
    state.story = story; state.trace = null; state.home = story.home || state.home;
    drawStory(story);
    drawGlobe();
    frameStory();
  } catch (err) {
    panel.append(el("p", "error", `That run failed: ${err.message}`));
    wait.remove();
  } finally { $("#pulse").classList.remove("busy"); }
}

function citesOf(list) {
  const box = el("div", "cites");
  for (const c of (list || []).slice(0, 3)) {
    const cite = el("div", "cite");
    cite.append(link(c.url, c.title || c.url));
    if (c.excerpts?.[0]) cite.append(el("q", null, c.excerpts[0].slice(0, 220)));
    box.append(cite);
  }
  return box;
}

function drawStory(s) {
  const panel = $("#story");
  panel.replaceChildren();
  panel.append(el("p", "item", `${s.lens ? "Market lens: " : ""}${s.item}${s.sample ? " (sample explanation)" : ""}`));
  panel.append(el("h2", null, s.headline || "No clear cause found"));
  if (s.plain_summary) panel.append(el("p", "summary", s.plain_summary));
  if (s.change_summary && s.change_summary !== s.plain_summary) panel.append(el("p", "change", s.change_summary));

  if (s.drivers?.length) panel.append(el("h3", null, "Where it starts"));
  for (const d of s.drivers || []) {
    const box = el("div", "driver");
    const top = el("div", "driver-top");
    top.append(el("span", "driver-title", d.title));
    if (d.confidence) top.append(el("span", `conf ${d.confidence}`, confWord(d.confidence)));
    box.append(top);
    const place = el("button", "place-btn", d.location_name || "");
    place.type = "button";
    place.addEventListener("click", () => flyTo(d.lat, d.lng, 1.1));
    if (d.lat == null) place.disabled = true;
    box.append(place, el("p", null, d.explanation));
    if (d.citations?.length) box.append(citesOf(d.citations));
    panel.append(box);
  }

  if (s.exposed?.length) panel.append(el("h3", null, "Who else this touches"));
  for (const x of s.exposed || []) {
    const box = el("div", "tied");
    const top = el("div", "tied-top");
    top.append(el("span", "tied-name", x.name));
    if (x.ticker) top.append(el("span", "ticker", x.ticker));
    top.append(el("span", "kind", x.kind));
    box.append(top, el("p", null, x.relationship));
    if (x.citations?.length) box.append(citesOf(x.citations));
    if (x.kind === "company") {
      const actions = el("div", "story-actions");
      if (x.ticker) {
        const q = el("button", "mini", "Get a live quote");
        q.type = "button";
        q.addEventListener("click", () => getQuote(x, q, box));
        actions.append(q);
      }
      const scout = el("button", "mini", "Scout its signals");
      scout.type = "button";
      scout.addEventListener("click", () => scoutSignals(x.name, scout, box));
      actions.append(scout);
      box.append(actions);
    }
    panel.append(box);
  }

  if (s.outlook) { panel.append(el("h3", null, "What sources expect")); panel.append(el("p", "summary", s.outlook)); }
  if ((s.risks || []).length) {
    panel.append(el("h3", null, "What could change this"));
    for (const r of s.risks) panel.append(el("p", "summary", r));
  }
  if (s.shopper_tip) panel.append(el("p", "tip", s.shopper_tip));

  const prov = el("p", "prov");
  if (s.sample) {
    prov.textContent = "Sample explanation with no citations. With a Nimble key the Price Detective agent researches any item and cites every claim.";
  } else {
    const t = s.trust || {};
    prov.append(document.createTextNode(
      `Researched by a Nimble Web Search Agent${s.cached ? " (cached run)" : ` in ${s.seconds} s`}, ${s.effort} effort. ` +
      `Overall confidence: ${t.confidence || "not graded"}. ${(t.sources || []).length} sources read. `));
  }
  prov.append(document.createTextNode(" This explains what is happening. It is not investment advice."));
  panel.append(prov);

  if (!s.sample && s.effort === "low") {
    const actions = el("div", "story-actions");
    const deeper = el("button", "mini", "Dig deeper (1 to 3 minutes)");
    deeper.type = "button";
    deeper.addEventListener("click", () => askWhy(s.item, null, "medium", s.lens));
    actions.append(deeper);
    panel.append(actions);
  }
  panel.append(askBox(`the price of ${s.item}`));
}

/* A follow-up question, answered by Nimble Answer with numbered sources. */
function askBox(topic) {
  const form = el("form", "ask");
  const input = el("input");
  input.type = "text"; input.maxLength = 200; input.placeholder = "Ask a follow-up question";
  input.setAttribute("aria-label", "Ask a follow-up question");
  const go = el("button", "mini", "Ask");
  const out = el("div", "ask-out");
  form.append(input, go, out);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const q = input.value.trim();
    if (q.length < 3) return;
    go.disabled = true; go.textContent = "Asking...";
    out.replaceChildren();
    try {
      const res = await post("/api/ask", { question: /\?$/.test(q) && q.split(" ").length > 6 ? q : `${q} (about ${topic})` });
      out.append(el("p", "summary", res.answer || "No answer found."));
      const cites = el("div", "cites");
      for (const c of res.citations || []) {
        const cite = el("div", "cite");
        cite.append(document.createTextNode(`[${c.marker}] `), link(c.url, c.title || c.url));
        cites.append(cite);
      }
      out.append(cites);
    } catch (err) {
      out.append(el("p", "error", err.message));
    } finally { go.disabled = false; go.textContent = "Ask"; }
  });
  return form;
}

/* ------------------------------------------------------- headline tracing */
const VERDICT = {
  corroborated: ["Backed up by other sources", "high"],
  single_source: ["Only one source so far", ""],
  disputed: ["Sources disagree", "low"],
  unclear: ["Could not be confirmed", ""],
};
const STAGE = { trigger: "It starts", supply: "Supply changes", market: "Prices react", company: "Companies feel it", shelf: "Store shelf", household: "Your home" };

async function traceHeadline(h, effort) {
  state.activeItem = `news:${h.title}`;
  document.querySelectorAll(".why[aria-pressed=true]").forEach((b) => b.setAttribute("aria-pressed", "false"));
  const panel = $("#story");
  panel.hidden = false;
  panel.replaceChildren(el("p", "item", "On the wire"), el("h2", null, h.title));
  const meta = el("p", "change");
  meta.append(document.createTextNode([h.source, h.published].filter(Boolean).join(", ") + " "));
  if (safeUrl(h.url)) meta.append(link(h.url, "Read the original"));
  panel.append(meta);
  if (h.wallet_impact) panel.append(el("p", "summary", h.wallet_impact));
  const wait = el("p", "wait");
  const label = el("span", null, "The News Tracer agent is starting");
  wait.append(el("i"), label);
  panel.append(wait);
  $("#pulse").classList.add("busy");
  const key = state.activeItem;
  try {
    const body = { title: h.title, url: h.url || null, description: h.description || null, source: h.source || null,
      published: h.published || null, place: h.place || null, lat: h.lat ?? null, lng: h.lng ?? null,
      groceries: h.groceries || null, zip: $("#zip").value, effort };
    const t = await runJob(post("/api/trace", body), (j) => {
      label.textContent = state.status.nimble === "live"
        ? `Nimble agent is checking sources and tracing the chain (${Math.round(j.elapsed)} s)`
        : "Loading a sample chain";
    });
    if (state.activeItem !== key) return;
    state.trace = t; state.story = null; state.home = t.home || state.home;
    drawTrace(t, h);
    drawGlobe();
    frameStory();
  } catch (err) {
    wait.remove();
    panel.append(el("p", "error", `That run failed: ${err.message}`));
  } finally { $("#pulse").classList.remove("busy"); }
}

function drawTrace(t, h) {
  const panel = $("#story");
  panel.replaceChildren();
  panel.append(el("p", "item", `On the wire${t.sample ? " (sample chain)" : ""}`));
  panel.append(el("h2", null, t.original.title));
  const meta = el("p", "change");
  meta.append(document.createTextNode([t.original.source, t.original.published].filter(Boolean).join(", ") + " "));
  if (safeUrl(t.original.url)) meta.append(link(t.original.url, "Read the original"));
  panel.append(meta);

  panel.append(el("h3", null, "Is it true?"));
  const [word, cls] = VERDICT[t.verdict] || VERDICT.unclear;
  const verdict = el("div", "verdict");
  verdict.append(el("span", `conf ${cls}`, word));
  const c = t.check || {};
  if (!t.sample) {
    verdict.append(el("span", "kind", `${c.independent_sites} other site${c.independent_sites === 1 ? "" : "s"} read, ${c.official_sites} official`));
  }
  panel.append(verdict);
  if (t.verdict_reason) panel.append(el("p", "summary", t.verdict_reason));
  if (t.primary_source) panel.append(el("p", "change", `Original data: ${t.primary_source}`));
  if ((c.sites || []).length) {
    const list = el("div", "cites");
    for (const site of c.sites.slice(0, 8)) {
      const cite = el("div", "cite");
      cite.append(el("span", "ticker", site.is_original ? "the story " : site.category === "official" ? "official " : ""));
      cite.append(link(site.pages[0].url, site.pages[0].title || site.site));
      cite.append(document.createTextNode(` (${site.site}${site.pages.length > 1 ? ", " + site.pages.length + " pages" : ""})`));
      list.append(cite);
    }
    panel.append(list);
  }

  panel.append(el("h3", null, "How it reaches you"));
  (t.chain || []).forEach((link_, i) => {
    const box = el("div", "driver");
    const top = el("div", "driver-top");
    top.append(el("span", "driver-title", `${i + 1}. ${STAGE[link_.stage] || link_.stage}`));
    if (link_.confidence) top.append(el("span", `conf ${link_.confidence}`, confWord(link_.confidence)));
    box.append(top);
    const place = el("button", "place-btn", link_.place || "");
    place.type = "button";
    place.disabled = link_.lat == null;
    place.addEventListener("click", () => flyTo(link_.lat, link_.lng, 1.1));
    box.append(place, el("p", null, link_.what));
    if (link_.citations?.length) box.append(citesOf(link_.citations));
    panel.append(box);
  });
  if (!(t.chain || []).length) panel.append(el("p", "summary", "The agent could not build a chain from its sources."));

  if ((t.affected_groceries || []).length) {
    panel.append(el("h3", null, "What it touches"));
    panel.append(el("p", "summary", t.affected_groceries.join(", ")));
  }
  if ((t.in_your_basket || []).length) panel.append(el("p", "tip", `On your list: ${t.in_your_basket.join(", ")}`));
  if (t.what_to_watch) { panel.append(el("h3", null, "What to watch")); panel.append(el("p", "summary", t.what_to_watch)); }

  const prov = el("p", "prov");
  prov.textContent = t.sample
    ? "Sample chain with no sources. With a Nimble key the News Tracer agent checks the story and cites each link."
    : `Checked and traced by a Nimble Web Search Agent${t.cached ? " (cached run)" : ` in ${t.seconds} s`}. Overall: ${confWord(t.trust?.confidence) || "not graded"}. It explains what is reported. It is not investment advice.`;
  panel.append(prov);
  if (!t.sample && t.effort === "low") {
    const actions = el("div", "story-actions");
    const deeper = el("button", "mini", "Check more sources (1 to 3 minutes)");
    deeper.type = "button";
    deeper.addEventListener("click", () => traceHeadline(h, "medium"));
    actions.append(deeper);
    panel.append(actions);
  }
  if (!t.sample) {
    const actions = el("div", "story-actions");
    const learn = el("button", "mini strong", "Learn from this story");
    learn.type = "button";
    learn.addEventListener("click", () => showLesson(h));
    const who = el("button", "mini", "Which companies does it touch?");
    who.type = "button";
    who.addEventListener("click", () => askWhy(t.claim || t.original.title, null, undefined, true));
    actions.append(learn, who);
    panel.append(actions);
  }
  panel.append(askBox(t.original.title));
}

/* ------------------------------------------------------------------ season */
async function showSeason() {
  state.activeItem = "season"; state.story = null; state.trace = null;
  const panel = $("#story");
  panel.hidden = false;
  panel.replaceChildren(el("p", "item", "In season"), el("h2", null, "Finding what is in season near you"));
  const wait = el("p", "wait"); wait.append(el("i"), el("span", null, "Asking Nimble"));
  panel.append(wait);
  try {
    const s = await api(`/api/season?zip=${encodeURIComponent($("#zip").value)}`);
    if (state.activeItem !== "season") return;
    panel.replaceChildren(el("p", "item", `In season${s.sample ? " (sample list)" : ""}`),
      el("h2", null, `${s.month} near ${s.home.label}`));
    if (!s.items.length && s.answer) panel.append(el("p", "summary", s.answer));
    const added = [];
    for (const it of s.items) {
      const row = el("div", "pick");
      row.append(el("b", null, it.name), el("span", null, it.why_now));
      const add = el("button", "mini", "Add to list");
      add.type = "button";
      add.addEventListener("click", () => {
        const list = $("#list");
        list.value = (list.value.trim() ? list.value.trim() + "\n" : "") + (it.list_name || it.name);
        add.disabled = true; add.textContent = "Added";
        $("#result").hidden = true; $("#compose").hidden = false;
        added.push(it.name);
      });
      row.append(add);
      panel.append(row);
    }
    if (s.items.length) {
      const actions = el("div", "story-actions");
      const price = el("button", "mini", "Price my list now");
      price.type = "button";
      price.addEventListener("click", findBasket);
      actions.append(price);
      panel.append(actions);
    }
    if ((s.citations || []).length) {
      panel.append(el("h3", null, "Sources"));
      const cites = el("div", "cites");
      for (const c of s.citations) { const cite = el("div", "cite"); cite.append(link(c.url, c.title || c.url)); cites.append(cite); }
      panel.append(cites);
    }
    panel.append(el("p", "prov", s.sample
      ? "Sample list for the season. With a Nimble key this is looked up for your state and month."
      : "Looked up with Nimble Answer; Gemini turned it into list items. In-season produce is usually cheaper, but check the shelf price."));
    drawGlobe();
  } catch (err) {
    wait.remove(); panel.append(el("p", "error", err.message));
  }
}

/* -------------------------------------------------------------- flashcards */
async function showCards(topic) {
  state.activeItem = "learn"; state.story = null; state.trace = null;
  const panel = $("#story");
  panel.hidden = false;
  panel.replaceChildren(el("p", "item", "Learn"), el("h2", null, topic ? `Building cards on ${topic}` : "Building cards from today's news"));
  const wait = el("p", "wait"); wait.append(el("i"), el("span", null, "Nimble is reading the news, Gemini is writing the cards"));
  panel.append(wait);
  try {
    const deck = await post("/api/learn", { topic: topic || "" });
    if (state.activeItem !== "learn") return;
    let at = 0, flipped = false, score = 0, answered = new Set();
    const render = () => {
      const c = deck.cards[at];
      panel.replaceChildren(el("p", "item", `Learn${deck.sample ? " (sample cards)" : ""}`),
        el("h2", null, topic ? `Finance cards: ${topic}` : "Finance cards from today's news"));
      if (!c) { panel.append(el("p", "summary", "No cards could be built.")); return; }
      const card = el("button", "card" + (flipped ? " flipped" : ""));
      card.type = "button";
      card.append(el("span", "term", c.term), el("span", "face", flipped ? c.back : c.front));
      if (flipped && c.example) card.append(el("span", "eg", c.example));
      card.append(el("span", "turn", flipped ? "Tap to see the question" : "Tap to see the answer"));
      card.addEventListener("click", () => { flipped = !flipped; render(); });
      panel.append(card);
      if (flipped && c.source) {
        const src = el("div", "cite"); src.append(document.createTextNode("From: "), link(c.source.url, c.source.title)); panel.append(src);
        const check = el("button", "mini", "Check this story's sources");
        check.type = "button";
        const h = state.news.find((n) => n.url === c.source.url);
        check.addEventListener("click", () => traceHeadline(h || { title: c.source.title, url: c.source.url, source: c.source.outlet }));
        panel.append(check);
      }
      if (flipped && c.quiz_question) {
        const quiz = el("div", "quiz");
        quiz.append(el("p", null, c.quiz_question));
        c.quiz_options.forEach((opt, i) => {
          const b = el("button", null, opt); b.type = "button";
          b.addEventListener("click", () => {
            if (answered.has(at)) return;
            answered.add(at);
            if (i === c.quiz_answer) score += 1;
            quiz.querySelectorAll("button").forEach((x, k) => x.classList.add(k === c.quiz_answer ? "right" : k === i ? "wrong" : "none"));
            tally.textContent = `Card ${at + 1} of ${deck.cards.length}, ${score} of ${answered.size} right`;
          });
          quiz.append(b);
        });
        panel.append(quiz);
      }
      const nav = el("div", "deck-nav");
      const prev = el("button", "mini", "Back"); prev.type = "button"; prev.disabled = at === 0;
      const next = el("button", "mini", "Next card"); next.type = "button"; next.disabled = at >= deck.cards.length - 1;
      const tally = el("span", null, `Card ${at + 1} of ${deck.cards.length}${answered.size ? `, ${score} of ${answered.size} right` : ""}`);
      prev.addEventListener("click", () => { at -= 1; flipped = false; render(); });
      next.addEventListener("click", () => { at += 1; flipped = false; render(); });
      nav.append(prev, tally, next);
      panel.append(nav);
      panel.append(el("p", "prov", deck.sample ? (deck.note || "Sample cards.")
        : "Headlines found by Nimble, cards written by Gemini from those headlines. For learning, not investment advice."));
    };
    render();
  } catch (err) {
    wait.remove(); panel.append(el("p", "error", err.message));
  }
}

/* ------------------------------------------------------- investing lessons */
const EFFECT = {
  "costs rise": ["Costs go up", "low"], "costs fall": ["Costs go down", "high"],
  "sales rise": ["Sales go up", "high"], "sales fall": ["Sales go down", "low"], mixed: ["Mixed", ""],
};
const NOT_ADVICE = "Nibble teaches how to read the news. It does not tell you what to buy or sell.";

/* The "Learn investing" start page: pick a story, type a topic, or practise with cards. */
function showLearn() {
  state.story = null; state.trace = null;
  const panel = openPanel("learn-hub", "Learn investing", "Learn from what is happening today");
  panel.append(el("p", "summary", "Pick a story. Nibble walks you through it in six short steps: what happened, why it matters, how it spreads, who feels it, what experts expect, and what could prove them wrong."));
  for (const h of state.news.slice(0, 6)) {
    panel.append(choice(h.title, [h.source, (h.in_your_basket || []).length ? `on your list: ${h.in_your_basket.join(", ")}` : ""].filter(Boolean).join(", "), () => showLesson(h)));
  }
  if (!state.news.length) panel.append(el("p", "change", "No headlines have loaded yet. Type a topic below instead."));
  const form = el("form", "ask");
  const input = el("input");
  input.type = "text"; input.maxLength = 160; input.placeholder = "Or type a topic: interest rates, oil, tariffs";
  input.setAttribute("aria-label", "A topic to learn about");
  const go = el("button", "mini", "Teach me");
  form.append(input, go);
  form.addEventListener("submit", (e) => { e.preventDefault(); const q = input.value.trim(); if (q.length > 2) showLesson({ title: q }); });
  panel.append(form);
  actionRow(panel, [["Practise with flashcards", () => showCards("")]]);
  panel.append(el("p", "prov", NOT_ADVICE));
  drawGlobe();
}

async function showLesson(h, effort) {
  const key = `lesson:${h.title}`;
  state.story = null; state.trace = null;
  const panel = openPanel(key, "Lesson", h.title);
  const wait = el("p", "wait");
  const label = el("span", null, "Starting the lesson");
  wait.append(el("i"), label);
  panel.append(wait, el("p", "change", "Nibble reads official reports and news about this story, then writes the lesson. This takes about a minute."));
  $("#pulse").classList.add("busy");
  if (h.lat != null) flyTo(h.lat, h.lng, 1.6);
  try {
    const body = { title: h.title, url: h.url || null, description: h.description || null, source: h.source || null,
      published: h.published || null, place: h.place || null, lat: h.lat ?? null, lng: h.lng ?? null,
      groceries: h.groceries || null, zip: $("#zip").value, effort };
    const l = await runJob(post("/api/lesson", body), (j) => {
      label.textContent = state.status.nimble === "live" ? `Reading the sources (${Math.round(j.elapsed)} seconds)` : "Loading a sample lesson";
    });
    if (state.activeItem !== key) return;
    state.trace = { chain: l.chain }; state.story = null; state.home = l.home || state.home;
    drawLesson(l, h);
    drawGlobe();
    frameStory();
  } catch (err) {
    wait.remove();
    panel.append(el("p", "error", `The lesson could not be built: ${err.message}`));
  } finally { $("#pulse").classList.remove("busy"); }
}

function drawLesson(l, h) {
  const panel = $("#story");
  panel.replaceChildren();
  panel.scrollTop = 0;
  panel.append(el("p", "item", `Lesson${l.sample ? " (sample)" : ""}`), el("h2", null, l.original.title));
  const meta = el("p", "change");
  meta.append(document.createTextNode([l.original.source, l.original.published].filter(Boolean).join(", ") + " "));
  if (safeUrl(l.original.url)) meta.append(link(l.original.url, "Read the original"));
  panel.append(meta);

  let n = 0;
  const step = (title) => {
    n += 1;
    const head = el("h3", "step");
    head.append(el("b", null, String(n)), document.createTextNode(title));
    panel.append(head);
  };

  if (l.what_happened) { step("What happened?"); panel.append(el("p", "summary", l.what_happened)); }
  if (l.why_it_matters) { step("Why should I care?"); panel.append(el("p", "summary", l.why_it_matters)); }

  if ((l.chain || []).length) {
    step("How does it spread?");
    panel.append(el("p", "hint", "Follow the numbers on the globe. Click a place to fly there."));
    l.chain.forEach((c, i) => {
      const box = el("div", "driver");
      const top = el("div", "driver-top");
      const place = el("button", "place-btn", `${i + 1}. ${c.place || "Step " + (i + 1)}`);
      place.type = "button";
      place.disabled = c.lat == null;
      place.addEventListener("click", () => flyTo(c.lat, c.lng, 1.1));
      top.append(place);
      if (c.confidence) top.append(el("span", `conf ${c.confidence}`, confWord(c.confidence)));
      box.append(top, el("p", null, c.what));
      if (c.citations?.length) box.append(citesOf(c.citations));
      panel.append(box);
    });
  }

  if ((l.affected || []).length) {
    step("Who feels it?");
    for (const x of l.affected) {
      const box = el("div", "tied");
      const top = el("div", "tied-top");
      top.append(el("span", "tied-name", x.name));
      if (x.ticker) top.append(el("span", "ticker", x.ticker));
      const [word, cls] = EFFECT[x.effect] || [x.effect, ""];
      top.append(el("span", `conf ${cls}`, word));
      box.append(top, el("p", null, x.because));
      if (x.citations?.length) box.append(citesOf(x.citations));
      if (x.kind === "company" && x.ticker) {
        const actions = el("div", "story-actions");
        const q = el("button", "mini", "See today's share price");
        q.type = "button";
        q.addEventListener("click", () => getQuote(x, q, box));
        actions.append(q);
        box.append(actions);
      }
      panel.append(box);
    }
  }

  if ((l.expected || []).length) {
    step("What do experts expect?");
    panel.append(el("p", "hint", "These are forecasts published by the people named. They are not Nibble's predictions, and forecasts are often wrong."));
    for (const e of l.expected) {
      const box = el("div", "expect");
      box.append(el("b", null, e.who), el("p", null, e.expects));
      if (e.by_when) box.append(el("span", "when", `By: ${e.by_when}`));
      if (e.citations?.length) box.append(citesOf(e.citations));
      panel.append(box);
    }
  }

  if ((l.could_change || []).length) {
    step("What could prove them wrong?");
    const list = el("ul", "plain");
    for (const r of l.could_change) list.append(el("li", null, r));
    panel.append(list);
  }
  if (l.what_to_watch) { step("What to watch next"); panel.append(el("p", "summary", l.what_to_watch)); }

  if ((l.concepts || []).length) {
    step("Words to know");
    const dl = el("dl", "terms");
    for (const c of l.concepts) dl.append(el("dt", null, c.term), el("dd", null, c.meaning));
    panel.append(dl);
  }

  if (l.quiz) {
    step("Check yourself");
    const quiz = el("div", "quiz");
    quiz.append(el("p", null, l.quiz.question));
    const why = el("p", "hint");
    l.quiz.options.forEach((opt, i) => {
      const b = el("button", null, opt); b.type = "button";
      b.addEventListener("click", () => {
        if (quiz.dataset.done) return;
        quiz.dataset.done = "1";
        quiz.querySelectorAll("button").forEach((x, k) => x.classList.add(k === l.quiz.answer ? "right" : k === i ? "wrong" : "none"));
        why.textContent = `${i === l.quiz.answer ? "Right." : "Not quite."} ${l.quiz.why || ""}`;
      });
      quiz.append(b);
    });
    quiz.append(why);
    panel.append(quiz);
  }

  const prov = el("p", "prov");
  prov.textContent = l.sample
    ? `Sample lesson with no sources. With a Nimble key, a research agent reads the web and cites each step. ${NOT_ADVICE}`
    : `Researched by a Nimble Web Search Agent${l.cached ? " (saved from earlier)" : ` in ${l.seconds} seconds`}, ${l.trust?.sources ?? 0} sources read. ` +
      `Overall: ${confWord(l.trust?.confidence) || "not graded"}. ${NOT_ADVICE}`;
  panel.append(prov);
  const pairs = [];
  if (h.url) pairs.push(["Check if this story is true", () => traceHeadline(h)]);
  if (!l.sample && l.effort === "low") pairs.push(["Go deeper (1 to 3 minutes)", () => showLesson(h, "medium")]);
  pairs.push(["Pick another story", showLearn]);
  actionRow(panel, pairs);
  panel.append(askBox(l.original.title));
}

/* ------------------------------------------------------------ start page */
function showWelcome() {
  state.story = null; state.trace = null;
  const panel = openPanel("welcome", "Start here", "See how the world reaches your wallet");
  panel.append(el("p", "summary", "Nibble finds the cheapest price for your groceries, then shows you why prices move and what the news means for your money."));
  panel.append(choice("1. Price my groceries", "Type a list on the left. Nibble checks real store shelves and picks the cheapest of each.", focusList));
  panel.append(choice("2. See today's news", "Stories that touch food, fuel and money. Check whether each one is true.", showNews));
  panel.append(choice("3. Learn investing", "Turn a real story into a short lesson: why, how, and what experts expect.", showLearn));
  panel.append(choice("4. What is in season", "Produce that is at its peak near you this month, and usually cheaper.", showSeason));
  panel.append(el("h3", null, "The globe"));
  panel.append(el("p", "change", "Every dot is something that can move a price: a farm region, a shipping route, a market, a news story, a storm. Click any dot to see what it is. Drag to turn the globe, scroll to zoom."));
  panel.append(el("p", "prov", NOT_ADVICE));
  drawGlobe();
}

function focusList() {
  $("#result").hidden = true; $("#compose").hidden = false;
  $("#list").focus();
  document.querySelector(".receipt").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function showNews() {
  state.story = null; state.trace = null;
  const panel = openPanel("news", "Today's news", "What is moving prices today");
  panel.append(el("p", "summary", "Found by Nimble a few minutes ago. Pick a story to check it or learn from it."));
  for (const h of state.news) {
    panel.append(choice(h.title, [h.source, h.wallet_impact].filter(Boolean).join(": "), () => showHeadline(h)));
  }
  if (!state.news.length) panel.append(el("p", "change", "No headlines right now."));
  drawGlobe();
}

/* ------------------------------------------------------- one box for it all */
async function say(text) {
  const go = $("#say .go");
  go.disabled = true; go.textContent = "...";
  try {
    const r = await post("/api/route", { text });
    if (r.intent === "basket" && r.items.length) {
      $("#list").value = r.items.join("\n");
      $("#result").hidden = true; $("#compose").hidden = false;
      await findBasket();
    } else if (r.intent === "season") {
      await showSeason();
    } else if (r.intent === "why" && r.topic) {
      await askWhy(r.topic);
    } else if (r.intent === "invest") {
      await showLesson({ title: r.topic || text });
    } else if (r.intent === "learn") {
      await showCards(r.topic);
    } else {
      await showAnswer(text);
    }
  } catch (err) {
    toast({ title: err.message, source: "Nibble could not do that" }, 12000);
  } finally { go.disabled = false; go.textContent = "Ask"; }
}

async function showAnswer(question) {
  state.activeItem = "ask"; state.story = null; state.trace = null;
  const panel = $("#story");
  panel.hidden = false;
  panel.replaceChildren(el("p", "item", "You asked"), el("h2", null, question));
  const wait = el("p", "wait"); wait.append(el("i"), el("span", null, "Asking Nimble"));
  panel.append(wait);
  try {
    const res = await post("/api/ask", { question });
    wait.remove();
    panel.append(el("p", "summary", res.answer || "No answer found."));
    const cites = el("div", "cites");
    for (const c of res.citations || []) { const cite = el("div", "cite"); cite.append(document.createTextNode(`[${c.marker}] `), link(c.url, c.title || c.url)); cites.append(cite); }
    panel.append(cites, askBox(question));
  } catch (err) { wait.remove(); panel.append(el("p", "error", err.message)); }
}

/* ------------------------------------------------------------ pop-up news */
const seenNews = new Set();

function toast(h, ms = 30000) {
  const box = $("#toasts");
  while (box.children.length >= 3) box.firstChild.remove();
  const t = el("div", "toast");
  t.append(el("span", "when", [h.source, h.published].filter(Boolean).join(", ") || "Just in"));
  t.append(el("strong", null, h.title));
  if ((h.in_your_basket || []).length) t.append(el("span", "hit", `On your list: ${h.in_your_basket.join(", ")}`));
  const close = () => t.remove();
  if (h.url !== undefined || h.lat != null) {
    const acts = el("div", "acts");
    if (h.kind) {
      const see = el("button", null, "Show on the globe"); see.type = "button";
      see.addEventListener("click", () => { flyTo(h.lat, h.lng, 1.2); close(); });
      acts.append(see);
    } else {
      const trace = el("button", null, "Check if it is true"); trace.type = "button";
      trace.addEventListener("click", () => { if (h.lat != null) flyTo(h.lat, h.lng, 1.4); traceHeadline(h); close(); });
      const learn = el("button", null, "Learn from it"); learn.type = "button";
      learn.addEventListener("click", () => { showLesson(h); close(); });
      acts.append(trace, learn);
    }
    t.append(acts);
  }
  const x = el("button", "x", "\u00d7"); x.type = "button"; x.setAttribute("aria-label", "Dismiss");
  x.addEventListener("click", close);
  t.append(x, el("i", "life"));
  if (ms !== 30000) t.querySelector(".life").style.animationDuration = `${ms}ms`;
  box.append(t);
  setTimeout(close, ms);
}

async function pollNews(first) {
  try {
    const res = await api("/api/news");
    drawNews(res);
    const fresh = (res.headlines || []).filter((h) => !seenNews.has(h.title));
    fresh.forEach((h) => seenNews.add(h.title));
    // On load, announce the one that matters most; afterwards, anything new.
    (first ? fresh.slice(0, 1) : fresh.slice(0, 3)).forEach((h) => toast(h));
  } catch { if (first) drawNews({ headlines: [] }); }
}

async function pollWorld(first) {
  try {
    const w = await api("/api/world");
    const before = new Set(state.world.map((e) => e.title));
    state.world = w.events || [];
    drawGlobe();
    if (first) return;
    state.world.filter((e) => !before.has(e.title) && (e.kind !== "earthquake" || e.size >= 6)).slice(0, 2)
      .forEach((e) => toast({ title: e.title, source: `Happening now: ${e.kind}`, lat: e.lat, lng: e.lng, kind: e.kind, url: e.url || null }));
  } catch { /* the feed is optional */ }
}

async function scoutSignals(company, button, box) {
  button.disabled = true; button.textContent = "Scouting...";
  try {
    const res = await runJob(post("/api/signals", { company }), (j) => { button.textContent = `Scouting (${Math.round(j.elapsed)} s)`; });
    const list = el("ul", "signals");
    for (const sig of res.signals || []) {
      const li = el("li");
      li.append(el("b", null, `${sig.signal}: `), document.createTextNode(`${sig.reading} (${sig.direction}). ${sig.why_it_matters}`));
      if (sig.citations?.[0]) { li.append(document.createTextNode(" ")); li.append(link(sig.citations[0].url, "source")); }
      list.append(li);
    }
    if (!(res.signals || []).length) list.append(el("li", null, "No signals found."));
    if (res.sample) list.append(el("li", null, "Sample readings. Add a Nimble key for live ones."));
    box.append(list);
    button.textContent = "Signals scouted";
  } catch (err) {
    button.disabled = false; button.textContent = "Scout its signals";
    box.append(el("p", "error", err.message));
  }
}

async function getQuote(x, button, box) {
  button.disabled = true; button.textContent = "Fetching...";
  try {
    const q = await runJob(post("/api/quote", { ticker: x.ticker.replace(/[^A-Za-z.]/g, ""), exchange: ["xnys", "xnas"].includes(x.exchange) ? x.exchange : null }), (j) => { button.textContent = `Fetching (${Math.round(j.elapsed)} s)`; });
    const list = el("ul", "signals");
    const li = el("li");
    if (q.price) {
      li.append(el("b", null, `${x.ticker} ${q.price}`), document.createTextNode(
        ` ${q.change_percent || q.change || ""}. 52-week range ${q["52_week_low"] || "?"} to ${q["52_week_high"] || "?"}. ${q.source}.`));
    } else {
      li.textContent = q.note || "No quote available.";
    }
    list.append(li); box.append(list);
    button.textContent = "Quote fetched";
  } catch (err) {
    button.disabled = false; button.textContent = "Get a live quote";
    box.append(el("p", "error", err.message));
  }
}

/* ------------------------------------------------------------------ wire */
function drawNews(res) {
  state.news = res.headlines || [];
  const list = $("#headlines");
  list.replaceChildren();
  if (!state.news.length) { list.append(el("li", null, "No headlines right now.")); return; }
  for (const h of state.news) {
    const li = el("li", "headline");
    li.style.setProperty("--cat", (h.in_your_basket || []).length ? "#ffb224" : COLOR.news);
    const title = el("button", "headline-title");
    title.type = "button";
    title.append(el("strong", null, h.title));
    title.addEventListener("click", () => showHeadline(h));
    li.append(title);
    if ((h.in_your_basket || []).length) li.append(el("em", "hit", `On your list: ${h.in_your_basket.join(", ")}`));
    const row = el("div", "headline-acts");
    const check = el("button", null, "Check it"); check.type = "button";
    check.addEventListener("click", () => { if (h.lat != null) flyTo(h.lat, h.lng, 1.4); traceHeadline(h); });
    const learn = el("button", null, "Learn from it"); learn.type = "button";
    learn.addEventListener("click", () => showLesson(h));
    row.append(check, learn, el("span", null, h.source || ""));
    li.append(row);
    list.append(li);
  }
  drawGlobe();
}

/* -------------------------------------------------------------- activity */
async function refreshActivity() {
  try {
    const a = await api("/api/activity");
    const c = a.counts;
    if ($("#activity").hidden) return;
    if (!refreshActivity.pinned) {
      $("#activity-note").textContent = `So far: ${c.nimble} web lookups by Nimble, ${c.hosted || 0} answers from the hosted model, ` +
        `${c.local || 0} from the model on this computer, ${c.gemini} from Gemini, ${c.errors} errors. ` + (state.status.sentry === "live"
          ? "Each step below is also recorded in Sentry."
          : "Add SENTRY_DSN to .env and each step below is recorded in Sentry.");
    }
    const list = $("#activity-list");
    list.replaceChildren();
    for (const e of a.events) {
      const li = el("li", e.ok ? "" : "bad");
      li.append(el("span", null, `${e.agent ? e.agent + ": " : ""}${e.name}${e.ok ? "" : " failed"}`), el("span", null, `${e.ms} ms`));
      li.title = e.detail || "";
      list.append(li);
    }
    if (!a.events.length) list.append(el("li", null, "No calls yet."));
  } catch { /* the next tick retries */ }
}

/* ------------------------------------------------------------------ start */
async function refreshHome() {
  const zip = $("#zip").value.trim();
  if (!/^\d{5}$/.test(zip)) return;
  // The server geocodes the ZIP with every basket; this keeps the globe in step before that.
  try {
    const r = await fetch(`https://api.zippopotam.us/us/${zip}`);
    if (!r.ok) return;
    const p = (await r.json()).places[0];
    state.home = { zip, lat: +p.latitude, lng: +p.longitude, label: `${p["place name"]}, ${p["state abbreviation"]}` };
    $("#place").textContent = state.home.label;
    drawGlobe();
  } catch { /* offline: the server falls back to a regional point */ }
}

async function start() {
  initGlobe();
  state.status = await api("/api/status");
  drawStatus();
  $("#list").value = state.status.sample_list.join("\n");

  const persona = await api("/api/persona");
  $("#pref-organic").checked = persona.organic_only;
  $("#pref-brands").checked = persona.store_brands_ok;
  drawHabits(persona);
  const savePrefs = () => api("/api/persona", { method: "PUT", headers: { "content-type": "application/json" },
    body: JSON.stringify({ organic_only: $("#pref-organic").checked, store_brands_ok: $("#pref-brands").checked }) });
  $("#pref-organic").addEventListener("change", savePrefs);
  $("#pref-brands").addEventListener("change", savePrefs);

  $("#go").addEventListener("click", findBasket);
  $("#scan").addEventListener("click", () => (state.status.ai !== "off" ? $("#photo").click() : readReceipt(null)));
  $("#photo").addEventListener("change", (e) => { if (e.target.files[0]) readReceipt(e.target.files[0]); e.target.value = ""; });
  $("#zip").addEventListener("change", refreshHome);
  $("#list").addEventListener("change", loadAtlas);

  $("#pulse").addEventListener("click", () => {
    const panel = $("#activity");
    panel.hidden = !panel.hidden;
    $("#pulse").setAttribute("aria-expanded", String(!panel.hidden));
    refreshActivity();
  });
  $("#close-activity").addEventListener("click", () => { $("#activity").hidden = true; $("#pulse").setAttribute("aria-expanded", "false"); });
  $("#chaos").addEventListener("click", async (e) => {
    e.target.disabled = true;
    const r = await post("/api/chaos", {}).catch((err) => ({ error: err.message }));
    e.target.disabled = false;
    refreshActivity.pinned = true;
    $("#activity-note").textContent = r.sentry_event
      ? `One step was broken on purpose. Sentry recorded it as event ${r.sentry_event}.`
      : `One step was broken on purpose: ${r.error}`;
    setTimeout(() => { refreshActivity.pinned = false; }, 15000);
    refreshActivity();
  });

  $("#say").addEventListener("submit", (e) => {
    e.preventDefault();
    const text = $("#say-text").value.trim();
    if (text.length > 1) say(text);
  });
  $("#q-shop").addEventListener("click", focusList);
  $("#q-news").addEventListener("click", showNews);
  $("#q-learn").addEventListener("click", showLearn);
  $("#q-season").addEventListener("click", showSeason);
  $("#home-btn").addEventListener("click", () => { showWelcome(); if (state.home) flyTo(state.home.lat, state.home.lng, 2.3); });

  drawLegend();
  showWelcome();
  loadAtlas();
  refreshHome();
  pollWorld(true);
  pollNews(true);
  setInterval(() => pollNews(false), 60000);
  setInterval(() => pollWorld(false), 300000);
  refreshActivity();
  setInterval(refreshActivity, 4000);
}

start().catch((err) => {
  document.body.append(el("p", "error", `Nibble could not start: ${err.message}`));
});
