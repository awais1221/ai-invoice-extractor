// AI Invoice Extractor - front-end (plain JavaScript, no framework)
(() => {
  "use strict";

  const ALLOWED = [".pdf", ".jpg", ".jpeg", ".png"];
  let maxMb = 10;
  let selected = [];

  const $ = (id) => document.getElementById(id);
  const dropzone = $("dropzone");
  const fileInput = $("file-input");
  const fileList = $("file-list");
  const analyzeBtn = $("analyze-btn");
  const clearBtn = $("clear-btn");
  const resultsCard = $("results-card");
  const resultsBody = $("results-body");
  const downloadBtn = $("download-btn");

  // ---------- helpers ----------
  const escapeHtml = (value) =>
    String(value ?? "").replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

  const formatSize = (bytes) =>
    bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(0)} KB` : `${(bytes / 1048576).toFixed(1)} MB`;

  const formatMoney = (value, currency) => {
    if (value === null || value === undefined) return "—";
    const num = Number(value).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return currency ? `${num} ${currency}` : num;
  };

  const extOf = (name) => (name.match(/\.[^.]+$/) || [""])[0].toLowerCase();

  let toastTimer;
  function toast(message, isError = false) {
    const el = $("toast");
    el.textContent = message;
    el.className = "toast" + (isError ? " error" : "");
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (el.hidden = true), 4500);
  }

  // ---------- server status ----------
  async function checkHealth() {
    const pill = $("llm-status");
    try {
      const res = await fetch("/health");
      const data = await res.json();
      maxMb = data.max_file_mb || maxMb;
      $("max-mb").textContent = maxMb;
      if (data.llm_configured) {
        pill.textContent = `● LLM ready (${data.model})`;
        pill.className = "pill pill-ok";
      } else {
        pill.textContent = "● LLM not configured (.env)";
        pill.className = "pill pill-bad";
      }
    } catch {
      pill.textContent = "● Server unreachable";
      pill.className = "pill pill-bad";
    }
  }

  // ---------- file selection ----------
  function addFiles(files) {
    for (const file of files) {
      if (!ALLOWED.includes(extOf(file.name))) {
        toast(`"${file.name}" skipped: only PDF, JPG and PNG are supported.`, true);
        continue;
      }
      if (file.size === 0) {
        toast(`"${file.name}" skipped: the file is empty.`, true);
        continue;
      }
      if (file.size > maxMb * 1024 * 1024) {
        toast(`"${file.name}" skipped: larger than ${maxMb} MB.`, true);
        continue;
      }
      if (!selected.some((f) => f.name === file.name && f.size === file.size)) {
        selected.push(file);
      }
    }
    renderFileList();
  }

  function renderFileList() {
    fileList.innerHTML = selected
      .map((f, i) => `<li><span>📄 ${escapeHtml(f.name)}<span class="size">${formatSize(f.size)}</span></span>
        <button class="remove" data-index="${i}" title="Remove">×</button></li>`)
      .join("");
    analyzeBtn.disabled = selected.length === 0;
    clearBtn.disabled = selected.length === 0;
    analyzeBtn.querySelector(".label").textContent =
      selected.length > 1 ? `Analyze ${selected.length} invoices` : "Analyze invoice";
  }

  fileList.addEventListener("click", (e) => {
    const btn = e.target.closest(".remove");
    if (!btn) return;
    selected.splice(Number(btn.dataset.index), 1);
    renderFileList();
  });

  dropzone.addEventListener("click", () => fileInput.click());
  dropzone.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); }
  });
  fileInput.addEventListener("change", () => { addFiles(fileInput.files); fileInput.value = ""; });

  ["dragenter", "dragover"].forEach((evt) =>
    dropzone.addEventListener(evt, (e) => { e.preventDefault(); dropzone.classList.add("dragover"); }));
  ["dragleave", "drop"].forEach((evt) =>
    dropzone.addEventListener(evt, (e) => { e.preventDefault(); dropzone.classList.remove("dragover"); }));
  dropzone.addEventListener("drop", (e) => addFiles(e.dataTransfer.files));

  clearBtn.addEventListener("click", () => { selected = []; renderFileList(); });

  // ---------- analyze ----------
  function setLoading(loading) {
    analyzeBtn.disabled = loading || selected.length === 0;
    clearBtn.disabled = loading;
    analyzeBtn.querySelector(".spinner").hidden = !loading;
    if (loading) analyzeBtn.querySelector(".label").textContent = "Analyzing… this can take a minute";
  }

  analyzeBtn.addEventListener("click", async () => {
    if (!selected.length) return;
    const form = new FormData();
    selected.forEach((f) => form.append("files", f));

    setLoading(true);
    try {
      const res = await fetch("/analyze-batch", { method: "POST", body: form });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || `Request failed (${res.status})`);
      renderResults(data);
      selected = [];
    } catch (err) {
      toast(err.message || "Something went wrong.", true);
    } finally {
      setLoading(false);
      renderFileList();
    }
  });

  // ---------- results ----------
  function lineItemsTable(items, currency) {
    if (!items.length) return "<em class='muted'>No line items found.</em>";
    const rows = items.map((it) => `<tr>
        <td>${escapeHtml(it.description) || "—"}</td>
        <td class="num">${it.quantity ?? "—"}</td>
        <td class="num">${formatMoney(it.unit_price)}</td>
        <td class="num">${formatMoney(it.line_total, currency)}</td></tr>`).join("");
    return `<table class="items"><thead><tr><th>Description</th><th class="num">Qty</th>
      <th class="num">Unit price</th><th class="num">Line total</th></tr></thead><tbody>${rows}</tbody></table>`;
  }

  function renderResults(batch) {
    resultsBody.innerHTML = batch.results.map((r, i) => {
      if (r.status === "error" || !r.data) {
        return `<tr class="row-err"><td><button class="toggle" disabled>▶</button></td>
          <td>${escapeHtml(r.filename)}</td><td colspan="4">—</td>
          <td><span class="badge badge-err">FAILED</span></td>
          <td class="notes">${escapeHtml(r.error)}</td></tr>`;
      }
      const d = r.data;
      const badge = d.totals_match
        ? '<span class="badge badge-ok">✔ Match</span>'
        : '<span class="badge badge-bad">⚠ Mismatch</span>';
      return `<tr class="${d.totals_match ? "" : "row-bad"}">
          <td><button class="toggle" data-row="${i}" title="Show line items">▶</button></td>
          <td>${escapeHtml(r.filename)}</td>
          <td>${escapeHtml(d.vendor_name) || "—"}</td>
          <td class="nowrap">${escapeHtml(d.invoice_number) || "—"}</td>
          <td class="nowrap">${escapeHtml(d.invoice_date) || "—"}</td>
          <td class="num">${formatMoney(d.total_amount, escapeHtml(d.currency))}</td>
          <td>${badge}</td>
          <td class="notes">${escapeHtml(d.validation_notes)}</td></tr>
        <tr class="details" id="details-${i}" hidden><td colspan="8">
          ${lineItemsTable(d.line_items, escapeHtml(d.currency))}</td></tr>`;
    }).join("");

    const mismatches = batch.results.filter((r) => r.data && !r.data.totals_match).length;
    $("summary").textContent =
      `${batch.succeeded} of ${batch.total_files} processed` +
      (batch.failed ? ` · ${batch.failed} failed` : "") +
      (mismatches ? ` · ${mismatches} with totals mismatch` : "");

    downloadBtn.href = `/export-excel?batch_id=${encodeURIComponent(batch.batch_id)}`;
    resultsCard.hidden = false;
    resultsCard.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  resultsBody.addEventListener("click", (e) => {
    const btn = e.target.closest(".toggle");
    if (!btn || btn.disabled) return;
    const details = $(`details-${btn.dataset.row}`);
    details.hidden = !details.hidden;
    btn.textContent = details.hidden ? "▶" : "▼";
  });

  checkHealth();
})();
