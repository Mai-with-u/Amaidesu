# 测试指南

本指南介绍如何在 Amaidesu 项目中编写和运行测试。三份材料的分工：**写测试的纪律**以 [tests/AGENTS.md](../../tests/AGENTS.md) 为权威（AI 编码代理的执行约束）；**操作检查单与假对象地图**在 `testing-conventions` skill（AI 写测试前加载）；本文只讲运行命令、测试类型定位与原理说明。

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
- **共享替身与构造器进 `helpers.py` / `fakes.py`**：跨测试文件复用的替身（fake）、工厂构造器收拢到该域的 `helpers.py` 或 `fakes.py`，写测试前先查该域现有文件复用，勿复制装配样板；pytest fixture 仍按作用域放 `conftest.py`（见"Fixtures 共享"一节的"子域共享"）。
- **合并 / 移动测试文件后同步文档**：本文档与其他指南点名的测试路径随之更新，不留指向不存在文件的引用。

## 3. 示例去哪看

完整示例不再成篇——真实样板以仓库现有测试为准，改动哪个域就照哪个域的现有测试写：

- EventBus / 拦截器 / 载荷注册表：`tests/modules/events/`（`test_event_bus.py`、`test_interceptors.py`）
- 采集器：`tests/modules/collectors/`
- 存储与配置迁移：`tests/modules/storage/`、`tests/config/`

替身（fake）与工厂构造器见各域的 `fakes.py` / `helpers.py`；AI 编码代理的操作检查单与基建地图在 `testing-conventions` skill。

## 4. 运行测试

### 基本命令

```bash
# 全量（并行，进程数见下面"并行开多大"）
uv run pytest tests/ -n 16

# 运行特定测试文件（一两个文件不开并行）
uv run pytest tests/modules/events/test_event_bus.py

# 运行特定测试函数
uv run pytest tests/modules/events/test_event_bus.py::test_on_register_handler

# 详细输出（显示打印语句）
uv run pytest tests/ -v -s

# 显示失败的详细信息
uv run pytest tests/ -v --tb=long
```

### 并行开多大

开并行本身有启动成本：每个测试进程启动时都要导入一遍全局测试配置（`tests/conftest.py`）并各自收集用例，进程越多启动越慢。按要跑的范围选：

- **一两个文件**：不加 `-n`。开并行光启动就比测试本身还慢。
- **一个目录**：`-n 4`。再往上加进程，收益已经很小。
- **全量**：`-n 16`。进程数超过机器的物理核数后反而变慢——本机 24 个物理核，开满（`-n auto`，32 个进程）实测比 16 个慢。换机器后按物理核数重新量。

### 测试过滤

```bash
# 排除慢速测试
uv run pytest -m "not slow"

# 只运行带某个标记的测试
uv run pytest -m slow
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

# 并行执行（pytest-xdist；进程数按上面"并行开多大"选）
uv run pytest tests/ -n 16
```

## 5. 测试最佳实践

### 5.1 使用 Fixture 管理依赖

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

### 5.2 异步测试与结果断言

项目启用 `asyncio_mode = "auto"`：异步测试直接写 `async def`，不加 `@pytest.mark.asyncio`——auto 模式下该标记冗余，误挂到同步测试还会产生 PytestWarning。

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

### 5.3 Mock 外部依赖

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

### 5.4 测试隔离

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

验证架构约束（四层依赖方向 + 事件流约束）。`tests/architecture/` 下两组：

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

迁移测试统一收在该文件内，不为每个钩子新建独立测试文件；新增 Case 以文件内现有 Case 为模板。

### 6.5 提示词规则的在场检查（集中清单）

提示词模板是散文，措辞会正常迭代，测试不逐句锁死。但有一部分规则是某场直播出了事故之后才补进提示词的，被后续重写弄丢时需要有东西提醒——这类检查集中放在 `tests/agents/prompt_rules.py`：一行一条规则，写明出自哪次事故、规则内容、必须出现在哪份提示词里、识别它的最短关键片段；`tests/agents/test_prompt_rules.py` 按清单逐条核对。

- 新增一条事故规则：往清单加一行，关键片段先确认在当前提示词里真实存在；
- 改了提示词措辞导致清单测试红：只更新那一行里的片段；
- 有意撤掉某条规则：删掉那一行，并在提交说明里写明为什么这条事故经验不再需要。

验"代码把数据送进了模型输入"的断言（比如"记忆线索里出现了这位观众"）不在此列，它们测的是数据流，照常写在各自的测试里。提示词与代码是否对得上（占位符、工具名、输出格式）由 `tests/architecture/test_prompt_code_consistency.py` 检查。

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

非 fixture 的共享替身（fake）与工厂构造器**不放 conftest**，放该域 `helpers.py` / `fakes.py` 供同域各测试文件 import 复用（示例：`tests/modules/llm/fakes.py`、`tests/modules/mcp/fakes.py`、`tests/config/helpers.py`），规范见"测试目录结构"一节的"文件组织与共享替身"。

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

异步测试偶发超时先分清两类：**结果没等到**（fire-and-forget 派发慢于固定睡眠）改用"测试最佳实践"一节"异步测试与结果断言"中的确定性等待（EventBus 派发用 `wait_until_drained()`，其余条件用带超时轮询）；**操作真的慢**则放宽该用例自己的等待上限（按全量回归满载估算余量），不要为迁就测试去压缩生产路径的时序参数。

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
- [组件开发指南](component/README.md) - 组件三范式开发指南
- [事件拦截器](../architecture/event-system.md#事件拦截器interceptor) - 事件拦截器开发指南
- [事件系统](../architecture/event-system.md) - EventBus 使用指南
