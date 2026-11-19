/* sRNAde job page: comparison and method choice, tiles, volcano plot, heatmap and table from #de-data.
 * Runs again after HTMX swaps the finished job in. */
(function () {
  "use strict";
  const FONT = {family: "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif", size: 12, color: "#3b444d"};
  const CONFIG = {displaylogo: false, responsive: true,
    modeBarButtonsToRemove: ["select2d", "lasso2d", "autoScale2d", "toggleSpikelines"]};
  // diverging pair: blue (down) <-> red (up), neutral grey for not significant
  const UP = "#e34948", DOWN = "#2a78d6", NS = "#b9bdc4";
  const GROUP_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"];
  const LIB = {mirna: "microRNAs", trna: "tRNAs", libs: "other RNA classes"};
  const COUNT = {rcadj: "multi-mappers shared", rcsa: "multi-mappers to the top reference"};

  const fmt = (v, d) => (v === null || v === undefined ? "–" : Number(v).toLocaleString(undefined, {maximumFractionDigits: d}));
  const fmtP = (v) => (v === null || v === undefined ? "–" : v === 0 ? "0" : v < 0.001 ? Number(v).toExponential(1) : Number(v).toPrecision(2));
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));

  function layout(extra) {
    return Object.assign({font: FONT, paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
      margin: {l: 55, r: 10, t: 10, b: 45}, hoverlabel: {font: FONT}}, extra);
  }

  function init() {
    const node = document.getElementById("de-data");
    const select = document.getElementById("de-comparison");
    if (!node || !select || select.dataset.ready) return;
    select.dataset.ready = "1";
    const de = JSON.parse(node.textContent);
    const byId = Object.fromEntries(de.analyses.map((a) => [a.id, a]));
    const groups = [...new Set(de.samples.map((s) => s.group))].sort();
    const methodSel = document.getElementById("de-method");
    const multiRef = new Set(de.comparisons.map((c) => c.library + c.reference + c.strand)).size > 1;
    const multiCount = new Set(de.comparisons.map((c) => c.count_type)).size > 1;

    de.comparisons.forEach((c, i) => {
      const extra = [LIB[c.library] || c.library];
      if (multiRef) extra.push(c.reference_label + (c.strand ? ` (${c.strand})` : ""));
      if (multiCount) extra.push(COUNT[c.count_type] || c.count_type);
      select.add(new Option(`${c.a} vs ${c.b} · ${extra.join(" · ")}`, String(i)));
    });

    function comparison() { return de.comparisons[Number(select.value)]; }

    function fillMethods() {
      const c = comparison();
      const keep = methodSel.value;
      methodSel.innerHTML = "";
      c.analyses.forEach((id) => methodSel.add(new Option(byId[id].method, id)));
      const same = c.analyses.find((id) => byId[id].method === (byId[keep] || {}).method);
      if (same) methodSel.value = same;
    }

    function tiles(c) {
      const el = document.getElementById("de-tiles");
      const parts = c.analyses.map((id) => {
        const a = byId[id];
        return `<div><strong>${a.stats ? `${a.up} ↑ ${a.down} ↓` : "–"}</strong><span>${esc(a.method)}: significant of ${a.tested.toLocaleString()} tested</span></div>`;
      });
      if (c.analyses.length > 1 && c.analyses.some((id) => byId[id].stats)) {
        parts.unshift(`<div><strong>${c.consensus_up.length} ↑ ${c.consensus_down.length} ↓</strong><span>significant with all ${c.analyses.length} methods (consensus)</span></div>`);
      }
      el.innerHTML = parts.join("");
    }

    function volcano(a) {
      const el = document.getElementById("chart-volcano");
      const v = a.volcano;
      const lfcMin = de.log2fc;
      const cls = v.s.map((s) => (s > 0 ? "up" : s < 0 ? "down" : "ns"));
      const trace = (k, name, color) => {
        const idx = cls.map((c, i) => (c === k ? i : -1)).filter((i) => i >= 0);
        return {type: "scattergl", mode: "markers", name, x: idx.map((i) => v.x[i]), y: idx.map((i) => v.y[i]),
          text: idx.map((i) => v.g[i]), marker: {size: k === "ns" ? 6 : 8, color, line: {color: "#ffffff", width: k === "ns" ? 0 : 1}},
          hovertemplate: "%{text}<br>log2 FC %{x:.2f}<br>−log10 p %{y:.2f}<extra></extra>"};
      };
      const traces = [trace("ns", "not significant", NS), trace("up", `higher in ${a.a}`, UP), trace("down", `higher in ${a.b}`, DOWN)];
      const shapes = a.stats ? [
        {type: "line", xref: "x", yref: "paper", x0: lfcMin, x1: lfcMin, y0: 0, y1: 1, line: {color: "#9aa1aa", width: 1, dash: "dot"}},
        {type: "line", xref: "x", yref: "paper", x0: -lfcMin, x1: -lfcMin, y0: 0, y1: 1, line: {color: "#9aa1aa", width: 1, dash: "dot"}},
      ] : [];
      Plotly.react(el, traces, layout({height: 380, shapes,
        xaxis: {title: {text: `log2 fold change (${a.a} / ${a.b})`}, zeroline: true, zerolinecolor: "#d5d9de", gridcolor: "#eef0f3"},
        yaxis: {title: {text: "−log10 p-value"}, rangemode: "tozero", gridcolor: "#eef0f3"},
        legend: {orientation: "h", y: -0.2, font: {size: 11}}}), CONFIG);
      document.getElementById("volcano-note").textContent = a.stats
        ? `${a.method}: log2 fold change against −log10 p-value; significant ones coloured. Hover for the name.`
        : `${a.method}: fold changes only (no p-values).`;
    }

    function heatmap(c) {
      const el = document.getElementById("chart-heatmap");
      const note = document.getElementById("heatmap-note");
      const h = c.heatmap;
      if (!h) {
        Plotly.purge(el);
        el.innerHTML = '<p class="small text-secondary">Not enough references or samples for a heatmap.</p>';
        return;
      }
      el.innerHTML = "";
      const used = c.consensus_up.length + c.consensus_down.length ? "consensus references" : "top references of " + byId[c.analyses[0]].method;
      note.textContent = `${h.genes.length} ${used}, sorted by fold change; z-score of log2 CPM per row. Coloured bar: group.`;
      const colorOf = (g) => GROUP_COLORS[groups.indexOf(g)] || "#8a8f98";
      const longest = Math.max(...h.genes.map((g) => g.length));
      Plotly.react(el, [
        {type: "heatmap", z: h.z, x: h.samples, y: h.genes, zmin: -3, zmax: 3, xaxis: "x", yaxis: "y",
          colorscale: [[0, DOWN], [0.5, "#f0efec"], [1, UP]], colorbar: {title: {text: "z"}, thickness: 10, len: 0.8},
          hovertemplate: "%{y}<br>%{x}<br>z %{z:.2f}<extra></extra>"},
        {type: "scatter", mode: "markers", x: h.samples, y: h.samples.map(() => 0), xaxis: "x", yaxis: "y2", showlegend: false,
          marker: {symbol: "square", size: 10, color: h.groups.map(colorOf)}, text: h.groups,
          hovertemplate: "%{x}: %{text}<extra></extra>"},
      ], layout({height: Math.max(380, 80 + 14 * h.genes.length),
        margin: {l: Math.min(220, 12 + 6.5 * longest), r: 10, t: 24, b: 90},
        xaxis: {tickangle: -45, automargin: true}, yaxis: {domain: [0, 0.94], autorange: "reversed", tickfont: {size: 10}},
        yaxis2: {domain: [0.955, 1], visible: false, range: [-0.5, 0.5]}}), CONFIG);
      el.style.height = Math.max(380, 80 + 14 * h.genes.length) + "px";
    }

    function table(a) {
      const body = document.querySelector("#de-table tbody");
      document.getElementById("th-mean-a").textContent = `Mean ${a.mean_cols[0] || a.a}`;
      document.getElementById("th-mean-b").textContent = `Mean ${a.mean_cols[1] || a.b}`;
      body.innerHTML = a.rows.map((r) => {
        const sig = a.stats && r[5] !== null && r[5] < de.fdr && Math.abs(r[3] || 0) >= de.log2fc;
        const dir = sig ? (r[3] > 0 ? "up" : "down") : "";
        return `<tr${sig ? ` class="row-sig row-${dir}"` : ""}><td data-value="${esc(r[0])}">${sig ? `<span class="sig-mark" aria-label="significant, ${dir}">${r[3] > 0 ? "↑" : "↓"}</span> ` : ""}${esc(r[0])}</td>` +
          `<td class="num" data-value="${r[1] ?? ""}">${fmt(r[1], 1)}</td><td class="num" data-value="${r[2] ?? ""}">${fmt(r[2], 1)}</td>` +
          `<td class="num" data-value="${r[3] ?? ""}">${fmt(r[3], 2)}</td><td class="num" data-value="${r[4] ?? ""}">${fmtP(r[4])}</td>` +
          `<td class="num" data-value="${r[5] ?? ""}">${fmtP(r[5])}</td></tr>`;
      }).join("");
      document.getElementById("table-note").textContent = `${a.method}, sorted by FDR; ` +
        (a.rows.length < a.tested ? `the first ${a.rows.length} of ${a.tested.toLocaleString()} references (the full table is a download).` : `${a.tested.toLocaleString()} references.`);
      const dl = document.getElementById("de-download");
      dl.href = dl.dataset.base.replace(/__$/, a.file.replace(/^de\//, "").split("/").map(encodeURIComponent).join("/"));
      const filter = document.querySelector("#tab-de .table-filter");
      if (filter) filter.value = "";
    }

    function show() {
      const c = comparison();
      const a = byId[methodSel.value] || byId[c.analyses[0]];
      document.getElementById("de-nostats").hidden = a.stats;
      tiles(c);
      volcano(a);
      table(a);
      heatmap(c);
    }

    select.addEventListener("change", () => { fillMethods(); show(); });
    methodSel.addEventListener("change", show);
    fillMethods();
    show();
  }

  document.addEventListener("shown.bs.tab", () => {
    ["chart-volcano", "chart-heatmap"].forEach((id) => {
      const el = document.getElementById(id);
      if (el && el.data) Plotly.Plots.resize(el);
    });
  });
  document.body.addEventListener("htmx:afterSwap", init);
  init();
})();
