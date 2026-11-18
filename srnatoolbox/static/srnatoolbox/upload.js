/* Chunked, resumable upload of read files into a dataset (server side: jobs/uploads.py).
 * Files are sent one after the other, in chunks; after a network error the upload asks the server how much it
 * has and continues from there. Choosing the same file again later continues an interrupted upload. */
(function () {
  "use strict";
  const root = document.getElementById("dataset");
  if (!root) return;
  const list = document.getElementById("upload-list");
  const csrf = (JSON.parse(document.body.getAttribute("hx-headers") || "{}"))["X-CSRFToken"] || "";
  const url = (key, token) => root.dataset[key].replace("TOKEN", encodeURIComponent(token));
  const queue = [];
  let busy = false;

  function fmtBytes(n) {
    const units = ["B", "KB", "MB", "GB", "TB"];
    let i = 0;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return (i ? n.toFixed(1) : n) + " " + units[i];
  }
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  function row(file) {
    const li = document.createElement("li");
    li.innerHTML = '<div class="d-flex justify-content-between gap-2"><span class="text-truncate"><code></code></span>' +
      '<span class="text-nowrap state">waiting</span></div>' +
      '<div class="progress" role="progressbar" aria-valuemin="0" aria-valuemax="100"><div class="progress-bar" style="width:0%"></div></div>';
    li.querySelector("code").textContent = file.webkitRelativePath || file.name;
    li.querySelector(".progress").setAttribute("aria-label", file.name);
    list.prepend(li);
    const empty = document.querySelector(".transfers-empty");
    if (empty) empty.remove();
    return li;
  }

  function show(item, text, pct, cls) {
    const state = item.el.querySelector(".state");
    state.textContent = text;
    state.className = "text-nowrap state" + (cls ? " " + cls : "");
    const bar = item.el.querySelector(".progress");
    if (pct === null) { if (bar) bar.remove(); return; }
    if (bar) bar.firstChild.style.width = pct + "%";
  }

  async function post(target, data) {
    const r = await fetch(target, {method: "POST", body: data, credentials: "same-origin",
      headers: {"X-CSRFToken": csrf}});
    return [r, await r.json().catch(() => ({}))];
  }

  async function uploadOne(item) {
    const file = item.file;
    const form = new FormData();
    form.append("name", file.name);
    form.append("size", file.size);
    form.append("dataset", root.dataset.slug);
    let r, info;
    try {
      [r, info] = await post(root.dataset.uploadStart, form);
    } catch (e) {
      show(item, "connection failed", null, "text-danger fw-semibold");
      return;
    }
    if (!r.ok) {
      show(item, info.error || "refused (" + r.status + ")", null, "text-danger");
      return;
    }
    const token = info.token, chunk = info.chunk_size;
    let offset = info.received || 0, tries = 0;
    const t0 = Date.now(), start = offset;
    while (offset < file.size) {
      const blob = file.slice(offset, Math.min(file.size, offset + chunk));
      try {
        const res = await fetch(url("uploadChunk", token) + "?offset=" + offset, {method: "PUT", body: blob,
          credentials: "same-origin", headers: {"X-CSRFToken": csrf, "Content-Type": "application/octet-stream"}});
        const data = await res.json().catch(() => ({}));
        if (res.ok) {
          offset = data.received;
          tries = 0;
        } else if (res.status === 409 && typeof data.received === "number") {
          offset = data.received;
        } else if (res.status >= 500 || res.status === 0) {
          throw new Error(data.error || "server error");
        } else {
          show(item, data.error || "refused (" + res.status + ")", null, "text-danger");
          return;
        }
      } catch (e) {
        tries++;
        if (tries > 8) {
          show(item, "connection lost: choose the file again to continue", null, "text-danger");
          return;
        }
        show(item, "connection problem, retrying…", Math.floor(100 * offset / file.size));
        await sleep(Math.min(30000, 1000 * 2 ** tries));
        try {
          const s = await fetch(url("uploadStatus", token), {credentials: "same-origin"});
          if (s.ok) offset = (await s.json()).received;
        } catch (e2) { /* next round */ }
        continue;
      }
      const secs = (Date.now() - t0) / 1000;
      const speed = secs > 0 ? (offset - start) / secs : 0;
      show(item, fmtBytes(offset) + " of " + fmtBytes(file.size) + (speed ? " · " + fmtBytes(speed) + "/s" : ""),
        Math.floor(100 * offset / file.size));
    }
    show(item, "checking…", 100);
    try {
      [r, info] = await post(url("uploadFinish", token), new FormData());
    } catch (e) {
      show(item, "connection failed while checking: choose the file again", null, "text-danger");
      return;
    }
    if (!r.ok) {
      show(item, info.message || info.error || "refused", null, "text-danger");
      return;
    }
    if (info.file_status === "unpacking") show(item, "uploaded · unpacking on the server", null, "text-success fw-semibold");
    else show(item, "✓ " + (info.format || "uploaded").replace(".", " · "), null, "text-success fw-semibold");
    htmx.trigger(document.body, "samples-changed");
    htmx.trigger(document.body, "transfers-changed");
  }

  async function run() {
    if (busy) return;
    busy = true;
    while (queue.length) await uploadOne(queue.shift());
    busy = false;
  }

  function add(files) {
    for (const file of files) {
      if (file.name.startsWith(".")) continue; // .DS_Store and similar
      queue.push({file, el: row(file)});
    }
    run();
  }

  // folders dropped onto the page: walk them
  function readEntry(entry, out) {
    return new Promise((resolve) => {
      if (entry.isFile) {
        entry.file((f) => { out.push(f); resolve(); }, () => resolve());
      } else if (entry.isDirectory) {
        const reader = entry.createReader();
        const all = [];
        const next = () => reader.readEntries(async (batch) => {
          if (!batch.length) {
            for (const e of all) await readEntry(e, out);
            resolve();
          } else { all.push(...batch); next(); }
        }, () => resolve());
        next();
      } else resolve();
    });
  }

  const zone = document.getElementById("drop-zone");
  ["dragenter", "dragover"].forEach((ev) => zone.addEventListener(ev, (e) => {
    e.preventDefault();
    zone.classList.add("drag");
  }));
  ["dragleave", "drop"].forEach((ev) => zone.addEventListener(ev, () => zone.classList.remove("drag")));
  zone.addEventListener("drop", async (e) => {
    e.preventDefault();
    const items = [...(e.dataTransfer.items || [])];
    const entries = items.map((i) => i.webkitGetAsEntry && i.webkitGetAsEntry()).filter(Boolean);
    if (entries.length) {
      const files = [];
      for (const entry of entries) await readEntry(entry, files);
      add(files);
    } else add(e.dataTransfer.files);
  });
  for (const id of ["pick-files", "pick-folder"]) {
    const input = document.getElementById(id);
    input.addEventListener("change", () => { add(input.files); input.value = ""; });
  }
  window.addEventListener("beforeunload", (e) => {
    if (busy) { e.preventDefault(); e.returnValue = ""; }
  });
})();
