"""核心提示词组装器。

移植自 SillyTavern 的 openai.js populateChatCompletion() 和 PromptManager.js.
按 ST 的顺序组装分层 system prompt，支持角色卡覆盖语义。
"""

from typing import Optional

from .macro_engine import MacroEngine
from .card_parser import CharacterCard
from .preset_store import PromptPreset


# SillyTavern 中 openai.js lines 1203-1279 的 prompt 组装顺序
# 每个 section 有: identifier, 是否来自角色卡, 默认模板
SECTION_ORDER = [
    ("worldInfoBefore", False, ""),        # 世界书（角色前）
    ("main", True, ""),                      # 主提示词（可被角色卡 system_prompt 覆盖）
    ("worldInfoAfter", False, ""),          # 世界书（角色后）
    ("charDescription", True, ""),          # 角色描述
    ("charPersonality", True, ""),          # 角色性格
    ("scenario", True, ""),                 # 场景
    ("personaDescription", False, ""),      # 用户人称描述
    ("nsfw", False, ""),                    # 辅助提示词
    ("jailbreak", True, ""),                # 历史后指令（可被角色卡 post_history_instructions 覆盖）
    ("enhanceDefinitions", False, ""),      # 增强定义
    ("dialogueExamples", True, ""),         # 对话示例
]

# SillyTavern 的默认 prompt 标签 (wrap_sections_with_labels)
SECTION_LABELS = {
    "charDescription": "[{}'s Description]",
    "charPersonality": "[{}'s Personality]",
    "scenario": "[Scenario]",
    "personaDescription": "[{}'s Persona]",
    "nsfw": "[Important Instructions]",
    "jailbreak": "[Post-History Instructions]",
    "dialogueExamples": "[Example Dialogue]",
    "enhanceDefinitions": "[Enhance Definitions]",
}


class PromptAssembler:
    """将角色卡 + 预设按 ST 顺序组装为最终 system_prompt。"""

    def __init__(self, macro_engine: Optional[MacroEngine] = None):
        self.macro = macro_engine or MacroEngine()

    def build_system_prompt(
        self,
        character: Optional[CharacterCard],
        preset: PromptPreset,
        user_name: str = "User",
        persona_description: str = "",
        wrap_labels: bool = True,
        include_examples: bool = True,
        enabled_sections: dict[str, bool] | None = None,
        world_info_before: str = "",
        world_info_after: str = "",
    ) -> str:
        """组装完整的 system prompt。

        Args:
            character: 当前活跃角色卡（可为 None）
            preset: 当前系统提示词预设
            user_name: 用户名，替换 {{user}}
            persona_description: 用户人称描述，替换 {{persona}}
            wrap_labels: 是否为每个 section 添加标签
            include_examples: 是否包含对话示例
            enabled_sections: section 启用状态，key 为 section 标识符
            world_info_before: 世界书内容（角色前）
            world_info_after: 世界书内容（角色后）

        Returns:
            组装好的完整 system_prompt 字符串
        """
        if enabled_sections is None:
            enabled_sections = {s[0]: True for s in SECTION_ORDER}

        char_name = character.name if character and character.name else "Assistant"
        parts = []

        # 准备宏替换参数
        macro_kwargs = {
            "user_name": user_name,
            "char_name": char_name,
            "persona_description": persona_description,
        }

        if character:
            macro_kwargs.update({
                "description": character.description,
                "personality": character.personality,
                "scenario": character.scenario,
                "first_mes": character.first_mes,
                "mes_example": character.mes_example,
            })

        for section_id, from_character, _ in SECTION_ORDER:
            if not enabled_sections.get(section_id, True):
                continue

            content = self._get_section_content(
                section_id,
                character,
                preset,
                user_name,
                char_name,
                persona_description,
                include_examples,
                world_info_before,
                world_info_after,
            )

            if not content or not content.strip():
                continue

            # 可选: 添加节标签
            if wrap_labels and section_id in SECTION_LABELS:
                label_template = SECTION_LABELS[section_id]
                label = label_template.format(char_name, user_name)
                content = f"{label}\n{content}"

            # 先对 content 做宏替换
            content = self.macro.substitute(content, **macro_kwargs)
            parts.append(content)

        # 对 system 和 jailbreak 的额外替换
        macro_kwargs["system_content"] = preset.content if preset else ""
        macro_kwargs["jailbreak_content"] = (
            character.get_effective_post_history(preset.post_history)
            if character
            else (preset.post_history if preset else "")
        )

        assembled = "\n\n".join(parts)
        assembled = self.macro.substitute(assembled, **macro_kwargs)

        return assembled

    def _get_section_content(
        self,
        section_id: str,
        character: Optional[CharacterCard],
        preset: PromptPreset,
        user_name: str,
        char_name: str,
        persona_description: str,
        include_examples: bool,
        world_info_before: str,
        world_info_after: str,
    ) -> str:
        """获取每个 section 的实际内容。

        关键覆盖规则（移植自 ST）:
        - main: 角色卡 system_prompt > 预设 content
        - jailbreak: 角色卡 post_history_instructions > 预设 post_history
        - charDescription/charPersonality/scenario: 来自角色卡
        - dialogueExamples: 来自角色卡 mes_example
        """
        if section_id == "worldInfoBefore":
            return world_info_before

        if section_id == "worldInfoAfter":
            return world_info_after

        if section_id == "main":
            # 角色卡 system_prompt 覆盖预设 content
            if character and character.system_prompt:
                return character.system_prompt
            return preset.content if preset else ""

        if section_id == "charDescription":
            return character.description if character else ""

        if section_id == "charPersonality":
            return character.personality if character else ""

        if section_id == "scenario":
            return character.scenario if character else ""

        if section_id == "personaDescription":
            return persona_description

        if section_id == "nsfw":
            # ST 中 nsfw 是一个辅助提示词，通常为空
            return ""

        if section_id == "jailbreak":
            # 角色卡 post_history_instructions 覆盖预设 post_history
            if character and character.post_history_instructions:
                return character.post_history_instructions
            return preset.post_history if preset else ""

        if section_id == "enhanceDefinitions":
            return (
                "If you have more knowledge of {{char}}, add to the character's lore "
                "and personality to enhance them but keep the core definitions absolute."
            )

        if section_id == "dialogueExamples":
            if not include_examples:
                return ""
            if character and character.mes_example:
                return character.mes_example
            return ""

        return ""
