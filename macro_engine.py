"""宏替换引擎：将提示词中的 {{macro}} 替换为实际值。

移植自 SillyTavern public/script.js substituteParams().
"""

# 支持的宏列表（对应 ST 的 macros）
MACRO_MAP = {
    "user": "user_name",
    "char": "char_name",
    "description": "description",
    "personality": "personality",
    "scenario": "scenario",
    "system": "system_content",
    "jailbreak": "jailbreak_content",
    "persona": "persona_description",
    "first_mes": "first_mes",
    "mes_example": "mes_example",
}


class MacroEngine:
    """替换提示词文本中的 {{macro}} 占位符。

    使用方式:
        engine = MacroEngine()
        result = engine.substitute(
            text,
            user_name="小明",
            char_name="Alice",
            description="Alice is a cheerful girl.",
            ...
        )
    """

    def substitute(self, text: str, **kwargs) -> str:
        """替换文本中的所有已知宏。

        Args:
            text: 包含 {{macro}} 占位符的字符串
            **kwargs: 宏名到值的映射

        Returns:
            替换后的字符串
        """
        if not text:
            return text

        for macro, field in MACRO_MAP.items():
            value = kwargs.get(field, "")
            if value:
                text = text.replace(f"{{{{{macro}}}}}", str(value))

        return text
