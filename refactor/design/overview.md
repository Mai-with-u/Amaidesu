# Amaidesu 3层架构设计

## 核心理念

**按 AI VTuber 数据处理流程组织为 3 个业务域，每个域回答一个核心问题：**

| 域 | 核心问题 | 输入 → 输出 |
|----|----------|-------------|
| **Input** | 从哪里获取数据？如何标准化？ | 外部数据 → NormalizedMessage |
| **Decision** | 如何决定回应？ | NormalizedMessage → Intent |
| **Output** | 如何表达回应？ | Intent → 实际输出 |

---

## 架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                        Amaidesu 架构                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─────────────────┐                                            │
│  │  Input Domain   │  外部数据 → NormalizedMessage              │
│  │                 │                                            │
│  │  • InputProvider: 数据采集（弹幕、游戏、语音）               │
│  │  • Normalization: 标准化为 NormalizedMessage                 │
│  │  • Pipelines: 预处理（限流、过滤）                           │
│  │                                                              │
│  │  配置: [providers.input]                                     │
│  └────────┬────────┘                                            │
│           │ EventBus: normalization.message_ready               │
│           ▼                                                     │
│  ┌─────────────────┐                                            │
│  │ Decision Domain │  NormalizedMessage → Intent                │
│  │                 │                                            │
│  │  • DecisionProvider: 意图生成                                │
│  │    - MaiCore（默认，WebSocket + LLM意图解析）                │
│  │    - LocalLLM（可选，直接LLM）                               │
│  │    - RuleEngine（可选，规则引擎）                            │
│  │                                                              │
│  │  配置: [providers.decision]                                  │
│  └────────┬────────┘                                            │
│           │ EventBus: decision.intent_generated                 │
│           ▼                                                     │
│  ┌─────────────────┐                                            │
│  │  Output Domain  │  Intent → 实际输出                         │
│  │                 │                                            │
│  │  • Parameters: 参数生成（情绪→表情、动作→热键）              │
│  │  • OutputProvider: 实际渲染（TTS、字幕、VTS 等）             │
│  │                                                              │
│  │  配置: [providers.output]                                    │
│  └─────────────────┘                                            │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│  core（基础设施）: EventBus, Connectors, Base Classes           │
│  services（共享服务）: LLMService, ConfigService, Context       │
└─────────────────────────────────────────────────────────────────┘
```

---

## 设计决策

### 为什么是 3 个业务域而不是 5 层？

早期设计将「标准化」和「参数生成」独立成层，但实际分析发现：

| 阶段 | 是否需要独立可替换？ | 结论 |
|------|---------------------|------|
| 数据采集 | **是** - 输入源多样 | 独立 |
| 标准化 | **否** - 与采集源强耦合 | 合并到 Input |
| 决策 | **是** - 策略完全不同 | 独立 |
| 参数生成 | **否** - 与输出设备强耦合 | 合并到 Output |
| 渲染输出 | **是** - 输出设备多样 | 独立 |

**核心原则**：只在真正需要「可替换/可扩展」的地方设置独立的域。

### 参数生成为什么不是独立层？

1. **设备依赖性**：VTS 和 Warudo 需要完全不同的参数格式
2. **避免两次转换**：独立层需要「通用格式」，Output 还要再转换一次
3. **作为内部模块更灵活**：需要复用的映射逻辑作为 Output Domain 内部模块，不强制所有 Provider 经过

---

## 目录结构

```
Amaidesu/
├── main.py                         # CLI入口（参数解析、信号处理）
│
└── src/
    ├── amaidesu_core.py            # 核心协调器（管理组件生命周期）
    │
    ├── core/                       # 基础设施（框架层）
    │   ├── events/                 # 事件子系统
    │   │   ├── event_bus.py        # 事件总线
    │   │   ├── names.py            # 事件名常量
    │   │   ├── payloads.py         # 事件载荷定义
    │   │   └── registry.py         # 事件注册表
    │   │
    │   ├── base/                   # 抽象基类和数据类型
    │   │   ├── input_provider.py
    │   │   ├── decision_provider.py
    │   │   ├── output_provider.py
    │   │   ├── normalized_message.py
    │   │   └── raw_data.py
    │   │
    │   ├── connectors/             # 通信组件
    │   │   ├── websocket_connector.py
    │   │   └── router_adapter.py
    │   │
    │   └── utils/                  # 无状态工具函数
    │       ├── logger.py
    │       └── config.py
    │
    ├── services/                   # 共享服务（能力层）
    │   ├── llm/                    # LLM 子系统
    │   │   ├── llm_service.py
    │   │   └── backends/
    │   │
    │   ├── config/                 # 配置子系统
    │   │   ├── config_service.py
    │   │   ├── generator.py
    │   │   └── schemas/
    │   │
    │   └── context/                # 上下文子系统
    │       └── context_manager.py
    │
    └── domains/                    # 业务域（业务层）
        ├── input/                  # Input Domain
        │   ├── input_layer.py
        │   ├── input_provider_manager.py
        │   ├── pipeline_manager.py
        │   ├── normalization/      # 标准化模块
        │   ├── pipelines/          # 预处理管道
        │   └── providers/          # InputProvider 实现
        │
        ├── decision/               # Decision Domain
        │   ├── decision_manager.py
        │   ├── intent.py
        │   └── providers/          # DecisionProvider 实现
        │
        └── output/                 # Output Domain
            ├── flow_coordinator.py
            ├── output_provider_manager.py
            ├── provider_registry.py
            ├── parameters/         # 参数生成模块
            ├── adapters/           # 设备适配器
            └── providers/          # OutputProvider 实现
```

### 目录设计决策

| 决策 | 理由 |
|------|------|
| **main.py 在根目录** | CLI入口，负责参数解析和启动，与业务逻辑分离 |
| **amaidesu_core.py 在 src/ 根** | 应用级协调器，不属于基础设施，与 core/services/domains 并列 |
| **core/services/domains 三分** | 分类维度一致：基础设施 vs 共享能力 vs 业务逻辑 |
| **按子系统组织** | 高内聚：事件系统、LLM系统各自内聚，便于理解和维护 |
| **normalization 放入 Input** | 与输入源强耦合，是 Input Domain 的内部实现 |
| **parameters 放入 Output** | 与输出设备强耦合，是 Output Domain 的内部模块 |
| **pipelines 放入 Input** | 预处理管道是 Input 的一部分，处理标准化后的消息 |
| **rendering → output** | 命名与 3 域架构一致 |
| **connectors 独立** | 原 `core/providers` 命名有误导，拆分为通信组件 |
| **utils 放入 core** | 无状态工具是基础设施的一部分 |

---

## 核心数据类型

### NormalizedMessage（Input → Decision）

```python
@dataclass
class NormalizedMessage:
    text: str                     # 用于 LLM 的文本描述
    content: StructuredContent    # 原始结构化数据（不丢失信息）
    source: str                   # 来源标识
    data_type: str                # 数据类型
    importance: float             # 重要性（0-1）
    metadata: Dict[str, Any]
```

### Intent（Decision → Output）

```python
@dataclass
class Intent:
    original_text: str            # 原始输入
    response_text: str            # AI 回复
    emotion: EmotionType          # 情感
    actions: List[IntentAction]   # 动作列表
    metadata: Dict[str, Any]
```

---

## 扩展指南

### 添加新的 InputProvider

1. 在 `src/domains/input/providers/` 创建目录
2. 继承 `InputProvider` 基类，实现 `_collect_data()` 方法
3. 在 `__init__.py` 中注册到 `ProviderRegistry`
4. 在配置 `[providers.input]` 中启用

### 添加新的 DecisionProvider

1. 在 `src/domains/decision/providers/` 创建目录
2. 继承 `DecisionProvider` 基类，实现 `decide()` 方法
3. 在 `__init__.py` 中注册
4. 在配置 `[providers.decision]` 中选择

### 添加新的 OutputProvider

1. 在 `src/domains/output/providers/` 创建目录
2. 继承 `OutputProvider` 基类，实现 `render()` 方法
3. 可选调用 `parameters/` 中的共享映射模块
4. 在 `__init__.py` 中注册
5. 在配置 `[providers.output]` 中启用

---

## 相关文档

- [决策层设计](./decision_layer.md) - Decision Domain 详细设计
- [多Provider并发](./multi_provider.md) - 并发处理设计
- [Pipeline设计](./pipeline_refactoring.md) - TextPipeline 设计
- [事件契约](./event_data_contract.md) - 事件类型安全
- [LLM服务](./llm_service.md) - LLM 服务设计
- [配置系统](./config_system.md) - Pydantic Schema + 三级配置合并
