"""角色卡管理：本地 JSON 文件 CRUD + AstrBot persona 同步。

每张角色卡是一个 JSON 文件，包含:
- prompt: 系统提示词
- skills: 技能列表 (null=全部, []=无, ["name"]=指定)
- tools: 工具列表 (null=全部, []=无, ["name"]=指定)
- description, personality, scenario: ST 风格分层人设
- first_mes, mes_example: 对话示例
- user_name, persona_description: 用户设定
"""

import json
import os
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class CharacterCard:
    """角色卡数据模型。"""
    name: str = ""
    prompt: str = ""
    skills: list[str] | None = None
    tools: list[str] | None = None
    description: str = ""
    personality: str = ""
    scenario: str = ""
    first_mes: str = ""
    mes_example: str = ""
    user_name: str = "User"
    persona_description: str = ""

    @property
    def is_loaded(self) -> bool:
        return bool(self.name)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "CharacterCard":
        return cls(
            name=data.get("name", ""),
            prompt=data.get("prompt", ""),
            skills=data.get("skills"),
            tools=data.get("tools"),
            description=data.get("description", ""),
            personality=data.get("personality", ""),
            scenario=data.get("scenario", ""),
            first_mes=data.get("first_mes", ""),
            mes_example=data.get("mes_example", ""),
            user_name=data.get("user_name", "User"),
            persona_description=data.get("persona_description", ""),
        )

    def __repr__(self):
        skills_info = "all" if self.skills is None else (str(len(self.skills)) if self.skills else "none")
        tools_info = "all" if self.tools is None else (str(len(self.tools)) if self.tools else "none")
        return f"CharacterCard(name={self.name!r}, skills={skills_info}, tools={tools_info})"


DEFAULT_CARD = CharacterCard(
    name="default",
    prompt="Continue the roleplay conversation naturally. Write {{char}}'s next reply.",
)


class CardStore:
    """管理 data/cards/ 目录下的角色卡 JSON 文件。"""

    def __init__(self, directory: str):
        self.directory = directory
        os.makedirs(directory, exist_ok=True)
        self._ensure_default()

    def _ensure_default(self) -> None:
        path = os.path.join(self.directory, "default.json")
        if not os.path.isfile(path):
            self.save(DEFAULT_CARD)

    def _path(self, name: str) -> str:
        safe = name.replace("/", "_").replace("\\", "_")
        return os.path.join(self.directory, f"{safe}.json")

    # ── 本地 CRUD ───────────────────────────────────────

    def list_all(self) -> list[CharacterCard]:
        cards = []
        if not os.path.isdir(self.directory):
            return cards
        for fname in sorted(os.listdir(self.directory)):
            if fname.endswith(".json"):
                card = self.load(fname[:-5])
                if card:
                    cards.append(card)
        return cards

    def list_names(self) -> list[str]:
        return [c.name for c in self.list_all()]

    def load(self, name: str) -> Optional[CharacterCard]:
        path = self._path(name)
        if not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return CharacterCard.from_dict(json.load(f))
        except (json.JSONDecodeError, IOError) as e:
            print(f"[ST-Prompt] 加载角色卡失败 {name}: {e}")
            return None

    def save(self, card: CharacterCard) -> bool:
        if not card.name:
            return False
        try:
            with open(self._path(card.name), "w", encoding="utf-8") as f:
                json.dump(card.to_dict(), f, ensure_ascii=False, indent=2)
            return True
        except IOError as e:
            print(f"[ST-Prompt] 保存角色卡失败 {card.name}: {e}")
            return False

    def delete(self, name: str) -> bool:
        path = self._path(name)
        if not os.path.isfile(path):
            return False
        try:
            os.remove(path)
            return True
        except IOError as e:
            print(f"[ST-Prompt] 删除角色卡失败 {name}: {e}")
            return False

    def exists(self, name: str) -> bool:
        return os.path.isfile(self._path(name))

    # ── AstrBot Persona 同步 ────────────────────────────

    async def sync_to_astrbot(
        self,
        card: CharacterCard,
        persona_manager,
        conversation_manager,
        umo: str,
    ) -> str:
        """将角色卡同步到 AstrBot persona 系统并切换会话。

        1. 检查同名 persona 是否存在 → 存在则更新，不存在则创建
        2. 更新当前会话的 persona_id
        """
        persona_id = f"st_{card.name}"

        try:
            existing = persona_manager.get_persona_v3_by_id(persona_id)
        except Exception:
            existing = None

        try:
            if existing:
                await persona_manager.update_persona(
                    persona_id=persona_id,
                    system_prompt=card.prompt,
                    skills=card.skills,
                    tools=card.tools,
                )
            else:
                await persona_manager.create_persona(
                    persona_id=persona_id,
                    system_prompt=card.prompt,
                    skills=card.skills,
                    tools=card.tools,
                )
        except Exception as e:
            return f"❌ 同步 AstrBot persona 失败: {e}"

        # 切换会话
        try:
            await conversation_manager.update_conversation_persona_id(umo, persona_id)
        except Exception as e:
            return f"❌ 切换会话 persona 失败: {e}"

        return f"✅ 已加载角色卡 **{card.name}**\nskills: {card.skills}\ntools: {card.tools}"

    async def delete_from_astrbot(self, name: str, persona_manager) -> str:
        """从 AstrBot 中删除对应的 persona。"""
        persona_id = f"st_{name}"
        try:
            existing = persona_manager.get_persona_v3_by_id(persona_id)
            if existing:
                await persona_manager.delete_persona(persona_id)
                return f"已删除 AstrBot persona: {persona_id}"
            return ""
        except Exception as e:
            return f"⚠️ 删除 AstrBot persona 失败: {e}"
