# 决策层设计

## 🎯 核心目标

实现可替换的决策系统，支持多种决策方式：
1. **MaiCoreDecisionProvider**：默认实现，使用MaiCore进行决策
2. **LocalLLMDecisionProvider**：可选实现，使用本地LLM进行决策
3. **RuleEngineDecisionProvider**：可选实现，使用规则引擎进行决策

---

## 📊 决策层位置

```
Layer 3: 中间表示（CanonicalMessage）
    ↓
Layer 4: 决策层（DecisionProvider）⭐ 可替换、可扩展
    ├─ MaiCoreDecisionProvider (默认）
    ├─ LocalLLMDecisionProvider (可选)
    └─ RuleEngineDecisionProvider (可选)
    ↓
DecisionProvider返回MessageBase
    ↓
Layer 5: 表现理解（解析MessageBase → Intent）
```

---

## 🔗 核心接口

### DecisionProvider接口

```python
from typing import Protocol
from src.core.event_bus import EventBus
from src.canonical.canonical_message import CanonicalMessage

class DecisionProvider(Protocol):
    """决策Provider接口 - 决策层

    支持多种决策实现：MaiCore、本地LLM、规则引擎等
    """

    async def setup(self, event_bus: EventBus, config: dict):
        """
        初始化决策Provider

        Args:
            event_bus: 事件总线实例
            config: Provider配置
        """
        ...

    async def decide(self, canonical_message: CanonicalMessage):
        """
        根据CanonicalMessage做出决策

        Args:
            canonical_message: 标准化消息

        Returns:
            MessageBase: 决策结果
        """
        ...

    async def cleanup(self):
        """清理资源"""
        ...

    def get_info(self) -> dict:
        """
        获取DecisionProvider信息

        Returns:
            dict: Provider信息（name, version, description等）
        """
        return {
            "name": "DecisionProviderName",
            "version": "1.0.0",
            "description": "Decision provider description",
            "api_version": "1.0"
        }
```

---

## 🎨 具体实现

### 1. MaiCoreDecisionProvider（默认）

**设计理念**：继续使用现有的maim_message WebSocket通信

**关键特性**：
- ✅ 使用maim_message.Router进行WebSocket连接
- ✅ 保持与MaiCore的兼容性
- ✅ 保留所有现有功能

```python
from maim_message import MessageBase
from src.core.decision_provider import DecisionProvider, CanonicalMessage
from src.utils.logger import get_logger

class MaiCoreDecisionProvider:
    """MaiCore决策Provider（默认实现）"""

    def __init__(self, config: dict):
        self.config = config
        self.host = config.get("host", "localhost")
        self.port = config.get("port", 8000)
        self.router = None
        self.logger = get_logger("MaiCoreDecisionProvider")

    async def setup(self, event_bus: EventBus, config: dict):
        """初始化WebSocket连接（自己管理！）"""
        from maim_message import Router, RouteConfig, TargetConfig

        ws_url = f"ws://{self.host}:{self.port}/ws"

        route_config = RouteConfig(
            route_config={
                "amaidesu": TargetConfig(
                    url=ws_url,
                    token=None
                )
            }
        )

        self.router = Router(route_config)
        self.router.register_class_handler(self._handle_maicore_message)

        # 订阅EventBus
        event_bus.on("canonical.message_ready", self._on_canonical_message)

        self.logger.info(f"MaiCore WebSocket连接已配置: {ws_url}")

        # 启动WebSocket连接
        self._ws_task = asyncio.create_task(self._run_websocket())

    async def _run_websocket(self):
        """运行WebSocket连接（自己管理！）"""
        try:
            await self.router.run()
        except asyncio.CancelledError:
            self.logger.info("WebSocket任务被取消")
        except Exception as e:
            self.logger.error(f"WebSocket异常: {e}", exc_info=True)

    async def _on_canonical_message(self, event: dict):
        """处理CanonicalMessage事件"""
        canonical_message = event.get("data")

        # 构建MessageBase
        message = self._build_messagebase(canonical_message)

        # 发送给MaiCore（自己管理！）
        await self.router.send_message(message)

    async def _handle_maicore_message(self, message_data: dict):
        """处理MaiCore返回的消息"""
        message = MessageBase.from_dict(message_data)

        # 发布到EventBus
        await self.event_bus.emit("decision.response_generated", {
            "data": message
        })

    async def decide(self, canonical_message: CanonicalMessage):
        """决策接口"""
        # 构建MessageBase
        message = self._build_messagebase(canonical_message)

        # 发送给MaiCore
        await self.router.send_message(message)

        # 简化实现：等待响应（实际应该用asyncio.Queue）
        # 响应会通过_handle_maicore_message回调

        return message

    def _build_messagebase(self, canonical_message: CanonicalMessage):
        """构建MessageBase"""
        from maim_message import MessageBase, BaseMessageInfo, UserInfo, Seg, FormatInfo
        # ... 构建逻辑

    async def cleanup(self):
        """清理资源"""
        if self._ws_task:
            self._ws_task.cancel()
        self.logger.info("MaiCore WebSocket连接已清理")
```

### 2. LocalLLMDecisionProvider（可选）

**设计理念**：使用本地LLM API进行决策，无需MaiCore

**关键特性**：
- ✅ 使用OpenAI API或其他LLM API
- ✅ 无需外部依赖
- ✅ 可配置不同的模型
- ✅ 支持离线场景

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
        self.base_url = config.get("base_url", "https://api.openai.com/v1")
        self.logger = get_logger("LocalLLMDecisionProvider")

    async def setup(self, event_bus: EventBus, config: dict):
        """初始化LLM客户端"""
        # 订阅EventBus
        event_bus.on("canonical.message_ready", self._on_canonical_message)

        self.logger.info(f"LocalLLM DecisionProvider初始化完成，模型: {self.model}")

    async def _on_canonical_message(self, event: dict):
        """处理CanonicalMessage事件"""
        canonical_message = event.get("data")

        # 调用LLM API
        response_text = await self._call_llm(canonical_message.text)

        # 构建MessageBase
        message = self._build_messagebase(canonical_message, response_text)

        # 发布到EventBus
        await self.event_bus.emit("decision.response_generated", {
            "data": message
        })

    async def decide(self, canonical_message: CanonicalMessage):
        """决策接口"""
        # 调用LLM API
        response_text = await self._call_llm(canonical_message.text)

        # 构建MessageBase
        message = self._build_messagebase(canonical_message, response_text)

        return message

    async def _call_llm(self, prompt: str) -> str:
        """调用LLM API"""
        import aiohttp

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        data = {
            "model": self.model,
            "messages": [
                {"role": "user", "content": prompt}
            ]
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=data
            ) as response:
                result = await response.json()
                return result["choices"][0]["message"]["content"]

    def _build_messagebase(self, canonical_message: CanonicalMessage, response_text: str):
        """构建MessageBase"""
        from maim_message import MessageBase, BaseMessageInfo, UserInfo, Seg, FormatInfo
        # ... 构建逻辑

    async def cleanup(self):
        """清理资源"""
        self.logger.info("LocalLLM DecisionProvider cleanup")
```

### 3. RuleEngineDecisionProvider（可选）

**设计理念**：使用规则引擎进行决策，无需AI

**关键特性**：
- ✅ 基于规则匹配
- ✅ 无需外部依赖
- ✅ 可配置规则文件
- ✅ 适用于简单场景

```python
from maim_message import MessageBase
from src.core.decision_provider import DecisionProvider, CanonicalMessage
from src.utils.logger import get_logger

class RuleEngineDecisionProvider:
    """规则引擎决策Provider（可选实现）"""

    def __init__(self, config: dict):
        self.config = config
        self.rules_file = config.get("rules_file", "rules.json")
        self.logger = get_logger("RuleEngineDecisionProvider")
        self._rules = []

    async def setup(self, event_bus: EventBus, config: dict):
        """初始化规则引擎"""
        # 订阅EventBus
        event_bus.on("canonical.message_ready", self._on_canonical_message)

        # 加载规则
        await self._load_rules()

        self.logger.info(f"RuleEngine DecisionProvider初始化完成，规则文件: {self.rules_file}")

    async def _on_canonical_message(self, event: dict):
        """处理CanonicalMessage事件"""
        canonical_message = event.get("data")

        # 匹配规则
        response_text = self._match_rules(canonical_message.text)

        # 构建MessageBase
        message = self._build_messagebase(canonical_message, response_text)

        # 发布到EventBus
        await self.event_bus.emit("decision.response_generated", {
            "data": message
        })

    async def decide(self, canonical_message: CanonicalMessage):
        """决策接口"""
        # 匹配规则
        response_text = self._match_rules(canonical_message.text)

        # 构建MessageBase
        message = self._build_messagebase(canonical_message, response_text)

        return message

    async def _load_rules(self):
        """加载规则文件"""
        # 从JSON文件加载规则
        pass

    def _match_rules(self, text: str) -> str:
        """匹配规则"""
        # 简化实现：基于关键词匹配
        # 实际应使用复杂的规则引擎
        pass

    def _build_messagebase(self, canonical_message: CanonicalMessage, response_text: str):
        """构建MessageBase"""
        from maim_message import MessageBase, BaseMessageInfo, UserInfo, Seg, FormatInfo
        # ... 构建逻辑

    async def cleanup(self):
        """清理资源"""
        self.logger.info("RuleEngine DecisionProvider cleanup")
```

---

## 📊 DecisionManager设计

```python
from typing import Dict, Any, Optional
from src.core.event_bus import EventBus
from src.core.decision_provider import DecisionProvider, CanonicalMessage

class DecisionManager:
    """决策管理器 - 管理决策Provider"""

    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self.logger = get_logger("DecisionManager")
        self._factory = DecisionProviderFactory()
        self._current_provider: DecisionProvider = None
        self._provider_name: str = None

    async def setup(self, provider_name: str, config: dict):
        """设置决策Provider"""
        provider_class = self.factory._providers.get(provider_name)
        if not provider_class:
            raise ValueError(f"DecisionProvider not found: {provider_name}")

        if self._current_provider:
            await self._current_provider.cleanup()

        self._current_provider = provider_class(config)
        self._provider_name = provider_name
        await self._current_provider.setup(self.event_bus, config)

    async def decide(self, canonical_message: CanonicalMessage):
        """进行决策"""
        if not self._current_provider:
            raise RuntimeError("No decision provider configured")
        return await self._current_provider.decide(canonical_message)

    async def switch_provider(self, provider_name: str, config: dict):
        """切换决策Provider（运行时）"""
        await self.setup(provider_name, config)

    async def cleanup(self):
        """清理资源"""
        if self._current_provider:
            await self._current_provider.cleanup()
```

---

## 📋 配置示例

```toml
# 决策层配置
[decision]
default_provider = "maicore"  # 可切换为 local_llm 或 rule_engine

[decision.providers.maicore]
host = "127.0.0.1"
port = 8000

[decision.providers.local_llm]
model = "gpt-4"
api_key = "your_openai_key"
base_url = "https://api.openai.com/v1"

[decision.providers.rule_engine]
rules_file = "rules.json"
```

---

## ✅ 关键优势

### 1. 可替换性
- ✅ 支持多种DecisionProvider实现
- ✅ 支持运行时切换
- ✅ 社区开发者可以开发自定义DecisionProvider

### 2. 解耦性
- ✅ AmaidesuCore不关心外部通信
- ✅ DecisionProvider自己管理通信
- ✅ 通过EventBus松耦合

### 3. 灵活性
- ✅ 可以混合多种决策方式
- ✅ 可以A/B测试不同DecisionProvider
- ✅ 支持本地LLM、规则引擎等

### 4. 可扩展性
- ✅ 社区开发者可以实现自定义DecisionProvider
- ✅ 支持新的通信协议
- ✅ 不限制决策算法

---

## 🔗 相关文档

- [7层架构设计](./layer_refactoring.md)
- [多Provider并发设计](./multi_provider.md)
- [AmaidesuCore重构设计](./core_refactoring.md)
