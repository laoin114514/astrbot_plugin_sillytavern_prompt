"""预设管理：系统提示词预设的 JSON 文件 CRUD 操作。

移植自 SillyTavern src/endpoints/presets.js 和 public/scripts/sysprompt.js.
预设存储为 JSON 文件，格式: { "name": "...", "content": "...", "post_history": "..." }
"""

import json
import os
from typing import Optional


class PromptPreset:
    """对应 SillyTavern sysprompt JSON 格式。"""

    def __init__(self, name: str = "", content: str = "", post_history: str = ""):
        self.name = name
        self.content = content
        self.post_history = post_history

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "content": self.content,
            "post_history": self.post_history,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PromptPreset":
        return cls(
            name=data.get("name", ""),
            content=data.get("content", ""),
            post_history=data.get("post_history", ""),
        )

    def __repr__(self):
        return f"PromptPreset(name={self.name!r})"


class PresetStore:
    """管理预设目录中的 JSON 预设文件。"""

    def __init__(self, directory: str):
        self.directory = directory
        os.makedirs(directory, exist_ok=True)

    def _path(self, name: str) -> str:
        safe = name.replace("/", "_").replace("\\", "_")
        return os.path.join(self.directory, f"{safe}.json")

    def list_all(self) -> list[PromptPreset]:
        """列出所有预设。"""
        presets = []
        if not os.path.isdir(self.directory):
            return presets
        for fname in sorted(os.listdir(self.directory)):
            if fname.endswith(".json"):
                preset = self.load(fname[:-5])
                if preset:
                    presets.append(preset)
        return presets

    def list_names(self) -> list[str]:
        """列出所有预设名称。"""
        return [p.name for p in self.list_all()]

    def load(self, name: str) -> Optional[PromptPreset]:
        """根据名称加载一个预设。"""
        path = self._path(name)
        if not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return PromptPreset.from_dict(data)
        except (json.JSONDecodeError, IOError) as e:
            print(f"[ST-Prompt] 加载预设失败 {name}: {e}")
            return None

    def save(self, preset: PromptPreset) -> bool:
        """保存一个预设（覆盖同名文件）。"""
        if not preset.name:
            return False
        path = self._path(preset.name)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(preset.to_dict(), f, ensure_ascii=False, indent=2)
            return True
        except IOError as e:
            print(f"[ST-Prompt] 保存预设失败 {preset.name}: {e}")
            return False

    def delete(self, name: str) -> bool:
        """删除一个预设。"""
        path = self._path(name)
        if not os.path.isfile(path):
            return False
        try:
            os.remove(path)
            return True
        except IOError as e:
            print(f"[ST-Prompt] 删除预设失败 {name}: {e}")
            return False
