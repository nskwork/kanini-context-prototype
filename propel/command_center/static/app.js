/* Kanini Context command centre. Every highlight, comet and particle follows an event from a real run or a
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

  const S = { orders: [], order: null, inst: null, onto: null, view: "inst", compare: {}, run: null, es: null,
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
      .force("collide", d3.forceCollide((n) => (mode === "onto" ? 26 : n.on ? 42 : n.prog ? 22 : 10)))
      .force("radial", d3.forceRadial(shell, 0, 0).strength(0.55))
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
    nodes.forEach((n) => {
      let d = 0, r = n;
      while (parent.has(r) && d < 6) { r = parent.get(r); d++; }
      n.root = r.id; n.depth = d; n.shell = d === 0 ? 110 : d === 1 ? 220 : 330;
    });
  }

  function nodeColor(n) {
    if (S.view === "onto") return ROOT_COLOR[n.root] || "#a78bfa";
    if (n.unscr) return "#fb7185";
    if (n.prog) return "#fb923c";
    return KIND_COLOR[n.kind] || "#94a3b8";
  }
  function nodeLabel(n) {
    if (S.view === "onto") return n.id;
    const id = String(n.id ?? "");
    if (n.kind === "Rule") return "rule " + id.split("/").pop();
    if (n.kind === "Source") return "source · " + (n.doc || id);
    if (n.kind === "Concept") return n.label || id;
    if (n.kind === "Authority" && n.prog) return id.replace(/^CMS-/, "");   // the screened programs: short names
    if (n.kind === "DataElement" && n.name) return n.name.length > 24 ? n.name.slice(0, 22) + "…" : n.name;
    if (n.name) return n.name;
    if (n.kind === "Code" || n.kind === "DataElement") return id.split("|").slice(1).join(" ") || id;
    return id;
  }
  function paint() {
    const any = nodes.some((n) => n.on || n.cand);
    root.classed("dim", any);
    gNodes.selectAll("g").attr("class", (n) => "node" + (n.on ? " on" : "") + (n.prog || n.unscr ? " prog" : "") + (n.cand ? " cand" : ""));
    gNodes.selectAll("circle.core").attr("r", (n) => (n.on ? 7 : n.prog || n.cand || n.unscr ? 5.5 : S.view === "onto" ? 5 : 3.6))
      .attr("fill", nodeColor).style("filter", (n) => `drop-shadow(0 0 ${n.on ? 8 : n.prog ? 6 : 3}px ${nodeColor(n)})`);
    gNodes.selectAll("circle.halo").attr("r", (n) => (n.on && n.primary ? 16 : 0)).attr("stroke", nodeColor);
    gEdges.selectAll("line").attr("class", (l) => "edge" + (l.source.on && l.target.on ? " on" : "")
      + ((l.source.prog || l.target.prog) && (l.source.prog || l.source.on) && (l.target.prog || l.target.on) ? " prog" : "")
      + (l.source.cand && l.target.cand ? " cand" : ""));
    labelPass();
  }
  function labelPass() {
    const big = new Set(["Plan", "Payer", "Authority", "Concept", "Class"]);
    gNodes.selectAll("text").style("opacity", (n) =>
      n.kind === "Source" && n.on && zoomK < 2.1 ? 0   // its authority carries the same name; the source shows on hover or deep zoom
      : n.on || n.cand || n.unscr || (n.prog && n.kind !== "Rule") ? 1 : n.prog ? 0 : zoomK >= 2.1 ? 0.85 : zoomK >= 1.25 && big.has(n.kind) ? 0.75 : S.view === "onto" && (n.depth <= 1 || zoomK >= 1.3) ? 0.75 : 0);
  }
  function legend(mode) {
    const counts = {};
    nodes.forEach((n) => { const k = mode === "onto" ? n.root : n.kind; counts[k] = (counts[k] || 0) + 1; });
    const pal = mode === "onto" ? ROOT_COLOR : KIND_COLOR;
    $("legend").innerHTML = (mode === "onto" ? "<span>classes by root ·</span>" : `<span>${nodes.length} nodes · ${links.length} edges ·</span>`) +
      Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([k, n]) => `<span><i style="background:${pal[k] || "#94a3b8"}"></i>${esc(k)} ${n}</span>`).join("");
  }
  function tip(e, n) {
    const t = $("tip"), r = svg.node().getBoundingClientRect();
    t.innerHTML = `<b>${esc(n.kind)}</b> · ${esc(nodeLabel(n))}` + (n.citation ? `<br>${esc(n.citation)}${n.page ? " · page " + esc(n.page) : ""}` : "") +
      (n.valid_from ? `<br>from ${esc(n.valid_from)}` : "") + (n.basis ? ` · ${esc(n.basis)}` : "") + `<br>${adj.get(n)?.length || 0} links`;
    t.style.left = e.clientX - r.left + 14 + "px"; t.style.top = e.clientY - r.top + 10 + "px";
    t.classList.remove("hidden");
  }
  function fitTo(set, ms = 900) {
    const pts = set.filter((n) => Number.isFinite(n.x));
    if (!pts.length) return;
    const [w, h] = size();
    const x0 = d3.min(pts, (n) => n.x), x1 = d3.max(pts, (n) => n.x), y0 = d3.min(pts, (n) => n.y), y1 = d3.max(pts, (n) => n.y);
    const k = Math.min(2.4, 0.74 / Math.max((x1 - x0 + 90) / w, (y1 - y0 + 90) / h));
    const tr = d3.zoomIdentity.translate(w / 2, h / 2).scale(k).translate(-(x0 + x1) / 2, -(y0 + y1) / 2);
    (ms ? svg.transition().duration(ms).ease(d3.easeCubicInOut) : svg).call(zoom.transform, tr);
  }
  $("zin").onclick = () => svg.transition().duration(300).call(zoom.scaleBy, 1.4);
  $("zout").onclick = () => svg.transition().duration(300).call(zoom.scaleBy, 1 / 1.4);
  $("zfit").onclick = () => { const f = nodes.filter((n) => n.on || n.prog); fitTo(f.length ? f : nodes); };
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
      .force("collide", d3.forceCollide((n) => (n.on ? 42 : n.prog ? 22 : 10)))
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
    (st ? out(st, "WITHIN") : []).forEach(add);
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
    if (p.route) { const py = node("Payer", String(cov.payer || "").toLowerCase()); if (py) out(py, "EXPOSES").forEach(add); }
    // programs the answer names as not screened: "not screened against: A; B" lists their citations
    const unscr = new Set();
    ((p.prior_authorization || {}).notes || []).filter((t) => t.startsWith("not screened against:"))
      .forEach((t) => t.replace("not screened against:", "").split(";").map((x) => x.trim()).forEach((cit) => {
        nodes.filter((n) => n.kind === "Authority" && n.name === cit).forEach((n) => unscr.add(n));
      }));
    if (prog.size) { const us = node("Jurisdiction", "US"); if (us) prog.add(us); }
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
    let body = `<button type="button" class="close" aria-label="Close">close</button><h4>${esc(nodeLabel(n))}</h4><div class="mono" style="font-size:11px;color:var(--mute)">${esc(n.kind)}</div>`;
    const nb = (adj.get(n) || []).slice(0, 14).map(([m, l]) => `${esc(l.source === n ? l.rel.toLowerCase() + " →" : "← " + l.rel.toLowerCase())} ${esc(nodeLabel(m))}`);
    try {
      if (S.view === "inst" && n.kind === "Rule") {
        const r = await (await fetch("/api/rule?ref=" + encodeURIComponent(n.id))).json();
        body += `<div class="quote">${esc(r.quote)}<span class="ref">${esc(r.citation || "")} · page ${esc((r.locator || {}).page)} · ${esc(r.rule_ref || "")}</span></div>`;
      } else if (S.view === "inst" && n.kind === "Source") {
        const s = await (await fetch("/api/source/" + encodeURIComponent(n.id))).json();
        const last = (s.checks || []).slice(-1)[0] || {};
        body += `<div style="margin-top:8px">${esc(s.title)}<br><span class="mono" style="font-size:11px;color:var(--mute)">${esc(s.edition)}<br>sha256 ${esc((s.sha256 || "not recorded").slice(0, 16))}…<br>last checked ${esc((last.at || "").slice(0, 10))} ${esc(last.note || "")}</span></div>`;
      }
    } catch (_) { /* the panel still shows the node's links */ }
    body += `<div class="mono" style="font-size:11px;margin-top:8px;line-height:1.6">${nb.join("<br>")}${(adj.get(n) || []).length > 14 ? "<br>…" : ""}</div>`;
    d.innerHTML = body;
    d.querySelector(".close").onclick = () => d.classList.add("hidden");
  }

  /* ---------------------------------------------------------------- decision card */
  function card(p) {
    const c = $("card");
    if (!p) { c.className = "card"; c.innerHTML = '<div class="card-empty">The decision card appears here when the agent or the workflow answers.</div>'; return; }
    const [label, color] = OUTCOME[p.outcome] || ["Not known", "#fb7185"];
    c.className = "card live"; c.style.setProperty("--oc", color);
    const prim = p.primary_rule || {};
    const src = (p.sources || []).find((s) => s.citation === prim.citation) || (p.sources || [])[0];
    const quote = S.live.quote || (src && { quote: src.quote, citation: src.citation, page: src.page });
    const clock = p.decision_clock ? (p.decision_clock.text || "") : "";
    const ch = ((p.route || {}).channels || []).map((c) => `${c.interface}: ${String(c.address || "").replace(/^tel:/, "")}`);
    const route = p.route ? [p.route.submit_via, ch.join(" · ")].filter(Boolean).join("<br>") : "";
    const ev = (p.evidence || []).map((e) => `${e.logic === "any" ? "One of" : "All of"} (${e.citation}):<br>• ` + (e.documents || []).map(esc).join("<br>• "));
    const notChecked = ((p.coverage_criteria || {}).not_checked || []);
    c.innerHTML = `<h3>Decision card · ${esc(S.order?.order_id || "")}</h3>
      <div style="font-family:Outfit;font-weight:800;font-size:26px;color:${color};text-shadow:0 0 14px ${color};margin-top:6px">${esc(label)}</div>
      <div style="font-size:12.5px;color:${color}">${p.safe_to_act ? "safe to act" : "not safe to act"}${(p.not_safe_because || []).length ? " · " + esc(p.not_safe_because.join(", ")) : ""}</div>
      <div class="approve"><button type="button" class="yes" id="ok" disabled>Approve</button><button type="button" class="no" id="nok" disabled>Reject</button></div>
      <div class="k">Why</div><ul>${(p.reasons || []).slice(0, 6).map((r) => `<li>${esc(r)}</li>`).join("") || `<li>${esc(p.reason_text || p.reason || "")}</li>`}</ul>
      <div class="k">Next step</div><div class="v">${esc(p.next_step_detail || p.next_step)}</div>
      ${clock ? `<div class="k">Deadline</div><div class="v">${esc(clock)}</div>` : ""}
      ${route ? `<div class="k">Submit via</div><div class="v">${route.split("<br>").map(esc).join("<br>")}</div>` : ""}
      ${ev.length ? `<div class="k">Evidence to send</div><div class="v">${ev.join("<br>")}</div>` : ""}
      ${notChecked.length ? `<div class="k">Not checked here, confirm</div><div class="v">${notChecked.slice(0, 2).map(esc).join("<br>")}</div>` : ""}
      <div class="k">Rule</div><div class="rule">${esc(prim.rule_ref || "none")} · ${esc(prim.citation || "")}</div>
      ${quote && quote.quote ? `<div class="quote"><mark>${esc(quote.quote)}</mark><span class="ref">${esc(quote.citation || "")}${quote.page ? " · page " + esc(quote.page) : ""}</span></div>` : ""}
      <div class="k">Pack</div><div class="v mono" style="font-size:11px">${esc(p.pack_release)} · sources ${esc(p.freshness)}${(p.review_exceptions || []).length ? " · approved by one engineer" : ""}</div>
      <div class="agent-text hidden" id="agent-text"></div>`;
    $("ok").onclick = () => confirm(true);
    $("nok").onclick = () => confirm(false);
    const o = $("outcome");
    o.classList.remove("hidden"); o.style.setProperty("--oc", color);
    $("o-label").textContent = label;
    const why = p.outcome === "unknown" ? (p.reason_text || (p.not_safe_because || [])[0] || p.reason) : prim.citation;
    $("o-sub").textContent = (p.safe_to_act ? "safe to act" : "not safe to act") + (why ? " · " + why : "");
  }

  /* ---------------------------------------------------------------- the race: A from the report, B and C live */
  const lanes = {};
  function scenarioFor(pid) {
    const sc = (S.compare.scenarios || {});
    return Object.values(sc).find((e) => e.patient === pid) || null;
  }
  function buildLanes() {
    const box = $("lanes"); box.innerHTML = "";
    [["A", "Model reads the policies", "#fb7185"], ["B", "Agent + Kanini Context", "#22d3ee"], ["C", "Workflow, no model", "#a3e635"]].forEach(([L, title, color]) => {
      const el = document.createElement("div");
      el.className = "lane"; el.style.setProperty("--lc", color);
      el.innerHTML = `<span class="L">${L}</span><div><div class="t">${title}</div><div class="s" id="s${L}"></div></div>
        <div class="track"><canvas id="c${L}"></canvas></div>
        <div class="nums"><div><div class="h">INPUT TOKENS</div><div class="n" id="n${L}1">–</div></div>
        <div><div class="h">CALLS · TIME</div><div class="n" id="n${L}2">–</div></div><div><div class="h">RIGHT</div><div class="n" id="n${L}3">–</div></div></div>`;
      box.appendChild(el);
      lanes[L] = { color, canvas: el.querySelector("canvas"), parts: [], target: 0, flash: 0 };
    });
    laneNumbers();
  }
  function laneNumbers() {
    const e = S.order && scenarioFor(S.order.patient), when = S.compare.at ? new Date(S.compare.at).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : null;
    const A = e?.A, B = e?.B, C = e?.C, live = S.live;
    $("sA").textContent = A ? `measured in the comparison run, ${when}` : "run propel/baseline/compare.py for these numbers";
    $("nA1").innerHTML = A ? fmt(A.tokens_in) : e ? fmt(e.A_prompt_tokens) + "<small> prompt</small>" : "–";
    $("nA2").innerHTML = A ? `1 · ${A.seconds.toFixed(1)}<small> s</small>` : "1";
    $("nA3").innerHTML = A ? `${A.matches_rules}/${A.runs}` : "–";
    lanes.A.target = A && S.run ? A.tokens_in / 900 : 0;
    const bl = live.mode === "agent";
    $("sB").textContent = bl ? (live.done ? "this run" : "live: this run") : B ? `last comparison ${when}: ${B.llm_calls.toFixed(1)} calls` : "press Ask the agent";
    const scripted = bl && S.scripted && !live.tin;
    if (scripted) $("sB").textContent = "scripted stand-in: no model, no tokens";
    $("nB1").innerHTML = scripted ? "–" : bl ? fmt(live.tin) : B ? fmt(B.tokens_in) : "–";
    $("nB2").innerHTML = bl ? `${live.turns} · ${((live.ms ?? (performance.now() - live.t0)) / 1000).toFixed(1)}<small> s</small>` : B ? `${B.llm_calls.toFixed(1)} · ${B.seconds.toFixed(1)}<small> s</small>` : "–";
    $("nB3").innerHTML = B ? `${B.matches_rules}/${B.runs}` : "–";
    lanes.B.target = bl ? live.tin / 900 : 0;
    const cl = live.mode === "workflow";
    $("sC").textContent = cl ? "this run" : C ? `last comparison ${when} · press Run without AI` : "press Run without AI";
    $("nC1").innerHTML = "0";
    $("nC2").innerHTML = cl && live.callMs != null ? `0 · ${live.callMs}<small> ms</small>` : C ? `0 · ${(C.seconds * 1000).toFixed(0)}<small> ms</small>` : "0";
    $("nC3").innerHTML = C ? `${C.matches_rules}/${C.runs}` : "–";
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
    if (S.live.mode === "agent" && !S.live.done) laneNumbers();
  }

  /* ---------------------------------------------------------------- runs */
  function chip(text, cls) {
    const t = $("trace"), last = t.lastElementChild;
    if (last && last.textContent === text) return;   // a confirmed call is reported again when it resumes
    const s = document.createElement("span"); s.textContent = text; if (cls) s.className = cls; t.appendChild(s);
  }
  function setBusy(b) {   // buttons stay usable: a new run replaces a slow one (the server releases its approval wait)
    $("run-agent").textContent = b && S.live.mode === "agent" ? "Asking… (press to restart)" : "Ask the agent";
  }

  async function start(mode) {
    if (!S.order) return;
    if (S.es) { S.es.close(); S.es = null; }
    if (S.view !== "inst") await setView("inst");
    unfocus(); card(null); $("outcome").classList.add("hidden"); $("trace").innerHTML = ""; $("detail").classList.add("hidden");
    S.live = { mode, turns: 0, tin: 0, tout: 0, t0: performance.now(), ms: null, eval: null, quote: null, done: false, callMs: null };
    setBusy(true);
    const r = await (await fetch("/api/run", { method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ patient: S.order.patient, mode }) })).json();
    S.run = r.run;
    laneNumbers();
    S.es = new EventSource(`/api/run/${r.run}/events`);
    S.es.onmessage = (m) => onEvent(JSON.parse(m.data));
  }

  function onEvent(e) {
    const L = S.live;
    if (e.kind === "model") {
      L.turns = e.turn; L.tin += e.tokens_in; L.tout += e.tokens_out;
      chip(e.tokens_in ? `turn ${e.turn} · ${fmt(e.tokens_in)} tok` : `turn ${e.turn}`);
      if (e.text && $("agent-text")) { $("agent-text").classList.remove("hidden"); $("agent-text").textContent = e.text; }
      laneNumbers();
    } else if (e.kind === "tool_call") {
      if (L.mode === "agent" && !(e.name === "journey_record" && [...$("trace").children].some((c) => c.textContent === "journey_record"))) chip(e.name, "tool");
    } else if (e.kind === "tool_result") {
      if (e.name === "context_applicable_rules") lightCandidates(e.payload);
      if (e.name === "rules_evaluate") {
        L.eval = e.payload; card(e.payload); lightAnswer(e.payload);
        if (L.mode === "workflow") { L.callMs = e.call_ms; lanes.C.flash = 1.6; chip(`rules_evaluate · ${e.call_ms} ms · no model`, "tool"); }
      }
      if (e.name === "policy_search" && e.payload && e.payload.quote) {
        L.quote = { quote: e.payload.quote, citation: e.payload.citation, page: (e.payload.locator || {}).page };
        if (L.eval) { card(L.eval); }
        const src = nodes.find((n) => n.kind === "Source" && n.doc === e.payload.citation);
        if (src) { src.on = true; paint(); }
      }
      if (e.name === "journey_get" && e.payload && e.payload.status === "found") chip("patient record");
    } else if (e.kind === "confirm_request") {
      if ($("ok")) { $("ok").disabled = false; $("nok").disabled = false; }
      L.ms = performance.now() - L.t0; L.done = true; laneNumbers();
    } else if (e.kind === "confirm") {
      chip(e.approved ? "approved by staff" : "rejected by staff", "tool");
      setTimeout(loadLog, 800);
    } else if (e.kind === "abandoned") {
      chip("not approved: left waiting", "tool");
    } else if (e.kind === "done") {
      if (S.es) { S.es.close(); S.es = null; }
      if (L.ms == null) L.ms = performance.now() - L.t0;
      L.done = true; setBusy(false); laneNumbers(); loadLog();
      if ($("ok") && L.mode === "workflow") { $("ok").remove(); $("nok").remove(); }
    } else if (e.kind === "error") {
      if (S.es) { S.es.close(); S.es = null; }
      chip("error: " + e.message, "tool"); setBusy(false);
    }
  }
  async function confirm(ok) {
    if (!S.run) return;
    $("ok").disabled = true; $("nok").disabled = true;
    await fetch(`/api/run/${S.run}/confirm`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ approved: ok }) });
  }
  async function loadLog() {
    const rows = await (await fetch("/api/log")).json();
    $("log").innerHTML = "Decision log · " + (rows.slice(-4).reverse().map((r) =>
      r.decision === "rejected" ? `<i>rejected</i> ${esc(r.order)} · ${esc(r.reviewer)} · no outcome logged`
        : `<b>approved</b> ${esc(r.order)} · ${esc(r.outcome)} · ${esc(r.ruleRef || r.rule_ref)} · ${esc(r.approved_by || r.reviewer || "")}`).join("   |   ") || "empty");
  }

  /* ---------------------------------------------------------------- orders, views, start-up */
  function pick(o) {
    S.order = o;
    document.querySelectorAll(".chip").forEach((c) => c.setAttribute("aria-pressed", String(c.dataset.p === o.patient)));
    $("q-title").textContent = `${o.description.split(" (")[0]} · ${o.code}`;
    const f = o.facts, labs = ["hgb_g_dl", "hct_pct", "egfr", "creatinine_date"].filter((k) => f[k] != null).map((k) => `${k} ${f[k]}`).join(" · ");
    $("q-facts").innerHTML = `${esc(o.order_id)} · ${esc(o.display_name)}<br>${esc(o.plan)} · ${esc(o.state)} · ${esc(o.date_of_service)}<br>` +
      `${esc((f.diagnosis_codes || []).join(", "))}<br>${esc(labs || "no lab results on file")}`;
    $("outcome").classList.add("hidden"); card(null); unfocus(); $("trace").innerHTML = "";
    S.live = { mode: null, turns: 0, tin: 0, tout: 0, t0: 0, ms: null, done: false };
    S.run = null;
    laneNumbers();
  }
  async function setView(v) {
    S.view = v;
    $("view-inst").setAttribute("aria-selected", String(v === "inst"));
    $("view-onto").setAttribute("aria-selected", String(v === "onto"));
    if (v === "onto" && !S.onto) S.onto = await (await fetch("/api/ontology")).json();
    setGraph(v === "inst" ? S.inst : S.onto, v);
    if (v === "inst" && S.live.eval) lightAnswer(S.live.eval);
  }
  $("view-inst").onclick = () => setView("inst");
  $("view-onto").onclick = () => setView("onto");
  $("run-agent").onclick = () => start("agent");
  $("run-flow").onclick = () => start("workflow");

  (async function init() {
    const get = (u, fb) => fetch(u).then((r) => (r.ok ? r.json() : fb)).catch(() => fb);
    const [st, orders, g, cmp] = await Promise.all([get("/api/status", {}), get("/api/orders", []), get("/api/graph", { nodes: [], edges: [] }), get("/api/compare", {})]);
    S.scripted = !!st.scripted;
    $("status").innerHTML = `release <b>${esc(st.release)}</b> · ${st.rules} rules · ${st.sources} sources<br>model <b>${esc(st.model)}</b>`;
    S.orders = orders; S.inst = g; S.compare = cmp || {};
    $("orders").innerHTML = orders.map((o) => `<button type="button" class="chip" data-p="${esc(o.patient)}" aria-pressed="false"><b>${esc(o.patient)}</b><span>${esc(o.code)} · ${esc(o.plan === "original-medicare" ? "Medicare" : o.plan)} · ${esc(o.state)}</span></button>`).join("");
    document.querySelectorAll(".chip").forEach((c) => (c.onclick = () => pick(orders.find((o) => o.patient === c.dataset.p))));
    buildLanes();
    setGraph(g, "inst");
    pick(orders[0]);
    loadLog();
  })();
})();
