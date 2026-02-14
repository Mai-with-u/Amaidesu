# 提示词管理模块

负责统一管理 LLM 提示词模板。

## 概述

`src/modules/prompts/` 模块提供提示词管理功能：
- Markdown 模板文件存储
- 配置覆盖支持
- 全局单例访问

## 目录结构

```
src/modules/prompts/
├── manager.py           # PromptManager 核心实现
├── templates/           # 提示词模板目录
│   ├── decision/       # 决策相关模板
│   ├── output/        # 输出相关模板
│   └── system/        # 系统模板
└── __init__.py        # 模块导出
```

## 核心 API

### 获取提示词管理器

```python
from src.modules.prompts import get_prompt_manager

prompt_manager = get_prompt_manager()
```

### 获取原始提示词

```python
# 获取未渲染的模板
template = prompt_manager.get_raw("decision/intent_parser")
```

### 渲染提示词

```python
# 渲染提示词模板
prompt = prompt_manager.render(
    "decision/intent_parser",
    user_message="你好",
    context="之前对话..."
)
```

### 重置管理器

```python
from src.modules.prompts import reset_prompt_manager

# 重置为默认状态
reset_prompt_manager()
```

## 模板格式

提示词模板使用 Markdown 格式，支持 YAML frontmatter：

```markdown
---
name: intent_parser
version: 1.0.0
description: 意图解析提示词
variables:
  - user_message
  - context
---

# 意图解析

用户消息：$user_message

上下文：$context

请分析用户的意图并返回结构化结果。
```

## 使用示例

### 在 DecisionProvider 中使用

```python
class MyDecisionProvider(DecisionProvider):
    def __init__(self, config, dependencies):
        self.prompt_manager = dependencies.get("prompt_manager")

    async def _process_message(self, message: NormalizedMessage) -> Optional[Intent]:
        # 渲染提示词
        prompt = self.prompt_manager.render(
            "decision/intent_parser",
            user_message=message.content,
            context=await self._get_context(message.session_id)
        )

        # 调用 LLM
        response = await self.llm_manager.chat([
            {"role": "user", "content": prompt}
        ])

        return self._parse_response(response.content)
```

### 在 OutputProvider 中使用

```python
class MyOutputProvider(OutputProvider):
    def __init__(self, config, dependencies):
        self.prompt_manager = dependencies.get("prompt_manager")

    async def _render(self, intent: Intent) -> None:
        # 渲染输出模板
        template = self.prompt_manager.render(
            "output/text_formatter",
            text=intent.response_text,
            emotion=intent.emotion.value
        )
```

## 模板变量

常用变量：

| 变量 | 说明 |
|------|------|
| `$user_message` | 用户消息 |
| `$context` | 对话上下文 |
| `$emotion` | 情感类型 |
| `$actions` | 动作列表 |
| `$session_id` | 会话 ID |

## 详细文档

更详细的提示词管理指南请查看 [开发指南 - 提示词管理](../development/prompt-management.md)。

---

*最后更新：2026-02-14*
