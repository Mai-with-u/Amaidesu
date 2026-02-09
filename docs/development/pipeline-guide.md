# 管道开发指南

管道（Pipeline）用于在消息处理流程中进行预处理，位于 Input Domain 内部。

## 管道概述

### 管道作用

管道处理 `NormalizedMessage`，实现：
- **限流**：控制消息处理速率
- **过滤**：丢弃不需要的消息
- **转换**：修改消息内容
- **统计**：收集消息统计信息
- **日志**：记录消息流转

### 管道特点

- **链式处理**：消息按优先级顺序通过管道
- **可插拔**：通过配置启用/禁用管道
- **异步**：支持异步操作
- **可中断**：返回 `None` 丢弃消息

## 基本结构

### TextPipeline 接口

```python
from src.domains.input.pipeline_manager import TextPipeline
from src.core.base.normalized_message import NormalizedMessage
from typing import Optional, Dict, Any

class MyPipeline(TextPipeline):
    """自定义管道"""

    # 管道优先级（数值越小越先执行）
    priority = 500

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.param = self.config.get("param", "default")

    async def process(self, message: NormalizedMessage) -> Optional[NormalizedMessage]:
        """
        处理消息

        返回：
            NormalizedMessage: 继续传递
            None: 丢弃消息
        """
        # 处理逻辑
        if self._should_filter(message):
            return None  # 丢弃消息

        # 修改消息
        message.text = self._transform(message.text)

        return message  # 继续传递
```

### 接口方法

| 方法 | 说明 | 必须实现 |
|------|------|----------|
| `process()` | 处理消息 | ✅ |
| `priority` | 管道优先级 | ✅（类属性） |
| `initialize()` | 初始化管道 | ❌（可选） |
| `cleanup()` | 清理资源 | ❌（可选） |

## 常见管道模式

### 1. 过滤管道

```python
class ProfanityFilterPipeline(TextPipeline):
    """敏感词过滤管道"""
    priority = 100

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.bad_words = set(config.get("bad_words", []))

    async def process(self, message: NormalizedMessage) -> Optional[NormalizedMessage]:
        # 检查是否包含敏感词
        for word in self.bad_words:
            if word in message.text:
                self.logger.info(f"过滤敏感词: {word}")
                return None  # 丢弃消息

        return message  # 继续传递
```

### 2. 限流管道

```python
import asyncio
from collections import deque

class RateLimitPipeline(TextPipeline):
    """限流管道"""
    priority = 50

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.max_messages = config.get("max_messages", 10)
        self.time_window = config.get("time_window", 60)  # 秒
        self.message_history = deque()

    async def process(self, message: NormalizedMessage) -> Optional[NormalizedMessage]:
        now = time.time()

        # 清理过期记录
        while self.message_history and now - self.message_history[0] > self.time_window:
            self.message_history.popleft()

        # 检查是否超限
        if len(self.message_history) >= self.max_messages:
            self.logger.warning("触发限流，丢弃消息")
            return None

        # 记录消息
        self.message_history.append(now)

        return message
```

### 3. 转换管道

```python
class TextTransformPipeline(TextPipeline):
    """文本转换管道"""
    priority = 200

    async def process(self, message: NormalizedMessage) -> Optional[NormalizedMessage]:
        # 转换为小写
        message.text = message.text.lower()

        # 去除多余空格
        message.text = " ".join(message.text.split())

        return message
```

### 4. 相似文本过滤

```python
from difflib import SequenceMatcher

class SimilarTextFilterPipeline(TextPipeline):
    """相似文本过滤管道"""
    priority = 150

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.similarity_threshold = config.get("similarity_threshold", 0.8)
        self.recent_messages = []
        self.max_history = config.get("max_history", 10)

    async def process(self, message: NormalizedMessage) -> Optional[NormalizedMessage]:
        # 检查与历史消息的相似度
        for recent_msg in self.recent_messages:
            similarity = SequenceMatcher(None, message.text, recent_msg).ratio()
            if similarity > self.similarity_threshold:
                self.logger.info(f"过滤相似文本 (相似度: {similarity:.2f})")
                return None

        # 添加到历史记录
        self.recent_messages.append(message.text)
        if len(self.recent_messages) > self.max_history:
            self.recent_messages.pop(0)

        return message
```

### 5. 消息日志管道

```python
class MessageLoggerPipeline(TextPipeline):
    """消息日志管道"""
    priority = 1000  # 最后执行

    async def process(self, message: NormalizedMessage) -> Optional[NormalizedMessage]:
        self.logger.info(
            f"消息: {message.text[:50]}... "
            f"来源: {message.source} "
            f"用户: {message.user.nickname if message.user else 'N/A'}"
        )
        return message
```

## 管道配置

### 配置文件

```toml
[pipelines]
  [pipelines.my_pipeline]
  priority = 500
  param = "value"

  [pipelines.rate_limit]
  priority = 50
  max_messages = 10
  time_window = 60

  [pipelines.similar_filter]
  priority = 150
  similarity_threshold = 0.8
  max_history = 10
```

### 优先级规则

管道按优先级从小到大执行：

```
priority = 50   (RateLimitPipeline)
     ↓
priority = 100  (ProfanityFilterPipeline)
     ↓
priority = 150  (SimilarTextFilterPipeline)
     ↓
priority = 200  (TextTransformPipeline)
     ↓
priority = 500  (MyPipeline)
     ↓
priority = 1000 (MessageLoggerPipeline)
```

## 管道注册

### 自动注册

在 `src/domains/input/pipelines/my_pipeline/__init__.py` 中注册：

```python
from src.domains.input.pipeline_manager import PipelineManager
from .pipeline import MyPipeline

PipelineManager.register_pipeline("my_pipeline", MyPipeline)
```

### 配置启用

```toml
[pipelines]
enabled_pipelines = ["rate_limit", "similar_filter", "my_pipeline"]

  [pipelines.rate_limit]
  priority = 50
  max_messages = 10
  time_window = 60

  [pipelines.similar_filter]
  priority = 150
  similarity_threshold = 0.8

  [pipelines.my_pipeline]
  priority = 500
  param = "value"
```

## 最佳实践

### 1. 优先级设计

```python
# ✅ 正确：合理的优先级分配
class ProfanityFilterPipeline(TextPipeline):
    priority = 100  # 优先过滤

class RateLimitPipeline(TextPipeline):
    priority = 50   # 最先执行，避免处理超限消息

class MessageLoggerPipeline(TextPipeline):
    priority = 1000  # 最后执行，记录最终状态
```

### 2. 错误处理

```python
async def process(self, message: NormalizedMessage) -> Optional[NormalizedMessage]:
    try:
        # 处理逻辑
        return message
    except Exception as e:
        self.logger.error(f"管道处理失败: {e}", exc_info=True)
        # 错误时选择：返回 None（丢弃）或返回 message（放行）
        return message
```

### 3. 性能考虑

```python
class ExpensivePipeline(TextPipeline):
    """耗时操作管道"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.cache = {}  # 使用缓存

    async def process(self, message: NormalizedMessage) -> Optional[NormalizedMessage]:
        # 检查缓存
        cache_key = hash(message.text)
        if cache_key in self.cache:
            return self.cache[cache_key]

        # 执行耗时操作
        result = await self._expensive_operation(message)

        # 缓存结果
        self.cache[cache_key] = result

        return result
```

### 4. 可配置性

```python
class ConfigurablePipeline(TextPipeline):
    """可配置管道"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.enabled = config.get("enabled", True)
        self.mode = config.get("mode", "strict")

    async def process(self, message: NormalizedMessage) -> Optional[NormalizedMessage]:
        # 支持动态启用/禁用
        if not self.enabled:
            return message

        # 根据模式选择不同处理逻辑
        if self.mode == "strict":
            return await self._strict_process(message)
        else:
            return await self._lenient_process(message)
```

## 测试

### 单元测试

```python
import pytest
from src.domains.input.pipelines.my_pipeline import MyPipeline
from src.core.base.normalized_message import NormalizedMessage
from src.core.base.user import User

@pytest.mark.asyncio
async def test_my_pipeline():
    config = {"param": "test"}
    pipeline = MyPipeline(config)

    # 创建测试消息
    message = NormalizedMessage(
        text="测试消息",
        source="test",
        user=User(nickname="test_user")
    )

    # 测试处理
    result = await pipeline.process(message)

    # 验证结果
    assert result is not None  # 消息未被过滤
    assert result.text == "预期的文本"

@pytest.mark.asyncio
async def test_my_pipeline_filter():
    config = {"param": "test"}
    pipeline = MyPipeline(config)

    message = NormalizedMessage(
        text="应该被过滤的消息",
        source="test",
        user=User(nickname="test_user")
    )

    result = await pipeline.process(message)

    # 验证消息被过滤
    assert result is None
```

## 调试

### 查看管道执行顺序

```python
# 启用调试模式
uv run python main.py --debug

# 查看日志中的管道执行记录
# [INFO] PipelineManager: 管道执行顺序: rate_limit (50) → profanity_filter (100) → ...
```

### 临时禁用管道

```toml
# 设置 enabled = false
[pipelines.my_pipeline]
enabled = false
priority = 500
```

## 相关文档

- [3域架构总览](../architecture/overview.md)
- [数据流规则](../architecture/data-flow.md)
- [Provider 开发](provider-guide.md)

---

*最后更新：2026-02-09*
