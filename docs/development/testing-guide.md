# 测试指南

本文档介绍项目的测试规范、工具和最佳实践。

## 测试框架

### 使用 pytest

项目使用 [pytest](https://docs.pytest.org/) 作为测试框架。

```bash
# 安装测试依赖
uv sync --dev

# 运行所有测试
uv run pytest tests/

# 运行特定测试文件
uv run pytest tests/layers/input/test_console_provider.py

# 运行特定测试用例
uv run pytest tests/layers/input/test_console_provider.py::test_console_provider_basic

# 详细输出模式
uv run pytest tests/ -v

# 只运行标记的测试
uv run pytest -m "not slow"
```

### pytest-asyncio

异步测试使用 [pytest-asyncio](https://pytest-asyncio.readthedocs.io/) 插件。

```python
import pytest

@pytest.mark.asyncio
async def test_async_function():
    result = await some_async_function()
    assert result is not None
```

## 测试规范

### 文件和函数命名

| 类型 | 命名规则 | 示例 |
|------|---------|------|
| 测试文件 | `test_*.py` | `test_console_provider.py` |
| 测试函数 | `async def test_*():` | `async def test_provider_init():` |
| 测试类 | `Test*` | `class TestInputProvider:` |

### 基本测试结构

```python
import pytest
from src.domains.input.providers.console_input import ConsoleInputProvider

@pytest.mark.asyncio
async def test_console_provider_init():
    """测试 ConsoleInputProvider 初始化"""
    config = {"name": "test"}
    provider = ConsoleInputProvider(config)

    assert provider is not None
    assert provider.config == config
```

### 断言

使用 `assert` 进行断言：

```python
# ✅ 正确：使用 assert
assert result is not None
assert result.value == "expected"
assert len(items) > 0
assert "error" not in message

# ❌ 错误：使用 unittest 风格的断言
self.assertEqual(result, "expected")
```

## 异步测试

### 使用 pytest-asyncio

```python
import pytest

@pytest.mark.asyncio
async def test_async_function():
    result = await some_async_function()
    assert result is not None

@pytest.mark.asyncio
async def test_with_fixture(event_bus):
    # 使用 fixture
    await event_bus.emit("test.event", data)
    assert True
```

### 使用 asyncio.run（不推荐）

```python
import asyncio

def test_sync_wrapper():
    async def async_test():
        result = await some_async_function()
        assert result is not None

    asyncio.run(async_test())
```

## 测试固件（Fixtures）

### 定义 Fixture

```python
import pytest
from src.core.events.event_bus import EventBus

@pytest.fixture
async def event_bus():
    """创建 EventBus 实例"""
    bus = EventBus()
    yield bus
    # 清理
    await bus.clear()
```

### 使用 Fixture

```python
@pytest.mark.asyncio
async def test_with_event_bus(event_bus):
    """测试使用 EventBus fixture"""
    await event_bus.emit("test.event", data)
    assert True
```

### 内置 Fixture 位置

项目级 fixtures 放在 `tests/conftest.py`：

```python
# tests/conftest.py
import pytest
from src.core.events.event_bus import EventBus

@pytest.fixture
async def global_event_bus():
    """全局 EventBus fixture"""
    bus = EventBus()
    yield bus
    await bus.clear()
```

## Mock 和 Patch

### 使用 unittest.mock

```python
from unittest.mock import Mock, AsyncMock, patch

@pytest.mark.asyncio
async def test_with_mock():
    # 创建 Mock 对象
    mock_provider = Mock()
    mock_provider.decide.return_value = Intent(type="response")

    # 创建 AsyncMock
    async_mock = AsyncMock()
    async_mock.fetch_data.return_value = {"data": "test"}

    result = await async_mock.fetch_data()
    assert result == {"data": "test"}
```

### 使用 patch

```python
from unittest.mock import patch

@pytest.mark.asyncio
async def test_with_patch():
    # Patch 函数
    with patch("src.domains.input.providers.my_provider.fetch_data") as mock_fetch:
        mock_fetch.return_value = {"data": "test"}

        provider = MyInputProvider(config)
        result = await provider._fetch_data()

        assert result == {"data": "test"}
        mock_fetch.assert_called_once()
```

### 使用 monkeypatch（pytest）

```python
def test_with_monkeypatch(monkeypatch):
    # Mock 环境变量
    monkeypatch.setenv("API_KEY", "test_key")

    # Mock 函数
    def mock_get_config():
        return {"key": "value"}

    monkeypatch.setattr("src.config.get_config", mock_get_config)
```

## 测试分类

### 标记测试

```python
import pytest

@pytest.mark.slow
def test_slow_operation():
    """标记为慢速测试"""
    pass

@pytest.mark.integration
def test_integration():
    """标记为集成测试"""
    pass

@pytest.mark.unit
def test_unit():
    """标记为单元测试"""
    pass
```

### 运行特定标记的测试

```bash
# 只运行慢速测试
uv run pytest -m slow

# 排除慢速测试
uv run pytest -m "not slow"

# 运行多个标记
uv run pytest -m "slow and integration"
```

## 测试最佳实践

### 1. 测试独立性

```python
# ✅ 正确：每个测试独立
@pytest.mark.asyncio
async def test_1():
    provider = MyProvider({})
    assert provider.init() is True

@pytest.mark.asyncio
async def test_2():
    provider = MyProvider({})  # 新实例
    assert provider.init() is True

# ❌ 错误：测试相互依赖
global provider = None

async def test_1():
    global provider
    provider = MyProvider({})

async def test_2():
    provider.init()  # 依赖 test_1
```

### 2. 清理资源

```python
# ✅ 正确：使用 fixture 清理
@pytest.fixture
async def provider():
    p = MyProvider(config)
    yield p
    await p.cleanup()

# ❌ 错误：未清理资源
async def test_without_cleanup():
    p = MyProvider(config)
    await p.start()
    # 测试结束，资源未清理
```

### 3. 使用描述性名称

```python
# ✅ 正确：描述性名称
async def test_provider_init_with_valid_config():
    pass

async def test_provider_init_with_missing_api_key():
    pass

# ❌ 错误：模糊名称
async def test_1():
    pass

async def test_2():
    pass
```

### 4. 测试边界条件

```python
# ✅ 正确：测试各种情况
@pytest.mark.asyncio
async def test_provider_with_valid_input():
    provider = MyProvider({})
    result = await provider.process("valid input")
    assert result is not None

@pytest.mark.asyncio
async def test_provider_with_empty_input():
    provider = MyProvider({})
    result = await provider.process("")
    assert result is None

@pytest.mark.asyncio
async def test_provider_with_none_input():
    provider = MyProvider({})
    with pytest.raises(ValueError):
        await provider.process(None)

# ❌ 错误：只测试正常情况
async def test_provider():
    provider = MyProvider({})
    result = await provider.process("valid input")
    assert result is not None
```

## Provider 测试

### InputProvider 测试

```python
import pytest
from src.domains.input.providers.my_provider import MyInputProvider
from src.core.base.raw_data import RawData

@pytest.mark.asyncio
async def test_input_provider_collect_data():
    """测试 InputProvider 数据采集"""
    config = {"api_url": "https://api.example.com"}
    provider = MyInputProvider(config)

    data_count = 0
    async for raw_data in provider._collect_data():
        data_count += 1
        assert raw_data.source == "my_provider"
        if data_count >= 3:
            await provider.stop()

    assert data_count > 0
```

### DecisionProvider 测试

```python
import pytest
from src.domains.decision.providers.my_provider import MyDecisionProvider
from src.core.base.normalized_message import NormalizedMessage
from src.domains.decision.intent import Intent

@pytest.mark.asyncio
async def test_decision_provider_decide():
    """测试 DecisionProvider 决策"""
    provider = MyDecisionProvider({})

    message = NormalizedMessage(
        text="测试消息",
        user=None,
        source="test"
    )

    intent = await provider.decide(message)

    assert intent is not None
    assert intent.type in ["response", "ignore"]
```

### OutputProvider 测试

```python
import pytest
from src.domains.output.providers.my_provider import MyOutputProvider
from src.domains.output.parameters.render_parameters import RenderParameters

@pytest.mark.asyncio
async def test_output_provider_render():
    """测试 OutputProvider 渲染"""
    provider = MyOutputProvider({})

    params = RenderParameters(
        text="测试文本",
        tts_text="测试文本",
        emotion_type="happy",
    )

    # Mock 渲染方法
    with patch.object(provider, "_actual_render"):
        await provider.render(params)
        provider._actual_render.assert_called_once()
```

## Pipeline 测试

```python
import pytest
from src.domains.input.pipelines.my_pipeline import MyPipeline
from src.core.base.normalized_message import NormalizedMessage

@pytest.mark.asyncio
async def test_pipeline_process():
    """测试 Pipeline 处理"""
    config = {"param": "test"}
    pipeline = MyPipeline(config)

    message = NormalizedMessage(
        text="测试消息",
        source="test",
        user=None
    )

    result = await pipeline.process(message)

    # 返回 None 表示消息被过滤
    if result is None:
        assert True  # 消息被过滤
    else:
        assert result.text == "预期的文本"

@pytest.mark.asyncio
async def test_pipeline_filter():
    """测试 Pipeline 过滤"""
    config = {"similarity_threshold": 0.8}
    pipeline = SimilarTextFilterPipeline(config)

    message = NormalizedMessage(
        text="相似消息",
        source="test",
        user=None
    )

    result = await pipeline.process(message)

    # 验证消息被过滤
    assert result is None
```

## 架构测试

项目包含架构测试自动验证数据流约束。

### 运行架构测试

```bash
uv run pytest tests/architecture/test_event_flow_constraints.py -v
```

### 测试内容

- Output Domain 不订阅 Input Domain 事件
- Decision Domain 不订阅 Output Domain 事件
- Input Domain 不订阅下游事件
- 事件流向严格遵守单向原则

**在提交代码前务必运行架构测试！**

## 测试覆盖率

### 安装 coverage

```bash
uv add --dev pytest-cov
```

### 生成覆盖率报告

```bash
# 终端输出
uv run pytest --cov=src tests/

# HTML 报告
uv run pytest --cov=src --cov-report=html tests/
open htmlcov/index.html

# 只报告覆盖率低于 100% 的文件
uv run pytest --cov=src --cov-fail-under=80 tests/
```

## 调试测试

### 使用 pdb

```python
def test_with_debug():
    import pdb; pdb.set_trace()
    assert True
```

### 使用 pytest 的调试选项

```bash
# 进入 pdb 当测试失败时
uv run pytest --pdb

# 在测试开始时进入 pdb
uv run pytest --trace
```

### 查看详细输出

```bash
# 显示 print 输出
uv run pytest -s

# 显示详细日志
uv run pytest -vv --log-cli-level=DEBUG
```

## 常见问题

### Q: 异步测试报错 "RuntimeError: Event loop is closed"

A: 使用 pytest-asyncio 的 fixture：

```python
@pytest.fixture
async def event_loop():
    loop = asyncio.get_event_loop()
    yield loop
```

### Q: Mock 不起作用

A: 确保使用正确的导入路径：

```python
# ✅ 正确：使用实际导入路径
with patch("src.domains.input.my_provider.fetch_data"):
    pass

# ❌ 错误：使用测试文件中的导入
with patch("tests.test_my_provider.fetch_data"):
    pass
```

### Q: 测试数据库文件污染

A: 使用临时目录：

```python
import pytest
import tempfile

@pytest.fixture
def temp_db():
    with tempfile.NamedTemporaryFile(suffix=".db") as f:
        yield f.name
```

## 相关文档

- [开发规范](development-guide.md) - 代码风格和约定
- [Provider 开发](provider-guide.md) - Provider 测试示例
- [管道开发](pipeline-guide.md) - Pipeline 测试示例

---

*最后更新：2026-02-09*
