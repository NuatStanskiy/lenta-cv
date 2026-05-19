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

// Full target CSV schema — same column order as csv_writer.FIELDNAMES on the pipeline.
const CSV_FIELDS = [
  "filename", "product_name", "price_default", "price_card", "price_discount",
  "barcode", "discount_amount", "id_sku", "print_datetime", "code",
  "additional_info", "color", "special_symbols", "frame_timestamp",
  "x_min", "y_min", "x_max", "y_max",
  "qr_code_barcode", "price1_qr", "price2_qr", "price3_qr", "price4_qr",
  "wholesale_level_1_count", "wholesale_level_1_price",
  "wholesale_level_2_count", "wholesale_level_2_price",
  "action_price_qr", "action_code_qr",
];

let currentFile = null;
let previewURL = null;
let lastRows = null;
let pipelineRotate = "none"; // updated from /api/health

// ── health ───────────────────────────────────────────────────────────────
async function checkHealth() {
  try {
    const r = await fetch("/api/health");
    const j = await r.json();
    if (j.ok && j.pipeline && j.pipeline.detector_ready) {
      pipelineRotate = j.pipeline.rotate || "none";
      healthEl.textContent = `pipeline ok · rotate=${pipelineRotate}`;
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
  csvBtn.disabled = true; // until /api/process succeeds we have nothing to download
  lastRows = null;
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
    csvBtn.disabled = lastRows.length === 0;
  } catch (e) {
    statusEl.textContent = `error: ${e.message}`;
    statusEl.className = "status err";
  } finally {
    goBtn.disabled = false;
  }
}

function csvEscape(v) {
  if (v == null || v === "") return "Нет";
  const s = String(v);
  return /[",\r\n]/.test(s) ? '"' + s.replaceAll('"', '""') + '"' : s;
}

function buildCSV(rows) {
  const lines = [CSV_FIELDS.join(",")];
  for (const r of rows) {
    lines.push(CSV_FIELDS.map((f) => csvEscape(r[f])).join(","));
  }
  return "\uFEFF" + lines.join("\r\n") + "\r\n";
}

function downloadCSV() {
  if (!lastRows || !lastRows.length) {
    statusEl.textContent = "сначала нажми «Обработать»";
    statusEl.className = "status err";
    return;
  }
  const csv = buildCSV(lastRows);
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  const stem = (currentFile?.name || "result").replace(/\.[^.]+$/, "");
  a.download = `${stem}.csv`;
  a.click();
  URL.revokeObjectURL(url);
  statusEl.textContent = `csv saved (${lastRows.length} строк)`;
  statusEl.className = "status ok";
}

// ── rendering ────────────────────────────────────────────────────────────
async function render() {
  await renderPreview();
  renderTable();
}

// Pick effective rotation by comparing bbox aspect to bitmap aspect.
// Browsers auto-rotate images/videos via EXIF/mp4-orientation, OpenCV usually doesn't —
// so even with pipelineRotate=ccw the displayed bitmap may already match bbox coords.
function effectiveRotation(bitmap, rows, pipelineRot) {
  if (!rows || !rows.length) return "none";
  const maxX = Math.max(...rows.map((r) => r.x_max));
  const maxY = Math.max(...rows.map((r) => r.y_max));
  if (!maxX || !maxY) return "none";
  const aspBbox = maxX / maxY;
  const aspBitmap = bitmap.width / bitmap.height;
  // aspects roughly equal → bbox already in displayed coord system, no transform
  if (Math.abs(aspBbox - aspBitmap) / Math.max(aspBbox, aspBitmap) < 0.15) return "none";
  // otherwise apply pipeline-side rotation inverse
  return pipelineRot || "ccw";
}

function mapBbox(r, rot, W, H) {
  // r contains x_min, y_min, x_max, y_max IN the pipeline-processed (post-rotate) frame.
  // We want canvas coords on a bitmap drawn in its NATURAL orientation (size W × H).
  if (rot === "ccw") {
    return { x: W - r.y_max, y: r.x_min, w: r.y_max - r.y_min, h: r.x_max - r.x_min };
  }
  if (rot === "cw") {
    return { x: r.y_min, y: H - r.x_max, w: r.y_max - r.y_min, h: r.x_max - r.x_min };
  }
  if (rot === "180") {
    return { x: W - r.x_max, y: H - r.y_max, w: r.x_max - r.x_min, h: r.y_max - r.y_min };
  }
  return { x: r.x_min, y: r.y_min, w: r.x_max - r.x_min, h: r.y_max - r.y_min };
}

async function renderPreview() {
  const isVideo = (currentFile.type || "").startsWith("video/");
  const ctx = canvas.getContext("2d");

  const bitmap = isVideo ? await frameFromVideo(previewURL) : await loadImageBitmap(previewURL);
  const W = bitmap.width;
  const H = bitmap.height;
  canvas.width = W;
  canvas.height = H;
  ctx.drawImage(bitmap, 0, 0);

  if (!lastRows) return;
  const rot = effectiveRotation(bitmap, lastRows, pipelineRotate);

  ctx.lineWidth = Math.max(3, Math.round(W / 600));
  ctx.font = `${Math.max(16, Math.round(W / 60))}px sans-serif`;
  ctx.textBaseline = "bottom";
  lastRows.forEach((r, i) => {
    const ok = !!r._llm_ok;
    ctx.strokeStyle = ok ? "#22c55e" : "#ef4444";
    ctx.fillStyle = ok ? "#22c55e" : "#ef4444";
    const b = mapBbox(r, rot, W, H);
    ctx.strokeRect(b.x, b.y, b.w, b.h);
    ctx.fillText(`#${i} ${r.price_card}`, b.x, b.y - 4);
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
  renderPreviewHighlight(idx);
}

async function renderPreviewHighlight(highlightIdx) {
  await renderPreview();
  const ctx = canvas.getContext("2d");
  const r = lastRows[highlightIdx];
  if (!r) return;
  const rot = effectiveRotation({ width: canvas.width, height: canvas.height }, lastRows, pipelineRotate);
  const b = mapBbox(r, rot, canvas.width, canvas.height);
  ctx.lineWidth = Math.max(8, Math.round(canvas.width / 250));
  ctx.strokeStyle = "#fbbf24";
  ctx.strokeRect(b.x, b.y, b.w, b.h);
}
