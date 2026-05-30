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
  JSON Format   — 社交媒体场景强制 JSON 输出，控制条数和长度，禁 markdown

Author: laoin
Version: 0.5.0
"""

import json
import re

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig
from astrbot.api.provider import ProviderRequest
from astrbot.core.agent.message import TextPart


PLUGIN_NAME = "sillytavern_prompt"

# ── SillyTavern 防 OOC 常量 ────────────────────────────

ST_MAIN = (
    "Write {char}'s next reply in a fictional chat between {char} and {user}. "
    "Stay in character at all times. Write in a narrative style, describing actions, "
    "expressions, and dialogue naturally. Never break the fourth wall or acknowledge "
    "that you are an AI. Never speak for {user} or describe {user}'s actions."
)

ST_ENHANCE = (
    "If you have more knowledge of {char}, add to the character's lore "
    "and personality to enhance them but keep the defined persona absolute."
)

ST_JAILBREAK = (
    "[SYSTEM REMINDER: You are {char}. Maintain strict adherence to your defined "
    "personality, speech patterns, and knowledge boundaries. Never control {user}'s "
    "actions or words. Respond only as {char} would.]"
)

ST_REMINDER = (
    "[Note: You are {char}, not an AI assistant. Stay in character.]"
)

DEPTHS = [0, 3, 6]

# ── JSON 响应格式常量 ──────────────────────────────────

JSON_FORMAT_INSTRUCTION = (
    "\n\n[Response Format]\n"
    "Reply ONLY with a JSON object, no other text before or after.\n"
    'Format: {{"messages":[{{"content":"text1"}},{{"content":"text2"}}]}}\n'
    "- max {max_messages} messages, each ≤{max_chars} chars\n"
    "- Plain text only: no **bold**, no `code`, no markdown, no formatting\n"
    "- Use emoji or words for emotion, never markdown syntax\n"
    "- Do NOT wrap the JSON in ``` fences"
)

JSON_FORMAT_CRITICAL = (
    "\n\n[CRITICAL RESPONSE FORMAT - YOU FAILED THIS LAST TIME]\n"
    "You MUST respond with ONLY valid JSON. NO other text.\n"
    'MUST be exactly: {{"messages":[{{"content":"your reply"}}]}}\n'
    "- max {max_messages} messages, each ≤{max_chars} chars\n"
    "- NO markdown. NO ```json```. NO text before/after JSON.\n"
    "- If you break format again, the response will be rejected."
)

JSON_FORMAT_FINAL = (
    "\n\n[FINAL WARNING - YOU FAILED FORMAT TWICE]\n"
    "LAST CHANCE: ONLY output the JSON. Nothing else. No explanations.\n"
    'Example of what you MUST output: {{"messages":[{{"content":"hello"}}]}}\n'
    "Previous bad response (DO NOT DO THIS):\n{snippet}"
)

# Markdown 清理正则
MD_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__|`(.+?)`|~~(.+?)~~|\*(.+?)\*|_(.+?)_")


@register(PLUGIN_NAME, "laoin", "ST 防 OOC + JSON格式控制 + 多层深度注入", "v0.5.0")
class SillyTavernAntiOOC(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config or {}
        # 格式失败计数器: session_id → 连续失败次数
        self._failures: dict[str, int] = {}
        # 上次原始响应: session_id → 上次失败的响应摘要
        self._last_bad: dict[str, str] = {}
        logger.info("[ST-AntiOOC] v0.5.0 loaded (depth injection @ %s, JSON format enforcement)", DEPTHS)

    # ── 人格解析 ────────────────────────────────────────

    def _resolve_persona_name(self, req: ProviderRequest) -> str:
        mgr = self.context.persona_manager
        try:
            conv = req.conversation
            if conv and getattr(conv, "persona_id", None):
                p = mgr.get_persona_v3_by_id(conv.persona_id)
                if p and isinstance(p, dict) and p.get("name"):
                    return p["name"]
        except Exception:
            pass
        try:
            p = mgr.get_default_persona_v3()
            if p and isinstance(p, dict) and p.get("name"):
                return p["name"]
        except Exception:
            pass
        return "Assistant"

    # ── 深度注入 ────────────────────────────────────────

    @staticmethod
    def _is_safe_position(contexts: list, pos: int) -> bool:
        for i in range(max(0, pos - 1), min(len(contexts), pos + 2)):
            msg = contexts[i]
            if msg.get("role") == "tool" or msg.get("tool_calls"):
                return False
        return True

    def _inject_at_depths(self, contexts: list, content: str, depths: list[int]) -> None:
        if not contexts:
            return
        contexts.reverse()
        total_inserted = 0
        for depth in sorted(depths):
            pos = depth + total_inserted
            if pos >= len(contexts):
                break
            while pos < len(contexts) and not self._is_safe_position(contexts, pos):
                pos += 1
            if pos >= len(contexts):
                break
            contexts.insert(pos, {"role": "system", "content": content, "_no_save": True})
            total_inserted += 1
        contexts.reverse()

    # ── JSON 解析 ──────────────────────────────────────

    @staticmethod
    def _strip_markdown(text: str) -> str:
        """移除 markdown 格式标记。"""
        return MD_RE.sub(r"\1\2\3\4\5\6", text)

    def _parse_response(self, text: str, max_msg: int, max_chars: int) -> list[str] | None:
        """从 LLM 响应中提取 messages 数组。

        Returns:
            解析成功返回 content 字符串列表，失败返回 None
        """
        if not text or not text.strip():
            return None

        text = text.strip()

        # 1. 去掉 ```json ... ``` 包裹
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)

        # 2. 找到 JSON 边界 (第一个 { 到最后一个 })
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or start > end:
            return None
        text = text[start : end + 1]

        # 3. 解析 JSON
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None

        if not isinstance(data, dict) or "messages" not in data:
            return None

        messages = data["messages"]
        if not isinstance(messages, list) or len(messages) == 0:
            return None

        # 4. 提取并清理每条消息
        result = []
        for msg in messages[:max_msg]:
            if not isinstance(msg, dict):
                continue
            content = str(msg.get("content", "")).strip()
            if not content:
                continue
            content = self._strip_markdown(content)
            if len(content) > max_chars:
                content = content[:max_chars]
            result.append(content)

        return result if result else None

    # ── LLM 请求钩子 ────────────────────────────────────

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        native = req.system_prompt or ""
        if not native.strip():
            return

        char_name = self._resolve_persona_name(req)
        user_name = event.get_sender_name() or "User"
        sid = req.session_id or ""

        # 配置
        max_msg = int(self.config.get("max_messages", 3))
        max_chars = int(self.config.get("max_chars_per_message", 200))

        # 渐进式格式强化
        fail_count = self._failures.get(sid, 0)
        if fail_count == 0:
            fmt = JSON_FORMAT_INSTRUCTION.format(max_messages=max_msg, max_chars=max_chars)
        elif fail_count == 1:
            fmt = JSON_FORMAT_CRITICAL.format(max_messages=max_msg, max_chars=max_chars)
        else:
            snippet = self._last_bad.get(sid, "")[:300]
            fmt = JSON_FORMAT_FINAL.format(max_messages=max_msg, max_chars=max_chars, snippet=snippet)

        main = ST_MAIN.format(char=char_name, user=user_name)
        enhance = ST_ENHANCE.format(char=char_name, user=user_name)
        jailbreak = ST_JAILBREAK.format(char=char_name, user=user_name)
        reminder = ST_REMINDER.format(char=char_name, user=user_name)

        # system_prompt
        req.system_prompt = (
            f"{main}\n\n"
            f"[Character Identity & Capabilities]\n{native}\n\n"
            f"{enhance}"
            f"{fmt}"
        )

        # user 级别格式提醒 (LLM 对用户消息遵循度更高)
        if req.extra_user_content_parts is None:
            req.extra_user_content_parts = []
        req.extra_user_content_parts.append(
            TextPart(text=f"[Remember: respond with ONLY the JSON object. No markdown, no other text.]")
        )

        # 深度注入
        if req.contexts is None:
            req.contexts = []
        mid_depths = [d for d in DEPTHS if d > 0]
        if mid_depths:
            self._inject_at_depths(req.contexts, reminder, mid_depths)
        req.contexts.append({"role": "system", "content": jailbreak, "_no_save": True})

    # ── 响应装饰钩子 ────────────────────────────────────

    @filter.on_decorating_result()
    async def on_decorating_result(self, event: AstrMessageEvent):
        """解析 LLM 响应中的 JSON，提取 messages 数组。"""
        result = event.get_result()
        if not result:
            return

        # 从 result.chain 中提取纯文本
        text = ""
        try:
            for comp in result.chain:
                if hasattr(comp, "text"):
                    text += comp.text
        except Exception:
            pass
        if not text.strip():
            return

        sid = event.session_id or ""
        max_msg = int(self.config.get("max_messages", 3))
        max_chars = int(self.config.get("max_chars_per_message", 200))

        messages = self._parse_response(text.strip(), max_msg, max_chars)

        if messages:
            self._failures[sid] = 0
            event.set_result("\n".join(messages))
        else:
            self._failures[sid] = self._failures.get(sid, 0) + 1
            self._last_bad[sid] = text.strip()[:1000]
            logger.info("[ST-AntiOOC] JSON 解析失败 (会话=%s, 连续%d次)", sid, self._failures[sid])
