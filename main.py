"""astrbot_plugin_sillytavern_prompt — SillyTavern 风格 Prompt 管理插件。

移植 SillyTavern 的分层 Prompt 组装系统到 AstrBot：
- 导入 SillyTavern PNG/JSON 角色卡
- 系统提示词预设管理
- on_llm_request hook 接管 system_prompt
- slash 指令运行时管理
"""

import json
import os
import shutil
from typing import Optional

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig
from astrbot.api.provider import ProviderRequest

from card_parser import CharacterCard, CardParser
from prompt_assembler import PromptAssembler, SECTION_ORDER
from preset_store import PresetStore, PromptPreset
from macro_engine import MacroEngine

PLUGIN_NAME = "sillytavern_prompt"

# 可切换的 section 标识符（主组件顺序）
TOGGLEABLE_SECTIONS = [
    s[0] for s in SECTION_ORDER
]


@register(PLUGIN_NAME, "laoin", "SillyTavern 风格 Prompt 管理器：角色卡导入、分层提示词、人设保持", "v0.1.0")
class SillyTavernPromptPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        # 目录
        self.plugin_dir = os.path.dirname(os.path.abspath(__file__))
        self.data_dir = os.path.join(self.plugin_dir, "data")
        self.characters_dir = os.path.join(self.plugin_dir, "characters")
        self.presets_builtin_dir = os.path.join(self.plugin_dir, "presets")
        os.makedirs(self.data_dir, exist_ok=True)
        os.makedirs(self.characters_dir, exist_ok=True)

        # 组件
        self.macro = MacroEngine()
        self.assembler = PromptAssembler(macro_engine=self.macro)
        self.preset_store = PresetStore(self.presets_builtin_dir)
        self._state_file = os.path.join(self.data_dir, "state.json")

        # 运行时状态
        self._state = self._load_state()

        # 当前加载的角色卡 (按 unified_msg_origin 区分)
        self._characters: dict[str, CharacterCard] = {}

        logger.info(f"[ST-Prompt] 插件已加载，{len(self.preset_store.list_all())} 个预设可用")

    # ── 状态持久化 ──────────────────────────────────────

    def _load_state(self) -> dict:
        try:
            with open(self._state_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {
                "active_preset": self.config.get("default_preset", "default"),
                "user_name": self.config.get("default_user_name", "User"),
                "persona_description": self.config.get("default_persona", ""),
                "enabled_sections": {},
            }

    def _save_state(self) -> None:
        try:
            with open(self._state_file, "w", encoding="utf-8") as f:
                json.dump(self._state, f, ensure_ascii=False, indent=2)
        except IOError as e:
            logger.error(f"[ST-Prompt] 保存状态失败: {e}")

    def _get_active_preset(self) -> Optional[PromptPreset]:
        name = self._state.get("active_preset", "default")
        preset = self.preset_store.load(name)
        if not preset:
            # 回退到第一个可用预设
            presets = self.preset_store.list_all()
            if presets:
                preset = presets[0]
                self._state["active_preset"] = preset.name
        return preset

    def _get_active_character(self, umo: str = "") -> Optional[CharacterCard]:
        return self._characters.get(umo)

    # ── LLM 钩子 ────────────────────────────────────────

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        """每次 LLM 调用前组装并注入 system_prompt。"""
        umo = event.unified_msg_origin
        character = self._get_active_character(umo)
        preset = self._get_active_preset()

        if not preset:
            logger.warning("[ST-Prompt] 没有可用的预设，跳过 prompt 注入")
            return

        # 组装
        assembled = self.assembler.build_system_prompt(
            character=character,
            preset=preset,
            user_name=self._state.get("user_name", "User"),
            persona_description=self._state.get("persona_description", ""),
            wrap_labels=self.config.get("wrap_sections_with_labels", True),
            include_examples=self.config.get("include_dialogue_examples", True),
            enabled_sections=self._state.get("enabled_sections", {}),
        )

        if not assembled:
            return

        # 接管 system_prompt
        if self.config.get("override_astrbot_persona", True):
            req.system_prompt = assembled
        else:
            # 在原有人设前追加
            existing = req.system_prompt or ""
            req.system_prompt = f"{assembled}\n\n---\n\n{existing}"

    # ── 指令：角色卡管理 ─────────────────────────────────

    @filter.command("st_char")
    async def cmd_st_char(self, event: AstrMessageEvent):
        """角色卡管理: /st_char <load|list|show|unload> [参数]"""
        args = event.message_str.replace("/st_char", "").strip().split(maxsplit=1)
        sub = args[0].lower() if args else ""

        if sub == "load":
            yield event.plain_result(await self._char_load(event, args[1] if len(args) > 1 else ""))
        elif sub == "list":
            yield event.plain_result(await self._char_list())
        elif sub == "show":
            yield event.plain_result(await self._char_show(event))
        elif sub == "unload":
            yield event.plain_result(await self._char_unload(event))
        else:
            yield event.plain_result(
                "📋 **角色卡管理**\n"
                "/st_char load <文件名> — 加载角色卡 (PNG/JSON)\n"
                "/st_char list — 列出所有角色卡\n"
                "/st_char show — 查看当前角色\n"
                "/st_char unload — 卸载当前角色"
            )

    async def _char_load(self, event: AstrMessageEvent, filename: str) -> str:
        if not filename:
            return "用法: /st_char load <文件名>\n文件请放在 characters/ 目录下"
        filepath = os.path.join(self.characters_dir, filename)
        if not os.path.isfile(filepath):
            # 也尝试不区分大小写
            found = None
            for f in os.listdir(self.characters_dir):
                if f.lower() == filename.lower():
                    found = f
                    break
            if found:
                filepath = os.path.join(self.characters_dir, found)
            else:
                files = os.listdir(self.characters_dir)
                pngs = [f for f in files if f.endswith((".png", ".json"))]
                return f"未找到角色卡: {filename}\n\n可用的文件:\n" + "\n".join(f"  - {f}" for f in pngs) if pngs else f"未找到角色卡: {filename}\n\ncharacters/ 目录中没有角色卡文件"

        try:
            card = CardParser.from_file(filepath)
            umo = event.unified_msg_origin
            self._characters[umo] = card
            return (
                f"✅ 已加载角色卡: **{card.name}**\n"
                f"格式: {card.spec} v{card.spec_version}\n"
                f"描述: {card.description[:100]}{'...' if len(card.description) > 100 else ''}"
            )
        except Exception as e:
            logger.error(f"[ST-Prompt] 加载角色卡失败: {e}")
            return f"❌ 加载失败: {e}"

    async def _char_list(self) -> str:
        files = sorted(os.listdir(self.characters_dir))
        pngs = [f for f in files if f.endswith((".png", ".json"))]
        if not pngs:
            return "characters/ 目录中没有角色卡文件\n\n将 SillyTavern 角色卡 PNG 放入此目录后使用 /st_char load <文件名> 加载"
        return "📁 **角色卡列表**\n" + "\n".join(f"  - {f}" for f in pngs)

    async def _char_show(self, event: AstrMessageEvent) -> str:
        umo = event.unified_msg_origin
        card = self._get_active_character(umo)
        if not card:
            return "当前没有加载角色卡。使用 /st_char load <文件名> 加载"

        lines = [
            f"🎭 **{card.name}** ({card.spec} v{card.spec_version})",
            f"描述: {card.description[:200]}{'...' if len(card.description) > 200 else ''}",
            f"性格: {card.personality[:200]}{'...' if len(card.personality) > 200 else ''}",
            f"场景: {card.scenario[:200]}{'...' if len(card.scenario) > 200 else ''}",
            f"开场白: {card.first_mes[:100]}{'...' if len(card.first_mes) > 100 else ''}" if card.first_mes else "",
            f"对话示例: {'有' if card.mes_example else '无'}",
            f"自定义主提示词: {'有' if card.system_prompt else '无'}",
            f"历史后指令: {'有' if card.post_history_instructions else '无'}",
            f"创建者: {card.creator}" if card.creator else "",
            f"标签: {', '.join(card.tags)}" if card.tags else "",
            f"来源: {os.path.basename(card.source_file)}",
        ]
        return "\n".join(l for l in lines if l)

    async def _char_unload(self, event: AstrMessageEvent) -> str:
        umo = event.unified_msg_origin
        if umo in self._characters:
            card = self._characters.pop(umo)
            return f"已卸载角色卡: {card.name}"
        return "当前没有加载角色卡"

    # ── 指令：预设管理 ───────────────────────────────────

    @filter.command("st_preset")
    async def cmd_st_preset(self, event: AstrMessageEvent):
        """预设管理: /st_preset <load|list|show> [参数]"""
        args = event.message_str.replace("/st_preset", "").strip().split(maxsplit=1)
        sub = args[0].lower() if args else ""

        if sub == "load":
            name = args[1] if len(args) > 1 else ""
            yield event.plain_result(await self._preset_load(name))
        elif sub == "list":
            yield event.plain_result(await self._preset_list())
        elif sub == "show":
            yield event.plain_result(await self._preset_show())
        else:
            yield event.plain_result(
                "📋 **预设管理**\n"
                "/st_preset load <名称> — 加载预设\n"
                "/st_preset list — 列出所有预设\n"
                "/st_preset show — 查看当前预设内容\n"
                "\n💡 预设文件在 presets/ 目录，也可通过 WebUI 上传"
            )

    async def _preset_load(self, name: str) -> str:
        if not name:
            return "用法: /st_preset load <名称>"
        preset = self.preset_store.load(name)
        if not preset:
            names = self.preset_store.list_names()
            return f"未找到预设: {name}\n\n可用预设:\n" + "\n".join(f"  - {n}" for n in names)
        self._state["active_preset"] = name
        self._save_state()
        return f"✅ 已加载预设: **{name}**\n内容: {preset.content[:200]}{'...' if len(preset.content) > 200 else ''}"

    async def _preset_list(self) -> str:
        presets = self.preset_store.list_all()
        if not presets:
            return "没有可用的预设"
        active = self._state.get("active_preset", "")
        lines = ["📁 **预设列表**"]
        for p in presets:
            marker = " ✅" if p.name == active else ""
            lines.append(f"  - {p.name}{marker}")
        return "\n".join(lines)

    async def _preset_show(self) -> str:
        name = self._state.get("active_preset", "default")
        preset = self.preset_store.load(name)
        if not preset:
            return "当前没有选中预设"
        return (
            f"📝 **当前预设: {name}**\n\n"
            f"**主提示词 (content):**\n{preset.content}\n\n"
            f"**历史后指令 (post_history):**\n{preset.post_history or '(空)'}"
        )

    # ── 指令：提示词查看与控制 ────────────────────────────

    @filter.command("st_prompt")
    async def cmd_st_prompt(self, event: AstrMessageEvent):
        """提示词控制: /st_prompt <show|toggle> [section]"""
        args = event.message_str.replace("/st_prompt", "").strip().split(maxsplit=1)
        sub = args[0].lower() if args else ""

        if sub == "show":
            yield event.plain_result(await self._prompt_show(event))
        elif sub == "toggle":
            section = args[1] if len(args) > 1 else ""
            yield event.plain_result(await self._prompt_toggle(event, section))
        else:
            available = ", ".join(TOGGLEABLE_SECTIONS)
            yield event.plain_result(
                "📋 **提示词控制**\n"
                "/st_prompt show — 查看当前完整 system_prompt\n"
                f"/st_prompt toggle <section> — 切换某个 section 的启用/禁用\n\n"
                f"可切换的 section: {available}"
            )

    async def _prompt_show(self, event: AstrMessageEvent) -> str:
        umo = event.unified_msg_origin
        character = self._get_active_character(umo)
        preset = self._get_active_preset()

        if not preset:
            return "当前没有选中预设。使用 /st_preset load <名称>"

        assembled = self.assembler.build_system_prompt(
            character=character,
            preset=preset,
            user_name=self._state.get("user_name", "User"),
            persona_description=self._state.get("persona_description", ""),
            wrap_labels=self.config.get("wrap_sections_with_labels", True),
            include_examples=self.config.get("include_dialogue_examples", True),
            enabled_sections=self._state.get("enabled_sections", {}),
        )

        # 截断显示
        if len(assembled) > 1500:
            assembled = assembled[:1500] + "\n\n... (已截断，完整长度: " + str(len(assembled)) + " 字符)"

        status_lines = [
            f"角色卡: {character.name if character else '无'}",
            f"预设: {self._state.get('active_preset', 'default')}",
            f"用户名: {self._state.get('user_name', 'User')}",
        ]

        # 显示 section 状态
        enabled = self._state.get("enabled_sections", {})
        section_status = []
        for section_id, _, _ in SECTION_ORDER:
            e = enabled.get(section_id, True)
            icon = "✅" if e else "❌"
            section_status.append(f"  {icon} {section_id}")
        status_lines.append("Sections:\n" + "\n".join(section_status))

        return (
            "📋 **当前 System Prompt**\n\n"
            + "\n".join(status_lines)
            + "\n\n────────── 组装结果 ──────────\n"
            + assembled
        )

    async def _prompt_toggle(self, event: AstrMessageEvent, section: str) -> str:
        if not section:
            available = ", ".join(TOGGLEABLE_SECTIONS)
            return f"用法: /st_prompt toggle <section>\n可选: {available}"

        if section not in TOGGLEABLE_SECTIONS:
            return f"无效的 section: {section}\n可选: {', '.join(TOGGLEABLE_SECTIONS)}"

        enabled = self._state.setdefault("enabled_sections", {})
        current = enabled.get(section, True)
        enabled[section] = not current
        self._save_state()
        status = "✅ 启用" if not current else "❌ 禁用"
        return f"Section **{section}** {status}"

    # ── 指令：用户人称管理 ───────────────────────────────

    @filter.command("st_persona")
    async def cmd_st_persona(self, event: AstrMessageEvent):
        """用户人称管理: /st_persona <set|show> [文本]"""
        args = event.message_str.replace("/st_persona", "").strip().split(maxsplit=1)
        sub = args[0].lower() if args else ""

        if sub == "set":
            text = args[1] if len(args) > 1 else ""
            yield event.plain_result(await self._persona_set(text))
        elif sub == "show":
            yield event.plain_result(await self._persona_show())
        else:
            yield event.plain_result(
                "📋 **用户人称管理**\n"
                "/st_persona set <文本> — 设置用户 persona 描述\n"
                "/st_persona show — 查看当前 persona"
            )

    async def _persona_set(self, text: str) -> str:
        if not text:
            return "用法: /st_persona set <描述文本>\n例如: /st_persona set 我是一个来自异世界的冒险者"
        self._state["persona_description"] = text
        self._save_state()
        return f"✅ 已设置 persona: {text[:100]}{'...' if len(text) > 100 else ''}"

    async def _persona_show(self) -> str:
        text = self._state.get("persona_description", "")
        if not text:
            return "当前没有设置 persona。使用 /st_persona set <文本> 设置\n\n也可以在 WebUI 配置中设置默认 persona"
        return f"📝 **当前 Persona**\n{text}"

    # ── 指令：用户名管理 ─────────────────────────────────

    @filter.command("st_user")
    async def cmd_st_user(self, event: AstrMessageEvent):
        """用户名管理: /st_user set <名字>"""
        args = event.message_str.replace("/st_user", "").strip().split(maxsplit=1)
        sub = args[0].lower() if args else ""

        if sub == "set":
            name = args[1] if len(args) > 1 else ""
            if not name:
                yield event.plain_result("用法: /st_user set <名字>")
                return
            self._state["user_name"] = name
            self._save_state()
            yield event.plain_result(f"✅ 用户名已设置为: **{name}**")
        else:
            current = self._state.get("user_name", "User")
            yield event.plain_result(
                f"📋 **用户名管理**\n当前用户名: **{current}**\n\n"
                "/st_user set <名字> — 设置用户名 (替换 {{user}} 宏)"
            )
