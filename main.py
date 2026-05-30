"""astrbot_plugin_sillytavern_prompt — 自管理角色卡系统。

管理角色卡 JSON 文件，同步到 AstrBot persona 系统使 skills/tools 原生可用。
ST 分层人设通过 on_llm_request hook 追加到 system_prompt 前面。
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

EDITABLE_FIELDS = [
    "prompt", "skills", "tools", "description", "personality",
    "scenario", "first_mes", "mes_example", "user_name", "persona_description",
]

TOGGLEABLE_SECTIONS = [s[0] for s in SECTION_ORDER]


@register(PLUGIN_NAME, "laoin", "自管理角色卡系统：prompt+skills+分层人设", "v0.2.0")
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

        # 运行时: 每个会话当前加载的角色卡名
        self._active: dict[str, str] = {}
        # 运行时: 已导入的 ST PNG 角色卡 (按会话)
        self._imported_st_cards: dict[str, STCharCard] = {}

        # 注册 Web API 路由供前端页面调用
        ctx = context
        ctx.register_web_api(f"/{PLUGIN_NAME}/cards", self._api_list_cards, ["GET"], "列出所有角色卡")
        ctx.register_web_api(f"/{PLUGIN_NAME}/cards/<name>", self._api_get_card, ["GET"], "获取单张角色卡")
        ctx.register_web_api(f"/{PLUGIN_NAME}/cards/save", self._api_save_card, ["POST"], "保存角色卡")
        ctx.register_web_api(f"/{PLUGIN_NAME}/cards/delete", self._api_delete_card, ["POST"], "删除角色卡")

        logger.info(
            f"[ST-Prompt] 角色卡系统已加载，{len(self.store.list_all())} 张角色卡可用"
        )

    # ── Web API ─────────────────────────────────────────

    async def _api_list_cards(self):
        """GET /cards → [{name, skills, tools}, ...]"""
        cards = self.store.list_all()
        result = []
        for c in cards:
            result.append({
                "name": c.name,
                "skills": c.skills,
                "tools": c.tools,
            })
        return jsonify(result)

    async def _api_get_card(self, name: str):
        """GET /cards/<name> → card object"""
        name = unquote(name)
        card = self.store.load(name)
        if not card:
            return jsonify({"error": "not found"}), 404
        return jsonify(card.to_dict())

    async def _api_save_card(self):
        """POST /cards/save {name, prompt, skills, ...}"""
        data = await request.get_json()
        name = data.get("name", "").strip()
        if not name:
            return jsonify({"error": "name is required"}), 400
        card = CharacterCard.from_dict(data)
        self.store.save(card)
        return jsonify({"ok": True, "name": name})

    async def _api_delete_card(self):
        """POST /cards/delete {name}"""
        data = await request.get_json()
        name = data.get("name", "").strip()
        if not name:
            return jsonify({"error": "name is required"}), 400
        self.store.delete(name)
        await self.store.delete_from_astrbot(name, self.context.persona_manager)
        return jsonify({"ok": True})

    # ── 辅助 ────────────────────────────────────────────

    def _active_card(self, umo: str) -> CharacterCard:
        name = self._active.get(umo, self.config.get("default_card", "default"))
        card = self.store.load(name)
        if not card:
            card = self.store.load("default") or DEFAULT_CARD
            self._active[umo] = "default"
        return card

    def _st_card(self, umo: str) -> Optional[STCharCard]:
        return self._imported_st_cards.get(umo)

    # ── LLM 钩子 ────────────────────────────────────────

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        """在 AstrBot 编译好 persona+skills+tools 后，追加 ST 分层人设。

        req.system_prompt 此时已包含:
          - persona.prompt
          - ## Skills 文本
          - ## Tools 文本
        我们在最前面追加 ST 的分层人设内容。
        """
        umo = event.unified_msg_origin
        card = self._active_card(umo)
        st = self._st_card(umo)

        if not card.is_loaded:
            return

        # 组装 ST 分层人设
        st_layers = self.assembler.build_system_prompt(
            character=st,
            preset=None,  # 不使用 preset，角色卡已包含 prompt
            user_name=card.user_name,
            persona_description=card.persona_description,
            wrap_labels=self.config.get("wrap_sections_with_labels", True),
            include_examples=self.config.get("include_dialogue_examples", True),
            enabled_sections=self.config.get("enabled_sections", {}),
            card_override_prompt=card.prompt if not st else st.system_prompt or card.prompt,
        )

        # 追加 description/personality/scenario 等分层内容
        extra_layers = []
        if card.description:
            text = card.description
            if self.config.get("wrap_sections_with_labels", True):
                char_name = st.name if st and st.name else card.name
                text = f"[{char_name}'s Description]\n{text}"
            extra_layers.append(self.macro.substitute(text, user_name=card.user_name, char_name=st.name if st else card.name))

        if card.personality:
            text = card.personality
            if self.config.get("wrap_sections_with_labels", True):
                char_name = st.name if st and st.name else card.name
                text = f"[{char_name}'s Personality]\n{text}"
            extra_layers.append(self.macro.substitute(text, user_name=card.user_name, char_name=st.name if st else card.name))

        if card.scenario:
            text = card.scenario
            if self.config.get("wrap_sections_with_labels", True):
                text = f"[Scenario]\n{text}"
            extra_layers.append(self.macro.substitute(text, user_name=card.user_name, char_name=st.name if st else card.name))

        if card.persona_description:
            text = card.persona_description
            if self.config.get("wrap_sections_with_labels", True):
                text = f"[{card.user_name}'s Persona]\n{text}"
            extra_layers.append(text)

        if card.mes_example and self.config.get("include_dialogue_examples", True):
            text = card.mes_example
            if self.config.get("wrap_sections_with_labels", True):
                text = f"[Example Dialogue]\n{text}"
            extra_layers.append(text)

        # 合并: ST 分层人设在前，AstrBot persona+skills+tools 在后
        all_layers = [st_layers] + extra_layers if st_layers.strip() else extra_layers
        astrbot_native = req.system_prompt or ""

        if all_layers:
            assembled = "\n\n".join(l for l in all_layers if l.strip())
            if astrbot_native.strip():
                req.system_prompt = f"{assembled}\n\n{astrbot_native}"
            else:
                req.system_prompt = assembled

    # ── /st_char ────────────────────────────────────────

    @filter.command("st_char")
    async def cmd_st_char(self, event: AstrMessageEvent):
        """角色卡管理"""
        args = event.message_str.replace("/st_char", "").strip().split(maxsplit=1)
        sub = args[0].lower() if args else ""
        rest = args[1] if len(args) > 1 else ""

        if sub == "load":
            yield event.plain_result(await self._char_load(event, rest))
        elif sub == "list":
            yield event.plain_result(self._char_list())
        elif sub == "show":
            yield event.plain_result(self._char_show(event))
        elif sub == "create":
            yield event.plain_result(await self._char_create(rest))
        elif sub == "delete":
            yield event.plain_result(await self._char_delete(rest))
        elif sub == "edit":
            yield event.plain_result(self._char_edit(event, rest))
        elif sub == "import":
            yield event.plain_result(self._char_import(event, rest))
        else:
            yield event.plain_result(
                "🎭 **角色卡管理**\n"
                "/st_char load <名称>    — 加载角色卡\n"
                "/st_char list           — 列出所有角色卡\n"
                "/st_char show           — 查看当前角色卡\n"
                "/st_char create <名称>  — 从 AstrBot 当前人格创建角色卡\n"
                "/st_char delete <名称>  — 删除角色卡\n"
                "/st_char edit <字段> <值> — 编辑角色卡字段\n"
                "/st_char import <文件>  — 从 ST PNG/JSON 导入\n"
                f"\n可编辑字段: {', '.join(EDITABLE_FIELDS)}"
            )

    async def _char_load(self, event: AstrMessageEvent, name: str) -> str:
        if not name:
            return "用法: /st_char load <名称>"
        card = self.store.load(name)
        if not card:
            names = self.store.list_names()
            return f"未找到角色卡: {name}\n可用: {', '.join(names)}"

        umo = event.unified_msg_origin
        self._active[umo] = name
        r = await self.store.sync_to_astrbot(
            card, self.context.persona_manager, self.context.conversation_manager, umo
        )
        return r

    def _char_list(self) -> str:
        cards = self.store.list_all()
        if not cards:
            return "没有角色卡。使用 /st_char create <名称> 创建"
        lines = ["📁 **角色卡列表**"]
        for c in cards:
            s = "all" if c.skills is None else (str(len(c.skills)) if c.skills else "none")
            t = "all" if c.tools is None else (str(len(c.tools)) if c.tools else "none")
            lines.append(f"  - {c.name}  (skills: {s}, tools: {t})")
        return "\n".join(lines)

    def _char_show(self, event: AstrMessageEvent) -> str:
        umo = event.unified_msg_origin
        card = self._active_card(umo)
        st = self._st_card(umo)
        lines = [
            f"🎭 **{card.name}**",
            f"prompt: {card.prompt[:300]}{'...' if len(card.prompt) > 300 else ''}",
            f"skills: {card.skills}",
            f"tools: {card.tools}",
            f"description: {card.description[:100]}{'...' if len(card.description) > 100 else ''}" if card.description else "",
            f"personality: {card.personality[:100]}{'...' if len(card.personality) > 100 else ''}" if card.personality else "",
            f"scenario: {card.scenario[:100]}{'...' if len(card.scenario) > 100 else ''}" if card.scenario else "",
            f"first_mes: {card.first_mes[:100]}{'...' if len(card.first_mes) > 100 else ''}" if card.first_mes else "",
            f"mes_example: {'有' if card.mes_example else '无'}",
            f"user_name: {card.user_name}",
            f"persona_description: {card.persona_description[:100]}{'...' if len(card.persona_description) > 100 else ''}" if card.persona_description else "",
            f"\n导入的 ST 角色卡: {st.name}" if st else "",
        ]
        return "\n".join(l for l in lines if l)

    async def _char_create(self, name: str) -> str:
        if not name:
            return "用法: /st_char create <名称>\n将从 AstrBot 当前默认人格复制 prompt/skills/tools"
        if self.store.exists(name):
            return f"角色卡 {name} 已存在，使用 /st_char edit 修改或 /st_char delete 删除"

        # 从 AstrBot 当前默认人格读取
        try:
            mgr = self.context.persona_manager
            persona_id = mgr.default_persona  # 默认人格名称
            persona = mgr.get_persona_v3_by_id(persona_id)
            if persona:
                prompt = persona.get("prompt", "") if isinstance(persona, dict) else ""
                skills = persona.get("skills") if isinstance(persona, dict) else None
                tools = persona.get("tools") if isinstance(persona, dict) else None
            else:
                prompt = f"You are {name}."
                skills = None
                tools = None
        except Exception as e:
            logger.warning(f"[ST-Prompt] 读取 AstrBot 人格失败，使用默认: {e}")
            prompt = f"You are {name}."
            skills = None
            tools = None

        card = CharacterCard(name=name, prompt=prompt, skills=skills, tools=tools)
        self.store.save(card)
        return f"✅ 已创建角色卡 **{name}**\nskills: {skills}\ntools: {tools}"

    async def _char_delete(self, name: str) -> str:
        if not name:
            return "用法: /st_char delete <名称>"
        if not self.store.exists(name):
            return f"角色卡 {name} 不存在"
        self.store.delete(name)
        r = await self.store.delete_from_astrbot(name, self.context.persona_manager)
        return f"✅ 已删除角色卡 **{name}**\n{r}"

    def _char_edit(self, event: AstrMessageEvent, rest: str) -> str:
        parts = rest.split(maxsplit=1)
        if len(parts) < 2:
            return f"用法: /st_char edit <字段> <值>\n可编辑: {', '.join(EDITABLE_FIELDS)}"
        field, value = parts[0], parts[1]

        if field not in EDITABLE_FIELDS:
            return f"无效字段: {field}\n可编辑: {', '.join(EDITABLE_FIELDS)}"

        umo = event.unified_msg_origin
        card = self._active_card(umo)

        if field in ("skills", "tools"):
            # 解析: null, [], ["a","b"]
            val = value.strip()
            if val.lower() == "null":
                setattr(card, field, None)
            elif val == "[]":
                setattr(card, field, [])
            else:
                try:
                    parsed = json.loads(val)
                    if isinstance(parsed, list):
                        setattr(card, field, parsed)
                    else:
                        return f"{field} 必须是 null, [] 或 [\"name\",...]"
                except json.JSONDecodeError:
                    return f"{field} 格式错误，需要 JSON 数组，如 [\"memory\"]"
        else:
            setattr(card, field, value)

        self.store.save(card)
        return f"✅ {field} 已更新"

    def _char_import(self, event: AstrMessageEvent, filename: str) -> str:
        """导入 ST PNG/JSON 角色卡，作为额外的 ST 分层人设来源。"""
        if not filename:
            return "用法: /st_char import <文件路径>\n文件需在 characters/ 目录或提供完整路径"

        fp = os.path.join(self.plugin_dir, "characters", filename)
        if not os.path.isfile(fp):
            fp = filename  # 尝试作为绝对路径
        if not os.path.isfile(fp):
            return f"未找到文件: {filename}"

        try:
            st_card = CardParser.from_file(fp)
            umo = event.unified_msg_origin
            self._imported_st_cards[umo] = st_card
            return (
                f"✅ 已导入 ST 角色卡: **{st_card.name}**\n"
                f"格式: {st_card.spec} v{st_card.spec_version}\n"
                f"此角色卡的分层人设 (description/personality/scenario/mes_example) 将在对话中生效"
            )
        except Exception as e:
            return f"❌ 导入失败: {e}"

    # ── /st_prompt ──────────────────────────────────────

    @filter.command("st_prompt")
    async def cmd_st_prompt(self, event: AstrMessageEvent):
        """查看/控制 system_prompt"""
        args = event.message_str.replace("/st_prompt", "").strip().split(maxsplit=1)
        sub = args[0].lower() if args else ""

        if sub == "show":
            yield event.plain_result(self._prompt_show(event))
        elif sub == "toggle":
            section = args[1] if len(args) > 1 else ""
            yield event.plain_result(self._prompt_toggle(section))
        else:
            available = ", ".join(TOGGLEABLE_SECTIONS)
            yield event.plain_result(
                "/st_prompt show — 查看当前角色卡内容\n"
                f"/st_prompt toggle <section> — 切换 section\n可切换: {available}"
            )

    def _prompt_show(self, event: AstrMessageEvent) -> str:
        umo = event.unified_msg_origin
        card = self._active_card(umo)
        st = self._st_card(umo)
        lines = [
            f"角色卡: {card.name}",
            f"skills: {card.skills}",
            f"tools: {card.tools}",
            f"导入 ST 卡: {st.name}" if st else "",
            "\n── prompt ──",
            card.prompt[:1500] + ("..." if len(card.prompt) > 1500 else ""),
        ]
        # 显示额外分层
        for label, val in [
            ("description", card.description),
            ("personality", card.personality),
            ("scenario", card.scenario),
            ("first_mes", card.first_mes),
            ("mes_example", card.mes_example[:500] if card.mes_example else ""),
            ("persona_description", card.persona_description),
        ]:
            if val:
                lines.append(f"\n── {label} ──\n{val[:500]}{'...' if len(val) > 500 else ''}")
        return "\n".join(lines)

    def _prompt_toggle(self, section: str) -> str:
        if not section:
            available = ", ".join(TOGGLEABLE_SECTIONS)
            return f"用法: /st_prompt toggle <section>\n可选: {available}"
        if section not in TOGGLEABLE_SECTIONS:
            return f"无效 section: {section}\n可选: {', '.join(TOGGLEABLE_SECTIONS)}"

        enabled = self.config.setdefault("enabled_sections", {})
        cur = enabled.get(section, True)
        enabled[section] = not cur
        self.config.save_config()
        return f"Section **{section}** {'✅ 启用' if not cur else '❌ 禁用'}"
