# Phase 3: 决策层 + Layer 3-4

## 🎯 目标

实现：
1. **决策层**：可替换的决策Provider系统
2. **Layer 3: 中间表示层**：统一消息格式
3. **Layer 4: 表现理解层**：解析MessageBase → Intent

## 📁 目录结构

```
src/
├── canonical/                         # Layer 3: 中间表示
│   ├── __init__.py
│   ├── canonical_message.py
│   ├── message_builder.py
│   └── maicore_adapter.py            # MaiCore适配器
│
├── understanding/                     # Layer 4: 表现理解
│   ├── __init__.py
│   ├── response_parser.py
│   ├── text_cleanup.py
│   └── emotion_judge.py
│
└── core/
    ├── decision_manager.py             # 决策管理器（新增）
    └── providers/                     # 决策Provider实现
        ├── maicore_decision_provider.py    # MaiCore决策Provider
        ├── local_llm_decision_provider.py   # 本地LLM决策Provider
        └── rule_engine_decision_provider.py # 规则引擎决策Provider
```

## 📝 实施内容

### 3.1 Layer 3: 中间表示层

#### 创建CanonicalMessage

`src/canonical/canonical_message.py`:
```python
import time
from typing import Dict, Any

class CanonicalMessage:
    """标准化消息格式 - Layer 3的输出格式"""

    def __init__(
        self,
        text: str,
        source: str,
        user_id: str = None,
        user_name: str = None,
        timestamp: float = None,
        metadata: Dict[str, Any] = None
    ):
        self.text = text
        self.source = source
        self.user_id = user_id
        self.user_name = user_name
        self.timestamp = timestamp or time.time()
        self.metadata = metadata or {}

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "text": self.text,
            "source": self.source,
            "user_id": self.user_id,
            "user_name": self.user_name,
            "timestamp": self.timestamp,
            "metadata": self.metadata
        }
```

`src/canonical/message_builder.py`:
```python
from typing import Dict, Any
from src.canonical.canonical_message import CanonicalMessage

class MessageBuilder:
    """消息构建器 - 便捷创建CanonicalMessage"""

    @staticmethod
    def create_from_text(text: str, source: str, **metadata) -> CanonicalMessage:
        """从文本创建消息"""
        return CanonicalMessage(
            text=text,
            source=source,
            **metadata
        )

    @staticmethod
    def create_from_dict(data: Dict[str, Any]) -> CanonicalMessage:
        """从字典创建消息"""
        return CanonicalMessage(
            text=data.get("text", ""),
            source=data.get("source", "unknown"),
            user_id=data.get("user_id"),
            user_name=data.get("user_name"),
            timestamp=data.get("timestamp"),
            metadata=data.get("metadata", {})
        )
```

### 3.2 决策层实现

#### MaiCore决策Provider

`src/core/providers/maicore_decision_provider.py`:
```python
from maim_message import MessageBase
from src.core.decision_provider import DecisionProvider, CanonicalMessage
from src.core.amaidesu_core import AmaidesuCore
from src.utils.logger import get_logger

class MaiCoreDecisionProvider:
    """MaiCore决策Provider（默认实现）"""

    def __init__(self, config: dict):
        self.config = config
        self.core: AmaidesuCore = None
        self.logger = get_logger("MaiCoreDecisionProvider")

    async def setup(self, event_bus, config: dict):
        """初始化MaiCore连接"""
        # AmaidesuCore会在外部传入
        self.logger.info("MaiCoreDecisionProvider setup complete")

    async def decide(self, canonical_message: CanonicalMessage) -> MessageBase:
        """发送给MaiCore进行决策"""
        self.logger.info(f"Sending to MaiCore: {canonical_message.text[:50]}...")

        # 构建MessageBase
        # 这里需要调用AmaidesuCore的方法发送给MaiCore
        # 实际实现会通过AmaidesuCore发送到MaiCore WebSocket

        # 返回MessageBase（模拟）
        from maim_message import MessageBase, BaseMessageInfo, UserInfo, Seg, FormatInfo
        message_info = BaseMessageInfo(
            platform="amaidesu",
            message_id=f"maicore_{int(time.time() * 1000)}",
            time=time.time(),
            user_info=UserInfo(
                platform="amaidesu",
                user_id=canonical_message.user_id or "unknown",
                user_nickname=canonical_message.user_name or "User"
            ),
            format_info=FormatInfo(
                content_format=["text"],
                accept_format=["text"]
            )
        )

        message_segment = Seg(type="text", data="这是MaiCore的回复文本")

        return MessageBase(
            message_info=message_info,
            message_segment=message_segment,
            raw_message="这是MaiCore的回复文本"
        )

    async def cleanup(self):
        """清理资源"""
        self.logger.info("MaiCoreDecisionProvider cleanup")
```

#### 本地LLM决策Provider（示例）

`src/core/providers/local_llm_decision_provider.py`:
```python
from maim_message import MessageBase
from src.core.decision_provider import DecisionProvider, CanonicalMessage
from src.utils.logger import get_logger

class LocalLLMDecisionProvider:
    """本地LLM决策Provider（可选实现）"""

    def __init__(self, config: dict):
        self.config = config
        self.model = config.get("model", "gpt-4")
        self.api_key = config.get("api_key")
        self.logger = get_logger("LocalLLMDecisionProvider")

    async def setup(self, event_bus, config: dict):
        """初始化LLM客户端"""
        self.logger.info(f"LocalLLMDecisionProvider setup with model: {self.model}")

    async def decide(self, canonical_message: CanonicalMessage) -> MessageBase:
        """使用本地LLM进行决策"""
        self.logger.info(f"Using local LLM: {canonical_message.text[:50]}...")

        # 调用本地LLM API
        # response_text = await self._call_llm(canonical_message.text)

        response_text = "这是本地LLM的回复"

        # 构建MessageBase
        from maim_message import MessageBase, BaseMessageInfo, UserInfo, Seg, FormatInfo
        message_info = BaseMessageInfo(
            platform="amaidesu",
            message_id=f"local_llm_{int(time.time() * 1000)}",
            time=time.time(),
            user_info=UserInfo(
                platform="amaidesu",
                user_id=canonical_message.user_id or "unknown",
                user_nickname=canonical_message.user_name or "User"
            ),
            format_info=FormatInfo(
                content_format=["text"],
                accept_format=["text"]
            )
        )

        message_segment = Seg(type="text", data=response_text)

        return MessageBase(
            message_info=message_info,
            message_segment=message_segment,
            raw_message=response_text
        )

    async def _call_llm(self, prompt: str) -> str:
        """调用LLM API"""
        # 实际实现会调用OpenAI API或其他LLM API
        return "这是LLM生成的回复"

    async def cleanup(self):
        """清理资源"""
        self.logger.info("LocalLLMDecisionProvider cleanup")
```

### 3.3 Layer 4: 表现理解层

#### 响应解析器

`src/understanding/response_parser.py`:
```python
from typing import Protocol
from maim_message import MessageBase

class Intent:
    """意图对象 - Layer 4的输出格式"""

    def __init__(
        self,
        original_text: str,
        emotion: str = "NEUTRAL",
        response_text: str = "",
        actions: list = None,
        metadata: dict = None
    ):
        self.original_text = original_text
        self.emotion = emotion
        self.response_text = response_text
        self.actions = actions or []
        self.metadata = metadata or {}

class ResponseParser(Protocol):
    """响应解析器协议 - Layer 4"""

    async def parse(self, message: MessageBase) -> Intent:
        """解析MessageBase生成Intent"""
        ...
```

`src/understanding/emotion_judge.py`:
```python
from maim_message import MessageBase
from src.understanding.response_parser import Intent, ResponseParser
from src.utils.logger import get_logger

class EmotionJudgeProvider:
    """情感判断Provider"""

    def __init__(self, config: dict):
        self.config = config
        self.logger = get_logger("EmotionJudgeProvider")

    async def parse(self, message: MessageBase) -> Intent:
        """解析消息并判断情感"""
        text = ""
        if message.message_segment and message.message_segment.type == "text":
            text = message.message_segment.data

        # 判断情感
        emotion = await self._judge_emotion(text)

        return Intent(
            original_text=text,
            emotion=emotion,
            response_text=text,
            metadata={"source": "emotion_judge"}
        )

    async def _judge_emotion(self, text: str) -> str:
        """判断文本情感"""
        # 简单实现（实际应使用更复杂的算法）
        positive_keywords = ["开心", "高兴", "哈哈", "棒", "好"]
        negative_keywords = ["难过", "伤心", "不好", "讨厌"]

        for keyword in positive_keywords:
            if keyword in text:
                return "HAPPY"

        for keyword in negative_keywords:
            if keyword in text:
                return "SAD"

        return "NEUTRAL"
```

## ✅ 验证标准

1. ✅ CanonicalMessage可以正确构建和转换
2. ✅ MaiCoreDecisionProvider可以发送消息到MaiCore
3. ✅ LocalLLMDecisionProvider可以调用本地LLM
4. ✅ ResponseParser可以解析MessageBase生成Intent
5. ✅ EmotionJudge可以正确判断情感
6. ✅ 决策层可以切换不同的DecisionProvider

## 📝 提交

```bash
git add src/canonical/ src/understanding/ src/core/decision_manager.py src/core/providers/

git commit -m "feat(phase3): implement decision layer and Layer 3-4"
```
