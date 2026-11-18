/* sRNAbench settings page: species combobox (results from species_search via HTMX), microRNA references and
 * microRNA references enabled according to the species, folded sections, copying an earlier job's settings. */
(function () {
  "use strict";
  const form = document.getElementById("srnabench-form");
  if (!form) return;
  const search = document.getElementById("species-search");
  const hits = document.getElementById("species-hits");
  const hidden = form.querySelector("input[name=species]");
  const chosen = document.getElementById("species-chosen");
  let active = -1;

  function applySpecies(dbs, genome) {
    for (const radio of form.querySelectorAll("input[name=mirdb]")) {
      const note = radio.parentElement.querySelector(".mirdb-note");
      let ok = radio.value === "own" ? genome : dbs.includes(radio.value);
      radio.disabled = !ok;
      radio.parentElement.classList.toggle("text-secondary", !ok);
      note.textContent = ok ? "" : (radio.value === "own" ? " (needs a genome)" : " (no entries for this species)");
    }
    const checked = form.querySelector("input[name=mirdb]:checked");
    if (!checked || checked.disabled) {
      const first = form.querySelector("input[name=mirdb]:not(:disabled)");
      if (first) first.checked = true;
    }
    toggleOwn();
  }

  function choose(li) {
    hidden.value = li.dataset.key;
    search.value = li.dataset.label;
    search.classList.remove("is-invalid");
    chosen.textContent = li.dataset.label + " · " + li.dataset.detail;
    chosen.classList.remove("text-secondary");
    document.getElementById("summary-species").textContent = li.dataset.label;
    applySpecies((li.dataset.dbs || "").split(",").filter(Boolean), Boolean(li.dataset.genome));
    close();
  }

  function items() { return [...hits.querySelectorAll(".species-hit")]; }
  function close() { hits.hidden = true; search.setAttribute("aria-expanded", "false"); active = -1; }
  function mark(i) {
    const list = items();
    if (!list.length) return;
    active = (i + list.length) % list.length;
    list.forEach((li, j) => li.classList.toggle("active", j === active));
    list[active].scrollIntoView({block: "nearest"});
  }

  document.body.addEventListener("htmx:afterSwap", (e) => {
    if (e.target === hits) {
      hits.hidden = false;
      search.setAttribute("aria-expanded", "true");
      active = -1;
    }
  });
  hits.addEventListener("mousedown", (e) => {
    const li = e.target.closest(".species-hit");
    if (li) { e.preventDefault(); choose(li); }
  });
  search.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); mark(active + 1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); mark(active - 1); }
    else if (e.key === "Enter") {
      e.preventDefault();
      const list = items();
      if (!hits.hidden && list.length) choose(list[Math.max(active, 0)]);
    } else if (e.key === "Escape") close();
  });
  search.addEventListener("blur", () => setTimeout(close, 150));

  function toggleOwn() {
    const own = form.querySelector("input[name=mirdb]:checked");
    document.getElementById("own-mirna").hidden = !(own && own.value === "own");
  }
  form.addEventListener("change", (e) => {
    if (e.target.name === "mirdb") toggleOwn();
    if (e.target.name === "protocol") document.getElementById("custom-adapter").hidden = e.target.value !== "custom";
  });
  const copy = document.getElementById("copy-from");
  if (copy) copy.addEventListener("change", () => {
    window.location.search = copy.dataset.base.replace(/^\?/, "") + (copy.value ? "&from=" + encodeURIComponent(copy.value) : "");
  });
  form.addEventListener("submit", (e) => {
    if (!hidden.value) {
      e.preventDefault();
      search.classList.add("is-invalid");
      chosen.textContent = "Please choose a species from the list.";
      chosen.className = "small mt-1 text-danger";
      search.focus();
    }
  });
  if (chosen.dataset.set) applySpecies((chosen.dataset.dbs || "").split(",").filter(Boolean), Boolean(chosen.dataset.genome));
})();
