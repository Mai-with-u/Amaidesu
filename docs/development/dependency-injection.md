# 依赖注入指南

本文档说明 Amaidesu 项目的依赖注入约定。

## 核心原则

| 概念 | 含义 | 适用 |
|------|------|------|
| **IoC（控制反转）** | 对象不创建自己的依赖，由外部传入 | 原则 |
| **DI（依赖注入）** | 实现 IoC 的具体模式：构造器注入 / Setter 注入 / 接口注入 | 模式 |
| **IoC 容器** | 自动装配依赖的框架（Spring、Castle Windsor） | 工具 |

Amaidesu 使用 **纯 DI（Pure DI）/ 手动 DI**——不依赖容器，符合 Python 显式优于隐式的风格。

## 服务 vs 数据

**关键判断**：

```
传入的"东西"是：
├─ 服务（有行为、被调用、有状态、单例或长生命周期）
│   → DI（构造器注入）
│
└─ 数据（无行为、被读取、值对象、每次请求新建）
    │
    ├─ 数量少（1-2 个）→ 直接当参数传
    ├─ 数量多（3+）或要跨多层传递 → Context Object
    └─ 框架扩展点（用户自定义内容）→ Context Object（弱类型可接受）
```

| 类型 | 例子 | 模式 |
|------|------|------|
| **服务对象** | LLMManager、EventBus、Database、SimulatorService | DI 注入 |
| **数据对象** | HTTP Request、Session、User Preferences、Trace ID | Context / 参数 |
| **值对象** | Money、Date、Address | 不可变 dataclass |

## 项目中的 DI 模式

### 构造器注入（标准模式）

```python
@decider("llm")
class LLMDecider:
    def __init__(
        self,
        config: Dict[str, Any],
        event_bus: "EventBus",
        llm_service: "LLMManager",
        prompt_service: "PromptManager",
    ):
        self._event_bus = event_bus
        self._llm_service = llm_service
        self._prompt_service = prompt_service
```

### 反射式装配（组件工厂 / 服务实例化）

组件工厂与服务实例化（如 `SimulatorService` 构造依赖、Dashboard 组件装配）通过 `instantiate_with_di()`（类型匹配 DI）按需注入服务：

```python
from src.modules.di import instantiate_with_di

services_by_type = {
    EventBus: event_bus,
    LLMManager: llm_service,
    PromptManager: prompt_manager,
}

component = instantiate_with_di(
    component_cls,
    config=component_config,          # __init__ 的 config 参数自动注入
    services_by_type=services_by_type,
)
```

匹配规则（见 `src/modules/di/instantiation.py`）：
- **按类型注解匹配**（非参数名）：`__init__` 参数的类型注解在 `services_by_type` 字典中查找服务
- `Optional[X]` 自动解包为 `X`；`Union[X, Y]` 任一类型匹配即可
- 基本类型（`Dict[str, Any]`、`int` 等）不参与注入，只通过 `config` 传入
- 有默认值的参数在服务缺失时跳过（让默认值生效）；缺失必填服务抛 `DependencyInjectionError`

每个组件只收到自己声明的依赖，不需要的服务不会传入。

### 循环依赖的处理

使用 `TYPE_CHECKING` + 字符串注解：

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.modules.llm.manager import LLMManager


class MyCollector(BaseCollector):`n    def __init__(self, config, event_bus: "EventBus"):  # 字符串注解
        ...
```

`TYPE_CHECKING` 块只在类型检查时执行，运行时不会导入，因此无循环依赖。

## 反模式

### ❌ 把服务塞进 Context（属性包）

```python
# 错误
class BadContext:
    llm_service: Optional[Any] = None  # Optional[Any] 是反模式
    prompt_service: Optional[Any] = None


class BadComponent:`n    def __init__(self, config, context: BadContext):
        self._llm = context.llm_service  # 隐藏依赖，类型丢失
```

**为什么错**：
- 依赖隐式（看签名不知道组件需要什么）
- 类型丢失（`Optional[Any]`）
- 错误延迟到运行时（构造时不知道 `context.llm_service` 是不是 None）
- 上帝对象风险（Context 会无限增长）

### ✅ 用 DI 替代

```python
# 正确
class GoodComponent:`n    def __init__(self, config, event_bus: "EventBus"):
        self._llm = llm_service  # 显式依赖，类型安全
```

## 未来：插件层 Context

如果未来 Amaidesu 要支持第三方插件（pip install），需要 `PluginContext`：

```python
class PluginContext:
    """插件可访问的框架能力视图。Service Locator 模式对外部插件可接受。"""

    def get_llm_service(self) -> "LLMManager":
        ...
    def get_event_bus(self) -> "EventBus":
        ...
    def register_collector(self, name: str, cls: type) -> None:
        ...
```

**核心层与假想的插件层区别**：
- **核心层（项目内部）**：用 DI，类型完全已知
- **插件层（未来第三方）**：用 PluginContext，框架无法预知用户需求

> **注**：插件层 Context 是面向未来第三方扩展的设计边界。项目当前采用源码级扩展（直接改代码），插件系统已移除且不计划恢复，因此核心层一律使用构造器注入 DI，不存在运行时 PluginContext 实例。

## 决策清单

遇到"这个东西怎么传"时：

| 问题 | 答 Yes | 答 No |
|------|-------|------|
| 它有方法被调用吗？（`xxx.do_something()`） | → 大概率是服务 → DI | 继续 |
| 它每次请求都新建一份吗？ | → 大概率是数据 → Context | 继续 |
| 它是不可变的值对象吗？ | → 是数据 → Context 或直接参数 | 继续 |
| 它是单例或长生命周期吗？ | → 是服务 → DI | 继续 |
| 框架无法预知用户会塞什么？ | → 框架扩展点 → Context（弱类型可接受） | 继续 |
| 参数数量 ≤ 2 且只在一层用？ | → 直接当参数 | 继续 |
| 参数数量 ≥ 3 或跨多层？ | → Context Object | 结束 |

## 相关 ADR

- ADR-001：Pipeline 曾用 DI 替代 Context（ADR 已随管道系统移除而废弃，DI 原则沿用）