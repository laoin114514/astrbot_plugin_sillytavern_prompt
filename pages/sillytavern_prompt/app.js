const bridge = window.AstrBotPluginPage;

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
let availableSkills = [];
let availableTools = [];

// ── API ───────────────────────────────────────────────

async function apiGet(path) {
  const res = await bridge.apiGet(path);
  return res;
}

async function apiPost(path, data) {
  const res = await bridge.apiPost(path, data);
  return res;
}

// ── Skills / Tools 复选框 ─────────────────────────────

function renderCheckList(containerId, items, selected, mode) {
  const container = document.getElementById(containerId);
  container.innerHTML = "";
  items.forEach(item => {
    const label = document.createElement("label");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.value = item.name;
    cb.dataset.name = item.name;
    if (Array.isArray(selected) && selected.includes(item.name)) cb.checked = true;
    label.appendChild(cb);
    label.appendChild(document.createTextNode(item.name));
    if (item.description) label.title = item.description;
    container.appendChild(label);
  });
  setCheckListMode(containerId, mode);
}

function setCheckListMode(containerId, mode) {
  const container = document.getElementById(containerId);
  if (mode === "custom") {
    container.classList.remove("disabled");
  } else {
    container.classList.add("disabled");
  }
}

function getCheckedValues(containerId) {
  const container = document.getElementById(containerId);
  return [...container.querySelectorAll("input:checked")].map(cb => cb.value);
}

function parseListValue(raw) {
  if (raw === null || raw === undefined) return { mode: "all", values: [] };
  if (Array.isArray(raw) && raw.length === 0) return { mode: "none", values: [] };
  if (Array.isArray(raw)) return { mode: "custom", values: raw };
  return { mode: "all", values: [] };
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
  renderCheckList("skills-list", availableSkills, [], "all");
  setCheckListMode("skills-list", "all");
  renderCheckList("tools-list", availableTools, [], "all");
  setCheckListMode("tools-list", "all");
  cardForm.querySelectorAll('input[name="skills_mode"]').forEach(r => {
    r.checked = r.value === "all";
    r.addEventListener("change", () => setCheckListMode("skills-list", cardForm.skills_mode.value));
  });
  cardForm.querySelectorAll('input[name="tools_mode"]').forEach(r => {
    r.checked = r.value === "all";
    r.addEventListener("change", () => setCheckListMode("tools-list", cardForm.tools_mode.value));
  });
  dialog.showModal();
});

function openDialog(card) {
  editingName = card.name;
  dialogTitle.textContent = `编辑 ${card.name}`;
  cardForm.name.value = card.name || "";
  cardForm.name.setAttribute("readonly", "");
  cardForm.prompt.value = card.prompt || "";

  const skillsParsed = parseListValue(card.skills);
  cardForm.skills_mode.value = skillsParsed.mode;
  renderCheckList("skills-list", availableSkills, skillsParsed.values, skillsParsed.mode);
  cardForm.querySelectorAll('input[name="skills_mode"]').forEach(r => {
    r.checked = r.value === skillsParsed.mode;
    r.addEventListener("change", () => setCheckListMode("skills-list", cardForm.skills_mode.value));
  });

  const toolsParsed = parseListValue(card.tools);
  cardForm.tools_mode.value = toolsParsed.mode;
  renderCheckList("tools-list", availableTools, toolsParsed.values, toolsParsed.mode);
  cardForm.querySelectorAll('input[name="tools_mode"]').forEach(r => {
    r.checked = r.value === toolsParsed.mode;
    r.addEventListener("change", () => setCheckListMode("tools-list", cardForm.tools_mode.value));
  });

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

  const modeToList = (mode, checkedValues) => {
    if (mode === "all") return null;
    if (mode === "none") return [];
    return checkedValues;
  };

  const data = {
    name: fd.get("name").trim(),
    prompt: fd.get("prompt"),
    skills: modeToList(fd.get("skills_mode"), getCheckedValues("skills-list")),
    tools: modeToList(fd.get("tools_mode"), getCheckedValues("tools-list")),
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
const [s, t] = await Promise.all([
  apiGet("available-skills"),
  apiGet("available-tools"),
]);
availableSkills = s || [];
availableTools = t || [];
await loadCards();
