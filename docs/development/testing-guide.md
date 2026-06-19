# 测试指南

本指南介绍如何在 Amaidesu 项目中编写和运行测试。

## 1. 测试框架

项目使用 [pytest](https://docs.pytest.org/) 作为测试框架，配合 [pytest-asyncio](https://pytest-asyncio.readthedocs.io/) 支持异步测试。

### 依赖配置

测试依赖已在 `pyproject.toml` 中配置：

```toml
[project.optional-dependencies]
test = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
    "pytest-cov>=4.1.0",
]
```

## 2. 测试目录结构

```
tests/
├── architecture/           # 架构约束测试
│   ├── test_dependency_direction.py
│   └── test_event_flow_constraints.py
├── core/                   # 核心模块测试
│   ├── base/               # 基础类型测试
│   │   ├── test_input_collector.py
│   │   ├── test_output_handler.py
│   │   └── test_pipeline_stats.py
│   ├── config/             # 配置测试
│   ├── events/             # 事件系统测试
│   ├── test_event_bus.py
│   └── test_logger.py
├── domains/                # 阶段测试
│   ├── input/
│   │   ├── pipelines/      # Input Pipeline 测试
│   │   │   ├── test_rate_limit_pipeline.py
│   │   │   └── test_similar_filter_pipeline.py
│   │   ├── collectors/      # InputCollector 测试
│   │   └── test_input_collector_manager.py
│   ├── decision/
│   │   ├── deciders/     # Decider 测试
│   │   └── test_decider_manager.py
│   └── output/
│       ├── pipelines/     # Output Pipeline 测试
│       ├── handlers/     # OutputHandler 测试
│       │   ├── avatar/
│       │   ├── test_sticker_handler.py
│       │   └── test_obs_control_handler.py
│       └── test_output_handler_manager.py
├── integration/            # 集成测试
├── mocks/                  # Mock 对象
│   ├── mock_input_collector.py
│   ├── mock_output_handler.py
│   └── mock_decider.py
├── prompts/                # 提示词测试
├── services/               # 服务测试
│   ├── config/
│   ├── context/
│   ├── llm/
│   └── tts/
├── conftest.py             # 全局共享 fixtures
└── __init__.py
```

### 命名规范

| 类型 | 命名规范 | 示例 |
|------|---------|------|
| 测试文件 | `test_*.py` | `test_event_bus.py` |
| 测试函数 | `async def test_*():` | `async def test_event_publish():` |
| 测试类 | `Test*` | `class TestEventBus:` |
| Fixture | `*_fixture` 或直接用功能名 | `event_bus`, `sample_providers` |

## 3. 测试示例

### 3.1 基础测试结构

```python
"""
测试模块名称

运行: uv run pytest tests/path/to/test_file.py -v
"""

import asyncio
import pytest

from src.modules.events.event_bus import EventBus
from src.modules.types.base.normalized_message import NormalizedMessage


# =============================================================================
# Fixtures - 测试依赖
# =============================================================================


@pytest.fixture
def event_bus():
    """创建 EventBus 实例"""
    return EventBus()


@pytest.fixture
def sample_message():
    """创建示例 NormalizedMessage"""
    return NormalizedMessage(
        text="测试消息",
        source="test",
        data_type="text",
        importance=0.5,
    )


# =============================================================================
# 测试用例
# =============================================================================


@pytest.mark.asyncio
async def test_event_bus_publish(event_bus, sample_message):
    """测试事件总线发布订阅功能"""
    received = []

    async def handler(event_name: str, payload: NormalizedMessage, source: str):
        received.append(payload)

    # 订阅事件
    event_bus.on("test.event", handler, NormalizedMessage)

    # 发布事件
    await event_bus.emit("test.event", sample_message, source="test")
    await asyncio.sleep(0.1)  # 等待异步处理

    # 验证结果
    assert len(received) == 1
    assert received[0].text == "测试消息"


@pytest.mark.asyncio
async def test_event_bus_error_isolation(event_bus):
    """测试错误隔离功能"""
    results = []

    async def failing_handler(event_name, payload, source):
        results.append("before_error")
        raise ValueError("模拟错误")

    async def normal_handler(event_name, payload, source):
        results.append("normal")

    event_bus.on("test.event", failing_handler, NormalizedMessage, priority=10)
    event_bus.on("test.event", normal_handler, NormalizedMessage, priority=20)

    # 启用错误隔离
    await event_bus.emit("test.event", NormalizedMessage(
        text="test", source="test", data_type="text", importance=0.5
    ), source="test", error_isolate=True)
    await asyncio.sleep(0.1)

    # 验证两个处理器都执行了
    assert "before_error" in results
    assert "normal" in results
```

### 3.2 阶段参与者测试

```python
"""测试 InputCollector

运行: uv run pytest tests/domains/input/test_input_collector_manager.py -v
"""

import asyncio
import pytest

from src.domains.input.collector_manager import InputCollectorManager
from src.modules.events.names import CoreEvents
from src.modules.types.base.normalized_message import NormalizedMessage
from tests.mocks.mock_input_collector import MockInputCollector


class FailingMockCollector(MockInputCollector):
    """模拟失败的 Collector"""

    async def start(self):
        raise RuntimeError("启动失败")


@pytest.fixture
def collector_manager(event_bus):
    """创建 InputCollectorManager 实例"""
    return InputCollectorManager(event_bus)


@pytest.mark.asyncio
async def test_collector_start_and_stop(collector_manager):
    """测试 Collector 启动和停止"""
    collector = MockInputCollector({"name": "test_collector", "auto_exit": True})

    await collector_manager.start_all_collectors([collector])
    assert collector_manager._is_started is True

    await collector_manager.stop_all_collectors()
    assert collector_manager._is_started is False


@pytest.mark.asyncio
async def test_collector_data_flow(collector_manager, event_bus):
    """测试 Collector 数据流"""
    collected = []

    async def on_message(event_name, payload, source):
        collected.append(payload)

    event_bus.on(CoreEvents.DATA_MESSAGE, on_message, model_class=NormalizedMessage)

    collector = MockInputCollector({"name": "test_collector"})
    await collector_manager.start_all_collectors([collector])

    # 添加测试数据
    msg = NormalizedMessage(
        text="测试",
        source="test",
        data_type="text",
        importance=0.5,
    )
    collector.add_test_data(msg)

    await asyncio.sleep(0.3)

    assert len(collected) == 1
    assert collected[0].message.text == "测试"

    await collector_manager.stop_all_collectors()


@pytest.mark.asyncio
async def test_error_isolation(collector_manager):
    """测试错误隔离"""
    collectors = [
        MockInputCollector({"name": "good_collector"}),
        FailingMockCollector({"name": "failing_collector"}),
    ]

    await collector_manager.start_all_collectors(collectors)

    # 单个 Collector 失败不应影响整体
    assert collector_manager._is_started is True

    await collector_manager.stop_all_collectors()
```

### 3.3 Pipeline 测试

```python
"""测试 RateLimitInputPipeline

运行: uv run pytest tests/domains/input/pipelines/test_rate_limit_pipeline.py -v
"""

import pytest

from src.domains.input.pipelines.rate_limit.pipeline import RateLimitInputPipeline
from src.modules.types.base.normalized_message import NormalizedMessage


@pytest.fixture
def rate_limit_pipeline():
    """创建限流管道实例"""
    config = {
        "global_rate_limit": 10,
        "user_rate_limit": 3,
        "window_size": 60,
    }
    return RateLimitInputPipeline(config)


def create_message(text: str, user_id: str = "test_user") -> NormalizedMessage:
    """创建测试用的 NormalizedMessage"""
    return NormalizedMessage(text=text, source="test", user_id=user_id)


def test_pipeline_creation(rate_limit_pipeline):
    """测试管道创建"""
    assert rate_limit_pipeline._global_rate_limit == 10
    assert rate_limit_pipeline._user_rate_limit == 3


@pytest.mark.asyncio
async def test_process_message_pass(rate_limit_pipeline):
    """测试消息通过限流"""
    message = create_message("测试消息")
    result = await rate_limit_pipeline._process(message)
    assert result == message


@pytest.mark.asyncio
async def test_process_message_rate_limited(rate_limit_pipeline):
    """测试消息被限流"""
    config = {"global_rate_limit": 2, "user_rate_limit": 10, "window_size": 60}
    pipeline = RateLimitInputPipeline(config)

    # 前两条消息通过
    assert await pipeline._process(create_message("消息1", "user1")) is not None
    assert await pipeline._process(create_message("消息2", "user1")) is not None

    # 第三条消息被限流
    result = await pipeline._process(create_message("消息3", "user1"))
    assert result is None
```

### 3.4 Mock 对象

项目在 `tests/mocks/` 目录中提供了常用的 Mock 对象：

```python
from tests.mocks.mock_input_collector import MockInputCollector
from tests.mocks.mock_output_handler import MockOutputHandler
from tests.mocks.mock_decider import MockDecider

# 使用 MockInputCollector 进行测试
collector = MockInputCollector({"name": "test"})
collector.add_test_data(normalized_message)
```

#### 自定义 Mock Collector

```python
class CustomMockInputCollector(MockInputCollector):
    """自定义 Mock Collector"""

    def __init__(self, config=None, fail_on_start=False):
        super().__init__(config)
        self.fail_on_start = fail_on_start

    async def start(self):
        if self.fail_on_start:
            raise RuntimeError("模拟启动失败")
        async for data in super().start():
            yield data
```

## 4. 运行测试

### 基本命令

```bash
# 运行所有测试
uv run pytest tests/

# 运行特定测试文件
uv run pytest tests/core/test_event_bus.py

# 运行特定测试函数
uv run pytest tests/core/test_event_bus.py::test_on_register_handler

# 详细输出（显示打印语句）
uv run pytest tests/ -v -s

# 显示失败的详细信息
uv run pytest tests/ -v --tb=long
```

### 测试过滤

```bash
# 排除慢速测试
uv run pytest -m "not slow"

# 只运行特定标记的测试
uv.mark.asyncio
async def test_xxx():
    ...

# 运行带特定标记的测试
uv run pytest -m asyncio
```

### 覆盖率

```bash
# 生成覆盖率报告
uv run pytest --cov=src tests/

# 生成 HTML 覆盖率报告
uv run pytest --cov=src --cov-report=html tests/

# 查看覆盖率报告
uv run python -m http.server 8000 --directory htmlcov
```

### 其他选项

```bash
# 失败时立即停止
uv run pytest -x

# 显示本地变量（调试用）
uv run pytest -l

# 并行执行（需要安装 pytest-xdist）
uv run pytest -n auto
```

## 5. 测试最佳实践

### 5.1 测试命名和文档

```python
# ✅ 正确：清晰描述测试内容
@pytest.mark.asyncio
async def test_event_bus_publish_message_to_multiple_subscribers(event_bus):
    """测试事件总线向多个订阅者发布消息"""
    ...

# ❌ 错误：模糊的测试名称
@pytest.mark.asyncio
async def test_eb(event_bus):
    """test eb"""
    ...
```

### 5.2 使用 Fixture 管理依赖

```python
# ✅ 正确：使用 fixture 创建依赖
@pytest.fixture
def event_bus():
    return EventBus()

@pytest.mark.asyncio
async def test_event(event_bus):  # 自动注入
    ...

# ❌ 错误：在测试函数内部创建依赖
@pytest.mark.asyncio
async def test_event():
    event_bus = EventBus()  # 每次都创建新的
    ...
```

### 5.3 异步测试

```python
# ✅ 正确：使用 @pytest.mark.asyncio 装饰器
@pytest.mark.asyncio
async def test_async_operation():
    result = await async_function()
    assert result is not None

# ✅ 正确：等待异步操作完成
@pytest.mark.asyncio
async def test_event_handling(event_bus):
    await event_bus.emit("test", payload, source="test")
    await asyncio.sleep(0.1)  # 等待异步处理器执行
    assert received
```

### 5.4 Mock 外部依赖

```python
# ✅ 正确：使用 Mock 对象隔离外部依赖
from unittest.mock import AsyncMock, MagicMock

@pytest.mark.asyncio
async def test_llm_manager():
    mock_backend = AsyncMock()
    mock_backend.generate.return_value = "Mock response"

    manager = LLMManager()
    result = await manager.generate("prompt", backend=mock_backend)
    assert result == "Mock response"
```

### 5.5 测试隔离

```python
# ✅ 正确：每个测试独立，不依赖执行顺序
@pytest.mark.asyncio
async def test_first():
    collector = MockInputCollector({"name": "test"})
    await collector.start()
    # 测试逻辑
    await collector.stop()

@pytest.mark.asyncio
async def test_second():  # 独立运行，不依赖 test_first
    collector = MockInputCollector({"name": "test"})
    ...
```

### 5.6 跳过测试

```bash
# 使用 pytest.skip 跳过需要特定条件的测试
@pytest.mark.asyncio
async def test_feature_requiring_config():
    if not has_config():
        pytest.skip("需要配置文件")

    # 测试逻辑
    ...
```

## 6. 测试类型

### 6.1 单元测试

测试单个组件（Collector、Pipeline、Manager）的功能。

```python
# tests/domains/input/pipelines/test_rate_limit_pipeline.py
def test_rate_limit_pipeline_creation():
    """测试管道创建"""
    pipeline = RateLimitInputPipeline(config)
    assert pipeline is not None
```

### 6.2 集成测试

测试多个组件协作。

```python
# tests/integration/test_mock_danmaku_schema_migration.py
@pytest.mark.asyncio
async def test_collector_manager_with_pipeline():
    """测试 CollectorManager 与 Pipeline 集成"""
    manager = InputCollectorManager(event_bus)
    pipeline = RateLimitInputPipeline(config)
    manager.add_pipeline(pipeline)
    ...
```

### 6.3 架构测试

验证架构约束（数据流方向、依赖关系）。

```python
# tests/architecture/test_dependency_direction.py
def test_core_does_not_import_domain():
    """验证 Core 层不依赖 Domain 层"""
    ...
```

## 7. Fixtures 共享

### 7.1 全局 Fixtures

在 `tests/conftest.py` 中定义全局共享的 fixtures：

```python
# tests/conftest.py
@pytest.fixture
async def event_bus() -> EventBus:
    """创建干净的 EventBus 实例"""
    bus = EventBus()
    yield bus
    await bus.cleanup()
```

### 7.2 阶段特定 Fixtures

在 `tests/domains/*/conftest.py` 中定义特定阶段的 fixtures：

```python
# tests/domains/input/conftest.py
@pytest.fixture
def sample_collectors():
    """创建示例 Collector 列表"""
    return [
        MockInputCollector({"name": "collector1"}),
        MockInputCollector({"name": "collector2"}),
    ]
```

## 8. 调试测试

### 查看日志输出

```bash
# 显示所有日志
uv run pytest tests/ -v -s --log-cli-level=DEBUG

# 过滤特定模块的日志
uv run pytest tests/ -v --log-cli-level=DEBUG -k test_event_bus
```

### 断点调试

```python
import pytest

@pytest.mark.asyncio
async def test_debug():
    result = await some_operation()
    # 设置断点
    import pdb; pdb.set_trace()
    assert result
```

运行：
```bash
uv run pytest tests/test_file.py::test_debug -v -s
```

## 9. 常见问题

### 9.1 异步测试超时

如果遇到异步测试超时错误：

```python
# 增加超时时间
@pytest.mark.asyncio(timeout=30)  # 30 秒超时
async def test_slow_operation():
    ...
```

### 9.2 Fixture 循环依赖

确保 fixture 之间没有循环依赖：

```python
# ✅ 正确：依赖链清晰
@pytest.fixture
def manager(event_bus):
    return Manager(event_bus)

# ❌ 错误：循环依赖
@pytest.fixture
def manager(provider):
    return Manager(provider)

@pytest.fixture
def provider(manager):
    return Provider(manager)
```

### 9.3 事件总线清理

确保每个测试后清理事件总线：

```python
@pytest.fixture
async def event_bus():
    bus = EventBus()
    yield bus
    await bus.cleanup()  # 清理所有订阅
```

## 10. 相关文档

- [开发规范](development-guide.md) - 代码风格和数据类型规范
- [阶段参与者开发](development/provider-guide.md) - 阶段参与者开发指南
- [管道开发](development/pipeline-guide.md) - Pipeline 开发指南
- [事件系统](../architecture/event-system.md) - EventBus 使用指南

---

*最后更新：2026-06-19*
