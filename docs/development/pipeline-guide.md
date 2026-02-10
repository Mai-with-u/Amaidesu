# 管道开发指南

管道（Pipeline）用于在消息处理流程中进行预处理，位于 Input Domain 内部。

## 管道概述

### 管道作用

管道处理文本和元数据，实现：
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
- **回滚支持**：支持 PipelineContext 回滚，防止管道失败时数据损坏

## 基本结构

### TextPipeline 接口

```python
from src.domains.input.pipelines.manager import TextPipelineBase, PipelineContext
from typing import Optional, Dict, Any

class MyTextPipeline(TextPipelineBase):
    """自定义文本管道"""

    # 管道优先级（数值越小越先执行）
    priority = 500

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.param = self.config.get("param", "default")

    async def _process(
        self,
        text: str,
        metadata: Dict[str, Any],
        context: Optional[PipelineContext] = None
    ) -> Optional[str]:
        """
        处理文本

        返回：
            str: 继续传递处理后的文本
            None: 丢弃消息
        """
        # 处理逻辑
        if self._should_filter(text):
            return None  # 丢弃消息

        # 修改文本
        transformed_text = self._transform(text)

        # 如果需要支持回滚，可以注册回滚动作
        if context is not None:
            async def rollback():
                # 撤销操作
                pass
            context.add_rollback(rollback)

        return transformed_text  # 继续传递

    def _should_filter(self, text: str) -> bool:
        """判断是否过滤消息"""
        return False

    def _transform(self, text: str) -> str:
        """转换文本"""
        return text
```

### 接口方法

| 方法 | 说明 | 必须实现 |
|------|------|----------|
| `_process()` | 处理文本 | ✅ |
| `priority` | 管道优先级 | ✅（类属性） |
| `get_info()` | 获取管道信息 | ❌（可选） |
| `get_stats()` | 获取统计信息 | ❌（有默认实现） |
| `reset_stats()` | 重置统计信息 | ❌（有默认实现） |

## 常见管道模式

### 1. 过滤管道

```python
class ProfanityFilterTextPipeline(TextPipelineBase):
    """敏感词过滤管道"""
    priority = 100

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.bad_words = set(config.get("bad_words", []))

    async def _process(
        self,
        text: str,
        metadata: Dict[str, Any],
        context: Optional[PipelineContext] = None
    ) -> Optional[str]:
        # 检查是否包含敏感词
        for word in self.bad_words:
            if word in text:
                self.logger.info(f"过滤敏感词: {word}")
                return None  # 丢弃消息

        return text  # 继续传递
```

### 2. 限流管道

```python
import asyncio
import time
from collections import defaultdict, deque

class RateLimitTextPipeline(TextPipelineBase):
    """限流管道"""
    priority = 50

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.max_messages = config.get("max_messages", 10)
        self.time_window = config.get("time_window", 60)  # 秒
        self.message_history = deque()
        self._lock = asyncio.Lock()

    async def _process(
        self,
        text: str,
        metadata: Dict[str, Any],
        context: Optional[PipelineContext] = None
    ) -> Optional[str]:
        now = time.time()

        async with self._lock:
            # 清理过期记录
            while self.message_history and now - self.message_history[0] > self.time_window:
                self.message_history.popleft()

            # 检查是否超限
            if len(self.message_history) >= self.max_messages:
                self.logger.warning("触发限流，丢弃消息")
                return None

            # 记录消息
            self.message_history.append(now)

            # 注册回滚动作
            if context is not None:
                async def rollback():
                    if self.message_history and self.message_history[-1] == now:
                        self.message_history.pop()
                context.add_rollback(rollback)

        return text
```

### 3. 转换管道

```python
class TextTransformTextPipeline(TextPipelineBase):
    """文本转换管道"""
    priority = 200

    async def _process(
        self,
        text: str,
        metadata: Dict[str, Any],
        context: Optional[PipelineContext] = None
    ) -> Optional[str]:
        # 转换为小写
        transformed = text.lower()

        # 去除多余空格
        transformed = " ".join(transformed.split())

        return transformed
```

### 4. 相似文本过滤

```python
from difflib import SequenceMatcher
import time
from collections import defaultdict, deque

class SimilarFilterTextPipeline(TextPipelineBase):
    """相似文本过滤管道"""
    priority = 150

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.similarity_threshold = config.get("similarity_threshold", 0.8)
        self.time_window = config.get("time_window", 5.0)
        self.recent_messages: Dict[str, deque] = defaultdict(deque)

    async def _process(
        self,
        text: str,
        metadata: Dict[str, Any],
        context: Optional[PipelineContext] = None
    ) -> Optional[str]:
        user_id = metadata.get("user_id", "unknown")
        now = time.time()

        # 检查与历史消息的相似度
        for recent_msg in self.recent_messages[user_id]:
            cached_time, cached_text = recent_msg
            if now - cached_time > self.time_window:
                break

            similarity = SequenceMatcher(None, text, cached_text).ratio()
            if similarity > self.similarity_threshold:
                self.logger.info(f"过滤相似文本 (相似度: {similarity:.2f})")
                return None

        # 添加到历史记录
        self.recent_messages[user_id].append((now, text))

        # 注册回滚动作
        if context is not None:
            def rollback():
                if self.recent_messages[user_id] and self.recent_messages[user_id][-1] == (now, text):
                    self.recent_messages[user_id].pop()
            context.add_rollback(rollback)

        return text
```

### 5. 消息日志管道

```python
class MessageLoggerTextPipeline(TextPipelineBase):
    """消息日志管道"""
    priority = 1000  # 最后执行

    async def _process(
        self,
        text: str,
        metadata: Dict[str, Any],
        context: Optional[PipelineContext] = None
    ) -> Optional[str]:
        self.logger.info(
            f"消息: {text[:50]}... "
            f"来源: {metadata.get('source', 'N/A')} "
            f"用户: {metadata.get('user_id', 'N/A')}"
        )
        return text
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
  time_window = 5.0
```

### 优先级规则

管道按优先级从小到大执行：

```
priority = 50   (RateLimitTextPipeline)
     ↓
priority = 100  (ProfanityFilterTextPipeline)
     ↓
priority = 150  (SimilarFilterTextPipeline)
     ↓
priority = 200  (TextTransformTextPipeline)
     ↓
priority = 500  (MyTextPipeline)
     ↓
priority = 1000 (MessageLoggerTextPipeline)
```

## 管道目录结构

创建新管道时，需要按照以下目录结构：

```
src/domains/input/pipelines/
├── __init__.py
├── manager.py
├── rate_limit/
│   ├── __init__.py
│   └── pipeline.py
└── similar_filter/
    ├── __init__.py
    └── pipeline.py
```

### 管道命名规范

- 目录名：snake_case (例如 `rate_limit`)
- 类名：PascalCase + `TextPipeline` 后缀 (例如 `RateLimitTextPipeline`)

示例：
- 目录 `rate_limit` → 类名 `RateLimitTextPipeline`
- 目录 `similar_filter` → 类名 `SimilarFilterTextPipeline`
- 目录 `my_pipeline` → 类名 `MyPipelineTextPipeline`

## 管道注册

### 自动注册

管道管理器会自动扫描 `src/domains/input/pipelines/` 目录，加载所有符合条件的管道：

1. 目录结构包含 `__init__.py` 和 `pipeline.py`
2. 配置文件中定义了 `priority`
3. `pipeline.py` 中定义了继承自 `TextPipelineBase` 的类

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
  time_window = 5.0

  [pipelines.my_pipeline]
  priority = 500
  param = "value"
```

## 最佳实践

### 1. 优先级设计

```python
# ✅ 正确：合理的优先级分配
class ProfanityFilterTextPipeline(TextPipelineBase):
    priority = 100  # 优先过滤

class RateLimitTextPipeline(TextPipelineBase):
    priority = 50   # 最先执行，避免处理超限消息

class MessageLoggerTextPipeline(TextPipelineBase):
    priority = 1000  # 最后执行，记录最终状态
```

### 2. 错误处理

```python
async def _process(
    self,
    text: str,
    metadata: Dict[str, Any],
    context: Optional[PipelineContext] = None
) -> Optional[str]:
    try:
        # 处理逻辑
        return text
    except Exception as e:
        self.logger.error(f"管道处理失败: {e}", exc_info=True)
        # 错误时选择：返回 None（丢弃）或返回 text（放行）
        # 根据 error_handling 配置决定
        if self.error_handling == PipelineErrorHandling.DROP:
            return None
        elif self.error_handling == PipelineErrorHandling.CONTINUE:
            return text  # 返回原始文本
        else:
            raise  # STOP 模式，抛出异常
```

### 3. 性能考虑

```python
class ExpensiveTextPipeline(TextPipelineBase):
    """耗时操作管道"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.cache = {}  # 使用缓存

    async def _process(
        self,
        text: str,
        metadata: Dict[str, Any],
        context: Optional[PipelineContext] = None
    ) -> Optional[str]:
        # 检查缓存
        cache_key = hash(text)
        if cache_key in self.cache:
            return self.cache[cache_key]

        # 执行耗时操作
        result = await self._expensive_operation(text)

        # 缓存结果
        self.cache[cache_key] = result

        return result
```

### 4. 可配置性

```python
class ConfigurableTextPipeline(TextPipelineBase):
    """可配置管道"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.enabled = config.get("enabled", True)
        self.mode = config.get("mode", "strict")

    async def _process(
        self,
        text: str,
        metadata: Dict[str, Any],
        context: Optional[PipelineContext] = None
    ) -> Optional[str]:
        # 支持动态启用/禁用
        if not self.enabled:
            return text

        # 根据模式选择不同处理逻辑
        if self.mode == "strict":
            return await self._strict_process(text, metadata)
        else:
            return await self._lenient_process(text, metadata)
```

### 5. 回滚支持

```python
class StatefulTextPipeline(TextPipelineBase):
    """有状态管道，支持回滚"""

    async def _process(
        self,
        text: str,
        metadata: Dict[str, Any],
        context: Optional[PipelineContext] = None
    ) -> Optional[str]:
        # 修改状态
        self._state.append(text)

        # 注册回滚动作
        if context is not None:
            async def rollback():
                # 撤销状态修改
                if self._state and self._state[-1] == text:
                    self._state.pop()
            context.add_rollback(rollback)

        return text
```

## 错误处理策略

Pipeline 支持三种错误处理策略：

| 策略 | 说明 | 配置值 |
|------|------|--------|
| CONTINUE | 记录日志，继续执行下一个管道（使用原始文本） | `"continue"` |
| STOP | 停止执行，回滚所有副作用并抛出异常 | `"stop"` |
| DROP | 丢弃消息，回滚所有副作用 | `"drop"` |

配置示例：

```toml
[pipelines.my_pipeline]
priority = 500
error_handling = "continue"  # 或 "stop" 或 "drop"
timeout_seconds = 5.0
```

## 测试

### 单元测试

```python
import pytest
from src.domains.input.pipelines.my_pipeline.pipeline import MyTextPipeline

@pytest.mark.asyncio
async def test_my_pipeline():
    config = {"param": "test"}
    pipeline = MyTextPipeline(config)

    # 测试处理
    text = "测试消息"
    metadata = {"user_id": "test_user", "source": "test"}

    result = await pipeline.process(text, metadata)

    # 验证结果
    assert result is not None  # 消息未被过滤
    assert result == "预期的文本"

@pytest.mark.asyncio
async def test_my_pipeline_filter():
    config = {"param": "test"}
    pipeline = MyTextPipeline(config)

    text = "应该被过滤的消息"
    metadata = {"user_id": "test_user", "source": "test"}

    result = await pipeline.process(text, metadata)

    # 验证消息被过滤
    assert result is None
```

## 调试

### 查看管道执行顺序

```python
# 启用调试模式
uv run python main.py --debug

# 查看日志中的管道执行记录
# [INFO] InputPipelineManager: TextPipeline 已排序: rate_limit(50) → similar_filter(150) → ...
```

### 获取管道统计信息

```python
# 在代码中获取统计信息
stats = pipeline_manager.get_text_pipeline_stats()
print(stats)
# 输出:
# {
#   "RateLimitTextPipeline": {
#     "processed_count": 100,
#     "dropped_count": 5,
#     "error_count": 0,
#     "avg_duration_ms": 1.23,
#     "enabled": true,
#     "priority": 50
#   },
#   ...
# }
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

*最后更新：2026-02-10*
