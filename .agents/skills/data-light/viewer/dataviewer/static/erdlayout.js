// erdlayout.js -- bounded, relation-centric ERD auto-layout and relation routing.
// MIT — part of data-expert-framework viewer (copied into data-expert-framework-light)
//
// Algorithm ("layered-1"):
//   1. Validate: unknown edge endpoints -> `dangling_edge` warning (never silently dropped); every node is positioned.
//   2. Too large (nodes > maxNodes or edges > maxEdges) -> fast "simple" layout (depth columns, id order, per group).
//   3. Split by group (sorted by name), then connected components (larger first, ties by smallest id).
//      Edges across groups do not affect layering (they still count in metrics).
//   4. Layering: SCCs condensed with an iterative Tarjan (cycle members share one layer, self loops ignored);
//      layer(child) = max(layer(parent)) + 1, so parents are on the left.
//   5. Ordering: id order, then barycenter sweeps (down/up) scored by crossings of straight segments in
//      (layer, index) space; at most maxIter iterations, stop after `patience` non-improving ones, keep the best.
//   6. Peripheral nodes (code tables, hubs) are not layered and do not pull other nodes: they go to one compact
//      column at the FAR LEFT of their component (they are parents in FK terms), ordered by and nudged toward
//      the mean y of their neighbours, or compact from the top when that crosses less (both are tried).
//      (A band under the component was tried first: its lines crossed the main layout more.)
//   7. Coordinates: column x from cumulative max widths + gapX; y stacked with real heights + gapY, each node
//      nudged down toward the mean centre of its already placed left neighbours (never overlapping).
//      Components are placed side by side in wrapped rows, groups side by side (groupGap).
//   8. Pinned nodes keep their exact position; unpinned boxes are pushed below pinned ones. Overlapping pins
//      are reported as `pin_conflict` and left alone.
//   9. Guard: if the result has more crossings than the old algorithm (`baseline`) and nothing is pinned,
//      the baseline positions are returned instead (metrics.keptBaseline = true).
// Budget: the clock is checked in every loop. Elapsed > budget.ms -> simple layout, reason "time_budget";
// prep work units > maxPrepWork -> simple layout, reason "work_budget" (no retry). Elapsed > targetMs only
// stops ordering early (keeps the best ordering, truncated = true).
// routes(): up to routeCandidates orthogonal candidates per edge (direct Z, Z through a free gap, channel
// detour around the obstacles, around the side), scored by box hits (uniform grid index), overlap with
// chosen segments (bounded by maxRouteChecks), then length.
(function (root) {
  "use strict";
  const ALGO_VERSION = "layered-1";
  const DEFAULT_BUDGET = {targetMs: 250, ms: 1000, maxNodes: 200, maxEdges: 500, maxIter: 8, patience: 2,
    routeCandidates: 4, maxRouteChecks: 200000, maxPrepWork: 2000000};
  const LANE = 9, CELL = 128;

  const cmp = (a, b) => (a < b ? -1 : a > b ? 1 : 0);
  const num = (v, d) => (typeof v === "number" && isFinite(v) ? v : d);
  const defaultClock = () => (typeof performance !== "undefined" && performance.now ? performance.now() : Date.now());
  const hits = (a, b, m) => a.x < b.x + b.w + m && b.x < a.x + a.w + m && a.y < b.y + b.h + m && b.y < a.y + a.h + m;
  function Abort(reason) { this.reason = reason; }

  function mkGuard(budget, clock) {
    const t0 = clock();
    let work = 0, nextCheck = 1024;
    const g = {
      elapsed: () => clock() - t0,
      check() { if (clock() - t0 > budget.ms) throw new Abort("time_budget"); },
      work(n) {
        work += n;
        if (work > budget.maxPrepWork) throw new Abort("work_budget");
        if (work >= nextCheck) { nextCheck = work + 1024; g.check(); }
      },
    };
    return g;
  }
  const NOGUARD = {elapsed: () => 0, check() {}, work() {}};

  /* ------------------------------------------------------------------ input */
  function prepare(input, guard, maxPinPairs) {
    input = input || {};
    const o = input.origin || {};
    const P = {gapX: num(input.gapX, 70), gapY: num(input.gapY, 36), groupGap: num(input.groupGap, 90),
      origin: {x: num(o.x, 40), y: num(o.y, 40)}, nodes: [], byId: new Map(), edges: [], warnings: []};
    for (const n of input.nodes || []) {
      guard.work(1);
      if (!n || n.id === undefined || n.id === null) continue;
      const id = String(n.id);
      if (P.byId.has(id)) continue;
      const pin = n.pinned && isFinite(n.pinned.x) && isFinite(n.pinned.y) ? {x: +n.pinned.x, y: +n.pinned.y} : null;
      const node = {id, w: Math.max(1, num(n.w, 150)), h: Math.max(1, num(n.h, 40)),
        group: n.group === undefined || n.group === null ? "" : String(n.group), peripheral: !!n.peripheral, pinned: pin};
      P.byId.set(id, node); P.nodes.push(node);
    }
    P.nodes.sort((a, b) => cmp(a.id, b.id));
    for (const e of input.edges || []) {
      guard.work(1);
      if (!e) continue;
      const from = String(e.from), to = String(e.to);
      if (!P.byId.has(from) || !P.byId.has(to)) { P.warnings.push({kind: "dangling_edge", id: String(e.id)}); continue; }
      P.edges.push({id: String(e.id), from, to});
    }
    const pins = P.nodes.filter(n => n.pinned).map(n => ({id: n.id, x: n.pinned.x, y: n.pinned.y, w: n.w, h: n.h}));
    if (maxPinPairs !== undefined && pins.length * pins.length > maxPinPairs) { P.pins = pins; P.pinCheckSkipped = true; return P; }
    for (let i = 0; i < pins.length; i++) for (let j = i + 1; j < pins.length; j++) {
      guard.work(1);
      if (hits(pins[i], pins[j], 0)) P.warnings.push({kind: "pin_conflict", ids: [pins[i].id, pins[j].id]});
    }
    P.pins = pins;
    return P;
  }

  /* ---------------------------------------------- layering: iterative Tarjan + longest path */
  // parentsOf: Map id -> parent ids (same subset, no self loops). Tarjan emits an SCC after all of its
  // parents' SCCs, so the longest path from the roots can be computed on emission.
  function sccLayers(ids, parentsOf, guard) {
    const index = new Map(), low = new Map(), onStack = new Set(), stack = [], comp = new Map(), compLayer = [];
    const layer = new Map();
    let counter = 0;
    const visit = (v, work) => { index.set(v, counter); low.set(v, counter++); stack.push(v); onStack.add(v); work.push([v, 0]); };
    for (const s of ids) {
      if (index.has(s)) continue;
      const work = [];
      visit(s, work);
      while (work.length) {
        guard.work(1);
        const top = work[work.length - 1], v = top[0], ps = parentsOf.get(v);
        if (top[1] < ps.length) {
          const w = ps[top[1]++];
          if (!index.has(w)) visit(w, work);
          else if (onStack.has(w)) low.set(v, Math.min(low.get(v), index.get(w)));
          continue;
        }
        work.pop();
        if (work.length) { const u = work[work.length - 1][0]; low.set(u, Math.min(low.get(u), low.get(v))); }
        if (low.get(v) !== index.get(v)) continue;
        const members = [], c = compLayer.length;
        let w;
        do { w = stack.pop(); onStack.delete(w); comp.set(w, c); members.push(w); } while (w !== v);
        let L = 0;
        for (const m of members) for (const p of parentsOf.get(m)) { const pc = comp.get(p); if (pc !== c) L = Math.max(L, compLayer[pc] + 1); }
        compLayer.push(L);
        for (const m of members) layer.set(m, L);
      }
    }
    return layer;
  }

  /* -------------------------------------------------------------- ordering */
  function segCross(a, b, c, d) {   // proper intersection of segments ab and cd
    const o = (p, q, r) => Math.sign((q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0]));
    const o1 = o(a, b, c), o2 = o(a, b, d), o3 = o(c, d, a), o4 = o(c, d, b);
    return o1 * o2 < 0 && o3 * o4 < 0;
  }
  function crossingsOf(segs, guard) {   // segs: [[u, v, p1, p2]]
    let n = 0, k = 0;
    for (let i = 0; i < segs.length; i++) {
      const s = segs[i];
      for (let j = i + 1; j < segs.length; j++) {
        if ((++k & 4095) === 0) guard.check();
        const t = segs[j];
        if (s[0] === t[0] || s[0] === t[1] || s[1] === t[0] || s[1] === t[1]) continue;
        if (segCross(s[2], s[3], t[2], t[3])) n++;
      }
    }
    return n;
  }
  // Ordering is scored on real geometry: `geom(layers)` gives local box centres (same stacking as placePlan),
  // and crossings are counted between straight centre-to-centre segments -- the metric reported to callers.
  function orderLayers(layers, layerOf, edges, nbr, geom, B, guard, stats) {
    if (!edges.length || layers.length < 2) return;
    let c = geom(layers);
    const count = () => crossingsOf(edges.map(([u, v]) => [u, v, c.get(u), c.get(v)]), guard);
    let bestC = count(), best = layers.map(L => L.slice()), stale = 0;
    const sweep = (down) => {
      for (let i = 0; i < layers.length; i++) {
        guard.check();
        const l = down ? i : layers.length - 1 - i, L = layers[l], key = new Map();
        for (const id of L) {
          let s = 0, k = 0;
          for (const q of nbr.get(id)) { const lq = layerOf.get(q); if (down ? lq < l : lq > l) { s += c.get(q)[1]; k++; } }
          key.set(id, k ? s / k : c.get(id)[1]);
        }
        L.sort((a, b) => key.get(a) - key.get(b) || cmp(a, b));
        c = geom(layers);
      }
    };
    while (stats.iterations < B.maxIter && bestC > 0) {
      guard.check();
      if (guard.elapsed() > B.targetMs) { stats.truncated = true; break; }
      for (const down of [true, false]) {
        sweep(down);
        const n = count();
        if (n < bestC) { bestC = n; best = layers.map(L => L.slice()); stale = 0; } else stale++;
      }
      stats.iterations++;
      if (stale >= 2 * B.patience) break;
    }
    best.forEach((L, i) => { layers[i] = L; });
  }

  /* ------------------------------------------------- plans (local x, order) */
  // plan = {cols: [{x, w, ids}], side: peripheral ids, sideW, w, nbr: Map id -> neighbours (for alignment) | null}
  function makePlan(P, ids, simple, B, guard, stats) {
    const set = new Set(ids);
    let core = simple ? ids : ids.filter(id => !P.byId.get(id).peripheral);
    if (!core.length) core = ids;
    const coreSet = new Set(core), periph = ids.filter(id => !coreSet.has(id));
    const parentsOf = new Map(core.map(id => [id, []])), nbr = new Map(ids.map(id => [id, []])), edges = [];
    for (const e of P.edges) {
      guard.work(1);
      if (e.from === e.to || !set.has(e.from) || !set.has(e.to)) continue;
      nbr.get(e.from).push(e.to); nbr.get(e.to).push(e.from);
      if (coreSet.has(e.from) && coreSet.has(e.to)) { parentsOf.get(e.from).push(e.to); edges.push([e.from, e.to]); }
    }
    for (const l of parentsOf.values()) l.sort(cmp);
    const layerOf = sccLayers(core, parentsOf, guard), layers = [];
    for (const id of core) { const l = layerOf.get(id); while (layers.length <= l) layers.push([]); layers[l].push(id); }
    if (!simple) {
      const coreNbr = new Map(core.map(id => [id, nbr.get(id).filter(q => coreSet.has(q))]));
      const geom = (ls) => {
        const c = new Map();
        let x = 0;
        for (const L of ls) {
          let w = 0, cur = 0;
          for (const id of L) {
            const n = P.byId.get(id), l = layerOf.get(id);
            let y = cur, s = 0, k = 0;
            for (const q of coreNbr.get(id)) { const pq = c.get(q); if (pq && layerOf.get(q) < l) { s += pq[1]; k++; } }
            if (k) y = Math.max(cur, Math.round(s / k - n.h / 2));
            c.set(id, [x + n.w / 2, y + n.h / 2]); cur = y + n.h + P.gapY; w = Math.max(w, n.w);
          }
          x += w + P.gapX;
        }
        return c;
      };
      orderLayers(layers, layerOf, edges, coreNbr, geom, B, guard, stats);
    }
    const free = (L) => L.filter(id => !P.byId.get(id).pinned);
    const side = free(periph), sideW = side.length ? Math.max(...side.map(id => P.byId.get(id).w)) : 0, cols = [];
    let x = side.length ? sideW + P.gapX : 0;
    for (const L of layers) {
      guard.check();
      const ids = free(L);
      if (!ids.length) continue;
      const w = Math.max(...ids.map(id => P.byId.get(id).w));
      cols.push({x, w, ids}); x += w + P.gapX;
    }
    const all = [];
    if (side.length) for (const e of P.edges) if (e.from !== e.to && set.has(e.from) && set.has(e.to)) all.push([e.from, e.to]);
    return {cols, side, sideW, w: Math.max(0, x - P.gapX), nbr: simple ? null : nbr, edges: all};
  }

  /* --------------------------------------- absolute placement with pin avoidance */
  function avoid(r, near, gap) {
    for (let k = 0; k <= near.length; k++) {
      let moved = false;
      for (const p of near) if (hits(r, p, 8)) { r.y = p.y + p.h + gap; moved = true; }
      if (!moved) break;
    }
    return r.y;
  }
  // Stack one column top-down: each box goes to max(cursor, aligned y), then below any pinned box it would hit.
  function stack(P, ids, ax, w, oy, pos, want) {
    const near = P.pins.filter(p => p.x < ax + w + 8 && ax < p.x + p.w + 8);
    let cur = oy, bottom = oy;
    for (const id of ids) {
      const n = P.byId.get(id), c = want(id);
      const y = avoid({x: ax, y: c === null ? cur : Math.max(cur, Math.round(c - n.h / 2)), w: n.w, h: n.h}, near, P.gapY);
      pos[id] = {x: ax, y}; cur = y + n.h + P.gapY; bottom = Math.max(bottom, y + n.h);
    }
    return bottom;
  }
  function placePlan(P, plan, ox, oy, pos, guard) {
    // mean centre y of already placed (unpinned) neighbours, optionally only those left of x
    const meanY = (id, leftOf) => {
      if (!plan.nbr) return null;
      let s = 0, k = 0;
      for (const q of plan.nbr.get(id)) { const pq = pos[q], nq = P.byId.get(q); if (pq && !nq.pinned && pq.x < leftOf) { s += pq.y + nq.h / 2; k++; } }
      return k ? s / k : null;
    };
    let bottom = oy;
    for (const col of plan.cols) {
      guard.check();
      const ax = ox + col.x;
      bottom = Math.max(bottom, stack(P, col.ids, ax, col.w, oy, pos, id => meanY(id, ax)));
    }
    if (plan.side.length) {   // peripheral column at the far left, ordered by and aligned to its neighbours
      guard.check();
      const key = new Map(plan.side.map(id => [id, meanY(id, Infinity)]));
      const k = (id) => (key.get(id) === null ? Infinity : key.get(id));
      const ids = plan.side.slice().sort((a, b) => k(a) - k(b) || cmp(a, b));
      // two candidates: aligned to the neighbours or compact from the top -- keep the one with fewer crossings
      const tries = [id => key.get(id), () => null].map(want => {
        const p = {}, b = stack(P, ids, ox, plan.sideW, oy, p, want), all = Object.assign({}, pos, p);
        const c = plan.edges.length ? crossingsOf(plan.edges.filter(([u, v]) => all[u] && all[v]).map(([u, v]) => [u, v,
          [all[u].x + P.byId.get(u).w / 2, all[u].y + P.byId.get(u).h / 2], [all[v].x + P.byId.get(v).w / 2, all[v].y + P.byId.get(v).h / 2]]), guard) : 0;
        return {p, b, c};
      });
      const t = tries[1].c < tries[0].c ? tries[1] : tries[0];
      Object.assign(pos, t.p); bottom = Math.max(bottom, t.b);
    }
    return bottom;
  }

  function components(P, ids, guard) {
    const set = new Set(ids), adj = new Map(ids.map(id => [id, []]));
    for (const e of P.edges) { guard.work(1); if (e.from !== e.to && set.has(e.from) && set.has(e.to)) { adj.get(e.from).push(e.to); adj.get(e.to).push(e.from); } }
    const seen = new Set(), out = [];
    for (const s of ids) {
      if (seen.has(s)) continue;
      const c = [], q = [s];
      seen.add(s);
      while (q.length) { guard.work(1); const v = q.pop(); c.push(v); for (const w of adj.get(v)) if (!seen.has(w)) { seen.add(w); q.push(w); } }
      c.sort(cmp); out.push(c);
    }
    return out.sort((a, b) => b.length - a.length || cmp(a[0], b[0]));
  }

  function arrange(P, simple, B, guard, stats) {
    const pos = {}, groups = new Map();
    for (const n of P.nodes) { if (!groups.has(n.group)) groups.set(n.group, []); groups.get(n.group).push(n.id); }
    let gx = P.origin.x;
    for (const g of [...groups.keys()].sort(cmp)) {
      const ids = groups.get(g);
      const plans = (simple ? [ids] : components(P, ids, guard)).map(c => makePlan(P, c, simple, B, guard, stats));
      const W = Math.max(1800, ...plans.map(p => p.w));
      let rowX = gx, rowTop = P.origin.y, rowBottom = rowTop, right = gx;
      for (const plan of plans) {
        if (!plan.cols.length && !plan.side.length) continue;
        if (rowX > gx && rowX + plan.w - gx > W) { rowX = gx; rowTop = rowBottom + P.gapY * 2; }
        rowBottom = Math.max(rowBottom, placePlan(P, plan, rowX, rowTop, pos, guard));
        right = Math.max(right, rowX + plan.w); rowX += plan.w + P.gapX;
      }
      if (right > gx) gx = right + P.groupGap;
    }
    for (const n of P.nodes) if (n.pinned) pos[n.id] = {x: n.pinned.x, y: n.pinned.y};
    return pos;
  }

  /* --------------------------------------------------------------- metrics */
  function countCrossings(positions, nodes, edges) {
    const by = new Map((nodes || []).map(n => [String(n.id), n])), segs = [];
    for (const e of edges || []) {
      const a = String(e.from), b = String(e.to), pa = positions[a], pb = positions[b], na = by.get(a), nb = by.get(b);
      if (a === b || !pa || !pb || !na || !nb) continue;
      segs.push([a, b, [pa.x + na.w / 2, pa.y + na.h / 2], [pb.x + nb.w / 2, pb.y + nb.h / 2]]);
    }
    return crossingsOf(segs, NOGUARD);
  }
  function countOverlaps(positions, nodes) {
    const r = [];
    for (const n of nodes || []) { const p = positions[n.id]; if (p) r.push({x: p.x, y: p.y, w: n.w, h: n.h}); }
    r.sort((a, b) => a.x - b.x);
    let c = 0;
    for (let i = 0; i < r.length; i++) for (let j = i + 1; j < r.length && r[j].x < r[i].x + r[i].w; j++)
      if (r[j].y < r[i].y + r[i].h && r[i].y < r[j].y + r[j].h) c++;
    return c;
  }

  /* ------------------------------------- baseline: the viewer's previous autoPlace */
  function baseline(input) {
    const P = prepare(input, NOGUARD), parents = new Map(P.nodes.map(n => [n.id, []])), rank = new Map(), pos = {};
    for (const e of P.edges) if (e.from !== e.to) parents.get(e.from).push(e.to);
    const depth = (k, seen) => {
      if (rank.has(k)) return rank.get(k);
      if (seen.has(k)) return 0;
      seen.add(k);
      const d = Math.max(-1, ...parents.get(k).map(p => depth(p, seen))) + 1;
      rank.set(k, d); return d;
    };
    const groups = new Map();
    for (const n of P.nodes) { if (!groups.has(n.group)) groups.set(n.group, []); groups.get(n.group).push(n); }
    let gx = P.origin.x;
    for (const g of [...groups.keys()].sort((a, b) => cmp(a, b))) {
      const cols = new Map();
      for (const n of groups.get(g)) { const d = depth(n.id, new Set()); if (!cols.has(d)) cols.set(d, []); cols.get(d).push(n); }
      let x = gx, right = gx;
      for (const d of [...cols.keys()].sort((a, b) => a - b)) {
        let y = P.origin.y, w = 0;
        for (const n of cols.get(d).sort((a, b) => cmp(a.id, b.id))) { pos[n.id] = {x, y}; y += n.h + P.gapY; w = Math.max(w, n.w); }
        x += w + P.gapX; right = x;
      }
      gx = right + P.groupGap;
    }
    return {positions: pos};
  }

  /* ---------------------------------------------------------------- layout */
  // Cheap fallback, O(n log n) and no layering: rows of boxes by group then id, below any pinned boxes.
  // Used when the graph is too large or a budget ran out, so the fallback itself can never overrun.
  function grid(P) {
    const pos = {}, free = P.nodes.filter(n => !n.pinned);
    free.sort((a, b) => cmp(a.group, b.group) || cmp(a.id, b.id));
    let area = 0; for (const n of free) area += (n.w + P.gapX) * (n.h + P.gapY);
    const W = Math.max(1800, Math.sqrt(area) * 1.3);
    let top = P.origin.y;              // only pins that share the grid's columns push it down
    for (const p of P.pins) if (p.x < P.origin.x + W && p.x + p.w > P.origin.x && p.y + p.h > P.origin.y) top = Math.max(top, p.y + p.h + P.gapY);
    let x = P.origin.x, y = top, rowH = 0, group = null;
    for (const n of free) {
      const newGroup = group !== null && n.group !== group;
      if (newGroup || (x > P.origin.x && x + n.w - P.origin.x > W)) { x = P.origin.x; y += rowH + (newGroup ? P.groupGap : P.gapY); rowH = 0; }
      group = n.group; pos[n.id] = {x, y}; x += n.w + P.gapX; rowH = Math.max(rowH, n.h);
    }
    for (const n of P.nodes) if (n.pinned) pos[n.id] = {x: n.pinned.x, y: n.pinned.y};
    return pos;
  }

  /* ---------------------------------------------------------------- layout */
  function layout(input, budget, clock) {
    const B = Object.assign({}, DEFAULT_BUDGET, budget || {});
    clock = clock || defaultClock;
    const guard = mkGuard(B, clock), stats = {iterations: 0, truncated: false};
    const nN = ((input && input.nodes) || []).length, nE = ((input && input.edges) || []).length;
    let P = null, mode = "detailed", reason = null, positions;
    try {
      if (nN > B.maxNodes || nE > B.maxEdges) throw new Abort("too_large");     // decided before any per-pair work
      P = prepare(input, guard);
      guard.check();
      positions = arrange(P, false, B, guard, stats);
      guard.check();
    } catch (e) {
      if (!(e instanceof Abort)) throw e;
      mode = "simple"; reason = e.reason; stats.truncated = true;
      P = prepare(input, NOGUARD, 90000);            // linear except the pin-pair check, which is capped
      positions = grid(P);
    }
    // Quality metrics only for detailed layouts and only while time is left (they are reports, not the layout).
    let crossings = -1, base = -1, keptBaseline = false, overlaps, own = -1;
    if (mode === "detailed") {
      try {
        guard.check(); crossings = countCrossings(positions, P.nodes, P.edges);
        own = crossings;
        guard.check(); const bl = baseline(input); base = countCrossings(bl.positions, P.nodes, P.edges);
        if (crossings > base && !P.pins.length) { positions = bl.positions; crossings = base; keptBaseline = true; }
      } catch (e) { if (!(e instanceof Abort)) throw e; stats.truncated = true; }
      overlaps = countOverlaps(positions, P.nodes);
    } else {
      overlaps = P.pinCheckSkipped ? -1 : P.warnings.filter(w => w.kind === "pin_conflict").length;   // the grid itself never overlaps
    }
    const ownCrossings = own;
    return {positions, mode, reason, algo: ALGO_VERSION, warnings: P.warnings,
      metrics: {nodes: P.nodes.length, edges: P.edges.length, crossings, baselineCrossings: base, overlaps,
        ms: Math.round(guard.elapsed() * 10) / 10, iterations: stats.iterations, routeChecks: 0, truncated: stats.truncated, keptBaseline, ownCrossings}};
  }

  /* ---------------------------------------------------------------- routes */
  function routes(input, budget, clock) {
    const B = Object.assign({}, DEFAULT_BUDGET, budget || {});
    clock = clock || defaultClock;
    const t0 = clock(), byId = new Map(), grid = new Map(), boxes = [], warnings = [];
    for (const n of (input && input.nodes) || []) {
      const b = {id: String(n.id), x: +n.x, y: +n.y, w: +n.w, h: +n.h, i: boxes.length};
      byId.set(b.id, b); boxes.push(b);
      for (let cx = Math.floor(b.x / CELL); cx <= Math.floor((b.x + b.w) / CELL); cx++)
        for (let cy = Math.floor(b.y / CELL); cy <= Math.floor((b.y + b.h) / CELL); cy++) {
          const k = cx + "," + cy; if (!grid.has(k)) grid.set(k, []); grid.get(k).push(b);
        }
    }
    let checks = 0, truncated = false, stamp = 0;
    const mark = new Int32Array(boxes.length);
    const query = (x1, y1, x2, y2, fn) => {   // boxes whose cells touch the rect, each once
      stamp++;
      for (let cx = Math.floor(x1 / CELL); cx <= Math.floor(x2 / CELL); cx++)
        for (let cy = Math.floor(y1 / CELL); cy <= Math.floor(y2 / CELL); cy++)
          for (const b of grid.get(cx + "," + cy) || []) if (mark[b.i] !== stamp) { mark[b.i] = stamp; fn(b); }
    };
    const segHits = (p, q, skip, list) => {
      const x1 = Math.min(p[0], q[0]), x2 = Math.max(p[0], q[0]), y1 = Math.min(p[1], q[1]), y2 = Math.max(p[1], q[1]);
      let n = 0;
      query(x1, y1, x2, y2, b => {
        checks++;
        if (skip.has(b.id)) return;
        if (x2 > b.x + 1 && x1 < b.x + b.w - 1 && y2 > b.y + 1 && y1 < b.y + b.h - 1) { n++; if (list) list.push(b); }
      });
      return n;
    };
    const boxHitsOf = (pts, skip, list) => { let n = 0; for (let i = 1; i < pts.length; i++) n += segHits(pts[i - 1], pts[i], skip, list); return n; };
    const chosen = [];
    const overlapOf = (pts) => {
      let n = 0;
      for (let i = 1; i < pts.length; i++) {
        const p = pts[i - 1], q = pts[i], h = p[1] === q[1], c = h ? p[1] : p[0];
        const lo = h ? Math.min(p[0], q[0]) : Math.min(p[1], q[1]), hi = h ? Math.max(p[0], q[0]) : Math.max(p[1], q[1]);
        for (const s of chosen) { checks++; if (s.h === h && Math.abs(s.c - c) < 4 && Math.min(hi, s.hi) - Math.max(lo, s.lo) > 0) n++; }
      }
      return n;
    };
    const lengthOf = (pts) => { let L = 0; for (let i = 1; i < pts.length; i++) L += Math.abs(pts[i][0] - pts[i - 1][0]) + Math.abs(pts[i][1] - pts[i - 1][1]); return L; };
    const clean = (pts) => pts.filter((p, i) => i === 0 || p[0] !== pts[i - 1][0] || p[1] !== pts[i - 1][1]);
    const pairLane = new Map(), paths = {};
    let boxHits = 0, directHits = 0;
    for (const e of (input && input.edges) || []) {
      const a = byId.get(String(e.from)), b = byId.get(String(e.to)), eid = String(e.id);
      if (!a || !b) { warnings.push({kind: "dangling_edge", id: eid}); continue; }
      if (checks > B.maxRouteChecks) truncated = true;
      const pk = a.id < b.id ? a.id + "|" + b.id : b.id + "|" + a.id, lane = pairLane.get(pk) || 0;
      pairLane.set(pk, lane + 1);
      const ya = num(e.ya, a.y + a.h / 2), yb = num(e.yb, b.y + b.h / 2), skip = new Set([a.id, b.id]);
      if (a === b) {
        const x = a.x + a.w, d = 24 + lane * LANE, top = a.y - 14 - lane * LANE;
        paths[eid] = {points: [[x, ya], [x + d, ya], [x + d, top], [x - a.w / 2, top], [x - a.w / 2, a.y]], kind: "self", lane};
        continue;
      }
      const cands = [];
      const overlapX = a.x < b.x + b.w + 30 && b.x < a.x + a.w + 30;
      const aroundR = () => { const X = Math.max(a.x + a.w, b.x + b.w) + 26 + lane * LANE; return {points: [[a.x + a.w, ya], [X, ya], [X, yb], [b.x + b.w, yb]], kind: "around"}; };
      const aroundL = () => { const X = Math.min(a.x, b.x) - 26 - lane * LANE; return {points: [[a.x, ya], [X, ya], [X, yb], [b.x, yb]], kind: "around"}; };
      let xa, da, xb, db;
      if (overlapX) { cands.push(aroundR(), aroundL()); xa = a.x + a.w; da = 1; xb = b.x + b.w; db = 1; } else {
        if (a.x + a.w / 2 < b.x + b.w / 2) { xa = a.x + a.w; da = 1; xb = b.x; db = -1; } else { xa = a.x; da = -1; xb = b.x + b.w; db = 1; }
        const mx = Math.round((xa + xb) / 2) + lane * LANE;
        cands.push({points: [[xa, ya], [mx, ya], [mx, yb], [xb, yb]], kind: "direct"});
        if (!truncated) {   // Z through the free vertical gap nearest the middle
          const lo = Math.min(xa, xb) + 8, hi = Math.max(xa, xb) - 8, y1 = Math.min(ya, yb), y2 = Math.max(ya, yb), iv = [];
          query(lo, y1, hi, y2, bx => { checks++; if (!skip.has(bx.id) && bx.y < y2 && y1 < bx.y + bx.h) iv.push([bx.x - 6, bx.x + bx.w + 6]); });
          iv.sort((p, q) => p[0] - q[0]);
          let cur = lo, bestX = null;
          const consider = (s, t) => { if (t - s >= 12) { const x = Math.min(Math.max(mx, s + 6), t - 6); if (bestX === null || Math.abs(x - mx) < Math.abs(bestX - mx)) bestX = x; } };
          for (const [s, t] of iv) { consider(cur, Math.min(s, hi)); cur = Math.max(cur, t); }
          consider(cur, hi);
          if (bestX !== null && bestX !== mx) cands.push({points: [[xa, ya], [bestX, ya], [bestX, yb], [xb, yb]], kind: "detour"});
        }
      }
      if (!truncated) {   // channel above/below the boxes the first candidate would cut through
        const list = [];
        boxHitsOf(cands[0].points, skip, list);
        if (list.length) {
          const top = Math.min(...list.map(q => q.y)) - 16 - lane * LANE, bot = Math.max(...list.map(q => q.y + q.h)) + 16 + lane * LANE;
          const mid = (ya + yb) / 2, yc = Math.abs(top - mid) <= Math.abs(bot - mid) ? top : bot, x1 = xa + da * 14, x2 = xb + db * 14;
          cands.push({points: [[xa, ya], [x1, ya], [x1, yc], [x2, yc], [x2, yb], [xb, yb]], kind: "detour"});
        }
        if (!overlapX) cands.push(aroundR());
      }
      const first = cands[0];
      directHits += boxHitsOf(first.points, skip);
      let pick = first, pickHits = null;
      if (!truncated) {
        let best = Infinity;
        for (const c of cands.slice(0, Math.max(1, B.routeCandidates))) {
          const h = boxHitsOf(c.points, skip), s = h * 1e6 + overlapOf(c.points) * 1e3 + lengthOf(c.points);
          if (s < best) { best = s; pick = c; pickHits = h; }
          if (checks > B.maxRouteChecks) { truncated = true; break; }
        }
      }
      if (pickHits === null) pickHits = boxHitsOf(pick.points, skip);
      boxHits += pickHits;
      const pts = clean(pick.points);
      for (let i = 1; i < pts.length; i++) {
        const p = pts[i - 1], q = pts[i], h = p[1] === q[1];
        chosen.push({h, c: h ? p[1] : p[0], lo: h ? Math.min(p[0], q[0]) : Math.min(p[1], q[1]), hi: h ? Math.max(p[0], q[0]) : Math.max(p[1], q[1])});
      }
      paths[eid] = {points: pts, kind: pick.kind, lane};
    }
    return {paths, warnings, metrics: {boxHits, directHits, checks, ms: Math.round((clock() - t0) * 10) / 10, truncated}};
  }

  function quick(input) { return {positions: grid(prepare(input, NOGUARD, 90000)), mode: "simple", reason: "quick"}; }   // first paint
  const ERDLayout = {ALGO_VERSION, DEFAULT_BUDGET, layout, baseline, routes, quick, countCrossings, countOverlaps};

  if (typeof module !== "undefined" && module.exports) module.exports = ERDLayout;
  else if (root) root.ERDLayout = ERDLayout;
  if (typeof importScripts === "function" && typeof window === "undefined" && root) {
    root.onmessage = function (ev) {
      const m = ev.data || {};
      try {
        const fn = m.op === "layout" ? layout : m.op === "routes" ? routes : null;
        if (!fn) throw new Error("unknown op: " + m.op);
        root.postMessage({jobId: m.jobId, op: m.op, result: fn(m.input, m.budget)});
      } catch (e) {
        root.postMessage({jobId: m.jobId, op: m.op, error: String((e && e.message) || e)});
      }
    };
  }
})(typeof self !== "undefined" ? self : typeof window !== "undefined" ? window : this);
