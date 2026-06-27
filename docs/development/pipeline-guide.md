# Pipeline 开发指南

本文档详细介绍 Amaidesu 项目的 Pipeline（管道）开发规范。

## 概述

Pipeline 是一种消息处理机制，用于在数据流经 3 阶段架构时对数据进行过滤、转换或预处理。所有 Pipeline 统一继承 `Pipeline[T]` 泛型基类：

| 阶段 | 处理对象 | 类型参数 | 触发位置 |
|------|----------|---------|---------|
| **Input Pipeline** | `NormalizedMessage` | `Pipeline[NormalizedMessage]` | `InputCollectorManager._run_collector()` |
| **Output Pipeline** | `Intent` | `Pipeline["Intent"]` | `OutputHandlerManager._on_decision_intent()` |

## 1. 创建 Pipeline

### 1.1 文件位置

| 阶段 | 路径 | 示例 |
|------|------|------|
| Input | `src/stages/input/pipelines/<name>/pipeline.py` | `rate_limit/pipeline.py` |
| Output | `src/stages/output/pipelines/<name>/pipeline.py` | `profanity_filter/pipeline.py` |

### 1.2 装饰器注册

每个 Pipeline 类必须用 `@pipeline("name")` 装饰器显式注册，stage 由基类自动推断：

```python
from src.modules.pipeline import pipeline, Pipeline
from src.modules.types.base.normalized_message import NormalizedMessage
from typing import Optional, Any, Dict


@pipeline("rate_limit")
class RateLimitInputPipeline(Pipeline[NormalizedMessage]):
    """限流管道。基于滑动时间窗口限制消息频率。"""

    priority = 100

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._rate = config.get("rate", 100)

    async def _process(self, item: NormalizedMessage) -> Optional[NormalizedMessage]:
        if self._is_over_limit():
            return None  # 返回 None = 丢弃
        return item
```

装饰器规则：
- 必须传入 `name`（在配置 `[pipelines.<stage>.<name>]` 中使用）
- `priority` 写在类属性中（数字越小越先执行）
- 不允许重复注册（`(stage, name)` 唯一）
- 抽象类自动跳过注册

### 1.3 配置启用

在 `config/core.toml`（或被加载的配置文件中）：

```toml
[pipelines.input.rate_limit]
enabled = true
priority = 100        # 可覆盖类属性默认值
rate = 100

[pipelines.output.profanity_filter]
enabled = true
priority = 100
words = ["bad1", "bad2"]
replacement = "***"
```

`PipelineItemConfig` Schema 校验：

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `priority` | int | 500 | 优先级（必填，配置错误立即报错） |
| `enabled` | bool | true | 是否启用 |
| `error_handling` | enum | "continue" | CONTINUE / STOP / DROP |
| `timeout_seconds` | float | 5.0 | 单次处理超时 |
| 其他 | any | - | 子类自定义字段（`extra="allow"`） |

## 2. 进程契约

`_process()` 方法必须遵守：

| 返回值 | 含义 |
|--------|------|
| 原 `item` | 透传 |
| `item.model_copy(update={...})` 或新对象 | 转换 |
| `None` | 丢弃整条消息（后续 Pipeline 不执行） |

行为契约：
- 错误处理策略默认 CONTINUE（记录日志继续执行下一个 Pipeline）
- 第一处返回 `None` 短路整条链路
- 每次处理累加统计（processed_count / dropped_count / error_count / total_duration_ms）

## 3. 依赖注入

Pipeline 不需要 Context 容器。如需注入服务（如 LLMManager），直接在 `__init__` 声明：

```python
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from src.modules.llm.manager import LLMManager


@pipeline("llm_filter")
class LLMFilterOutputPipeline(Pipeline["Intent"]):
    def __init__(self, config: Dict[str, Any], llm_service: "LLMManager"):
        super().__init__(config)
        self._llm = llm_service
```

`PipelineManager._instantiate_pipeline()` 会通过反射 `__init__` 签名自动注入：

```python
# main.py
output_pipeline_manager = await create_pipeline_manager(
    stage="output",
    config=config,
    services_by_type={
        LLMManager: llm_service,
        PromptManager: prompt_manager,
    },
)
```

## 4. 测试

```python
import pytest
from src.modules.pipeline import Pipeline


class TestPipeline(Pipeline[NormalizedMessage]):
    async def _process(self, item):
        return item


@pytest.mark.asyncio
async def test_pipeline():
    pipeline = TestPipeline(config={"priority": 100})
    msg = create_test_message()
    result = await pipeline.process(msg)
    assert result is not None
```

测试隔离：使用 `clear_registry()` 清理装饰器副作用。

## 5. 已实现 Pipeline

| 名称 | 阶段 | Priority | 功能 |
|------|------|----------|------|
| `rate_limit` | Input | 100 | 滑动时间窗口限流（全局 + 用户级） |
| `similar_filter` | Input | 500 | 相似文本去重 |
| `profanity_filter` | Output | 100 | 敏感词过滤 |

## 6. 反模式

**禁止**：
- ❌ 创建新的 Plugin（插件系统已移除，使用 Pipeline）
- ❌ 使用 Context 容器传递服务
- ❌ 硬编码事件名字符串（使用 `CoreEvents` 常量）
- ❌ 使用空的 except 块
- ❌ 在 Pipeline 内修改全局状态
- ❌ 给 Pipeline 加 `setup()`/`cleanup()` 方法

详见 [依赖注入指南](dependency-injection.md)。