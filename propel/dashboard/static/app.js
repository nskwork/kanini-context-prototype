/* Kanini Context dashboard. Every highlight, comet and particle follows an event from a real run or a
   number from the release or the latest comparison report; nothing is scripted on the page. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const fmt = (n) => (n == null ? "–" : Math.round(n).toLocaleString());
  const bare = (ref) => String(ref || "").split("@")[0];

  /* colours for the navy theme: bright enough to read on the dark cards */
  const KIND_COLOR = { Payer: "#E07AB4", Plan: "#0075FF", Program: "#0075FF", Jurisdiction: "#FFB547", Concept: "#01B574", Code: "#A0AEC0",
    ValueSet: "#A0AEC0", Rule: "#9F7AEA", Authority: "#B794F4", Source: "#CDB27A", Snapshot: "#BFA875", DataElement: "#4FD1C5", Endpoint: "#E07AB4" };
  const SHELL = { Concept: 0, Code: 70, Rule: 120, DataElement: 165, Authority: 200, Plan: 250, Program: 250, Payer: 250,
    Source: 285, Snapshot: 320, ValueSet: 320, Endpoint: 320, Jurisdiction: 390 };
  const ROOT_COLOR = { CoverageItem: "#0075FF", Authority: "#B794F4", Rule: "#9F7AEA", TerminologyItem: "#01B574", Endpoint: "#E07AB4",
    KnowledgeArtifact: "#4FD1C5", Source: "#CDB27A", Snapshot: "#BFA875", DataElement: "#4FD1C5", Thing: "#A0AEC0" };
  const OUTCOME = {
    no_pa: ["No PA", "#01B574"], exempt: ["Exempt", "#01B574"], pa_required: ["PA required", "#0075FF"],
    other_gate: ["Other gate", "#FFB547"], needs_facts: ["Not yet known", "#FFB547"], unknown: ["Not known", "#F04F4F"]
  };

  const words = (s) => String(s || "").replace(/_/g, " ").replace(/([a-z])([A-Z])/g, "$1 $2");
  const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1).toLowerCase();
  const TOOL_LABEL = {   // the agent's tool calls in plain words (the tool name shows on hover)
    journey_get: "patient record", context_applicable_rules: "find the rules", rules_evaluate: "check the rules",
    policy_search: "quote the source", journey_record: "log the decision"
  };

  const S = { seq: 0, orders: [], order: null, inst: null, onto: null, view: "inst", compare: {}, run: null, es: null,
    page: "ask", overlay: "graph", cEval: null, trace: null, kn: null, kTab: "rules", src: null,
    live: { mode: null, turns: 0, tin: 0, tout: 0, t0: 0, ms: null, eval: null, quote: null } };

  /* ---------------------------------------------------------------- the star field behind everything */
  (function stars() {
    const sky = $("sky");
    for (let i = 0; i < 70; i++) {
      const s = document.createElement("i");
      const z = 1 + (i % 3);
      s.style.cssText = `position:absolute;left:${(i * 137.5) % 100}%;top:${(i * 61.7) % 100}%;width:${z}px;height:${z}px;border-radius:50%;` +
        `background:#c4b5fd;opacity:.5;animation:tw ${3 + (i % 5)}s ease-in-out ${-(i % 7)}s infinite`;
      sky.appendChild(s);
    }
    const st = document.createElement("style");
    st.textContent = "@keyframes tw{0%,100%{opacity:.15}50%{opacity:.9}}";
    document.head.appendChild(st);
  })();

  /* ---------------------------------------------------------------- graph engine (d3 force, zoom, comets) */
  const svg = d3.select("#graph");
  const root = svg.append("g");
  const gEdges = root.append("g");
  const gNodes = root.append("g");
  const gFx = root.append("g");
  let sim = null, nodes = [], links = [], adj = new Map(), byKey = new Map(), zoomK = 1, t0 = performance.now();
  const comets = [];   // {chain:[node...], color, t, speed}

  const zoom = d3.zoom().scaleExtent([0.25, 6]).on("zoom", (e) => {
    root.attr("transform", e.transform);
    zoomK = e.transform.k;
    if (S.view === "onto") return;   // the schema diagram scales as one drawing
    gNodes.selectAll("text").attr("transform", `scale(${Math.min(1.6, Math.max(0.75, 1 / Math.sqrt(zoomK)))})`);
    labelPass();
  });
  svg.call(zoom).on("dblclick.zoom", null);

  function size() { const r = svg.node().getBoundingClientRect(); return [r.width, r.height]; }

  function setGraph(data, mode) {
    if (sim) sim.stop();
    gEdges.selectAll("*").remove(); gNodes.selectAll("*").remove(); gFx.selectAll("*").remove();
    comets.length = 0;
    nodes = data.nodes.map((n, i) => ({ ...n, i, phase: i * 0.7 }));
    const idx = new Map(nodes.map((n) => [n.gid, n]));
    links = data.edges.filter((e) => idx.has(e.s) && idx.has(e.t)).map((e) => ({ source: idx.get(e.s), target: idx.get(e.t), rel: e.rel }));
    adj = new Map(nodes.map((n) => [n, []]));
    links.forEach((l) => { adj.get(l.source).push([l.target, l]); adj.get(l.target).push([l.source, l]); });
    byKey = new Map(nodes.map((n) => [n.kind + "|" + n.id, n]));
    if (mode === "onto") ontoShells();

    const [w, h] = size();
    const shell = (n) => (mode === "onto" ? n.shell : SHELL[n.kind] ?? 300);
    sim = d3.forceSimulation(nodes)
      .force("link", d3.forceLink(links).distance(mode === "onto" ? 72 : 46).strength(0.12))
      .force("charge", d3.forceManyBody().strength(mode === "onto" ? -120 : -65))
      .force("collide", d3.forceCollide((n) => (mode === "onto" ? 36 : n.on ? 42 : n.prog || n.unscr ? 22 : 10)))
      .force("radial", d3.forceRadial(shell, 0, 0).strength(mode === "onto" ? 0.32 : 0.55))
      .force("drift", () => {   // slow breathing so the sky never freezes
        const t = (performance.now() - t0) / 1000;
        nodes.forEach((n) => { n.vx += Math.sin(t * 0.35 + n.phase) * 0.012; n.vy += Math.cos(t * 0.3 + n.phase) * 0.012; });
      })
      .alphaDecay(0.03).alphaTarget(0.012);

    const le = gEdges.selectAll("line").data(links).join("line").attr("class", "edge");
    const ng = gNodes.selectAll("g").data(nodes).join("g").attr("class", "node")
      .on("mouseenter", (e, n) => tip(e, n)).on("mousemove", (e, n) => tip(e, n)).on("mouseleave", () => $("tip").classList.add("hidden"))
      .on("click", (e, n) => detail(n))
      .call(d3.drag().on("start", (e, n) => { sim.alphaTarget(0.1).restart(); n.fx = n.x; n.fy = n.y; })
        .on("drag", (e, n) => { n.fx = e.x; n.fy = e.y; })
        .on("end", (e, n) => { sim.alphaTarget(0.012); n.fx = null; n.fy = null; }));
    ng.append("circle").attr("class", "halo").attr("r", 0);
    ng.append("circle").attr("class", "core");
    ng.append("text").attr("dy", 16).attr("text-anchor", "middle").text((n) => nodeLabel(n));
    paint();
    sim.on("tick", () => {
      le.attr("x1", (l) => l.source.x).attr("y1", (l) => l.source.y).attr("x2", (l) => l.target.x).attr("y2", (l) => l.target.y);
      ng.attr("transform", (n) => `translate(${n.x},${n.y})`);
    });
    legend(mode);
    setTimeout(() => fitTo(nodes, 0), 60);
    setTimeout(() => fitTo(nodes, 900), 1600);
  }

  function ontoShells() {   // the ontology: classes in shells by depth under their root class
    const parent = new Map();
    links.filter((l) => l.rel === "subClassOf").forEach((l) => parent.set(l.source, l.target));
    const seen = {};   // siblings under one root alternate between three rings, so their labels do not stack
    nodes.forEach((n) => {
      let d = 0, r = n;
      while (parent.has(r) && d < 6) { r = parent.get(r); d++; }
      const k = (seen[r.id] = (seen[r.id] || 0) + 1);
      n.root = r.id; n.depth = d; n.shell = d === 0 ? 100 : d === 1 ? 195 + (k % 3) * 38 : 340;
    });
  }

  function nodeColor(n) {
    if (S.view === "onto") return ROOT_COLOR[n.root] || "#9F7AEA";
    if (n.unscr) return "#F04F4F";
    if (n.prog) return "#F6AD55";
    return KIND_COLOR[n.kind] || "#A0AEC0";
  }
  function nodeLabel(n) {
    if (S.view === "onto") return words(n.id);
    const id = String(n.id ?? "");
    if (n.kind === "Rule") return cap(words(n.rule_kind || "rule"));   // the policy node beside it carries the citation
    if (n.kind === "Source") return "Source: " + (n.doc || id);
    if (n.kind === "Snapshot") return "Saved copy";
    if (n.kind === "Concept") return n.label || id;
    if (n.kind === "Authority" && n.authority_kind === "pa_program") return id.replace(/^CMS-/, "").replace(/-/g, " ");   // PA programs: short names
    if (n.kind === "DataElement" && n.name) return n.name.length > 24 ? n.name.slice(0, 22) + "…" : n.name;
    if (n.name) return n.name.length > 30 ? n.name.slice(0, 28) + "…" : n.name;
    if (n.kind === "Code" || n.kind === "DataElement") return id.split("|").slice(1).join(" ") || id;
    return id;
  }
  function paint() {
    const any = nodes.some((n) => n.on || n.cand);
    root.classed("dim", any);
    gNodes.selectAll("g").attr("class", (n) => "node" + (n.on ? " on" : "") + (n.prog ? " prog" : "") + (n.unscr ? " unscr" : "") + (n.cand ? " cand" : ""));
    gNodes.selectAll("text").text((n) => nodeLabel(n));
    gNodes.selectAll("circle.core").attr("r", (n) => (n.on ? 7 : n.prog || n.cand || n.unscr ? 5.5 : S.view === "onto" ? 5 : 3.6))
      .attr("fill", nodeColor).attr("stroke", "#12161c").attr("stroke-width", (n) => (n.on ? 2 : 1)).style("filter", null);
    gNodes.selectAll("circle.halo").attr("r", (n) => (n.on && n.primary ? 16 : 0)).attr("stroke", nodeColor);
    gEdges.selectAll("line").attr("class", (l) => "edge" + (l.source.on && l.target.on ? " on" : "")
      + ((l.source.prog || l.target.prog) && (l.source.prog || l.source.on) && (l.target.prog || l.target.on) ? " prog" : "")
      + ((l.source.unscr || l.target.unscr) && (l.source.unscr || l.source.on || l.source.prog) && (l.target.unscr || l.target.on || l.target.prog) ? " unscr" : "")
      + (l.source.cand && l.target.cand ? " cand" : ""));
    labelPass();
  }
  function labelPass() {
    const big = new Set(["Plan", "Payer", "Authority", "Concept", "Class"]);
    gNodes.selectAll("text").style("opacity", (n) =>
      ["Source", "Program", "ValueSet", "Snapshot"].includes(n.kind) && n.on && zoomK < 2.1 ? 0   // repeats a nearby name: on hover or deep zoom
      : n.on || n.cand || n.unscr ? 1 : n.prog ? (zoomK >= 1.8 && n.kind !== "Rule" ? 0.85 : 0) : zoomK >= 2.1 ? 0.85 : zoomK >= 1.25 && big.has(n.kind) ? 0.75 : S.view === "onto" && (n.depth <= 1 || zoomK >= 1.3) ? 0.75 : 0);
  }
  function legend(mode) {
    const counts = {};
    nodes.forEach((n) => { const k = mode === "onto" ? n.root : n.kind; counts[k] = (counts[k] || 0) + 1; });
    const pal = mode === "onto" ? ROOT_COLOR : KIND_COLOR;
    $("legend").innerHTML = (mode === "onto" ? "<span>classes by root ·</span>" : `<span>${nodes.length} nodes · ${links.length} edges ·</span>`) +
      Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([k, n]) => `<span><i style="background:${pal[k] || "#A0AEC0"}"></i>${esc(cap(words(k)))} ${n}</span>`).join("");
  }
  function tip(e, n) {
    const t = $("tip"), r = svg.node().getBoundingClientRect();
    t.innerHTML = `<b>${esc(nodeLabel(n))}</b><br>${esc(n.kind)}` + (n.citation ? `<br>${esc(n.citation)}${n.page ? " · page " + esc(n.page) : ""}` : "") +
      (n.valid_from ? `<br>from ${esc(n.valid_from)}` : "") + (n.basis ? ` · ${esc(n.basis)}` : "") + `<br>${adj.get(n)?.length || 0} links`;
    t.style.left = e.clientX - r.left + 14 + "px"; t.style.top = e.clientY - r.top + 10 + "px";
    t.classList.remove("hidden");
  }
  function fitTo(set, ms = 900) {
    const pts = set.filter((n) => Number.isFinite(n.x));
    if (!pts.length) return;
    const [w, h] = size();
    if (w < 10 || h < 10) return;   // the graph is on a page not in view
    const x0 = d3.min(pts, (n) => n.x), x1 = d3.max(pts, (n) => n.x), y0 = d3.min(pts, (n) => n.y), y1 = d3.max(pts, (n) => n.y);
    const k = Math.min(2.4, 0.7 / Math.max((x1 - x0 + 100) / w, (y1 - y0 + 100) / h));
    const tr = d3.zoomIdentity.translate(w / 2, h / 2).scale(k).translate(-(x0 + x1) / 2, -(y0 + y1) / 2);
    (ms ? svg.transition().duration(ms).ease(d3.easeCubicInOut) : svg).call(zoom.transform, tr);
  }
  /* zoom with the mouse wheel or a trackpad; the camera still follows each answer on its own */

  function bfs(a, b, allowed) {
    if (!a || !b) return null;
    const prev = new Map([[a, null]]), q = [a];
    while (q.length) {
      const x = q.shift();
      if (x === b) break;
      for (const [y] of adj.get(x) || []) if (!prev.has(y) && (!allowed || allowed.has(y) || y === b)) { prev.set(y, x); q.push(y); }
    }
    if (!prev.has(b)) return allowed ? bfs(a, b, null) : null;
    const out = []; for (let x = b; x; x = prev.get(x)) out.unshift(x);
    return out;
  }
  function addComets(chain, color, n = 3) {
    if (!chain || chain.length < 2) return;
    for (let i = 0; i < n; i++) comets.push({ chain, color, t: -i / n, speed: 0.22 + chain.length * -0.008 });
  }
  function fxLoop(now) {
    const dt = Math.min(0.05, (now - (fxLoop.last || now)) / 1000); fxLoop.last = now;
    const dots = [];
    comets.forEach((c) => {
      c.t += dt * Math.max(0.12, c.speed);
      if (c.t > 1.15) c.t -= 1.3;
      if (c.t < 0 || c.t > 1) return;
      const seg = c.chain.length - 1, f = c.t * seg, i = Math.min(seg - 1, Math.floor(f)), u = f - i;
      const a = c.chain[i], b = c.chain[i + 1];
      if (!Number.isFinite(a?.x) || !Number.isFinite(b?.x)) return;
      dots.push({ x: a.x + (b.x - a.x) * u, y: a.y + (b.y - a.y) * u, color: c.color });
    });
    gFx.selectAll("circle").data(dots).join("circle").attr("r", 3.4).attr("cx", (d) => d.x).attr("cy", (d) => d.y)
      .attr("fill", (d) => d.color).attr("stroke", "#12161c").attr("stroke-width", 1.5).style("filter", null);
    lanesTick(dt);
    requestAnimationFrame(fxLoop);
  }
  requestAnimationFrame(fxLoop);

  /* focus: involved nodes pulled to the middle, the rest drift outward, camera follows */
  function focus() {
    if (!sim) return;
    const on = (n) => n.on || n.prog || n.unscr;
    sim.force("fx", d3.forceX(0).strength((n) => (on(n) ? 0.07 : 0)))
      .force("fy", d3.forceY(0).strength((n) => (on(n) ? 0.07 : 0)))
      .force("radial", d3.forceRadial((n) => (on(n) ? (SHELL[n.kind] ?? 300) * 0.6 : (SHELL[n.kind] ?? 300) + 120), 0, 0).strength((n) => (on(n) ? 0.25 : 0.5)))
      .force("collide", d3.forceCollide((n) => (n.on ? 42 : n.prog || n.unscr ? 22 : 10)))
      .alpha(0.6).restart();
    setTimeout(() => fitTo(nodes.filter(on), 1300), 900);
  }
  function unfocus() {
    nodes.forEach((n) => { n.on = n.prog = n.cand = n.primary = n.unscr = false; });
    comets.length = 0;
    if (sim) sim.force("fx", null).force("fy", null).force("radial", d3.forceRadial((n) => SHELL[n.kind] ?? 300, 0, 0).strength(0.55)).alpha(0.4).restart();
    paint();
  }

  /* what an answer touches in the graph: worked out from the answer and the graph's own edges */
  const node = (kind, id) => byKey.get(kind + "|" + id);
  const out = (n, rel) => (adj.get(n) || []).filter(([m, l]) => l.rel === rel && l.source === n).map(([m]) => m);
  const inn = (n, rel) => (adj.get(n) || []).filter(([m, l]) => l.rel === rel && l.target === n).map(([m]) => m);

  function lightCandidates(p) {
    if (S.view !== "inst" || !p || !Array.isArray(p.rules)) return;
    p.rules.forEach((r) => { const rn = node("Rule", bare(r.rule_ref)); if (rn) { rn.cand = true; inn(rn, "STATES").forEach((a) => (a.cand = true)); } });
    paint();
  }
  function lightAnswer(p) {
    if (S.view !== "inst" || !p) return;
    // nothing reviewed decides it: keep the rules the pack considered visible, so it is clear what was looked at
    if (p.reason !== "no_reviewed_rule_in_scope") nodes.forEach((n) => { n.cand = false; });
    const cov = p.coverage || {};
    const set = new Set(), prog = new Set();
    const add = (n) => n && set.add(n);
    add(node("Plan", cov.plan)); add(node("Program", cov.program)); add(node("Payer", String(cov.payer || "").toLowerCase()));
    const st = node("Jurisdiction", cov.state); add(st);
    const code = node("Code", p.code); add(code);
    (code ? inn(code, "MAPS_TO") : []).forEach(add);
    const refs = new Set([...(p.rule_refs || []), ...((p.prior_authorization || {}).rule_refs || [])].map(bare));
    const prim = node("Rule", bare((p.primary_rule || {}).rule_ref));
    refs.forEach((k) => {
      const r = node("Rule", k); if (!r) return;
      const screen = r.basis === "stated_by_exhaustion";
      (screen ? prog : set).add(r);
      inn(r, "STATES").forEach((a) => (screen ? prog : set).add(a));
      if (!screen) { out(r, "SOURCED_FROM").forEach(add); out(r, "REQUIRES").forEach(add); out(r, "ABOUT").forEach(add); }
    });
    // a region that holds the state (a contractor jurisdiction) lights up only when a rule in the answer binds it
    (st ? out(st, "WITHIN") : []).filter((j) => (adj.get(j) || []).some(([m, l]) => l.rel === "BINDS" && (set.has(m) || prog.has(m)))).forEach(add);
    if (p.route) { const py = node("Payer", String(cov.payer || "").toLowerCase()); if (py) out(py, "EXPOSES").forEach(add); }
    // programs the answer names as not screened: "not screened against: A; B" lists their citations
    const unscr = new Set();
    ((p.prior_authorization || {}).notes || []).filter((t) => t.startsWith("not screened against:"))
      .forEach((t) => t.replace("not screened against:", "").split(";").map((x) => x.trim()).forEach((cit) => {
        nodes.filter((n) => n.kind === "Authority" && n.name === cit).forEach((n) => unscr.add(n));
      }));
    if (prog.size || unscr.size) { const us = node("Jurisdiction", "US"); if (us) prog.add(us); }
    nodes.forEach((n) => { n.on = set.has(n); n.prog = !n.on && prog.has(n); n.unscr = unscr.has(n); n.primary = n === prim; });
    paint();
    comets.length = 0;
    const allowed = new Set([...set, ...prog]);
    const primAuth = prim ? inn(prim, "STATES")[0] : null;
    const src = prim ? out(prim, "SOURCED_FROM")[0] : null;
    addComets(bfs(node("Plan", cov.plan), prim, allowed)?.concat(src ? [src] : []), "#01B574", 4);
    addComets(bfs(code, prim, allowed), "#0075FF", 3);
    addComets(bfs(st, primAuth, allowed), "#01B574", 3);
    [...prog, ...unscr].filter((n) => n.kind === "Authority").forEach((a) => {
      const via = (adj.get(a) || []).map(([m]) => m).find((m) => m === st || (m.kind === "Jurisdiction" && m.id === "US"));
      if (via) addComets([via, a], a.unscr ? "#F04F4F" : "#F6AD55", 1);
    });
    focus();
  }

  /* ---------------------------------------------------------------- detail panel (click a node) */
  async function detail(n) {
    const d = $("detail");
    d.classList.remove("hidden");
    let body = `<div class="panel-head"><span class="eyebrow">${esc(n.kind)}</span><button type="button" class="close" aria-label="Close">Close</button></div><div class="panel-body"><h4>${esc(n.name || nodeLabel(n))}</h4>`;
    const rel = (r) => r.toLowerCase().replace(/_/g, " ");
    const nb = (adj.get(n) || []).slice(0, 14).map(([m, l]) => `${esc(l.source === n ? rel(l.rel) + " →" : "← " + rel(l.rel))} ${esc(nodeLabel(m))}`);
    try {
      if (S.view === "inst" && n.kind === "Rule") {
        const r = await (await fetch("/api/rule?ref=" + encodeURIComponent(n.id))).json();
        body += `<div class="quote">${esc(r.quote)}<span class="ref">${esc(r.citation || "")} · page ${esc((r.locator || {}).page)}</span></div><div class="meta"><span class="code">${esc(r.rule_ref || "")}</span></div>`;
      } else if (S.view === "inst" && n.kind === "Source") {
        const s = await (await fetch("/api/source/" + encodeURIComponent(n.id))).json();
        const last = (s.checks || []).slice(-1)[0] || {};
        body += `<div>${esc(s.title)}</div><div class="meta">${esc(s.edition)}<br>Last checked ${esc((last.at || "").slice(0, 10))}: ${esc(last.note || "")}<br>SHA-256 <span class="code">${esc((s.sha256 || "not recorded").slice(0, 16))}…</span></div>`;
      }
    } catch (_) { /* the panel still shows the node's links */ }
    body += `<div class="links">${nb.join("<br>")}${(adj.get(n) || []).length > 14 ? "<br>…" : ""}</div>`;
    d.innerHTML = body + "</div>";
    d.querySelector(".close").onclick = () => d.classList.add("hidden");
  }

  /* ---------------------------------------------------------------- decision card */
  const WHY = {   // plain words for the answer's reason codes (the codes themselves are in the answer contract)
    pa_program_not_screened: "a prior authorization program here is not checked yet",
    no_reviewed_rule_in_scope: "no checked rule covers this case",
    coverage_criteria_not_met: "the coverage criteria are not met",
    needs_facts: "a test result or fact is missing",
    source_aging: "a source is due for a recheck",
    sync_aging: "the local copy is due for a sync",
    basis_interpreted: "the rule rests on an interpretation, so staff confirm"
  };
  const why = (code) => WHY[code] || String(code).replace(/_/g, " ");
  const CASE_NAME = { "maria-tn": "Tennessee", "maria-tn-egfr52": "Tennessee, eGFR 52", "maria-oh": "Ohio", "maria-uhc": "UHC",
    "maria-uhc-esa": "UHC, epoetin", "maria-nohgb": "No hemoglobin" };
  const caseName = (o) => CASE_NAME[o.patient] || o.state_name;
  const drugName = (o) => o.description.split(" (")[0].split(",")[0];
  const planShort = (o) => (o.plan === "original-medicare" ? "Medicare" : (o.payer || "") + " Advantage");
  function factRows(o) {
    const f = o.facts, fn = o.fact_names || {};
    const val = (k) => {
      const v = f[k], meta = fn[k] && fn[k].name !== k ? fn[k] : { name: k.charAt(0).toUpperCase() + k.slice(1).replace(/_/g, " "), unit: "" };
      return [meta.name.replace(/ \(date of most recent result\)/, " date").replace(/^Diagnosis: .*/, "Diagnoses").replace(/ \(CKD-EPI 2021\)/, ""),
        Array.isArray(v) ? v.join(", ") : `${v}${meta.unit && meta.unit !== "%" ? " " + meta.unit : meta.unit}`];
    };
    const rows = [["Plan", o.plan_name], ["State", o.state_name], ["Service date", o.date_of_service]]
      .concat(["diagnosis_codes", "hgb_g_dl", "hct_pct", "egfr", "dialysis_status"].filter((k) => f[k] != null).map(val));
    if (f.hgb_g_dl == null && f.hct_pct == null) rows.push(["Hemoglobin", "not on file"]);
    const exp = scenarioFor(o.patient);
    if (exp && exp.reference_outcome) rows.push(["Expected", OUTCOME[exp.reference_outcome]?.[0] || exp.reference_outcome]);
    return `<dl class="facts">${rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd${k === "Expected" ? ' class="exp"' : ""}>${esc(v)}</dd>`).join("")}</dl>`;
  }
  function card(p) {
    const c = $("card"), o = S.order;
    if (!o) { c.innerHTML = ""; return; }
    const head = `<div class="k">Case ${S.orders.indexOf(o) + 1} · ${esc(caseName(o))}</div>`;
    const q = `Does ${drugName(o).toLowerCase()} (${o.code}) for this ${o.state_name} ${planShort(o)} patient need prior authorization?`;
    const b = lanes.B && lanes.B.st;
    const approve = b && (b.awaiting || (b.active && !b.done))
      ? '<div class="approve"><span>The agent asks to log this decision</span><button type="button" class="yes" id="ok" disabled>Approve</button><button type="button" class="no" id="nok" disabled>Reject</button></div>' : "";
    if (!p) {
      c.style.removeProperty("--oc"); c.classList.remove("live");
      c.innerHTML = head + `<p class="q">${esc(q)}</p>` + factRows(o) + approve;
    } else {
      const [label, color] = OUTCOME[p.outcome] || ["Not known", "#F04F4F"];
      c.style.setProperty("--oc", color); c.classList.add("live");
      const prim = p.primary_rule || {};
      const src = (p.sources || []).find((x) => x.citation === prim.citation);
      const state = p.safe_to_act ? "Safe to act" : "Not safe to act" + ((p.not_safe_because || []).length ? ": " + why(p.not_safe_because[0]) : "");
      c.innerHTML = head + `<div class="big-ans">${esc(label)}</div><div class="state"><span class="dot"></span>${esc(state)}</div>`
        + (prim.citation ? `<div class="srcl">Source: ${esc(prim.citation)}${src && src.page ? ", page " + esc(src.page) : ""}</div>` : "")
        + approve + `<details class="pf"><summary>Patient facts</summary>${factRows(o)}</details>`;
    }
    if ($("ok")) {
      $("ok").onclick = () => confirm(true); $("nok").onclick = () => confirm(false);
      if (b.awaiting) { $("ok").disabled = false; $("nok").disabled = false; }
    }
    renderAnswers();
  }

  /* ---------------------------------------------------------------- the race: every lane fills only from a run made here */
  // A is the same agent without the context layer: it finds and reads the policy documents for every order. B is
  // the agent with Kanini Context (reduce: short calls, no document text); C is Kanini Context with no AI (eliminate).
  // Earlier baselines (an LLM application with search, or given every document) are in the Testing tab.
  const LANES = { C: ["workflow", "Rules only, no AI: Kanini Context answers", "#01B574", "The answer"],
    B: ["agent", "AI agent with Kanini Context", "#0075FF", "Matches C"],
    A: ["nocontext", "AI agent without Kanini Context", "#F04F4F", "Matches C"] };
  const MODE_LANE = { nocontext: "A", agent: "B", workflow: "C" };
  const A_STEP = { journey_get: "patient record", list_documents: "lists the documents", search_documents: "searches the documents",
    read_pages: "reads pages" };
  const lanes = {};
  const blank = () => ({ run: null, es: null, active: false, done: false, t0: 0, ms: null, tin: 0, tout: 0, turns: 0, prompt: 0,
    answer: null, ref: null, ok: null, callMs: null, note: "", eval: null, wrote: null });
  function scenarioFor(pid) {
    return Object.values(S.compare.scenarios || {}).find((e) => e.patient === pid) || null;
  }
  function buildLanes() {
    const box = $("lanes"); box.innerHTML = "";
    Object.entries(LANES).forEach(([L, [mode, title, color, third]]) => {
      const el = document.createElement("div");
      el.className = "lane"; el.style.setProperty("--lc", color);
      el.innerHTML = `<span class="L">${L}</span><div class="lt"><div class="t">${title}</div><div class="s" id="s${L}"></div></div>
        <div class="track"><canvas id="c${L}"></canvas><button type="button" class="lane-run" id="r${L}">Run ${L}</button></div>
        <div class="nums"><div><div class="h">Tokens sent to AI</div><div class="n" id="n${L}1">–</div></div>
        <div><div class="h">AI calls · time</div><div class="n" id="n${L}2">–</div></div><div><div class="h">${third}</div><div class="n" id="n${L}3">–</div></div></div>`;
      box.appendChild(el);
      lanes[L] = { color, canvas: el.querySelector("canvas"), parts: [], target: 0, flash: 0, st: blank() };
      el.querySelector(".lane-run").onclick = () => race([L]);
    });
    laneNumbers();
  }
  function renderAnswers() {   // C, B and A: each one's answer for this case, B and A marked against C
    const box = $("answers");
    if (!box) return;
    const row = (L, name) => {
      const st = lanes[L].st;
      let ans = null, ok = null;
      if (L === "A") { ans = st.answer; ok = st.ok; }
      else if (L === "B") { ans = st.eval ? st.eval.outcome : null; ok = st.done ? st.ok : null; }
      else if (st.eval) ans = st.eval.outcome;
      const label = st.skipped ? "not run" : st.active ? "running…" : ans ? (OUTCOME[ans]?.[0] || ans) : "–";
      const mark = ok == null ? "" : ok ? '<span class="ok">✓</span>' : '<span class="bad">✗</span>';
      const cost = st.done ? (L === "C" ? `0 tokens · ${st.callMs ?? "–"} ms` : `${S.scripted && !st.tin ? "scripted" : fmt(st.tin) + " tokens"} · ${(((st.ms ?? 0) / 1000)).toFixed(1)} s`) : "";
      const col = ans && !st.active ? (OUTCOME[ans]?.[1] || "#fff") : "var(--mute)";
      return `<div class="r"><span class="L" style="color:${LANES[L][2]}">${L}</span><span class="nm">${name}${cost ? `<small>${cost}</small>` : ""}</span><b style="color:${col}">${esc(label)}</b>${mark}</div>`;
    };
    box.innerHTML = `<div class="k">Same question, three ways</div>` + row("C", "Rules only") + row("B", "Agent + Kanini Context") + row("A", "Agent, no context");
  }
  function laneNumbers() {
    renderAnswers();
    const e = S.order && scenarioFor(S.order.patient);
    const secs = (st) => (((st.ms ?? (performance.now() - st.t0)) / 1000).toFixed(1));
    // one run on one order says little about accuracy: every mark comes with the lane's score over all six orders
    // in the latest comparison (five runs each)
    const overall = (m) => {
      const sc = Object.values(S.compare.scenarios || {}).filter((x) => x[m]);
      if (!sc.length) return "";
      const ok = sc.reduce((t, x) => t + x[m].matches_rules, 0), n = sc.reduce((t, x) => t + x[m].runs, 0);
      const what = m === "B" ? "gave the rules' answer" : "matched the rules";
      return `<small class="overall" title="In testing, the ${sc.length} demo orders were each run ${n / sc.length} times (${n} runs). This way ${what} ${ok} times out of ${n}. One run on one order can be right by chance; this is how often it is right.">`
        + `${m === "B" ? "" : "right "}${ok} of ${n} times in testing</small>`;
    };
    const tick = (b) => (b == null ? "–" : b ? '<span class="ok">✓</span>' : '<span class="bad">✗</span>');
    // A: the same agent without Kanini Context, counted from its own model calls
    const a = lanes.A.st, aScripted = S.scripted && (a.active || a.done) && !a.tin;
    const reads = () => [a.searches ? `${a.searches} search${a.searches > 1 ? "es" : ""}` : "", a.pages ? `${a.pages} page${a.pages > 1 ? "s" : ""} read` : ""].filter(Boolean).join(", ");
    $("sA").textContent = a.note || (a.done ? `AI: ${OUTCOME[a.answer]?.[0] || a.answer || "no card"} · rules: ${OUTCOME[a.ref]?.[0] || a.ref || "…"}`
        + (reads() ? ` · ${reads()}` : "") + (aScripted ? " · scripted stand-in" : "")
      : a.active ? (a.step || "Starting…") : "No Kanini Context: for every order it finds and reads the policy again");
    $("nA1").innerHTML = aScripted ? "–" : a.active || a.done ? fmt(a.tin) : "–";
    $("nA2").innerHTML = a.active || a.done ? `${a.turns} · ${secs(a)}<small> s</small>` : "–";
    $("nA3").innerHTML = a.done && a.answer ? tick(a.ok) + overall("A") : "–";
    lanes.A.target = a.active || a.done ? a.tin / 900 : 0;
    // B: the agent, counted from its own model calls
    const b = lanes.B.st, scripted = S.scripted && (b.active || b.done) && !b.tin;
    $("sB").textContent = scripted ? "Rehearsal: scripted stand-in, no AI and no tokens"
      : b.done && b.timing ? `This run: AI ${b.timing.ai_s.toFixed(1)} s · tools ${b.timing.tools_s.toFixed(1)} s · start-up ${b.timing.setup_s.toFixed(1)} s · between steps ${b.timing.between_s.toFixed(1)} s`
      : b.done ? "This run: short answers from checked rules" : b.active ? "Running now…" : "Short AI calls: the facts come from the context, not from re-reading documents";
    $("nB1").innerHTML = scripted ? "–" : b.active || b.done ? fmt(b.tin) : "–";
    $("nB2").innerHTML = b.active || b.done ? `${b.turns} · ${secs(b)}<small> s</small>` : "–";
    $("nB3").innerHTML = b.done && b.eval ? tick(b.ok) + overall("B") : b.done ? tick(false) : "–";   // marked against C when the agent has finished
    lanes.B.target = b.active || b.done ? b.tin / 900 : 0;
    // C: plain code, timed
    const c = lanes.C.st;
    $("sC").textContent = c.done ? "This run: no AI, no tokens · the approved rules, applied by code"
      : c.active ? "Looking up…" : "The information is already in the context: no AI call at all";
    $("nC1").innerHTML = c.done ? "0" : "–";
    $("nC2").innerHTML = c.done && c.callMs != null ? `0 · ${c.callMs}<small> ms</small>` : "–";
    $("nC3").innerHTML = c.done && c.eval ? `<b class="c-ans">${esc(OUTCOME[c.eval.outcome]?.[0] || c.eval.outcome)}</b><small class="overall">same answer every run</small>` : "–";
  }
  function lanesTick(dt) {
    Object.values(lanes).forEach((ln) => {
      const cv = ln.canvas, w = cv.clientWidth, h = cv.clientHeight;
      if (cv.width !== w * 2) { cv.width = w * 2; cv.height = h * 2; }
      const ctx = cv.getContext("2d");
      ctx.setTransform(2, 0, 0, 2, 0, 0); ctx.clearRect(0, 0, w, h);
      while (ln.parts.length < Math.min(60, Math.round(ln.target))) ln.parts.push({ x: -Math.random() * w, y: 5 + Math.random() * (h - 10), l: 5 + Math.random() * 9 });
      if (ln.parts.length > Math.round(ln.target)) ln.parts.length = Math.round(ln.target);
      ctx.shadowBlur = 0; ctx.fillStyle = ln.color;
      ln.parts.forEach((p) => { p.x += dt * w / 2.8; if (p.x > w) p.x -= w + 20; ctx.fillRect(p.x, p.y, p.l, 3); });
      if (ln.flash > 0) {
        ln.flash -= dt;
        const f = Math.max(0, ln.flash), grow = Math.min(1, (1.6 - f) * 6);
        ctx.globalAlpha = Math.min(1, f * 2); ctx.fillRect(0, h / 2 - 2, w * grow, 4); ctx.globalAlpha = 1;
      }
    });
    if (Object.values(lanes).some((l) => l.st.active)) laneNumbers();
  }

  /* ---------------------------------------------------------------- runs (each lane has its own) */
  function chip(text, cls, title) {
    const t = $("trace"), last = t.lastElementChild;
    if (last && last.dataset.t === text) return;   // a confirmed call is reported again when it resumes
    const s = document.createElement("li"); s.dataset.t = text; if (cls) s.className = cls; if (title) s.title = title;
    if (cls === "ai") s.textContent = "AI";   // the model's turns sit between the numbered steps
    else s.innerHTML = `<b>${t.querySelectorAll("li:not(.ai)").length + 1}</b> ${esc(text.charAt(0).toUpperCase() + text.slice(1))}`;
    t.appendChild(s);
    // keep only whole chips: the oldest go when the row is full, the latest stay in view
    let room = t.clientWidth - 24;
    const kids = [...t.children].reverse();
    kids.forEach((k) => { room -= k.offsetWidth + 6; k.style.display = room < 0 ? "none" : ""; });
  }
  function setBusy() {   // the button stays usable: a new run replaces a slow one (the server releases its approval wait)
    const busy = Object.values(lanes).some((l) => l.st.active);
    $("run-race").textContent = busy ? "Running… (press to restart)" : "Run C, B, A";
  }
  function closeAll() { Object.values(lanes).forEach((l) => { if (l.st.es) l.st.es.close(); }); }
  function clearAnswer() {
    unfocus(); card(null); $("trace").innerHTML = ""; $("detail").classList.add("hidden");
    S.eval = null; S.quote = null; S.lit = false; S.cEval = null; S.trace = null;
    renderWhy(); renderMatch();
  }
  async function race(which) {
    if (!S.order) return;
    if (S.view !== "inst") await setView("inst");
    which.forEach((L) => { const l = lanes[L]; if (l.st.es) l.st.es.close(); l.st = blank(); l.parts.length = 0; });
    if (which.includes("C") || (which.includes("B") && !S.cEval)) clearAnswer();
    else if (which.includes("B")) $("trace").innerHTML = "";
    const seq = ++S.seq;   // a newer run or order choice stops this sequence
    for (const L of which) {   // one lane at a time, so each gets the model to itself and its time is its own
      if (seq !== S.seq) return;
      const st = lanes[L].st; st.active = true; st.t0 = performance.now();
      const finished = new Promise((res) => { st.finish = res; });
      const r = await (await fetch("/api/run", { method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ patient: S.order.patient, mode: LANES[L][0] }) })).json();
      st.run = r.run;
      st.es = new EventSource(`/api/run/${r.run}/events`);
      st.es.onmessage = (m) => onEvent(L, JSON.parse(m.data));
      if (L === "B" && S.eval) card(S.eval);   // C's answer stays; the card gains B's approve buttons
      setBusy(); laneNumbers();
      await finished;
    }
  }
  /* the answer on screen is C's. B's own result stays on B's lane and is marked against C: the same outcome, the same
     safe-to-act flag and the same deciding rule (no deciding rule on both sides counts as the same). With no C answer
     yet, B's result is shown and marked against the expected answer from testing. */
  const usable = (p) => p && !["invalid_input", "unavailable", "error"].includes(p.status);
  const ruleOf = (p) => (p && p.primary_rule && p.primary_rule.rule_ref) || null;
  function sameAnswer(b, c) {
    return !!b && !!c && b.outcome === c.outcome && !!b.safe_to_act === !!c.safe_to_act && ruleOf(b) === ruleOf(c);
  }
  function gradeB() {
    const b = lanes.B.st;
    if (!b.eval) { b.ok = b.done ? false : null; return; }
    if (S.cEval) b.ok = sameAnswer(b.eval, S.cEval);
    else { const e = S.order && scenarioFor(S.order.patient); b.ok = e ? b.eval.outcome === e.reference_outcome : null; }
  }
  function showAnswer(p, L) {
    if (L === "C") S.cEval = p;
    if (L === "B" && S.cEval) { gradeB(); renderMatch(); return; }   // C's answer is already on screen
    const first = !S.eval || L === "C";
    S.eval = p; card(p);
    if (first) { unfocus(); lightAnswer(p); }
    gradeB(); renderMatch();
    if (L === "C" || !S.trace) loadWhy();
  }
  function onEvent(L, e) {
    const st = lanes[L].st;
    if (e.kind === "doc_start") {
      st.prompt = e.prompt_tokens; st.whole = e.whole_tokens; st.docs = e.documents; st.method = e.method;
    } else if (e.kind === "model") {
      // B's numbers stop at the decision card: the model's reply after staff approve is not part of answering
      if (!(L === "B" && st.confirmed)) { st.turns = e.turn; st.tin += e.tokens_in; st.tout += e.tokens_out; }
      if (L === "B") {
        chip(`AI step ${e.turn}`, "ai", e.tokens_in ? `AI step ${e.turn} · ${fmt(e.tokens_in)} tokens` : `AI step ${e.turn}`);
        if (e.text && e.text.includes("(Outcome line set from rules_evaluate.)")) st.cardFixed = true;
      }
    } else if (e.kind === "timing") {
      st.timing = e;
    } else if (e.kind === "answer") {
      st.answer = e.answer;
    } else if (e.kind === "reference") {
      st.ref = e.outcome; st.ok = e.matches;
    } else if (e.kind === "unavailable") {
      st.note = "Not run: " + e.message; st.skipped = true;
    } else if (e.kind === "tool_call" && L === "A") {
      const a = e.args || {};
      st.steps = (st.steps || 0) + 1;
      if (e.name === "read_pages") st.pages = (st.pages || 0) + ((a.pages || []).length || 1);
      if (e.name === "search_documents") st.searches = (st.searches || 0) + 1;
      st.step = `AI step ${st.turns || 1}: ${A_STEP[e.name] || e.name}` + (e.name === "read_pages" ? ` ${a.source_id || ""} p. ${(a.pages || []).join(", ")}`
        : e.name === "search_documents" ? ` "${String(a.query || "").slice(0, 60)}"` : "");
    } else if (e.kind === "tool_call") {
      const lbl = TOOL_LABEL[e.name] || e.name;
      if (L === "B" && e.name === "journey_record") st.wrote = e.args || {};   // what the model itself wrote, before the guardrail
      if (L === "B" && !(e.name === "journey_record" && [...$("trace").children].some((c) => c.dataset.t === lbl))) chip(lbl, "tool", e.name);
    } else if (e.kind === "tool_result") {
      if (e.name === "context_applicable_rules") lightCandidates(e.payload);
      if (e.name === "rules_evaluate" && usable(e.payload)) {   // a refused call is the model's to fix, not an answer
        st.eval = e.payload;
        if (L === "C") { st.callMs = e.call_ms; lanes.C.flash = 1.6; }
        showAnswer(e.payload, L);
      }
      if (e.name === "policy_search" && e.payload && e.payload.quote) {
        S.quote = { quote: e.payload.quote, citation: e.payload.citation, page: (e.payload.locator || {}).page };
        if (S.eval) card(S.eval);
        const src = nodes.find((n) => n.kind === "Source" && n.doc === e.payload.citation);
        if (src) { src.on = true; paint(); }
      }
    } else if (e.kind === "confirm_request") {
      if ($("ok")) { $("ok").disabled = false; $("nok").disabled = false; }
      st.confirmed = true; st.awaiting = true;
      if (L === "B") { st.done = true; gradeB(); renderMatch(); }
      if (st.finish) st.finish();
      st.ms = performance.now() - st.t0; st.done = true; st.active = false;
    } else if (e.kind === "confirm") {
      st.awaiting = false;
      chip(e.approved ? "staff approved" : "staff rejected");
      setTimeout(loadLog, 800);
    } else if (e.kind === "abandoned") {
      chip("not approved: a new run started");
    } else if (e.kind === "done" || e.kind === "error") {
      if (st.es) { st.es.close(); st.es = null; }
      if (st.ms == null) st.ms = performance.now() - st.t0;
      st.done = true; st.active = false;
      if (st.finish) st.finish();
      if (e.kind === "error") st.note = "Error: " + e.message;
      if (L === "B") {
        gradeB(); renderMatch();
        loadLog();
        const ok = $("ok");   // the agent finished without asking for approval (nothing to record): no buttons
        if (ok && !st.awaiting && !st.confirmed) ok.closest(".approve").remove();
      }
    }
    setBusy(); laneNumbers();
  }
  async function confirm(ok) {
    const run = lanes.B.st.run;
    if (!run) return;
    $("ok").disabled = true; $("nok").disabled = true;
    await fetch(`/api/run/${run}/confirm`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ approved: ok }) });
  }
  async function loadLog() {
    const rows = await (await fetch("/api/log")).json();
    $("log").innerHTML = '<span class="eyebrow">Decision log</span>  ' + (rows.slice(-4).reverse().map((r) =>
      r.decision === "rejected" ? `<i>Rejected</i> ${esc(r.order)} by ${esc(r.reviewer)}, no outcome logged`
        : `<b>Approved</b> ${esc(r.order)} · ${esc(OUTCOME[r.outcome]?.[0] || r.outcome)} · <span class="code">${esc(r.ruleRef || r.rule_ref)}</span> · ${esc(r.approved_by || r.reviewer || "")}`).join("  ·  ") || "empty");
  }

  /* ---------------------------------------------------------------- orders, views, start-up */
  function pick(o) {
    S.order = o;
    S.seq = (S.seq || 0) + 1;   // stop any sequence still running
    closeAll();
    document.querySelectorAll(".case").forEach((c) => c.setAttribute("aria-pressed", String(c.dataset.p === o.patient)));
    clearAnswer();
    Object.values(lanes).forEach((l) => { l.st = blank(); l.parts.length = 0; });
    card(null); setBusy(); laneNumbers();
  }
  /* ---------------------------------------------------------------- schema diagram (the Schema tab)
     The release's ontology drawn as a class diagram: one box per class with its fields and its kinds
     (subclasses), the two class groups that relations point at as frames, and every relation as a named arrow.
     After an answer, the classes it passed through glow. */
  // Two rows: what a rule is and where it comes from on top; who pays, below. Columns are fixed so the diagram
  // reads the same way every time.
  const SCHEMA_TOP = [
    { title: "Provenance", items: ["Source", "Snapshot"] },
    { title: "Authorities", items: ["Authority"] },
    { title: "Rules", items: ["Rule", "DataElement", "KnowledgeArtifact"] },
    { title: "Terminology", frame: "TerminologyItem", items: ["Concept", "Code", "ValueSet"] }
  ];
  const SCHEMA_BOTTOM = { title: "Coverage", frame: "CoverageItem", items: ["Payer", "Plan", "Program", "Jurisdiction"], before: ["Endpoint"] };
  const SCHEMA_COLOR = { Source: "#CDB27A", Snapshot: "#BFA875", Authority: "#B794F4", Rule: "#9F7AEA", DataElement: "#4FD1C5",
    KnowledgeArtifact: "#4FD1C5", Concept: "#01B574", Code: "#A0AEC0", ValueSet: "#A0AEC0", Payer: "#E07AB4", Plan: "#0075FF",
    Program: "#0075FF", Jurisdiction: "#FFB547", Endpoint: "#E07AB4", TerminologyItem: "#01B574", CoverageItem: "#0075FF" };
  const BOX_W = 224, COL_GAP = 120, ROW_GAP = 40, FIELD_H = 15, MAX_FIELDS = 9, BAND_GAP = 120;
  let schemaBox = null;

  function schemaLit(p) {   // the classes an answer passes through, from the answer itself
    const lit = new Set();
    if (!p) return lit;
    ["Plan", "Payer", "Program", "Jurisdiction", "Code", "Concept"].forEach((c) => lit.add(c));
    if ((p.rule_refs || []).length || p.primary_rule) ["Rule", "Authority", "Source", "Snapshot"].forEach((c) => lit.add(c));
    if ((p.missing_facts || []).length || ((p.coverage_criteria || {}).policies || []).length) lit.add("DataElement");   // facts the rules test
    if (p.route) lit.add("Endpoint");
    return lit;
  }

  function renderSchema(data) {
    if (sim) sim.stop();
    gEdges.selectAll("*").remove(); gNodes.selectAll("*").remove(); gFx.selectAll("*").remove();
    comets.length = 0; nodes = []; links = [];
    const sc = data.schema, ent = new Map(sc.entities.map((e) => [e.name, e]));
    const strip = (cls, f) => { const n = f.replace(new RegExp("^" + cls.charAt(0).toLowerCase() + cls.slice(1)), ""); return n.charAt(0).toLowerCase() + n.slice(1) || f; };
    const lit = schemaLit(S.eval);
    const selfRel = {};   // a class's relations to itself are listed inside its box
    sc.relations.filter((r) => r.s === r.t).forEach((r) => (selfRel[r.s] = (selfRel[r.s] || []).concat(words(r.rel).toLowerCase())));
    const kindLines = (e) => {
      const out = []; let line = "";
      e.kinds.map(words).forEach((k) => { if ((line + " · " + k).length > 33 && line) { out.push(line); line = k; } else line = line ? line + " · " + k : k; });
      if (line) out.push(line);
      return out;
    };
    const selfLines = (name) => {   // "↻ defers to · preempts · supersedes another authority", wrapped to the box
      const parts = ["↻", ...selfRel[name].map((r, i) => (i ? "· " : "") + r), `another ${words(name).toLowerCase()}`];
      const out = []; let line = "";
      parts.forEach((w) => { if ((line + " " + w).length > 32 && line) { out.push(line); line = "  " + w; } else line = line ? line + " " + w : w; });
      return out.concat(line);
    };
    const heightOf = (name) => {
      const e = ent.get(name) || { fields: [], kinds: [] };
      const nf = Math.min(e.fields.length, MAX_FIELDS) + (e.fields.length > MAX_FIELDS ? 1 : 0);
      const kl = kindLines(e).length;
      return 38 + nf * FIELD_H + (kl ? 16 + kl * 14 : 0) + (selfRel[name] ? 8 + selfLines(name).length * 14 : 0) + 8;
    };
    const box = new Map();
    // top row: columns, each centred on the tallest
    let topH = 0;
    SCHEMA_TOP.forEach((col, ci) => {
      let y = col.frame ? 40 : 0;
      col.items.forEach((name) => { const h = heightOf(name); box.set(name, { e: ent.get(name) || { name, fields: [], kinds: [] }, x: ci * (BOX_W + COL_GAP), y, w: BOX_W, h }); y += h + ROW_GAP; });
      col.h = y - ROW_GAP; topH = Math.max(topH, col.h);
    });
    SCHEMA_TOP.forEach((col) => { const dy = (topH - col.h) / 2; col.items.forEach((n) => (box.get(n).y += dy)); });
    // bottom row: the coverage group side by side, the routes before it
    const y2 = topH + BAND_GAP;
    const row2 = [...SCHEMA_BOTTOM.before, ...SCHEMA_BOTTOM.items];
    const rowH = d3.max(row2, heightOf);
    row2.forEach((name, i) => box.set(name, { e: ent.get(name) || { name, fields: [], kinds: [] }, x: i * (BOX_W + COL_GAP) - (BOX_W + COL_GAP) / 2,
      y: y2 + 40 + (rowH - heightOf(name)) / 2, w: BOX_W, h: heightOf(name) }));
    const frames = new Map();
    const frameOf = (key, title, items) => {
      const bs = items.map((n) => box.get(n));
      const x0 = d3.min(bs, (b) => b.x) - 16, x1 = d3.max(bs, (b) => b.x + b.w) + 16, y0 = d3.min(bs, (b) => b.y) - 38, y1 = d3.max(bs, (b) => b.y + b.h) + (key === "CoverageItem" ? 46 : 16);
      frames.set(key, { x: x0, y: y0, w: x1 - x0, h: y1 - y0, title });
    };
    frameOf("TerminologyItem", "Terminology", SCHEMA_TOP[3].items);
    frameOf("CoverageItem", "Coverage", SCHEMA_BOTTOM.items);
    const at = (name) => box.get(name) || frames.get(name);
    const all = [...box.values(), ...frames.values()];
    schemaBox = [d3.min(all, (b) => b.x) - 40, d3.min(all, (b) => b.y) - 50, d3.max(all, (b) => b.x + b.w) + 40, d3.max(all, (b) => b.y + b.h) + 40];

    let defs = svg.select("defs.schema");
    if (defs.empty()) {
      defs = svg.insert("defs", ":first-child").attr("class", "schema");
      [["arrow", "#5A6775"], ["arrow-on", "#01B574"]].forEach(([id, c]) => defs.append("marker").attr("id", id).attr("viewBox", "0 0 10 10")
        .attr("refX", 9).attr("refY", 5).attr("markerWidth", 7).attr("markerHeight", 7).attr("orient", "auto-start-reverse")
        .append("path").attr("d", "M0,0 L10,5 L0,10 z").attr("fill", c));
    }
    SCHEMA_TOP.forEach((col, ci) => {
      if (col.frame) return;
      gNodes.append("text").attr("class", "sc-col").attr("x", ci * (BOX_W + COL_GAP) + BOX_W / 2).attr("y", d3.min(col.items, (n) => box.get(n).y) - 14)
        .attr("text-anchor", "middle").text(col.title);
    });
    { const b = box.get("Endpoint"); gNodes.append("text").attr("class", "sc-col").attr("x", b.x + b.w / 2).attr("y", b.y - 14).attr("text-anchor", "middle").text("Routes"); }
    frames.forEach((f, name) => {
      const g = gNodes.append("g").attr("class", "sc-frame" + (lit.has(name === "CoverageItem" ? "Plan" : "Code") ? " on" : "")).attr("data-cls", name).style("--c", SCHEMA_COLOR[name]);
      g.append("rect").attr("x", f.x).attr("y", f.y).attr("width", f.w).attr("height", f.h).attr("rx", 16);
      g.append("text").attr("class", "sc-col").attr("x", f.x + f.w / 2).attr("y", f.y + 22).attr("text-anchor", "middle").text(`${f.title} · ${words(name)}`);
      g.on("click", (ev) => { ev.stopPropagation(); schemaSelect(name); });
    });

    // relations: straight between neighbours, around the side when a box is in the way
    const rels = [];
    const groups = new Map();
    sc.relations.filter((r) => r.s !== r.t).forEach((r) => { const k = r.s + ">" + r.t; (groups.get(k) || groups.set(k, []).get(k)).push(r); });
    groups.forEach((list, k) => {
      const [s, t] = k.split(">"), A = at(s), B = at(t);
      if (!A || !B) return;
      list.forEach((r, i) => {
        const off = (i - (list.length - 1) / 2) * 18;
        const ov = (a0, a1, b0, b1) => Math.min(a1, b1) - Math.max(a0, b0);
        let d, lx, ly, anchor = "middle", rot = false;
        const hov = ov(A.y, A.y + A.h, B.y, B.y + B.h), vov = ov(A.x, A.x + A.w, B.x, B.x + B.w);
        if (vov > 40) {   // one above the other
          const down = B.y > A.y, x = Math.max(A.x, B.x) + Math.min(vov, A.w, B.w) / 2 + off;
          const y1 = down ? A.y + A.h : A.y, y2 = down ? B.y : B.y + B.h;
          const between = [...box.values()].some((o) => o !== A && o !== B && o.x < x && x < o.x + o.w && Math.min(y1, y2) < o.y && o.y + o.h < Math.max(y1, y2));
          if (between) {   // skip over the box between them on the left
            const lxx = Math.min(A.x, B.x) - 46, ya = A.y + 26, yb = B.y + 26;
            d = `M${A.x},${ya} C${lxx},${ya} ${lxx},${yb} ${B.x},${yb}`; lx = lxx + 6; ly = (ya + yb) / 2 + 30; anchor = "middle"; rot = true;
          } else { d = `M${x},${y1} L${x},${y2}`; lx = x + 8; ly = (y1 + y2) / 2 + 4; anchor = "start"; }
        } else if (hov > 30) {   // side by side
          const right = B.x > A.x, y = Math.max(A.y, B.y) + Math.min(hov, 80) / 2 + off;
          const x1 = right ? A.x + A.w : A.x, x2 = right ? B.x : B.x + B.w;
          const between = [...box.values()].some((o) => o !== A && o !== B && Math.min(x1, x2) < o.x && o.x + o.w < Math.max(x1, x2) && o.y < y && y < o.y + o.h);
          if (between) {   // go under the boxes between them
            const under = [...box.values()].filter((o) => o.x + o.w > Math.min(A.x, B.x) && o.x < Math.max(A.x + A.w, B.x + B.w));
            const yy = d3.max(under, (o) => o.y + o.h) + 26, xa = A.x + A.w / 2, xb = B.x + B.w / 2;
            d = `M${xa},${A.y + A.h} L${xa},${yy - 10} Q${xa},${yy} ${xa + 10},${yy} L${xb - 10},${yy} Q${xb},${yy} ${xb},${yy - 10} L${xb},${B.y + B.h}`;
            lx = (xa + xb) / 2; ly = yy - 5;
          } else { d = `M${x1},${y} L${x2},${y}`; lx = (x1 + x2) / 2; ly = y - 6; }
        } else {   // diagonal: leave from the nearer side, arrive from above or below
          const right = B.x > A.x, down = B.y > A.y;
          const x1 = right ? A.x + A.w : A.x, y1 = A.y + Math.min(A.h / 2, 40) + off;
          const x2 = B.x + B.w / 2 + off, y2 = down ? B.y : B.y + B.h;
          d = `M${x1},${y1} C${x2},${y1} ${x2},${y1} ${x2},${y2}`; lx = (x1 + x2) / 2; ly = y1 - 6;
        }
        rels.push({ s, t, label: words(r.rel).toLowerCase(), d, lx, ly, anchor, rot });
      });
    });
    const relOn = (r) => lit.size && (lit.has(r.s) || frames.has(r.s)) && (lit.has(r.t) || frames.has(r.t));
    const eg = gEdges.selectAll("g").data(rels).join("g").attr("class", (r) => "sc-rel" + (relOn(r) ? " on" : ""));
    eg.append("path").attr("d", (r) => r.d).attr("marker-end", (r) => (relOn(r) ? "url(#arrow-on)" : "url(#arrow)"));
    eg.append("text").attr("x", (r) => r.lx).attr("y", (r) => r.ly).attr("text-anchor", (r) => r.anchor)
      .attr("transform", (r) => (r.rot ? `rotate(-90 ${r.lx} ${r.ly})` : null)).text((r) => r.label);

    box.forEach((b, name) => {
      const e = b.e, c = SCHEMA_COLOR[name] || "#9F7AEA";
      const g = gNodes.append("g").attr("class", "sc-box" + (lit.has(name) ? " on" : "")).attr("data-cls", name)
        .attr("transform", `translate(${b.x},${b.y})`).style("--c", c);
      g.append("rect").attr("class", "body").attr("width", b.w).attr("height", b.h).attr("rx", 10);
      g.append("path").attr("class", "band").attr("d", `M0,10 a10,10 0 0 1 10,-10 h${b.w - 20} a10,10 0 0 1 10,10 v20 h${-b.w} z`);
      g.append("text").attr("class", "name").attr("x", 12).attr("y", 20).text(words(name));
      g.append("text").attr("class", "count").attr("x", b.w - 12).attr("y", 20).attr("text-anchor", "end")
        .text(`${e.fields.length} field${e.fields.length === 1 ? "" : "s"}`);
      let y = 48;
      e.fields.slice(0, MAX_FIELDS).forEach((f) => {
        const t = g.append("text").attr("class", "field").attr("x", 12).attr("y", y);
        t.append("tspan").text(strip(name, f.name));
        t.append("tspan").attr("class", "type").attr("x", b.w - 12).attr("text-anchor", "end").text(f.type);
        y += FIELD_H;
      });
      if (e.fields.length > MAX_FIELDS) { g.append("text").attr("class", "more").attr("x", 12).attr("y", y).text(`+ ${e.fields.length - MAX_FIELDS} more · click to see all`); y += FIELD_H; }
      if (selfRel[name]) { selfLines(name).forEach((l, i) => g.append("text").attr("class", "self").attr("x", 12).attr("y", y + 4 + i * 14).text(l)); y += 8 + selfLines(name).length * 14; }
      const kl = kindLines(e);
      if (kl.length) {
        g.append("line").attr("x1", 10).attr("x2", b.w - 10).attr("y1", y - 6).attr("y2", y - 6).attr("class", "sep");
        g.append("text").attr("class", "kinds-h").attr("x", 12).attr("y", y + 8).text("KINDS");
        kl.forEach((l, i) => g.append("text").attr("class", "kinds").attr("x", 12).attr("y", y + 22 + i * 14).text(l));
      }
      g.on("click", (ev) => { ev.stopPropagation(); schemaSelect(name); });
    });
    svg.on("click.schema", () => schemaSelect(null));

    const kindsN = sc.entities.reduce((a, e) => a + e.kinds.length, 0);
    $("legend").innerHTML = `<span>${data.counts.classes} classes (${box.size} boxes, 2 groups, ${kindsN} listed as kinds) · ${data.counts.relations} relations · ${data.counts.attributes} fields ·</span>`
      + (lit.size ? `<span><i style="background:var(--lime)"></i>classes the last answer passed through</span>` : `<span>answer an order to light up the classes it passes through</span>`);
    fitSchema(0);
  }

  function schemaSelect(name) {
    const boxes = gNodes.selectAll(".sc-box,.sc-frame"), rels = gEdges.selectAll(".sc-rel");
    if (!name) { boxes.classed("dim", false).classed("sel", false); rels.classed("dim", false).classed("sel", false); $("detail").classList.add("hidden"); return; }
    const near = new Set([name]);
    rels.each((r) => { if (r.s === name || r.t === name) { near.add(r.s); near.add(r.t); } });
    boxes.classed("dim", function () { return !near.has(this.dataset.cls); }).classed("sel", function () { return this.dataset.cls === name; });
    rels.classed("dim", (r) => r.s !== name && r.t !== name).classed("sel", (r) => r.s === name || r.t === name);
    const bb = []; gNodes.selectAll(".sc-frame").each(function () { if (near.has(this.dataset.cls)) bb.push(this.getBBox()); });
    gNodes.selectAll(".sc-box").each(function () {   // getBBox ignores the group's translate: add it back
      if (!near.has(this.dataset.cls)) return;
      const m = /translate\(([-\d.]+),([-\d.]+)\)/.exec(this.getAttribute("transform")), b = this.getBBox();
      bb.push({ x: b.x + +m[1], y: b.y + +m[2], width: b.width, height: b.height });
    });
    const pts = bb.filter((b) => b && b.width && !Number.isNaN(b.x));
    if (pts.length) fitRect(d3.min(pts, (b) => b.x), d3.min(pts, (b) => b.y), d3.max(pts, (b) => b.x + b.width), d3.max(pts, (b) => b.y + b.height));
    const e = (S.onto.schema.entities.find((x) => x.name === name)) || { name, fields: [], kinds: [], about: "" };
    const strip = (f) => { const n = f.replace(new RegExp("^" + name.charAt(0).toLowerCase() + name.slice(1)), ""); return n.charAt(0).toLowerCase() + n.slice(1) || f; };
    const relLines = S.onto.schema.relations.filter((r) => r.s === name || r.t === name)
      .map((r) => (r.s === name ? `${words(r.rel).toLowerCase()} → ${words(r.t)}` : `← ${words(r.rel).toLowerCase()} · ${words(r.s)}`));
    const d = $("detail");
    d.classList.remove("hidden");
    d.innerHTML = `<div class="panel-head"><span class="eyebrow">Class${e.parent ? " · kind of " + esc(words(e.parent)) : ""}</span><button type="button" class="close" aria-label="Close">Close</button></div>`
      + `<div class="panel-body"><h4>${esc(words(name))}</h4><div>${esc(e.about)}</div>`
      + (e.fields.length ? `<div class="meta">${e.fields.map((f) => `<span class="code">${esc(strip(f.name))}</span> ${esc(f.type)}`).join("<br>")}</div>` : "")
      + (e.kinds.length ? `<div class="meta"><b>Kinds:</b> ${e.kinds.map((k) => esc(words(k))).join(", ")}</div>` : "")
      + (relLines.length ? `<div class="links">${relLines.map(esc).join("<br>")}</div>` : "") + "</div>";
    d.querySelector(".close").onclick = () => schemaSelect(null);
  }

  function fitSchema(ms = 700) {
    if (schemaBox) fitRect(...schemaBox, ms);
  }
  function fitRect(x0, y0, x1, y1, ms = 700) {
    const [w, h] = size();
    if (w < 10 || h < 10) return;
    x0 -= 30; y0 -= 30; x1 += 30; y1 += 30;
    const k = Math.min(1.6, 0.96 / Math.max((x1 - x0) / w, (y1 - y0) / h));
    const tr = d3.zoomIdentity.translate(w / 2, h / 2).scale(k).translate(-(x0 + x1) / 2, -(y0 + y1) / 2);
    (ms ? svg.transition().duration(ms).ease(d3.easeCubicInOut) : svg).call(zoom.transform, tr);
  }

  /* ---------------------------------------------------------------- the Testing tab
     Results from repeated testing (propel/baseline/compare.py), never from a live run: an LLM application without
     Kanini Context answering the same six orders five times each, beside Kanini Context's two ways. Every number is
     read from the latest comparison report. */
  const PLAIN = { no_pa: "No PA", pa_required: "PA required", exempt: "Exempt", other_gate: "Other gate", needs_facts: "Not yet known",
    unknown: "Not known", unparseable: "no readable answer", "no card": "no decision card" };
  function whatBroke(x) {   // order by order: the rules' answer, each baseline's wrong answers and why (propel/baseline/failures.py)
    if (!x || !x.orders) return "";
    const part = (it, name, r, key) => {
      if (!it) return "";
      const broke = it.broke.map((b) => `${b.runs} run${b.runs > 1 ? "s" : ""} answered <b>${esc(PLAIN[b.answer] || b.answer)}</b>: ${esc(b.why)}.`
        + (b.cited_other_region ? ` ${b.cited_other_region} of them cited ${esc(b.other_region_name)}'s policy.` : "")
        + (b.quote && b.quote.why ? `<span class="t-q">In its own words: “${esc(b.quote.why)}”${b.quote.source ? ` <i>Source it cited: ${esc(b.quote.source)}</i>` : ""}</span>` : "")).join(" ");
      const ret = key === "search" && "deciding_page_found" in it && r.deciding
        ? ` <span class="t-ret">The deciding page was ${it.deciding_page_found ? "among" : "<b>not</b> among"} the ${it.passages} passages retrieved`
          + ("other_region" in it ? `; ${it.other_region} came from ${esc(it.other_region_name)}'s policy, ${it.own_region} from ${esc(r.state)}'s` : "") + ".</span>" : "";
      return `<div class="t-line"><span class="t-who">${name}</span> right ${it.right} of ${it.runs}.${broke ? " " + broke : ""}${it.broke.length ? ret : ""}</div>`;
    };
    return `<h4 class="t-h4">What broke, order by order</h4><p class="t-when">Tests started: ${esc(x.when || "")}. Each order answered 5 times by each way.</p>`
      + x.orders.map((r) => `<div class="t-order"><div class="t-o"><code>${esc(r.order)}</code> the rules say <b>${esc(PLAIN[r.rules] || r.rules)}</b>`
        + (r.deciding ? `, resting on ${esc(r.deciding.citation)}, page ${esc(r.deciding.page)}` : r.rules === "unknown" ? ": the documents do not settle it, so it goes to staff" : "") + `</div>`
        + part(r.agent, "A · agent without Kanini Context", r, "agent") + part(r.search, "Single LLM call with search (RAG)", r, "search") + part(r.whole, "Single LLM call with every document", r, "whole") + `</div>`).join("");
  }
  function renderTesting() {
    const box = $("testing"), cmp = S.compare || {}, scs = Object.values(cmp.scenarios || {});
    if (!scs.length) { box.innerHTML = '<p class="t-empty">No comparison report yet. Run <code>python -m propel.baseline.compare --runs 5</code>.</p>'; return; }
    const sum = (k, f) => scs.filter((e) => e[k]).reduce((t, e) => t + f(e[k]), 0);
    const cnt = (k) => scs.filter((e) => e[k]).length;
    const avg = (k, f) => (cnt(k) ? sum(k, f) / cnt(k) : 0);
    const rng = (k, f, d = 1) => { const v = scs.filter((e) => e[k]).map((e) => f(e[k])); if (!v.length) return "–"; const lo = Math.min(...v).toFixed(d), hi = Math.max(...v).toFixed(d); return lo === hi ? lo : `${lo} to ${hi}`; };
    const tile = (k, name, sub, cls) => {
      if (!cnt(k)) return "";
      const ok = sum(k, (s) => s.matches_rules), n = sum(k, (s) => s.runs);
      const head = k === "C" ? "same answer every run" : k === "B" ? `${ok} of ${n} from the rules` : `${ok} of ${n} right`;
      const vary = k.startsWith("A") ? `<div class="t-vary">answer changed between runs on ${varied(k)} of ${cnt(k)} orders</div>` : "";
      const older = ((cmp.history || {})[k] || []).slice(1);   // earlier tests of the same way, newest first
      const hist = older.length ? `<div class="t-hist">earlier test${older.length > 1 ? "s" : ""}: ${older.map((h) => `${h.right} of ${h.runs}`).join(", ")}; all: ${[...older, (cmp.history[k] || [])[0]].reduce((t, h) => t + h.right, 0)} of ${[...older, (cmp.history[k] || [])[0]].reduce((t, h) => t + h.runs, 0)}</div>` : "";
      return `<div class="t-tile ${cls}"><div class="t-name">${name}</div><div class="t-sub">${sub}</div><div class="t-big">${head}</div>`
        + vary + hist + `<div class="t-meta">${fmt(avg(k, (s) => s.tokens_in))} tokens · ${k === "C" ? "0 AI calls" : avg(k, (s) => s.llm_calls).toFixed(1) + " AI calls"} · ${rng(k, (s) => s.seconds, k === "C" ? 2 : 1)} s</div></div>`;
    };
    const hasA = cnt("A") > 0;
    const ord = (pid) => S.orders.find((o) => o.patient === pid) || {};
    const cell = (s) => (s ? `<span class="t-c ${s.matches_rules === s.runs ? "good" : s.matches_rules === 0 ? "bad" : "mid"}">${s.matches_rules}/${s.runs}</span>` : "–");
    const rows = scs.map((e) => { const o = ord(e.patient);
      return `<tr><td><b>${esc(e.patient)}</b><span>${esc([o.code, o.plan_name, o.state_name].filter(Boolean).join(" · "))}</span></td>`
        + `<td>${esc(OUTCOME[e.reference_outcome]?.[0] || e.reference_outcome)}</td>${hasA ? `<td>${cell(e.A)}</td>` : ""}<td>${cell(e.A_rag)}</td><td>${cell(e.A_whole)}</td><td>${cell(e.B)}</td><td>${e.C ? `<span class="t-c good" title="The same answer on every run">${Math.max(...Object.values(e.C.outcomes))}/${e.C.runs} same</span>` : "–"}</td></tr>`; }).join("");
    // findings, worked out from the report
    const oh = scs.find((e) => e.patient === "maria-oh"), esa = scs.find((e) => e.patient === "maria-uhc-esa");
    const tn = (oh && oh.A_sent ? oh.A_sent.passages : []).filter((p) => /A58982|L39237/.test(p.title)).length;
    const ohio = (oh && oh.A_sent ? oh.A_sent.passages : []).filter((p) => /A56462|L34356/.test(p.title)).length;
    const lucky = scs.filter((e) => e.A_rag && e.A_rag.matches_rules === e.A_rag.runs).map((e) => e.patient);
    const varied = (k) => scs.filter((e) => e[k] && Object.keys(e[k].outcomes || {}).length > 1).length;
    const find = [];
    if (hasA) find.push(`<b>The same agent without the context layer.</b> For every order it searched and read the policy documents again: ${avg("A", (s) => s.llm_calls).toFixed(1)} AI calls and about ${fmt(avg("A", (s) => s.tokens_in))} tokens an order, against ${avg("B", (s) => s.llm_calls).toFixed(1)} calls and ${fmt(avg("B", (s) => s.tokens_in))} tokens with Kanini Context, and none without AI. It matched the rules ${sum("A", (s) => s.matches_rules)} of ${sum("A", (s) => s.runs)} times, and gave more than one answer to the same question on ${varied("A")} of ${cnt("A")} orders.`);
    if (cnt("A_rag")) find.push(`<b>Repeated calls, different answers.</b> Without the context layer, every order sends the AI back to the documents to work out the same information again: about ${fmt(avg("A_rag", (s) => s.tokens_in))} tokens a question with search, ${fmt(avg("A_whole", (s) => s.tokens_in))} reading everything. Asked the same question five times, it gave more than one answer on ${varied("A_rag")} of ${cnt("A_rag")} orders with search${cnt("A_whole") ? ` and ${varied("A_whole")} of ${cnt("A_whole")} reading everything` : ""}. Kanini Context gave one answer per order, every time.`);
    const said = (st) => Object.keys((st && st.outcomes) || {}).filter((o) => o !== "unknown").map((o) => `"${PLAIN[o] || o}"`);
    const per = (e, parts) => parts.filter(([k]) => e[k]).map(([k, w]) => `${e[k].matches_rules} of ${e[k].runs} ${w}`).join(", ");
    const WAYS = [["A", "as the agent with no context"], ["A_rag", "with search"], ["A_whole", "reading everything"]];
    if (oh && (oh.A || oh.A_rag)) find.push(`<b>The wrong region.</b> On the Ohio order the AI applied Tennessee's policy to an Ohio patient (its eGFR limit of 45, or its CKD code list without N18.31)`
      + (oh.A_rag && oh.A_sent ? `; retrieval sent ${tn} passages from Tennessee's policy and ${ohio} from Ohio's` : "")
      + `. Right ${per(oh, WAYS)}. Kanini Context binds each rule to its contractor region, so the question never reaches the wrong policy.`);
    if (esa && (esa.A || esa.A_rag)) find.push(`<b>Silence read as a rule.</b> The UnitedHealthcare list names the anemia drug only with a cancer diagnosis. Without Kanini Context the AI answered `
      + `${[...new Set(WAYS.flatMap(([k]) => said(esa[k])))].join(" or ")}; right ${per(esa, WAYS)}. Kanini Context says what the documents support: not known, route to staff.`);
    if (lucky.length) find.push(`<b>Right only sometimes.</b> On ${lucky.map(esc).join(", ")} the AI with search was right every run, with fewer tokens than the agent. Nothing tells which orders those are, so every answer it gives has to be checked by a person.`);
    find.push(`<b>Reduce or eliminate.</b> The documents are read once, when the rules are drafted and approved. After that, the agent needs only short calls (B, about ${fmt(avg("B", (s) => s.tokens_in))} tokens, no document text), and a workflow needs none (C: no tokens, the same answer every time), each answer with its source, rule edition and safe-to-act flag.`);
    const when = (cmp.at && (cmp.at.A || cmp.at.A_rag)) ? new Date(cmp.at.A || cmp.at.A_rag).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "";
    const whole = scs.find((e) => e.A_whole_tokens)?.A_whole_tokens;
    box.innerHTML = `<div class="t-head"><h3>Testing: the same orders with and without the context layer</h3>`
      + `<p>Each of the ${scs.length} demo orders answered ${scs[0].C ? scs[0].C.runs : 5} times by each method${when ? ", " + esc(when) : ""}, GPT-5 mini. Without Kanini Context, the AI answers from the same ${whole ? fmt(whole) + "-token" : ""} policy library: as the same agent reading it itself (lane A)${hasA ? "" : ", not yet tested"}, by search (RAG), or given every document. These are repeated runs, not a live demo: one answer can land either way by chance.</p></div>`
      + `<div class="t-tiles">${tile("A", "A · same agent, no Kanini Context", "finds and reads the documents itself", "a")}`
      + `${tile("B", "B · agent with Kanini Context", "the checked rules, through tools", "b")}${tile("C", "C · Kanini Context, no AI", "the approved rules, applied by code", "c")}`
      + `${tile("A_rag", "Single LLM call with search (RAG)", "no Kanini Context, earlier test", "a old")}${tile("A_whole", "Single LLM call with every document", "no Kanini Context, earlier test", "a old")}</div>`
      + `<table class="t-table"><thead><tr><th>Order</th><th>The rules say</th>${hasA ? "<th>A · same agent, no context</th>" : ""}<th>Search (RAG)</th><th>Read all</th><th>B · agent</th><th>C · no AI</th></tr></thead><tbody>${rows}</tbody></table>`
      + `<ul class="t-find">${find.map((f) => `<li>${f}</li>`).join("")}</ul>`
      + whatBroke(cmp.explain)
      + `<p class="t-foot">From <code>propel/baseline/results/</code>: ${[...new Set(Object.values(cmp.files || {}))].map((f) => `<code>${esc(f)}</code>`).join(", ")}. Right means the same answer as the approved rules for that order.</p>`;
  }


  /* ---------------------------------------------------------------- pages
     Four sections of this one page, switched without a reload, so a run in progress, the chosen case and the trace
     survive a look at another page. The graph is one drawing: it sits on the Ask page and moves to the Knowledge page
     while that page shows it. */
  const PAGES = ["sources", "knowledge", "ask", "testing"];
  function placeGraph(where) {
    const box = $("graphbox"), host = where === "knowledge" ? $("kn-graph") : $("ask-graph");
    if (box.parentElement !== host) host.insertBefore(box, host.firstChild);
  }
  async function showPage(name) {
    if (!PAGES.includes(name)) return;
    S.page = name;
    PAGES.forEach((n) => $("page-" + n).classList.toggle("hidden", n !== name));
    document.querySelectorAll("#pages button").forEach((b) => b.setAttribute("aria-current", b.dataset.page === name ? "page" : "false"));
    if (location.hash.slice(1) !== name) history.replaceState(null, "", "#" + name);
    if (name === "ask") {
      placeGraph("ask");
      if (S.view !== "inst") await setView("inst"); else if (S.eval) { focus(); }
      setTimeout(() => fitTo(S.eval ? nodes.filter((n) => n.on || n.prog || n.unscr) : nodes, 400), 50);
    } else if (name === "knowledge") {
      await knShow(S.kTab);
    } else if (name === "sources") {
      await renderSources();
    } else if (name === "testing") {
      renderScores();
    }
  }

  /* ---------------------------------------------------------------- Ask: the drawer and the sheet over the graph */
  function openPanel(which, open) {
    const el = which === "why" ? $("why-drawer") : $("cmp-sheet"), btn = which === "why" ? $("open-why") : $("open-cmp");
    const show = open ?? el.classList.contains("hidden");
    el.classList.toggle("hidden", !show); btn.setAttribute("aria-expanded", String(show));
    $("page-ask").classList.toggle("with-sheet", !$("cmp-sheet").classList.contains("hidden"));
    $("page-ask").classList.toggle("with-drawer", !$("why-drawer").classList.contains("hidden"));
    if (show && which === "cmp") lanesTick(0);
  }
  function setOverlay(o) { if (o === "why") openPanel("why", true); }   // kept for older callers
  async function loadWhy() {
    const o = S.order;
    if (!o) return;
    try {
      const r = await fetch("/api/explain?patient=" + encodeURIComponent(o.patient));
      if (S.order !== o) return;
      S.trace = r.ok ? await r.json() : { error: (await r.json().catch(() => ({}))).detail || "The explanation is not available." };
    } catch (_) { S.trace = { error: "The explanation is not available." }; }
    renderWhy();
  }
  const STATUS = { met: ["met", "ok"], not_met: ["not met", "bad"], unknown: ["unknown", "warn"], not_applicable: ["not applicable", "mute"] };
  const pill = (st) => { const [t, c] = STATUS[st] || [words(st || ""), "mute"]; return `<span class="st ${c}">${esc(t)}</span>`; };
  const KIND = { coverage_criteria: "coverage criteria", approval_required: "prior authorization", documentation_requirement: "evidence needed",
    exemption: "exemption", decision_timeframe: "decision deadline" };
  const andList = (xs) => (xs.length <= 1 ? xs.join("") : xs.slice(0, -1).join(", ") + " and " + xs[xs.length - 1]);
  function whySteps(t) {   // five one-line steps, from the same answer C gives
    const a = t.answer || {}, cov = t.coverage || {}, ctr = cov.contractor;
    const pols = [...new Set((t.policies || []).filter((r) => r.basis !== "stated_by_exhaustion").map((r) => r.citation))];
    const cc = (a.coverage_criteria || {}).policies || [];
    const items = cc.flatMap((p) => (p.groups || []).flatMap((g) => g.items || []));
    const short = (it) => String(it.detail || it.element || it.fact).replace(/^Diagnosis: [^:]*: /, "diagnosis ").replace(/ is on the order$/, " listed").replace(/ is < /, " below ");
    let facts;
    if (cc.length) {
      const bad = items.filter((i) => i.status === "not_met"), unk = cc.some((p) => p.status === "unknown");
      facts = cc.every((p) => p.status === "met") ? ["The patient meets it", items.filter((i) => i.status === "met").slice(0, 2).map(short).join(" · ")]
        : bad.length ? ["The patient does not meet it", bad.slice(0, 2).map(short).join(" · ")]
        : unk ? ["A fact is missing", (a.missing_facts || []).map(words).join(", ") || "see the details"] : ["Checked against the facts", ""];
    } else {
      const pa = a.prior_authorization || {};
      facts = ["Checked against the order", pa.outcome ? `prior authorization: ${OUTCOME[pa.outcome]?.[0] || words(pa.outcome)}` : "no patient facts needed"];
    }
    const progs = t.programs || [], checked = progs.filter((g) => g.screened), open = progs.filter((g) => !g.screened);
    const prog = !progs.length ? ["No CMS program applies", "this plan uses its own list"]
      : [open.length ? `${open.length} Medicare program${open.length > 1 ? "s" : ""} not checked yet` : "No Medicare program lists the drug",
         `${checked.length} of ${progs.length} programs checked${open.length ? ": " + open.map((g) => g.program.replace(/^CMS /, "")).join(", ") + " still to check" : ""}`];
    const [label, color] = OUTCOME[a.outcome] || ["Not known", "#F04F4F"];
    return [
      ["Coverage found", `${cov.plan_name || cov.plan || "none"}, ${cov.state_name || cov.state || "–"}${ctr ? `, contractor ${ctr.contractor.split(" ")[0]} (${ctr.code})` : ""}`, cov.plan ? "ok" : "bad"],
      [pols.length ? `${pols.length > 1 ? "These policies apply" : "This policy applies"}` : "No approved policy covers it", andList(pols), pols.length ? "ok" : "warn"],
      [facts[0], facts[1], facts[0].startsWith("The patient meets") || facts[0].startsWith("Checked") ? "ok" : facts[0].startsWith("A fact") ? "warn" : "bad"],
      [prog[0], prog[1], open.length ? "warn" : "ok"],
      [label, a.safe_to_act ? "Safe to act" : "Not safe to act" + ((a.not_safe_because || []).length ? ": " + why(a.not_safe_because[0]) : ""), a.safe_to_act ? "ok" : "warn", color]];
  }
  function whyDetail(t) {   // every rule checked, item by item, with its quote; then the query
    const a = t.answer || {};
    const src = (cit) => (a.sources || []).find((x) => x.citation === cit) || {};
    const cc = ((a.coverage_criteria || {}).policies || []).map((p) => {
      const sx = src(p.citation);
      const groups = (p.groups || []).map((g) => `<div class="grp"><span class="gl">${g.logic === "any" ? "Any one of" : "All of"}</span>${pill(g.status)}<ul>`
        + (g.items || []).map((it) => `<li>${pill(it.status)} ${esc(it.detail || it.element || it.fact)}</li>`).join("") + "</ul></div>").join("");
      return `<div class="rule"><div class="rh"><b>${esc(p.citation)}</b> ${pill(p.status)}</div>${groups}`
        + (sx.quote ? `<blockquote>${esc(sx.quote)}<cite>${esc(p.citation)}${sx.page ? ", page " + esc(sx.page) : ""}</cite></blockquote>` : "") + "</div>";
    }).join("");
    const progRows = (t.programs || []).map((g) => `<tr><td>${esc(g.program)}</td><td>${g.screened ? '<span class="st ok">checked</span>' : '<span class="st warn">not checked</span>'}</td>`
      + `<td>${esc(g.screened ? g.finding : "This program's list is not saved in this copy of the pack.")}${g.page ? ` <i>page ${esc(g.page)}</i>` : ""}</td></tr>`).join("");
    return cc + (progRows ? `<table class="mini"><thead><tr><th>Medicare program</th><th>List</th><th>Finding</th></tr></thead><tbody>${progRows}</tbody></table>` : "")
      + `<pre class="q">${esc(t.sql.answer)}\n\n-- the steps above\n${esc(t.sql.trace)}</pre>`;
  }
  function renderWhy() {
    const box = $("why"), t = S.trace;
    if (!t) { box.innerHTML = '<p class="empty">Run the case to see why the answer is right.</p>'; return; }
    if (t.error) { box.innerHTML = `<p class="empty">${esc(t.error)}</p>`; return; }
    box.innerHTML = `<ol class="steps">${whySteps(t).map(([h, d, st, col], i) => `<li class="${st}"><span>${i + 1}</span><div><b${col ? ` style="color:${col}"` : ""}>${esc(h)}</b>${d ? `<p>${esc(d)}</p>` : ""}</div></li>`).join("")}</ol>`
      + `<p class="note">The same database function C uses, step by step. Nothing here is written by AI.</p>`
      + `<details class="more-d"><summary>Show the details and the query</summary>${whyDetail(t)}</details>`;
  }
  function renderMatch() {
    const box = $("match"), c = S.cEval, b = lanes.B ? lanes.B.st : null;
    if (!c && !(b && b.eval)) { box.innerHTML = '<p class="empty">Run C and B to compare them field by field.</p>'; return; }
    const srcOf = (p) => { if (!p) return null; const pr = p.primary_rule || {}; const x = (p.sources || []).find((y) => y.citation === pr.citation) || {}; return pr.citation ? `${pr.citation}${x.page ? ", page " + x.page : ""}` : "none"; };
    const yes = (v) => (v == null ? null : v ? "yes" : "no");
    const w = b && b.wrote;
    const F = [
      ["Outcome", (p) => p && (OUTCOME[p.outcome]?.[0] || p.outcome), w && (OUTCOME[w.outcome]?.[0] || w.outcome)],
      ["Safe to act", (p) => p && yes(p.safe_to_act), w && w.safe_to_act != null ? yes(w.safe_to_act === true || w.safe_to_act === "true") : null],
      ["Deciding rule", (p) => p && (ruleOf(p) || "none"), w && w.rule_ref],
      ["Source", srcOf, w && w.source],
      ["Next step", (p) => p && words(p.next_step || ""), w && words(w.next_step || "")]];
    const cell = (v, ref) => (v == null || v === "" ? '<td class="none">–</td>' : `<td>${esc(v)}${ref != null && ref !== "" ? (v === ref ? ' <span class="ok">✓</span>' : ' <span class="bad">✗</span>') : ""}</td>`);
    const rows = F.map(([k, f, wv]) => { const cv = f(c); return `<tr><th>${k}</th>${cell(cv, null)}${cell(wv, cv)}${cell(b && b.eval ? f(b.eval) : null, cv)}</tr>`; }).join("");
    const verdict = !b || !b.eval || !c ? "" : sameAnswer(b.eval, c) ? '<p class="verdict ok">B matches C.</p>' : '<p class="verdict bad">B does not match C. C\'s answer stays on screen.</p>';
    box.innerHTML = verdict + `<table class="mini match-t"><thead><tr><th></th><th>C · rules only</th><th>B · what the AI wrote itself</th><th>B · final, after the guardrail</th></tr></thead><tbody>${rows}</tbody></table>`
      + (b && b.done && !w ? '<p class="note">The AI made no record call this run, so it wrote nothing of its own.</p>' : "");
  }

  /* ---------------------------------------------------------------- Sources page */
  const ago = (iso) => (iso ? new Date(String(iso).length === 10 ? iso + "T00:00:00" : iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "–");
  const FRESH = { current: "ok", aging: "warn", stale: "bad", "never checked": "warn", "not saved": "mute" };
  const GROUPS = [["Medicare coverage policies", "Coverage policies and billing articles from the Medicare contractors"],
    ["Payer documents", "A payer's prior authorization list and drug policy"],
    ["Federal regulations", "Decision deadlines for Medicare Advantage"],
    ["CMS program lists", "Where Original Medicare requires prior authorization"]];
  async function renderSources() {
    if (!S.src) {
      const r = await fetch("/api/sources");
      S.src = r.ok ? await r.json() : { error: (await r.json().catch(() => ({}))).detail || "Not available." };
    }
    const d = S.src;
    if (d.error) { $("src-groups").innerHTML = `<p class="empty">${esc(d.error)}</p>`; return; }
    $("src-lede").textContent = `${d.documents.length} official documents, saved and fingerprinted. Every rule points back to one of them.`;
    $("src-sql").textContent = d.sql.documents;
    $("src-groups").innerHTML = GROUPS.map(([g, about], i) => {
      const docs = d.documents.filter((x) => x.document_group === g);
      const rules = docs.reduce((t, x) => t + x.rule_count, 0);
      const by = {}; docs.forEach((x) => (by[x.freshness] = (by[x.freshness] || 0) + 1));
      const worst = ["stale", "aging", "never checked", "not saved", "current"].find((k) => by[k]) || "current";
      const fresh = by.current === docs.length ? "all current" : Object.entries(by).map(([k, n]) => `${n} ${k}`).join(" · ");
      return `<button type="button" class="panel grp" data-g="${i}" aria-expanded="false"><span class="n">${docs.length}</span><span class="nm">${esc(g)}</span><span class="d">${esc(about)}</span>`
        + `<span class="ft"><span><i class="dot ${FRESH[worst]}"></i>${esc(fresh)}</span><span>${rules} rule${rules === 1 ? "" : "s"}</span></span></button>`;
    }).join("");
    $("src-groups").querySelectorAll(".grp").forEach((b) => (b.onclick = () => openGroup(+b.dataset.g)));
    const regions = d.regions.map((r) => `${r.code} (${r.states})`);
    $("src-cover").textContent = `Covers ${d.plans.map((x) => x.name.split(" (")[0]).join(" and ")}. Original Medicare in contractor regions ${andList(regions)}. Any other state goes to staff.`;
    $("src-flow").innerHTML = FLOW5;
  }
  function openGroup(i) {
    const box = $("src-docs"), btns = $("src-groups").querySelectorAll(".grp"), was = btns[i].getAttribute("aria-expanded") === "true";
    btns.forEach((b) => b.setAttribute("aria-expanded", "false"));
    if (was) { box.classList.add("hidden"); return; }
    btns[i].setAttribute("aria-expanded", "true");
    const docs = S.src.documents.filter((x) => x.document_group === GROUPS[i][0]);
    box.innerHTML = `<h2>${esc(GROUPS[i][0])}</h2>` + docs.map((x, k) => `<div class="doc"><div class="dh" data-k="${k}">`
      + `<div class="t"><b>${esc(x.title)}</b><span>${esc(x.document_type)}${x.edition ? " · " + esc(x.edition) : ""}</span></div>`
      + `<div class="m">${x.is_change_pending ? `<span class="st warn" title="A new edition was detected and is waiting for review">change pending${x.change_applies_from ? " from " + ago(x.change_applies_from) : ""}</span>` : ""}<span class="st ${FRESH[x.freshness] || "mute"}">${esc(x.freshness)}</span><span>checked ${ago(x.last_checked_at)}</span>`
      + `${x.url ? `<a href="${esc(x.url)}" target="_blank" rel="noopener">original</a>` : ""}</div></div>`
      + `<div class="dr">${x.rules.length ? x.rules.map((r) => `<p><span>${esc(r.rule_type)}</span>${esc(r.summary)}${r.page ? ` <i>page ${esc(r.page)}</i>` : ""}</p>`).join("") : `<p class="role">${esc(x.role)}</p>`}`
      + `${x.fingerprint ? `<p class="fp">Fingerprint <code>${esc(x.fingerprint)}</code> · saved ${ago(x.saved_at)}</p>` : ""}</div></div>`).join("");
    box.classList.remove("hidden");
  }
  const FLOW5 = [["Check", "Look for a new edition on a schedule", "being built", "warn"],
    ["Fingerprint", "Download it and compare its SHA-256", "built", "ok"],
    ["Flag", "Answers resting on it go to staff", "built", "ok"],
    ["Draft and check", "AI drafts the rule, code checks the quote", "built", "ok"],
    ["Approve and release", "People approve; client copies sync", "built", "ok"]]
    .map(([h, d, t, c]) => `<li><b>${h}</b><span>${d}</span><em class="st ${c}">${t}</em></li>`).join("");

  /* ---------------------------------------------------------------- Knowledge page */
  const WHERE = [["Tennessee", (r) => /\bTN\b|Tennessee/.test(r.applies_in || "")], ["Ohio", (r) => /\bOH\b|Ohio/.test(r.applies_in || "")],
    ["UHC Advantage", (r) => /UHC Medicare Advantage/.test(r.applies_in || "")], ["Nationwide", (r) => /nationwide/.test(r.applies_in || "")]];
  const whereText = (s) => String(s || "").replace(/Medicare A\/B Jurisdiction \w+ \(([^:]+): ([^)]+)\)/g, "$2")
    .replace(/;/g, ",");
  const KF = { where: null, kind: null, q: "" };
  async function knShow(k) {
    S.kTab = k;
    document.querySelectorAll("#kn-show button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.k === k)));
    const graph = k !== "rules";
    $("kn-rules").classList.toggle("hidden", graph); $("kn-graph").classList.toggle("hidden", !graph);
    $("kn-where-box").classList.toggle("hidden", graph); $("kn-kind-box").classList.toggle("hidden", graph);
    if (graph) {
      placeGraph("knowledge");
      await setView(k === "schema" ? "onto" : "inst");
      if (k === "graph") setTimeout(() => fitTo(nodes, 400), 60);
      return;
    }
    if (!S.kn) {
      const r = await fetch("/api/knowledge");
      S.kn = r.ok ? await r.json() : { error: (await r.json().catch(() => ({}))).detail || "Not available." };
      if (!S.kn.error) {
        const chips = (box, list, key) => {
          $(box).innerHTML = list.map((x) => `<button type="button" data-v="${esc(x)}" aria-pressed="false">${esc(x)}</button>`).join("");
          $(box).querySelectorAll("button").forEach((b) => (b.onclick = () => { KF[key] = KF[key] === b.dataset.v ? null : b.dataset.v; renderRules(); }));
        };
        chips("kn-where", WHERE.map((w) => w[0]), "where");
        chips("kn-kind", [...new Set(S.kn.rules.map((r) => r.rule_type))], "kind");
        $("kn-search").oninput = (e) => { KF.q = e.target.value.trim().toLowerCase(); renderRules(); };
        $("kn-sql").textContent = S.kn.sql.rules;
      }
    }
    renderRules();
  }
  function renderRules() {
    if (S.kn.error) { $("kn-list").innerHTML = `<p class="empty">${esc(S.kn.error)}</p>`; return; }
    ["where", "kind"].forEach((k) => $("kn-" + k).querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(KF[k] === b.dataset.v))));
    const wf = KF.where && WHERE.find((w) => w[0] === KF.where)[1];
    const rows = S.kn.rules.filter((r) => (!wf || wf(r)) && (!KF.kind || r.rule_type === KF.kind)
      && (!KF.q || [r.summary, r.citation, r.subject, r.rule_ref, r.quote, r.applies_in].join(" ").toLowerCase().includes(KF.q)));
    $("kn-count").textContent = `${rows.length} of ${S.kn.rules.length} rules`;
    $("kn-list").innerHTML = rows.map((r) => `<article class="panel rc" tabindex="0"><div class="kd">${esc(r.rule_type)} · ${esc(whereText(r.applies_in))}</div>`
      + `<div class="side">${esc(r.citation)}${r.page ? " · page " + esc(r.page) : ""}</div><p class="s">${esc(r.summary)}</p>`
      + `<div class="m">Approved ${ago(r.approved_at)} · in force from ${ago(r.effective_from)}${r.effective_to ? " to " + ago(r.effective_to) : ""}</div>`
      + (r.quote ? `<blockquote class="hidden">${esc(r.quote)}<cite>${esc(r.citation)}${r.page ? ", page " + esc(r.page) : ""} · ${esc(r.rule_ref)}</cite></blockquote>` : "") + `</article>`).join("")
      || '<p class="empty">No rule matches.</p>';
    $("kn-list").querySelectorAll(".rc").forEach((a) => (a.onclick = () => a.querySelector("blockquote")?.classList.toggle("hidden")));
  }

  /* ---------------------------------------------------------------- Testing page: the scores first, everything else one click away */
  function renderScores() {
    const cmp = S.compare || {}, scs = Object.values(cmp.scenarios || {});
    if (!scs.length) { $("t-scores").innerHTML = '<p class="empty">No test report yet. Run <code>python -m propel.baseline.compare --runs 5</code>.</p>'; return; }
    const sum = (k, f) => scs.filter((e) => e[k]).reduce((t, e) => t + f(e[k]), 0);
    const cnt = (k) => scs.filter((e) => e[k]).length;
    const avg = (k, f) => (cnt(k) ? sum(k, f) / cnt(k) : 0);
    const varied = (k) => scs.filter((e) => e[k] && Object.keys(e[k].outcomes || {}).length > 1).length;
    const cRuns = sum("C", (x) => x.runs), cSame = scs.filter((e) => e.C).every((e) => Object.keys(e.C.outcomes || {}).length === 1);
    const tile = (L, n, d, good) => `<div class="panel sc"><span class="L" style="color:${LANES[L][2]}">${L}</span><div class="n ${good ? "ok" : "bad"}">${n}</div><p>${d}</p></div>`;
    $("t-scores").innerHTML = tile("C", cSame ? `${cRuns} / ${cRuns}` : "–", "Rules only: the same answer every run, no AI", cSame)
      + tile("B", `${sum("B", (x) => x.matches_rules)} / ${sum("B", (x) => x.runs)}`, "Agent with Kanini Context matched the rules", sum("B", (x) => x.matches_rules) === sum("B", (x) => x.runs))
      + (cnt("A") ? tile("A", `${sum("A", (x) => x.matches_rules)} / ${sum("A", (x) => x.runs)}`, `Agent without it; its answer changed between runs on ${varied("A")} of ${cnt("A")} cases`, false) : "");
    const bars = [["C · rules only", 0, "C"], ["B · with context", avg("B", (x) => x.tokens_in), "B"], ["A · without", avg("A", (x) => x.tokens_in), "A"]];
    const max = Math.max(...bars.map((b) => b[1]), 1);
    $("t-bars").innerHTML = bars.map(([n, v, L]) => `<div class="bar"><span>${n}</span><i style="width:${Math.max(0.6, (v / max) * 100)}%;background:${LANES[L][2]}"></i><b>${fmt(v)}</b></div>`).join("")
      + `<p class="note">Average over ${scs.length} cases, ${scs[0].C ? scs[0].C.runs : 5} runs each.</p>`;
    $("t-fails").innerHTML = [["Wrong region.", "Applied Tennessee's policy to an Ohio patient."],
      ["Silence read as a rule.", "Gave an answer the documents do not give."],
      ["Different answers.", "The same question got a different answer between runs."]].map(([h, d]) => `<p class="fail"><b>${h}</b> ${d}</p>`).join("");
  }
  $("t-more").onclick = () => {
    const box = $("t-all"), open = box.classList.contains("hidden");
    box.classList.toggle("hidden", !open); $("t-more").setAttribute("aria-expanded", String(open));
    $("t-more").textContent = open ? "Hide every run and the limits" : "See every run and the limits";
    if (open) { renderTesting(); renderLimits(); box.scrollIntoView({ behavior: "smooth" }); }
  };
  /* ---------------------------------------------------------------- Testing page: what is still to run, and the limits */
  function renderLimits() {
    $("t-e1").innerHTML = "<p>Search split by section and by rule-sized chunks, scored the same way, counting answers that invented a rule apart from answers that applied a real rule to the wrong case.</p><p class=\"note\">Not run yet.</p>";
    $("t-e2").innerHTML = "<p>Cases built to make the agent fail: a value only in a note, two dated results, other units, an injected instruction, a drug name without a code, conflicting diagnoses, an unknown state or drug.</p><p class=\"note\">Not run yet.</p>";
    $("t-limits").innerHTML = [
      "It covers only what has been curated: two plans, the regions above, two drugs. Every new state or payer is curation work.",
      "Curation needs people: rules are drafted with AI but checked and approved by hand. In the prototype one engineer approved them.",
      "An answer is only as current as the last check. Old sources or a lapsed client copy make answers \"not safe to act\": safe, but less automation.",
      "\"No PA\" under Original Medicare rests on the CMS program lists being complete; a new program has to be added before such answers are trusted again.",
      "The agent still has to read the patient record correctly. Wrong facts in give a wrong answer out.",
      "B's score depends on its guardrail: the model often skips writing its own card, and a card it writes can be wrong until the guardrail sets it from the rules.",
      "B costs model calls and time that C does not. Use C wherever no judgement is needed.",
      "It is not a payer's determination: the final decision stays with staff and the payer."].map((t) => `<li>${esc(t)}</li>`).join("");
  }

  async function setView(v) {
    S.view = v;
    if (v === "onto" && !S.onto) S.onto = await (await fetch("/api/ontology")).json();
    $("detail").classList.add("hidden");
    if (v === "onto") { renderSchema(S.onto); return; }
    svg.on("click.schema", null);
    setGraph(S.inst, v);
    if (S.eval) lightAnswer(S.eval);
  }
  $("run-race").onclick = () => race(["C", "B", "A"]);
  $("open-why").onclick = () => openPanel("why");
  $("open-cmp").onclick = () => openPanel("cmp");
  document.querySelectorAll("[data-close]").forEach((b) => (b.onclick = () => openPanel(b.dataset.close, false)));
  document.querySelectorAll("#cmp-tabs button").forEach((b) => (b.onclick = () => {
    document.querySelectorAll("#cmp-tabs button").forEach((x) => x.setAttribute("aria-selected", String(x === b)));
    $("lanes").classList.toggle("hidden", b.dataset.t !== "lanes"); $("match").classList.toggle("hidden", b.dataset.t !== "match");
  }));
  document.querySelectorAll("#kn-show button").forEach((b) => (b.onclick = () => knShow(b.dataset.k)));
  document.querySelectorAll("#pages button").forEach((b) => (b.onclick = () => showPage(b.dataset.page)));
  window.addEventListener("hashchange", () => { const h = location.hash.slice(1); if (PAGES.includes(h) && h !== S.page) showPage(h); });

  (async function init() {
    const get = (u, fb) => fetch(u).then((r) => (r.ok ? r.json() : fb)).catch(() => fb);
    const [st, orders, g, cmp] = await Promise.all([get("/api/status", {}), get("/api/orders", []), get("/api/graph", { nodes: [], edges: [] }), get("/api/compare", {})]);
    S.scripted = !!st.scripted;
    $("status").innerHTML = `Release <span class="code">${esc(st.release)}</span>`;
    $("status").title = `${st.rules} rules · model ${st.model}`;
    S.orders = orders; S.inst = g; S.compare = cmp || {};
    $("orders").innerHTML = orders.map((o) => `<button type="button" class="case" data-p="${esc(o.patient)}" aria-pressed="false"><b>${esc(caseName(o))}</b><span>${esc(drugName(o))} · ${esc(planShort(o))}</span></button>`).join("");
    document.querySelectorAll(".case").forEach((c) => (c.onclick = () => pick(orders.find((o) => o.patient === c.dataset.p))));
    buildLanes();
    setGraph(g, "inst");
    pick(orders[0]);
    loadLog();
    renderLimits();
    const h = location.hash.slice(1);
    if (PAGES.includes(h) && h !== "ask") showPage(h);
  })();
})();
