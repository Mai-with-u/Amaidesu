# DecisionProvider API

## 概述

DecisionProvider 是决策 Provider 的抽象基类，负责将 CanonicalMessage 转换为 MessageBase。

## 核心方法

### `async def decide(self, canonical_message: CanonicalMessage) -> MessageBase`
决策核心方法。

**参数**:
- canonical_message: 标准消息对象

**返回**: MessageBase 对象（决策结果）

**示例**:
```python
from src.core.providers.decision_provider import DecisionProvider
from src.canonical.canonical_message import CanonicalMessage
from maim_message import MessageBase
from src.utils.logger import get_logger

class MyDecisionProvider(DecisionProvider):
    """简单的决策 Provider 示例"""

    async def decide(self, canonical_message: CanonicalMessage) -> MessageBase:
        text = canonical_message.text
        
        # 简单决策：直接返回文本消息
        message_segment = MessageSegment(
            type="text",
            data=text
        )
        
        return MessageBase(message_segment=message_segment)
```

## 辅助方法

### `_setup_internal(self)`（可选）
内部设置逻辑，子类可以重写。

**默认行为**: 无操作

**示例**:
```python
class MyDecisionProvider(DecisionProvider):
    def __init__(self, config):
        super().__init__(config)
        self.api_client = None

    async def _setup_internal(self):  # noqa: B027
        """初始化 API 客户端"""
        import httpx
        self.api_client = httpx.Client()
        await super()._setup_internal()

    async def decide(self, canonical_message: CanonicalMessage) -> MessageBase:
        response = await self.api_client.post(
            "https://api.example.com/decide",
            json={"text": canonical_message.text}
        )
        
        # 返回决策结果
        message_segment = MessageSegment(
            type="text",
            data=response.text
        )
        
        return MessageBase(message_segment=message_segment)
```

### `_cleanup_internal(self)`（可选）
内部清理逻辑，子类可以重写。

**默认行为**: 无操作

**示例**:
```python
class MyDecisionProvider(DecisionProvider):
    async def _cleanup_internal(self):  # noqa: B027
        """清理 API 客户端"""
        if self.api_client:
            await self.api_client.aclose()
        await super()._cleanup_internal()
```

## 属性

### `config: Dict[str, Any]`
Provider 配置。

### `event_bus: Optional[EventBus]`
事件总线实例（可选，用于事件通信）。

### `is_setup: bool`
是否已完成设置。

## 完整示例

### MaiCore 决策 Provider

```python
import aiohttp
from typing import Optional
from src.core.providers.decision_provider import DecisionProvider
from src.canonical.canonical_message import CanonicalMessage
from maim_message import MessageBase, MessageSegment, MessageInfo, FormatInfo
from src.utils.logger import get_logger

class MaiCoreDecisionProvider(DecisionProvider):
    """使用 MaiCore WebSocket 的决策 Provider"""

    def __init__(self, config: dict, event_bus: Optional = None):
        super().__init__(config, event_bus)
        self.host = config.get("host", "localhost")
        self.port = config.get("port", 8000)
        self.ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self.session: Optional[aiohttp.ClientSession] = None

    async def _setup_internal(self):  # noqa: B027
        """连接到 MaiCore WebSocket"""
        uri = f"ws://{self.host}:{self.port}"
        self.session = aiohttp.ClientSession()
        self.ws = await self.session.ws_connect(uri)
        await super()._setup_internal()
        self.logger.info(f"连接到 MaiCore: {uri}")

    async def decide(self, canonical_message: CanonicalMessage) -> MessageBase:
        """发送消息到 MaiCore 并获取响应"""
        try:
            # 构造消息
            message = {
                "text": canonical_message.text,
                "user": canonical_message.user.nickname if canonical_message.user else "user",
            }
            
            await self.ws.send_json(message)
            
            # 等待响应
            response = await self.ws.receive_json()
            
            # 构造返回消息
            message_info = MessageInfo(
                user=response.get("user", "user"),
                message_segment=MessageSegment(
                    type="text",
                    data=response.get("text", "")
                )
            )
            
            return MessageBase(message_info=message_info)
            
        except Exception as e:
            self.logger.error(f"决策失败: {e}", exc_info=True)
            raise

    async def _cleanup_internal(self): # noqa: B027
        """断开 WebSocket 连接"""
        if self.ws:
            await self.ws.close()
        if self.session:
            await self.session.close()
        await super()._cleanup_internal()
```

### 本地 LLM 决策 Provider

```python
from src.core.providers.decision_provider import DecisionProvider
from src.canonical.canonical_message import CanonicalMessage
from maim_message import MessageBase, MessageSegment, MessageInfo
from src.utils.logger import get_logger

class LocalLLMDecisionProvider(DecisionProvider):
    """使用本地 LLM API 的决策 Provider"""

    def __init__(self, config: dict, event_bus: Optional = None]):
        super().__init__(config, event_bus)
        self.model = config.get("model", "gpt-4")
        self.api_key = config.get("api_key")
        self.api_url = config.get("api_url", "https://api.openai.com/v1")

    async def decide(self, canonical_message: CanonicalMessage) -> MessageBase:
        """使用本地 LLM 生成响应"""
        import httpx
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": canonical_message.text
                }
            ]
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.api_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=30
            )
            
            result = response.json()
            text = result["choices"][0]["message"]["content"]
            
            message_segment = MessageSegment(
                type="text",
                data=text
            )
            
            message_info = MessageInfo(user="local_llm")
            
            return MessageBase(message_info=message_info, message_segment=message_segment)

    async def _cleanup_internal(self):  # noqa: B027
        """无资源需要清理"""
        await super()._cleanup_internal()
```

---

**相关文档**:
- [Plugin Protocol](./plugin_protocol.md)
- [InputProvider API](./input_provider.md)
- [OutputProvider API](./output_provider.md)
- [EventBus API](./event_bus.md)
