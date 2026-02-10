# DecisionProvider API

## 概述

`DecisionProvider` 是决策域（Decision Domain）的抽象基类，负责将 `NormalizedMessage` 转换为 `Intent`。

### 核心职责

- **输入**：接收来自 Input Domain 的 `NormalizedMessage`
- **处理**：根据业务逻辑生成决策意图
- **输出**：返回 `Intent` 对象，包含回复文本、情感、动作等

### 数据流

```
Input Domain           Decision Domain          Output Domain
─────────────────      ─────────────────        ────────────────
NormalizedMessage  →  DecisionProvider.decide()  →  Intent
                          ↓
                    DECISION_INTENT_GENERATED 事件
                          ↓
                    Output Domain 接收并处理
```

---

## 核心方法

### `async def decide(self, message: NormalizedMessage) -> Intent`

决策核心方法（抽象方法，必须实现）。

**参数**：
- `message` (`NormalizedMessage`): 标准化消息对象

**返回**：`Intent` - 决策意图对象

**异常**：
- `ValueError`: 如果输入消息无效
- `Exception`: 决策过程中的其他错误

**示例**：

```python
from src.modules.types.base.decision_provider import DecisionProvider
from src.modules.types.base.normalized_message import NormalizedMessage
from src.domains.decision.intent import Intent
from src.modules.types import EmotionType, ActionType, IntentAction
from src.modules.logging import get_logger

class SimpleDecisionProvider(DecisionProvider):
    """简单的决策 Provider 示例"""

    def __init__(self, config: dict):
        super().__init__(config)
        self.logger = get_logger("SimpleDecisionProvider")

    async def decide(self, message: NormalizedMessage) -> Intent:
        """根据消息生成简单的响应"""

        # 简单的回声逻辑
        response_text = f"你说：{message.text}"

        # 生成决策意图
        return Intent(
            original_text=message.text,
            response_text=response_text,
            emotion=EmotionType.NEUTRAL,
            actions=[
                IntentAction(
                    type=ActionType.BLINK,
                    params={},
                    priority=30
                )
            ],
            metadata={"provider": "simple"}
        )
```

---

## 生命周期方法

### `async def setup(self, event_bus, config, dependencies)`

设置 Provider，初始化资源并注册到 EventBus。

**参数**：
- `event_bus` (`EventBus`): EventBus 实例
- `config` (`dict`, 可选): Provider 配置（如果传入则覆盖构造时的配置）
- `dependencies` (`dict`, 可选): 依赖注入字典（如 `llm_service` 等）

**默认行为**：
1. 保存 `event_bus` 和 `config`
2. 保存 `dependencies`
3. 调用 `_setup_internal()` 执行子类特定的初始化
4. 设置 `is_setup = True`

**示例**：

```python
class MyDecisionProvider(DecisionProvider):
    def __init__(self, config: dict):
        super().__init__(config)
        self.api_client = None
        self.logger = get_logger("MyDecisionProvider")

    async def _setup_internal(self):
        """初始化 API 客户端"""
        import httpx
        self.api_client = httpx.AsyncClient(
            base_url=self.config.get("api_url", "https://api.example.com")
        )
        self.logger.info("API 客户端已初始化")

    async def decide(self, message: NormalizedMessage) -> Intent:
        response = await self.api_client.post(
            "/decide",
            json={"text": message.text}
        )
        data = response.json()

        return Intent(
            original_text=message.text,
            response_text=data["response"],
            emotion=EmotionType(data.get("emotion", "neutral")),
            actions=[],
            metadata={"provider": "my_api"}
        )
```

### `async def cleanup(self)`

清理资源，停止 Provider。

**默认行为**：
1. 调用 `_cleanup_internal()` 执行子类特定的清理
2. 设置 `is_setup = False`

**示例**：

```python
class MyDecisionProvider(DecisionProvider):
    async def _cleanup_internal(self):
        """清理 API 客户端"""
        if self.api_client:
            await self.api_client.aclose()
            self.logger.info("API 客户端已关闭")
```

### `async def _setup_internal(self)`（可选）

内部设置逻辑，子类可以重写。

**默认行为**：无操作

**用途**：
- 连接到外部服务
- 加载模型或配置
- 初始化内部状态

### `async def _cleanup_internal(self)`（可选）

内部清理逻辑，子类可以重写。

**默认行为**：无操作

**用途**：
- 关闭连接
- 释放资源
- 保存状态

---

## 属性

### `config: Dict[str, Any]`

Provider 配置（来自 `config.toml` 中的 `[providers.decision.xxx]` 配置）。

### `event_bus: Optional[EventBus]`

EventBus 实例（可选，用于事件通信）。

### `is_setup: bool`

是否已完成设置。

---

## Intent 结构

`Intent` 是 DecisionProvider 的输出类型，包含以下字段：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `id` | `str` | 否 | 唯一标识符（自动生成） |
| `original_text` | `str` | 是 | 原始输入文本 |
| `response_text` | `str` | 是 | AI 回复文本 |
| `emotion` | `EmotionType` | 否 | 情感类型（默认 `NEUTRAL`） |
| `actions` | `List[IntentAction]` | 否 | 动作列表（默认空） |
| `source_context` | `SourceContext` | 否 | 输入源上下文 |
| `metadata` | `Dict[str, Any]` | 否 | 元数据 |
| `timestamp` | `float` | 否 | 时间戳（自动生成） |

### IntentAction 结构

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `type` | `ActionType` | 是 | 动作类型 |
| `params` | `Dict[str, Any]` | 否 | 动作参数 |
| `priority` | `int` | 否 | 优先级（0-100，默认 50） |

### EmotionType 枚举

```python
class EmotionType(str, Enum):
    NEUTRAL = "neutral"
    HAPPY = "happy"
    SAD = "sad"
    ANGRY = "angry"
    SURPRISED = "surprised"
    LOVE = "love"
    SHY = "shy"
    EXCITED = "excited"
    CONFUSED = "confused"
    SCARED = "scared"
```

### ActionType 枚举

```python
class ActionType(str, Enum):
    EXPRESSION = "expression"      # 表情
    HOTKEY = "hotkey"              # 热键
    EMOJI = "emoji"                # emoji表情
    BLINK = "blink"                # 眨眼
    NOD = "nod"                    # 点头
    SHAKE = "shake"                # 摇头
    WAVE = "wave"                  # 挥手
    CLAP = "clap"                  # 鼓掌
    STICKER = "sticker"            # 贴图
    MOTION = "motion"              # 动作
    CUSTOM = "custom"              # 自定义
    GAME_ACTION = "game_action"    # 游戏动作
    NONE = "none"                  # 无动作
```

---

## 完整示例

### 示例 1：本地 LLM 决策 Provider

```python
"""
LocalLLMDecisionProvider - 使用本地 LLM 进行决策
"""

from typing import Dict, Any, Optional
from src/modules/types/base/decision_provider import DecisionProvider
from src/modules/types/base/normalized_message import NormalizedMessage
from src/domains/decision/intent import Intent, SourceContext
from src/modules/types import EmotionType, ActionType, IntentAction
from src/modules/logging import get_logger
from src.prompts import get_prompt_manager

class LocalLLMDecisionProvider(DecisionProvider):
    """本地 LLM 决策 Provider"""

    def __init__(self, config: dict):
        super().__init__(config)
        self.logger = get_logger("LocalLLMDecisionProvider")

        # 配置
        self.backend = config.get("backend", "llm")
        self.fallback_mode = config.get("fallback_mode", "simple")

        # LLM Service（通过依赖注入）
        self._llm_service = None

        # 统计
        self._total_requests = 0
        self._successful_requests = 0
        self._failed_requests = 0

    async def setup(
        self,
        event_bus,
        config: Dict[str, Any],
        dependencies: Optional[Dict[str, Any]] = None
    ):
        """设置 Provider"""
        await super().setup(event_bus, config, dependencies)

        # 从依赖注入中获取 LLM Service
        if dependencies and "llm_service" in dependencies:
            self._llm_service = dependencies["llm_service"]
            self.logger.info("LLM Service 已注入")
        else:
            self.logger.warning("LLM Service 未注入，决策功能将不可用")

    async def decide(self, message: NormalizedMessage) -> Intent:
        """使用 LLM 生成响应"""

        if self._llm_service is None:
            raise RuntimeError("LLM Service 未注入！")

        self._total_requests += 1

        # 构建提示词
        prompt = get_prompt_manager().render(
            "decision/local_llm",
            text=message.text
        )

        try:
            # 调用 LLM
            response = await self._llm_service.chat(
                prompt=prompt,
                client_type=self.backend
            )

            if not response.success:
                self._failed_requests += 1
                self.logger.error(f"LLM 调用失败: {response.error}")
                return self._handle_fallback(message)

            self._successful_requests += 1

            # 创建 Intent
            return Intent(
                original_text=message.text,
                response_text=response.content,
                emotion=EmotionType.NEUTRAL,
                actions=[
                    IntentAction(
                        type=ActionType.BLINK,
                        params={},
                        priority=30
                    )
                ],
                source_context=SourceContext(
                    source=message.source,
                    data_type=message.data_type,
                    user_id=message.user_id,
                    importance=message.importance
                ),
                metadata={"provider": "local_llm"}
            )

        except Exception as e:
            self._failed_requests += 1
            self.logger.error(f"LLM 调用异常: {e}", exc_info=True)
            return self._handle_fallback(message)

    def _handle_fallback(self, message: NormalizedMessage) -> Intent:
        """降级处理"""
        if self.fallback_mode == "simple":
            return Intent(
                original_text=message.text,
                response_text=message.text,
                emotion=EmotionType.NEUTRAL,
                actions=[],
                metadata={"provider": "local_llm", "fallback": True}
            )
        elif self.fallback_mode == "echo":
            return Intent(
                original_text=message.text,
                response_text=f"你说：{message.text}",
                emotion=EmotionType.NEUTRAL,
                actions=[],
                metadata={"provider": "local_llm", "fallback": True}
            )
        else:
            raise RuntimeError("LLM 请求失败，且未配置降级模式")

    async def cleanup(self):
        """清理资源"""
        success_rate = (
            self._successful_requests / self._total_requests * 100
            if self._total_requests > 0
            else 0
        )
        self.logger.info(
            f"统计: 总请求={self._total_requests}, "
            f"成功={self._successful_requests}, "
            f"失败={self._failed_requests}, "
            f"成功率={success_rate:.1f}%"
        )
```

### 示例 2：规则引擎决策 Provider

```python
"""
RuleEngineDecisionProvider - 基于规则引擎的决策Provider
"""

from typing import Dict, Any, List
from src/modules/types/base/decision_provider import DecisionProvider
from src/modules/types/base/normalized_message import NormalizedMessage
from src/domains/decision/intent import Intent, SourceContext
from src/modules/types import EmotionType, ActionType, IntentAction
from src/modules/logging import get_logger

class RuleEngineDecisionProvider(DecisionProvider):
    """规则引擎决策 Provider"""

    def __init__(self, config: dict):
        super().__init__(config)
        self.logger = get_logger("RuleEngineDecisionProvider")

        # 加载规则
        self.rules = config.get("rules", [])
        self.logger.info(f"加载了 {len(self.rules)} 条规则")

    async def decide(self, message: NormalizedMessage) -> Intent:
        """根据规则匹配生成响应"""

        # 默认响应
        default_response = "我听到了"

        # 遍历规则
        for rule in self.rules:
            if self._match_rule(message, rule):
                self.logger.info(f"匹配规则: {rule['name']}")

                return Intent(
                    original_text=message.text,
                    response_text=rule.get("response", default_response),
                    emotion=EmotionType(rule.get("emotion", "neutral")),
                    actions=[
                        IntentAction(
                            type=ActionType(action["type"]),
                            params=action.get("params", {}),
                            priority=action.get("priority", 50)
                        )
                        for action in rule.get("actions", [])
                    ],
                    source_context=SourceContext(
                        source=message.source,
                        data_type=message.data_type,
                        user_id=message.user_id,
                        importance=message.importance
                    ),
                    metadata={"provider": "rule_engine", "rule": rule["name"]}
                )

        # 没有匹配规则
        return Intent(
            original_text=message.text,
            response_text=default_response,
            emotion=EmotionType.NEUTRAL,
            actions=[],
            source_context=SourceContext(
                source=message.source,
                data_type=message.data_type,
                user_id=message.user_id,
                importance=message.importance
            ),
            metadata={"provider": "rule_engine", "matched": False}
        )

    def _match_rule(self, message: NormalizedMessage, rule: Dict[str, Any]) -> bool:
        """检查消息是否匹配规则"""
        conditions = rule.get("conditions", {})

        # 检查数据类型
        if "data_type" in conditions:
            if message.data_type != conditions["data_type"]:
                return False

        # 检查重要性
        if "min_importance" in conditions:
            if message.importance < conditions["min_importance"]:
                return False

        # 检查关键词
        if "keywords" in conditions:
            text = message.text.lower()
            if not all(kw.lower() in text for kw in conditions["keywords"]):
                return False

        # 检查来源
        if "sources" in conditions:
            if message.source not in conditions["sources"]:
                return False

        return True
```

---

## 导入路径

```python
# 基类
from src.modules.types.base.decision_provider import DecisionProvider

# 数据类型
from src.modules.types.base.normalized_message import NormalizedMessage
from src.domains.decision.intent import Intent, SourceContext

# 枚举类型
from src.modules.types import EmotionType, ActionType, IntentAction

# 事件
from src/modules/events/names import CoreEvents

# 日志
from src.modules.logging import get_logger

# 提示词管理
from src/modules/prompts import get_prompt_manager
```

---

## 事件订阅

DecisionProvider 通常订阅以下事件：

| 事件名 | 常量 | 数据类型 | 说明 |
|--------|------|---------|------|
| `normalization.message_ready` | `CoreEvents.NORMALIZATION_MESSAGE_READY` | `NormalizedMessage` | Input Domain 生成标准化消息 |

DecisionProvider 发布以下事件：

| 事件名 | 常量 | 数据类型 | 说明 |
|--------|------|---------|------|
| `decision.intent_generated` | `CoreEvents.DECISION_INTENT_GENERATED` | `Intent` | 生成决策意图 |

**注意**：DecisionProvider 通常不需要手动订阅事件，由 `DecisionCoordinator` 统一管理。

---

## 配置示例

```toml
[providers.decision]
# 激活的决策 Provider
active_provider = "local_llm"

# LocalLLM 配置
[providers.decision.local_llm]
type = "local_llm"
backend = "llm"           # 使用的 LLM 后端 (llm, llm_fast, vlm)
fallback_mode = "simple"  # 降级模式 (simple, echo, error)

# RuleEngine 配置
[providers.decision.rule_engine]
type = "rule_engine"

[[providers.decision.rule_engine.rules]]
name = "打招呼"
response = "你好呀！"
emotion = "happy"

[[providers.decision.rule_engine.rules.actions]]
type = "expression"
params = { name = "smile" }
priority = 60

[providers.decision.rule_engine.rules.conditions]
keywords = ["你好", "hello", "hi"]
```

---

## 最佳实践

### 1. 错误处理

```python
async def decide(self, message: NormalizedMessage) -> Intent:
    try:
        # 决策逻辑
        result = await self._make_decision(message)
        return result
    except Exception as e:
        self.logger.error(f"决策失败: {e}", exc_info=True)
        # 返回降级 Intent
        return self._create_fallback_intent(message)
```

### 2. 使用依赖注入

```python
async def setup(self, event_bus, config, dependencies):
    await super().setup(event_bus, config, dependencies)

    # 从依赖注入中获取服务
    if dependencies and "llm_service" in dependencies:
        self._llm_service = dependencies["llm_service"]
```

### 3. 统计信息

```python
def __init__(self, config: dict):
    super().__init__(config)
    self._total_requests = 0
    self._successful_requests = 0
    self._failed_requests = 0

async def cleanup(self):
    # 输出统计信息
    self.logger.info(f"总请求: {self._total_requests}")
    self.logger.info(f"成功: {self._successful_requests}")
    self.logger.info(f"失败: {self._failed_requests}")
```

### 4. 使用提示词模板

```python
async def decide(self, message: NormalizedMessage) -> Intent:
    # 使用 PromptManager 渲染模板
    prompt = get_prompt_manager().render(
        "decision/my_provider",
        text=message.text,
        user_name=message.user_id
    )

    response = await self._llm_service.chat(prompt=prompt)
    # ...
```

### 5. 降级策略

```python
def _handle_fallback(self, message: NormalizedMessage) -> Intent:
    """降级处理"""
    fallback_mode = self.config.get("fallback_mode", "simple")

    if fallback_mode == "simple":
        # 返回原始文本
        return Intent(
            original_text=message.text,
            response_text=message.text,
            emotion=EmotionType.NEUTRAL,
            actions=[]
        )
    elif fallback_mode == "echo":
        # 回声模式
        return Intent(
            original_text=message.text,
            response_text=f"你说：{message.text}",
            emotion=EmotionType.NEUTRAL,
            actions=[]
        )
    else:
        # 抛出异常
        raise RuntimeError("决策失败且未配置降级模式")
```

---

## 相关文档

- [Provider 开发指南](../development/provider.md)
- [3域架构](../architecture/overview.md)
- [数据流规则](../architecture/data-flow.md)
- [事件系统](../architecture/event-system.md)
- [InputProvider API](./input_provider.md)
- [OutputProvider API](./output_provider.md)
