"""astrbot_plugin_sillytavern_prompt — 自管理角色卡系统。

角色卡 JSON 文件通过 WebUI 管理，加载时同步到 AstrBot persona。
ST 分层人设通过 on_llm_request hook 追加到 system_prompt 前面。
角色卡选择持久化到 data/state.json，只有 WebUI 可以修改。
"""

import json
import os
from typing import Optional
from urllib.parse import unquote

from quart import jsonify, request
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig
from astrbot.api.provider import ProviderRequest

from .card_parser import CharacterCard as STCharCard, CardParser
from .card_store import CardStore, CharacterCard, DEFAULT_CARD
from .prompt_assembler import PromptAssembler, SECTION_ORDER
from .macro_engine import MacroEngine

PLUGIN_NAME = "sillytavern_prompt"

# 仅 WebUI 可修改的字段
EDITABLE_FIELDS = [
    "prompt", "skills", "tools", "description", "personality",
    "scenario", "first_mes", "mes_example", "user_name", "persona_description",
]

TOGGLEABLE_SECTIONS = [s[0] for s in SECTION_ORDER]

# ── 持久化状态文件 ─────────────────────────────────────

def _load_state(state_file: str) -> dict:
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def _save_state(state_file: str, state: dict) -> None:
    try:
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except IOError as e:
        print(f"[ST-Prompt] 保存状态失败: {e}")


@register(PLUGIN_NAME, "laoin", "自管理角色卡系统：prompt+skills+分层人设", "v0.2.1")
class SillyTavernPromptPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        self.plugin_dir = os.path.dirname(os.path.abspath(__file__))
        self.data_dir = os.path.join(self.plugin_dir, "data")
        self.cards_dir = os.path.join(self.data_dir, "cards")
        os.makedirs(self.cards_dir, exist_ok=True)

        self.macro = MacroEngine()
        self.assembler = PromptAssembler(macro_engine=self.macro)
        self.store = CardStore(self.cards_dir)

        # 持久化状态: 全局活跃角色卡名
        self._state_file = os.path.join(self.data_dir, "state.json")
        self._state = _load_state(self._state_file)
        self._active_name: str = self._state.get("active_card", self.config.get("default_card", "default"))

        # 运行时: 已导入的 ST PNG 角色卡 (按会话)
        self._imported_st_cards: dict[str, STCharCard] = {}

        # 注册 Web API 路由供前端页面调用
        ctx = context
        ctx.register_web_api(f"/{PLUGIN_NAME}/cards", self._api_list_cards, ["GET"], "列出角色卡")
        ctx.register_web_api(f"/{PLUGIN_NAME}/cards/<name>", self._api_get_card, ["GET"], "获取单张角色卡")
        ctx.register_web_api(f"/{PLUGIN_NAME}/cards/save", self._api_save_card, ["POST"], "保存角色卡")
        ctx.register_web_api(f"/{PLUGIN_NAME}/cards/delete", self._api_delete_card, ["POST"], "删除角色卡")
        ctx.register_web_api(f"/{PLUGIN_NAME}/cards/select", self._api_select_card, ["GET", "POST"], "获取/设置当前角色卡")
        ctx.register_web_api(f"/{PLUGIN_NAME}/available-skills", self._api_available_skills, ["GET"], "获取可用 skills")
        ctx.register_web_api(f"/{PLUGIN_NAME}/available-tools", self._api_available_tools, ["GET"], "获取可用 tools")

        logger.info(
            f"[ST-Prompt] 已加载 {len(self.store.list_all())} 张角色卡, 当前选中: {self._active_name}"
        )

    # ── 角色卡引用 ─────────────────────────────────────

    def _active_card(self) -> CharacterCard:
        """全局唯一的活跃角色卡（持久化）。"""
        card = self.store.load(self._active_name)
        if not card:
            card = self.store.load("default") or DEFAULT_CARD
            self._active_name = card.name
        return card

    def _st_card(self, umo: str) -> Optional[STCharCard]:
        return self._imported_st_cards.get(umo)

    def _selected_card_data(self) -> dict:
        """返回当前选中的角色卡概要，供前端显示。"""
        card = self._active_card()
        return {"name": card.name, "skills": card.skills, "tools": card.tools}

    # ── Web API ─────────────────────────────────────────

    async def _api_list_cards(self):
        cards = self.store.list_all()
        return jsonify([{
            "name": c.name,
            "skills": c.skills,
            "tools": c.tools,
        } for c in cards])

    async def _api_get_card(self, name: str):
        name = unquote(name)
        card = self.store.load(name)
        if not card:
            return jsonify({"error": "not found"}), 404
        return jsonify(card.to_dict())

    async def _api_save_card(self):
        data = await request.get_json()
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"error": "name is required"}), 400
        card = CharacterCard.from_dict(data)
        ok = self.store.save(card)
        return jsonify({"ok": ok, "name": name})

    async def _api_delete_card(self):
        data = await request.get_json()
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"error": "name is required"}), 400
        ok = self.store.delete(name)
        await self.store.delete_from_astrbot(name, self.context.persona_manager)
        if self._active_name == name:
            self._active_name = "default"
            self._state["active_card"] = "default"
            _save_state(self._state_file, self._state)
        return jsonify({"ok": ok})

    async def _api_select_card(self):
        """GET /cards/select → 返回当前选中的角色卡名。
           POST /cards/select {name} → 持久化选中角色卡。"""
        if request.method == "GET":
            return jsonify(self._selected_card_data())

        data = await request.get_json()
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"error": "name is required"}), 400
        card = self.store.load(name)
        if not card:
            return jsonify({"error": f"card '{name}' not found"}), 404

        self._active_name = name
        self._state["active_card"] = name
        _save_state(self._state_file, self._state)

        return jsonify({"ok": True, "name": name})

    async def _api_available_skills(self):
        try:
            from astrbot.core.skills.skill_manager import SkillManager
            sm = SkillManager()
            skills = sm.list_skills(active_only=False)
            return jsonify([{"name": s.name, "description": s.description or ""} for s in skills])
        except Exception as e:
            logger.warning(f"[ST-Prompt] 获取 skills 列表失败: {e}")
            return jsonify([])

    async def _api_available_tools(self):
        try:
            tm = self.context.get_llm_tool_manager()
            result = []
            for f in tm.func_list:
                if getattr(f, "active", True):
                    result.append({"name": f.name, "description": getattr(f, "description", "") or ""})
            for f in tm.builtin_func_list.values():
                if getattr(f, "active", True):
                    result.append({"name": f.name, "description": getattr(f, "description", "") or ""})
            return jsonify(result)
        except Exception as e:
            logger.warning(f"[ST-Prompt] 获取 tools 列表失败: {e}")
            return jsonify([])

    # ── LLM 钩子 ────────────────────────────────────────

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        """AstrBot 已编译好 persona+skills+tools → 追加 ST 分层人设。"""
        umo = event.unified_msg_origin
        card = self._active_card()
        st = self._st_card(umo)

        if not card.is_loaded:
            return

        # 每次请求时同步角色卡到 AstrBot persona + 当前会话
        try:
            await self.store.sync_to_astrbot(
                card, self.context.persona_manager, self.context.conversation_manager, umo
            )
        except Exception as e:
            logger.warning(f"[ST-Prompt] 同步 persona 失败: {e}")

        # 组装 ST 分层人设
        st_layers = self.assembler.build_system_prompt(
            character=st,
            preset=None,
            user_name=card.user_name,
            persona_description=card.persona_description,
            wrap_labels=self.config.get("wrap_sections_with_labels", True),
            include_examples=self.config.get("include_dialogue_examples", True),
            enabled_sections=self.config.get("enabled_sections", {}),
            card_override_prompt=card.prompt if not st else st.system_prompt or card.prompt,
        )

        char_name = st.name if st and st.name else card.name
        extra = []
        if card.description:
            label = f"[{char_name}'s Description]\n" if self.config.get("wrap_sections_with_labels", True) else ""
            extra.append(label + self.macro.substitute(card.description, user_name=card.user_name, char_name=char_name))
        if card.personality:
            label = f"[{char_name}'s Personality]\n" if self.config.get("wrap_sections_with_labels", True) else ""
            extra.append(label + self.macro.substitute(card.personality, user_name=card.user_name, char_name=char_name))
        if card.scenario:
            label = "[Scenario]\n" if self.config.get("wrap_sections_with_labels", True) else ""
            extra.append(label + self.macro.substitute(card.scenario, user_name=card.user_name, char_name=char_name))
        if card.persona_description:
            label = f"[{card.user_name}'s Persona]\n" if self.config.get("wrap_sections_with_labels", True) else ""
            extra.append(label + card.persona_description)
        if card.mes_example and self.config.get("include_dialogue_examples", True):
            label = "[Example Dialogue]\n" if self.config.get("wrap_sections_with_labels", True) else ""
            extra.append(label + card.mes_example)

        layers = [st_layers] + extra if st_layers.strip() else extra
        native = req.system_prompt or ""
        if layers:
            assembled = "\n\n".join(l for l in layers if l.strip())
            req.system_prompt = f"{assembled}\n\n{native}" if native.strip() else assembled

    # ── /st_char 指令（只读查看）─────────────────────────

    @filter.command("st_char")
    async def cmd_st_char(self, event: AstrMessageEvent):
        """角色卡信息查看（修改请通过 WebUI 后台）。"""
        parts = event.message_str.split()
        sub = parts[1].lower() if len(parts) > 1 else ""

        if sub == "list":
            yield event.plain_result(self._char_list())
        elif sub == "show":
            yield event.plain_result(self._char_show())
        elif sub == "import":
            rest = " ".join(parts[2:]) if len(parts) > 2 else ""
            yield event.plain_result(self._char_import(event, rest))
        else:
            yield event.plain_result(
                "🎭 角色卡信息\n"
                "/st_char list     — 列出所有角色卡\n"
                "/st_char show     — 查看当前角色卡\n"
                "/st_char import <文件> — 导入 ST PNG/JSON\n\n"
                "💡 新建/编辑/删除/切换角色卡请通过 WebUI 后台"
            )

    def _char_list(self) -> str:
        try:
            cards = self.store.list_all()
        except Exception as e:
            logger.error(f"[ST-Prompt] 读取角色卡列表失败: {e}")
            return f"读取失败: {e}"

        if not cards:
            return "没有角色卡。请通过 WebUI 后台新建"

        active = self._active_name
        lines = ["📁 角色卡列表"]
        for c in cards:
            s = "all" if c.skills is None else (str(len(c.skills)) if c.skills else "none")
            t = "all" if c.tools is None else (str(len(c.tools)) if c.tools else "none")
            marker = " ✅" if c.name == active else ""
            lines.append(f"  {c.name}{marker}  (skills:{s} tools:{t})")
        return "\n".join(lines)

    def _char_show(self) -> str:
        card = self._active_card()
        lines = [
            f"🎭 {card.name}",
            f"prompt: {card.prompt[:300]}{'...' if len(card.prompt) > 300 else ''}",
            f"skills: {card.skills}",
            f"tools: {card.tools}",
        ]
        for label, val in [
            ("description", card.description),
            ("personality", card.personality),
            ("scenario", card.scenario),
            ("first_mes", card.first_mes),
            ("mes_example", card.mes_example),
            ("persona_description", card.persona_description),
        ]:
            if val:
                lines.append(f"\n── {label} ──\n{val[:500]}{'...' if len(val) > 500 else ''}")
        return "\n".join(lines)

    def _char_import(self, event: AstrMessageEvent, filename: str) -> str:
        if not filename:
            return "用法: /st_char import <文件路径>"
        fp = os.path.join(self.plugin_dir, "characters", filename)
        if not os.path.isfile(fp):
            fp = filename
        if not os.path.isfile(fp):
            return f"未找到文件: {filename}"
        try:
            st = CardParser.from_file(fp)
            umo = event.unified_msg_origin
            self._imported_st_cards[umo] = st
            return f"✅ 已导入: {st.name} ({st.spec} v{st.spec_version})"
        except Exception as e:
            return f"导入失败: {e}"

    # ── /st_prompt 指令 ─────────────────────────────────

    @filter.command("st_prompt")
    async def cmd_st_prompt(self, event: AstrMessageEvent):
        parts = event.message_str.split()
        sub = parts[1].lower() if len(parts) > 1 else ""

        if sub == "show":
            yield event.plain_result(self._prompt_show())
        elif sub == "toggle":
            section = parts[2] if len(parts) > 2 else ""
            yield event.plain_result(self._prompt_toggle(section))
        else:
            yield event.plain_result(
                "/st_prompt show            — 查看当前角色卡\n"
                "/st_prompt toggle <section> — 切换 section"
            )

    def _prompt_show(self) -> str:
        card = self._active_card()
        return (
            f"当前角色卡: {card.name}\n"
            f"skills: {card.skills}\ntools: {card.tools}\n\n"
            f"── prompt ──\n{card.prompt[:2000]}{'...' if len(card.prompt) > 2000 else ''}"
        )

    def _prompt_toggle(self, section: str) -> str:
        if not section or section not in TOGGLEABLE_SECTIONS:
            return f"用法: /st_prompt toggle <section>\n可选: {', '.join(TOGGLEABLE_SECTIONS)}"
        enabled = self.config.setdefault("enabled_sections", {})
        cur = enabled.get(section, True)
        enabled[section] = not cur
        self.config.save_config()
        return f"Section {section}: {'✅ 启用' if not cur else '❌ 禁用'}"
