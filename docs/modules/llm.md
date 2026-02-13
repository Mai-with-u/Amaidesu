# LLM 客户端管理模块

负责管理多种 LLM 客户端（OpenAI, Ollama 等）。

## 概述

`src/modules/llm/` 模块提供统一的 LLM 调用接口：
- 多客户端支持（OpenAI, Ollama, Anthropic 等）
- 统一的调用接口
- Token 使用量统计

## 主要组件

| 文件 | 功能 |
|------|------|
| `manager.py` | LLMManager - LLM 客户端管理器 |
| `clients/` | LLM 客户端实现 |

### clients 子模块

| 文件 | 功能 |
|------|------|
| `client_base.py` | 客户端基类定义 |
| `openai_client.py` | OpenAI API 客户端 |
| `token_usage_manager.py` | Token 使用量管理 |

## 核心 API

### LLMManager

```python
from src.modules.llm import LLMManager

# 创建 LLM 管理器
llm_manager = LLMManager(config)

# 同步调用
response = await llm_manager.chat(
    messages=[
        {"role": "user", "content": "你好"}
    ],
    model="gpt-4",
    temperature=0.7
)

# 流式调用
async for chunk in llm_manager.chat_stream(
    messages=[...],
    model="gpt-4"
):
    print(chunk.content, end="")
```

### 客户端配置

```python
# OpenAI 配置
openai_config = {
    "provider": "openai",
    "model": "gpt-4",
    "api_key": "sk-...",
    "base_url": None,  # 可选，自定义端点
    "timeout": 30,
    "max_retries": 3,
}

# Ollama 配置
ollama_config = {
    "provider": "ollama",
    "model": "llama2",
    "base_url": "http://localhost:11434",
    "timeout": 60,
}
```

### Token 使用量统计

```python
from src.modules.llm.clients import TokenUsageManager

usage_manager = TokenUsageManager()

# 记录使用量
usage_manager.record_usage(
    model="gpt-4",
    prompt_tokens=100,
    completion_tokens=50,
    total_tokens=150
)

# 获取统计
stats = usage_manager.get_usage_stats("gpt-4")
print(f"Total tokens: {stats.total_tokens}")
print(f"Total cost: ${stats.total_cost}")
```

## 支持的客户端

| 客户端 | 说明 | 配置键 |
|--------|------|--------|
| OpenAI | OpenAI API | `openai` |
| Ollama | 本地 LLM | `ollama` |
| Anthropic | Claude API | `anthropic` |
| Azure OpenAI | Azure OpenAI | `azure_openai` |

## 使用示例

### 在 DecisionProvider 中使用

```python
class LLMDecisionProvider(DecisionProvider):
    def __init__(self, config, dependencies):
        self.llm_manager = dependencies.get("llm_manager")
        self.prompt_manager = dependencies.get("prompt_manager")

    async def _process_message(self, message: NormalizedMessage) -> Optional[Intent]:
        # 构建提示词
        prompt = self.prompt_manager.render(
            "decision/intent_parser",
            user_message=message.content,
            context=await self._get_context(message.session_id)
        )

        # 调用 LLM
        response = await self.llm_manager.chat(
            messages=[{"role": "user", "content": prompt}],
            model="gpt-4"
        )

        # 解析响应
        return self._parse_response(response.content)
```

### 自定义客户端

```python
from src.modules.llm.clients import BaseLLMClient

class CustomLLMClient(BaseLLMClient):
    async def chat(self, messages: List[Dict], **kwargs) -> LLMResponse:
        # 实现自定义逻辑
        pass

    async def chat_stream(self, messages: List[Dict], **kwargs):
        # 实现流式逻辑
        pass

# 注册自定义客户端
llm_manager.register_client("custom", CustomLLMClient)
```

---

*最后更新：2026-02-14*
