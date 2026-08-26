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

测试按 v2 布局组织（`modules/` 框架模块 + `agents/` 业务 Agent），目录结构与 `src/` 对应。**权威且完整的目录结构见 [tests/README.md](../../tests/README.md#目录结构)**（位于测试目录内，随代码演进维护）：

```
tests/
├── architecture/           # 架构约束测试（分层依赖 / 事件流约束）
├── agents/                 # 业务 Agent 测试（含 game/text_adv 等）
├── characterization/       # 特征化测试（占位与历史快照）
├── config/                 # 配置系统测试
├── dashboard/              # Dashboard API 与服务
├── integration/            # 集成测试
├── mocks/                  # Mock 对象
└── modules/                # 模块层测试（对应 src/modules/）
    ├── agents/             # Agent 框架 + StreamerAgent 组件
    │   └── streamer/       # planner / replyer / agenda / 决策循环
    ├── base/               # NormalizedMessage 等基类
    ├── collectors/         # bilibili / console / mock / screen / stt
    ├── config/             # 配置 Schema / 升级 hook / 漂移写回
    ├── context/            # ContextAssembler 快照组装
    ├── events/             # EventBus / 拦截器 / Payload 注册表（含 test_interceptors.py）
    ├── llm/                # LLMManager 与客户端
    ├── memory/             # MemoryProvider / SimpleMemory
    ├── storage/            # SQLite 存储层
    ├── tools/              # 工具契约（ToolSpec / Registry / ResultBlock）
    │   └── output/         # 渲染工具（vts / warudo / tts / obs / subtitle…）
    ├── tts/                # TTS 客户端
    └── types/              # 共享类型（bili 消息等）
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
### 3.2 采集器测试

```python
"""测试 BaseCollector / CollectorManager（v2 采集器框架）

运行: uv run pytest tests/modules/collectors/ -v
"""

import pytest

from src.modules.collectors import BaseCollector, CollectorManager
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser


class _FakeEventBus:
    """捕获 emit 的桩（替代真实 EventBus）"""

    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    async def emit(self, event_name: str, payload, **kwargs):
        self.events.append((event_name, payload))


class _SampleCollector(BaseCollector):
    """最小可测的 Collector 子类（与 tests/modules/collectors/test_collector_manager.py 中同形态）"""
    name = "sample_collector"
    description = "示例采集器（用于测试）"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.started = False

    async def _on_start(self) -> None:
        self.started = True

    async def _on_stop(self) -> None:
        self.started = False


@pytest.fixture
def manager() -> CollectorManager:
    return CollectorManager()


@pytest.fixture
def sample_collector() -> _SampleCollector:
    return _SampleCollector()


def test_register_dedup(manager, sample_collector):
    """Collector 注册与去重"""
    assert manager.register(sample_collector) is True
    assert manager.register(sample_collector) is False  # 同名 Collector 跳过


@pytest.mark.asyncio
async def test_collector_emit_room_message(sample_collector):
    """v2：Collector 主动推语义域事件（room.message.danmaku 等）"""
    bus = _FakeEventBus()
    sample_collector.set_event_bus(bus)

    payload = RoomMessagePayload(
        live_session_id="ls_test_001",
        message_type="danmaku",
        user=RoomMessageUser(id="u1", name="观众A"),
        content="主播好可爱！",
    )
    await bus.emit(CoreEvents.ROOM_MESSAGE_DANMAKU, payload)

    assert bus.events[-1][0] == CoreEvents.ROOM_MESSAGE_DANMAKU
    assert bus.events[-1][1].content == "主播好可爱！"


@pytest.mark.asyncio
async def test_collector_start_lifecycle(sample_collector):
    """Collector start/stop 生命周期（start 进入 RUNNING，stop 进入 STOPPED）"""
    from src.modules.collectors.base import CollectorState

    await sample_collector.start()
    assert sample_collector.state == CollectorState.RUNNING

    await sample_collector.stop()
    assert sample_collector.state == CollectorState.STOPPED
```

> 真实触发链与各子采集器（console/bilibili/stt/screen/mock）的差异化测试见 [tests/modules/collectors/](../../tests/modules/collectors/)：每个采集器一个测试文件，覆盖该采集器特有的输入源与语义域事件组合。完整的错误隔离与 `CollectorManager` 健康监控测试见 `tests/modules/collectors/test_collector_manager.py`。

### 3.3 事件拦截器测试

```python
"""测试 RateLimitInterceptor

运行: uv run pytest tests/modules/events/test_interceptors.py -v
"""

import pytest

from src.modules.events.interceptors.rate_limit import RateLimitInterceptor


@pytest.fixture
def interceptor():
    """创建限流拦截器实例"""
    return RateLimitInterceptor(
        global_rate_limit=10,
        user_rate_limit=3,
        window_size=60,
    )


def create_payload(text: str, user_id: str = "test_user") -> dict:
    """创建测试用的事件 payload（model_dump 后的 dict 形态）"""
    return {"text": text, "user_id": user_id, "source": "test"}


@pytest.mark.asyncio
async def test_intercept_pass(interceptor):
    """测试事件通过限流"""
    result = await interceptor.intercept("room.message.danmaku", create_payload("测试消息"), "Test")
    assert result is not None


@pytest.mark.asyncio
async def test_intercept_rate_limited(interceptor):
    """测试超出频率的事件被丢弃"""
    slow = RateLimitInterceptor(global_rate_limit=2, user_rate_limit=10, window_size=60)

    # 前两条通过
    assert await slow.intercept("room.message.danmaku", create_payload("消息1", "user1"), "T") is not None
    assert await slow.intercept("room.message.danmaku", create_payload("消息2", "user1"), "T") is not None

    # 第三条被丢弃（返回 None）
    assert await slow.intercept("room.message.danmaku", create_payload("消息3", "user1"), "T") is None
```
### 3.4 Mock 对象与桩

v2 测试通常在测试文件内定义小型桩，避免跨文件 Mock 依赖。常见两类：

```python
# 在你的测试文件中定义（贴近 tests/modules/collectors/ 现有风格）
from src.modules.collectors import BaseCollector


class _FakeEventBus:
    """捕获 emit 的桩（替代真实 EventBus）"""

    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    async def emit(self, event_name: str, payload, **kwargs):
        self.events.append((event_name, payload))


class _SampleCollector(BaseCollector):
    """最小可测的 Collector 子类（与 tests/modules/collectors/test_collector_manager.py 同步）"""
    name = "sample_collector"
    description = "示例采集器（用于测试）"

    async def _on_start(self) -> None:
        self.started = True

    async def _on_stop(self) -> None:
        self.started = False
```

#### 自定义失败注入

模拟 Collector 启动失败时，在 `_SampleCollector` 子类中重写对应生命周期方法：

```python
class _FailingCollector(_SampleCollector):
    """启动时失败的 Collector（用于错误隔离测试）"""

    async def _on_start(self) -> None:
        raise RuntimeError("模拟启动失败")
```

更完整的错误隔离测试与 `CollectorManager` 健康监控见 `tests/modules/collectors/test_collector_manager.py`；采集器专用 Mock 见 `src/modules/collectors/mock/`（与 v2 测试同处一套 Mock 框架）。

## 4. 运行测试

### 基本命令

```bash
# 运行所有测试
uv run pytest tests/

# 运行特定测试文件
uv run pytest tests/modules/events/test_event_bus.py

# 运行特定测试函数
uv run pytest tests/modules/events/test_event_bus.py::test_on_register_handler

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

# 示例：Mock LLM 客户端
@pytest.mark.asyncio
async def test_llm_client():
    mock_client = AsyncMock()
    mock_client.chat.return_value = "Mock response"

    # 将 mock 注入使用方（示例）
    result = await mock_client.chat("prompt")
    assert result == "Mock response"
```

### 5.5 测试隔离

```python
# ✅ 正确：每个测试独立，不依赖执行顺序
@pytest.mark.asyncio
async def test_first(event_bus):
    collector = MockInputCollector({"name": "test"}, event_bus)
    await collector.start()
    # 测试逻辑
    await collector.stop()

@pytest.mark.asyncio
async def test_second(event_bus):  # 独立运行，不依赖 test_first
    collector = MockInputCollector({"name": "test"}, event_bus)
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

测试单个组件（Collector、Interceptor、Manager）的功能。

```python
# tests/modules/events/test_interceptors.py
def test_rate_limit_interceptor_creation():
    """测试拦截器创建"""
    interceptor = RateLimitInterceptor(global_rate_limit=10)
    assert interceptor is not None
```

### 6.2 集成测试

`tests/integration/` 验证 Amaidesu 与外部宿主（如 MaiBot）的集成边界。当前主用例：

```python
# tests/integration/test_amaidesu_plugin.py
def test_manifest_version():
    """验证 Amaidesu 作为 MaiBot 插件的清单字段（manifest_version / id / sdk.min_version）"""
    ...
```

跨组件协作的 EventBus / 拦截器 / 采集器链路测试已下沉到 `tests/modules/events/test_interceptors.py` 与 `tests/modules/agents/`，不属于本目录。

### 6.3 架构测试

验证架构约束（v2 四层依赖方向 + 事件流约束）。`tests/architecture/` 下两组：

```python
# tests/architecture/test_dependency_direction.py
def test_input_domain_does_not_import_agent_or_tool(self):
    """Input 层（采集器）不得 import Agent 或 Tool"""
    ...

# tests/architecture/test_event_flow_constraints.py
def test_tool_does_not_subscribe_to_input_events(self):
    """工具不得订阅 room.message.* 等数据事件"""
    ...
```

四层依赖方向（Core ← Input ← Agent ← Tool）由 `test_proper_layer_hierarchy` 强制保证；事件流约束（单向、防环）由 `test_event_based_communication_pattern` 验证。

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

### 7.2 模块特定 Fixtures

按需在 `tests/<子域>/conftest.py` 中定义该子域共享的 fixtures。现有 conftest.py 位置：`tests/conftest.py`（全局）、`tests/integration/conftest.py`、`tests/modules/llm/conftest.py`、`tests/modules/tools/output/warudo/conftest.py`。

```python
# tests/modules/<子域>/conftest.py（如该子域需共享 fixture 可新建）
import pytest

from src.modules.collectors import CollectorManager


@pytest.fixture
def manager() -> CollectorManager:
    """CollectorManager 实例（共享给该子域所有测试）"""
    return CollectorManager()
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

- [开发规范](../development-guide.md) - 代码风格和数据类型规范
- [组件开发指南](component-guide.md) - 组件三范式开发指南
- [事件拦截器](../architecture/event-system.md#事件拦截器interceptor) - 事件拦截器开发指南
- [事件系统](../architecture/event-system.md) - EventBus 使用指南

---

*最后更新：2026-08-26（v2.0.0 全面落库——测试目录按 src/modules+src/agents 双层布局重排；移除 `tests/stages/` 整目录与各阶段旧 Mock 文件引用；采集器示例切到 BaseCollector / CollectorManager + 语义域事件 ROOM_MESSAGE_DANMAKU；架构测试与 fixtures 路径切到 v2 四层 Core ← Input ← Agent ← Tool）*
