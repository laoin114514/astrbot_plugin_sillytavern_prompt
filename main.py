"""SillyTavern Anti-OOC Prompt Wrapper for AstrBot.

零配置插件。读取 AstrBot 当前会话选中的人格，用 SillyTavern 的
分层防 OOC 结构重新包装 system_prompt，防止人设崩坏。

移植自 SillyTavern 的防 OOC 机制:
  Main          — 系统指令最前面，设定对话框架
  Persona       — AstrBot 原生人格 prompt + skills + MCP (完整保留)
  enhanceDefs   — 告诉 AI 可用训练知识补充人设，但以显式定义为准
  Jailbreak     — 聊天历史末尾的独立系统消息，作为生成前最后锚定
  Depth Inject  — 在对话中多个深度注入简版 jailbreak，长对话不丢人设
  begin_dialogs — AstrBot 已注入到 contexts 前面，具有 few-shot 效果

Author: laoin
Version: 0.4.0
"""

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig
from astrbot.api.provider import ProviderRequest


PLUGIN_NAME = "sillytavern_prompt"

# SillyTavern 的默认 Main Prompt (openai.js:101)
ST_MAIN = (
    "Write {char}'s next reply in a fictional chat between {char} and {user}. "
    "Stay in character at all times. Write in a narrative style, describing actions, "
    "expressions, and dialogue naturally. Never break the fourth wall or acknowledge "
    "that you are an AI. Never speak for {user} or describe {user}'s actions."
)

# SillyTavern 的 enhanceDefinitions (PromptManager.js:2052)
ST_ENHANCE = (
    "If you have more knowledge of {char}, add to the character's lore "
    "and personality to enhance them but keep the defined persona absolute."
)

# SillyTavern 的 Jailbreak / Post-History Instructions
# 完整版，注入到 contexts 末尾 (depth=0)
ST_JAILBREAK = (
    "[SYSTEM REMINDER: You are {char}. Maintain strict adherence to your defined "
    "personality, speech patterns, and knowledge boundaries. Never control {user}'s "
    "actions or words. Respond only as {char} would.]"
)

# 简版提醒，注入到深度位置 (depth > 0)
ST_REMINDER = (
    "[Note: You are {char}, not an AI assistant. Stay in character.]"
)

# 深度注入位置：depth=0 末尾，depth=3 倒数第3条前，depth=6 倒数第6条前
# 移植自 ST 的 Author's Note 默认 depth=4 + populationInjectionPrompts 算法
DEPTHS = [0, 3, 6]


@register(PLUGIN_NAME, "laoin", "ST 防 OOC 包装器：多层深度注入 + 人格保护", "v0.4.0")
class SillyTavernAntiOOC(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        logger.info("[ST-AntiOOC] 已加载 (depth injection @ %s)", DEPTHS)

    # ── 人格解析 ────────────────────────────────────────

    def _resolve_persona_name(self, req: ProviderRequest) -> str:
        """解析当前请求实际使用的人格名。

        优先级: 会话 persona → 默认 persona → 回退值
        """
        mgr = self.context.persona_manager
        try:
            conv = req.conversation
            if conv and getattr(conv, "persona_id", None):
                persona = mgr.get_persona_v3_by_id(conv.persona_id)
                if persona and isinstance(persona, dict) and persona.get("name"):
                    return persona["name"]
        except Exception:
            pass
        try:
            persona = mgr.get_default_persona_v3()
            if persona and isinstance(persona, dict) and persona.get("name"):
                return persona["name"]
        except Exception:
            pass
        return "Assistant"

    # ── 深度注入 ────────────────────────────────────────

    def _inject_at_depths(self, contexts: list, content: str, depths: list[int]) -> None:
        """在对话历史的多个深度位置注入系统消息。

        移植自 ST 的 populationInjectionPrompts (openai.js:801-866):
          1. 反转数组 (最新消息在前)
          2. 按深度插入 (depth=0 最近, depth=N 倒数第N条前)
          3. 反转回原始顺序

        totalInserted 跟踪已插入数量，确保后续插入位置正确。
        """
        if not contexts:
            return

        # ST 算法: 反转 → 按深度插入 → 反转回
        contexts.reverse()
        total_inserted = 0

        for depth in sorted(depths):
            pos = depth + total_inserted
            if pos >= len(contexts):
                break
            contexts.insert(pos, {
                "role": "system",
                "content": content,
                "_no_save": True,
            })
            total_inserted += 1

        contexts.reverse()

    # ── LLM 钩子 ────────────────────────────────────────

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        """用 ST 防 OOC 结构重组 system_prompt 和 contexts。

        最终消息结构:
          system: [Main]
          system: [Persona + Skills + Tools]  ← AstrBot 原生
          system: [enhanceDefs]
          user/assistant: [begin_dialogs]     ← AstrBot 注入 (few-shot)
          user/assistant: [chat history]
          system: [Reminder @ depth=6]
          user/assistant: [more chat]
          system: [Reminder @ depth=3]
          user/assistant: [more chat]
          system: [Jailbreak @ depth=0]       ← 生成前最后锚定
        """
        native = req.system_prompt or ""
        if not native.strip():
            return

        char_name = self._resolve_persona_name(req)
        user_name = event.get_sender_name() or "User"

        main = ST_MAIN.format(char=char_name, user=user_name)
        enhance = ST_ENHANCE.format(char=char_name, user=user_name)
        jailbreak = ST_JAILBREAK.format(char=char_name, user=user_name)
        reminder = ST_REMINDER.format(char=char_name, user=user_name)

        # 1. system_prompt: Main + AstrBot 原生 + enhanceDefs
        req.system_prompt = (
            f"{main}\n\n"
            f"[Character Identity & Capabilities]\n{native}\n\n"
            f"{enhance}"
        )

        # 2. 深度注入: 在 contexts 中多个位置插入提醒
        if req.contexts is None:
            req.contexts = []

        # depth>0 用简版提醒，depth=0 用完整 jailbreak
        mid_depths = [d for d in DEPTHS if d > 0]
        if mid_depths:
            self._inject_at_depths(req.contexts, reminder, mid_depths)

        # depth=0 末尾用完整版
        req.contexts.append({
            "role": "system",
            "content": jailbreak,
            "_no_save": True,
        })
