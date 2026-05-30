"""SillyTavern Anti-OOC Prompt Wrapper for AstrBot.

防 OOC (移植自 SillyTavern):
  Main / Persona / enhanceDefs / Depth Inject / Jailbreak
响应控制:
  结构化 JSON 输出 → 插件提取 messages 数组 → 逐条发送

Author: laoin
Version: 0.5.2
"""

import json
import re

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig
from astrbot.api.provider import ProviderRequest
from astrbot.core.agent.message import TextPart

PLUGIN_NAME = "sillytavern_prompt"

# ── 防 OOC 常量 ──────────────────────────────────────

ST_MAIN = (
    "Write {char}'s next reply in a fictional chat between {char} and {user}. "
    "Stay in character at all times. Describe actions, expressions, and dialogue "
    "naturally. Never break the fourth wall or acknowledge that you are an AI. "
    "Never speak for {user} or describe {user}'s actions."
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

# ── JSON 格式指令 ────────────────────────────────────

JSON_FMT = (
    "\n\n[OUTPUT FORMAT - MUST FOLLOW]\n"
    "Your entire response must be a single JSON object and nothing else.\n"
    'Format: {{"messages":[{{"content":"your reply text here"}}]}}\n'
    'Example response: {{"messages":[{{"content":"Hey! What\'s up?"}}]}}\n'
    "- messages: 1-{max_messages} items, each content ≤{max_chars} chars\n"
    "- plain text only inside content, no markdown"
)

MD_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__|`(.+?)`|~~(.+?)~~|\*(.+?)\*|_(.+?)_")


@register(PLUGIN_NAME, "laoin", "ST 防 OOC + JSON 结构化输出", "v0.5.2")
class SillyTavernAntiOOC(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config or {}
        self._failures: dict[str, int] = {}
        self._last_bad: dict[str, str] = {}
        self._request_count = 0
        self._response_count = 0
        logger.warning(
            "[ST-AntiOOC] v0.5.2 loaded debug=%s json=%s depths=%s config_keys=%s",
            self._debug, self._json_enabled, DEPTHS,
            list(self.config.keys())[:10] if self.config else [],
        )

    # ── 配置 ──────────────────────────────────────────

    @property
    def _debug(self) -> bool:
        return self.config.get("debug") is True

    @property
    def _json_enabled(self) -> bool:
        v = self.config.get("enable_json_format")
        # 默认开启 (key 不存在 / None / True / 非False字符串 → 开启)
        if v is None:
            return True
        if v is False:
            return False
        if isinstance(v, str) and v.lower() in ("false", "0", "no", "off"):
            return False
        return True

    # ── 人格解析 ──────────────────────────────────────

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

    # ── 深度注入 ──────────────────────────────────────

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

    # ── JSON 解析 ────────────────────────────────────

    @staticmethod
    def _strip_markdown(text: str) -> str:
        return MD_RE.sub(r"\1\2\3\4\5\6", text)

    def _parse_json_response(self, text: str, max_msg: int, max_chars: int) -> list[str] | None:
        """提取 JSON 中的 messages 数组。失败返回 None。"""
        if not text or not text.strip():
            return None
        text = text.strip()

        # 去 ``` 包裹
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)

        # 找 JSON 边界
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or start > end:
            return None
        text = text[start:end + 1]

        try:
            data = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return None

        if not isinstance(data, dict) or "messages" not in data:
            return None
        messages = data.get("messages")
        if not isinstance(messages, list) or not messages:
            return None

        result = []
        for msg in messages[:max_msg]:
            if not isinstance(msg, dict):
                continue
            content = str(msg.get("content", "")).strip()
            if not content:
                continue
            content = self._strip_markdown(content)
            result.append(content[:max_chars])
        return result or None

    # ── on_llm_request ────────────────────────────────

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        native = req.system_prompt or ""
        if not native.strip():
            return

        char_name = self._resolve_persona_name(req)
        user_name = event.get_sender_name() or "User"
        sid = req.session_id or ""

        max_msg = int(self.config.get("max_messages", 3))
        max_chars = int(self.config.get("max_chars_per_message", 200))

        # JSON 格式指令
        fmt = ""
        if self._json_enabled:
            fail_count = self._failures.get(sid, 0)
            fmt = JSON_FMT.format(max_messages=max_msg, max_chars=max_chars)
            if fail_count > 0:
                fmt += f"\n(Previous response was invalid JSON. Please fix this time.)"

        main = ST_MAIN.format(char=char_name, user=user_name)
        enhance = ST_ENHANCE.format(char=char_name, user=user_name)
        jailbreak = ST_JAILBREAK.format(char=char_name, user=user_name)
        reminder = ST_REMINDER.format(char=char_name, user=user_name)

        req.system_prompt = (
            f"{main}\n\n"
            f"[Character Identity & Capabilities]\n{native}\n\n"
            f"{enhance}"
            f"{fmt}"
        )

        if self._debug:
            logger.warning(
                "[ST-AntiOOC] req#%d char=%s json=%s fail=%d contexts=%d prompt=%d",
                self._request_count, char_name, self._json_enabled,
                self._failures.get(sid, 0),
                len(req.contexts or []), len(req.system_prompt),
            )
            self._request_count += 1

        if req.contexts is None:
            req.contexts = []
        mid_depths = [d for d in DEPTHS if d > 0]
        if mid_depths:
            self._inject_at_depths(req.contexts, reminder, mid_depths)
        req.contexts.append({"role": "system", "content": jailbreak, "_no_save": True})

    # ── on_llm_response ───────────────────────────────

    @filter.on_llm_response()
    async def on_llm_response(self, event: AstrMessageEvent, resp):
        """捕获 LLM 原始响应用于调试。"""
        if self._debug and resp:
            # LLMResponse 的文本在 _completion_text，不是 .content
            text = getattr(resp, "_completion_text", "") or ""
            text = str(text) if text else ""
            role = getattr(resp, "role", "") or ""
            tool_calls = getattr(resp, "tools_call_name", []) or []
            logger.warning(
                "[ST-AntiOOC] LLM响应#%d role=%s text(%d): %s tools=%s",
                self._response_count, role, len(text), text[:500], tool_calls,
            )
            self._response_count += 1

    # ── on_decorating_result ──────────────────────────

    @filter.on_decorating_result()
    async def on_decorating_result(self, event: AstrMessageEvent):
        """解析 AI JSON 响应，提取 messages 逐条发送。"""
        result = event.get_result()
        if not result:
            return

        # 提取纯文本
        text = ""
        try:
            for comp in result.chain:
                if hasattr(comp, "text"):
                    text += comp.text
        except Exception:
            pass

        if self._debug and text.strip():
            logger.warning(
                "[ST-AntiOOC] result#%d raw(%d): %s",
                self._response_count - 1, len(text), text[:500],
            )

        if not self._json_enabled:
            return
        if not text.strip():
            return

        sid = event.session_id or ""
        max_msg = int(self.config.get("max_messages", 3))
        max_chars = int(self.config.get("max_chars_per_message", 200))

        messages = self._parse_json_response(text.strip(), max_msg, max_chars)

        if messages:
            self._failures[sid] = 0
            event.set_result("\n\n".join(messages))
            logger.info("[ST-AntiOOC] JSON 解析成功: %d 条消息", len(messages))
        else:
            self._failures[sid] = self._failures.get(sid, 0) + 1
            self._last_bad[sid] = text.strip()[:500]
            # 清理 JSON 残骸，防止 {"messages":} 进入对话历史造成死循环
            cleaned = re.sub(r'[{}"]', '', text.strip())
            cleaned = re.sub(r'\bmessages\b', '', cleaned, flags=re.IGNORECASE)
            cleaned = cleaned.strip().strip(':').strip()
            if cleaned:
                event.set_result(cleaned)
            logger.warning(
                "[ST-AntiOOC] JSON 解析失败#%d, 原始=%s, 清理后=%s",
                self._failures[sid], text.strip()[:100], cleaned[:100],
            )
