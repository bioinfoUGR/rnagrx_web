/* Dataset page: sample selection, filter, editing of names and annotations (saved at once), pasting a column
 * from a spreadsheet, copy link. The samples table is re-rendered by HTMX when files arrive; the selection
 * survives (samples you untick stay unticked, new samples are ticked). */
(function () {
  "use strict";
  const root = document.getElementById("dataset");
  if (!root) return;
  const csrf = (JSON.parse(document.body.getAttribute("hx-headers") || "{}"))["X-CSRFToken"] || "";
  const unticked = new Set();
  let filter = "";

  function fmtBytes(n) {
    const units = ["B", "KB", "MB", "GB", "TB"];
    let i = 0;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return (i ? n.toFixed(1) : n) + " " + units[i];
  }
  const rows = () => [...document.querySelectorAll("#sample-table tbody tr")];
  const box = (tr) => tr.querySelector("input[name=s]");

  function summary() {
    let n = 0, size = 0;
    const all = rows();
    for (const tr of all) {
      if (box(tr).checked) { n++; size += Number(tr.dataset.size || 0); }
    }
    document.getElementById("sel-count").textContent = n;
    document.getElementById("sel-total").textContent = all.length;
    document.getElementById("sel-size").textContent = fmtBytes(size);
    document.getElementById("run-button").disabled = n === 0;
    const head = document.getElementById("select-all");
    if (head) {
      head.checked = n > 0 && n === all.length;
      head.indeterminate = n > 0 && n < all.length;
    }
  }

  function applyFilter() {
    const terms = filter.toLowerCase().split(/\s+/).filter(Boolean);
    for (const tr of rows()) {
      const text = tr.dataset.search || "";
      tr.hidden = !terms.every((t) => text.includes(t));
    }
  }

  function setup() {
    for (const tr of rows()) box(tr).checked = !unticked.has(tr.dataset.id);
    const f = document.getElementById("sample-filter");
    if (f) f.value = filter;
    applyFilter();
    summary();
  }

  document.addEventListener("change", (e) => {
    const t = e.target;
    if (t.matches("#sample-table input[name=s]")) {
      const id = t.closest("tr").dataset.id;
      if (t.checked) unticked.delete(id); else unticked.add(id);
      summary();
    } else if (t.id === "select-all") {
      for (const tr of rows()) {
        if (tr.hidden) continue;
        box(tr).checked = t.checked;
        if (t.checked) unticked.delete(tr.dataset.id); else unticked.add(tr.dataset.id);
      }
      summary();
    } else if (t.matches("#sample-table input.cell")) {
      save([edit(t)]);
    }
  });

  document.addEventListener("input", (e) => {
    if (e.target.id === "sample-filter") { filter = e.target.value; applyFilter(); }
  });

  document.addEventListener("click", (e) => {
    const sel = e.target.closest("[data-select]");
    if (sel) {
      e.preventDefault();
      for (const tr of rows()) {
        const on = sel.dataset.select === "all" || (sel.dataset.select === "visible" && !tr.hidden);
        box(tr).checked = on;
        if (on) unticked.delete(tr.dataset.id); else unticked.add(tr.dataset.id);
      }
      summary();
    }
    const copy = e.target.closest("[data-copy]");
    if (copy && navigator.clipboard) {
      navigator.clipboard.writeText(copy.dataset.copy).then(() => {
        const old = copy.textContent;
        copy.textContent = "Link copied";
        setTimeout(() => { copy.textContent = old; }, 1500);
      });
    }
  });

  // ---- editing
  function columns() {
    const el = document.getElementById("sample-columns");
    return el ? JSON.parse(el.textContent) : [];
  }

  function edit(input) {
    const e = {sample: input.closest("tr").dataset.id, field: input.dataset.field, value: input.value};
    if (input.dataset.field === "column") e.column = columns()[Number(input.dataset.column)];
    e.input = input;
    return e;
  }

  async function save(edits) {
    const status = document.getElementById("cell-status");
    const body = JSON.stringify({edits: edits.map(({input, ...rest}) => rest)});
    let data;
    try {
      const r = await fetch(root.dataset.cells, {method: "POST", body, credentials: "same-origin",
        headers: {"X-CSRFToken": csrf, "Content-Type": "application/json"}});
      data = await r.json();
      if (!r.ok) throw new Error(data.error || "error");
    } catch (err) {
      status.textContent = "Not saved: connection problem. Please try again.";
      status.className = "small text-danger ms-auto";
      return;
    }
    let bad = 0;
    for (const e of edits) {
      const key = e.sample + ":" + e.field + (e.field === "column" ? ":" + e.column : "");
      const msg = data.errors[key];
      e.input.classList.toggle("is-invalid", Boolean(msg));
      e.input.title = msg || "";
      if (msg) bad++;
      else if (e.field === "name") e.input.defaultValue = e.input.value;
    }
    if (bad) {
      status.textContent = bad === 1 ? Object.values(data.errors)[0] : bad + " values not saved (see the marked cells).";
      status.className = "small text-danger ms-auto";
    } else {
      status.textContent = edits.length === 1 ? "Saved." : edits.length + " values saved.";
      status.className = "small text-success ms-auto";
    }
  }

  // pasting several lines (and tab-separated columns) from a spreadsheet fills the cells below/right
  document.addEventListener("paste", (e) => {
    const input = e.target;
    if (!input.matches || !input.matches("#sample-table input.cell")) return;
    const text = (e.clipboardData || window.clipboardData).getData("text");
    if (!/[\n\t]/.test(text.trim())) return;
    e.preventDefault();
    const grid = text.replace(/\r/g, "").replace(/\n$/, "").split("\n").map((l) => l.split("\t"));
    const visible = rows().filter((tr) => !tr.hidden);
    const startRow = visible.indexOf(input.closest("tr"));
    const cellsOf = (tr) => [...tr.querySelectorAll("input.cell")];
    const startCol = cellsOf(input.closest("tr")).indexOf(input);
    const edits = [];
    grid.forEach((values, i) => {
      const tr = visible[startRow + i];
      if (!tr) return;
      const cells = cellsOf(tr);
      values.forEach((v, j) => {
        const cell = cells[startCol + j];
        if (!cell) return;
        cell.value = v.trim();
        edits.push(edit(cell));
      });
    });
    if (edits.length) save(edits);
  });

  document.body.addEventListener("htmx:afterSwap", (e) => {
    if (e.target.id === "samples" || (e.detail && e.detail.target && e.detail.target.id === "samples")) setup();
  });
  document.body.addEventListener("htmx:load", (e) => {
    if (e.target.id === "samples") setup();
  });
  setup();
})();
