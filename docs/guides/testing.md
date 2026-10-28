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

测试按 v2 布局组织（`modules/` 框架模块 + `agents/` 业务 Agent），目录结构与 `src/` 镜像对应。**归属规则与测试编写纪律见 [tests/AGENTS.md](../../tests/AGENTS.md)**（AI 编码代理的执行约束）；**权威且完整的目录结构见 [tests/README.md](../../tests/README.md#目录结构)**（位于测试目录内，随代码演进维护）：

```
tests/
├── architecture/           # 架构约束测试（分层依赖 / 事件流约束）
├── agents/                 # 业务 Agent 测试（镜像 src/agents/：streamer / minecraft / text_adv / game_2048）
├── config/                 # 配置系统测试
├── dashboard/              # Dashboard API 与服务
├── integration/            # 集成测试
└── modules/                # 模块层测试（镜像 src/modules/）
    ├── agents/             # Agent 框架（base / manager / supervisor / delegation / factory / companions）
    ├── collectors/         # bilibili / console / stt
    ├── config/             # 配置加载 / 回归修复
    ├── events/             # EventBus / 拦截器 / Payload 注册表（含 test_interceptors.py）
    ├── llm/                # LLMManager 与客户端
    ├── storage/            # SQLite 存储层
    ├── tools/              # 工具契约（ToolSpec / Registry / as_tool_impl）
    │   └── output/         # 渲染工具（remote_stream）
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

### 文件组织与共享替身

- **按域合并，不开微文件**：同域内主题相近的用例合并到少数文件（如事件域按总线 / 注册表 / 载荷 / 拦截器各一文件），不"一个类一个测试文件"；新增用例先找现有文件并入，主题确实独立才新建。
- **共享替身与构造器进 `helpers.py` / `fakes.py`**：跨测试文件复用的替身（fake）、工厂构造器收拢到该域的 `helpers.py` 或 `fakes.py`，写测试前先查该域现有文件复用，勿复制装配样板；pytest fixture 仍按作用域放 `conftest.py`（见 §7.2）。
- **合并 / 移动测试文件后同步文档**：本文档与其他指南点名的测试路径随之更新，不留指向不存在文件的引用。

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
from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser


@pytest.fixture
def event_bus():
    """创建 EventBus 实例"""
    return EventBus()


@pytest.fixture
def sample_message():
    """创建示例 RoomMessagePayload"""
    return RoomMessagePayload(
        message_type="danmaku",
        user=RoomMessageUser(id="u1", name="观众A"),
        content="测试消息",
    )


async def test_event_bus_publish(event_bus, sample_message):
    """测试事件总线发布订阅功能"""
    received = []

    async def handler(event_name: str, payload: RoomMessagePayload, source: str):
        received.append(payload)

    # 订阅事件
    event_bus.on("test.event", handler, RoomMessagePayload)

    # 发布事件
    await event_bus.emit("test.event", sample_message, source="test")
    await event_bus.wait_until_drained()  # 等派发落地（含级联），确定性等待

    # 验证结果
    assert len(received) == 1
    assert received[0].content == "测试消息"


async def test_event_bus_error_isolation(event_bus):
    """测试错误隔离功能"""
    results = []

    async def failing_handler(event_name, payload, source):
        results.append("before_error")
        raise ValueError("模拟错误")

    async def normal_handler(event_name, payload, source):
        results.append("normal")

    event_bus.on("test.event", failing_handler, RoomMessagePayload)
    event_bus.on("test.event", normal_handler, RoomMessagePayload)

    # 处理器异常在 EventBus 内部闭环（计数 + ERROR 日志），不向外传播
    await event_bus.emit("test.event", RoomMessagePayload(
        message_type="danmaku", user=RoomMessageUser(id="u1", name="观众A"), content="test"
    ), source="test")
    await event_bus.wait_until_drained()

    # 验证一个处理器抛异常没有影响另一个
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
    """最小可测的 Collector 子类（与 tests/modules/collectors/test_collector_framework.py 中同形态）"""
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


async def test_collector_start_lifecycle(sample_collector):
    """Collector start/stop 生命周期（start 进入 RUNNING，stop 进入 STOPPED）"""
    from src.modules.collectors.base import CollectorState

    await sample_collector.start()
    assert sample_collector.state == CollectorState.RUNNING

    await sample_collector.stop()
    assert sample_collector.state == CollectorState.STOPPED
```

> 真实触发链与各子采集器（console/bilibili/stt）的差异化测试见 [tests/modules/collectors/](../../tests/modules/collectors/)：每个采集器一个测试文件，覆盖该采集器特有的输入源与语义域事件组合。完整的错误隔离与 `CollectorManager` 健康监控测试见 `tests/modules/collectors/test_collector_framework.py`。

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


async def test_intercept_pass(interceptor):
    """测试事件通过限流"""
    result = await interceptor.intercept("room.message.danmaku", create_payload("测试消息"), "Test")
    assert result is not None


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
    """最小可测的 Collector 子类（与 tests/modules/collectors/test_collector_framework.py 同步）"""
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

更完整的错误隔离测试与 `CollectorManager` 健康监控见 `tests/modules/collectors/test_collector_framework.py`。

## 4. 运行测试

### 基本命令

```bash
# 运行所有测试
uv run pytest tests/

# 并行运行所有测试（pytest-xdist；本机 32 worker 约 1 分钟）
uv run pytest tests/ -n auto

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
async def test_event_bus_publish_message_to_multiple_subscribers(event_bus):
    """测试事件总线向多个订阅者发布消息"""
    ...

# ❌ 错误：模糊的测试名称
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

async def test_event(event_bus):  # 自动注入
    ...

# ❌ 错误：在测试函数内部创建依赖
async def test_event():
    event_bus = EventBus()  # 每次都创建新的
    ...
```

### 5.3 异步测试与结果断言

项目启用 `asyncio_mode = "auto"`，异步测试直接写 `async def`，**不加 `@pytest.mark.asyncio`**——auto 模式下该标记冗余，误挂到同步测试还会产生 PytestWarning：

```python
# ✅ 正确：无需任何标记
async def test_async_operation():
    result = await async_function()
    assert result is not None
```

异步 / 事件驱动的结果断言**等确定性信号，不用固定睡眠猜进度**：fire-and-forget 派发在满载运行时固定 sleep 可能赶不上，是偶发红的主要来源。

- 等 EventBus 派发落地：`await bus.wait_until_drained()`（src/modules/events；传递性等待在途与级联派发全部完成）；bus 藏在被测对象内部拿不到时优先给测试接出 bus，而不是退化回 sleep；
- 等其他条件（不经过 bus 的后台动作）：轮询到条件成立，带超时上限：

```python
# ✅ 正确：轮询到条件成立，带超时上限
async def _until(condition, timeout: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not condition() and loop.time() < deadline:
        await asyncio.sleep(0.005)

async def test_health_event_published():
    await registry.invoke(...)
    await _until(lambda: len(events) == 1)
    assert events[-1].state == "open"
```

断言"没有事件"的场景才允许固定等待（等满时间窗口确认没发生）。等待上限按全量回归满载运行估算余量，不留贴着正常耗时的紧上限。

### 5.4 Mock 外部依赖

```python
# ✅ 正确：使用 Mock 对象隔离外部依赖
from unittest.mock import AsyncMock, MagicMock

# 示例：Mock LLM 客户端
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
async def test_first(event_bus):
    collector = MockInputCollector({"name": "test"}, event_bus)
    await collector.start()
    # 测试逻辑
    await collector.stop()

async def test_second(event_bus):  # 独立运行，不依赖 test_first
    collector = MockInputCollector({"name": "test"}, event_bus)
    ...
```

### 5.6 跳过测试

```python
# 使用 pytest.skip 跳过需要特定条件的测试
async def test_feature_requiring_config():
    if not has_config():
        pytest.skip("需要配置文件")

    # 测试逻辑
    ...
```

### 5.7 重复场景参数化

同一流程多组输入输出，用一张参数表，**禁止复制粘贴成长手写**（真实先例：`tests/modules/llm/clients/test_openai_client.py` 的 `test_auth_modes` 将四种鉴权方式并为一条用例）：

```python
@pytest.mark.parametrize(
    ("word", "masked"),
    [
        ("傻逼", "**"),
        ("hello", "hello"),
    ],
)
def test_word_filter_masks(word: str, masked: str) -> None:
    assert mask(word) == masked
```

断言差异也用参数表达，只有控制流真正不同才拆独立测试函数。同文件内命名成族（`test_x_includes_*` / `test_x_omits_*` 一类）的多条用例，是应当并表的信号。

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

`tests/integration/` 验证 Amaidesu 与外部宿主（如 MaiBot）的集成边界。当前主用例：`test_simulator_wiring.py`（组合根装配契约——`[simulator]` 启用/自启三种组合下的 SimulatorService 装配路径）与 `test_persona_pipeline.py`（人设供应链端到端防回退——配置→提示词的注入与决策/表达分离契约）。

跨组件协作的 EventBus / 拦截器 / 采集器链路测试已下沉到 `tests/modules/events/test_interceptors.py` 与 `tests/agents/`，不属于本目录。

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

### 6.4 配置迁移测试（表驱动）

配置迁移测试收拢在 `tests/config/test_migrations.py`：一个 `Case`（`id` / `files` / `seed` / `check`）描述"怎样把默认配置拨回旧布局"（seed）与"迁移后应是什么样"（check），两条通用参数化断言对所有 Case 执行——迁移生效落盘且版本推进到钩子链尾；二次加载零漂移零写回（幂等）。

- 版本断言一律用 `tests/config/helpers.py` 的 `tail_version(file)` 从钩子注册表（`_FILE_HOOKS`）推导，**禁止硬编码版本串**——否则每次新增钩子都会撞断言；
- 在 `src/modules/config/upgrade.py` 注册新钩子时，必须在 `CASES` 表补对应 Case（迁移测试是配置数据变换硬约束的落地形态，见 [AGENTS.md](../../AGENTS.md) 配置与存储变更节）；
- 复杂钩子的额外场景（多步、跨文件、边界值）在该文件内以独立测试补充。

不再为每个钩子新建独立迁移测试文件；新增 Case 以文件内现有 Case 为模板。

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

### 7.2 子域共享

pytest fixture 按作用域放 `conftest.py`：全局在 `tests/conftest.py`，子域共享时在该域新建（现有如 `tests/dashboard/conftest.py`、`tests/modules/llm/conftest.py`）。

非 fixture 的共享替身（fake）与工厂构造器**不放 conftest**，放该域 `helpers.py` / `fakes.py` 供同域各测试文件 import 复用（示例：`tests/modules/llm/fakes.py`、`tests/modules/mcp/fakes.py`、`tests/config/helpers.py`），规范见 §2"文件组织与共享替身"。

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

### 9.1 异步测试偶发超时

异步测试偶发超时先分清两类：**结果没等到**（fire-and-forget 派发慢于固定睡眠）改用 §5.3 的确定性等待（EventBus 派发用 `wait_until_drained()`，其余条件用带超时轮询）；**操作真的慢**则放宽该用例自己的等待上限（按全量回归满载估算余量），不要为迁就测试去压缩生产路径的时序参数。

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

- [开发规范](../../AGENTS.md#代码约定) - 代码风格和数据类型规范
- [组件开发指南](component.md) - 组件三范式开发指南
- [事件拦截器](../architecture/event-system.md#事件拦截器interceptor) - 事件拦截器开发指南
- [事件系统](../architecture/event-system.md) - EventBus 使用指南
