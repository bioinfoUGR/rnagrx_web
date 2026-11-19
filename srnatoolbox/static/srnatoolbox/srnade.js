/* sRNAde form: group and sample ticks, live summary line, PCA of the ticked samples (Plotly). */
(function () {
  "use strict";
  const form = document.getElementById("srnade-form");
  if (!form) return;
  const data = JSON.parse(document.getElementById("de-page-data").textContent);
  // categorical slots in fixed order (identity follows the group, never its rank)
  const GROUP_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"];
  const groupNames = [...new Set(data.samples.map((s) => s.group).filter(Boolean))].sort();
  const colorOf = (g) => {
    const i = groupNames.indexOf(g);
    return i >= 0 && i < GROUP_COLORS.length ? GROUP_COLORS[i] : "#8a8f98";
  };
  const rows = [...form.querySelectorAll("#de-samples tbody tr")];
  const groupBoxes = [...form.querySelectorAll("input[name=groups]")];
  const libLabel = () => {
    const r = form.querySelector("input[name=library_type]:checked");
    return r ? form.querySelector(`label[for="${r.id}"]`).textContent.trim().split(" (")[0] : "";
  };

  function groupsOn() {
    return new Set(groupBoxes.filter((b) => b.checked).map((b) => b.value));
  }

  function used() {
    const on = groupsOn();
    return rows.filter((r) => {
      const box = r.querySelector("input[name=samples]");
      return box.checked && !box.disabled && on.has(r.dataset.group);
    }).map((r) => r.dataset.name);
  }

  function update() {
    const on = groupsOn();
    rows.forEach((r) => r.classList.toggle("row-off", !on.has(r.dataset.group)));
    const names = used();
    const counts = {};
    rows.forEach((r) => { if (names.includes(r.dataset.name)) counts[r.dataset.group] = (counts[r.dataset.group] || 0) + 1; });
    const groups = Object.keys(counts).sort();
    const usable = rows.filter((r) => !r.querySelector("input[name=samples]").disabled).length;
    document.getElementById("sample-count").textContent = `${names.length} of ${usable} samples` +
      (groups.length ? " · " + groups.map((g) => `${g} ${counts[g]}`).join(", ") : "");
    const summary = document.getElementById("de-summary");
    const submit = document.getElementById("de-submit");
    const method = form.querySelector("select[name=method]");
    const methodText = method.value === "all" ? "consensus of 3 methods" : method.options[method.selectedIndex].text;
    if (groups.length < 2) {
      summary.textContent = "Select samples of at least two groups.";
      submit.disabled = true;
    } else {
      const pairs = groups.length * (groups.length - 1) / 2;
      const what = groups.length === 2 ? `${groups[0]} (${counts[groups[0]]}) vs ${groups[1]} (${counts[groups[1]]})`
        : `${groups.length} groups, ${pairs} comparisons`;
      const single = groups.filter((g) => counts[g] < 2);
      summary.textContent = `${what} · ${libLabel()} · ${methodText}` +
        (single.length ? ` · ${single.join(", ")}: one sample, fold changes only` : "");
      submit.disabled = false;
    }
    schedulePca();
  }

  // ---- PCA
  let timer = null;
  let request = 0;
  function schedulePca() {
    const el = document.getElementById("chart-pca");
    if (!el || typeof Plotly === "undefined") return;
    clearTimeout(timer);
    timer = setTimeout(loadPca, 350);
  }

  async function loadPca() {
    const el = document.getElementById("chart-pca");
    const names = used();
    const mine = ++request;
    const params = new URLSearchParams();
    names.forEach((n) => params.append("s", n));
    let res;
    try {
      const r = await fetch(`${data.pca_url}?${params}`, {headers: {"Accept": "application/json"}});
      res = (await r.json()).pca;
    } catch (e) {
      return;
    }
    if (mine !== request) return;
    if (!res) {
      Plotly.purge(el);
      el.innerHTML = '<p class="small text-secondary p-3">Tick at least three samples for a PCA.</p>';
      return;
    }
    el.innerHTML = "";
    const groupOf = Object.fromEntries(data.samples.map((s) => [s.name, s.group]));
    const inUse = new Set(names);
    const traces = [];
    const shown = groupNames.length ? groupNames : [""];
    for (const g of shown) {
      for (const on of [true, false]) {
        const idx = res.names.map((n, i) => i).filter((i) => (groupOf[res.names[i]] || "") === g && inUse.has(res.names[i]) === on);
        if (!idx.length) continue;
        traces.push({
          type: "scatter", mode: "markers", name: on ? (g || "samples") : `${g || "samples"} (left out)`,
          legendgroup: g, showlegend: on || !idx.length,
          x: idx.map((i) => res.x[i]), y: idx.map((i) => res.y[i]), text: idx.map((i) => res.names[i]),
          marker: on ? {size: 10, color: colorOf(g), line: {color: "#ffffff", width: 2}}
            : {size: 10, color: "rgba(0,0,0,0)", line: {color: colorOf(g), width: 2}},
          hovertemplate: `%{text}<br>${g ? data.column + ": " + g : ""}${on ? "" : " · left out"}<extra></extra>`,
        });
      }
    }
    Plotly.react(el, traces, {
      font: {family: "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif", size: 12, color: "#3b444d"},
      paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)", height: 340,
      margin: {l: 50, r: 10, t: 10, b: 40},
      xaxis: {title: {text: `PC1 ${res.explained[0]} %`}, zeroline: false, gridcolor: "#eef0f3"},
      yaxis: {title: {text: `PC2 ${res.explained[1] ?? 0} %`}, zeroline: false, gridcolor: "#eef0f3"},
      legend: {orientation: "h", y: -0.2, font: {size: 11}},
    }, {displaylogo: false, responsive: true, modeBarButtonsToRemove: ["select2d", "lasso2d", "autoScale2d", "toggleSpikelines"]});
  }

  // ---- events
  form.addEventListener("change", (e) => {
    if (e.target.name === "column") {
      window.location.search = e.target.dataset.reload.replace(/^\?/, "") + encodeURIComponent(e.target.value);
      return;
    }
    if (e.target.id === "copy-from") {
      window.location.search = e.target.value ? "from=" + encodeURIComponent(e.target.value) : "";
      return;
    }
    update();
  });
  form.addEventListener("click", (e) => {
    const b = e.target.closest("[data-select]");
    if (!b) return;
    rows.forEach((r) => {
      const box = r.querySelector("input[name=samples]");
      if (box.disabled) return;
      if (b.dataset.select === "all") box.checked = true;
      if (b.dataset.select === "unflag" && r.dataset.flagged) box.checked = false;
    });
    update();
  });
  update();
})();
