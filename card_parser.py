"""SillyTavern 角色卡 PNG 解析器。

移植自 SillyTavern src/character-card-parser.js.
支持 V2 (chara chunk) 和 V3 (ccv3 chunk) 格式。
"""

import base64
import json
import os
import struct
from typing import Optional


class CharacterCard:
    """对应 SillyTavern chara_card_v2/v3 规范的数据模型。

    处理两种格式:
    - V2: spec="chara_card_v2", 数据在 .data 下
    - V3: spec="chara_card_v3", 数据结构同 V2
    """

    def __init__(
        self,
        name: str = "",
        description: str = "",
        personality: str = "",
        scenario: str = "",
        first_mes: str = "",
        mes_example: str = "",
        system_prompt: str = "",
        post_history_instructions: str = "",
        alternate_greetings: list[str] | None = None,
        creator: str = "",
        character_version: str = "",
        tags: list[str] | None = None,
        extensions: dict | None = None,
        spec: str = "chara_card_v2",
        spec_version: str = "2.0",
        source_file: str = "",
    ):
        self.spec = spec
        self.spec_version = spec_version
        self.name = name
        self.description = description
        self.personality = personality
        self.scenario = scenario
        self.first_mes = first_mes
        self.mes_example = mes_example
        self.system_prompt = system_prompt
        self.post_history_instructions = post_history_instructions
        self.alternate_greetings = alternate_greetings or []
        self.creator = creator
        self.character_version = character_version
        self.tags = tags or []
        self.extensions = extensions or {}
        self.source_file = source_file

    @property
    def is_loaded(self) -> bool:
        return bool(self.name)

    def get_effective_system_prompt(self, fallback: str) -> str:
        """角色卡 system_prompt 覆盖全局预设。

        对应 ST 行为: character.system_prompt 若不为空则替代 preset.content
        """
        return self.system_prompt if self.system_prompt else fallback

    def get_effective_post_history(self, fallback: str) -> str:
        """角色卡 post_history_instructions 覆盖 jailbreak。

        对应 ST 行为: character.post_history_instructions 替代 preset.post_history
        """
        return self.post_history_instructions if self.post_history_instructions else fallback

    def to_dict(self) -> dict:
        return {
            "spec": self.spec,
            "spec_version": self.spec_version,
            "name": self.name,
            "description": self.description,
            "personality": self.personality,
            "scenario": self.scenario,
            "first_mes": self.first_mes,
            "mes_example": self.mes_example,
            "system_prompt": self.system_prompt,
            "post_history_instructions": self.post_history_instructions,
            "alternate_greetings": self.alternate_greetings,
            "creator": self.creator,
            "character_version": self.character_version,
            "tags": self.tags,
            "extensions": self.extensions,
        }

    def __repr__(self):
        return f"CharacterCard(name={self.name!r}, spec={self.spec})"


class CardParser:
    """解析 SillyTavern 兼容的 PNG 角色卡或 JSON 文件。"""

    @classmethod
    def from_file(cls, filepath: str) -> CharacterCard:
        """自动检测格式并解析角色卡。"""
        ext = os.path.splitext(filepath)[1].lower()
        if ext == ".png":
            return cls.from_png(filepath)
        elif ext == ".json":
            return cls.from_json(filepath)
        else:
            raise ValueError(f"不支持的角色卡格式: {ext}")

    @classmethod
    def from_png(cls, filepath: str) -> CharacterCard:
        """从 PNG 文件解析角色卡。

        读取 PNG 的 tEXt chunk，提取 'ccv3' (优先) 或 'chara' 中的
        base64 编码的 JSON 数据。
        """
        with open(filepath, "rb") as f:
            data = f.read()

        text_chunks = cls._extract_text_chunks(data)
        if not text_chunks:
            raise ValueError("PNG 文件中没有 tEXt 块")

        # V3 (ccv3) 优先，回退到 V2 (chara)
        raw_json = None
        for keyword, text in text_chunks:
            if keyword == "ccv3":
                raw_json = base64.b64decode(text).decode("utf-8")
                break
            elif keyword == "chara" and raw_json is None:
                raw_json = base64.b64decode(text).decode("utf-8")

        if not raw_json:
            raise ValueError("PNG 中没有找到角色卡数据 (chara/ccv3)")

        return cls._parse_card_json(raw_json, filepath)

    @classmethod
    def from_json(cls, filepath: str) -> CharacterCard:
        """从独立 JSON 文件加载角色卡。"""
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls._parse_card_json(data, filepath)

    @classmethod
    def _parse_card_json(cls, data, filepath: str = "") -> CharacterCard:
        """将 JSON 数据解析为 CharacterCard。

        处理 V2 { spec, spec_version, data: {...} } 格式，
        V3 格式 (数据在顶层)，
        以及 V1 扁平格式。
        """
        if isinstance(data, str):
            data = json.loads(data)

        spec = data.get("spec", "")

        if spec == "chara_card_v2":
            # V2 格式: 字段在 .data 下
            inner = data.get("data", data)
            return cls._from_v2_data(inner, spec, data.get("spec_version", "2.0"), filepath)
        elif spec == "chara_card_v3":
            # V3 格式: 字段在顶层
            return cls._from_v2_data(data, spec, data.get("spec_version", "3.0"), filepath)
        else:
            # V1 或未标注格式: 扁平结构，可能有嵌套的 .data
            inner = data.get("data", data)
            if isinstance(inner, dict) and inner.get("name"):
                return cls._from_v2_data(inner, "chara_card_v2", "2.0", filepath)
            # 纯扁平
            return cls._from_v2_data(data, "chara_card_v1", "1.0", filepath)

    @classmethod
    def _from_v2_data(
        cls, data: dict, spec: str, spec_version: str, source_file: str
    ) -> CharacterCard:
        return CharacterCard(
            spec=spec,
            spec_version=spec_version,
            name=data.get("name", ""),
            description=data.get("description", ""),
            personality=data.get("personality", ""),
            scenario=data.get("scenario", ""),
            first_mes=data.get("first_mes", ""),
            mes_example=data.get("mes_example", ""),
            system_prompt=data.get("system_prompt", ""),
            post_history_instructions=data.get("post_history_instructions", ""),
            alternate_greetings=data.get("alternate_greetings", []),
            creator=data.get("creator", ""),
            character_version=data.get("character_version", ""),
            tags=data.get("tags", []),
            extensions=data.get("extensions", {}),
            source_file=source_file,
        )

    # ── PNG 二进制解析 ───────────────────────────────────

    @staticmethod
    def _extract_text_chunks(data: bytes) -> list[tuple[str, str]]:
        """从 PNG 原始字节中提取所有 tEXt 块。

        返回 [(keyword, text), ...] 列表。
        """
        # 验证 PNG 签名
        png_sig = b"\x89PNG\r\n\x1a\n"
        if data[:8] != png_sig:
            raise ValueError("不是有效的 PNG 文件")

        chunks = []
        offset = 8  # 跳过 PNG 签名

        while offset < len(data):
            if offset + 8 > len(data):
                break

            length = struct.unpack(">I", data[offset : offset + 4])[0]
            chunk_type = data[offset + 4 : offset + 8]
            chunk_data_start = offset + 8
            chunk_data_end = chunk_data_start + length

            if chunk_data_end > len(data):
                break

            if chunk_type == b"tEXt":
                keyword, text = CardParser._decode_text_chunk(
                    data[chunk_data_start:chunk_data_end]
                )
                chunks.append((keyword, text))

            offset = chunk_data_end + 4  # 跳过 CRC (4 bytes)

        return chunks

    @staticmethod
    def _decode_text_chunk(chunk_data: bytes) -> tuple[str, str]:
        """解码 tEXt 块: keyword\0text。"""
        try:
            null_idx = chunk_data.index(0)
        except ValueError:
            return ("", "")
        keyword = chunk_data[:null_idx].decode("latin-1").lower()
        text = chunk_data[null_idx + 1 :].decode("latin-1")
        return keyword, text
