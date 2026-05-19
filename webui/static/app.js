const drop = document.getElementById("drop");
const fileInput = document.getElementById("file");
const pickBtn = document.getElementById("pick");
const goBtn = document.getElementById("go");
const csvBtn = document.getElementById("csv");
const statusEl = document.getElementById("status");
const pickedEl = document.getElementById("picked");
const healthEl = document.getElementById("health");
const resultsEl = document.getElementById("results");
const countEl = document.getElementById("count");
const canvas = document.getElementById("canvas");
const tbody = document.querySelector("#rows tbody");
const thead = document.querySelector("#rows thead");

const LLM_FIELDS = [
  "product_name", "price_card", "price_default", "price_discount",
  "discount_amount", "color", "special_symbols", "barcode",
  "id_sku", "print_datetime", "code", "additional_info",
];

let currentFile = null;
let previewURL = null;
let lastRows = null;

// ── health ───────────────────────────────────────────────────────────────
async function checkHealth() {
  try {
    const r = await fetch("/api/health");
    const j = await r.json();
    if (j.ok && j.pipeline && j.pipeline.detector_ready) {
      healthEl.textContent = `pipeline ok · ${j.pipeline_url}`;
      healthEl.className = "health ok";
    } else {
      healthEl.textContent = `pipeline: ${j.error || "not ready"}`;
      healthEl.className = "health err";
    }
  } catch (e) {
    healthEl.textContent = "pipeline: unreachable";
    healthEl.className = "health err";
  }
}
checkHealth();
setInterval(checkHealth, 15000);

// ── file picker / drag-drop ──────────────────────────────────────────────
pickBtn.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", () => setFile(fileInput.files[0]));

["dragenter", "dragover"].forEach((ev) =>
  drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("drag"); })
);
["dragleave", "drop"].forEach((ev) =>
  drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("drag"); })
);
drop.addEventListener("drop", (e) => {
  const f = e.dataTransfer.files[0];
  if (f) setFile(f);
});

function setFile(f) {
  if (!f) return;
  currentFile = f;
  pickedEl.textContent = `${f.name} · ${(f.size / 1024 / 1024).toFixed(1)} MB · ${f.type || "?"}`;
  goBtn.disabled = false;
  csvBtn.disabled = false;
  if (previewURL) URL.revokeObjectURL(previewURL);
  previewURL = URL.createObjectURL(f);
  resultsEl.hidden = true;
  statusEl.textContent = "";
  statusEl.className = "status";
}

// ── process ──────────────────────────────────────────────────────────────
goBtn.addEventListener("click", () => process());
csvBtn.addEventListener("click", () => downloadCSV());

async function process() {
  if (!currentFile) return;
  goBtn.disabled = true; csvBtn.disabled = true;
  statusEl.innerHTML = '<span class="spinner"></span>processing…';
  statusEl.className = "status";

  const fd = new FormData();
  fd.append("file", currentFile);
  const t0 = performance.now();
  try {
    const r = await fetch("/api/process", { method: "POST", body: fd });
    const j = await r.json();
    const dt = ((performance.now() - t0) / 1000).toFixed(1);
    if (!r.ok || !j.ok) {
      statusEl.textContent = `error: ${j.detail || j.error || r.statusText}`;
      statusEl.className = "status err";
      return;
    }
    lastRows = j.rows || [];
    statusEl.textContent = `ok · ${lastRows.length} ценников · ${dt}s`;
    statusEl.className = "status ok";
    countEl.textContent = `(${lastRows.length})`;
    await render();
    resultsEl.hidden = false;
  } catch (e) {
    statusEl.textContent = `error: ${e.message}`;
    statusEl.className = "status err";
  } finally {
    goBtn.disabled = false; csvBtn.disabled = false;
  }
}

async function downloadCSV() {
  if (!currentFile) return;
  csvBtn.disabled = true;
  const prev = statusEl.innerHTML;
  statusEl.innerHTML = '<span class="spinner"></span>downloading CSV…';
  statusEl.className = "status";
  const fd = new FormData();
  fd.append("file", currentFile);
  try {
    const r = await fetch("/api/process_csv", { method: "POST", body: fd });
    if (!r.ok) {
      statusEl.textContent = `error: ${r.statusText}`;
      statusEl.className = "status err";
      return;
    }
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    const stem = currentFile.name.replace(/\.[^.]+$/, "");
    a.download = `${stem}.csv`;
    a.click();
    URL.revokeObjectURL(url);
    statusEl.textContent = "csv saved";
    statusEl.className = "status ok";
  } catch (e) {
    statusEl.textContent = `error: ${e.message}`;
    statusEl.className = "status err";
  } finally {
    csvBtn.disabled = false;
  }
}

// ── rendering ────────────────────────────────────────────────────────────
async function render() {
  await renderPreview();
  renderTable();
}

async function renderPreview() {
  const isVideo = (currentFile.type || "").startsWith("video/");
  const ctx = canvas.getContext("2d");

  let bitmap;
  if (isVideo) {
    bitmap = await frameFromVideo(previewURL);
  } else {
    bitmap = await loadImageBitmap(previewURL);
  }

  // Pipeline rotates CCW (90°), so width/height swap when comparing bbox to source.
  // bbox is in rotated coordinates: x ∈ [0, original_h], y ∈ [0, original_w]
  // We rotate the preview canvas to match the rotated frame the pipeline saw.
  const rotW = bitmap.height;
  const rotH = bitmap.width;
  canvas.width = rotW;
  canvas.height = rotH;
  ctx.save();
  ctx.translate(0, rotH);
  ctx.rotate(-Math.PI / 2);
  ctx.drawImage(bitmap, 0, 0);
  ctx.restore();

  if (!lastRows) return;
  ctx.lineWidth = Math.max(3, Math.round(rotW / 600));
  ctx.font = `${Math.max(16, Math.round(rotW / 60))}px sans-serif`;
  ctx.textBaseline = "bottom";
  lastRows.forEach((r, i) => {
    const ok = !!r._llm_ok;
    ctx.strokeStyle = ok ? "#22c55e" : "#ef4444";
    ctx.fillStyle = ok ? "#22c55e" : "#ef4444";
    const x = r.x_min, y = r.y_min, w = r.x_max - r.x_min, h = r.y_max - r.y_min;
    ctx.strokeRect(x, y, w, h);
    const label = `#${i} ${r.price_card}`;
    ctx.fillText(label, x, y - 4);
  });
}

function loadImageBitmap(url) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = reject;
    img.src = url;
  });
}

function frameFromVideo(url) {
  return new Promise((resolve, reject) => {
    const v = document.createElement("video");
    v.preload = "auto";
    v.muted = true;
    v.src = url;
    v.addEventListener("loadeddata", () => {
      // seek to mid-frame for a representative shot
      v.currentTime = Math.min(v.duration * 0.5, 5);
    }, { once: true });
    v.addEventListener("seeked", () => {
      const c = document.createElement("canvas");
      c.width = v.videoWidth;
      c.height = v.videoHeight;
      c.getContext("2d").drawImage(v, 0, 0);
      const out = new Image();
      out.onload = () => resolve(out);
      out.src = c.toDataURL("image/jpeg");
    }, { once: true });
    v.addEventListener("error", reject);
  });
}

function renderTable() {
  thead.innerHTML = "";
  tbody.innerHTML = "";

  const cols = ["#", ...LLM_FIELDS];
  const trh = document.createElement("tr");
  cols.forEach((c) => {
    const th = document.createElement("th");
    th.textContent = c;
    trh.appendChild(th);
  });
  thead.appendChild(trh);

  lastRows.forEach((r, i) => {
    const tr = document.createElement("tr");
    if (!r._llm_ok) tr.classList.add("fail");
    tr.dataset.idx = i;
    const idxTd = document.createElement("td");
    idxTd.className = "idx";
    idxTd.textContent = `#${i}`;
    tr.appendChild(idxTd);
    LLM_FIELDS.forEach((f) => {
      const td = document.createElement("td");
      const v = r[f];
      td.textContent = v ?? "";
      if (String(v).toLowerCase() === "нет" || v == null || v === "") {
        td.className = "empty";
      }
      tr.appendChild(td);
    });
    tr.addEventListener("mouseenter", () => highlight(i, true));
    tr.addEventListener("mouseleave", () => highlight(i, false));
    tbody.appendChild(tr);
  });
}

function highlight(idx, on) {
  document.querySelectorAll("#rows tbody tr").forEach((tr) => tr.classList.remove("hl"));
  if (!on || !lastRows) return renderPreview();
  document.querySelector(`#rows tbody tr[data-idx="${idx}"]`)?.classList.add("hl");
  // re-render preview emphasising this row
  renderPreviewHighlight(idx);
}

async function renderPreviewHighlight(highlightIdx) {
  await renderPreview();
  const ctx = canvas.getContext("2d");
  const r = lastRows[highlightIdx];
  if (!r) return;
  ctx.lineWidth = Math.max(8, Math.round(canvas.width / 250));
  ctx.strokeStyle = "#fbbf24";
  ctx.strokeRect(r.x_min, r.y_min, r.x_max - r.x_min, r.y_max - r.y_min);
}
