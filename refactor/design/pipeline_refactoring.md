# Pipeline 系统设计

## 核心理念

Pipeline（管道）是数据流中的**拦截-变换-过滤**机制，用于在不修改核心组件的情况下，对流经的数据进行预处理或后处理。

**设计原则**：
- 管道是 EventBus 的**前置守门人**，不是 EventBus 的内部组件
- 管道负责"这条数据该不该进入/离开系统"，EventBus 负责"这条数据该通知谁"
- 输入管道和输出管道对称：输入端守住"进来的数据"，输出端守住"出去的内容"

---

## 为什么需要两层管道

### 输入端：外部用户不可控

观众弹幕、礼物等来自外部，数量和质量不可预测，需要限流、去重、过滤。

### 输出端：LLM 输出同样不可控

决策层核心是 LLM（MaiCore / 本地 LLM），LLM 生成的内容本质上和外部输入一样不可预测。
AI VTuber 领域的真实需求（如 Neuro-sama 的脏话过滤）证明了输出管道的必要性。

```
输入端：观众弹幕（外部不可控）→ 需要过滤 → 输入管道
输出端：LLM 生成（同样不可控）→ 需要过滤 → 输出管道
```

---

## 在架构中的位置

```
外部数据 → InputProvider → Normalizer → NormalizedMessage
                                              ↓
                                     ┌────────────────────┐
                                     │  输入管道链 (Input) │  限流、去重、相似过滤
                                     └────────┬───────────┘
                                              ↓
                                     emit(data.message)
                                              ↓
                                       DecisionManager → LLM / MaiCore
                                              ↓
                                     emit(decision.intent)
                                              ↓
                                     ExpressionGenerator
                                              ↓
                                     ExpressionParameters
                                              ↓
                                     ┌─────────────────────┐
                                     │ 输出管道链 (Output)  │  敏感词过滤、文本长度限制
                                     └────────┬────────────┘
                                              ↓
                                     emit(output.params)
                                              ↓
                                     OutputProviders (TTS, 字幕, VTS...)
```

### 关键：管道在 EventBus 之前，不在 EventBus 内部

管道处理完毕后，数据才被 emit 到 EventBus。EventBus 的订阅者看到的是**过滤后的最终结果**。
这保证了 Pub/Sub 的核心契约："只要事件被 emit，所有订阅者一定能收到"。

---

## 为什么不把管道做成 EventBus 中间件

这个方案听起来更"通用"，但会带来根本性问题：

| 问题 | 说明 |
|------|------|
| **破坏 Pub/Sub 契约** | 订阅者无法确定是否能收到事件（可能被中间件丢弃） |
| **日志和过滤矛盾** | 日志器想看所有事件，过滤器想丢弃部分事件，两者在同一条链上互斥 |
| **调试困难** | 事件没到达订阅者时，无法区分"没人发"还是"中间件丢了" |
| **性能浪费** | 所有事件都要过中间件链，即使大多数事件不需要过滤 |
| **职责膨胀** | EventBus 从单纯的消息代理变成了"消息代理 + 处理引擎" |

**结论**：管道和 EventBus 是两种正交的模式，各管各的，不应合并。

- **Pipeline（管道）**：线性链，可修改/丢弃数据，用于需要拦截的场景
- **EventBus（事件总线）**：扇出广播，所有订阅者独立收到，用于解耦

---

## 输入管道（Input Pipeline）

### 位置

在 `InputLayer` 内部，标准化完成之后、emit `normalization.message_ready` 之前。

### 处理对象

`NormalizedMessage` — 标准化后的消息对象。

> **注意**：Input Pipeline 已升级为处理 `NormalizedMessage`，支持：
> - rate_limit：基于滑动时间窗口的限流
> - similar_filter：相似消息过滤

### 接口

```python
class TextPipeline(Protocol):
    """文本处理管道（Input Domain 的文本预处理）"""

    priority: int  # 执行优先级（数值小优先）
    enabled: bool

    async def process(self, text: str, metadata: Dict[str, Any]) -> Optional[str]:
        """
        处理文本内容

        Args:
            text: 原始文本内容
            metadata: 消息元数据（用户ID、来源等）

        Returns:
            处理后的文本，或 None 表示丢弃
        """
        ...
```

### 内置管道

| 管道 | 优先级 | 功能 |
|------|--------|------|
| **RateLimitPipeline** | 100 | 全局+用户级频率限制，防止刷屏 |
| **SimilarFilterPipeline** | 500 | 过滤短时间内的重复/相似消息 |

### 为什么输入管道处理 (text, metadata) 而不是 NormalizedMessage

| 对比项 | `(str, Dict)` | `NormalizedMessage` |
|--------|---------------|---------------------|
| 覆盖消息类型 | 只有 text | 全部（text/gift/superchat/guard） |
| 可修改的内容 | 只有文本 | text、importance、metadata、任意字段 |
| 类型安全 | metadata 无类型 | 有明确属性（user_id、source、data_type 等） |
| 限流适用性 | 只能限制文本消息 | 可以限制所有类型（礼物刷屏等） |

> **注意**：当前 TextPipeline 处理的是 `(text: str, metadata: Dict[str, Any])`，
> 升级为处理 `NormalizedMessage` 是计划中的 Phase 3 改进（见下方"演进路线"）。

### 调用位置

```python
# TextNormalizer.normalize() 中
text, metadata = self._extract_text_and_metadata(raw_data)  # 提取文本和元数据
if text and self.pipeline_manager:
    processed_text = await self.pipeline_manager.process_text(text, metadata)
    if processed_text:
        normalized_message = NormalizedMessage(
            text=processed_text,
            source=metadata.get("source"),
            user_id=metadata.get("user_id"),
            data_type=metadata.get("data_type"),
            importance=metadata.get("importance", 1),
        )
    else:
        normalized_message = None  # 文本被管道丢弃
else:
    normalized_message = NormalizedMessage(
        text=text,
        source=metadata.get("source"),
        user_id=metadata.get("user_id"),
        data_type=metadata.get("data_type"),
        importance=metadata.get("importance", 1),
    )
if normalized_message:
    await self.event_bus.emit(DATA_MESSAGE, ...)
```

---

## 输出管道（Output Pipeline）

### 位置

在 `FlowCoordinator` 内部，ExpressionGenerator 生成参数之后、emit `expression.parameters_generated` 之前。

### 处理对象

`ExpressionParameters` — 包含 TTS 文本、字幕文本、表情参数、热键等渲染参数。

### 为什么输出管道有必要存在

当决策层是 LLM 时，输出内容和外部输入一样不可预测：

1. **LLM 可能输出脏话/敏感词**：AI VTuber 领域的真实需求（Neuro-sama 案例）
2. **不能在输入管道做**：输入管道过滤的是观众说的话，LLM 自己生成的内容跟输入无关
3. **不能靠 EventBus 订阅做**：需要**修改**文本（替换敏感词），不是仅仅观察
4. **不该在 ExpressionGenerator 做**：ExpressionGenerator 负责 Intent→参数映射，敏感词过滤是不同的关注点
5. **不该在各 OutputProvider 内部做**：需要在分发之前做一次，否则每个 Provider 重复实现

### 接口

```python
class OutputPipeline(Protocol):
    """输出后处理管道"""

    priority: int
    enabled: bool

    async def process(
        self,
        params: ExpressionParameters
    ) -> Optional[ExpressionParameters]:
        """
        处理输出参数

        Args:
            params: 渲染参数

        Returns:
            处理后的参数，或 None 表示丢弃本次输出
        """
        ...
```

### 典型管道

| 管道 | 优先级 | 功能 |
|------|--------|------|
| **ProfanityFilterPipeline** | 100 | 脏话/敏感词替换（处理 tts_text 和 subtitle_text） |
| **TextLengthLimitPipeline** | 200 | 限制输出文本长度，防止超长回复 |

### 调用位置

```python
# FlowCoordinator._on_intent_ready() 中
params = await self.expression_generator.generate(intent)
if self.output_pipeline_manager:
    params = await self.output_pipeline_manager.process(params)
if params:
    await self.event_bus.emit(OUTPUT_PARAMS, ...)
```

---

## 共享基础设施

输入管道和输出管道共享基础设施（优先级、错误处理、超时、统计），但处理的数据类型不同。

### 共享基类

```python
class PipelineBase(ABC):
    """管道共享基类"""

    priority: int = 500
    enabled: bool = True
    error_handling: PipelineErrorHandling = PipelineErrorHandling.CONTINUE
    timeout_seconds: float = 5.0

    # 统计信息
    _stats: PipelineStats

    # 子类实现具体处理逻辑
    ...


class InputPipelineBase(PipelineBase):
    """输入管道基类"""
    @abstractmethod
    async def _process(self, message: NormalizedMessage) -> Optional[NormalizedMessage]: ...


class OutputPipelineBase(PipelineBase):
    """输出管道基类"""
    @abstractmethod
    async def _process(self, params: ExpressionParameters) -> Optional[ExpressionParameters]: ...
```

### 错误处理策略

| 策略 | 行为 |
|------|------|
| `CONTINUE` | 记录日志，跳过当前管道，继续执行下一个 |
| `STOP` | 停止执行，抛出异常 |
| `DROP` | 丢弃消息/参数，不执行后续管道 |

### PipelineManager

`PipelineManager` 分别管理输入管道链和输出管道链：

```python
class PipelineManager:
    """管道管理器"""
    _input_pipelines: List[InputPipeline]
    _output_pipelines: List[OutputPipeline]

    async def process_input(self, message: NormalizedMessage) -> Optional[NormalizedMessage]: ...
    async def process_output(self, params: ExpressionParameters) -> Optional[ExpressionParameters]: ...
```

或拆为两个独立的管理器（`InputPipelineManager` + `OutputPipelineManager`），视实际复杂度而定。

---

## 配置示例

```toml
# === 输入管道 ===
[pipelines.input.rate_limit]
enabled = true
priority = 100
global_rate_limit = 100
user_rate_limit = 10
window_size = 60

[pipelines.input.similar_filter]
enabled = true
priority = 500
similarity_threshold = 0.85
time_window = 5.0

# === 输出管道 ===
[pipelines.output.profanity_filter]
enabled = true
priority = 100
# 敏感词列表文件路径
wordlist = "data/profanity_words.txt"
replacement = "**"

[pipelines.output.text_length_limit]
enabled = true
priority = 200
max_length = 500
```

---

## 管道 vs EventBus：何时用哪个

| 需求 | 使用机制 | 理由 |
|------|----------|------|
| 过滤/丢弃数据 | **管道** | 需要拦截，EventBus 不支持丢弃 |
| 修改/替换数据内容 | **管道** | 需要变换，EventBus 订阅者收到的是只读副本 |
| 记录日志/监控 | **EventBus 订阅** | 纯观察，不影响数据流 |
| 触发副作用（通知等） | **EventBus 订阅** | 解耦，不阻塞主数据流 |
| 统计/计数 | **EventBus 订阅** | 纯观察 |

---

## 演进路线

### Phase 1：清除 MessagePipeline 死代码（当前计划）

移除旧的 `MessagePipeline` 架构（inbound/outbound），只保留 TextPipeline。
详见 `.omc/plans/pipeline-refactoring.md`。

### Phase 2：重命名（Phase 1 之后）

去掉 `_text` 后缀（`load_text_pipelines` → `load_pipelines`、`process_text` → `process` 等），
因为只有一种管道类型后 "text" 前缀冗余。

### Phase 3：双管道架构升级

1. **输入管道接口升级**：从 `(text, metadata) → Optional[str` 改为 `(NormalizedMessage) → Optional[NormalizedMessage]`
2. **输出管道实现**：新增 `OutputPipeline` 接口和 `OutputPipelineBase`，调用点在 `FlowCoordinator`
3. **管道调用位置调整**：从 TextNormalizer 内部移到 InputLayer 的 `on_raw_data_generated()` 中
4. **配置格式更新**：`[pipelines.input.*]` 和 `[pipelines.output.*]` 分组
5. **共享基础设施**：提取 `PipelineBase` 共享基类

---

## 常见疑问

### Q: 输出管道和 ExpressionGenerator 有什么区别？

**ExpressionGenerator** 是一次性的**类型转换**（Intent → ExpressionParameters），负责情感映射、动作映射等"生成"逻辑。
**输出管道** 是可配置的**链式处理**，负责对已生成的参数做"检查和修正"（如敏感词过滤）。
两者关注点不同：前者是"生成正确的参数"，后者是"确保参数安全可输出"。

### Q: 敏感词过滤为什么不在输入管道里做？

输入管道过滤的是**观众说的话**。LLM 自己生成的脏话跟输入内容无关——
即使所有输入都是正常的，LLM 也可能自行产生不当内容。输出管道是最后一道防线。

### Q: 为什么不把管道做成 EventBus 的中间件？

管道和 EventBus 是两种正交模式：
- **管道**：线性链，可修改/丢弃数据（Pipes & Filters 模式）
- **EventBus**：扇出广播，所有订阅者独立收到（Pub/Sub 模式）

将管道合并进 EventBus 会破坏 Pub/Sub 的核心契约（"emit 后所有订阅者一定收到"），
导致调试困难、日志和过滤互斥、EventBus 职责膨胀等问题。
正确的做法是：管道在 EventBus **之前**处理数据，EventBus 只广播过滤后的结果。

### Q: 输入管道从 `(text, metadata)` 改为 `NormalizedMessage` 后，相似过滤等纯文本操作会不会变复杂？

不会。管道内部仍然可以直接访问 `message.text` 进行文本操作。
改变的只是接口签名，内部逻辑几乎不变，但获得了处理所有消息类型和修改任意字段的能力。

### Q: 输出管道需要处理 ExpressionParameters 里的所有字段吗？

不需要。每个管道只处理自己关心的字段。例如敏感词管道只处理 `tts_text` 和 `subtitle_text`，
忽略 `expressions`、`hotkeys` 等字段。管道不需要理解所有字段的含义。
