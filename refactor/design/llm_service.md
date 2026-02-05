# LLM 服务设计

## 核心目标

LLMService 是**核心基础设施**，与 EventBus 同级，为所有需要 AI 能力的模块提供统一的 LLM 调用接口。

---

## 定位说明

**LLMService 不是 Provider**：

| 概念 | Provider | LLMService |
|------|----------|------------|
| 定位 | 数据流中的节点 | 基础设施服务 |
| 职责 | 处理特定数据 | 提供 AI 能力 |
| 调用方 | 被 Manager 管理 | 被任何模块调用 |
| 类比 | 管道中的处理器 | 类似 EventBus、Logger |

---

## 架构位置

```
┌─────────────────────────────────────────────┐
│              基础设施层                      │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐     │
│  │EventBus │  │LLMService│  │ Logger │     │
│  └─────────┘  └─────────┘  └─────────┘     │
└─────────────────────────────────────────────┘
                    ↑
        ┌───────────┼───────────┐
        ↓           ↓           ↓
   DecisionProvider  IntentParser  其他模块
```

---

## 核心接口

```python
class LLMService:
    """统一的 LLM 调用服务"""
    
    async def setup(self, config: Dict[str, Any]) -> None:
        """根据配置初始化 LLM 客户端"""
    
    async def chat(
        self,
        messages: List[Dict[str, str]],
        client_type: str = "llm",  # llm | llm_fast | vlm
        **kwargs
    ) -> str:
        """发送聊天请求"""
    
    async def chat_json(
        self,
        messages: List[Dict[str, str]],
        schema: Type[BaseModel],
        client_type: str = "llm_fast",
    ) -> BaseModel:
        """发送请求并解析为结构化数据"""
    
    async def cleanup(self) -> None:
        """清理资源"""
```

---

## 客户端类型

| 类型 | 用途 | 典型模型 |
|------|------|----------|
| `llm` | 通用对话 | GPT-4, Claude |
| `llm_fast` | 快速响应（意图解析等） | GPT-3.5, Haiku |
| `vlm` | 视觉理解 | GPT-4V, Claude Vision |

---

## 使用示例

### 在 DecisionProvider 中使用

```python
class LocalLLMDecisionProvider(DecisionProvider):
    def __init__(self, llm_service: LLMService):
        self.llm = llm_service
    
    async def decide(self, message: NormalizedMessage) -> Intent:
        response = await self.llm.chat_json(
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": message.text}
            ],
            schema=IntentResponse,
            client_type="llm"
        )
        return Intent.from_response(response)
```

### 在 IntentParser 中使用

```python
class IntentParser:
    def __init__(self, llm_service: LLMService):
        self.llm = llm_service
    
    async def parse(self, text: str) -> Intent:
        # 使用快速模型解析意图
        result = await self.llm.chat_json(
            messages=[...],
            schema=ParsedIntent,
            client_type="llm_fast"  # 快速响应
        )
        return Intent.from_parsed(result)
```

---

## 配置示例

```toml
[llm]
backend = "openai"
model = "gpt-4"
api_key = "your-key"
base_url = "https://api.openai.com/v1"

[llm_fast]
backend = "openai"
model = "gpt-3.5-turbo"
api_key = "your-key"

[vlm]
backend = "openai"
model = "gpt-4-vision-preview"
api_key = "your-key"
```

---

## 内置功能

- **重试机制**：网络错误自动重试
- **超时控制**：可配置请求超时
- **错误处理**：统一的异常类型
- **日志记录**：请求/响应日志（可选）
