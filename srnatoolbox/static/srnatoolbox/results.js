/* sRNAbench result pages: Plotly charts from the summary JSON (job page: #summary-data, sample page:
 * #sample-data), sortable and filterable tables. Runs again after HTMX swaps the job status in. */
(function () {
  "use strict";
  const COLORS = {
    "microRNA": "#1f5f8b", "tRNA": "#e08a2c", "rRNA": "#8fb9d9", "snoRNA": "#2a7f62", "snRNA": "#6fb08f",
    "Y RNA": "#c9a227", "other small ncRNA": "#7b61a8", "lncRNA": "#c47bb0", "mRNA, introns, other genomic": "#9aa5b1",
    "other": "#cfd5db", "unassigned": "#eceff2",
  };
  const ORDER = Object.keys(COLORS);
  const FONT = {family: "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif", size: 12, color: "#3b444d"};
  const CONFIG = {displaylogo: false, responsive: true,
    modeBarButtonsToRemove: ["select2d", "lasso2d", "autoScale2d", "toggleSpikelines"]};

  function layout(extra) {
    return Object.assign({font: FONT, paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
      margin: {l: 50, r: 10, t: 10, b: 40}, hoverlabel: {font: FONT}}, extra);
  }

  function classesChart(el, samples, classes) {
    const names = samples.map((s) => s.name);
    const traces = ORDER.filter((c) => classes.includes(c)).map((c) => ({
      type: "bar", orientation: "h", name: c, y: names, x: samples.map((s) => s.classes[c] || 0),
      marker: {color: COLORS[c], line: {color: c === "unassigned" ? "#c8d3dd" : COLORS[c], width: 1}},
      hovertemplate: "%{y}<br>" + c + ": %{x:.1f} %<extra></extra>",
    }));
    const height = Math.max(160, 60 + 22 * names.length);
    el.style.height = height + "px";
    Plotly.newPlot(el, traces, layout({
      barmode: "stack", height, margin: {l: Math.min(160, 10 + 7 * Math.max(...names.map((n) => n.length))), r: 10, t: 10, b: 70},
      xaxis: {title: {text: "% of reads in analysis"}, range: [0, 100], gridcolor: "#eef0f3"},
      yaxis: {autorange: "reversed", automargin: true},
      legend: {orientation: "h", y: -0.25, x: 0, font: {size: 11}},
    }), CONFIG);
  }

  function lengthsChart(el, samples) {
    const many = samples.length > 12;
    const traces = samples.map((s) => {
      const xs = Object.keys(s.lengths).map(Number).filter((x) => x <= 50).sort((a, b) => a - b);
      return samples.length === 1
        ? {type: "bar", name: s.name, x: xs, y: xs.map((x) => s.lengths[x]), marker: {color: "#1f5f8b"},
           hovertemplate: "%{x} nt: %{y:.2f} %<extra></extra>"}
        : {type: "scatter", mode: "lines", name: s.name, x: xs, y: xs.map((x) => s.lengths[x]),
           line: {width: many ? 1 : 2}, opacity: many ? 0.6 : 1,
           hovertemplate: s.name + "<br>%{x} nt: %{y:.2f} %<extra></extra>"};
    });
    Plotly.newPlot(el, traces, layout({
      height: 320, showlegend: !many && samples.length > 1, legend: {font: {size: 11}},
      xaxis: {title: {text: "read length (nt)"}, dtick: 5, gridcolor: "#eef0f3"},
      yaxis: {title: {text: "% of reads"}, gridcolor: "#eef0f3", rangemode: "tozero"},
    }), CONFIG);
  }

  function charts() {
    if (typeof Plotly === "undefined") return;
    const job = document.getElementById("summary-data");
    const one = document.getElementById("sample-data");
    let samples, classes;
    if (job) {
      const data = JSON.parse(job.textContent);
      samples = data.samples;
      classes = data.classes;
    } else if (one) {
      const s = JSON.parse(one.textContent);
      samples = [s];
      classes = Object.keys(s.classes);
    } else return;
    const c = document.getElementById("chart-classes");
    const l = document.getElementById("chart-lengths");
    if (c && !c.dataset.done) { classesChart(c, samples, classes); c.dataset.done = "1"; }
    if (l && !l.dataset.done) { lengthsChart(l, samples); l.dataset.done = "1"; }
  }

  // ---- tables
  document.addEventListener("click", (e) => {
    const th = e.target.closest("table.sortable th[data-sort]");
    if (!th) return;
    const table = th.closest("table");
    const idx = [...th.parentElement.children].indexOf(th);
    const dir = th.dataset.dir === "asc" ? "desc" : "asc";
    table.querySelectorAll("th[data-sort]").forEach((h) => { delete h.dataset.dir; h.removeAttribute("aria-sort"); });
    th.dataset.dir = dir;
    th.setAttribute("aria-sort", dir === "asc" ? "ascending" : "descending");
    const num = th.dataset.sort === "num";
    const body = table.tBodies[0];
    const rows = [...body.rows];
    const key = (r) => {
      const cell = r.cells[idx];
      const v = cell ? (cell.dataset.value ?? cell.textContent.trim()) : "";
      return num ? Number(v) || 0 : v.toLowerCase();
    };
    rows.sort((a, b) => {
      const ka = key(a), kb = key(b);
      return (ka < kb ? -1 : ka > kb ? 1 : 0) * (dir === "asc" ? 1 : -1);
    });
    rows.forEach((r) => body.appendChild(r));
  });
  document.addEventListener("input", (e) => {
    const f = e.target.closest(".table-filter");
    if (!f) return;
    const terms = f.value.toLowerCase().split(/\s+/).filter(Boolean);
    for (const r of document.getElementById(f.dataset.table).tBodies[0].rows) {
      const text = r.textContent.toLowerCase();
      r.hidden = !terms.every((t) => text.includes(t));
    }
  });
  // charts in tabs that were hidden: resize when shown
  document.addEventListener("shown.bs.tab", () => {
    document.querySelectorAll(".chart[data-done]").forEach((el) => Plotly.Plots.resize(el));
  });
  document.body.addEventListener("htmx:afterSwap", charts);
  charts();
})();
