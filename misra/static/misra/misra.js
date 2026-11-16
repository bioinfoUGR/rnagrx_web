// Small helpers for the miSRA browse pages (the rest is server-rendered + htmx).
(function () {
  "use strict";
  var FACET_KEY = "misra.openFacets";

  function readOpen() {
    try { return JSON.parse(sessionStorage.getItem(FACET_KEY) || "{}"); } catch (e) { return {}; }
  }
  function writeOpen(state) {
    try { sessionStorage.setItem(FACET_KEY, JSON.stringify(state)); } catch (e) { /* storage blocked */ }
  }

  // Keep the facets the user opened or closed in that state across htmx refreshes.
  function restoreFacets(root) {
    var state = readOpen();
    root.querySelectorAll("details.facet[data-facet]").forEach(function (d) {
      var key = d.dataset.facet;
      if (key in state && !d.querySelector("input:checked")) { d.open = state[key]; }
      d.addEventListener("toggle", function () {
        var s = readOpen(); s[key] = d.open; writeOpen(s);
      });
    });
  }

  function updateTicked(form) {
    var n = form.querySelectorAll(".row-select:checked").length;
    form.querySelectorAll("[data-ticked-count]").forEach(function (el) { el.textContent = n; });
    form.querySelectorAll("[data-needs-ticked]").forEach(function (b) { b.disabled = n === 0; });
  }

  function initSelection(root) {
    var outer = root.closest && root.closest(".selection-form");  // a part of a form was swapped
    if (outer) { updateTicked(outer); }
    root.querySelectorAll(".selection-form").forEach(function (form) {
      updateTicked(form);
    });
  }

  document.addEventListener("change", function (ev) {
    var t = ev.target;
    var form = t.closest && t.closest(".selection-form");
    if (!form) { return; }
    if (t.matches("[data-select-all]")) {
      form.querySelectorAll(".row-select").forEach(function (c) { c.checked = t.checked; });
    }
    if (t.matches("[data-select-all], .row-select")) { updateTicked(form); }
  });

  // Toasts disappear after a few seconds.
  document.addEventListener("htmx:afterSwap", function (ev) {
    if (ev.detail.target && ev.detail.target.id === "toasts") {
      var msg = ev.detail.target.querySelector(".toast-msg");
      if (msg) { setTimeout(function () { msg.remove(); }, 6000); }
    }
  });
  document.addEventListener("htmx:afterSettle", function (ev) {
    restoreFacets(ev.detail.elt);
    initSelection(ev.detail.elt);
  });
  // Show server errors instead of silently doing nothing.
  document.addEventListener("htmx:responseError", function (ev) {
    var area = document.getElementById("toasts");
    if (!area) { return; }
    var div = document.createElement("div");
    div.className = "toast-msg warning";
    div.textContent = "Something went wrong (" + ev.detail.xhr.status + "). Please reload the page.";
    area.prepend(div);
    setTimeout(function () { div.remove(); }, 8000);
  });
  document.addEventListener("DOMContentLoaded", function () {
    restoreFacets(document);
    initSelection(document);
  });
})();
