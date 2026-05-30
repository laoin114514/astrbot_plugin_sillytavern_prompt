"""SillyTavern Anti-OOC Prompt Wrapper for AstrBot.

零配置插件。读取 AstrBot 当前会话选中的人格，用 SillyTavern 的
分层防 OOC 结构重新包装 system_prompt，防止人设崩坏。

防 OOC 机制 (移植自 SillyTavern):
  Main        — 系统指令最前面，设定对话框架
  Persona     — AstrBot 原生人格 prompt + skills + MCP (保留)
  enhanceDefs — 告诉 AI 可用训练知识补充人设，但以显式定义为准
  Jailbreak   — contexts 末尾的独立系统消息，作为生成前最后锚定
  begin_dialogs — AstrBot 已注入到 contexts 前面，具有 few-shot 效果

Author: laoin
Version: 0.3.1
"""

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig
from astrbot.api.provider import ProviderRequest


PLUGIN_NAME = "sillytavern_prompt"

# SillyTavern 的默认 Main Prompt (openai.js:101)
ST_MAIN_PROMPT = (
    "Write {char}'s next reply in a fictional chat between {char} and {user}. "
    "Stay in character at all times. Write in a narrative style, describing actions, "
    "expressions, and dialogue naturally. Never break the fourth wall or acknowledge "
    "that you are an AI. Never speak for {user} or describe {user}'s actions."
)

# SillyTavern 的 enhanceDefinitions (PromptManager.js:2052)
# 告诉 AI 可用自身知识扩充角色，但角色卡定义绝对优先
ST_ENHANCE_DEFS = (
    "If you have more knowledge of {char}, add to the character's lore "
    "and personality to enhance them but keep the defined persona absolute."
)

# SillyTavern 的 Jailbreak / Post-History Instructions
# 放在聊天历史之后，作为生成前最后一条指令，注意力权重最高
ST_JAILBREAK = (
    "[SYSTEM REMINDER: You are {char}. Maintain strict adherence to your defined "
    "personality, speech patterns, and knowledge boundaries. Never control {user}'s "
    "actions or words. Respond only as {char} would.]"
)


@register(PLUGIN_NAME, "laoin", "ST 防 OOC 包装器：读取 AstrBot 人格，用 ST 分层结构防止人设崩坏", "v0.3.1")
class SillyTavernAntiOOC(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        logger.info("[ST-AntiOOC] 已加载，将自动包装 AstrBot 原生人格")

    # ── 人格解析 ────────────────────────────────────────

    def _resolve_persona_name(self, req: ProviderRequest) -> str:
        """解析当前请求实际使用的人格名。

        优先级: 会话 persona → 默认 persona → 回退值
        """
        mgr = self.context.persona_manager

        # 1. 尝试从当前会话的 persona_id 解析
        try:
            conv = req.conversation
            if conv and getattr(conv, "persona_id", None):
                persona = mgr.get_persona_v3_by_id(conv.persona_id)
                if persona and isinstance(persona, dict):
                    name = persona.get("name")
                    if name:
                        return name
        except Exception:
            pass

        # 2. 回退到默认人格
        try:
            persona = mgr.get_default_persona_v3()
            if persona and isinstance(persona, dict):
                name = persona.get("name")
                if name:
                    return name
        except Exception:
            pass

        # 3. 最终回退
        return "Assistant"

    # ── LLM 钩子 ────────────────────────────────────────

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        """用 ST 防 OOC 结构重组 system_prompt 和 contexts。

        ST 的四层防 OOC:
          1. Main          → system_prompt 最前面
          2. enhanceDefs   → system_prompt 中，约束 AI 的知识使用
          3. Persona+Skills+Tools → system_prompt 中原生保留
          4. Jailbreak     → contexts 末尾 (post-history)
        """
        native = req.system_prompt or ""
        if not native.strip():
            return

        char_name = self._resolve_persona_name(req)
        user_name = event.get_sender_name() or "User"

        main = ST_MAIN_PROMPT.format(char=char_name, user=user_name)
        enhance = ST_ENHANCE_DEFS.format(char=char_name, user=user_name)
        jailbreak = ST_JAILBREAK.format(char=char_name, user=user_name)

        # 1. system_prompt: Main + enhanceDefs + AstrBot 原生内容
        req.system_prompt = f"{main}\n\n[Character Identity & Capabilities]\n{native}\n\n{enhance}"

        # 2. Jailbreak 注入到 contexts 末尾 (ST 的 post-history 定位)
        if req.contexts is None:
            req.contexts = []
        req.contexts.append({
            "role": "system",
            "content": jailbreak,
            "_no_save": True,
        })
