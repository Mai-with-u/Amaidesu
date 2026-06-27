# ADR-004: 核心层 DI 与插件层 Context 的边界

## Context

在讨论 Pipeline 重构时，需要厘清两个层次的依赖注入策略：

1. **核心层（项目内部组件）**：InputCollector、Decider、OutputHandler、Pipeline 等由项目自身维护
2. **插件层（未来第三方扩展）**：可能通过 pip 安装的第三方组件

这两层对依赖注入的需求不同：

- **核心层**：依赖已知、有限、可控。可以用构造器注入，类型完全安全。
- **插件层**：依赖未知、用户自定义、跨版本兼容。框架无法预知用户需要什么。

历史上 Amaidesu 曾有过插件系统（参见 `AGENTS.md` 中的"禁止事项"），后被移除重构为阶段参与者系统。当前是"源码级扩展"机制（修改项目代码加新文件），不是"二进制级插件"（pip install）。

## Decision

**两层使用不同的依赖注入策略**：

| 层级 | 适用模式 | 原因 |
|------|---------|------|
| **核心层（项目内部）** | **构造器注入 DI**（类型安全） | 依赖已知有限，可以完全类型化 |
| **插件层（未来第三方）** | **Context Object / Service Locator**（稳定 API 边界） | 框架需要稳定的 API 边界，内部实现可变 |

**两层不冲突，可以叠加**：

```
┌──────────────────────────────────────────────┐
│ 核心层（项目内部组件）                       │
│  InputCollector、Decider、Handler、Pipeline   │
│  → 用 DI（类型完全已知、可控）               │
└──────────────────────────────────────────────┘
                    ↓ 框架暴露
┌──────────────────────────────────────────────┐
│ PluginContext（这是合理的 Context！）        │
│  - get_llm_service() → LLMManager            │
│  - get_event_bus() → EventBus                │
│  - register_collector(name, cls)             │
│  - register_handler(name, cls)               │
│  - register_pipeline(stage, name, cls)       │
│  - on_event(event_name, handler)             │
└──────────────────────────────────────────────┘
                    ↓ 插件使用
┌──────────────────────────────────────────────┐
│ 插件层（外部第三方扩展）                     │
│  → 通过 PluginContext 访问框架能力           │
└──────────────────────────────────────────────┘
```

**核心原则**：

- **服务对象 → DI**：`LLMManager`、`EventBus`、`Database`、`PipelineManager` 等（有行为、被调用、单例）通过构造器注入
- **数据对象 → Context**：HTTP Request、Session、用户偏好、消息追踪 ID 等（无行为、被读取、请求级）通过 Context Object 传递

## Decision Tree（开发时判断用）

```
传入的"东西"是：
│
├─ 服务（有行为、被调用、有状态、单例或长生命周期）
│   → DI（构造器注入）
│   例：LLMManager.generate()、EventBus.emit()、Database.query()
│
└─ 数据（无行为、被读取、值对象、每次请求新建）
    │
    ├─ 数量少（1-2 个）→ 直接当参数传
    ├─ 数量多（3+）或要跨多层传递 → Context Object
    └─ 框架扩展点（用户自定义内容）→ Context Object（弱类型可接受）
```

## Considered Alternatives

### 替代方案 A：核心层 + 插件层都用 Context

- **优点**：模式统一
- **缺点**：核心层内部组件互传 Context 会丧失类型安全
- **否决理由**：违反"显式依赖优于隐式"原则

### 替代方案 B：核心层 + 插件层都用 DI

- **优点**：类型完全安全
- **缺点**：插件层 DI 要求框架导出所有可能服务类型，版本兼容性差
- **否决理由**：插件作者无法 import 框架内部类型（不同版本可能不兼容）

### 替代方案 C：统一用 IoC 容器（如 dependency-injector）

- **优点**：自动装配
- **缺点**：增加"魔法"，依赖关系隐式化；Python 项目通常不需要容器
- **否决理由**：手动 DI 更易测试、更易调试、与 Python 风格一致

## Consequences

### 短期影响

- 当前重构（ADR-001~003）只涉及核心层，统一用 DI
- 不需要为未来插件系统做兼容（PluginContext 是未来单独设计）

### 长期影响

- 核心层 DI 化让未来 PluginContext 包装更简单（核心层干净，外部包装才可能）
- 第三方开发者未来可以通过 PluginContext 访问框架能力，API 稳定

### 当下需要避免的混淆

- **不要**把当前 `OutputPipelineContext` 当作"插件层 Context 的雏形"——它是异类，应被砍掉（ADR-001）
- **不要**因为未来可能做插件系统，就在当前核心层提前用 Context——YAGNI

## Reversibility

回滚方法：无（这是概念性 ADR，没有代码改动）

后续如果决定不做插件系统，此 ADR 自动失效。如果要做插件系统，需要单独 ADR 设计 `PluginContext`。