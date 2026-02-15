# Pipeline 开发指南

本文档详细介绍如何为 Amaidesu 项目开发 Pipeline（管道）。

## 概述

Pipeline 是一种消息处理机制，用于在数据流经 3 域架构时对数据进行过滤、转换或预处理。项目中有两种 Pipeline：

| 类型 | 域 | 处理对象 | 位置 |
|------|-----|----------|------|
| **TextPipeline** | Input Domain | 原始文本 (str) | RawData → NormalizedMessage |
| **MessagePipeline** | Input Domain | NormalizedMessage 对象 | Provider 产出后、发布事件前 |
| **OutputPipeline** | Output Domain | Intent 对象 | Intent → OutputProvider |

## 1. TextPipeline

TextPipeline 处理原始字符串文本，在 `InputDomain.normalize()` 方法中调用。

### 基类定义

```python
from src.domains.input.pipelines.manager import TextPipelineBase

class TextPipelineBase(ABC):
    priority: int = 500          # 执行优先级，越小越先执行
    enabled: bool = True         # 是否启用
    error_handling: PipelineErrorHandling = PipelineErrorHandling.CONTINUE
    timeout_seconds: float = 5.0 # 超时时间

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = get_logger(self.__class__.__name__)
        self._stats = PipelineStats()

    async def process(self, text: str, metadata: Dict[str, Any]) -> Optional[str]:
        """处理文本，返回处理后的文本或 None（丢弃消息）"""
        start_time = time.time()
        result = await self._process(text, metadata)
        self._stats.processed_count += 1
        return result

    @abstractmethod
    async def _process(self, text: str, metadata: Dict[str, Any]) -> Optional[str]:
        """子类实现实际处理逻辑"""
        pass
```

### 创建 TextPipeline

1. 在 `src/domains/input/pipelines/` 目录下创建新目录
2. 创建 `pipeline.py` 文件，继承 `TextPipelineBase`
3. 实现 `_process()` 方法

```python
# src/domains/input/pipelines/my_filter/pipeline.py
from typing import Any, Dict, Optional

from src.domains.input.pipelines.manager import TextPipelineBase


class MyFilterTextPipeline(TextPipelineBase):
    """我的自定义过滤器"""

    priority = 500  # 中等优先级

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.threshold = self.config.get("threshold", 10)

    async def _process(self, text: str, metadata: Dict[str, Any]) -> Optional[str]:
        """
        处理文本

        Args:
            text: 待处理的文本
            metadata: 元数据（包含 user_id, group_id 等）

        Returns:
            处理后的文本，或 None 表示丢弃
        """
        # 在这里实现过滤逻辑
        if len(text) > self.threshold:
            return text  # 返回原文本（允许通过）
        return None  # 返回 None 丢弃消息
```

### 创建 __init__.py

```python
# src/domains/input/pipelines/my_filter/__init__.py
from .pipeline import MyFilterTextPipeline

__all__ = ["MyFilterTextPipeline"]
```

## 2. MessagePipeline

MessagePipeline 处理完整的 `NormalizedMessage` 对象，在 `InputProviderManager._run_provider()` 中调用。

### 基类定义

```python
from src.domains.input.pipelines.manager import MessagePipelineBase
from src.modules.types.base.normalized_message import NormalizedMessage

class MessagePipelineBase(ABC):
    priority: int = 500
    enabled: bool = True
    error_handling: PipelineErrorHandling = PipelineErrorHandling.CONTINUE
    timeout_seconds: float = 5.0

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = get_logger(self.__class__.__name__)
        self._stats = PipelineStats()

    async def process(self, message: NormalizedMessage) -> Optional[NormalizedMessage]:
        """处理消息，返回处理后的消息或 None（丢弃消息）"""
        result = await self._process(message)
        self._stats.processed_count += 1
        return result

    @abstractmethod
    async def _process(self, message: NormalizedMessage) -> Optional[NormalizedMessage]:
        """子类实现实际处理逻辑"""
        pass
```

### 创建 MessagePipeline

```python
# src/domains/input/pipelines/my_filter/pipeline.py
from typing import Any, Dict, Optional

from src.domains.input.pipelines.manager import MessagePipelineBase
from src.modules.types.base.normalized_message import NormalizedMessage


class MyFilterMessagePipeline(MessagePipelineBase):
    """我的自定义消息过滤器"""

    priority = 500

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.min_length = self.config.get("min_length", 3)

    async def _process(self, message: NormalizedMessage) -> Optional[NormalizedMessage]:
        """
        处理 NormalizedMessage

        注意：NormalizedMessage 是 Pydantic 模型，不可直接修改字段。
        如需修改，使用 message.model_copy(update={...}) 创建新实例。
        """
        # 检查消息长度
        if len(message.text) < self.min_length:
            self.logger.debug(f"消息长度 {len(message.text)} 小于最小要求 {self.min_length}")
            return None  # 丢弃

        # 返回原消息（允许通过）
        return message
```

可以在同一个 `pipeline.py` 文件中同时定义 TextPipeline 和 MessagePipeline 版本。

## 3. OutputPipeline

OutputPipeline 处理 `Intent` 对象，在 Intent 分发给 OutputProvider 前执行过滤。

### 基类定义

```python
from src.domains.output.pipelines.base import OutputPipelineBase
from src.modules.types import Intent

class OutputPipelineBase(ABC):
    priority: int = 500
    enabled: bool = True
    error_handling: PipelineErrorHandling = PipelineErrorHandling.CONTINUE
    timeout_seconds: float = 5.0

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = get_logger(self.__class__.__name__)
        self._stats = PipelineStats()

    async def process(self, intent: Intent) -> Optional[Intent]:
        result = await self._process(intent)
        self._stats.processed_count += 1
        return result

    @abstractmethod
    async def _process(self, intent: Intent) -> Optional[Intent]:
        """子类实现实际处理逻辑"""
        pass
```

### 创建 OutputPipeline

```python
# src/domains/output/pipelines/my_filter/pipeline.py
from typing import TYPE_CHECKING, Any, Optional

from src.domains.output.pipelines.base import OutputPipelineBase

if TYPE_CHECKING:
    from src.modules.types import Intent


class MyFilterPipeline(OutputPipelineBase):
    """我的自定义输出过滤器"""

    priority = 100  # 高优先级

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.blocked_words = self.config.get("blocked_words", [])

    async def _process(self, intent: "Intent") -> Optional["Intent"]:
        """处理 Intent"""
        if not intent.response_text:
            return intent

        # 检查是否包含敏感词
        for word in self.blocked_words:
            if word in intent.response_text:
                self.logger.info(f"检测到敏感词 '{word}'，丢弃消息")
                return None  # 丢弃

        return intent
```

## 4. 配置启用

在 `config.toml` 中配置 Pipeline。

### TextPipeline 配置

```toml
[pipelines.my_filter]
priority = 500           # 必须：定义优先级并启用（数字越小越先执行）
enabled = true           # 可选：是否启用（默认 true）
threshold = 10           # 可选：自定义配置参数
timeout_seconds = 5.0    # 可选：处理超时时间
error_handling = "continue"  # 可选：错误处理策略
```

### MessagePipeline 配置

```toml
[pipelines.my_filter]
[pipelines.my_filter.input]      # 注意：需要 input 子配置
priority = 500
enabled = true
min_length = 3
```

### OutputPipeline 配置

```toml
[pipelines.my_filter]
[pipelines.my_filter.output]     # 注意：需要 output 子配置
priority = 100
enabled = true
blocked_words = ["敏感词1", "敏感词2"]
```

### 错误处理策略

| 值 | 说明 |
|-----|------|
| `continue` | 记录错误，继续执行下一个 Pipeline（默认） |
| `stop` | 停止执行，抛出异常 |
| `drop` | 丢弃消息，不执行后续 Pipeline |

## 5. 现有 Pipeline

项目已内置以下 Pipeline：

### Input Domain

| Pipeline | 说明 | 优先级 |
|----------|------|-------|
| **rate_limit** | 限流管道，基于滑动时间窗口限制消息频率 | 100 |
| **similar_filter** | 相似文本过滤管道，过滤短时间内重复的消息 | 500 |

### Output Domain

| Pipeline | 说明 | 优先级 |
|----------|------|-------|
| **profanity_filter** | 敏感词过滤管道，过滤 Intent 中的敏感词 | 100 |

## 6. 执行流程

### Input Pipeline 流程

```
外部输入 (RawData)
    ↓
【TextPipeline 链】text + metadata → text | None
    ↓ (返回 text)
标准化为 NormalizedMessage
    ↓
【MessagePipeline 链】NormalizedMessage → NormalizedMessage | None
    ↓ (返回消息)
EventBus: data.message 事件
    ↓
【Decision Domain】
```

### Output Pipeline 流程

```
Decision Domain
    ↓
EventBus: decision.intent 事件
    ↓
【OutputPipeline 链】Intent → Intent | None
    ↓ (返回 Intent)
【Output Domain】Intent → 实际输出
```

### 执行顺序

1. 系统初始化时扫描配置中的 Pipeline
2. 按 `priority` 升序排列（数字越小越先执行）
3. 消息依次通过各 Pipeline
4. 如果任何 Pipeline 返回 `None`，消息被丢弃
5. Pipeline 支持超时控制和错误处理策略

## 7. 统计信息

所有 Pipeline 都自动收集统计信息：

```python
# 获取统计
stats = pipeline.get_stats()
# processed_count: 处理的消息数
# dropped_count: 丢弃的消息数
# error_count: 错误次数
# avg_duration_ms: 平均处理时间（毫秒）

# 获取 Pipeline 信息
info = pipeline.get_info()
# name: Pipeline 名称
# priority: 优先级
# enabled: 是否启用
```

## 8. 最佳实践

### 命名规范

- TextPipeline 类名：`MyFilterTextPipeline`
- MessagePipeline 类名：`MyFilterMessagePipeline`
- OutputPipeline 类名：`MyFilterPipeline`
- 目录名：`my_filter`（snake_case）

### 注意事项

1. **不要直接修改 Pydantic 模型**：如需修改 `NormalizedMessage`，使用 `message.model_copy(update={...})`
2. **优先使用现有基类**：继承 `TextPipelineBase` / `MessagePipelineBase` / `OutputPipelineBase`
3. **合理设置优先级**：限流等基础过滤使用较低优先级（先执行）
4. **处理空值**：始终检查输入是否为 None
5. **日志记录**：在关键路径添加适当日志

### 代码示例：完整的消息过滤 Pipeline

```python
from typing import Any, Dict, Optional
import time

from src.domains.input.pipelines.manager import MessagePipelineBase
from src.modules.types.base.normalized_message import NormalizedMessage


class LengthFilterMessagePipeline(MessagePipelineBase):
    """按消息长度过滤的 Pipeline"""

    priority = 100  # 高优先级，尽早过滤

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.min_length = self.config.get("min_length", 1)
        self.max_length = self.config.get("max_length", 500)

    async def _process(self, message: NormalizedMessage) -> Optional[NormalizedMessage]:
        text_length = len(message.text)

        # 检查长度范围
        if text_length < self.min_length:
            self.logger.debug(f"消息太短: {text_length} < {self.min_length}")
            return None

        if text_length > self.max_length:
            self.logger.debug(f"消息太长: {text_length} > {self.max_length}")
            return None

        return message

    def get_info(self) -> Dict[str, Any]:
        info = super().get_info()
        info.update({
            "min_length": self.min_length,
            "max_length": self.max_length,
        })
        return info
```
