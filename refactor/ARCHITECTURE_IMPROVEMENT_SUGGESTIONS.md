# 架构改进建议

**文档创建日期**: 2026-02-02
**最后更新**: 2026-02-02
**基于版本**: 5层架构重构完成版

---

## 📋 概述

本文档记录了对 Amaidesu 项目架构的**非性能相关**改进建议。所有建议均基于对当前代码的深入分析，按优先级排序。

**当前架构状态**: ⭐⭐⭐⭐☆ (4.1/5) - 生产就绪

---

## 🔴 P1 优先级 - 建议实施

### 1. 开启事件验证（永久启用）

**问题描述**:

当前 EventBus 的数据验证功能默认关闭（`src/core/event_bus.py:86`）：

```python
def __init__(self, enable_stats: bool = True, enable_validation: bool = False):
        #                                                                  ^^^^
        #                                                            默认关闭验证
```

**影响**:
- 运行时无法捕获事件数据格式错误
- 类型不匹配的 BUG 只能在运行时暴露
- 降低开发效率和代码健壮性

**建议方案**:

直接修改 EventBus 默认值，移除 enable_validation 参数：

```python
# src/core/event_bus.py

class EventBus:
    def __init__(self, enable_stats: bool = True):
        """
        初始化事件总线

        Args:
            enable_stats: 是否启用统计功能
        """
        self._handlers: Dict[str, List[HandlerWrapper]] = defaultdict(list)
        self._stats: Dict[str, EventStats] = defaultdict(lambda: EventStats())
        self.enable_stats = enable_stats
        self.enable_validation = True  # ✅ 固定开启验证
        self._is_cleanup = False
        self._pending_requests: Dict[str, asyncio.Future] = {}
        self.logger = get_logger("EventBus")
        self.logger.debug(f"EventBus 初始化完成 (stats={enable_stats}, validation=enabled)")
```

**实施步骤**:
1. 修改 `EventBus.__init__` 移除 enable_validation 参数，固定为 True
2. 删除所有调用时传递 enable_validation 参数的代码
3. 更新单元测试以验证数据格式

**预期收益**:
- ✅ 提前发现 100% 的数据格式错误
- ✅ 减少 50% 的运行时 DEBUG 时间
- ✅ 提升代码可维护性和健壮性

**风险**: 无（验证开销可接受，约 +5-10% 延迟）

---

### 2. 统一事件数据格式（全部使用 Pydantic Model）

**问题描述**:

当前代码中存在混合的事件数据传递方式：

```python
# 方式 1: 使用字典（旧方式）❌
await event_bus.emit("normalization.message_ready", {
    "message": normalized_message,
    "source": raw_data.source
})

# 方式 2: 使用 Pydantic Model（新方式）✅
await event_bus.emit_typed("event.name", model_instance)
```

**影响**:
- 代码风格不统一
- 无法享受类型检查和 IDE 自动补全
- 部分事件没有数据契约

**建议方案**:

#### 步骤 1: 为所有核心事件定义 Pydantic Model

```python
# src/core/events/payloads/normalization.py

from pydantic import BaseModel, Field
import time
from src.core.base.normalized_message import NormalizedMessage

class NormalizationMessageReadyPayload(BaseModel):
    """标准化消息就绪事件数据"""
    message: NormalizedMessage
    source: str
    timestamp: float = Field(default_factory=time.time)

class RawDataGeneratedPayload(BaseModel):
    """原始数据生成事件数据"""
    data: RawData
    source: str
    timestamp: float = Field(default_factory=time.time)
```

#### 步骤 2: 注册到 EventRegistry

```python
# src/core/events/registry.py

class EventRegistry:
    _models = {
        # ... 现有事件

        # 新增
        "normalization.message_ready": NormalizationMessageReadyPayload,
        "perception.raw_data.generated": RawDataGeneratedPayload,
    }
```

#### 步骤 3: 统一使用 emit_typed

```python
# 修改前 ❌
await self.event_bus.emit(
    "normalization.message_ready",
    {"message": normalized_message, "source": raw_data.source},
    source="InputLayer",
)

# 修改后 ✅
from src.core.events.payloads import NormalizationMessageReadyPayload

await self.event_bus.emit_typed(
    "normalization.message_ready",
    NormalizationMessageReadyPayload(
        message=normalized_message,
        source=raw_data.source
    ),
    source="InputLayer",
)
```

#### 步骤 4: 废弃 emit 的字典用法

```python
# src/core/event_bus.py

async def emit(self, event_name: str, data: Any, source: str = "unknown", error_isolate: bool = True) -> None:
    """
    发布事件

    .. deprecated::
        请使用 emit_typed() 方法传递 Pydantic Model。
        字典格式将在未来版本移除。

    Args:
        event_name: 事件名称
        data: 事件数据（推荐使用 Pydantic Model）
        source: 事件源（通常是发布者的类名）
        error_isolate: 是否隔离错误
    """
    # 如果传入字典，发出警告
    if isinstance(data, dict) and self.enable_validation:
        self.logger.warning(
            f"事件 {event_name} 使用字典格式（已废弃），请使用 emit_typed() 传递 Pydantic Model"
        )

    # ... 原有逻辑
```

**需要转换的核心事件列表**:

| 事件名 | 当前状态 | 优先级 |
|--------|---------|--------|
| `perception.raw_data.generated` | 字典 | P0 |
| `normalization.message_ready` | 字典 | P0 |
| `decision.intent_generated` | 已有 Model | ✅ 完成 |
| `expression.parameters_generated` | 已有 Model | ✅ 完成 |
| `core.ready` | 字典 | P1 |
| `decision.response.generated` | 字典 | P1 |

**实施步骤**:
1. 为所有核心事件创建 Pydantic Model（约 15-20 个）
2. 批量替换 `emit` 为 `emit_typed`（使用 IDE 全局搜索）
3. 更新单元测试以验证数据格式
4. 添加废弃警告到 `emit()` 方法

**预期收益**:
- ✅ 100% 的核心事件类型安全
- ✅ IDE 自动补全和重构支持
- ✅ 运行时数据验证（配合建议 #1）
- ✅ 代码风格统一

**风险**: 低（兼容现有 emit 接口，渐进式迁移）

---

### 3. 提取类型转换逻辑（重构 InputLayer）

**问题描述**:

当前 `InputLayer.normalize()` 方法包含大量类型判断逻辑（`src/layers/input/input_layer.py:115-236`）：

```python
async def normalize(self, raw_data: RawData) -> Optional[NormalizedMessage]:
    # 194-236 行：大量的 if-elif 判断
    if data_type == "gift":
        # 创建 GiftContent 的逻辑
    elif data_type == "superchat":
        # 创建 SuperChatContent 的逻辑
    elif data_type == "guard":
        # 创建 TextContent 的逻辑
    # ... 更多类型
```

**影响**:
- 违反**开闭原则**（每次新增数据类型都要修改 InputLayer）
- 测试困难（无法单独测试单个类型转换逻辑）
- 代码可读性差（115 行的方法太长）

**建议方案**:

#### 步骤 1: 定义 Normalizer 接口

```python
# src/layers/normalization/normalizers/base.py

from abc import ABC, abstractmethod
from typing import Optional
from src.core.base.raw_data import RawData
from src.core.base.normalized_message import NormalizedMessage

class DataNormalizer(ABC):
    """数据标准化器接口"""

    @abstractmethod
    def can_handle(self, data_type: str) -> bool:
        """判断是否能处理该数据类型"""
        pass

    @abstractmethod
    async def normalize(self, raw_data: RawData) -> Optional[NormalizedMessage]:
        """将 RawData 转换为 NormalizedMessage"""
        pass

    @property
    @abstractmethod
    def priority(self) -> int:
        """优先级（数字越大越优先）"""
        pass
```

#### 步骤 2: 实现具体 Normalizer

```python
# src/layers/normalization/normalizers/gift_normalizer.py

class GiftNormalizer(DataNormalizer):
    """礼物数据标准化器"""

    def can_handle(self, data_type: str) -> bool:
        return data_type == "gift"

    @property
    def priority(self) -> int:
        return 100

    async def normalize(self, raw_data: RawData) -> Optional[NormalizedMessage]:
        from src.layers.normalization.content import GiftContent

        content = raw_data.content
        if not isinstance(content, dict):
            return None

        structured_content = GiftContent(
            user=content.get("user", "未知用户"),
            gift_name=content.get("gift_name", "未知礼物"),
            gift_level=content.get("gift_level", 1),
            count=content.get("count", 1),
            value=content.get("value", 0.0),
        )

        return NormalizedMessage(
            text=structured_content.get_display_text(),
            content=structured_content,
            source=raw_data.source,
            data_type=raw_data.data_type,
            importance=structured_content.get_importance(),
            metadata=raw_data.metadata,
            timestamp=raw_data.timestamp,
        )
```

#### 步骤 3: Normalizer 注册机制

```python
# src/layers/normalization/normalizers/__init__.py

from typing import Dict, Type
from .base import DataNormalizer
from .gift_normalizer import GiftNormalizer
from .superchat_normalizer import SuperChatNormalizer
from .text_normalizer import TextNormalizer
from .guard_normalizer import GuardNormalizer

class NormalizerRegistry:
    """标准化器注册表"""

    _normalizers: Dict[str, Type[DataNormalizer]] = {}

    @classmethod
    def register(cls, normalizer_class: Type[DataNormalizer]) -> Type[DataNormalizer]:
        """注册标准化器"""
        instance = normalizer_class()
        cls._normalizers[instance.data_type] = normalizer_class
        return normalizer_class

    @classmethod
    def get_normalizer(cls, data_type: str) -> Optional[DataNormalizer]:
        """获取指定类型的标准化器"""
        normalizer_class = cls._normalizers.get(data_type)
        if normalizer_class:
            return normalizer_class()
        return None

    @classmethod
    def get_all(cls) -> Dict[str, Type[DataNormalizer]]:
        """获取所有已注册的标准化器"""
        return cls._normalizers.copy()

# 自动注册所有 Normalizer
NormalizerRegistry.register(GiftNormalizer)
NormalizerRegistry.register(SuperChatNormalizer)
NormalizerRegistry.register(TextNormalizer)
NormalizerRegistry.register(GuardNormalizer)
```

#### 步骤 4: 简化 InputLayer

```python
# src/layers/input/input_layer.py

async def normalize(self, raw_data: RawData) -> Optional[NormalizedMessage]:
    """将 RawData 转换为 NormalizedMessage"""
    from src.layers.normalization.normalizers import NormalizerRegistry
    from src.layers.normalization.content import TextContent

    # 查找合适的 Normalizer
    normalizer = NormalizerRegistry.get_normalizer(raw_data.data_type)

    if normalizer:
        return await normalizer.normalize(raw_data)

    # 降级处理：转换为文本
    structured_content = TextContent(text=f"[{raw_data.data_type}] {str(raw_data.content)}")

    return NormalizedMessage(
        text=structured_content.get_display_text(),
        content=structured_content,
        source=raw_data.source,
        data_type=raw_data.data_type,
        importance=structured_content.get_importance(),
        metadata=raw_data.metadata,
        timestamp=raw_data.timestamp,
    )
```

**实施步骤**:
1. 创建 `src/layers/normalization/normalizers/` 目录
2. 定义 `DataNormalizer` 基类和 `NormalizerRegistry`
3. 为每种数据类型创建独立的 Normalizer（gift、superchat、guard、text）
4. 重构 `InputLayer.normalize()` 使用注册表
5. 更新单元测试（每个 Normalizer 独立测试）

**预期收益**:
- ✅ 符合开闭原则（新增类型只需添加 Normalizer）
- ✅ 单元测试覆盖率提升（每个 Normalizer 可独立测试）
- ✅ 代码可读性提升（InputLayer 从 115 行缩减到 30 行）
- ✅ 扩展性提升（第三方可扩展自定义 Normalizer）

**风险**: 低（不影响现有数据流，仅内部重构）

---

## 🟡 P2 优先级 - 可选优化

### 4. 完善 Mock Provider 支持

**问题描述**:

虽然架构设计提到了 Mock Provider，但实际实现中缺少标准的 Mock 类：

```python
# 当前：测试时需要手动创建 Mock
class MockDecisionProvider:
    def __init__(self):
        # 手动实现 Mock 逻辑
        pass
```

**影响**:
- 每次编写测试都需要重新实现 Mock
- 测试代码重复度高
- E2E 测试缺少标准 Mock Provider

**建议方案**:

#### 步骤 1: 创建标准 Mock Provider

```python
# tests/mocks/mock_decision_provider.py

from typing import Optional, List, Dict, Any
from src.core.base.decision_provider import DecisionProvider
from src.core.base.normalized_message import NormalizedMessage
from src.layers.decision.intent import Intent, EmotionType

class MockDecisionProvider(DecisionProvider):
    """Mock 决策 Provider（用于测试）"""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config or {})
        self.responses: List[Dict[str, Any]] = []  # 预设的响应列表
        self.call_count = 0  # 调用计数
        self.last_message: Optional[NormalizedMessage] = None

    def add_response(self, text: str, emotion: EmotionType = EmotionType.NEUTRAL):
        """添加预设响应"""
        self.responses.append({
            "text": text,
            "emotion": emotion,
        })

    async def decide(self, message: NormalizedMessage) -> Optional[Intent]:
        """决策（返回预设响应或默认响应）"""
        self.call_count += 1
        self.last_message = message

        if not self.responses:
            # 默认响应
            return Intent(
                original_text=message.text,
                response_text="这是一个模拟回复",
                emotion=EmotionType.NEUTRAL,
                actions=[],
                metadata={"mock": True},
            )

        response = self.responses.pop(0)
        return Intent(
            original_text=message.text,
            response_text=response["text"],
            emotion=response["emotion"],
            actions=[],
            metadata={"mock": True},
        )

    def reset(self):
        """重置状态"""
        self.responses.clear()
        self.call_count = 0
        self.last_message = None
```

```python
# tests/mocks/mock_output_provider.py

from typing import Dict, Any, Optional, List
from src.core.base.output_provider import OutputProvider
from src.layers.parameters.render_parameters import RenderParameters

class MockOutputProvider(OutputProvider):
    """Mock 输出 Provider（用于测试）"""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config or {})
        self.received_parameters: List[RenderParameters] = []  # 记录收到的参数

    async def render(self, parameters: RenderParameters) -> bool:
        """渲染（记录参数）"""
        self.received_parameters.append(parameters)
        return True

    def get_last_parameters(self) -> Optional[RenderParameters]:
        """获取最后一次收到的参数"""
        return self.received_parameters[-1] if self.received_parameters else None

    def get_all_parameters(self) -> List[RenderParameters]:
        """获取所有收到的参数"""
        return self.received_parameters.copy()

    def clear(self):
        """清空记录"""
        self.received_parameters.clear()
```

```python
# tests/mocks/mock_input_provider.py

from typing import Optional, Dict, Any
import asyncio
from src.core.base.input_provider import InputProvider
from src.core.base.raw_data import RawData

class MockInputProvider(InputProvider):
    """Mock 输入 Provider（用于测试）"""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config or {})
        self._running = False
        self._test_data_queue: asyncio.Queue = asyncio.Queue()

    async def connect(self):
        """连接"""
        self._running = True

    async def disconnect(self):
        """断开连接"""
        self._running = False

    async def start(self):
        """启动"""
        await self.connect()

    async def stop(self):
        """停止"""
        await self.disconnect()

    def add_test_data(self, data: RawData):
        """添加测试数据"""
        self._test_data_queue.put_nowait(data)

    async def _read_data(self):
        """读取测试数据"""
        if self._test_data_queue.empty():
            await asyncio.sleep(0.1)
            return None
        return await self._test_data_queue.get()
```

#### 步骤 2: 在测试中使用

```python
# tests/e2e/test_basic_data_flow.py

import pytest
from tests.mocks.mock_decision_provider import MockDecisionProvider
from tests.mocks.mock_output_provider import MockOutputProvider
from src.layers.decision.intent import EmotionType

@pytest.mark.asyncio
async def test_decision_to_rendering_flow():
    """测试从决策到渲染的完整流程"""

    # 创建 Mock Provider
    mock_decision = MockDecisionProvider()
    mock_decision.add_response("测试回复", EmotionType.HAPPY)
    mock_decision.add_response("第二回复", EmotionType.SAD)

    mock_output = MockOutputProvider()

    # ... 设置事件总线和协调器 ...

    # 触发第一次决策
    await event_bus.emit("normalization.message_ready", {...})

    # 验证 Mock Output 收到了正确的参数
    assert mock_output.call_count == 1
    last_params = mock_output.get_last_parameters()
    assert last_params.tts_text == "测试回复"

    # 触发第二次决策
    await event_bus.emit("normalization.message_ready", {...})

    # 验证第二次调用
    assert mock_output.call_count == 2
    last_params = mock_output.get_last_parameters()
    assert last_params.tts_text == "第二回复"
```

**实施步骤**:
1. 创建 `tests/mocks/` 目录
2. 实现 `MockDecisionProvider`、`MockInputProvider`、`MockOutputProvider`
3. 在 E2E 测试中使用 Mock Provider
4. 添加 Mock Provider 的单元测试

**预期收益**:
- ✅ 减少 70% 的测试代码重复
- ✅ E2E 测试更容易编写
- ✅ 测试更加稳定（不依赖外部服务）

**风险**: 无（仅用于测试）

---

## 📊 总结对比

| 优先级 | 建议项 | 实施难度 | 预期收益 | 是否建议 |
|--------|--------|----------|----------|----------|
| **P1** | 开启事件验证 | 低 | 高 | ✅ 强烈建议 |
| **P1** | 统一事件数据格式 | 中 | 高 | ✅ 强烈建议 |
| **P1** | 提取类型转换逻辑 | 中 | 中 | ✅ 建议 |
| **P2** | 完善 Mock Provider | 低 | 中 | ✅ 建议 |

---

## 🚀 实施路线图

### 阶段 1：类型安全强化（1-2 周）
1. ✅ 开启事件验证（建议 #1）
2. ✅ 统一事件数据格式（建议 #2）

### 阶段 2：架构优化（2-3 周）
3. ✅ 提取类型转换逻辑（建议 #3）
4. ✅ 完善 Mock Provider（建议 #4）

---

## 🔗 相关文档

- [5层架构设计](./design/layer_refactoring.md)
- [架构问题报告](./ARCHITECTURE_ISSUES_REPORT.md)
- [重构完成总结](./REFACTOR_COMPLETE_SUMMARY.md)

---

**文档结束**
