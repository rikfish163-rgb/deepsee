/* deepsee Web 面板前端逻辑(无框架,零依赖) */
"use strict";

const $ = (id) => document.getElementById(id);

const state = {
  mode: "describe",
  fileDataUrl: null,
  fileName: "",
  cfg: null, // 最新 config(已掩码)
};

// ---------- 网络 ----------
async function api(url, opts = {}) {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));

// ---------- 状态 ----------
async function loadStatus() {
  try {
    const s = await api("/api/status");

    // 视觉链路 chip
    const ok = s.problems.length === 0;
    const chip = $("chainChip");
    chip.dataset.state = ok ? "ok" : s.problems.length ? "warn" : "bad";
    $("chainChipText").textContent = ok
      ? "视觉链路就绪"
      : s.vision.chain.length
        ? "配置不完整"
        : "未配置视觉 API";

    // 管线中间节点:视觉供应商
    const first = s.vision.chain[0];
    $("visionLabel").textContent = first
      ? `${first.provider} · ${first.model}`
      : "视觉模型";
    $("pnodeVision").style.borderColor = first
      ? "rgba(79,216,224,0.45)"
      : "var(--line)";

    // 状态表
    const rows = [];
    rows.push(["视觉链", s.vision.chain.length
      ? s.vision.chain.map((c) => `${c.provider} · ${c.model}`).join("<br>")
      : "<span class='dot dot-bad'></span>未配置"]);
    rows.push(["上游模型", `${esc(s.upstream.model)} ${s.upstream.key_set ? "" : "· 无 key"}`]);
    rows.push(["文生图", s.generation.model
      ? `${esc(s.generation.model)} ${s.generation.key_set ? "" : "· 无 key"}`
      : "<span class='dot dot-warn'></span>未启用"]);
    rows.push(["代理端口", String(s.ports.proxy)]);
    rows.push(["面板端口", String(s.ports.web)]);
    for (const [k, v] of Object.entries(s.agents)) {
      rows.push([`检测 ${k}`, v
        ? "<span class='dot dot-ok'></span>已探测"
        : "<span class='dot dot-warn'></span>未探测"]);
    }
    $("statusList").innerHTML = rows
      .map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`)
      .join("");

    // 问题列表
    const list = $("problems");
    list.innerHTML = s.problems.length
      ? s.problems.map((p) => `<li>${esc(p)}</li>`).join("")
      : `<li class="ok">全部就绪</li>`;

    // 配置预览
    const cfg = await api("/api/config");
    state.cfg = cfg;
    $("cfgView").textContent = JSON.stringify(cfg, null, 2);
    if (!$("cfgEdit").hidden) $("cfgText").value = JSON.stringify(cfg, null, 2);
  } catch (e) {
    $("chainChip").dataset.state = "bad";
    $("chainChipText").textContent = "无法连接服务";
  }
}

// ---------- 识图 ----------
function setMode(m) {
  state.mode = m;
  document.querySelectorAll(".mode-switch button").forEach((b) =>
    b.classList.toggle("active", b.dataset.mode === m));
}

async function runSee() {
  const btn = $("seeBtn");
  const meta = $("seeMeta");
  btn.disabled = true;
  $("pipeline").classList.add("busy");
  meta.textContent = "识别中…";
  try {
    const payload = { mode: state.mode, question: $("questionInput").value.trim() };
    if (state.fileDataUrl) {
      payload.base64 = state.fileDataUrl;
    } else {
      payload.source = $("urlInput").value.trim() || "";
    }
    const r = await api("/api/test", { method: "POST", body: JSON.stringify(payload) });
    const out = $("seeOutput");
    out.hidden = false;
    out.textContent = r.text;
    meta.textContent = `${r.provider} · ${r.latency_ms} ms`;
  } catch (e) {
    $("seeOutput").hidden = false;
    $("seeOutput").textContent = "识别失败:" + e.message;
    meta.textContent = "";
  } finally {
    btn.disabled = false;
    $("pipeline").classList.remove("busy");
  }
}

async function pickLatest() {
  try {
    const r = await api("/api/latest", { method: "POST", body: "{}" });
    $("urlInput").value = r.path;
    $("urlInput").placeholder = "已填入最近会话截图";
  } catch (e) {
    $("urlInput").value = "";
    $("urlInput").placeholder = "未找到会话截图,请粘贴路径";
  }
}

// ---------- 文生图 ----------
async function runGen() {
  const btn = $("genBtn");
  const meta = $("genMeta");
  const prompt = $("genPrompt").value.trim();
  if (!prompt) { meta.textContent = "先写一句提示词"; return; }
  btn.disabled = true;
  $("pipeline").classList.add("busy");
  meta.textContent = "生成中…";
  try {
    const r = await api("/api/gen", { method: "POST", body: JSON.stringify({ prompt }) });
    $("genResult").hidden = false;
    $("genImg").src = r.url;
    $("genPath").textContent = r.path;
    meta.textContent = "完成";
  } catch (e) {
    meta.textContent = "失败:" + e.message;
  } finally {
    btn.disabled = false;
    $("pipeline").classList.remove("busy");
  }
}

// ---------- 配置编辑 ----------
function toggleConfigEditor() {
  const edit = $("cfgEdit");
  edit.hidden = !edit.hidden;
  if (!edit.hidden) {
    $("cfgText").value = JSON.stringify(state.cfg, null, 2);
    $("editCfgBtn").textContent = "收起";
  } else {
    $("editCfgBtn").textContent = "编辑";
  }
}

async function saveConfig() {
  const meta = $("cfgMeta");
  try {
    const patch = JSON.parse($("cfgText").value);
    const cfg = await api("/api/config", { method: "PUT", body: JSON.stringify(patch) });
    state.cfg = cfg;
    $("cfgView").textContent = JSON.stringify(cfg, null, 2);
    meta.textContent = "已保存";
    await loadStatus();
  } catch (e) {
    meta.textContent = "保存失败:" + e.message;
  }
}

// ---------- 文件 & 粘贴 ----------
function onFile(file) {
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    state.fileDataUrl = reader.result;
    state.fileName = file.name;
    $("urlInput").placeholder = `已选:${file.name}(点「识别这张图」)`;
    $("urlInput").value = "";
  };
  reader.readAsDataURL(file);
}

// ---------- 事件绑定 ----------
$("pickBtn").addEventListener("click", () => $("fileInput").click());
$("fileInput").addEventListener("change", (e) => onFile(e.target.files[0]));
$("clipBtn").addEventListener("click", pickLatest);
$("seeBtn").addEventListener("click", runSee);
$("genBtn").addEventListener("click", runGen);
$("editCfgBtn").addEventListener("click", toggleConfigEditor);
$("saveCfgBtn").addEventListener("click", saveConfig);

document.querySelectorAll(".mode-switch button").forEach((b) =>
  b.addEventListener("click", () => setMode(b.dataset.mode)));

// 拖拽
const dz = $("dropzone");
dz.addEventListener("dragover", (e) => { e.preventDefault(); dz.classList.add("dragover"); });
dz.addEventListener("dragleave", () => dz.classList.remove("dragover"));
dz.addEventListener("drop", (e) => {
  e.preventDefault();
  dz.classList.remove("dragover");
  onFile(e.dataTransfer.files[0]);
});

// 全局粘贴(截图)
document.addEventListener("paste", (e) => {
  const items = e.clipboardData?.items || [];
  for (const it of items) {
    if (it.type.startsWith("image/")) {
      const f = it.getAsFile();
      if (f) onFile(f);
      break;
    }
  }
});

// 回车快捷键
$("urlInput").addEventListener("keydown", (e) => { if (e.key === "Enter") runSee(); });
$("questionInput").addEventListener("keydown", (e) => { if (e.key === "Enter") runSee(); });
$("genPrompt").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) runGen();
});

loadStatus();
setInterval(loadStatus, 15000);
