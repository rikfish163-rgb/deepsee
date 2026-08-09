/* deepsee Web 面板前端逻辑(无框架,零依赖) */
"use strict";

const $ = (id) => document.getElementById(id);

const state = {
  mode: "describe",
  fileDataUrl: null,
  fileName: "",
  cfg: null,
  // 对话
  chatHistory: [],       // OpenAI 格式 messages
  attach: null,          // {dataUrl, name}
};

const TAB_META = {
  chat: ["对话工作台", "把图片和问题一起交给 DeepSeek，视觉中继会自动补全它看不到的部分。"],
  see: ["视觉分析台", "选择识别方式和关注问题，把图片转换成可验证、可继续推理的文字。"],
  gen: ["图片生成台", "使用你自己的图片生成端点，把描述转换成可下载的视觉结果。"],
  status: ["链路状态", "检查视觉模型、上游推理、客户端接入与本地服务是否已经就绪。"],
  config: ["本地配置", "查看经过遮盖的配置；所有修改都只保存在这台设备上。"],
};

// ---------- 工具 ----------
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

function activeTab() {
  return document.querySelector(".tabs button.active")?.dataset.tab || "chat";
}

// ---------- 标签页 ----------
function switchTab(name) {
  document.querySelectorAll(".tabs button").forEach((b) =>
    b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab-panel").forEach((p) =>
    p.classList.toggle("active", p.id === `tab-${name}`));
  const meta = TAB_META[name] || TAB_META.chat;
  $("viewTitle").textContent = meta[0];
  $("viewSubtitle").textContent = meta[1];
}

// ---------- 状态 ----------
async function loadStatus() {
  try {
    const s = await api("/api/status");

    const ok = s.problems.length === 0;
    const chip = $("chainChip");
    chip.dataset.state = ok ? "ok" : s.problems.length ? "warn" : "bad";
    $("chainChipText").textContent = ok
      ? "视觉链路就绪"
      : s.vision.chain.length
        ? "配置不完整"
        : "未配置视觉 API";

    const first = s.vision.chain[0];
    $("visionLabel").textContent = first
      ? `${first.provider} · ${first.model}`
      : "视觉模型";
    $("pnodeVision").style.borderColor = first
      ? "rgba(79,216,224,0.45)"
      : "var(--line)";

    const rows = [];
    rows.push(["视觉链", s.vision.chain.length
      ? s.vision.chain.map((c) => `${esc(c.provider)} · ${esc(c.model)}`).join("<br>")
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
      .map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`)
      .join("");

    const list = $("problems");
    list.innerHTML = s.problems.length
      ? s.problems.map((p) => `<li>${esc(p)}</li>`).join("")
      : `<li class="ok">全部就绪</li>`;

    // 集成信息
    if (s.integration) {
      $("intBaseUrl").value = s.integration.base_url;
      $("intKey").value = s.integration.master_key || "(空,无需鉴权)";
      $("intModel").value = s.integration.model;
    }

    const cfg = await api("/api/config");
    state.cfg = cfg;
    $("cfgView").textContent = JSON.stringify(cfg, null, 2);
    if (!$("cfgEdit").hidden) $("cfgText").value = JSON.stringify(cfg, null, 2);
  } catch (e) {
    $("chainChip").dataset.state = "bad";
    $("chainChipText").textContent = "无法连接服务";
  }
}

// ---------- 对话 ----------
function renderChat() {
  const log = $("chatLog");
  log.innerHTML = "";
  state.attach = null;
  updateAttachRow();
  if (!state.chatHistory.length) {
    const empty = document.createElement("div");
    empty.className = "chat-empty";
    empty.id = "chatEmpty";
    empty.innerHTML = `
      <div class="empty-aperture" aria-hidden="true"><i></i><b></b></div>
      <h3>把图发给 DeepSeek</h3>
      <p>粘贴截图、选择图片，或者直接提问。deepsee 会先提取视觉事实，再交给文本模型推理。</p>
      <div class="prompt-list" aria-label="示例问题">
        <button type="button" data-prompt="帮我定位这张报错截图里的根本原因">分析报错截图</button>
        <button type="button" data-prompt="请准确提取图片里的全部文字">提取图片文字</button>
        <button type="button" data-prompt="解释这张技术图中的节点和数据流">读懂技术图</button>
      </div>`;
    log.appendChild(empty);
    return;
  }
  for (const m of state.chatHistory) {
    log.appendChild(buildBubble(m.role, m.content, true));
  }
}

function buildBubble(role, content, includeImage) {
  const div = document.createElement("div");
  div.className = `chat-msg ${role === "user" ? "user" : "assistant"}`;
  const roleTag = document.createElement("span");
  roleTag.className = "role";
  roleTag.textContent = role === "user" ? "你" : "DeepSeek";
  div.appendChild(roleTag);

  const body = document.createElement("span");
  body.className = "chat-body";

  if (Array.isArray(content)) {
    let text = "";
    for (const item of content) {
      if (item.type === "image_url") {
        if (includeImage) {
          const img = document.createElement("img");
          img.className = "img-thumb";
          img.src = item.image_url.url;
          div.appendChild(img);
          const tag = document.createElement("span");
          tag.className = "bridge-tag";
          tag.textContent = "本图经视觉模型转换后交给 DeepSeek";
          div.appendChild(tag);
        }
      } else if (item.type === "text") {
        text += item.text;
      }
    }
    body.textContent = text;
  } else {
    body.textContent = content;
  }
  div.appendChild(body);
  return div;
}

function appendToLast(role, text) {
  // 找到最后一个同角色气泡,追加文本
  const msgs = $("chatLog").querySelectorAll(".chat-msg");
  let target = null;
  for (let i = msgs.length - 1; i >= 0; i--) {
    if (msgs[i].classList.contains(role === "user" ? "user" : "assistant")) {
      target = msgs[i];
      break;
    }
  }
  if (!target) {
    target = buildBubble(role, "", false);
    $("chatLog").appendChild(target);
  }
  target.classList.remove("typing");
  const body = target.querySelector(".chat-body");
  if (!body) return;
  body.textContent += text;
  $("chatLog").scrollTop = $("chatLog").scrollHeight;
}

function updateAttachRow() {
  const row = $("attachRow");
  if (state.attach) {
    row.hidden = false;
    $("attachThumb").src = state.attach.dataUrl;
    $("attachName").textContent = state.attach.name;
  } else {
    row.hidden = true;
  }
}

function setChatAttach(file) {
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    state.attach = { dataUrl: reader.result, name: file.name };
    updateAttachRow();
  };
  reader.readAsDataURL(file);
}

function userText() {
  return $("chatInput").value.trim();
}

async function sendChat() {
  const input = userText();
  if (!input && !state.attach) return;
  const sendBtn = $("sendBtn");
  sendBtn.disabled = true;
  $("pipeline").classList.add("busy");
  $("chatMeta").textContent = "DeepSeek 思考中…";

  const content = state.attach
    ? [
        { type: "image_url", image_url: { url: state.attach.dataUrl } },
        { type: "text", text: input || "请描述这张图。" },
      ]
    : input;

  state.chatHistory.push({ role: "user", content });
  $("chatInput").value = "";
  state.attach = null;
  updateAttachRow();

  const log = $("chatLog");
  const empty = $("chatEmpty");
  if (empty) empty.remove();
  log.appendChild(buildBubble("user", content, true));
  log.scrollTop = log.scrollHeight;

  // 占位气泡(流式填充)
  const aiBubble = buildBubble("assistant", "", false);
  aiBubble.classList.add("typing");
  log.appendChild(aiBubble);
  log.scrollTop = log.scrollHeight;

  const payload = { messages: state.chatHistory, stream: true };
  let acc = "";
  let errMsg = "";
  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      throw new Error(d.error || `HTTP ${res.status}`);
    }
    if (!res.body) throw new Error("浏览器不支持流式响应");
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    let done = false;
    while (!done) {
      const { done: d, value } = await reader.read();
      if (d) break;
      buf += dec.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const block = buf.slice(0, idx).trim();
        buf = buf.slice(idx + 2);
        for (const line of block.split("\n")) {
          if (!line.startsWith("data:")) continue;
          const ev = JSON.parse(line.slice(5).trim() || "{}");
          if (ev.delta) {
            acc += ev.delta;
            appendToLast("assistant", ev.delta);
          } else if (ev.error) {
            errMsg = ev.error;
          } else if (ev.done) {
            done = true;
          }
        }
      }
    }
  } catch (e) {
    errMsg = e.message;
  } finally {
    aiBubble.classList.remove("typing");
    sendBtn.disabled = false;
    $("pipeline").classList.remove("busy");
  }

  if (errMsg) {
    aiBubble.textContent = "";
    aiBubble.innerHTML = `<span class="role">DeepSeek</span>⚠ ${esc(errMsg)}`;
    state.chatHistory.push({ role: "assistant", content: `⚠ ${errMsg}` });
    $("chatMeta").textContent = "出错了";
  } else {
    aiBubble.classList.remove("typing");
    state.chatHistory.push({ role: "assistant", content: acc });
    $("chatMeta").textContent = "完成";
  }
  log.scrollTop = log.scrollHeight;
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
    $("genPlaceholder").hidden = true;
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
    $("editCfgBtn").textContent = "编辑 JSON";
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

// ---------- 文件 & 粘贴(按活动标签路由) ----------
function onSeeFile(file) {
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

function handlePasteFile(file) {
  if (!file) return;
  if (activeTab() === "chat") setChatAttach(file);
  else onSeeFile(file);
}

// ---------- 事件绑定 ----------
document.querySelectorAll(".tabs button").forEach((b) =>
  b.addEventListener("click", () => switchTab(b.dataset.tab)));

document.addEventListener("click", (e) => {
  const chip = e.target.closest("[data-prompt]");
  if (!chip) return;
  $("chatInput").value = chip.dataset.prompt || "";
  $("chatInput").focus();
});

// 对话
$("sendBtn").addEventListener("click", sendChat);
$("clearChatBtn").addEventListener("click", () => {
  state.chatHistory = [];
  state.attach = null;
  renderChat();
  $("chatMeta").textContent = "";
});
$("attachBtn").addEventListener("click", () => $("chatFile").click());
$("chatFile").addEventListener("change", (e) => { setChatAttach(e.target.files[0]); e.target.value = ""; });
$("attachRemove").addEventListener("click", () => { state.attach = null; updateAttachRow(); });
$("chatInput").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendChat(); }
});

// 识图
$("pickBtn").addEventListener("click", () => $("fileInput").click());
$("fileInput").addEventListener("change", (e) => onSeeFile(e.target.files[0]));
$("clipBtn").addEventListener("click", pickLatest);
$("seeBtn").addEventListener("click", runSee);
document.querySelectorAll(".mode-switch button").forEach((b) =>
  b.addEventListener("click", () => setMode(b.dataset.mode)));
$("urlInput").addEventListener("keydown", (e) => { if (e.key === "Enter") runSee(); });
$("questionInput").addEventListener("keydown", (e) => { if (e.key === "Enter") runSee(); });

// 画图
$("genBtn").addEventListener("click", runGen);
$("genPrompt").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) runGen();
});

// 配置
$("editCfgBtn").addEventListener("click", toggleConfigEditor);
$("saveCfgBtn").addEventListener("click", saveConfig);

// 拖拽 → 识图台
const dz = $("dropzone");
dz.addEventListener("dragover", (e) => { e.preventDefault(); dz.classList.add("dragover"); });
dz.addEventListener("dragleave", () => dz.classList.remove("dragover"));
dz.addEventListener("drop", (e) => {
  e.preventDefault();
  dz.classList.remove("dragover");
  onSeeFile(e.dataTransfer.files[0]);
});

// 全局粘贴(截图):按活动标签路由
document.addEventListener("paste", (e) => {
  const items = e.clipboardData?.items || [];
  for (const it of items) {
    if (it.type.startsWith("image/")) {
      const f = it.getAsFile();
      if (f) handlePasteFile(f);
      break;
    }
  }
});

// 集成复制
document.querySelectorAll("[data-copy]").forEach((btn) =>
  btn.addEventListener("click", () => {
    const val = $(btn.dataset.copy).value;
    navigator.clipboard?.writeText(val).then(() => {
      btn.textContent = "已复制";
      setTimeout(() => (btn.textContent = "复制"), 1200);
    }).catch(() => {});
  }));

if (location.protocol === "file:") {
  $("fileNotice").hidden = false;
  $("chainChip").dataset.state = "warn";
  $("chainChipText").textContent = "静态预览";
} else {
  loadStatus();
  setInterval(loadStatus, 15000);
}
