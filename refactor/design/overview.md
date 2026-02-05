# Amaidesu 3层架构设计

## 核心理念

**按 AI VTuber 数据处理流程组织为 3 个域，每个域回答一个核心问题：**

| 域 | 核心问题 | 输入 → 输出 |
|----|----------|-------------|
| **Input** | 从哪里获取数据？如何标准化？ | 外部数据 → NormalizedMessage |
| **Decision** | 如何决定回应？ | NormalizedMessage → Intent |
| **Output** | 如何表达回应？ | Intent → 实际输出 |

---

## 架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                     Amaidesu 3层架构                             │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─────────────────┐                                            │
│  │  Input Domain   │  外部数据 → NormalizedMessage              │
│  │                 │                                            │
│  │  • InputProvider: 数据采集（弹幕、游戏、语音）               │
│  │  • InputLayer: 标准化 + TextPipeline                        │
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
│  │  • ParameterService: 共享映射服务（情绪→表情、动作→热键）    │
│  │  • OutputProvider: 实际渲染（TTS、字幕、VTS 等）             │
│  │                                                              │
│  │  配置: [providers.output]                                    │
│  └─────────────────┘                                            │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│  基础设施: EventBus, LLMService, PipelineManager                │
└─────────────────────────────────────────────────────────────────┘
```

---

## 设计决策

### 为什么是 3 层而不是 5 层？

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
3. **共享服务更灵活**：需要复用的映射逻辑（情绪→表情）作为共享服务，不强制所有 Provider 经过

---

## 目录结构

```
src/
├── core/                           # 基础设施
│   ├── base/                       # 基类定义
│   │   ├── input_provider.py       # InputProvider 基类
│   │   ├── decision_provider.py    # DecisionProvider 基类
│   │   └── output_provider.py      # OutputProvider 基类
│   ├── event_bus.py                # 事件总线
│   ├── llm_service.py              # LLM 服务
│   ├── amaidesu_core.py            # 核心协调器
│   └── events/                     # 事件定义
│
├── layers/
│   ├── input/                      # Input Domain
│   │   ├── input_layer.py          # 标准化处理
│   │   ├── input_provider_manager.py
│   │   └── providers/              # InputProvider 实现
│   │
│   ├── decision/                   # Decision Domain
│   │   ├── decision_manager.py
│   │   ├── intent.py               # Intent 定义
│   │   └── providers/              # DecisionProvider 实现
│   │
│   ├── normalization/              # 辅助模块：数据类型定义
│   │   └── content/                # StructuredContent 类型
│   │
│   ├── parameters/                 # 辅助模块：共享映射服务
│   │   ├── emotion_mapper.py       # 情绪→表情映射
│   │   └── action_mapper.py        # 动作→热键映射
│   │
│   └── rendering/                  # Output Domain
│       ├── provider_registry.py
│       └── providers/              # OutputProvider 实现
│
└── pipelines/                      # TextPipeline（限流、过滤）
```

**说明**：`normalization/` 和 `parameters/` 是辅助模块，不是独立的运行时层。

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

1. 在 `src/layers/input/providers/` 创建目录
2. 继承 `InputProvider` 基类，实现 `_collect_data()` 方法
3. 在 `__init__.py` 中注册到 `ProviderRegistry`
4. 在配置中启用

### 添加新的 DecisionProvider

1. 在 `src/layers/decision/providers/` 创建目录
2. 继承 `DecisionProvider` 基类，实现 `decide()` 方法
3. 在 `__init__.py` 中注册
4. 在配置中选择

### 添加新的 OutputProvider

1. 在 `src/layers/rendering/providers/` 创建目录
2. 继承 `OutputProvider` 基类，实现 `render()` 方法
3. 可选择调用 `ParameterService` 中的共享映射
4. 在 `__init__.py` 中注册
5. 在配置中启用

---

## 相关文档

- [决策层设计](./decision_layer.md) - Decision Domain 详细设计
- [多Provider并发](./multi_provider.md) - 并发处理设计
- [Pipeline设计](./pipeline_refactoring.md) - TextPipeline 设计
- [事件契约](./event_data_contract.md) - 事件类型安全
- [LLM服务](./llm_service.md) - LLM 基础设施
