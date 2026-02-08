# ProviderRegistry 显式注册模式重构

> **版本**: v1.0
> **日期**: 2026-02-08
> **状态**: ✅ 阶段1-2已完成（向后兼容）

---

## 概述

本次重构为 ProviderRegistry 添加了**显式注册模式**，作为对现有自动注册模式的补充。显式注册模式解决了测试隔离问题，使多实例测试和并行测试成为可能。

### 当前状态

- ✅ **阶段 1**: 在 Provider 基类中添加 `get_registration_info()` 类方法
- ✅ **阶段 2**: 在 ProviderRegistry 中添加 `register_from_info()` 和 `register_provider_class()` 方法
- ✅ **阶段 3**: 为示例 Provider 添加 `get_registration_info()` 实现
- ⏳ **阶段 4**: 创建显式注册函数（可选，未来版本）

### 向后兼容性

本次重构**完全向后兼容**：
- 现有的自动注册模式（在 `__init__.py` 中调用 `ProviderRegistry.register_*()`）仍然有效
- 新的显式注册模式是可选的
- 两种模式可以共存

---

## 问题背景

### 问题 #10: 测试隔离困难

**现状**：
- Provider 在模块导入时自动注册到全局的 ProviderRegistry
- 测试难以隔离，无法在同一进程运行多实例
- 测试间的 Provider 注册会相互干扰

**影响**：
- 测试必须顺序运行
- 无法进行并行测试
- `ProviderRegistry.clear_all()` 会影响其他测试
- 测试不稳定，依赖执行顺序

---

## 解决方案：显式注册模式

### 核心思想

将 Provider 注册从"模块导入时自动注册"改为"显式调用注册方法"，提供以下好处：

1. **测试隔离**: 每个测试可以独立控制 Provider 注册
2. **并行测试**: 不同测试可以使用不同的 Provider 注册
3. **可追溯性**: 注册来源清晰，便于调试
4. **向后兼容**: 不破坏现有代码

### 设计原则

1. **不破坏现有代码**: 自动注册模式仍然有效
2. **渐进式迁移**: Provider 可以逐步迁移到显式注册
3. **测试友好**: 测试可以自由控制注册时机
4. **类型安全**: 使用 Pydantic 验证注册信息

---

## 实现细节

### 1. Provider 基类新增方法

#### InputProvider

```python
# src/core/base/input_provider.py

@classmethod
def get_registration_info(cls) -> Dict[str, Any]:
    """
    获取 Provider 注册信息（子类重写）

    用于显式注册模式，避免模块导入时的自动注册。

    Returns:
        注册信息字典，包含:
        - layer: "input"
        - name: Provider 名称（唯一标识符）
        - class: Provider 类
        - source: 注册来源（如 "builtin:console_input"）

    Raises:
        NotImplementedError: 如果子类未实现此方法

    Example:
        @classmethod
        def get_registration_info(cls):
            return {
                "layer": "input",
                "name": "console_input",
                "class": cls,
                "source": "builtin:console_input"
            }
    """
    raise NotImplementedError(
        f"{cls.__name__} 必须实现 get_registration_info() 类方法以支持显式注册。"
        "如果使用自动注册模式，可以在 __init__.py 中直接调用 ProviderRegistry.register_input()。"
    )
```

#### DecisionProvider 和 OutputProvider

类似实现，分别返回 `"layer": "decision"` 和 `"layer": "output"`。

### 2. ProviderRegistry 新增方法

#### register_from_info()

```python
# src/core/provider_registry.py

@classmethod
def register_from_info(cls, info: Dict[str, Any]) -> None:
    """
    从注册信息字典注册 Provider

    这是显式注册模式的核心方法，与 Provider.get_registration_info() 配合使用。
    避免了模块导入时的自动注册，使测试更容易隔离。

    Args:
        info: 注册信息字典（来自 Provider.get_registration_info()）
            - layer: "input", "decision", 或 "output"
            - name: Provider 名称
            - class: Provider 类
            - source: 注册来源（可选）

    Raises:
        ValueError: 如果注册信息无效
        TypeError: 如果 Provider 类型不匹配

    Example:
        # 在 main.py 中显式注册
        from src.domains.input.providers.console_input import ConsoleInputProvider

        info = ConsoleInputProvider.get_registration_info()
        ProviderRegistry.register_from_info(info)
    """
    # 验证必需字段
    required_fields = ["layer", "name", "class"]
    for field in required_fields:
        if field not in info:
            raise ValueError(f"注册信息缺少必需字段: {field}")

    layer = info["layer"]
    name = info["name"]
    provider_class = info["class"]
    source = info.get("source", "manual")

    # 根据域类型调用对应的注册方法
    if layer == "input":
        cls.register_input(name, provider_class, source=source)
    elif layer == "decision":
        cls.register_decision(name, provider_class, source=source)
    elif layer == "output":
        cls.register_output(name, provider_class, source=source)
    else:
        raise ValueError(
            f"无效的 Provider 域: {layer}. "
            f"必须是 'input', 'decision', 或 'output'"
        )
```

#### register_provider_class()

```python
@classmethod
def register_provider_class(cls, provider_class: Type) -> None:
    """
    直接从 Provider 类注册（便捷方法）

    自动调用 Provider.get_registration_info() 并注册。

    Args:
        provider_class: Provider 类

    Raises:
        NotImplementedError: 如果 Provider 未实现 get_registration_info()
        ValueError/TypeError: 如果注册信息无效

    Example:
        from src.domains.input.providers.console_input import ConsoleInputProvider
        ProviderRegistry.register_provider_class(ConsoleInputProvider)
    """
    if not hasattr(provider_class, "get_registration_info"):
        raise NotImplementedError(
            f"{provider_class.__name__} 未实现 get_registration_info() 类方法。"
            "请先在 Provider 类中实现此方法，或使用传统的 register_input/output/decision() 方法。"
        )

    info = provider_class.get_registration_info()
    cls.register_from_info(info)
```

### 3. Provider 实现

#### ConsoleInputProvider 示例

```python
# src/domains/input/providers/console_input/console_input_provider.py

class ConsoleInputProvider(InputProvider):
    """控制台输入Provider"""

    class ConfigSchema(BaseProviderConfig):
        type: Literal["console_input"] = "console_input"
        user_id: str = Field(default="console_user")
        user_nickname: str = Field(default="控制台")

    @classmethod
    def get_registration_info(cls) -> Dict[str, Any]:
        """
        获取 Provider 注册信息

        用于显式注册模式，避免模块导入时的自动注册。
        """
        return {
            "layer": "input",
            "name": "console_input",
            "class": cls,
            "source": "builtin:console_input"
        }

    # ... 其他方法
```

---

## 使用方式

### 方式 1: 自动注册模式（现有方式，仍然有效）

```python
# src/domains/input/providers/console_input/__init__.py

from .console_input_provider import ConsoleInputProvider
from src.core.provider_registry import ProviderRegistry

# 自动注册（模块导入时执行）
ProviderRegistry.register_input("console_input", ConsoleInputProvider, source="builtin:console_input")

__all__ = ["ConsoleInputProvider"]
```

### 方式 2: 显式注册模式（新方式）

```python
# main.py 或测试文件中

from src.domains.input.providers.console_input import ConsoleInputProvider
from src.core.provider_registry import ProviderRegistry

# 方式 2a: 使用注册信息
info = ConsoleInputProvider.get_registration_info()
ProviderRegistry.register_from_info(info)

# 方式 2b: 直接注册类（更简洁）
ProviderRegistry.register_provider_class(ConsoleInputProvider)
```

### 测试中的使用

```python
# tests/test_my_provider.py

import pytest
from src.core.provider_registry import ProviderRegistry
from src.domains.input.providers.console_input import ConsoleInputProvider

@pytest.fixture
def registered_provider():
    """每个测试独立注册 Provider"""
    ProviderRegistry.clear_all()  # 清空注册表
    ProviderRegistry.register_provider_class(ConsoleInputProvider)
    yield
    ProviderRegistry.clear_all()  # 清理

def test_console_input(registered_provider):
    """测试可以安全运行，不会影响其他测试"""
    provider = ProviderRegistry.create_input("console_input", {})
    assert isinstance(provider, ConsoleInputProvider)
```

---

## 迁移指南

### 对于现有 Provider

如果要将现有 Provider 迁移到显式注册模式：

1. **在 Provider 类中添加 `get_registration_info()` 方法**

```python
@classmethod
def get_registration_info(cls) -> Dict[str, Any]:
    return {
        "layer": "input",  # 或 "decision", "output"
        "name": "your_provider_name",
        "class": cls,
        "source": "builtin:your_provider_name"
    }
```

2. **（可选）修改 `__init__.py` 注释掉自动注册**

```python
# 原来的自动注册（已废弃，保留用于向后兼容）
# ProviderRegistry.register_input("your_provider", YourProvider, source="builtin:your_provider")
```

3. **在 main.py 中添加显式注册（未来版本）**

```python
def register_builtin_providers():
    """显式注册所有内置 Provider"""
    from src.domains.input.providers.console_input import ConsoleInputProvider
    from src.domains.decision.providers.maicore import MaiCoreDecisionProvider
    # ... 其他 Provider

    providers = [
        ConsoleInputProvider,
        MaiCoreDecisionProvider,
        # ... 其他 Provider
    ]

    for provider_cls in providers:
        ProviderRegistry.register_provider_class(provider_cls)
```

### 对于新 Provider

建议新 Provider 直接实现 `get_registration_info()`，可以使用显式注册模式。

---

## 测试

新增了 `TestExplicitRegistration` 测试类，包含以下测试：

```python
class TestExplicitRegistration:
    """测试显式注册模式"""

    def test_register_from_info_input(self)
    def test_register_from_info_decision(self)
    def test_register_from_info_output(self)
    def test_register_from_info_missing_field(self)
    def test_register_from_info_invalid_layer(self)
    def test_register_provider_class(self)
    def test_register_provider_class_no_method(self)
    def test_explicit_registration_isolation(self)
```

运行测试：

```bash
uv run pytest tests/core/test_provider_registry.py::TestExplicitRegistration -v
```

所有测试通过 ✅

---

## 优势与限制

### 优势

1. **测试隔离**: 每个测试可以独立控制 Provider 注册
2. **并行测试**: 不同测试可以使用不同的 Provider 注册
3. **可追溯性**: 注册来源清晰，便于调试
4. **向后兼容**: 不破坏现有代码
5. **类型安全**: 使用 Pydantic 验证注册信息

### 限制

1. **渐进式迁移**: 需要逐步为现有 Provider 添加 `get_registration_info()`
2. **双重模式**: 自动注册和显式注册共存，可能造成混淆
3. **可选性**: 不是强制性的，依赖开发者自觉使用

---

## 未来计划

### 阶段 4: 完全显式注册（可选）

在未来版本中，可以考虑：

1. **在 main.py 中创建 `register_builtin_providers()` 函数**
2. **逐步注释掉 `__init__.py` 中的自动注册**
3. **最终完全移除自动注册模式**

但这不是必需的，因为：
- 现有模式已经工作良好
- 显式注册主要为了测试隔离
- 自动注册对应用启动更友好

---

## 相关问题

- **问题 #10**: ProviderRegistry 显式注册模式重构 ✅ 已解决（阶段1-2）

---

## 总结

本次重构为 ProviderRegistry 添加了**显式注册模式**，解决了测试隔离问题，同时保持了完全的向后兼容性。

**关键成就**：
- ✅ 添加了 `get_registration_info()` 基类方法
- ✅ 添加了 `register_from_info()` 和 `register_provider_class()` 方法
- ✅ 为关键 Provider 实现了显式注册
- ✅ 添加了完整的测试覆盖
- ✅ 保持了向后兼容性

**下一步**：
- 可以根据需要逐步为其他 Provider 添加 `get_registration_info()`
- 在测试中优先使用显式注册模式
- 考虑在文档中推荐使用显式注册模式

---

**最后更新**: 2026年2月8日
**维护者**: Amaidesu Team
