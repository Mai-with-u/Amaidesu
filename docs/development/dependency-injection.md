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

组件在构造函数签名中显式声明所需服务，由组合根（`main.py::create_app_components`）构造并注入：

```python
class MyCollector(BaseCollector):
    def __init__(
        self,
        config: Dict[str, Any],
        event_bus: "EventBus",
        llm_manager: "LLMManager",
    ):
        self._event_bus = event_bus
        self._llm_manager = llm_manager
```

装配发生在组合根：每个组件的依赖在 `main.py` 中显式构造、显式传入——
没有反射式实例化工具，也没有服务定位器。每个组件只收到自己声明的依赖，
不需要的服务不会传入。

### 循环依赖的处理

使用 `TYPE_CHECKING` + 字符串注解：

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.modules.llm.manager import LLMManager


class MyCollector(BaseCollector):
    def __init__(self, config, event_bus: "EventBus"):  # 字符串注解
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


class BadComponent:
    def __init__(self, config, context: BadContext):
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
class GoodComponent:
    def __init__(self, config, event_bus: "EventBus"):
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

---

*最后更新：2026-09-06（删除反射式装配节：src/modules/di 已随 v2 组合根显式装配移除；示例改为 v2 组件；修复代码块损坏字符）*
