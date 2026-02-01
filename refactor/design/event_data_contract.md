# 事件数据契约系统设计

## 🎯 核心目标

构建类型安全、可验证、社区友好的事件数据契约系统，解决当前 EventBus 数据传递中的类型不安全和契约不明确问题。

---

## 📊 问题分析

### 当前实现的问题

| 问题 | 影响 | 严重程度 |
|------|------|----------|
| **无运行时验证** | 错误数据可能传播到下游，难以定位问题源 | 高 |
| **类型不安全** | `data: Any` 类型注解，IDE 无法提供有效提示 | 中 |
| **魔法字符串** | 事件名硬编码为字符串，易拼错且无自动补全 | 中 |
| **契约不明确** | 依赖文档约定，代码与文档易不同步 | 高 |
| **测试困难** | 难以对事件数据格式进行自动化测试 | 中 |

### 当前代码示例

```python
# 当前方式：无验证、无类型提示
await event_bus.emit(
    "perception.raw_data.generated",  # 魔法字符串
    {"data": raw_data, "source": provider_name},  # 字典，无类型
    source=provider_name
)

# 处理器：需要手动检查类型
async def handler(event_name: str, event_data: Any, source: str):
    data = event_data.get("data")  # 可能不存在
    if isinstance(data, RawData):  # 手动类型检查
        # ...
```

---

## 🚀 解决方案概述

### 核心设计：Pydantic + 开放式注册表

结合 **Pydantic Model** 的验证能力和 **开放式注册表** 的扩展性：

```
┌─────────────────────────────────────────────────────────┐
│              EventRegistry（事件注册表）                  │
├─────────────────────────────────────────────────────────┤
│  Core Events（核心事件，只读，启动时注册）                │
│  ├─ perception.raw_data.generated                       │
│  ├─ normalization.text.ready                            │
│  ├─ decision.response_generated                         │
│  └─ expression.parameters_generated                     │
├─────────────────────────────────────────────────────────┤
│  Plugin Events（插件事件，开放，插件setup时注册）         │
│  ├─ plugin.bili_danmaku.gift_received                  │
│  ├─ plugin.minecraft.player_joined                     │
│  └─ plugin.{plugin_name}.{event_name}                  │
├─────────────────────────────────────────────────────────┤
│  Unregistered Events（未注册事件，允许，仅警告）          │
│  └─ 任何未注册的事件名（向后兼容）                        │
└─────────────────────────────────────────────────────────┘
```

### 关键设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| **数据模型** | Pydantic BaseModel | 运行时验证、自动序列化、IDE 友好 |
| **注册表类型** | 开放式（支持插件注册） | 保持社区扩展性 |
| **验证策略** | 可配置（debug 模式强验证） | 平衡性能和安全性 |
| **命名空间** | 分层约定（core/plugin） | 避免命名冲突 |
| **向后兼容** | 渐进式迁移 | 降低迁移风险 |

---

## 🏗️ 详细设计

### 1. 事件注册表（EventRegistry）

```python
# src/core/events/registry.py
from typing import Dict, Type, Optional, List
from pydantic import BaseModel
from src.utils.logger import get_logger


class EventRegistry:
    """
    事件类型注册表
    
    支持两种事件类型：
    - 核心事件：系统内部使用，只读
    - 插件事件：社区插件使用，开放注册
    
    验证策略：
    - 核心事件：强制验证（debug 模式）
    - 插件事件：可选验证
    - 未注册事件：允许发布，仅警告
    """
    
    # 核心事件（只读）
    _core_events: Dict[str, Type[BaseModel]] = {}
    # 插件事件（开放）
    _plugin_events: Dict[str, Type[BaseModel]] = {}
    
    _logger = get_logger("EventRegistry")
    
    # ==================== 核心事件 API ====================
    
    @classmethod
    def register_core_event(cls, event_name: str, model: Type[BaseModel]) -> None:
        """
        注册核心事件（仅内部使用）
        
        Args:
            event_name: 事件名称（如 "perception.raw_data.generated"）
            model: Pydantic Model 类型
            
        Raises:
            ValueError: 事件名不符合核心事件命名规范
        """
        # 验证命名规范
        valid_prefixes = ("perception.", "normalization.", "decision.", 
                         "understanding.", "expression.", "render.", "core.")
        if not any(event_name.startswith(prefix) for prefix in valid_prefixes):
            raise ValueError(
                f"核心事件名必须以 {valid_prefixes} 之一开头，"
                f"收到: {event_name}"
            )
        
        if event_name in cls._core_events:
            cls._logger.warning(f"核心事件已存在，将覆盖: {event_name}")
        
        cls._core_events[event_name] = model
        cls._logger.debug(f"注册核心事件: {event_name} -> {model.__name__}")
    
    # ==================== 插件事件 API ====================
    
    @classmethod
    def register_plugin_event(cls, event_name: str, model: Type[BaseModel]) -> None:
        """
        注册插件事件（对社区开放）
        
        命名约定：plugin.{plugin_name}.{event_name}
        
        Args:
            event_name: 事件名称（必须以 "plugin." 开头）
            model: Pydantic Model 类型
            
        Raises:
            ValueError: 事件名不符合插件事件命名规范
        """
        if not event_name.startswith("plugin."):
            raise ValueError(
                f"插件事件名必须以 'plugin.' 开头，"
                f"收到: {event_name}。"
                f"正确格式: plugin.{{plugin_name}}.{{event_name}}"
            )
        
        # 解析插件名
        parts = event_name.split(".")
        if len(parts) < 3:
            raise ValueError(
                f"插件事件名格式错误: {event_name}。"
                f"正确格式: plugin.{{plugin_name}}.{{event_name}}"
            )
        
        if event_name in cls._plugin_events:
            cls._logger.warning(f"插件事件已存在，将覆盖: {event_name}")
        
        cls._plugin_events[event_name] = model
        cls._logger.debug(f"注册插件事件: {event_name} -> {model.__name__}")
    
    # ==================== 查询 API ====================
    
    @classmethod
    def get(cls, event_name: str) -> Optional[Type[BaseModel]]:
        """
        获取事件的 Model 类型（核心事件优先）
        
        Args:
            event_name: 事件名称
            
        Returns:
            Pydantic Model 类型，未注册返回 None
        """
        return cls._core_events.get(event_name) or cls._plugin_events.get(event_name)
    
    @classmethod
    def is_registered(cls, event_name: str) -> bool:
        """检查事件是否已注册"""
        return event_name in cls._core_events or event_name in cls._plugin_events
    
    @classmethod
    def is_core_event(cls, event_name: str) -> bool:
        """检查是否为核心事件"""
        return event_name in cls._core_events
    
    @classmethod
    def is_plugin_event(cls, event_name: str) -> bool:
        """检查是否为插件事件"""
        return event_name in cls._plugin_events
    
    # ==================== 列表 API ====================
    
    @classmethod
    def list_core_events(cls) -> Dict[str, Type[BaseModel]]:
        """列出所有核心事件"""
        return cls._core_events.copy()
    
    @classmethod
    def list_plugin_events(cls) -> Dict[str, Type[BaseModel]]:
        """列出所有插件事件"""
        return cls._plugin_events.copy()
    
    @classmethod
    def list_all_events(cls) -> Dict[str, Type[BaseModel]]:
        """列出所有注册的事件"""
        return {**cls._core_events, **cls._plugin_events}
    
    @classmethod
    def list_plugin_events_by_plugin(cls, plugin_name: str) -> Dict[str, Type[BaseModel]]:
        """
        列出指定插件的所有事件
        
        Args:
            plugin_name: 插件名称
            
        Returns:
            该插件的所有事件
        """
        prefix = f"plugin.{plugin_name}."
        return {
            name: model 
            for name, model in cls._plugin_events.items() 
            if name.startswith(prefix)
        }
    
    # ==================== 清理 API ====================
    
    @classmethod
    def unregister_plugin_events(cls, plugin_name: str) -> int:
        """
        注销指定插件的所有事件（插件 cleanup 时调用）
        
        Args:
            plugin_name: 插件名称
            
        Returns:
            注销的事件数量
        """
        prefix = f"plugin.{plugin_name}."
        to_remove = [name for name in cls._plugin_events if name.startswith(prefix)]
        
        for name in to_remove:
            del cls._plugin_events[name]
            cls._logger.debug(f"注销插件事件: {name}")
        
        return len(to_remove)
    
    @classmethod
    def clear_plugin_events(cls) -> None:
        """清空所有插件事件（仅用于测试）"""
        cls._plugin_events.clear()
        cls._logger.info("已清空所有插件事件")
    
    @classmethod
    def clear_all(cls) -> None:
        """清空所有事件（仅用于测试）"""
        cls._core_events.clear()
        cls._plugin_events.clear()
        cls._logger.info("已清空所有事件")
```

### 2. 事件名称常量（EventNames）

```python
# src/core/events/names.py
"""
事件名称常量定义

使用常量替代魔法字符串，提供 IDE 自动补全和重构支持。
"""


class CoreEvents:
    """核心事件名称常量"""
    
    # Layer 1: 输入感知
    PERCEPTION_RAW_DATA_GENERATED = "perception.raw_data.generated"
    
    # Layer 2: 输入标准化
    NORMALIZATION_TEXT_READY = "normalization.text.ready"
    
    # Layer 4: 决策层
    DECISION_REQUEST = "decision.request"
    DECISION_RESPONSE_GENERATED = "decision.response_generated"
    DECISION_PROVIDER_CONNECTED = "decision.provider.connected"
    DECISION_PROVIDER_DISCONNECTED = "decision.provider.disconnected"
    
    # Layer 5: 表现理解
    UNDERSTANDING_INTENT_GENERATED = "understanding.intent_generated"
    
    # Layer 6: 表现生成
    EXPRESSION_PARAMETERS_GENERATED = "expression.parameters_generated"
    
    # Layer 7: 渲染呈现
    RENDER_COMPLETED = "render.completed"
    RENDER_FAILED = "render.failed"
    
    # 系统事件
    CORE_STARTUP = "core.startup"
    CORE_SHUTDOWN = "core.shutdown"
    CORE_ERROR = "core.error"


class PluginEventPrefix:
    """插件事件前缀工具"""
    
    @staticmethod
    def create(plugin_name: str, event_name: str) -> str:
        """
        创建插件事件名称
        
        Args:
            plugin_name: 插件名称（snake_case）
            event_name: 事件名称
            
        Returns:
            完整的插件事件名称
            
        Example:
            >>> PluginEventPrefix.create("bili_danmaku", "gift_received")
            "plugin.bili_danmaku.gift_received"
        """
        return f"plugin.{plugin_name}.{event_name}"
```

### 3. 核心事件模型（Pydantic Models）

```python
# src/core/events/models.py
"""
核心事件数据模型

使用 Pydantic BaseModel 定义，提供：
- 运行时类型验证
- 自动序列化/反序列化
- IDE 类型提示
- 自动文档生成
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator
import time


# ==================== Layer 1: 输入感知 ====================

class RawDataEvent(BaseModel):
    """
    原始数据事件
    
    事件名：perception.raw_data.generated
    发布者：InputProvider
    订阅者：InputLayer（Layer 2）
    """
    
    content: Any = Field(..., description="原始数据内容（bytes, str, dict等）")
    source: str = Field(..., min_length=1, description="数据源标识符")
    data_type: str = Field(..., description="数据类型")
    timestamp: float = Field(default_factory=time.time, description="Unix时间戳（秒）")
    preserve_original: bool = Field(default=False, description="是否保留原始数据")
    original_data: Optional[Any] = Field(default=None, description="原始数据（如果已处理）")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="元数据")
    
    # 支持的数据类型
    SUPPORTED_DATA_TYPES = ["text", "audio", "image", "json", "event", "binary"]
    
    @field_validator("data_type")
    @classmethod
    def validate_data_type(cls, v: str) -> str:
        """验证数据类型"""
        if v not in cls.SUPPORTED_DATA_TYPES:
            # 警告但不阻断（允许扩展）
            import warnings
            warnings.warn(f"非标准数据类型: {v}，标准类型: {cls.SUPPORTED_DATA_TYPES}")
        return v
    
    class Config:
        """Pydantic 配置"""
        json_schema_extra = {
            "example": {
                "content": "用户输入的文本",
                "source": "console_input",
                "data_type": "text",
                "timestamp": 1706745600.0,
                "metadata": {"user_id": "12345"}
            }
        }


# ==================== Layer 2: 输入标准化 ====================

class NormalizedTextEvent(BaseModel):
    """
    标准化文本事件
    
    事件名：normalization.text.ready
    发布者：InputLayer
    订阅者：CanonicalMessageBuilder（Layer 3）
    """
    
    text: str = Field(..., min_length=1, description="标准化后的文本")
    source: str = Field(..., min_length=1, description="数据源")
    data_type: str = Field(default="text", description="数据类型")
    timestamp: float = Field(default_factory=time.time, description="时间戳")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="元数据")
    
    class Config:
        json_schema_extra = {
            "example": {
                "text": "你好，今天天气怎么样？",
                "source": "bili_danmaku",
                "data_type": "text",
                "timestamp": 1706745600.0,
                "metadata": {"user": "观众A", "room_id": "123456"}
            }
        }


# ==================== Layer 4: 决策层 ====================

class DecisionRequestEvent(BaseModel):
    """
    决策请求事件
    
    事件名：decision.request
    发布者：CanonicalMessageBuilder
    订阅者：DecisionManager
    """
    
    canonical_message: Dict[str, Any] = Field(..., description="规范化消息")
    context: Dict[str, Any] = Field(default_factory=dict, description="上下文信息")
    priority: int = Field(default=100, ge=0, le=1000, description="优先级")
    
    class Config:
        json_schema_extra = {
            "example": {
                "canonical_message": {
                    "text": "你好",
                    "sender": {"id": "user_123", "name": "观众A"}
                },
                "context": {"conversation_id": "conv_456"},
                "priority": 100
            }
        }


class DecisionResponseEvent(BaseModel):
    """
    决策响应事件
    
    事件名：decision.response_generated
    发布者：DecisionProvider
    订阅者：UnderstandingLayer（Layer 5）
    """
    
    response: Dict[str, Any] = Field(..., description="决策响应（MessageBase格式）")
    provider: str = Field(..., description="决策Provider名称")
    latency_ms: float = Field(default=0, ge=0, description="决策延迟（毫秒）")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="元数据")


# ==================== Layer 5: 表现理解 ====================

class IntentGeneratedEvent(BaseModel):
    """
    意图生成事件
    
    事件名：understanding.intent_generated
    发布者：UnderstandingLayer
    订阅者：ExpressionLayer（Layer 6）
    """
    
    original_text: str = Field(..., description="原始文本")
    emotion: str = Field(..., description="情感类型")
    response_text: str = Field(..., description="响应文本")
    actions: List[Dict[str, Any]] = Field(default_factory=list, description="动作列表")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="元数据")
    timestamp: float = Field(default_factory=time.time, description="时间戳")


# ==================== Layer 6: 表现生成 ====================

class ExpressionParametersEvent(BaseModel):
    """
    表现参数事件
    
    事件名：expression.parameters_generated
    发布者：ExpressionLayer
    订阅者：OutputProvider（Layer 7）
    """
    
    tts_text: str = Field(default="", description="TTS 文本")
    tts_enabled: bool = Field(default=True, description="是否启用 TTS")
    subtitle_text: str = Field(default="", description="字幕文本")
    subtitle_enabled: bool = Field(default=True, description="是否启用字幕")
    expressions: Dict[str, float] = Field(default_factory=dict, description="表情参数")
    expressions_enabled: bool = Field(default=True, description="是否启用表情")
    hotkeys: List[str] = Field(default_factory=list, description="热键列表")
    hotkeys_enabled: bool = Field(default=True, description="是否启用热键")
    actions: List[Dict[str, Any]] = Field(default_factory=list, description="动作列表")
    actions_enabled: bool = Field(default=True, description="是否启用动作")
    priority: int = Field(default=100, ge=0, description="优先级")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="元数据")
    timestamp: float = Field(default_factory=time.time, description="时间戳")
    
    class Config:
        json_schema_extra = {
            "example": {
                "tts_text": "你好呀~",
                "tts_enabled": True,
                "subtitle_text": "你好呀~",
                "subtitle_enabled": True,
                "expressions": {"happy": 0.8, "surprised": 0.2},
                "expressions_enabled": True,
                "hotkeys": ["wave"],
                "priority": 100
            }
        }


# ==================== 系统事件 ====================

class SystemErrorEvent(BaseModel):
    """
    系统错误事件
    
    事件名：core.error
    发布者：任何组件
    订阅者：错误处理器
    """
    
    error_type: str = Field(..., description="错误类型")
    message: str = Field(..., description="错误消息")
    source: str = Field(..., description="错误源")
    stack_trace: Optional[str] = Field(default=None, description="堆栈跟踪")
    context: Dict[str, Any] = Field(default_factory=dict, description="上下文信息")
    timestamp: float = Field(default_factory=time.time, description="时间戳")
    recoverable: bool = Field(default=True, description="是否可恢复")
```

### 4. 事件注册初始化

```python
# src/core/events/__init__.py
"""
事件系统模块

提供类型安全的事件数据契约系统。
"""

from .registry import EventRegistry
from .names import CoreEvents, PluginEventPrefix
from .models import (
    RawDataEvent,
    NormalizedTextEvent,
    DecisionRequestEvent,
    DecisionResponseEvent,
    IntentGeneratedEvent,
    ExpressionParametersEvent,
    SystemErrorEvent,
)


def register_core_events() -> None:
    """
    注册所有核心事件
    
    在 AmaidesuCore 初始化时调用。
    """
    # Layer 1: 输入感知
    EventRegistry.register_core_event(
        CoreEvents.PERCEPTION_RAW_DATA_GENERATED,
        RawDataEvent
    )
    
    # Layer 2: 输入标准化
    EventRegistry.register_core_event(
        CoreEvents.NORMALIZATION_TEXT_READY,
        NormalizedTextEvent
    )
    
    # Layer 4: 决策层
    EventRegistry.register_core_event(
        CoreEvents.DECISION_REQUEST,
        DecisionRequestEvent
    )
    EventRegistry.register_core_event(
        CoreEvents.DECISION_RESPONSE_GENERATED,
        DecisionResponseEvent
    )
    
    # Layer 5: 表现理解
    EventRegistry.register_core_event(
        CoreEvents.UNDERSTANDING_INTENT_GENERATED,
        IntentGeneratedEvent
    )
    
    # Layer 6: 表现生成
    EventRegistry.register_core_event(
        CoreEvents.EXPRESSION_PARAMETERS_GENERATED,
        ExpressionParametersEvent
    )
    
    # 系统事件
    EventRegistry.register_core_event(
        CoreEvents.CORE_ERROR,
        SystemErrorEvent
    )


__all__ = [
    # 注册表
    "EventRegistry",
    # 事件名常量
    "CoreEvents",
    "PluginEventPrefix",
    # 事件模型
    "RawDataEvent",
    "NormalizedTextEvent",
    "DecisionRequestEvent",
    "DecisionResponseEvent",
    "IntentGeneratedEvent",
    "ExpressionParametersEvent",
    "SystemErrorEvent",
    # 初始化函数
    "register_core_events",
]
```

### 5. EventBus 集成

```python
# src/core/event_bus.py 的增强（关键改动部分）

from typing import Any, Optional
from pydantic import BaseModel, ValidationError
from src.core.events.registry import EventRegistry


class EventBus:
    """增强的事件总线（新增验证功能）"""
    
    def __init__(self, enable_stats: bool = True, enable_validation: bool = False):
        """
        初始化事件总线
        
        Args:
            enable_stats: 是否启用统计功能
            enable_validation: 是否启用数据验证（建议仅 debug 模式开启）
        """
        # ... 原有初始化代码 ...
        self.enable_validation = enable_validation
    
    async def emit(
        self, 
        event_name: str, 
        data: Any, 
        source: str = "unknown", 
        error_isolate: bool = True
    ) -> None:
        """
        发布事件（新增验证逻辑）
        
        Args:
            event_name: 事件名称
            data: 事件数据（推荐使用 Pydantic Model 或 dict）
            source: 事件源
            error_isolate: 是否隔离错误
        """
        if self._is_cleanup:
            self.logger.warning(f"EventBus正在清理中，忽略事件: {event_name}")
            return
        
        # === 新增：数据验证 ===
        if self.enable_validation:
            self._validate_event_data(event_name, data)
        
        # ... 原有发布逻辑 ...
    
    def _validate_event_data(self, event_name: str, data: Any) -> None:
        """
        验证事件数据
        
        策略：
        - 已注册事件：验证数据格式
        - 未注册事件：仅警告，不阻断
        """
        model = EventRegistry.get(event_name)
        
        if model is None:
            # 未注册事件
            if not event_name.startswith("plugin."):
                self.logger.debug(f"未注册的非插件事件: {event_name}")
            return
        
        # 已注册事件：验证数据
        try:
            if isinstance(data, BaseModel):
                # 已经是 Pydantic Model，跳过验证
                return
            elif isinstance(data, dict):
                # 字典数据，尝试验证
                model.model_validate(data)
            else:
                self.logger.warning(
                    f"事件 {event_name} 数据类型不支持验证: {type(data).__name__}"
                )
        except ValidationError as e:
            self.logger.warning(
                f"事件数据验证失败 ({event_name}): {e.error_count()} 个错误"
            )
            for error in e.errors():
                self.logger.debug(f"  - {error['loc']}: {error['msg']}")
    
    # === 新增：类型安全的发布方法 ===
    
    async def emit_typed(
        self,
        event_name: str,
        data: BaseModel,
        source: str = "unknown",
        error_isolate: bool = True
    ) -> None:
        """
        发布类型安全的事件（推荐使用）
        
        Args:
            event_name: 事件名称
            data: Pydantic Model 实例（自动序列化为 dict）
            source: 事件源
            error_isolate: 是否隔离错误
        """
        await self.emit(
            event_name,
            data.model_dump(),
            source,
            error_isolate
        )
```

---

## 🔌 插件集成指南

### 社区插件注册自定义事件

```python
# plugins/my_plugin/plugin.py
from typing import Dict, Any, List
from pydantic import BaseModel, Field
from src.core.events import EventRegistry, PluginEventPrefix


# 1. 定义插件事件模型
class GiftReceivedEvent(BaseModel):
    """礼物接收事件"""
    gift_name: str = Field(..., description="礼物名称")
    gift_count: int = Field(..., ge=1, description="礼物数量")
    sender_name: str = Field(..., description="发送者名称")
    sender_id: str = Field(..., description="发送者ID")
    total_price: float = Field(default=0, ge=0, description="总价值")


class MyPlugin:
    """我的社区插件"""
    
    PLUGIN_NAME = "my_plugin"
    
    # 2. 定义事件名常量
    EVENT_GIFT_RECEIVED = PluginEventPrefix.create(PLUGIN_NAME, "gift_received")
    EVENT_USER_JOINED = PluginEventPrefix.create(PLUGIN_NAME, "user_joined")
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
    
    async def setup(self, event_bus, config: Dict[str, Any]) -> List[Any]:
        """设置插件"""
        self.event_bus = event_bus
        
        # 3. 注册插件事件
        EventRegistry.register_plugin_event(
            self.EVENT_GIFT_RECEIVED,
            GiftReceivedEvent
        )
        
        # 4. 订阅其他事件
        event_bus.on(self.EVENT_GIFT_RECEIVED, self._on_gift_received)
        
        return []
    
    async def cleanup(self):
        """清理插件"""
        # 5. 注销插件事件
        EventRegistry.unregister_plugin_events(self.PLUGIN_NAME)
    
    async def _on_gift_received(self, event_name: str, data: dict, source: str):
        """处理礼物事件"""
        # 类型安全的数据访问
        event = GiftReceivedEvent.model_validate(data)
        print(f"收到礼物: {event.gift_name} x {event.gift_count}")
    
    async def _send_gift_event(self, gift_data: dict):
        """发送礼物事件"""
        # 方式1：使用 Model（推荐）
        event = GiftReceivedEvent(**gift_data)
        await self.event_bus.emit_typed(
            self.EVENT_GIFT_RECEIVED,
            event,
            source=self.PLUGIN_NAME
        )
        
        # 方式2：使用 dict（兼容）
        await self.event_bus.emit(
            self.EVENT_GIFT_RECEIVED,
            gift_data,
            source=self.PLUGIN_NAME
        )
    
    def get_info(self) -> Dict[str, Any]:
        return {
            "name": "MyPlugin",
            "version": "1.0.0",
            "description": "示例社区插件",
            "category": "input",
            "api_version": "1.0",
            "events": [
                self.EVENT_GIFT_RECEIVED,
                self.EVENT_USER_JOINED,
            ]
        }


plugin_entrypoint = MyPlugin
```

---

## 📋 命名空间约定

### 事件名称规范

| 前缀 | 用途 | 验证策略 | 示例 |
|------|------|----------|------|
| `perception.*` | 输入感知层 | 强制 | `perception.raw_data.generated` |
| `normalization.*` | 输入标准化层 | 强制 | `normalization.text.ready` |
| `decision.*` | 决策层 | 强制 | `decision.response_generated` |
| `understanding.*` | 表现理解层 | 强制 | `understanding.intent_generated` |
| `expression.*` | 表现生成层 | 强制 | `expression.parameters_generated` |
| `render.*` | 渲染呈现层 | 强制 | `render.completed` |
| `core.*` | 系统核心 | 强制 | `core.startup`, `core.error` |
| `plugin.*` | 社区插件 | 可选 | `plugin.bili_danmaku.gift_received` |
| `internal.*` | 插件内部 | 跳过 | `internal.cache.updated` |

### 插件事件命名格式

```
plugin.{plugin_name}.{event_name}

示例：
- plugin.bili_danmaku.danmaku_received
- plugin.bili_danmaku.gift_received
- plugin.minecraft.player_joined
- plugin.minecraft.chat_message
```

---

## 🔄 迁移指南

### Phase 1：基础设施（无破坏性变更）

```bash
# 1. 创建事件模块
mkdir -p src/core/events
touch src/core/events/__init__.py
touch src/core/events/registry.py
touch src/core/events/names.py
touch src/core/events/models.py

# 2. 安装依赖（如果尚未安装）
pip install pydantic>=2.0
```

### Phase 2：渐进式迁移

```python
# 旧代码（保持兼容）
await event_bus.emit(
    "perception.raw_data.generated",
    {"data": raw_data, "source": provider_name},
    source=provider_name
)

# 新代码（推荐）
from src.core.events import CoreEvents, RawDataEvent

event_data = RawDataEvent(
    content=raw_data.content,
    source=raw_data.source,
    data_type=raw_data.data_type,
    metadata=raw_data.metadata
)
await event_bus.emit_typed(
    CoreEvents.PERCEPTION_RAW_DATA_GENERATED,
    event_data,
    source=provider_name
)
```

### Phase 3：启用验证

```python
# config.toml
[event_bus]
enable_validation = true  # 仅在 debug 模式启用
```

---

## ✅ 实施计划

| 阶段 | 任务 | 预计工时 | 风险 |
|------|------|----------|------|
| **Phase 1** | 创建 EventRegistry 和 Models | 1-2 天 | 低 |
| **Phase 2** | EventBus 集成（可选验证） | 1 天 | 低 |
| **Phase 3** | 注册核心事件 | 0.5 天 | 低 |
| **Phase 4** | 更新文档和示例 | 0.5 天 | 无 |
| **Phase 5** | 迁移核心组件 | 3-5 天 | 中 |
| **Phase 6** | 迁移插件 | 5-7 天 | 中 |
| **总计** | - | **11-16 天** | - |

---

## 🔗 相关文档

- [架构总览](./overview.md) - 重构目标和7层架构概述
- [插件系统设计](./plugin_system.md) - 插件系统和Provider接口
- [EventBus增强](../plan/eventbus_enhancement.md) - EventBus增强计划

---

**文档创建时间**: 2026-01-31  
**版本**: 1.0  
**状态**: 待实施
