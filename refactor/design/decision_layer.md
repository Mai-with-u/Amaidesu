# 决策层设计

## 🎯 核心目标

实现可替换的决策系统，支持多种决策方式：
1. **MaiCoreDecisionProvider**：默认实现，使用MaiCore进行决策（异步+LLM意图解析）
2. **LocalLLMDecisionProvider**：可选实现，使用本地LLM进行决策
3. **RuleEngineDecisionProvider**：可选实现，使用规则引擎进行决策

---

## 📊 决策层位置（5层架构）

```
Layer 1-2: Input（NormalizedMessage）
    ↓ normalization.message_ready
Layer 3: Decision（DecisionProvider）⭐ 可替换、可扩展
    ├─ MaiCoreDecisionProvider (默认，WebSocket + LLM意图解析)
    ├─ LocalLLMDecisionProvider (可选，直接LLM)
    └─ RuleEngineDecisionProvider (可选，规则引擎)
    ↓ Intent (decision.intent_generated)
Layer 4-5: Parameters+Rendering（RenderParameters → 输出）
```

**5层架构中的关键变化：**
- **移除了 UnderstandingLayer**：Intent 解析由 DecisionProvider 内部处理
- **移除了 Pre-Pipeline 和 Post-Pipeline**：TextPipeline 集成到 InputLayer (Layer 1-2)
- **简化数据流**：NormalizedMessage → Intent → RenderParameters

---

## 🔗 核心接口

### DecisionProvider接口（新）

```python
from typing import Protocol
from src.core.event_bus import EventBus
from src.data_types.normalized_message import NormalizedMessage
from src.layers.decision.intent import Intent

class DecisionProvider(Protocol):
    """决策Provider接口

    关键变更：
    - 输入：NormalizedMessage（结构化消息）
    - 输出：Intent（意图，而不是MessageBase）
    - 异步返回：符合AI VTuber的异步特性
    """

    async def setup(self, event_bus: EventBus, config: dict):
        """
        初始化决策Provider

        Args:
            event_bus: 事件总线实例
            config: Provider配置
        """
        ...

    async def decide(self, message: NormalizedMessage) -> Intent:
        """
        根据NormalizedMessage做出决策（异步）

        Args:
            message: 标准化消息

        Returns:
            Intent: 决策意图
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

**关键变更**：
- ✅ 输入从 `CanonicalMessage` 改为 `NormalizedMessage`
- ✅ 输出从 `MessageBase` 改为 `Intent`
- ✅ 支持异步返回（符合AI VTuber特性）

---

## 🎨 MaiCoreDecisionProvider实现（新架构）

### 设计理念

**挑战**：MaiCore是异步的，但`decide()`需要返回Intent

**解决方案**：
1. 发送消息到MaiCore
2. 使用`asyncio.Future`等待响应
3. 收到MessageBase后，使用LLM解析为Intent
4. 返回Intent

### 完整实现

```python
import asyncio
from typing import Dict, Any, Optional
from maim_message import MessageBase

from src.core.base.decision_provider import DecisionProvider
from src.data_types.normalized_message import NormalizedMessage
from src.layers.decision.intent import Intent
from src.layers.decision.intent_parser import IntentParser
from src.core.providers.websocket_connector import WebSocketConnector
from src.core.providers.router_adapter import RouterAdapter
from src.utils.logger import get_logger

class MaiCoreDecisionProvider:
    """MaiCore决策Provider（异步 + LLM意图解析）"""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = get_logger("MaiCoreDecisionProvider")

        # WebSocket配置
        self.host = config.get("host", "localhost")
        self.port = config.get("port", 8000)
        self.platform = config.get("platform", "amaidesu")

        # 意图解析器（小LLM）
        self._intent_parser: Optional[IntentParser] = None

        # 请求-响应映射（message_id → Future）
        self._pending_requests: Dict[str, asyncio.Future] = {}

        # WebSocket连接
        self._ws_connector: Optional[WebSocketConnector] = None
        self._router_adapter: Optional[RouterAdapter] = None

    async def setup(self, event_bus: EventBus, config: Dict[str, Any] = None):
        """设置Provider"""
        # 初始化意图解析器
        llm_config = self.config.get("llm", {})
        self._intent_parser = IntentParser(llm_config)
        await self._intent_parser.initialize()

        # 初始化WebSocket连接
        await self._setup_websocket()

    async def decide(self, message: NormalizedMessage) -> Intent:
        """
        进行决策（异步）

        Args:
            message: 标准化消息

        Returns:
            Intent: 决策意图
        """
        # 1. 转换为MessageBase
        message_base = message.to_message_base()
        message_id = message_base.message_info.message_id

        # 2. 创建Future等待响应
        future = asyncio.Future()
        self._pending_requests[message_id] = future

        # 3. 发送到MaiCore
        await self._router_adapter.send(message_base)

        # 4. 异步等待响应（超时30秒）
        try:
            response_message_base = await asyncio.wait_for(future, timeout=30.0)
        except asyncio.TimeoutError:
            del self._pending_requests[message_id]
            self.logger.error(f"MaiCore响应超时: {message_id}")
            # 返回默认Intent
            return Intent(
                original_text=message.text,
                response_text="(MaiCore响应超时)",
                emotion=EmotionType.NEUTRAL,
                actions=[],
                metadata={"error": "timeout"}
            )

        # 5. 使用LLM解析意图
        intent = await self._intent_parser.parse(response_message_base)
        intent.original_text = message.text
        intent.metadata["original_message_id"] = message_id

        return intent

    def _handle_maicore_message(self, message_data: Dict[str, Any]):
        """处理MaiCore的异步响应"""
        message = MessageBase.from_dict(message_data)
        message_id = message.message_info.message_id

        # 查找对应的Future
        future = self._pending_requests.get(message_id)
        if future and not future.done():
            future.set_result(message)
            del self._pending_requests[message_id]
        else:
            self.logger.warning(f"未找到对应的请求或已超时: {message_id}")

    async def cleanup(self):
        """清理资源"""
        if self._ws_connector:
            await self._ws_connector.disconnect()
        if self._intent_parser:
            await self._intent_parser.cleanup()
```

---

## 🤖 LLM意图解析

### 为什么需要LLM解析？

**问题**：MaiCore返回的是MessageBase（文本格式）
```
"你好呀！[开心] [微笑] 谢谢你的礼物！"
```

**需求**：提取结构化的Intent
```python
Intent(
    response_text="你好呀！谢谢你的礼物！",
    emotion=HAPPY,
    actions=[SMILE]
)
```

**挑战**：
- ❌ 正则表达式和关键词匹配：规则死板，易误判
- ❌ MaiCore不适合直接输出JSON（群聊机器人，各种过滤）
- ✅ **LLM解析**：智能、灵活、可扩展

### IntentParser设计

```python
from typing import Optional
from maim_message import MessageBase

class IntentParser:
    """使用小LLM解析意图"""

    def __init__(self, llm_config: dict):
        self.llm_service = LLMService(llm_config)
        self.system_prompt = """你是一个AI VTuber意图解析器。

任务：分析AI的回复，提取：
1. response_text: 清理后的回复文本（移除所有标记）
2. emotion: 情感类型（NEUTRAL/HAPPY/SAD/ANGRY/SURPRISED/LOVE）
3. actions: 表现动作列表（从以下选择：SMILE, BLINK, NOD, SHAKE, WAVE, CLAP, NONE）

示例：
输入: "你好呀！[开心] [微笑] 谢谢！"
输出:
{
  "response_text": "你好呀！谢谢！",
  "emotion": "HAPPY",
  "actions": ["SMILE"]
}

输入: "哈哈，太有趣了！😆"
输出:
{
  "response_text": "哈哈，太有趣了！",
  "emotion": "HAPPY",
  "actions": []
}

输入: "哦...是吗。"
输出:
{
  "response_text": "哦...是吗。",
  "emotion": "NEUTRAL",
  "actions": []
}

只返回JSON，不要其他内容。"""

    async def parse(self, message: MessageBase) -> Intent:
        """解析MessageBase → Intent"""
        # 1. 提取文本
        text = self._extract_text(message)

        # 2. 调用小LLM解析
        response = await self.llm_service.generate(
            prompt=text,
            system_prompt=self.system_prompt,
            temperature=0.1,  # 低温度，保证稳定
            model="haiku"  # 或其他小模型
        )

        # 3. 解析JSON
        import json
        try:
            result = json.loads(response)
            return Intent(
                response_text=result["response_text"],
                emotion=EmotionType[result["emotion"]],
                actions=[
                    IntentAction(
                        type=ActionType.EXPRESSION,
                        params={"expression": a}
                    ) for a in result.get("actions", [])
                ],
                metadata={"source": "maicore", "parser": "llm"}
            )
        except Exception as e:
            self.logger.error(f"LLM解析失败: {e}, 原始响应: {response}")
            # 降级：返回默认Intent
            return Intent(
                response_text=text,
                emotion=EmotionType.NEUTRAL,
                actions=[],
                metadata={"source": "maicore", "parser": "fallback"}
            )

    def _extract_text(self, message: MessageBase) -> str:
        """提取文本"""
        if not message.message_segment:
            return ""

        if hasattr(message.message_segment, "data"):
            data = message.message_segment.data
            if isinstance(data, str):
                return data
            elif isinstance(data, list):
                # 处理seglist
                text_parts = []
                for seg in data:
                    if hasattr(seg, "data") and isinstance(seg.data, str):
                        text_parts.append(seg.data)
                return " ".join(text_parts)
        return ""
```

### 成本考虑

**小LLM成本**（以Claude Haiku为例）：
- 输入：~100 tokens (MaiCore回复 + prompt)
- 输出：~50 tokens (JSON响应)
- 成本：~$0.00025 / 次

**假设每分钟处理10条弹幕**：
- 每小时：600次
- 每天成本：600 * 24 * $0.00025 = **$3.6/天**

**优化方案**：
- 使用更小的模型（如Qwen2.5-3B本地部署）
- 简单情况降级到规则匹配
- 缓存相似回复的解析结果

### LLM解析 vs 规则匹配对比

| 维度 | 规则匹配 | LLM解析 |
|------|---------|---------|
| **准确性** | ❌ 规则死板，易误判 | ✅ 上下文理解，更准确 |
| **灵活性** | ❌ 新格式需要改代码 | ✅ 自动适应各种格式 |
| **维护成本** | ❌ 需要维护规则库 | ✅ 只需调整prompt |
| **扩展性** | ❌ 复杂模式难以处理 | ✅ 可处理复杂语义 |
| **成本** | ✅ 免费 | ⚠️ 小LLM成本很低 |
| **速度** | ✅ 极快 | ⚠️ ~100ms延迟 |

---

## 🎨 LocalLLMDecisionProvider实现

### 设计理念

直接使用LLM生成决策，返回Intent（不需要二次解析）

```python
class LocalLLMDecisionProvider:
    """本地LLM决策Provider（直接返回Intent）"""

    def __init__(self, config: dict):
        self.config = config
        self.llm_service = LLMService(config)
        self.logger = get_logger("LocalLLMDecisionProvider")

    async def decide(self, message: NormalizedMessage) -> Intent:
        """
        决策接口（直接返回Intent）

        Args:
            message: 标准化消息

        Returns:
            Intent: 决策意图
        """
        # 1. 构建prompt
        prompt = self._build_prompt(message)

        # 2. 调用LLM
        response = await self.llm_service.generate(
            prompt=prompt,
            system_prompt="你是一个AI VTuber助手...",
            temperature=0.8
        )

        # 3. 返回Intent
        return Intent(
            original_text=message.text,
            response_text=response,
            emotion=self._detect_emotion(response),
            actions=[],
            metadata={"source": "local_llm"}
        )

    def _build_prompt(self, message: NormalizedMessage) -> str:
        """构建prompt"""
        # 基于结构化内容构建更智能的prompt
        if message.content.type == "gift":
            return f"用户送了礼物：{message.content.get_display_text()}，请回复感谢语。"
        elif message.content.type == "text":
            return f"用户说：{message.text}，请回复。"
        else:
            return f"用户输入：{message.text}，请回复。"
```

---

## 🎨 RuleEngineDecisionProvider实现

```python
class RuleEngineDecisionProvider:
    """规则引擎决策Provider"""

    def __init__(self, config: dict):
        self.config = config
        self.rules_file = config.get("rules_file", "rules.json")
        self.logger = get_logger("RuleEngineDecisionProvider")
        self._rules = []

    async def decide(self, message: NormalizedMessage) -> Intent:
        """
        决策接口（基于规则匹配）

        Args:
            message: 标准化消息

        Returns:
            Intent: 决策意图
        """
        # 匹配规则
        response_text = self._match_rules(message)

        return Intent(
            original_text=message.text,
            response_text=response_text,
            emotion=self._detect_emotion(response_text),
            actions=[],
            metadata={"source": "rule_engine"}
        )

    def _match_rules(self, message: NormalizedMessage) -> str:
        """匹配规则"""
        # 基于content类型匹配
        if message.content.type == "gift":
            return f"谢谢{message.content.user}送的{message.content.gift_name}！"
        elif message.content.type == "text":
            return self._generate_text_response(message.text)
        else:
            return "感谢支持！"

    async def _load_rules(self):
        """加载规则文件"""
        # 从JSON文件加载规则
        pass
```

---

## 📊 DecisionManager设计

```python
from typing import Dict, Any, Optional
from src.core.event_bus import EventBus
from src.layers.normalization.normalized_message import NormalizedMessage
from src.layers.decision.intent import Intent

class DecisionManager:
    """决策管理器 - 管理决策Provider"""

    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self.logger = get_logger("DecisionManager")
        self._factory = DecisionProviderFactory()
        self._current_provider: Optional[DecisionProvider] = None
        self._provider_name: Optional[str] = None

    async def setup(self, provider_name: str, config: dict):
        """设置决策Provider"""
        provider_class = self._factory.get_provider(provider_name)
        if not provider_class:
            raise ValueError(f"DecisionProvider not found: {provider_name}")

        if self._current_provider:
            await self._current_provider.cleanup()

        self._current_provider = provider_class(config)
        self._provider_name = provider_name
        await self._current_provider.setup(self.event_bus, config)

    async def decide(self, message: NormalizedMessage) -> Intent:
        """进行决策（异步）"""
        if not self._current_provider:
            raise RuntimeError("No decision provider configured")
        return await self._current_provider.decide(message)

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
platform = "amaidesu"

# LLM意图解析配置
[decision.providers.maicore.intent_parser]
model = "claude-3-5-haiku-20241022"  # 或 "qwen2.5-3b"
temperature = 0.1
timeout_seconds = 5
enable_fallback = true  # LLM失败时降级到规则匹配

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

### 2. 异步特性
- ✅ 符合AI VTuber的异步处理特性
- ✅ MaiCoreDecisionProvider支持异步返回
- ✅ 使用Future机制管理请求-响应

### 3. LLM意图解析
- ✅ 比规则更智能、更灵活
- ✅ 适应各种文本格式
- ✅ 成本可控（小LLM）

### 4. 解耦性
- ✅ AmaidesuCore不关心外部通信
- ✅ DecisionProvider自己管理通信和解析
- ✅ 通过EventBus松耦合

### 5. 灵活性
- ✅ 可以混合多种决策方式
- ✅ 可以A/B测试不同DecisionProvider
- ✅ 支持本地LLM、规则引擎等

---

## 🔗 相关文档

- [5层架构设计](./layer_refactoring.md)
- [多Provider并发设计](./multi_provider.md)
- [AmaidesuCore重构设计](./core_refactoring.md)
- [LLM服务设计](./llm_service.md)
