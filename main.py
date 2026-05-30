"""SillyTavern Anti-OOC Prompt Wrapper for AstrBot.

零配置插件。读取 AstrBot 当前选中的人格，用 SillyTavern 的
分层防 OOC 结构重新包装 system_prompt，防止人设崩坏。

工作原理:
  1. AstrBot 原生管线已组装好 persona.prompt + skills + tools → req.system_prompt
  2. on_llm_request hook 中将原生内容嵌入 ST 风格的三段式防 OOC 壳:
     - Main: 对话框架 + 防 OOC 基础指令
     - Persona: AstrBot 原生内容 (人格 + 技能 + MCP)
     - Jailbreak: 最终锚定提醒

Author: laoin
Version: 0.3.0
"""

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig
from astrbot.api.provider import ProviderRequest


PLUGIN_NAME = "sillytavern_prompt"

# SillyTavern 风格的默认 Main Prompt
ST_MAIN_PROMPT = (
    "Write {char}'s next reply in this fictional roleplay chat between {char} and {user}. "
    "Stay in character at all times. Write in a narrative style, describing actions, "
    "expressions, and dialogue naturally. Never break the fourth wall or acknowledge "
    "that you are an AI. Never speak for {user} or describe {user}'s actions."
)

# SillyTavern 风格的 Jailbreak / Post-History Instructions
ST_JAILBREAK = (
    "[SYSTEM REMINDER: You are {char}. Maintain strict adherence to your defined "
    "personality, speech patterns, and knowledge boundaries. Never control {user}'s "
    "actions or words. Respond only as {char} would.]"
)


@register(PLUGIN_NAME, "laoin", "SillyTavern 防 OOC 包装器：读取 AstrBot 人格并用 ST 分层结构防止人设崩坏", "v0.3.0")
class SillyTavernAntiOOC(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        logger.info("[ST-AntiOOC] 插件已加载，等待包装 AstrBot 原生人格")

    # ── 辅助 ────────────────────────────────────────────

    def _get_persona_name(self) -> str:
        """从 AstrBot 当前默认人格读取角色名。"""
        try:
            mgr = self.context.persona_manager
            persona = mgr.get_default_persona_v3()
            if persona and isinstance(persona, dict):
                return persona.get("name") or "Assistant"
        except Exception:
            pass
        return "Assistant"

    # ── LLM 钩子 ────────────────────────────────────────

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        """用 ST 防 OOC 壳包装 AstrBot 原生 system_prompt。"""
        native = req.system_prompt or ""
        if not native.strip():
            return

        char_name = self._get_persona_name()
        user_name = event.get_sender_name() or "User"

        main = ST_MAIN_PROMPT.format(char=char_name, user=user_name)
        jailbreak = ST_JAILBREAK.format(char=char_name, user=user_name)

        assembled = f"""{main}

[Character Identity & Capabilities]
{native}

{jailbreak}"""

        req.system_prompt = assembled
