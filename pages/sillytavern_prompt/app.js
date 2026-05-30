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
let activeName = null;
let editingName = null;
let availableSkills = [];
let availableTools = [];

// ── API ───────────────────────────────────────────────

async function apiGet(path) {
  return await bridge.apiGet(path);
}
async function apiPost(path, data) {
  return await bridge.apiPost(path, data);
}

// ── Skills / Tools 复选框 ─────────────────────────────

function renderCheckList(containerId, items, selected, mode) {
  const container = document.getElementById(containerId);
  if (!container) return;
  container.innerHTML = "";
  items.forEach(item => {
    const label = document.createElement("label");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.value = item.name;
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
  if (!container) return;
  if (mode === "custom") {
    container.classList.remove("disabled");
  } else {
    container.classList.add("disabled");
  }
}

function getCheckedValues(containerId) {
  const container = document.getElementById(containerId);
  if (!container) return [];
  return [...container.querySelectorAll("input:checked")].map(cb => cb.value);
}

function parseListValue(raw) {
  if (raw === null || raw === undefined) return { mode: "all", values: [] };
  if (Array.isArray(raw) && raw.length === 0) return { mode: "none", values: [] };
  if (Array.isArray(raw)) return { mode: "custom", values: raw };
  return { mode: "all", values: [] };
}

function setupModeRadios(prefix) {
  document.querySelectorAll(`input[name="${prefix}_mode"]`).forEach(r => {
    r.addEventListener("change", () => {
      setCheckListMode(`${prefix}-list`, document.querySelector(`input[name="${prefix}_mode"]:checked`)?.value);
    });
  });
}

// ── 列表 ──────────────────────────────────────────────

async function loadCards() {
  const sel = await apiGet("cards/active");
  activeName = sel?.name || null;
  cards = await apiGet("cards") || [];
  renderList();
}

function renderList() {
  cardList.innerHTML = "";
  cards.forEach(c => {
    const li = document.createElement("li");
    if (c.name === activeName) li.classList.add("active");

    const s = c.skills === null ? "all" : (c.skills?.length || "none");
    const t = c.tools === null ? "all" : (c.tools?.length || "none");

    li.innerHTML = `<span class="name">${esc(c.name)}</span>
      <span class="badges">
        <span class="badge">S:${s}</span>
        <span class="badge">T:${t}</span>
        ${c.name === activeName ? '<span class="badge" style="background:#2d6a4f">当前</span>' : ''}
      </span>`;
    li.addEventListener("click", () => selectCard(c.name));
    cardList.appendChild(li);
  });
}

// ── 详情 ──────────────────────────────────────────────

function renderDetail(card) {
  editor.innerHTML = `
    <div class="card-detail">
      <div style="display:flex;align-items:center;gap:12px;margin-bottom:16px">
        <h3 style="margin:0">${esc(card.name)}</h3>
        ${card.name === activeName
          ? '<span class="badge" style="background:#2d6a4f;font-size:12px;padding:4px 10px">当前使用中</span>'
          : `<button class="primary small" id="btn-select">设为当前角色</button>`
        }
      </div>
      ${field("prompt", card.prompt)}
      ${field("skills", card.skills === null ? "全部" : (card.skills?.length ? JSON.stringify(card.skills) : "无"), "json")}
      ${field("tools", card.tools === null ? "全部" : (card.tools?.length ? JSON.stringify(card.tools) : "无"), "json")}
      ${card.description ? field("description", card.description) : ""}
      ${card.personality ? field("personality", card.personality) : ""}
      ${card.scenario ? field("scenario", card.scenario) : ""}
      ${card.first_mes ? field("first_mes", card.first_mes) : ""}
      ${card.mes_example ? field("mes_example", card.mes_example) : ""}
      ${card.user_name ? field("user_name", card.user_name) : ""}
      ${card.persona_description ? field("persona_description", card.persona_description) : ""}
      <div class="actions">
        <button class="primary" id="btn-edit-detail">编辑</button>
        <button class="danger" id="btn-delete-detail">删除</button>
      </div>
    </div>
  `;

  const btnSelect = document.getElementById("btn-select");
  if (btnSelect) {
    btnSelect.addEventListener("click", async () => {
      await apiPost("cards/active", { name: card.name });
      activeName = card.name;
      renderList();
      renderDetail(card);
    });
  }

  document.getElementById("btn-edit-detail").addEventListener("click", () => openDialog(card));
  document.getElementById("btn-delete-detail").addEventListener("click", () => deleteCard(card.name));
}

async function selectCard(name) {
  const card = await apiGet(`cards/${encodeURIComponent(name)}`);
  if (!card || card.error) return;
  renderList();
  renderDetail(card);
}

function field(label, value, cls = "") {
  return `<div class="field"><label>${label}</label><div class="value ${cls}">${esc(String(value))}</div></div>`;
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
  setupModeRadios("skills");
  setupModeRadios("tools");
  document.querySelector('input[name="skills_mode"][value="all"]').checked = true;
  document.querySelector('input[name="tools_mode"][value="all"]').checked = true;
  dialog.showModal();
});

function openDialog(card) {
  editingName = card.name;
  dialogTitle.textContent = `编辑 ${card.name}`;
  cardForm.name.value = card.name || "";
  cardForm.name.setAttribute("readonly", "");
  cardForm.prompt.value = card.prompt || "";

  const skillsParsed = parseListValue(card.skills);
  renderCheckList("skills-list", availableSkills, skillsParsed.values, skillsParsed.mode);
  document.querySelectorAll('input[name="skills_mode"]').forEach(r => {
    r.checked = r.value === skillsParsed.mode;
  });

  const toolsParsed = parseListValue(card.tools);
  renderCheckList("tools-list", availableTools, toolsParsed.values, toolsParsed.mode);
  document.querySelectorAll('input[name="tools_mode"]').forEach(r => {
    r.checked = r.value === toolsParsed.mode;
  });

  setupModeRadios("skills");
  setupModeRadios("tools");

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
  await loadCards();
  selectCard(data.name);
});

// ── 删除 ──────────────────────────────────────────────

async function deleteCard(name) {
  if (!confirm(`确定删除角色卡「${name}」？`)) return;
  const resp = await apiPost("cards/delete", { name });
  if (resp?.ok === false) {
    alert("删除失败");
    return;
  }
  if (activeName === name) {
    activeName = null;
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
availableSkills = Array.isArray(s) ? s : [];
availableTools = Array.isArray(t) ? t : [];
await loadCards();
