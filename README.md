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
  begin_dialogs → req.contexts (前置)
        ↓
on_llm_request hook (本插件):
  system_prompt 重组 → Main 指令 + 原生内容
  contexts 末尾注入 → Jailbreak 系统消息 (ST 的 post-history 定位)
        ↓
最终消息结构:
  system: [Main]
  system: [Persona + Skills + MCP]      ← AstrBot 原生
  system: [enhanceDefs]
  user/assistant: [begin_dialogs]       ← AstrBot 注入 (few-shot)
  user/assistant: [chat messages...]
  system: [Reminder @ depth=6]          ← 插件深度注入
  user/assistant: [more chat...]
  system: [Reminder @ depth=3]          ← 插件深度注入
  user/assistant: [more chat...]
  user: [最新消息]
  system: [Jailbreak @ depth=0]         ← 生成前最后锚定
```

### 防 OOC 机制

| 层 | 位置 | 作用 |
|---|------|------|
| **Main** | system_prompt 最前 | 设定对话框架，防止 AI 退回助手模式 |
| **Persona** | system_prompt 中 | AstrBot 原生人格 prompt + skills + MCP |
| **enhanceDefs** | system_prompt 末 | AI 可用训练知识扩充角色，但以显式定义为准 |
| **Depth Injection** | contexts 深度 6/3 | 长对话中周期性刷新角色认知 (ST Author's Note 移植) |
| **Jailbreak** | contexts 末尾 | 生成前最后锚定，注意力权重最高 |

### 为什么有效

1. **Main 在最前面** — system role 明确"这是角色扮演"
2. **Jailbreak 在最末尾** — 离生成点最近，权重最高
3. **Depth Injection** — 长对话中早期指令被稀释，中段提醒刷新人设
4. **AstrBot 原生内容完整保留** — skills/MCP/tools 不受影响

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
