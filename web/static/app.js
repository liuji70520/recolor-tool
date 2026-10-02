"use strict";

const $ = (id) => document.getElementById(id);
const HEX_RE = /^#[0-9a-fA-F]{3,8}$/;
const SHOW_MAX = 300;
const state = {
  id: null,
  kind: null,
  fileName: null,
  before: null,
  after: null,
  view: "before",
  hlColor: null,
  colors: [],
  total: 0,
  background: null,
};
const BADGE_TEXT = { fill: "填充", stroke: "描边", "fill+stroke": "填充+描边", pixel: "像素" };

const norm = (h) => (h || "").trim().toLowerCase();

function showStatus(msg, isErr) {
  const el = $("status");
  el.textContent = msg;
  el.className = "status" + (isErr ? " error" : "");
}

function setSeg(view) {
  state.view = view;
  document.querySelectorAll("#viewSeg .seg-btn").forEach((b) => {
    b.classList.toggle("active", b.dataset.view === view);
  });
}

function setAfterAvailable(yes) {
  const b = document.querySelector('#viewSeg .seg-btn[data-view="after"]');
  b.disabled = !yes;
  if (!yes && state.view === "after") setSeg("before");
}

function refreshView() {
  const src = state.view === "after" && state.after ? state.after : state.before;
  if (!src) return;
  $("viewImg").src = src;
  $("placeholder").hidden = true;
}

async function pollStatus(id, onProgress, timeoutMs = 600000) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    const r = await fetch(`/api/status/${id}`);
    const j = await r.json();
    if (j.progress && onProgress) onProgress(j.progress);
    if (j.status === "ready") return j;
    if (j.status === "error") throw new Error(j.error || "处理失败");
    await new Promise((res) => setTimeout(res, 400));
  }
  throw new Error("处理超时");
}

/* ---------- 拖拽绑定（文件上传 / 映射文件共用） ---------- */

function bindDrop(zone, input, onFile) {
  ["dragover", "dragenter"].forEach((ev) =>
    zone.addEventListener(ev, (e) => {
      e.preventDefault();
      zone.classList.add("dragover");
    })
  );
  ["dragleave", "drop"].forEach((ev) =>
    zone.addEventListener(ev, (e) => {
      e.preventDefault();
      zone.classList.remove("dragover");
    })
  );
  zone.addEventListener("drop", (e) => {
    const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (f) onFile(f);
  });
  zone.addEventListener("click", () => input.click());
  input.addEventListener("change", () => {
    if (input.files && input.files[0]) onFile(input.files[0]);
    input.value = "";
  });
}

/* ---------- 主文件上传（拖拽 + 进度 + 异步解析） ---------- */

bindDrop($("fileDrop"), $("file"), upload);

function upload(file) {
  if (!/\.(svg|pdf|png|jpe?g|gif|bmp|tiff?|webp)$/i.test(file.name)) {
    alert("不支持该文件类型");
    return;
  }
  const fd = new FormData();
  fd.append("file", file);
  $("fileInfo").hidden = false;
  $("fileInfo").textContent = `上传中 0%`;
  showStatus("");
  $("exportFmt").hidden = true;
  $("download").hidden = true;

  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/upload");
  xhr.upload.onprogress = (e) => {
    if (e.lengthComputable) {
      $("fileInfo").textContent = `上传中 ${Math.round((e.loaded / e.total) * 100)}%`;
    }
  };
  xhr.onload = async () => {
    if (xhr.status !== 200) {
      let msg = "上传失败";
      try {
        msg = JSON.parse(xhr.responseText).error || msg;
      } catch (_) {}
      $("fileInfo").textContent = msg;
      return;
    }
    const j = JSON.parse(xhr.responseText);
    state.id = j.id;
    state.kind = j.kind;
    state.outNameTouched = false;
    $("fileInfo").textContent = `${j.name} · 解析中…`;
    try {
      const s = await pollStatus(j.id, (p) => {
        const msg = p.message ? ` · ${p.message}` : "";
        $("fileInfo").textContent = `${j.name} · 解析中 ${p.percent}%${msg}`;
      });
      onScanReady(j.name, s.scan);
    } catch (err) {
      $("fileInfo").textContent = "解析失败：" + err.message;
    }
  };
  xhr.onerror = () => {
    $("fileInfo").textContent = "网络错误，上传失败";
  };
  xhr.send(fd);
}

function onScanReady(fileName, scan) {
  state.fileName = fileName;
  state.before = scan.preview;
  state.after = null;
  state.colors = scan.colors;
  state.total = scan.total;
  state.background = scan.background || null;
  const pages = scan.pages
    ? scan.pages > 1
      ? ` · 共 ${scan.pages} 页（预览第 1 页）`
      : ""
    : "";
  $("fileInfo").textContent = `${fileName} · 共 ${scan.total} 种颜色${pages}`;
  $("mapPanel").hidden = false;
  $("warnbox").hidden = true;
  $("batchArea").value = "";
  cancelHighlight();
  renderRows(scan.colors, scan.total);
  setSeg("before");
  setAfterAvailable(false);
  refreshView();

  const m = fileName.match(/(.+)\.([^.]+)$/);
  $("outName").disabled = false;
  $("outName").value = m ? `${m[1]}-new` : `${fileName}-new`;
  setupExportFormats();
  if (state.background) {
    $("bgLabel").textContent = state.background;
    markBackgroundRows();
  } else {
    detectBackground(scan.preview).then((bg) => {
      state.background = bg;
      $("bgLabel").textContent = bg;
      markBackgroundRows();
    });
  }
  refreshHistory();
}

/* ---------- 导出格式（PDF / SVG / PNG / 颜色代码） ---------- */

function setupExportFormats() {
  const sel = $("exportFmt");
  const table = {
    pdf: [["pdf", "PDF"], ["svg", "SVG"], ["png", "PNG"], ["map-csv", "颜色代码 CSV"], ["map-json", "颜色代码 JSON"]],
    svg: [["svg", "SVG"], ["png", "PNG"], ["pdf", "PDF"], ["map-csv", "颜色代码 CSV"], ["map-json", "颜色代码 JSON"]],
  };
  const list = table[state.kind] || [["png", "PNG"], ["map-csv", "颜色代码 CSV"], ["map-json", "颜色代码 JSON"]];
  sel.innerHTML = "";
  list.forEach(([v, label]) => {
    const o = document.createElement("option");
    o.value = v;
    o.textContent = label;
    sel.appendChild(o);
  });
  sel.hidden = false;
}

function exportBaseName() {
  const v = $("outName").value.trim();
  if (v) return v;
  return (state.fileName || "recolored").replace(/\.[^.]+$/, "") + "-new";
}

function exportMapping(fmt) {
  const map = {};
  document.querySelectorAll("#mapBody .mrow").forEach((row) => {
    const old = norm(row.querySelector(".oldhex").value);
    const neu = norm(row.querySelector(".newhex").value);
    if (!HEX_RE.test(old) || !HEX_RE.test(neu)) return;
    map[old] = neu;
  });
  const keys = Object.keys(map);
  if (!keys.length) {
    showStatus("没有可导出的颜色映射", true);
    return;
  }
  let content, mime, extName;
  if (fmt === "map-json") {
    content = JSON.stringify(map, null, 2);
    mime = "application/json";
    extName = "json";
  } else {
    const head = ["原颜色", "替换颜色"];
    const body = keys.map((k) => `"${k}","${map[k]}"`);
    content = "\ufeff" + [head.join(","), ...body].join("\r\n");
    mime = "text/csv;charset=utf-8";
    extName = "csv";
  }
  const base = (state.fileName || "mapping").replace(/\.[^.]+$/, "") + "-mapping";
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${base}.${extName}`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
  showStatus(`已导出 ${keys.length} 组颜色映射 → ${a.download}`);
}

/* ---------- 颜色映射表 ---------- */

function renderRows(colors, total) {
  const body = $("mapBody");
  body.innerHTML = "";
  const maxCount = colors.length ? Math.max(...colors.map((c) => c.count)) : 0;
  let minorCount = 0;
  colors.slice(0, SHOW_MAX).forEach((c) => {
    const row = makeRow(c.hex, c.hex, false, c);
    if (isMinorColor(c, maxCount)) {
      row.dataset.minor = "1";
      minorCount++;
    }
    body.appendChild(row);
  });
  applyMinorFilter();
  const note = document.createElement("div");
  note.className = "note";
  note.id = "mapNote";
  body.appendChild(note);
  updateMapNote(total, minorCount);
  markBackgroundRows();
}

function markBackgroundRows() {
  if (!state.background) return;
  document.querySelectorAll("#mapBody .mrow").forEach((row) => {
    const isBg = norm(row.querySelector(".oldhex").value) === norm(state.background);
    row.classList.toggle("is-bg", isBg);
    const bgBadge = row.querySelector(".bg-badge");
    if (isBg && !bgBadge) {
      const badge = document.createElement("span");
      badge.className = "badge bg bg-badge";
      badge.textContent = "背景";
      const meta = row.querySelector(".meta");
      meta.insertBefore(badge, meta.firstChild);
    } else if (!isBg && bgBadge) {
      bgBadge.remove();
    }
  });
}

function detectBackground(dataUrl) {
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => {
      try {
        const c = document.createElement("canvas");
        const scale = Math.min(1, 200 / img.width);
        c.width = Math.max(1, Math.round(img.width * scale));
        c.height = Math.max(1, Math.round(img.height * scale));
        const ctx = c.getContext("2d", { willReadFrequently: true });
        ctx.drawImage(img, 0, 0, c.width, c.height);
        const d = ctx.getImageData(0, 0, c.width, c.height).data;
        const cnt = new Map();
        for (let i = 0; i < d.length; i += 16) {
          const key = `${d[i]},${d[i + 1]},${d[i + 2]}`;
          cnt.set(key, (cnt.get(key) || 0) + 1);
        }
        let best = null;
        let bestN = 0;
        cnt.forEach((n, k) => {
          if (n > bestN) {
            bestN = n;
            best = k;
          }
        });
        const [r, g, b] = best.split(",").map(Number);
        resolve("#" + [r, g, b].map((v) => v.toString(16).padStart(2, "0")).join(""));
      } catch (_) {
        resolve(null);
      }
    };
    img.onerror = () => resolve(null);
    img.src = dataUrl;
  });
}

$("protectBg").addEventListener("change", markBackgroundRows);

function isMinorColor(c, maxCount) {
  if (c.kind === "stroke") return true; // 细线/文字/轴线等纯描边
  if (c.kind === "pixel" && c.count < Math.max(50, Math.round(maxCount * 0.001))) {
    return true; // 位图边缘/过渡杂色
  }
  return false;
}

function applyMinorFilter() {
  const showAll = $("showMinor").checked;
  document.querySelectorAll("#mapBody .mrow[data-minor='1']").forEach((r) => {
    r.classList.toggle("minor-hidden", !showAll);
  });
}

function updateMapNote(total, minorCount) {
  const note = $("mapNote");
  if (!note) return;
  const showAll = $("showMinor").checked;
  const parts = [];
  if (!showAll && minorCount) {
    parts.push(`已隐藏 ${minorCount} 种次要颜色（细线/边缘/过渡），勾选“显示次要颜色”可查看`);
  }
  if (total > SHOW_MAX) parts.push(`共 ${total} 种，只显示出现最多的 ${SHOW_MAX} 种`);
  note.textContent = parts.join("；");
}

$("showMinor").addEventListener("change", () => {
  applyMinorFilter();
  updateMapNote(state.total, document.querySelectorAll("#mapBody .mrow[data-minor='1']").length);
});

function makeRow(oldHex, newHex, editable, meta) {
  const row = document.createElement("div");
  row.className = "mrow";
  const badge = meta && BADGE_TEXT[meta.kind]
    ? `<span class="badge ${meta.kind}" title="填充×${meta.fill_count || 0} 描边×${meta.stroke_count || 0}">${BADGE_TEXT[meta.kind]}</span>`
    : "";
  row.innerHTML = `
    <div class="oldcell">
      <span class="sw"></span>
      <input type="text" class="oldhex" value="${oldHex}" spellcheck="false"
        ${editable ? "" : "readonly"}>
    </div>
    <div class="newcell">
      <input type="color" class="new" value="${newHex}">
      <input type="text" class="newhex" value="${newHex}" spellcheck="false">
    </div>
    <div class="meta">
      ${badge}
      <span class="cnt">×${meta ? meta.count : ""}</span>
    </div>
    <button class="del" title="移除">×</button>`;
  row.classList.add("clickable");

  const sw = row.querySelector(".sw");
  const old = row.querySelector(".oldhex");
  const ci = row.querySelector(".new");
  const tx = row.querySelector(".newhex");

  const sync = () => {
    sw.style.background = norm(old.value);
    if (HEX_RE.test(old.value)) ci.value = old.value;
  };
  old.addEventListener("input", sync);
  ci.addEventListener("input", () => (tx.value = ci.value));
  tx.addEventListener("input", () => {
    if (HEX_RE.test(tx.value)) ci.value = tx.value;
  });
  row.querySelector(".del").addEventListener("click", () => row.remove());
  row.addEventListener("click", (e) => {
    if (
      e.target.closest(".del") ||
      e.target.closest(".new")
    ) {
      return;
    }
    toggleHighlight(row);
  });
  sync();
  return row;
}

$("addRowBtn").addEventListener("click", () => $("mapBody").appendChild(makeRow("", "", true, null)));

function buildMapping() {
  const map = {};
  const protectBg = $("protectBg").checked;
  document.querySelectorAll("#mapBody .mrow").forEach((row) => {
    const old = norm(row.querySelector(".oldhex").value);
    const neu = norm(row.querySelector(".newhex").value);
    if (
      HEX_RE.test(old) &&
      HEX_RE.test(neu) &&
      old !== neu &&
      !(protectBg && state.background && old === norm(state.background))
    ) {
      map[old] = neu;
    }
  });
  $("batchArea")
    .value.split("\n")
    .forEach((line) => {
      const parts = line.trim().split(/\s+/);
      if (parts.length === 2 && HEX_RE.test(parts[0]) && HEX_RE.test(parts[1])) {
        const old = norm(parts[0]);
        if (!(protectBg && state.background && old === norm(state.background))) {
          map[old] = norm(parts[1]);
        }
      }
    });
  return map;
}

/* ---------- 映射文件（拖拽导入 JSON / txt） ---------- */

bindDrop($("mapFileDrop"), $("mapFile"), loadMappingFile);

function loadMappingFile(file) {
  const reader = new FileReader();
  reader.onload = () => {
    try {
      const map = parseMappingText(String(reader.result));
      const keys = Object.keys(map);
      if (!keys.length) throw new Error("没有识别到有效的颜色映射");
      const updated = applyMapping(map);
      showStatus(`已从 ${file.name} 导入 ${updated} 组映射`);
    } catch (err) {
      showStatus("映射文件读取失败：" + err.message, true);
    }
  };
  reader.onerror = () => showStatus("映射文件读取失败", true);
  reader.readAsText(file);
}

function captureTableState() {
  const map = {};
  document.querySelectorAll("#mapBody .mrow").forEach((row) => {
    const old = norm(row.querySelector(".oldhex").value);
    const neu = norm(row.querySelector(".newhex").value);
    if (HEX_RE.test(old) && HEX_RE.test(neu)) map[old] = neu;
  });
  return map;
}

function applyMapping(map, sourceLabel, skipStatus) {
  let updated = 0;
  Object.keys(map).forEach((old) => {
    const row = [...document.querySelectorAll("#mapBody .mrow")].find(
      (r) => norm(r.querySelector(".oldhex").value) === old
    );
    if (row) {
      row.querySelector(".newhex").value = map[old];
      row.querySelector(".new").value = map[old];
      updated++;
    } else {
      $("mapBody").insertBefore(makeRow(old, map[old], false, null), $("mapBody").firstChild);
      updated++;
    }
  });
  if (!skipStatus && sourceLabel) showStatus(`已从 ${sourceLabel} 恢复 ${updated} 组映射`);
  return updated;
}

function parseMappingText(text) {
  const map = {};
  const t = text.trim();
  if (t.startsWith("{")) {
    const obj = JSON.parse(t);
    for (const k in obj) {
      const v = obj[k];
      if (HEX_RE.test(k) && typeof v === "string" && HEX_RE.test(v)) {
        map[norm(k)] = norm(v);
      }
    }
  } else {
    t.split("\n").forEach((line) => {
      const parts = line.trim().split(/\s+/);
      if (parts.length === 2 && HEX_RE.test(parts[0]) && HEX_RE.test(parts[1])) {
        map[norm(parts[0])] = norm(parts[1]);
      }
    });
  }
  return map;
}

/* ---------- 应用配色（异步） / 下载 ---------- */

$("outName").addEventListener("input", () => (state.outNameTouched = true));

$("applyBtn").addEventListener("click", async () => {
  const mapping = buildMapping();
  if (!Object.keys(mapping).length) {
    showStatus("还没有修改任何颜色：改某行的“替换颜色”，或用“批量粘贴/映射文件”添加映射。", true);
    return;
  }
  const tolerance = Math.max(0, Math.min(255, parseInt($("tolerance").value || "0", 10)));
  const smooth = $("mode").value === "smooth";
  showStatus("换色中…");
  $("applyBtn").disabled = true;
  try {
    const r = await fetch("/api/apply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: state.id, mapping, tolerance, smooth }),
    });
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || "换色失败");
    const s = await pollStatus(state.id, (p) => {
      const msg = p.message ? ` · ${p.message}` : "";
      showStatus(`换色中 ${p.percent}%${msg}`);
    });
    if (!s.apply) throw new Error("服务未返回结果");
    onApplyReady(s.apply);
  } catch (err) {
    showStatus("换色失败：" + err.message, true);
  } finally {
    $("applyBtn").disabled = false;
  }
});

function onApplyReady(res) {
  state.after = res.preview;
  setAfterAvailable(true);
  setSeg("after");
  refreshView();
  $("download").hidden = false;
  $("warnbox").hidden = !res.warnings;
  $("warnbox").textContent = res.warnings || "";
  showStatus(`完成：替换 ${res.matched} 处颜色`);
  cancelHighlight();
}

$("viewSeg").addEventListener("click", (e) => {
  const b = e.target.closest(".seg-btn");
  if (!b || b.disabled) return;
  setSeg(b.dataset.view);
  refreshView();
  cancelHighlight();
});

$("download").addEventListener("click", async (e) => {
  e.preventDefault();
  const fmt = $("exportFmt").value;
  $("download").disabled = true;
  const oldText = $("download").textContent;
  try {
    if (fmt === "map-csv" || fmt === "map-json") {
      exportMapping(fmt);
      return;
    }
    if (state.kind === "pdf") {
      $("download").textContent = "生成中…";
      showStatus("正在生成最终文件…");
      const g = await fetch(`/api/generate/${state.id}`, { method: "POST" });
      const gj = await g.json();
      if (!g.ok) throw new Error(gj.error || "生成失败");
      if (gj.status !== "ready") {
        await pollStatus(state.id, (p) => {
          const msg = p.message ? ` · ${p.message}` : "";
          showStatus(`生成最终文件 ${p.percent}%${msg}`);
        });
      }
    }
    $("download").textContent = "导出中…";
    showStatus(fmt === "png" ? "正在导出 PNG…" : `正在导出 ${fmt.toUpperCase()}…`);
    const r = await fetch(`/api/export/${state.id}/${fmt}`);
    if (!r.ok) {
      let msg = "生成失败";
      try {
        msg = (await r.json()).error || msg;
      } catch (_) {}
      throw new Error(msg);
    }
    const blob = await r.blob();
    const name = exportBaseName() + "." + fmt;
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
    showStatus(`已导出 ${name}`);
  } catch (err) {
    showStatus("导出失败：" + err.message, true);
  } finally {
    $("download").textContent = oldText || "导出";
    $("download").disabled = false;
  }
});

/* ---------- 颜色定位高亮 ---------- */

$("cancelHl").addEventListener("click", cancelHighlight);

async function toggleHighlight(row) {
  const inAfter = state.view === "after" && state.after;
  // 原图视图用原颜色；换色后视图用替换颜色
  const source = inAfter ? row.querySelector(".newhex") : row.querySelector(".oldhex");
  const hex = norm(source.value);
  if (!HEX_RE.test(hex)) return;
  if (state.hlColor === hex) {
    cancelHighlight();
    return;
  }
  const rgb = [
    parseInt(hex.slice(1, 3), 16),
    parseInt(hex.slice(3, 5), 16),
    parseInt(hex.slice(5, 7), 16),
  ];
  const base = inAfter ? state.after : state.before;
  if (!base) return;
  showStatus("高亮中…");
  try {
    // 精确匹配：只高亮该颜色本身 + 它在文件里的真实透明度版本（从扫描结果取），
    // 不再用固定 6 档透明度候选 + 宽容差，避免大面积重复高亮。
    const oldHex = norm(row.querySelector(".oldhex").value);
    const item = (state.colors || []).find((c) => norm(c.hex) === oldHex);
    const opacities = item && item.opacities ? item.opacities : [];
    const { dataUrl, matched } = await highlightImage(base, rgb, 30, opacities);
    if (!matched) {
      cancelHighlight();
      showStatus("图中未找到该颜色", true);
      return;
    }
    state.hlColor = hex;
    $("viewImg").src = dataUrl;
    $("cancelHl").hidden = false;
    document.querySelectorAll("#mapBody .mrow").forEach((r) => {
      r.classList.toggle("hl", r === row);
    });
    showStatus(`已高亮 ${matched.toLocaleString()} 像素`);
  } catch (err) {
    showStatus("高亮失败：" + err.message, true);
  }
}

function cancelHighlight() {
  state.hlColor = null;
  $("cancelHl").hidden = true;
  document.querySelectorAll("#mapBody .mrow").forEach((r) => r.classList.remove("hl"));
  refreshView();
}

function highlightImage(dataUrl, rgb, tol, opacities) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => {
      try {
        const c = document.createElement("canvas");
        c.width = img.width;
        c.height = img.height;
        const ctx = c.getContext("2d", { willReadFrequently: true });
        ctx.fillStyle = "#fff";
        ctx.fillRect(0, 0, c.width, c.height);
        ctx.drawImage(img, 0, 0);
        const d = ctx.getImageData(0, 0, c.width, c.height);
        const p = d.data;
        // 候选色：目标色本身 + 与白色按文件中的真实透明度混合
        const cands = [rgb];
        (opacities || []).forEach((a) => {
          a = Math.max(0, Math.min(1, a));
          cands.push([
            Math.round(rgb[0] * a + 255 * (1 - a)),
            Math.round(rgb[1] * a + 255 * (1 - a)),
            Math.round(rgb[2] * a + 255 * (1 - a)),
          ]);
        });
        let matched = 0;
        for (let i = 0; i < p.length; i += 4) {
          // 保护白色/浅色背景：保持原样，避免被“目标色+白色混合”候选色误命中全图提亮
          if (p[i] > 235 && p[i + 1] > 235 && p[i + 2] > 235) {
            continue;
          }
          let best = Infinity;
          for (const cr of cands) {
            const dist =
              Math.abs(p[i] - cr[0]) +
              Math.abs(p[i + 1] - cr[1]) +
              Math.abs(p[i + 2] - cr[2]);
            if (dist < best) best = dist;
          }
          if (best <= tol) {
            p[i] = Math.min(255, p[i] + 80);
            p[i + 1] = Math.min(255, p[i + 1] + 80);
            p[i + 2] = Math.min(255, p[i + 2] + 80);
            matched++;
          } else {
            p[i] = Math.round(p[i] * 0.55);
            p[i + 1] = Math.round(p[i + 1] * 0.55);
            p[i + 2] = Math.round(p[i + 2] * 0.55);
          }
        }
        ctx.putImageData(d, 0, 0);
        resolve({ dataUrl: c.toDataURL("image/png"), matched });
      } catch (e) {
        reject(e);
      }
    };
    img.onerror = reject;
    img.src = dataUrl;
  });
}

/* ---------- 历史记录（7 天） ---------- */

async function refreshHistory() {
  try {
    const r = await fetch("/api/history");
    const j = await r.json();
    const box = $("historyBox");
    const list = $("historyList");
    if (!j.items || !j.items.length) {
      box.hidden = true;
      return;
    }
    list.innerHTML = "";
    j.items.forEach((it) => {
      const row = document.createElement("div");
      row.className = "hist-item";
      row.title = "点击打开该文件";
      row.innerHTML = `
        <span class="hist-name">${escapeHtml(it.name)}</span>
        <span class="hist-time">${formatTime(it.time)}</span>
        <button class="hist-del" title="删除">×</button>`;
      row.addEventListener("click", (e) => {
        if (e.target.closest(".hist-del")) return;
        loadSession(it);
      });
      row.querySelector(".hist-del").addEventListener("click", (e) => {
        e.stopPropagation();
        deleteHistory(it.id, e.currentTarget);
      });
      list.appendChild(row);
    });
    box.hidden = false;
  } catch (_) {}
}

function formatTime(ms) {
  const d = new Date(ms);
  const now = new Date();
  const hm = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  if (d.toDateString() === now.toDateString()) return `今天 ${hm}`;
  return `${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")} ${hm}`;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));
}

async function loadSession(it) {
  try {
    const s = await pollStatus(it.id);
    if (!s.scan) throw new Error("该记录没有有效的扫描结果");
    state.id = it.id;
    state.kind = it.kind || null;
    onScanReady(it.name, s.scan);
    if (s.apply) {
      state.after = s.apply.preview;
      setAfterAvailable(true);
      $("download").hidden = false;
      $("download").href = s.apply.download;
      $("warnbox").hidden = !s.apply.warnings;
      $("warnbox").textContent = s.apply.warnings || "";
    }
    if (s.mapping && s.mapping.mapping) {
      applyMapping(s.mapping.mapping, it.name);
    }
    cancelHighlight();
  } catch (err) {
    showStatus("打开历史记录失败：" + err.message, true);
  }
}

async function deleteHistory(sid, btn) {
  if (btn) btn.disabled = true;
  try {
    const r = await fetch(`/api/history/${sid}`, { method: "DELETE" });
    if (r.status === 404) {
      await refreshHistory();
      return;
    }
    if (!r.ok) {
      let msg = "删除失败";
      try {
        msg = (await r.json()).error || msg;
      } catch (_) {}
      throw new Error(msg);
    }
    if (state.id === sid) {
      state.id = null;
      state.before = null;
      state.after = null;
      $("mapPanel").hidden = true;
      $("fileInfo").hidden = true;
      $("viewImg").removeAttribute("src");
      $("placeholder").hidden = false;
      $("download").hidden = true;
      $("exportFmt").hidden = true;
      $("cancelHl").hidden = true;
      setAfterAvailable(false);
    }
    await refreshHistory();
  } catch (err) {
    alert("删除失败：" + err.message);
  } finally {
    if (btn) btn.disabled = false;
  }
}

refreshHistory();

// ---- 生命周期：心跳 + 退出按钮 ----
// 页面还在就每 5s 报活；服务端超过 IDLE_EXIT_SECONDS 没收到心跳就自动退出。
const HEARTBEAT_MS = 5000;
let _dead = false;
async function ping() {
  if (_dead) return;
  try {
    const r = await fetch("/api/ping", { method: "POST" });
    if (r.status === 410) _dead = true; // 服务端已在退出
  } catch (_) {}
}
setInterval(ping, HEARTBEAT_MS);
ping();

const quitBtn = $("quitBtn");
if (quitBtn) {
  quitBtn.addEventListener("click", async () => {
    quitBtn.disabled = true;
    try {
      await fetch("/api/shutdown", { method: "POST" });
      document.body.innerHTML =
        '<div style="display:flex;align-items:center;justify-content:center;height:100vh;color:#666;font-size:15px">已退出，可以直接关闭此窗口。</div>';
    } catch (e) {
      quitBtn.disabled = false;
      alert("退出失败：" + e.message);
    }
  });
}
