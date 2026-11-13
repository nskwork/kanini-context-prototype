/* Kanini Context dashboard. Every highlight, comet and particle follows an event from a real run or a
   number from the release or the latest comparison report; nothing is scripted on the page. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const fmt = (n) => (n == null ? "–" : Math.round(n).toLocaleString());
  const bare = (ref) => String(ref || "").split("@")[0];

  const KIND_COLOR = { Payer: "#f472b6", Plan: "#60a5fa", Program: "#60a5fa", Jurisdiction: "#fbbf24", Concept: "#34d399", Code: "#94a3b8",
    ValueSet: "#94a3b8", Rule: "#a78bfa", Authority: "#c084fc", Source: "#fde68a", Snapshot: "#e7d8a0", DataElement: "#2dd4bf", Endpoint: "#f472b6" };
  const SHELL = { Concept: 0, Code: 70, Rule: 120, DataElement: 165, Authority: 200, Plan: 250, Program: 250, Payer: 250,
    Source: 285, Snapshot: 320, ValueSet: 320, Endpoint: 320, Jurisdiction: 390 };
  const ROOT_COLOR = { CoverageItem: "#fbbf24", Authority: "#c084fc", Rule: "#a78bfa", TerminologyItem: "#34d399", Endpoint: "#f472b6",
    KnowledgeArtifact: "#2dd4bf", Source: "#fde68a", Snapshot: "#e7d8a0", DataElement: "#2dd4bf", Thing: "#94a3b8" };
  const OUTCOME = {
    no_pa: ["No PA", "#a3e635"], exempt: ["Exempt", "#a3e635"], pa_required: ["PA required", "#22d3ee"],
    other_gate: ["Other gate", "#fbbf24"], needs_facts: ["Not yet known", "#fbbf24"], unknown: ["Not known", "#fb7185"]
  };

  const words = (s) => String(s || "").replace(/_/g, " ").replace(/([a-z])([A-Z])/g, "$1 $2");
  const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1).toLowerCase();
  const TOOL_LABEL = {   // the agent's tool calls in plain words (the tool name shows on hover)
    journey_get: "patient record", context_applicable_rules: "find the rules", rules_evaluate: "check the rules",
    policy_search: "quote the source", journey_record: "log the decision"
  };

  const S = { seq: 0, orders: [], order: null, inst: null, onto: null, view: "inst", compare: {}, run: null, es: null,
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
    if (S.view === "onto") return ROOT_COLOR[n.root] || "#a78bfa";
    if (n.unscr) return "#fb7185";
    if (n.prog) return "#fb923c";
    return KIND_COLOR[n.kind] || "#94a3b8";
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
      .attr("fill", nodeColor).style("filter", (n) => `drop-shadow(0 0 ${n.on ? 8 : n.prog || n.unscr ? 6 : 3}px ${nodeColor(n)})`);
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
      : n.on || n.cand || n.unscr || (n.prog && n.kind !== "Rule") ? 1 : n.prog ? 0 : zoomK >= 2.1 ? 0.85 : zoomK >= 1.25 && big.has(n.kind) ? 0.75 : S.view === "onto" && (n.depth <= 1 || zoomK >= 1.3) ? 0.75 : 0);
  }
  function legend(mode) {
    const counts = {};
    nodes.forEach((n) => { const k = mode === "onto" ? n.root : n.kind; counts[k] = (counts[k] || 0) + 1; });
    const pal = mode === "onto" ? ROOT_COLOR : KIND_COLOR;
    $("legend").innerHTML = (mode === "onto" ? "<span>classes by root ·</span>" : `<span>${nodes.length} nodes · ${links.length} edges ·</span>`) +
      Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([k, n]) => `<span><i style="background:${pal[k] || "#94a3b8"}"></i>${esc(cap(words(k)))} ${n}</span>`).join("");
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
    const x0 = d3.min(pts, (n) => n.x), x1 = d3.max(pts, (n) => n.x), y0 = d3.min(pts, (n) => n.y), y1 = d3.max(pts, (n) => n.y);
    const k = Math.min(2.4, 0.7 / Math.max((x1 - x0 + 100) / w, (y1 - y0 + 100) / h));
    const tr = d3.zoomIdentity.translate(w / 2, h / 2).scale(k).translate(-(x0 + x1) / 2, -(y0 + y1) / 2);
    (ms ? svg.transition().duration(ms).ease(d3.easeCubicInOut) : svg).call(zoom.transform, tr);
  }
  $("zin").onclick = () => svg.transition().duration(300).call(zoom.scaleBy, 1.4);
  $("zout").onclick = () => svg.transition().duration(300).call(zoom.scaleBy, 1 / 1.4);
  $("zfit").onclick = () => { const f = nodes.filter((n) => n.on || n.prog || n.unscr || n.cand); fitTo(f.length ? f : nodes); };
  $("zall").onclick = () => fitTo(nodes);

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
      .attr("fill", "#f5f3ff").style("filter", (d) => `drop-shadow(0 0 6px ${d.color}) drop-shadow(0 0 2px #fff)`);
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
    addComets(bfs(node("Plan", cov.plan), prim, allowed)?.concat(src ? [src] : []), "#a3e635", 4);
    addComets(bfs(code, prim, allowed), "#22d3ee", 3);
    addComets(bfs(st, primAuth, allowed), "#a3e635", 3);
    [...prog, ...unscr].filter((n) => n.kind === "Authority").forEach((a) => {
      const via = (adj.get(a) || []).map(([m]) => m).find((m) => m === st || (m.kind === "Jurisdiction" && m.id === "US"));
      if (via) addComets([via, a], a.unscr ? "#fb7185" : "#fb923c", 1);
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
  function card(p) {
    const c = $("card");
    if (!p) {
      c.className = "panel card"; c.style.removeProperty("--oc");
      c.innerHTML = '<div class="panel-head"><span class="eyebrow">Decision card</span></div><div class="panel-body card-empty">The decision card appears here when the agent or the workflow answers.</div>';
      return;
    }
    const [label, color] = OUTCOME[p.outcome] || ["Not known", "#fb7185"];
    c.className = "panel card live"; c.style.setProperty("--oc", color);
    const prim = p.primary_rule || {};
    const src = (p.sources || []).find((s) => s.citation === prim.citation) || (p.sources || [])[0];
    const quote = S.quote || (src && { quote: src.quote, citation: src.citation, page: src.page });
    const clock = p.decision_clock ? (p.decision_clock.text || "") : "";
    const ch = ((p.route || {}).channels || []).map((c) => `${c.interface}: ${String(c.address || "").replace(/^tel:/, "")}`);
    const route = p.route ? [p.route.submit_via, ch.join(" · ")].filter(Boolean).join("<br>") : "";
    const ev = (p.evidence || []).map((e) => `${e.logic === "any" ? "One of" : "All of"} (${e.citation}):<br>• ` + (e.documents || []).map(esc).join("<br>• "));
    const notChecked = ((p.coverage_criteria || {}).not_checked || []);
    c.innerHTML = `<div class="panel-head"><span class="eyebrow">Decision card · ${esc(S.order?.order_id || "")}</span></div><div class="panel-body">
      <div class="verdict">${esc(label)}</div>
      <div class="state">${p.safe_to_act ? "Safe to act" : "Not safe to act"}${(p.not_safe_because || []).length ? " · " + esc(p.not_safe_because.map(why).join("; ")) : ""}</div>
      ${lanes.B && (lanes.B.st.awaiting || (lanes.B.st.active && !lanes.B.st.done)) ? '<div class="approve"><button type="button" class="yes" id="ok" disabled>Approve</button><button type="button" class="no" id="nok" disabled>Reject</button></div>' : ""}
      <div class="answers" id="answers" aria-label="How each way answered"></div>
      <div class="k first">Why</div><ul>${(p.reasons || []).slice(0, 6).map((r) => `<li>${esc(r)}</li>`).join("") || `<li>${esc(p.reason_text || p.reason || "")}</li>`}</ul>
      <div class="k">Next step</div><div class="v">${esc(p.next_step_detail || p.next_step)}</div>
      ${clock ? `<div class="k">Deadline</div><div class="v">${esc(clock)}</div>` : ""}
      <div class="k">Rule</div><div class="v">${esc(prim.citation || "none")}</div><div class="rule">${esc(prim.rule_ref || "")}</div>
      ${quote && quote.quote ? `<div class="quote"><mark>${esc(quote.quote)}</mark><span class="ref">${esc(quote.citation || "")}${quote.page ? " · page " + esc(quote.page) : ""}</span></div>` : ""}
      ${route ? `<div class="k">Submit via</div><div class="v">${route.split("<br>").map(esc).join("<br>")}</div>` : ""}
      ${ev.length ? `<div class="k">Evidence to send</div><div class="v">${ev.join("<br>")}</div>` : ""}
      ${notChecked.length ? `<div class="k">Not checked here, confirm</div><div class="v">${notChecked.slice(0, 2).map(esc).join("<br>")}</div>` : ""}
      <div class="k">Pack</div><div class="v">Release <span class="code">${esc(p.pack_release)}</span> · sources ${esc(p.freshness)}${(p.review_exceptions || []).length ? " · approved by one engineer" : ""}</div>
      <div class="agent-text hidden" id="agent-text"></div></div>`;
    if ($("ok")) {
      $("ok").onclick = () => confirm(true); $("nok").onclick = () => confirm(false);
      if (lanes.B.st.awaiting) { $("ok").disabled = false; $("nok").disabled = false; }
    }
    renderAnswers();
    const o = $("outcome");
    o.classList.remove("hidden"); o.style.setProperty("--oc", color);
    $("o-label").textContent = label;
    const sub = p.outcome === "unknown" ? (p.reason_text || why((p.not_safe_because || [])[0] || p.reason)) : prim.citation;
    $("o-sub").textContent = (p.safe_to_act ? "Safe to act" : "Not safe to act") + (sub ? " · " + sub : "");
  }

  /* ---------------------------------------------------------------- the race: every lane fills only from a run made here */
  // The third number differs by lane on purpose: C is the approved rules applied by plain code, so it is the answer key
  // (it cannot be "wrong"); A is graded against it; B states the rules' answer by design, so its check is that it used it.
  const LANES = { A: ["documents", "AI reads the policy documents", "#fb7185", "Matches the key"],
    B: ["agent", "AI agent asks Kanini Context", "#22d3ee", "Used the rules"],
    C: ["workflow", "No AI: the approved rules, applied directly", "#a3e635", "Answer key"] };
  const MODE_LANE = { documents: "A", agent: "B", workflow: "C" };
  const lanes = {};
  const blank = () => ({ run: null, es: null, active: false, done: false, t0: 0, ms: null, tin: 0, tout: 0, turns: 0, prompt: 0,
    answer: null, ref: null, ok: null, callMs: null, note: "" });
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
  function renderAnswers() {   // the card's comparison strip: what each way answered for this order, and whether it matches the rules
    const box = $("answers");
    if (!box) return;
    const rules = S.eval ? S.eval.outcome : lanes.A?.st.ref;
    const row = (L, name) => {
      const st = lanes[L].st;
      let ans = null, ok = null;
      if (L === "A") { ans = st.answer; ok = st.ok; }
      else if (st.ok) { ans = rules; ok = L === "B" ? true : null; }
      const label = st.skipped ? "not run (no API key)" : st.active ? "running…" : ans ? (OUTCOME[ans]?.[0] || ans) : "not run";
      const mark = L === "C" && ans ? '<span class="key">key</span>'
        : L === "B" && ans && st.cardFixed ? '<span class="ok" title="The AI wrote a different outcome on its card; the card was set from the rules">✓ card fixed</span>'
        : ok == null ? "" : ok ? '<span class="ok">✓</span>' : '<span class="bad">✗</span>';
      return `<div class="ans" style="--lc:${LANES[L][2]}"><b>${L}</b><span class="nm">${name}</span><span class="av">${esc(label)}</span>${mark}</div>`;
    };
    box.innerHTML = `<div class="k first">How each way answered</div>` + row("A", "AI read the documents") + row("B", "AI agent + Kanini Context") + row("C", "No AI (answer key)")
      + `<div class="ans-note">C is the approved rules applied directly: the answer key. A is checked against it; B can only state the rules' answer.</div>`;
  }
  function laneNumbers() {
    renderAnswers();
    const e = S.order && scenarioFor(S.order.patient);
    const last = (m) => (e && e[m] ? `last comparison: ${fmt(e[m].tokens_in)} tokens, ${e[m].matches_rules}/${e[m].runs} `
      + (m === "B" ? "used the rules' answer" : "matched the key") : "");
    const secs = (st) => (((st.ms ?? (performance.now() - st.t0)) / 1000).toFixed(1));
    const tick = (b) => (b == null ? "–" : b ? '<span class="ok">✓</span>' : '<span class="bad">✗</span>');
    // A: the policy documents go to the AI, now
    const a = lanes.A.st;
    $("sA").textContent = a.note || (a.done ? `AI: ${OUTCOME[a.answer]?.[0] || a.answer} · rules: ${OUTCOME[a.ref]?.[0] || a.ref || "…"}`
      + (a.whole ? ` · whole docs: ${fmt(a.whole)} tokens` : "")
      : a.active ? `Sending ${fmt(a.prompt)} tokens: only the pages Kanini Context picked${a.whole ? ` (whole documents: ${fmt(a.whole)})` : ""}…` : `Policy pages sent to the AI with the order · ${last("A") || "press Run A"}`);
    $("sA").title = a.whole ? `A was sent only the pages Kanini Context picked for this order (${fmt(a.prompt)} tokens). Read whole, the same documents are ${fmt(a.whole)} tokens, and without the pack A would also have to find them.` : "";
    const ran = (a.active || a.done) && !a.skipped;
    $("nA1").innerHTML = ran && (a.tin || a.prompt) ? fmt(a.tin || a.prompt) : "–";
    $("nA2").innerHTML = ran ? `1 · ${secs(a)}<small> s</small>` : "–";
    $("nA3").innerHTML = a.done ? tick(a.ok) : "–";
    lanes.A.target = ran ? (a.tin || a.prompt) / 900 : 0;
    // B: the agent, counted from its own model calls
    const b = lanes.B.st, scripted = S.scripted && (b.active || b.done) && !b.tin;
    $("sB").textContent = scripted ? "Rehearsal: scripted stand-in, no AI and no tokens"
      : b.done && b.timing ? `This run: AI ${b.timing.ai_s.toFixed(1)} s · tools ${b.timing.tools_s.toFixed(1)} s · start-up ${b.timing.setup_s.toFixed(1)} s · between steps ${b.timing.between_s.toFixed(1)} s`
      : b.done ? "This run: short answers from checked rules" : b.active ? "Running now…" : `The AI gets short answers from checked rules · ${last("B") || "press Run B"}`;
    $("nB1").innerHTML = scripted ? "–" : b.active || b.done ? fmt(b.tin) : "–";
    $("nB2").innerHTML = b.active || b.done ? `${b.turns} · ${secs(b)}<small> s</small>` : "–";
    $("nB3").innerHTML = b.done ? tick(b.ok) : "–";   // marked when the agent has finished, with its final time
    lanes.B.target = b.active || b.done ? b.tin / 900 : 0;
    // C: plain code, timed
    const c = lanes.C.st;
    $("sC").textContent = c.done ? "This run: no AI · the answer key A is checked against"
      : c.active ? "Looking up…" : `The answer key: plain code applies the approved rules · ${last("C") ? "last comparison: 0 tokens" : "press Run C"}`;
    $("nC1").innerHTML = c.done ? "0" : "–";
    $("nC2").innerHTML = c.done && c.callMs != null ? `0 · ${c.callMs}<small> ms</small>` : "–";
    $("nC3").innerHTML = c.done ? '<span class="key">key</span>' : "–";
  }
  function lanesTick(dt) {
    Object.values(lanes).forEach((ln) => {
      const cv = ln.canvas, w = cv.clientWidth, h = cv.clientHeight;
      if (cv.width !== w * 2) { cv.width = w * 2; cv.height = h * 2; }
      const ctx = cv.getContext("2d");
      ctx.setTransform(2, 0, 0, 2, 0, 0); ctx.clearRect(0, 0, w, h);
      while (ln.parts.length < Math.min(60, Math.round(ln.target))) ln.parts.push({ x: -Math.random() * w, y: 5 + Math.random() * (h - 10), l: 5 + Math.random() * 9 });
      if (ln.parts.length > Math.round(ln.target)) ln.parts.length = Math.round(ln.target);
      ctx.shadowColor = ln.color; ctx.shadowBlur = 8; ctx.fillStyle = ln.color;
      ln.parts.forEach((p) => { p.x += dt * w / 2.8; if (p.x > w) p.x -= w + 20; ctx.fillRect(p.x, p.y, p.l, 3); });
      if (ln.flash > 0) {
        ln.flash -= dt;
        const f = Math.max(0, ln.flash), grow = Math.min(1, (1.6 - f) * 6);
        ctx.globalAlpha = Math.min(1, f * 2); ctx.shadowBlur = 24; ctx.fillRect(0, h / 2 - 2, w * grow, 4); ctx.globalAlpha = 1;
      }
    });
    if (Object.values(lanes).some((l) => l.st.active)) laneNumbers();
  }

  /* ---------------------------------------------------------------- runs (each lane has its own) */
  function chip(text, cls, title) {
    const t = $("trace"), last = t.lastElementChild;
    if (last && last.textContent === text) return;   // a confirmed call is reported again when it resumes
    const s = document.createElement("span"); s.textContent = text; if (cls) s.className = cls; if (title) s.title = title; t.appendChild(s);
    // keep only whole chips: the oldest go when the row is full, the latest stay in view
    let room = t.clientWidth - 24;
    const kids = [...t.children].reverse();
    kids.forEach((k) => { room -= k.offsetWidth + 6; k.style.display = room < 0 ? "none" : ""; });
  }
  function setBusy() {   // buttons stay usable: a new run replaces a slow one (the server releases its approval wait)
    const b = lanes.B?.st;
    $("run-agent").textContent = b && b.active ? "Asking… (press to restart)" : "Ask the AI agent";
  }
  function closeAll() { Object.values(lanes).forEach((l) => { if (l.st.es) l.st.es.close(); }); }
  function clearAnswer() {
    unfocus(); card(null); $("outcome").classList.add("hidden"); $("trace").innerHTML = ""; $("detail").classList.add("hidden");
    S.eval = null; S.quote = null; S.lit = false;
  }
  async function race(which) {
    if (!S.order) return;
    if (S.view !== "inst") await setView("inst");
    which.forEach((L) => { const l = lanes[L]; if (l.st.es) l.st.es.close(); l.st = blank(); l.parts.length = 0; });
    if (which.includes("B") || which.includes("C")) clearAnswer();
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
      setBusy(); laneNumbers();
      await finished;
    }
  }
  function showAnswer(p) {   // the rules' answer: card and graph (from B or C, whichever comes first)
    const first = !S.eval;
    S.eval = p; card(p);
    if (first) lightAnswer(p);
    ["B", "C"].forEach((L) => { if (lanes[L].st.active || lanes[L].st.done) lanes[L].st.ok = lanes[L].st.ok ?? null; });
  }
  function onEvent(L, e) {
    const st = lanes[L].st;
    if (e.kind === "doc_start") {
      st.prompt = e.prompt_tokens; st.whole = e.whole_tokens;
    } else if (e.kind === "model") {
      // B's numbers stop at the decision card: the model's reply after staff approve is not part of answering
      if (!(L === "B" && st.confirmed)) { st.turns = e.turn; st.tin += e.tokens_in; st.tout += e.tokens_out; }
      if (L === "A") st.answer = e.answer;
      if (L === "B") {
        chip(e.tokens_in ? `AI step ${e.turn} · ${fmt(e.tokens_in)} tokens` : `AI step ${e.turn}`);
        if (e.text && e.text.includes("(Outcome line set from rules_evaluate.)")) st.cardFixed = true;
        if (e.text && $("agent-text")) { $("agent-text").classList.remove("hidden"); $("agent-text").textContent = e.text; }
      }
    } else if (e.kind === "timing") {
      st.timing = e;
    } else if (e.kind === "reference") {
      st.ref = e.outcome; st.ok = e.matches;
    } else if (e.kind === "unavailable") {
      st.note = "Not run: " + e.message; st.skipped = true;
    } else if (e.kind === "tool_call") {
      const lbl = TOOL_LABEL[e.name] || e.name;
      if (L === "B" && !(e.name === "journey_record" && [...$("trace").children].some((c) => c.textContent === lbl))) chip(lbl, "tool", e.name);
    } else if (e.kind === "tool_result") {
      if (e.name === "context_applicable_rules") lightCandidates(e.payload);
      if (e.name === "rules_evaluate") {
        st.ok = true;   // B and C state the rules' answer itself
        showAnswer(e.payload);
        if (L === "C") { st.callMs = e.call_ms; lanes.C.flash = 1.6; }
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
    document.querySelectorAll(".chip").forEach((c) => c.setAttribute("aria-pressed", String(c.dataset.p === o.patient)));
    $("q-title").innerHTML = `${esc(o.description.split(" (")[0].split(",")[0])} <span class="code">${esc(o.code)}</span>`;
    const f = o.facts, fn = o.fact_names || {};
    const val = (k) => {
      const v = f[k], meta = fn[k] && fn[k].name !== k ? fn[k] : { name: k.charAt(0).toUpperCase() + k.slice(1).replace(/_/g, " "), unit: "" };
      return [meta.name.replace(/ \(date of most recent result\)/, " date").replace(/^Diagnosis: .*/, "Diagnoses"), Array.isArray(v) ? v.join(", ") : `${v}${meta.unit && meta.unit !== "%" ? " " + meta.unit : meta.unit}`];
    };
    const rows = [["Patient", o.display_name.split(",")[0]], ["Order", o.order_id], ["Coverage", o.plan_name], ["State", o.state_name], ["Service date", o.date_of_service]]
      .concat(["diagnosis_codes", "hgb_g_dl", "hct_pct", "egfr", "creatinine_date", "dialysis_status"].filter((k) => f[k] != null).map(val));
    if (f.hgb_g_dl == null && f.hct_pct == null) rows.push(["Hemoglobin", "not on file"]);
    $("q-facts").innerHTML = rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("");
    clearAnswer();
    Object.values(lanes).forEach((l) => { l.st = blank(); l.parts.length = 0; });
    setBusy(); laneNumbers();
  }
  async function setView(v) {
    S.view = v;
    $("view-inst").setAttribute("aria-selected", String(v === "inst"));
    $("view-onto").setAttribute("aria-selected", String(v === "onto"));
    if (v === "onto" && !S.onto) S.onto = await (await fetch("/api/ontology")).json();
    setGraph(v === "inst" ? S.inst : S.onto, v);
    if (v === "inst" && S.eval) lightAnswer(S.eval);
  }
  $("view-inst").onclick = () => setView("inst");
  $("view-onto").onclick = () => setView("onto");
  $("run-agent").onclick = () => race(["B"]);
  $("run-flow").onclick = () => race(["C"]);
  $("run-race").onclick = () => race(["A", "B", "C"]);

  (async function init() {
    const get = (u, fb) => fetch(u).then((r) => (r.ok ? r.json() : fb)).catch(() => fb);
    const [st, orders, g, cmp] = await Promise.all([get("/api/status", {}), get("/api/orders", []), get("/api/graph", { nodes: [], edges: [] }), get("/api/compare", {})]);
    S.scripted = !!st.scripted;
    $("status").innerHTML = `Release <span class="code">${esc(st.release)}</span> · ${st.rules} rules · ${st.sources} sources<br>Model <span class="code">${esc(st.model)}</span>`;
    S.orders = orders; S.inst = g; S.compare = cmp || {};
    $("orders").innerHTML = orders.map((o) => `<button type="button" class="chip" data-p="${esc(o.patient)}" aria-pressed="false"><b>${esc(o.patient)}</b><span>${esc(o.code)} · ${esc(o.payer)} · ${esc(o.state)}</span></button>`).join("");
    document.querySelectorAll(".chip").forEach((c) => (c.onclick = () => pick(orders.find((o) => o.patient === c.dataset.p))));
    buildLanes();
    setGraph(g, "inst");
    pick(orders[0]);
    loadLog();
  })();
})();
