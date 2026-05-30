const bridge = window.AstrBotPluginPage;
const PLUGIN = "sillytavern_prompt";

// DOM
const cardList = document.getElementById("card-list");
const editor = document.getElementById("editor");
const dialog = document.getElementById("card-dialog");
const cardForm = document.getElementById("card-form");
const dialogTitle = document.getElementById("dialog-title");
const btnNew = document.getElementById("btn-new-card");
const btnCancel = document.getElementById("btn-cancel");

let cards = [];
let activeCard = null;
let editingName = null;

// ── API ───────────────────────────────────────────────

async function apiGet(path) {
  const res = await bridge.apiGet(`${PLUGIN}/${path}`);
  return res;
}

async function apiPost(path, data) {
  const res = await bridge.apiPost(`${PLUGIN}/${path}`, data);
  return res;
}

// ── 列表 ──────────────────────────────────────────────

async function loadCards() {
  cards = await apiGet("cards") || [];
  renderList();
}

function renderList() {
  cardList.innerHTML = "";
  cards.forEach(c => {
    const li = document.createElement("li");
    if (c.name === activeCard) li.classList.add("active");

    const s = c.skills === null ? "all" : (c.skills.length || "none");
    const t = c.tools === null ? "all" : (c.tools.length || "none");

    li.innerHTML = `<span class="name">${esc(c.name)}</span>
      <span class="badges">
        <span class="badge">S:${s}</span>
        <span class="badge">T:${t}</span>
      </span>`;
    li.addEventListener("click", () => selectCard(c.name));
    cardList.appendChild(li);
  });
}

// ── 详情 ──────────────────────────────────────────────

async function selectCard(name) {
  activeCard = name;
  const card = await apiGet(`cards/${encodeURIComponent(name)}`);
  if (!card) return;

  renderList();
  editor.innerHTML = `
    <div class="card-detail">
      <h3>${esc(card.name)}</h3>
      ${field("prompt", card.prompt)}
      ${field("skills", JSON.stringify(card.skills), "json")}
      ${field("tools", JSON.stringify(card.tools), "json")}
      ${card.description ? field("description", card.description) : ""}
      ${card.personality ? field("personality", card.personality) : ""}
      ${card.scenario ? field("scenario", card.scenario) : ""}
      ${card.first_mes ? field("first_mes", card.first_mes) : ""}
      ${card.mes_example ? field("mes_example", card.mes_example) : ""}
      ${card.user_name ? field("user_name", card.user_name) : ""}
      ${card.persona_description ? field("persona_description", card.persona_description) : ""}
      <div class="actions">
        <button class="primary" id="btn-edit">编辑</button>
        <button class="danger" id="btn-delete">删除</button>
      </div>
    </div>
  `;

  document.getElementById("btn-edit").addEventListener("click", () => openDialog(card));
  document.getElementById("btn-delete").addEventListener("click", () => deleteCard(card.name));
}

function field(label, value, cls = "") {
  return `<div class="field"><label>${label}</label><div class="value ${cls}">${esc(value)}</div></div>`;
}

// ── 新建 / 编辑弹窗 ───────────────────────────────────

btnNew.addEventListener("click", () => {
  editingName = null;
  dialogTitle.textContent = "新建角色卡";
  cardForm.reset();
  cardForm.name.removeAttribute("readonly");
  dialog.showModal();
});

function openDialog(card) {
  editingName = card.name;
  dialogTitle.textContent = `编辑 ${card.name}`;
  cardForm.name.value = card.name || "";
  cardForm.name.setAttribute("readonly", "");
  cardForm.prompt.value = card.prompt || "";
  cardForm.skills.value = card.skills === null ? "null" : JSON.stringify(card.skills);
  cardForm.tools.value = card.tools === null ? "null" : JSON.stringify(card.tools);
  cardForm.description.value = card.description || "";
  cardForm.personality.value = card.personality || "";
  cardForm.scenario.value = card.scenario || "";
  cardForm.first_mes.value = card.first_mes || "";
  cardForm.mes_example.value = card.mes_example || "";
  cardForm.user_name.value = card.user_name || "User";
  cardForm.persona_description.value = card.persona_description || "";
  dialog.showModal();
}

btnCancel.addEventListener("click", () => dialog.close());

cardForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(cardForm);

  const parseList = (raw) => {
    if (!raw || raw.trim() === "null" || raw.trim() === "") return null;
    try { return JSON.parse(raw); } catch { return null; }
  };

  const data = {
    name: fd.get("name").trim(),
    prompt: fd.get("prompt"),
    skills: parseList(fd.get("skills")),
    tools: parseList(fd.get("tools")),
    description: fd.get("description"),
    personality: fd.get("personality"),
    scenario: fd.get("scenario"),
    first_mes: fd.get("first_mes"),
    mes_example: fd.get("mes_example"),
    user_name: fd.get("user_name") || "User",
    persona_description: fd.get("persona_description"),
  };

  if (!data.name) return;

  await apiPost("cards/save", data);
  if (editingName && editingName !== data.name) {
    await apiPost("cards/delete", { name: editingName });
  }

  dialog.close();
  if (!activeCard || activeCard === editingName || activeCard === data.name) {
    activeCard = data.name;
  }
  await loadCards();
  await selectCard(data.name);
});

// ── 删除 ──────────────────────────────────────────────

async function deleteCard(name) {
  if (!confirm(`确定删除角色卡「${name}」？`)) return;
  await apiPost("cards/delete", { name });
  if (activeCard === name) {
    activeCard = null;
    editor.innerHTML = '<div class="empty-state">选择一张角色卡或新建一张</div>';
  }
  await loadCards();
}

// ── 工具 ──────────────────────────────────────────────

function esc(s) {
  if (s == null) return "";
  const d = document.createElement("div");
  d.textContent = String(s);
  return d.innerHTML;
}

// ── 初始化 ────────────────────────────────────────────

await bridge.ready();
await loadCards();
