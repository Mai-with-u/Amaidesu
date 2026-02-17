# MaiBot 提示词覆盖功能

## 功能概述

Amaidesu 支持通过 `maim_message` 的 `template_info` 机制覆盖 MaiBot 的回复提示词，使 VTuber 在直播场景下具有更一致的个性和回复风格。

**默认启用**: 该功能默认启用，确保所有消息都使用 VTuber 风格的提示词。

## 工作原理

1. Amaidesu 发送消息到 MaiBot 时，携带 `template_info` 和 `group_info`
2. `template_info` 包含自定义的提示词模板
3. `group_info` 标识消息来自直播间（群聊），确保使用群聊提示词
4. MaiBot 收到消息后，在 `async_message_scope` 作用域内注册这些模板
5. 处理该消息时，MaiBot 使用覆盖后的提示词而非默认提示词

**重要**: 模板变量（如 `{bot_name}`, `{identity}` 等）由 MaiBot 端填充，Amaidesu 只需提供原始模板字符串。

### 群聊 vs 私聊

MaiBot 通过 `group_info` 判断消息类型：
- **有 `group_info`**: 使用群聊模板（`replyer_prompt`, `chat_target_group1/2`）
- **无 `group_info`**: 使用私聊模板（`private_replyer_prompt`, `chat_target_private1/2`）

Amaidesu 默认为所有消息设置 `group_info`（`group_id="live_room"`, `group_name="直播间"`），确保使用群聊模板。

## 配置方法

在 `config.toml` 的 `[providers.decision.maicore]` 段添加：

```toml
[providers.decision.maicore]
# 现有配置...
host = "127.0.0.1"
port = 8000

# 提示词覆盖配置
enable_prompt_override = true
override_template_name = "amaidesu_override"
override_templates = [
    "replyer_prompt",      # think_level=1 时使用
    "replyer_prompt_0",    # think_level=0 时使用（轻量回复）
    "chat_target_group1",
    "chat_target_group2",
]
```

### 配置项说明

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `enable_prompt_override` | bool | `true` | 是否启用提示词覆盖 |
| `override_template_name` | str | `"amaidesu_override"` | 作用域名称 |
| `override_templates` | list | 见上方 | 要覆盖的模板列表 |

### 重要：replyer_prompt vs replyer_prompt_0

MaiBot 根据消息复杂度选择不同的回复模板：
- **`think_level=0`**：使用 `replyer_prompt_0`（轻量回复，简短平淡）
- **`think_level=1`**：使用 `replyer_prompt`（标准回复，日常口语化）

**必须同时覆盖两个模板**，否则在某些场景下仍会使用默认的 QQ 群聊提示词。

## 可覆盖的提示词

### 核心回复模板

| 模板名称 | 用途 |
|---------|------|
| `replyer_prompt` | 群聊回复（主模板） |
| `replyer_prompt_0` | 群聊回复（备选） |
| `private_replyer_prompt` | 私聊回复 |

### 场景描述模板

| 模板名称 | 用途 |
|---------|------|
| `chat_target_group1` | 群聊场景描述 |
| `chat_target_group2` | 群聊场景简短描述 |
| `chat_target_private1` | 私聊场景描述 |
| `chat_target_private2` | 私聊场景简短描述 |

## 自定义模板

模板文件位于 `src/modules/prompts/templates/maibot_override/` 目录。

### 模板格式

使用 YAML frontmatter 定义元数据：

```markdown
---
name: replyer_prompt
version: "1.0"
description: "VTuber 场景回复提示词"
variables:
  - bot_name
  - identity
  - dialogue_prompt
---

{knowledge_prompt}
你是一位正在直播的 VTuber {bot_name}...
{dialogue_prompt}
现在，你说：
```

### 注意事项

1. **保留所有变量**: 模板中的变量占位符（如 `{identity}`）必须保留，由 MaiBot 端填充
2. **使用原始字符串**: 服务类使用 `get_raw()` 获取模板，不进行变量渲染
3. **模板缺失**: 缺失的模板会被跳过并记录警告日志

## 常见问题

### Q: 覆盖后 MaiBot 回复不符合预期？

检查：
1. 模板是否包含所有必需的变量占位符
2. `override_templates` 配置是否包含目标模板名称
3. 查看 Amaidesu 日志确认 template_info 已注入

### Q: 如何临时禁用覆盖？

设置 `enable_prompt_override = false` 或注释掉该配置项。

### Q: 多个 DecisionProvider 会冲突吗？

不会。`template_info` 在 `MaiCoreDecisionProvider` 层注入，其他 DecisionProvider（如 LLMDecisionProvider）不使用此机制。

## 相关文件

- 服务类: `src/modules/prompts/template_override_service.py`
- Provider 修改: `src/domains/decision/providers/maicore/maicore_decision_provider.py`
- 模板目录: `src/modules/prompts/templates/maibot_override/`
