# ST 防 OOC 包装器

读取 AstrBot 当前选中的人格，用 **SillyTavern (酒馆) 的分层防 OOC 结构**
重新包装 `system_prompt`，防止 AI 角色扮演中人设崩坏。

## 设计理念

**零配置**。你只需要在 AstrBot 中正常配置人格（prompt + skills + MCP），
插件会自动读取并用酒馆的防 OOC 壳包装起来。

不替代 AstrBot 原生的人格系统 —— 只在其之上加一层防崩保护。

## 工作原理

```
AstrBot 原生管线:
  persona.prompt + skills 文本 → req.system_prompt
        ↓
on_llm_request hook (本插件):
  ┌─────────────────────────────────────┐
  │ [Main] 对话框架 + 防 OOC 基础指令    │ ← 硬编码 ST 风格
  │ [Character Identity] AstrBot 原生    │ ← 人格 prompt + skills + MCP
  │ [Jailbreak] 最终锚定提醒             │ ← 硬编码 ST 风格
  └─────────────────────────────────────┘
        ↓
  req.system_prompt = 组装后
```

### 三层结构

| 层 | 作用 | 内容来源 |
|---|------|---------|
| **Main** | 设定对话框架，防止 AI 退回"助手模式" | 硬编码 (ST 经典指令) |
| **Persona** | 角色身份 + 技能 + 工具 | AstrBot 原生 `req.system_prompt` |
| **Jailbreak** | 最后锚定，强化人设边界 | 硬编码 (ST 风格提醒) |

### 为什么有效

1. **Main 在最前面** — 用 system role 明确"这是角色扮演"，防止 AI 默认助手行为
2. **Jailbreak 在最末尾** — 长对话中前面的指令可能被稀释，末尾的提醒离生成点最近、权重最高
3. **AstrBot 原生内容完整保留** — skills/MCP/tools 不会丢失

## 安装

1. 将本目录放入 AstrBot 的 `plugins/` 目录
2. 重启 AstrBot
3. 无需任何配置，插件自动生效

## 使用

1. 在 AstrBot WebUI 中正常配置人格（prompt + skills + tools）
2. 确保已选中该人格
3. 发送消息，AI 将自动以 ST 防 OOC 模式回复

**验证方法：** 发送 `/st_prompt` 可查看当前组装后的完整 system_prompt。

## 文件结构

```
astrbot_plugin_sillytavern_prompt/
├── main.py           # 唯一核心文件 (~70行)
├── metadata.yaml     # 插件元数据
├── requirements.txt  # 无额外依赖
└── README.md         # 本文件
```

## 兼容性

- AstrBot >= 4.16
- 支持所有平台 (QQ / Telegram / Discord / WeChat / WebChat 等)
- 不影响 AstrBot 原生的 skills / tools / MCP

## License

AGPL-3.0 (与 SillyTavern 保持一致)
