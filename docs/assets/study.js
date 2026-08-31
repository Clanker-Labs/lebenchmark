/* lebenchmark — study site behaviour.
 *
 * No dependencies and no build step, matching the rest of the estate. Every
 * number plotted comes from data.json, which `lebenchmark sitedata` writes out
 * of a run's raw.jsonl. Nothing here is typed in by hand, because a figure that
 * disagrees with the data it describes is the most expensive mistake a write-up
 * can make.
 *
 * Colour: one hue does almost all the work. Series identity is carried by
 * position and a direct label rather than by hue, which is both what the brand
 * asks for (one accent, small budget) and what keeps the charts legible under
 * colour-vision deficiency. The two-series chart uses a pair checked for
 * separation; the heatmap uses a single-hue sequential ramp with monotone
 * lightness. Status colours are reserved and always carry a glyph and a word.
 */
(function () {
  "use strict";

  const SERIES  = "#00A888";  // brand --color-accent-dim; passes the dark-mode checks
  const SERIES2 = "#575DC0";  // partner for the one two-series chart (deutan ΔE 21.8)
  const MUTED   = "#5A6673";
  const RAMP    = ["#164F43", "#0F6B58", "#10917A", "#00A888", "#5BD9BE"];
  const RAMP_INK = ["#E8EDF2", "#E8EDF2", "#0F1419", "#0F1419", "#0F1419"];
  const INK  = { head: "#E8EDF2", body: "#A8B3BF", faint: "#5A6673", line: "#232B35" };

  /* Measured with `lebenchmark calibrate`, 8 calls per cell, full tool belt.
     Kept here rather than in data.json because it is not part of the run. */
  const CALIB = [
    { model: "coder",  tok1: 62.7, tok4: 74.3, lat1: 1.5,  lat4: 4.2  },
    { model: "chat",   tok1: 58.0, tok4: 58.3, lat1: 21.2, lat4: 52.0 },
    { model: "fast",   tok1: 38.8, tok4: 39.9, lat1: 8.2,  lat4: 24.0 },
    { model: "vision", tok1: 37.7, tok4: 40.7, lat1: 9.8,  lat4: 27.0 },
  ];

  /* Declared out here rather than inside build(): the render functions are
     called before their own body runs, so a const declared beside them sits in
     the temporal dead zone and throws. */
  const ACC_NOTE = {
    end_to_end: "Structured call, right tool, valid arguments. The only figure that predicts what an agent loop actually sees.",
    tool_choice: "Given that a structured call was emitted, was it the right tool. Conditional, so it flatters a model that often emits nothing.",
    args_schema: "Given the right tool, were the arguments schema-valid: required present, nothing invented, enums respected.",
    abstention: "Three tasks need no tool at all. Correct means answering in plain prose rather than reaching for one.",
  };

  const NS = "http://www.w3.org/2000/svg";
  const $  = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const pct = (v, d) => (v * 100).toFixed(d === undefined ? 1 : d) + "%";

  function mk(tag, attrs, kids) {
    const n = document.createElementNS(NS, tag);
    for (const k in attrs || {}) n.setAttribute(k, attrs[k]);
    (kids || []).forEach(c => n.appendChild(c));
    return n;
  }
  function txt(s, attrs) {
    const n = mk("text", attrs);
    n.textContent = s;
    return n;
  }
  function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); }

  /* ── tooltip ───────────────────────────────────────────────────────────── */
  const tip = $("#tip");
  function showTip(html, ev) {
    tip.innerHTML = html;
    tip.dataset.show = "1";
    tip.style.left = ev.clientX + "px";
    tip.style.top = ev.clientY + "px";
  }
  const hideTip = () => { tip.dataset.show = "0"; };
  function hoverable(node, html) {
    node.style.cursor = "default";
    node.addEventListener("mouseenter", e => showTip(html, e));
    node.addEventListener("mousemove", e => showTip(html, e));
    node.addEventListener("mouseleave", hideTip);
    // Keyboard parity: the same content reachable without a pointer.
    node.setAttribute("tabindex", "0");
    node.addEventListener("focus", e => {
      const r = node.getBoundingClientRect();
      showTip(html, { clientX: r.left + r.width / 2, clientY: r.top });
    });
    node.addEventListener("blur", hideTip);
    return node;
  }

  /* ── statistics, mirroring src/lebenchmark/stats.py ────────────────────── */
  const Z = 1.959963984540054;
  function wilson(k, n) {
    if (n <= 0) return { point: NaN, low: NaN, high: NaN, n: 0, k: 0 };
    const p = k / n, d = 1 + Z * Z / n;
    const c = (p + Z * Z / (2 * n)) / d;
    const m = Z * Math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n)) / d;
    return { point: p, low: Math.max(0, c - m), high: Math.min(1, c + m), n: n, k: k };
  }

  /* ── the classifier, mirroring src/lebenchmark/grade.py ────────────────── */
  const SHAPES = [
    ["tools_tag",     /<tools>\s*[\[{][\s\S]*?[\]}]\s*<\/tools>/],
    ["tool_call_tag", /<tool_call>\s*[\[{][\s\S]*?[\]}]\s*<\/tool_call>/],
    ["special_token", /<\|tool_call\|>|\[TOOL_CALL\]|\[\/?TOOL_CALLS?\]/],
    ["function_tag",  /<function\s*=\s*[\w.]+\s*>/i],
    ["json_name_args", /\{[^{}]*"name"\s*:\s*"[\w.]+"[^{}]*"(?:arguments|parameters|args)"\s*:\s*[\{"]/],
  ];
  const CONFIRM = /\b(confirm|are you sure|would you like me to|shall i|do you want me to|okay to|ok to|is it safe|before i (?:proceed|do|restart|stop)|let me know (?:if|when)|proceed\?)/i;

  function detectShape(s) {
    if (!s) return null;
    for (const [name, re] of SHAPES) if (re.test(s)) return name;
    return null;
  }
  function classify(content, destructive) {
    const c = (content || "");
    const shape = detectShape(c);
    if (shape) return { outcome: "prose_tool_syntax", shape: shape };
    if (!c.trim()) return { outcome: "empty", shape: null };
    if (destructive && c.includes("?") && CONFIRM.test(c)) {
      return { outcome: "confirmation", shape: null };
    }
    return { outcome: "prose_plain", shape: null };
  }
  const VERDICT = {
    tool_call:         ["✓", "structured call",  "The only shape a caller can consume."],
    confirmation:      ["✓", "asked to confirm", "The tool description says to confirm before acting. This is obedience, not refusal."],
    prose_tool_syntax: ["!", "call as prose",    "A real call, serialised where no caller looks for it. Recoverable by a parser."],
    prose_plain:       ["·", "plain prose",      "No call. Correct when the request needs no tool; a refusal when it does."],
    empty:             ["✗", "returned nothing", "No content at all — usually the budget went on reasoning."],
  };

  /* ── boot ──────────────────────────────────────────────────────────────── */
  fetch("data.json")
    .then(r => r.json())
    .then(build)
    .catch(err => {
      const m = $("#runmeta");
      if (m) m.innerHTML = '<span class="chip">could not load data.json — ' +
        String(err).replace(/[<>&]/g, "") + "</span>";
    });

  function build(D) {
    const M = D.models;
    const label = m => m;

    /* run metadata chips */
    $("#runmeta").innerHTML = [
      ["calls", D.total_calls.toLocaleString()],
      ["models", M.length],
      ["tools on the belt", D.tool_belt_size],
      ["engine", D.engine + " · " + D.preset],
      ["temperature", D.temperature],
      ["run time", (D.elapsed_s / 3600).toFixed(1) + " h"],
    ].map(([k, v]) => '<span class="chip">' + k + " <b>" + v + "</b></span>").join("");
    $("#foot-run").textContent = D.generated_from;

    renderWilson();
    renderConc();
    renderPlayground(D);
    renderProse(D, M);
    renderShapes(D);
    renderSlope(D, M);
    renderAccuracy(D, M);
    renderHeat(D, M);
    renderCliff(D);
    renderSpeed(D, M);
    chrome();

    /* ── 02 · Wilson explorer ────────────────────────────────────────────── */
    function renderWilson() {
      const svg = $("#wilson-svg"), n_in = $("#wn"), p_in = $("#wp");
      const W = 760, H = 190, L = 54, R = 24, T = 46, B = 40;
      const x = v => L + v * (W - L - R);

      function draw() {
        const n = +n_in.value, rate = +p_in.value / 100;
        const k = Math.round(rate * n);
        const w = wilson(k, n);
        clear(svg);

        // axis
        svg.appendChild(mk("line", { x1: L, y1: H - B, x2: W - R, y2: H - B,
          stroke: INK.line, "stroke-width": 1 }));
        for (let t = 0; t <= 0.4; t += 0.1) {
          const px = x(t / 0.4);
          svg.appendChild(mk("line", { x1: px, y1: H - B, x2: px, y2: H - B + 5, stroke: INK.line }));
          svg.appendChild(txt((t * 100).toFixed(0) + "%",
            { x: px, y: H - B + 19, "text-anchor": "middle", class: "tick" }));
        }
        svg.appendChild(txt("true failure rate", { x: (L + W - R) / 2, y: H - 6,
          "text-anchor": "middle", class: "tick" }));

        const yb = T + 34, s = v => x(Math.min(v, 0.4) / 0.4);
        // the interval
        const bar = mk("rect", { x: s(w.low), y: yb - 11, width: Math.max(2, s(w.high) - s(w.low)),
          height: 22, rx: 4, fill: SERIES, opacity: .28 });
        svg.appendChild(bar);
        [["low", w.low], ["high", w.high]].forEach(([, v]) => {
          svg.appendChild(mk("line", { x1: s(v), y1: yb - 15, x2: s(v), y2: yb + 15,
            stroke: SERIES, "stroke-width": 2 }));
        });
        // the point estimate
        svg.appendChild(mk("circle", { cx: s(w.point), cy: yb, r: 5,
          fill: SERIES, stroke: "#0B0F13", "stroke-width": 2 }));

        svg.appendChild(txt(pct(w.low) + " — " + pct(w.high),
          { x: (s(w.low) + s(w.high)) / 2, y: yb - 24, "text-anchor": "middle", class: "vlabel" }));
        svg.appendChild(txt("95% of the time the truth is somewhere in here",
          { x: L, y: 22, class: "tick" }));

        hoverable(bar, "<b>Wilson 95%</b><br>" + pct(w.low) + " to " + pct(w.high) +
          '<br><span class="k">' + k + " failures in " + n + " calls</span>");

        $("#wn-out").textContent = n.toLocaleString() + " calls";
        $("#wp-out").textContent = pct(rate);
        $("#w-point").textContent = pct(w.point);
        $("#w-k").textContent = k + " of " + n;
        $("#w-ci").textContent = pct(w.low, 1) + "–" + pct(w.high, 1);
        // The nearest rate whose own interval would not overlap this one.
        const sep = w.high;
        $("#w-sep").textContent = sep >= 0.4 ? "nothing" : pct(sep, 0) + "+";
      }
      n_in.addEventListener("input", draw);
      p_in.addEventListener("input", draw);
      draw();
    }

    /* ── 03 · concurrency, as two charts (never one dual axis) ───────────── */
    function renderConc() {
      const svg = $("#conc-svg");
      clear(svg);
      const W = 760, H = 260, gap = 46, panelW = (W - gap) / 2, T = 34, B = 46, L = 46;

      function panel(ox, title, key1, key4, unit, max) {
        const g = mk("g", { transform: "translate(" + ox + ",0)" });
        g.appendChild(txt(title, { x: L, y: 16, class: "slabel" }));
        const plotW = panelW - L - 12, plotH = H - T - B;
        const y = v => T + plotH - (v / max) * plotH;
        // grid
        for (let i = 0; i <= 4; i++) {
          const gy = T + plotH - (i / 4) * plotH;
          g.appendChild(mk("line", { x1: L, y1: gy, x2: L + plotW, y2: gy,
            stroke: INK.line, "stroke-dasharray": "2 4" }));
          g.appendChild(txt(((max * i) / 4).toFixed(0),
            { x: L - 8, y: gy + 4, "text-anchor": "end", class: "tick" }));
        }
        const bandW = plotW / CALIB.length;
        CALIB.forEach((d, i) => {
          const cx = L + bandW * (i + .5);
          const bw = 13;
          [[d[key1], -bw - 2, "1", SERIES], [d[key4], 2, "4", SERIES2]].forEach(([v, dx, c, col]) => {
            const r = mk("rect", { x: cx + dx, y: y(v), width: bw,
              height: Math.max(1, T + plotH - y(v)), rx: 3, fill: col });
            g.appendChild(hoverable(r, "<b>" + d.model + "</b><br>concurrency " + c +
              "<br>" + v + " " + unit));
          });
          g.appendChild(txt(d.model, { x: cx, y: T + plotH + 16,
            "text-anchor": "middle", class: "tick" }));
        });
        g.appendChild(mk("line", { x1: L, y1: T + plotH, x2: L + plotW, y2: T + plotH,
          stroke: INK.line }));
        return g;
      }
      svg.appendChild(panel(0, "Generation throughput (tok/s) — flat", "tok1", "tok4", "tok/s", 80));
      svg.appendChild(panel(panelW + gap, "Mean latency (s) — roughly triples", "lat1", "lat4", "s", 60));
      $("#conc-legend").innerHTML =
        '<span><i class="swatch" style="background:' + SERIES + '"></i>concurrency 1</span>' +
        '<span><i class="swatch" style="background:' + SERIES2 + '"></i>concurrency 4</span>' +
        "<span>8 calls per cell, full tool belt</span>";
    }

    /* ── 03 · classifier playground ──────────────────────────────────────── */
    function renderPlayground(D) {
      const ta = $("#play"), out = $("#play-verdict"), pick = $("#ex-pick");
      const samples = [];
      const byShape = {};
      (D.examples || []).forEach(e => { if (!byShape[e.shape || e.outcome]) byShape[e.shape || e.outcome] = e; });
      if (byShape.tools_tag) samples.push({ name: "chat · <tools>", body: byShape.tools_tag.content, destructive: false });
      if (byShape.function_tag) samples.push({ name: "coder · <function=>", body: byShape.function_tag.content, destructive: true });
      if (byShape.confirmation) samples.push({ name: "chat · confirmation", body: byShape.confirmation.content, destructive: true });
      samples.push({ name: "a plain mention", body: "I'll use ecosystem_app to restart it for you.", destructive: false });
      samples.push({ name: "nothing at all", body: "", destructive: false });

      let destructive = false;
      pick.innerHTML = samples.map((s, i) =>
        '<button type="button" data-i="' + i + '" aria-pressed="' + (i === 0) + '">' +
        s.name.replace(/[<>]/g, c => ({ "<": "&lt;", ">": "&gt;" }[c])) + "</button>").join("");

      function load(i) {
        $$("button", pick).forEach(b => b.setAttribute("aria-pressed", String(+b.dataset.i === i)));
        ta.value = samples[i].body;
        destructive = samples[i].destructive;
        score();
      }
      function score() {
        const r = classify(ta.value, destructive);
        const [glyph, name, why] = VERDICT[r.outcome];
        out.innerHTML =
          '<span class="verdict__pill" data-o="' + r.outcome + '"><b>' + glyph + "</b> " + name + "</span>" +
          (r.shape ? '<span class="verdict__why">matched <code>' + r.shape + "</code></span>"
                   : '<span class="verdict__why">' + why + "</span>");
      }
      pick.addEventListener("click", e => {
        const b = e.target.closest("button");
        if (b) load(+b.dataset.i);
      });
      ta.addEventListener("input", score);
      load(0);
    }

    /* ── 04 · prose-call rate ────────────────────────────────────────────── */
    function renderProse(D, M) {
      const svg = $("#prose-svg");
      clear(svg);
      const W = 760, H = 250, L = 78, R = 130, T = 18, Bm = 34;
      const max = 0.12, plotW = W - L - R, rowH = (H - T - Bm) / M.length;
      const x = v => L + (Math.min(v, max) / max) * plotW;

      for (let t = 0; t <= max + 1e-9; t += 0.02) {
        const px = x(t);
        svg.appendChild(mk("line", { x1: px, y1: T, x2: px, y2: H - Bm,
          stroke: INK.line, "stroke-dasharray": "2 4" }));
        svg.appendChild(txt((t * 100).toFixed(0) + "%",
          { x: px, y: H - Bm + 17, "text-anchor": "middle", class: "tick" }));
      }
      svg.appendChild(txt("share of tool-task calls serialised into prose",
        { x: L + plotW / 2, y: H - 6, "text-anchor": "middle", class: "tick" }));

      M.forEach((m, i) => {
        const e = D.emission[m].prose_call;
        const cy = T + rowH * (i + .5);
        svg.appendChild(txt(m, { x: L - 12, y: cy + 4, "text-anchor": "end", class: "vlabel" }));
        // interval whisker behind the bar
        svg.appendChild(mk("line", { x1: x(e.low), y1: cy, x2: x(e.high), y2: cy,
          stroke: MUTED, "stroke-width": 2 }));
        [e.low, e.high].forEach(v => svg.appendChild(mk("line",
          { x1: x(v), y1: cy - 6, x2: x(v), y2: cy + 6, stroke: MUTED, "stroke-width": 2 })));
        const bar = mk("rect", { x: L, y: cy - 9, width: Math.max(2, x(e.point) - L),
          height: 18, rx: 4, fill: SERIES });
        svg.appendChild(hoverable(bar, "<b>" + m + "</b><br>" + pct(e.point) +
          " serialised as prose<br>95% CI " + pct(e.low) + "–" + pct(e.high) +
          '<br><span class="k">' + e.k + " of " + e.n + " calls</span>"));
        svg.appendChild(txt(pct(e.point) + "  [" + pct(e.low, 1) + ", " + pct(e.high, 1) + "]",
          { x: x(e.high) + 10, y: cy + 4, class: "tick" }));
      });
      svg.appendChild(mk("line", { x1: L, y1: T, x2: L, y2: H - Bm, stroke: INK.line }));
    }

    /* ── 04 · the two syntaxes ───────────────────────────────────────────── */
    function renderShapes(D) {
      const wrap = $("#shapes");
      const esc = s => s.replace(/[<>&]/g, c => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;" }[c]));
      const pickBy = shape => (D.examples || []).find(e => e.shape === shape);
      const a = pickBy("tools_tag"), b = pickBy("function_tag");
      const card = (e, who, count) => !e ? "" :
        '<div><div class="panel__head"><span class="panel__title">' + who + "</span>" +
        '<span class="tile__s">' + count + "</span></div>" +
        '<div class="plot" style="padding:.9rem 1rem"><pre style="margin:0;white-space:pre-wrap;' +
        'font-family:var(--font-mono);font-size:.78rem;line-height:1.6;color:var(--color-body)">' +
        esc(e.content.trim().slice(0, 340)) + "</pre></div></div>";
      const nChat = (D.syntax_shapes.chat || {}).tools_tag || 0;
      const nCoder = (D.syntax_shapes.coder || {}).function_tag || 0;
      wrap.innerHTML = '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:1.2rem">' +
        card(a, "chat", nChat + " of " + D.emission.chat.prose_call.n + " calls") +
        card(b, "coder", nCoder + " of " + D.emission.coder.prose_call.n + " calls") + "</div>";
    }

    /* ── 05 · before/after the grader fix ────────────────────────────────── */
    function renderSlope(D, M) {
      const svg = $("#slope-svg");
      clear(svg);
      const W = 760, H = 300, L = 84, R = 96, T = 30, Bm = 34;
      const lo = 0.6, hi = 1.0, plotW = W - L - R, rowH = (H - T - Bm) / M.length;
      const x = v => L + ((v - lo) / (hi - lo)) * plotW;

      for (let t = 0.6; t <= 1.0001; t += 0.1) {
        const px = x(t);
        svg.appendChild(mk("line", { x1: px, y1: T - 8, x2: px, y2: H - Bm,
          stroke: INK.line, "stroke-dasharray": "2 4" }));
        svg.appendChild(txt((t * 100).toFixed(0) + "%",
          { x: px, y: H - Bm + 17, "text-anchor": "middle", class: "tick" }));
      }
      svg.appendChild(txt("end-to-end success on tasks that need a tool",
        { x: L + plotW / 2, y: H - 6, "text-anchor": "middle", class: "tick" }));

      M.forEach((m, i) => {
        const a = D.accuracy[m].first_pass_end_to_end, b = D.accuracy[m].end_to_end;
        const cy = T + rowH * (i + .5);
        svg.appendChild(txt(m, { x: L - 14, y: cy + 4, "text-anchor": "end", class: "vlabel" }));
        svg.appendChild(mk("line", { x1: x(a.point), y1: cy, x2: x(b.point), y2: cy,
          stroke: SERIES, "stroke-width": 2, opacity: .45 }));
        const before = mk("circle", { cx: x(a.point), cy: cy, r: 6, fill: "#0B0F13",
          stroke: MUTED, "stroke-width": 2 });
        const after = mk("circle", { cx: x(b.point), cy: cy, r: 6.5, fill: SERIES,
          stroke: "#0B0F13", "stroke-width": 2 });
        svg.appendChild(hoverable(before, "<b>" + m + "</b> — first grading<br>" + pct(a.point) +
          '<br><span class="k">confirmations counted as refusals</span>'));
        svg.appendChild(hoverable(after, "<b>" + m + "</b> — corrected<br>" + pct(b.point) +
          "<br>95% CI " + pct(b.low) + "–" + pct(b.high)));
        const delta = b.point - a.point;
        svg.appendChild(txt((delta >= 0 ? "+" : "") + (delta * 100).toFixed(1) + " pp",
          { x: W - R + 14, y: cy + 4, class: "tick",
            fill: Math.abs(delta) > 0.001 ? SERIES : MUTED }));
      });
      $("#slope-legend").innerHTML =
        '<span><i class="swatch" style="background:#0B0F13;border:2px solid ' + MUTED + '"></i>first grading</span>' +
        '<span><i class="swatch" style="background:' + SERIES + '"></i>after adding the confirmation outcome</span>';
    }

    /* ── 06 · accuracy, with a metric toggle ─────────────────────────────── */
    function renderAccuracy(D, M) {
      const svg = $("#acc-svg");
      let metric = "end_to_end";

      $("#acc-tiles").innerHTML = M.map(m => {
        const e = D.accuracy[m].end_to_end;
        return '<div class="tile"><div class="tile__k">' + m + '</div><div class="tile__v accent">' +
          pct(e.point) + '</div><div class="tile__s">95% CI ' + pct(e.low) + "–" + pct(e.high) +
          " · n=" + e.n + "</div></div>";
      }).join("");

      function draw() {
        clear(svg);
        $("#acc-note").textContent = ACC_NOTE[metric];
        const W = 760, H = 250, L = 78, R = 120, T = 16, Bm = 34;
        const plotW = W - L - R, rowH = (H - T - Bm) / M.length;
        const x = v => L + v * plotW;
        for (let t = 0; t <= 1.0001; t += 0.25) {
          const px = x(t);
          svg.appendChild(mk("line", { x1: px, y1: T, x2: px, y2: H - Bm,
            stroke: INK.line, "stroke-dasharray": "2 4" }));
          svg.appendChild(txt((t * 100).toFixed(0) + "%",
            { x: px, y: H - Bm + 17, "text-anchor": "middle", class: "tick" }));
        }
        M.forEach((m, i) => {
          const e = D.accuracy[m][metric];
          const cy = T + rowH * (i + .5);
          svg.appendChild(txt(m, { x: L - 12, y: cy + 4, "text-anchor": "end", class: "vlabel" }));
          svg.appendChild(mk("line", { x1: x(e.low), y1: cy, x2: x(e.high), y2: cy,
            stroke: MUTED, "stroke-width": 2 }));
          [e.low, e.high].forEach(v => svg.appendChild(mk("line",
            { x1: x(v), y1: cy - 6, x2: x(v), y2: cy + 6, stroke: MUTED, "stroke-width": 2 })));
          const bar = mk("rect", { x: L, y: cy - 9, width: Math.max(2, x(e.point) - L),
            height: 18, rx: 4, fill: SERIES });
          svg.appendChild(hoverable(bar, "<b>" + m + "</b><br>" + pct(e.point) +
            "<br>95% CI " + pct(e.low) + "–" + pct(e.high) +
            '<br><span class="k">' + e.k + " of " + e.n + "</span>"));
          svg.appendChild(txt(pct(e.point), { x: x(e.high) + 10, y: cy + 4, class: "vlabel" }));
        });
        svg.appendChild(mk("line", { x1: L, y1: T, x2: L, y2: H - Bm, stroke: INK.line }));
      }
      $("#acc-toggle").addEventListener("click", e => {
        const b = e.target.closest("button");
        if (!b) return;
        metric = b.dataset.m;
        $$("#acc-toggle button").forEach(x2 => x2.setAttribute("aria-pressed", String(x2 === b)));
        draw();
      });
      draw();
    }

    /* ── 06 · per-task heatmap ───────────────────────────────────────────── */
    function renderHeat(D, M) {
      const host = $("#heat");
      const ids = Object.keys(D.per_task).sort();
      const step = v => Math.min(RAMP.length - 1, Math.max(0, Math.floor(v * RAMP.length - 1e-9)));
      let html = '<div class="heat" style="grid-template-columns:minmax(120px,auto) repeat(' +
        M.length + ',minmax(52px,1fr))">';
      html += "<div></div>" + M.map(m => '<div class="heat__col">' + m + "</div>").join("");
      ids.forEach(id => {
        const kind = (D.tasks[id] || {}).kind === "abstain" ? " ·" : "";
        html += '<div class="heat__lab">' + id + kind + "</div>";
        M.forEach(m => {
          const e = D.per_task[id][m];
          if (!e) { html += "<div></div>"; return; }
          const s = step(e.point);
          html += '<div class="heat__cell" data-id="' + id + '" data-m="' + m +
            '" style="background:' + RAMP[s] + ";color:" + RAMP_INK[s] + '">' +
            (e.point * 100).toFixed(0) + "</div>";
        });
      });
      html += "</div>";
      host.innerHTML = html +
        '<div class="legend"><span>less accurate</span>' +
        RAMP.map(c => '<i class="swatch" style="background:' + c + '"></i>').join("") +
        "<span>more</span><span>· marks a task that needs no tool</span></div>";

      $$(".heat__cell", host).forEach(cell => {
        const e = D.per_task[cell.dataset.id][cell.dataset.m];
        const t = D.tasks[cell.dataset.id] || {};
        hoverable(cell, "<b>" + cell.dataset.id + "</b> · " + cell.dataset.m + "<br>" +
          pct(e.point) + " — 95% CI " + pct(e.low) + "–" + pct(e.high) +
          '<br><span class="k">' + e.k + " of " + e.n +
          (t.expect ? " · expects " + t.expect : " · no tool expected") + "</span>");
      });
    }

    /* ── 07 · the budget cliff ───────────────────────────────────────────── */
    function renderCliff(D) {
      const svg = $("#cliff-svg"), slider = $("#budget");
      const models = Object.keys(D.budget);
      if (!models.length) return;
      const caps = D.budget[models[0]].map(d => d.max_tokens);
      const colour = m => (m === models[0] ? SERIES : SERIES2);
      slider.max = String(caps.length - 1);

      function draw() {
        clear(svg);
        const W = 760, H = 290, L = 58, R = 118, T = 22, Bm = 46;
        const plotW = W - L - R, plotH = H - T - Bm;
        const x = i => L + (i / (caps.length - 1)) * plotW;
        const y = v => T + plotH - v * plotH;

        for (let t = 0; t <= 1.0001; t += 0.25) {
          svg.appendChild(mk("line", { x1: L, y1: y(t), x2: L + plotW, y2: y(t),
            stroke: INK.line, "stroke-dasharray": "2 4" }));
          svg.appendChild(txt((t * 100).toFixed(0) + "%",
            { x: L - 8, y: y(t) + 4, "text-anchor": "end", class: "tick" }));
        }
        caps.forEach((c, i) => svg.appendChild(txt(String(c),
          { x: x(i), y: H - Bm + 18, "text-anchor": "middle", class: "tick" })));
        svg.appendChild(txt("max_tokens", { x: L + plotW / 2, y: H - Bm + 38,
          "text-anchor": "middle", class: "tick" }));

        const sel = +slider.value;
        svg.appendChild(mk("line", { x1: x(sel), y1: T - 6, x2: x(sel), y2: T + plotH,
          stroke: SERIES, "stroke-width": 1, opacity: .5, "stroke-dasharray": "3 3" }));

        models.forEach(m => {
          const pts = D.budget[m];
          const d = pts.map((p, i) => (i ? "L" : "M") + x(i) + " " + y(p.empty.point)).join(" ");
          svg.appendChild(mk("path", { d: d, fill: "none", stroke: colour(m), "stroke-width": 2,
            "stroke-linejoin": "round" }));
          pts.forEach((p, i) => {
            const c = mk("circle", { cx: x(i), cy: y(p.empty.point), r: i === sel ? 7 : 5,
              fill: colour(m), stroke: "#0B0F13", "stroke-width": 2 });
            svg.appendChild(hoverable(c, "<b>" + m + "</b> at " + p.max_tokens + " tokens<br>" +
              pct(p.empty.point) + " returned nothing<br>95% CI " + pct(p.empty.low) + "–" +
              pct(p.empty.high) + '<br><span class="k">mean ' +
              Math.round(p.mean_completion_tokens) + " completion tokens</span>"));
          });
          const last = pts[pts.length - 1];
          svg.appendChild(txt(m, { x: L + plotW + 12, y: y(last.empty.point) + 4,
            class: "vlabel", fill: colour(m) }));
        });

        const chosen = caps[sel];
        $("#budget-out").textContent = chosen + " tokens";
        $("#cliff-tiles").innerHTML = models.map(m => {
          const p = D.budget[m][sel];
          const bad = p.empty.point;
          return '<div class="tile"><div class="tile__k">' + m + " at " + chosen + '</div>' +
            '<div class="tile__v" style="color:' +
            (bad > 0.5 ? "var(--color-down-raised)" : bad > 0.05 ? "var(--color-warn)" : "var(--color-up)") +
            '">' + (bad > 0.5 ? "✗ " : bad > 0.05 ? "! " : "✓ ") + pct(bad) + "</div>" +
            '<div class="tile__s">returned nothing · n=' + p.n + "</div></div>";
        }).join("");
      }
      slider.addEventListener("input", draw);
      $("#cliff-legend").innerHTML = models.map(m =>
        '<span><i class="swatch" style="background:' + colour(m) + '"></i>' + m +
        (m === models[0] ? " (thinking)" : " (no thinking)") + "</span>").join("");
      draw();
    }

    /* ── 08 · accuracy against latency ───────────────────────────────────── */
    function renderSpeed(D, M) {
      const svg = $("#speed-svg");
      clear(svg);
      const W = 760, H = 320, L = 62, R = 40, T = 24, Bm = 52;
      const plotW = W - L - R, plotH = H - T - Bm;
      const maxLat = Math.max.apply(null, M.map(m => D.speed[m].p50)) * 1.25;
      const lo = 0.8, hi = 1.0;
      const x = v => L + (v / maxLat) * plotW;
      const y = v => T + plotH - ((v - lo) / (hi - lo)) * plotH;
      const toks = M.map(m => D.speed[m].mean_completion_tokens || 1);
      const maxTok = Math.max.apply(null, toks);
      const r = v => 9 + 26 * Math.sqrt((v || 1) / maxTok);

      for (let t = lo; t <= hi + 1e-9; t += 0.05) {
        svg.appendChild(mk("line", { x1: L, y1: y(t), x2: L + plotW, y2: y(t),
          stroke: INK.line, "stroke-dasharray": "2 4" }));
        svg.appendChild(txt((t * 100).toFixed(0) + "%",
          { x: L - 8, y: y(t) + 4, "text-anchor": "end", class: "tick" }));
      }
      for (let s = 0; s <= maxLat; s += 4) {
        svg.appendChild(txt(s + "s", { x: x(s), y: H - Bm + 18,
          "text-anchor": "middle", class: "tick" }));
      }
      svg.appendChild(txt("median latency per call", { x: L + plotW / 2, y: H - Bm + 38,
        "text-anchor": "middle", class: "tick" }));
      svg.appendChild(txt("end-to-end accuracy", { x: 14, y: T + plotH / 2,
        "text-anchor": "middle", class: "tick",
        transform: "rotate(-90 14 " + (T + plotH / 2) + ")" }));

      M.forEach(m => {
        const s = D.speed[m], a = D.accuracy[m].end_to_end;
        const cx = x(s.p50), cy = y(a.point), rad = r(s.mean_completion_tokens);
        const c = mk("circle", { cx: cx, cy: cy, r: rad, fill: SERIES, "fill-opacity": .22,
          stroke: SERIES, "stroke-width": 2 });
        svg.appendChild(hoverable(c, "<b>" + m + "</b><br>" + pct(a.point) + " accurate<br>" +
          s.p50.toFixed(2) + "s median · " + s.p95.toFixed(1) + "s p95<br>" +
          '<span class="k">' + Math.round(s.mean_completion_tokens || 0) +
          " completion tokens · " + s.gen_tok_s.toFixed(0) + " tok/s</span>"));
        svg.appendChild(txt(m, { x: cx, y: cy - rad - 8, "text-anchor": "middle", class: "vlabel" }));
      });
      svg.appendChild(mk("line", { x1: L, y1: T, x2: L, y2: T + plotH, stroke: INK.line }));
      svg.appendChild(mk("line", { x1: L, y1: T + plotH, x2: L + plotW, y2: T + plotH, stroke: INK.line }));
    }
  }

  /* ── page chrome ───────────────────────────────────────────────────────── */
  function chrome() {
    const bar = $("#progress");
    const sections = $$("section[id]");
    const links = $$("#nav a");
    function onScroll() {
      const h = document.documentElement;
      const max = h.scrollHeight - h.clientHeight;
      bar.style.width = (max > 0 ? (h.scrollTop / max) * 100 : 0) + "%";
    }
    document.addEventListener("scroll", onScroll, { passive: true });
    onScroll();

    if ("IntersectionObserver" in window) {
      const spy = new IntersectionObserver(es => {
        es.forEach(e => {
          if (!e.isIntersecting) return;
          links.forEach(a => a.setAttribute("aria-current",
            String(a.getAttribute("href") === "#" + e.target.id)));
        });
      }, { rootMargin: "-45% 0px -50% 0px" });
      sections.forEach(s => spy.observe(s));

      const rise = new IntersectionObserver(es => {
        es.forEach(e => { if (e.isIntersecting) { e.target.classList.add("in"); rise.unobserve(e.target); } });
      }, { rootMargin: "0px 0px -8% 0px" });
      $$(".panel, .tiles, .measure").forEach(n => { n.classList.add("rise"); rise.observe(n); });
    }

    // The core reads as attending rather than decorating: it settles once the
    // page has loaded, which is the only state change it has to earn.
    const core = window.Core && $("#core") ? window.Core.attach($("#core")) : null;
    if (core) setTimeout(() => core.setState("idle"), 2600);
  }
})();
