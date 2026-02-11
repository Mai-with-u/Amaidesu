# Amaidesu 架构分析：数据流问题报告

**分析日期**: 2025-02-08
**分析范围**: 从项目启动到完整数据流（Input → Decision → Output）的静态代码分析
**分析方法**: 跟踪 main.py 启动流程，沿着 EventBus 数据流向追踪所有关键组件

---

## 执行摘要

Amaidesu 项目实现了结构清晰的 3 域架构（Input → Decision → Output），采用 EventBus 驱动的通信模式。整体架构设计合理，但在实现层面存在多个关键问题：

- **🔴 严重问题（2个）**: 功能缺失、资源泄漏风险
- **⚠️ 警告问题（6个）**: 架构不一致、潜在的竞态条件、类型安全问题
- **💡 建议优化（2个）**: 代码清理、技术债务处理

---

## 问题清单

### 🔴 严重问题

#### 问题 #1: OutputPipeline 永远不会加载

**严重程度**: 🔴 严重 - 功能完全缺失
**影响范围**: 所有输出管道（敏感词过滤、参数验证等）
**发现位置**: `main.py:217`, `flow_coordinator.py:94-96`

**问题描述**:

系统启动时只加载了 InputPipeline（文本预处理管道），OutputPipelineManager 虽然被创建，但从未调用 `load_output_pipelines()` 方法。

**代码证据**:

```python
# main.py:203-229 - 只加载输入管道
async def load_pipeline_manager(config: Dict[str, Any]) -> Optional[PipelineManager]:
    manager = PipelineManager()
    await manager.load_text_pipelines(pipeline_load_dir, pipeline_config)  # ✅ 加载了
    # ... 没有对应的 load_output_pipelines() 调用

# flow_coordinator.py:94-96 - 空的输出管道管理器
if self.output_pipeline_manager is None:
    self.output_pipeline_manager = OutputPipelineManager()  # ❌ 创建后从未加载
```

**影响**:

- 所有输出后处理管道（如敏感词过滤 `profanity_filter`）永远不会执行
- 参数验证、参数转换等安全控制被绕过
- 配置的输出管道优先级、启用/禁用状态全部无效

**修复建议**:

在 `FlowCoordinator.setup()` 方法中添加输出管道加载：

```python
# flow_coordinator.py:72-108
async def setup(self, config: Dict[str, Any], config_service=None):
    # ... 现有代码 ...

    # 创建输出Pipeline管理器（如果未提供）
    if self.output_pipeline_manager is None:
        self.output_pipeline_manager = OutputPipelineManager()

    # ✅ 添加：从配置加载输出管道
    pipeline_config = config.get("pipelines", {})
    pipeline_load_dir = os.path.join(_BASE_DIR, "src", "domains", "output", "pipelines")
    await self.output_pipeline_manager.load_output_pipelines(pipeline_load_dir, pipeline_config)
```

---

#### 问题 #2: EventBus 清理期间的竞态条件

**严重程度**: 🔴 严重 - 可能导致崩溃和资源泄漏
**影响范围**: 系统关闭流程
**发现位置**: `event_bus.py:241-256`, `input_provider_manager.py:145-156`

**问题描述**:

EventBus 的 `cleanup()` 方法使用固定的 100ms 延迟等待处理完成，但无法保证所有事件处理器都已执行完毕。同时，`emit()` 在 `_is_cleanup=True` 后直接返回，但已调度的处理器仍可能运行。

**代码证据**:

```python
# event_bus.py:241-256
async def cleanup(self):
    self._is_cleanup = True  # 设置标志

    # 取消待处理的请求
    for future in self._pending_requests.values():
        if not future.done():
            future.cancel()

    await asyncio.sleep(0.1)  # ❌ 硬编码的等待时间，不可靠！
    self.clear()
    self.logger.info("EventBus已清理")

# event_bus.py:115-117
async def emit(self, event_name: str, data: BaseModel, ...):
    if self._is_cleanup:
        self.logger.warning(f"EventBus正在清理中，忽略事件: {event_name}")
        return  # ❌ 但已调度的处理器仍在运行！
```

**影响**:

1. **事件丢失**: 100ms 内未完成的事件处理器被强制中断
2. **资源访问错误**: 处理器可能访问已被清理的资源（如已关闭的 Provider）
3. **状态不一致**: 部分处理器执行完成，部分未完成，导致系统状态不一致
4. **静默失败**: 异步任务被取消但没有正确传播错误

**修复建议**:

使用显式的任务跟踪和同步屏障：

```python
class EventBus:
    def __init__(self, enable_stats: bool = True):
        self._handlers: Dict[str, List[HandlerWrapper]] = defaultdict(list)
        self._active_emits: Dict[str, asyncio.Event] = {}  # 跟踪活跃的 emit

    async def emit(self, event_name: str, data: BaseModel, ...):
        if self._is_cleanup:
            return

        # 创建完成事件
        complete_event = asyncio.Event()
        emit_id = f"{event_name}_{time.time()}"

        async def emit_with_tracking():
            try:
                await self._emit_internal(event_name, data, source, error_isolate)
            finally:
                complete_event.set()
                del self._active_emits[emit_id]

        self._active_emits[emit_id] = complete_event
        asyncio.create_task(emit_with_tracking())

    async def cleanup(self):
        self._is_cleanup = True

        # 等待所有活跃的 emit 完成（带超时）
        if self._active_emits:
            tasks = [event.wait() for event in self._active_emits.values()]
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=5.0)

        self.clear()
```

---

### ⚠️ 警告问题

#### 问题 #3: EventBus 类型安全不对称

**严重程度**: ⚠️ 警告 - 类型安全缺失
**影响范围**: 所有事件订阅者
**发现位置**: `event_bus.py:102-127`, `event_bus.py:168-200`

**问题描述**:

EventBus 的 `emit()` 方法强制要求 Pydantic BaseModel 类型，但 `on()` 注册的处理器接收的是原始字典，造成类型安全的单向性。

**代码证据**:

```python
# event_bus.py:120-125 - emit() 强制类型检查
async def emit(self, event_name: str, data: BaseModel, ...):
    if not isinstance(data, BaseModel):
        raise TypeError(
            f"EventBus.emit() 要求 data 参数必须是 Pydantic BaseModel 实例，"
            f"收到类型: {type(data).__name__}。"
        )
    dict_data = data.model_dump()  # 转换为字典

# event_bus.py:183 - 处理器接收字典
await wrapper.handler(event_name, dict_data, source)
```

**影响**:

1. **编译时类型检查失效**: IDE 无法推断处理器接收的数据类型
2. **运行时类型错误**: 字典结构错误只能在运行时发现
3. **手动反序列化**: 每个处理器都需要手动 `from_dict()` 重建对象

**修复建议**:

引入泛型事件处理器协议：

```python
from typing import TypeVar, Generic, Protocol

T = TypeVar('T', bound=BaseModel)

class TypedEventHandler(Protocol[T]):
    async def __call__(self, event_name: str, data: T, source: str): ...

class EventBus:
    def on_typed(self, event_name: str, handler: TypedEventHandler[T]):
        # 注册时自动反序列化为正确的类型
        original_handler = handler

        async def wrapper(event_name: str, dict_data: dict, source: str):
            # 从 EventRegistry 获取预期的类型
            model_class = EventRegistry.get(event_name)
            typed_data = model_class.model_validate(dict_data)
            await original_handler(event_name, typed_data, source)

        self._handlers[event_name].append(wrapper)
```

---

#### 问题 #4: 管道错误处理可能导致数据损坏

**严重程度**: ⚠️ 警告 - 潜在的数据损坏
**影响范围**: 所有 Pipeline 处理
**发现位置**: `src/domains/input/pipelines/manager.py:292-322`

**问题描述**:

当管道使用 `CONTINUE` 错误处理策略时，异常被捕获后文本保持不变传递给下一个管道。但如果失败的管道已经部分修改了状态（如已更新元数据），可能导致不一致的数据状态。

**代码证据**:

```python
# manager.py:309-321
except Exception as e:
    error = PipelineException(pipeline_name, f"处理失败: {e}", ...)
    self.logger.error(f"TextPipeline 错误: {error}", exc_info=True)

    if pipeline.error_handling == PipelineErrorHandling.CONTINUE:
        # ❌ 只是记录日志，current_text 可能处于部分修改状态
        pass
    elif pipeline.error_handling == PipelineErrorHandling.STOP:
        raise error from e
    elif pipeline.error_handling == PipelineErrorHandling.DROP:
        return None

# 继续执行下一个管道
```

**影响**:

1. **静默数据损坏**: 失败管道的副作用（如已写入数据库、已发送外部请求）无法回滚
2. **难以调试**: 错误被吞掉，下游组件收到不一致的数据
3. **违反最小惊讶原则**: CONTINUE 应该意味着"跳过此管道"，而不是"用损坏的数据继续"

**修复建议**:

添加回滚机制或明确的状态管理：

```python
class PipelineContext:
    """管道执行上下文，支持回滚"""
    def __init__(self, original_text: str, original_metadata: dict):
        self.original_text = original_text
        self.original_metadata = original_metadata.copy()
        self.rollback_actions: List[Callable] = []

    def add_rollback(self, action: Callable):
        self.rollback_actions.append(action)

    async def rollback(self):
        for action in reversed(self.rollback_actions):
            try:
                await action()
            except Exception:
                pass

# 在 PipelineManager.process_text() 中使用
async def process_text(self, text: str, metadata: Dict[str, Any]):
    context = PipelineContext(text, metadata)
    current_text = text

    for pipeline in self._text_pipelines:
        try:
            result = await pipeline.process(current_text, metadata, context)
            if result is None:
                # 丢弃时回滚所有副作用
                await context.rollback()
                return None
            current_text = result
        except Exception as e:
            if pipeline.error_handling == PipelineErrorHandling.CONTINUE:
                # 回滚当前管道的副作用
                await context.rollback()
                # 使用原始文本继续
                current_text = context.original_text
            elif ...:
```

---

#### 问题 #5: 关闭顺序错误导致消息丢失

**严重程度**: ⚠️ 警告 - 消息丢失风险
**影响范围**: 系统关闭流程
**发现位置**: `main.py:381-436`

**问题描述**:

关闭流程中，FlowCoordinator（依赖 InputDomain）先于 InputProviderManager 清理，可能导致正在处理的事件丢失。

**代码证据**:

```python
# main.py:390-396 - FlowCoordinator 先清理
if flow_coordinator:
    logger.info("正在清理数据流协调器...")
    await flow_coordinator.cleanup()  # ❌ 此时 InputProvider 还在运行！

# ... 中间有其他清理 ...

# main.py:408-414 - InputProviderManager 后停止
if input_provider_manager:
    logger.info("正在停止输入Provider...")
    await input_provider_manager.stop_all_providers()  # 晚了！
```

**正确的关闭顺序应该是**:

1. 停止 InputProvider（不再发布新事件）
2. 等待待处理事件完成（grace period）
3. 清理 DecisionManager、FlowCoordinator
4. 清理 EventBus

**影响**:

1. **消息丢失**: InputProvider 仍在发布事件，但 FlowCoordinator 已停止接收
2. **异常**: 处理器尝试访问已清理的资源
3. **状态不一致**: 部分组件已清理，部分仍在运行

**修复建议**:

调整 `run_shutdown()` 的执行顺序：

```python
async def run_shutdown(...):
    # 1. 先停止数据生产者
    if input_provider_manager:
        logger.info("正在停止输入Provider...")
        await input_provider_manager.stop_all_providers()

    # 2. 等待待处理事件完成（grace period）
    logger.info("等待待处理事件完成...")
    await asyncio.sleep(1.0)  # 或使用更智能的同步机制

    # 3. 清理消费者（DecisionManager, FlowCoordinator）
    if flow_coordinator:
        await flow_coordinator.cleanup()

    if decision_manager:
        await decision_manager.cleanup()

    # 4. 最后清理基础设施
    if input_domain:
        await input_domain.cleanup()

    # ... 其他清理 ...
```

---

#### 问题 #6: 手动反序列化造成类型混淆

**严重程度**: ⚠️ 警告 - 类型不安全
**影响范围**: DecisionManager, FlowCoordinator
**发现位置**: `decision_manager.py:208-224`, `flow_coordinator.py:169-174`

**问题描述**:

事件处理器需要手动判断数据是 BaseModel 还是 dict，并调用 `from_dict()` 重建对象。这破坏了类型安全，增加了维护负担。

**代码证据**:

```python
# decision_manager.py:216-221
if isinstance(message_dict, dict):
    # 使用 NormalizedMessage.from_dict() 工厂方法重建对象
    normalized = NormalizedMessage.from_dict(message_dict)
else:
    normalized = message_dict  # 假设已经是对象
```

**影响**:

1. **运行时类型检查**: 无法在编译时发现类型错误
2. **维护成本**: 每个事件处理器都需要重复这个模式
3. **重构风险**: 如果事件格式改变，所有 `from_dict()` 调用点都需要更新

**修复建议**:

结合问题 #3 的修复，在 EventBus 层统一处理反序列化。

---

#### 问题 #7: Normalization 失败后的静默数据丢失

**严重程度**: ⚠️ 警告 - 可观测性缺失
**影响范围**: InputDomain
**发现位置**: `input_domain.py:133-183`

**问题描述**:

当 RawData 转换为 NormalizedMessage 失败时，返回 `None` 并记录错误日志，但调用方无法区分"没有消息"和"转换失败"。

**代码证据**:

```python
# input_domain.py:182
except Exception as e:
    self.logger.error(f"转换RawData为NormalizedMessage时出错: {e}", exc_info=True)
    return None  # ❌ 调用方无法知道是失败还是空消息

# input_domain.py:114-115
if normalized_message:
    self._normalized_message_count += 1
    # 发布事件
else:
    # ❌ 这里可能是"正常没有消息"或"转换失败"
    pass
```

**影响**:

1. **监控盲区**: 无法统计 normalization 失败率
2. **调试困难**: 失败被静默吞掉，难以追踪
3. **数据丢失**: 用户输入被丢弃但没有明确提示

**修复建议**:

引入显式的结果类型：

```python
@dataclass
class NormalizationResult:
    """标准化结果"""
    success: bool
    message: Optional[NormalizedMessage]
    error: Optional[str] = None

# 在 InputDomain.normalize() 中使用
async def normalize(self, raw_data: RawData) -> NormalizationResult:
    try:
        # ... 转换逻辑 ...
        return NormalizationResult(success=True, message=normalized_message)
    except Exception as e:
        return NormalizationResult(
            success=False,
            message=None,
            error=f"转换失败: {e}"
        )

# 调用方可以区分结果
result = await self.normalize(raw_data)
if result.success:
    # 正常处理
    self._normalized_message_count += 1
else:
    # 记录失败统计
    self._normalization_error_count += 1
    self.logger.error(f"Normalization 失败: {result.error}")
```

---

#### 问题 #8: 架构约束依赖开发者自律

**严重程度**: ⚠️ 警告 - 架构约束弱
**影响范围**: 整体架构
**发现位置**: `refactor/design/overview.md:213-296`

**问题描述**:

虽然架构文档明确定义了禁止模式（如 OutputProvider 不应订阅 Input 事件），但没有运行时或编译时强制检查。

**架构约束**:

```
❌ 禁止: OutputProvider 直接订阅 Input 事件
❌ 禁止: DecisionProvider 订阅 Output 事件
❌ 禁止: InputProvider 订阅 Decision/Output 事件
```

**实际代码**:

```python
# decision_manager.py:164 - 虽然符合架构，但没有强制检查
self.event_bus.on(CoreEvents.DATA_MESSAGE, self._on_normalized_message_ready)

# 任何人都可以写出这样的代码（违反架构）：
class MyOutputProvider(OutputProvider):
    async def initialize(self):
        # ❌ 违反架构，但技术上可行！
        await self.event_bus.subscribe(
            CoreEvents.DATA_MESSAGE,
            self.handler
        )
```

**影响**:

1. **架构侵蚀**: 新开发者可能不熟悉架构约束
2. **代码审查负担**: 需要人工检查所有订阅关系
3. **技术债务积累**: 违反架构的代码可能长期存在

**修复建议**:

实现运行时订阅验证器：

```python
class ArchitecturalValidator:
    """架构约束验证器"""

    # 定义允许的订阅关系
    ALLOWED_SUBSCRIPTIONS = {
        "InputDomain": [],  # Input 不订阅任何事件
        "DecisionManager": ["data.message"],
        "FlowCoordinator": ["decision.intent"],
        "OutputProvider": ["output.params"],
    }

    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        # 包装原始的 on() 方法
        self._original_on = event_bus.on
        event_bus.on = self._validated_on

    def _validated_on(self, event_name: str, handler: Callable, subscriber: str = "unknown", priority: int = 100):
        """验证订阅是否符合架构约束"""
        # 获取调用者的类名
        import inspect
        frame = inspect.currentframe()
        caller_class = frame.f_back.f_locals.get('self', None)
        subscriber_name = caller_class.__class__.__name__ if caller_class else subscriber

        # 检查是否允许订阅
        allowed_events = self.ALLOWED_SUBSCRIPTIONS.get(subscriber_name, [])
        if event_name not in allowed_events:
            raise ArchitecturalViolationError(
                f"{subscriber_name} 不允许订阅 {event_name}。"
                f"允许的事件: {allowed_events}"
            )

        # 调用原始方法
        return self._original_on(event_name, handler, priority)

# 在 EventBus 初始化时启用
event_bus = EventBus()
validator = ArchitecturalValidator(event_bus)
```

---

### 💡 建议优化

#### 问题 #9: 未使用的参数

**严重程度**: 💡 优化 - API 混淆
**影响范围**: InputDomain 初始化
**发现位置**: `main.py:289`, `input_domain.py:36`

**问题描述**:

`InputDomain.__init__()` 接受 `input_provider_manager` 参数但从未使用，创建虚假的依赖注入印象。

**代码证据**:

```python
# main.py:289 - 创建时未传递 input_provider_manager
input_domain = InputDomain(event_bus, pipeline_manager=pipeline_manager)

# input_domain.py:36-38 - 参数被接受但未使用
def __init__(self, event_bus, input_provider_manager=None, pipeline_manager=None):
    self.event_bus = event_bus
    self.input_provider_manager = input_provider_manager  # ❌ 从未读取
    self.pipeline_manager = pipeline_manager
```

**修复建议**:

移除未使用的参数，或者明确说明未来用途：

```python
# 选项1: 移除参数
def __init__(self, event_bus, pipeline_manager=None):
    self.event_bus = event_bus
    self.pipeline_manager = pipeline_manager

# 选项2: 标记为预留
def __init__(self, event_bus, input_provider_manager=None, pipeline_manager=None):
    """
    初始化 InputDomain

    Args:
        input_provider_manager: (预留) 未来用于直接访问 Provider 实例
    """
    self.event_bus = event_bus
    # self.input_provider_manager = input_provider_manager  # TODO: 未来版本使用
    self.pipeline_manager = pipeline_manager
```

---

#### 问题 #10: ProviderRegistry 全局状态

**严重程度**: 💡 优化 - 可测试性
**影响范围**: 整个 Provider 系统
**发现位置**: 各个 Provider 的 `__init__.py` 文件

**问题描述**:

Provider 在模块导入时自动注册到全局的 ProviderRegistry，导致：

1. 无法在同一进程中运行多个 Amaidesu 实例
2. 测试难以隔离（需要清理全局状态）
3. 初始化顺序依赖

**示例代码**:

```python
# src/domains/input/providers/console_input/__init__.py
from src.core.provider_registry import ProviderRegistry
from .console_input_provider import ConsoleInputProvider

# ❌ 模块导入时立即注册，无法控制时机
ProviderRegistry.register_input(
    "console_input",
    ConsoleInputProvider,
    source="builtin:console_input"
)
```

**修复建议**:

使用显式注册模式：

```python
# console_input_provider.py
class ConsoleInputProvider(InputProvider):
    pass

# 添加类方法获取注册信息
@classmethod
def get_registration_info(cls):
    return {
        "name": "console_input",
        "class": cls,
        "source": "builtin:console_input",
        "layer": "input"
    }

# 在 main.py 中显式注册
def register_builtin_providers():
    providers = [
        ConsoleInputProvider,
        BiliDanmakuProvider,
        # ... 其他 Provider
    ]

    for provider_cls in providers:
        info = provider_cls.get_registration_info()
        ProviderRegistry.register(
            info["layer"],
            info["name"],
            info["class"],
            source=info["source"]
        )
```

---

## 完整数据流追踪

为了更好地理解问题在系统中的位置，以下是完整的启动到关闭的数据流：

### 1. 启动流程 (main.py:443-481)

```
main() [main.py:443]
  ├─ parse_args() [445]
  ├─ setup_logging_early() [448]
  ├─ load_config() [451]
  │   └─ ConfigService.initialize() [118-128]
  ├─ ProviderRegistry.discover_and_register_providers() [460-467]
  │   └─ ❌ 问题 #10: 全局状态污染
  ├─ validate_config() [467]
  ├─ load_pipeline_manager() [470]
  │   └─ await manager.load_text_pipelines() [215]
  │       └─ ✅ 加载 Input Pipeline
  │       └─ ❌ 问题 #1: 未加载 Output Pipeline
  └─ create_app_components() [472-481]
      ├─ 创建 ContextManager
      ├─ 创建 LLMManager
      ├─ 创建 EventBus
      ├─ 创建 InputProviderManager
      │   └─ await load_from_config() [277-280]
      │       └─ ProviderRegistry.create_input() [300]
      ├─ 创建 InputDomain
      │   └─ ❌ 问题 #9: 未使用 input_provider_manager 参数
      ├─ 创建 DecisionManager
      │   └─ await setup() [299-301]
      │       ├─ ProviderRegistry.create_decision() [128]
      │       └─ event_bus.on(DATA_MESSAGE) [162]
      │           └─ ❌ 问题 #8: 无架构约束检查
      └─ 创建 FlowCoordinator
          ├─ 创建 OutputPipelineManager
          │   └─ ❌ 问题 #1: 从未调用 load_output_pipelines()
          └─ event_bus.on(DECISION_INTENT) [101]
```

### 2. 运行时数据流

```
InputProvider._collect_data() [异步生成器]
  ↓
InputProviderManager._run_provider() [manager.py:207-238]
  ↓ 发布 RawDataPayload
EventBus.emit(DATA_RAW) [event_bus.py:102]
  ├─ ✅ 类型检查: BaseModel
  └─ 转换为 dict → handler(event_name, dict_data, source)
  ↓
InputDomain.on_raw_data_generated() [input_domain.py:74-131]
  ↓ 创建 RawData 对象
InputDomain.normalize() [input_domain.py:133-183]
  ├─ NormalizerRegistry.get_normalizer()
  ├─ ❌ 问题 #7: 失败时返回 None（静默丢失）
  └─ TextNormalizer (如果有文本)
      └─ PipelineManager.process_text() [pipelines/manager.py:237-323]
          ├─ 遍历所有 TextPipeline
          └─ ❌ 问题 #4: CONTINUE 模式可能导致数据损坏
  ↓ 发布 MessageReadyPayload
EventBus.emit(DATA_MESSAGE)
  ↓ 转换为 dict
DecisionManager._on_normalized_message_ready() [decision_manager.py:196-258]
  ├─ ❌ 问题 #6: 手动 from_dict() 重建 NormalizedMessage
  ├─ await self.decide(normalized) [241]
  │   └─ DecisionProvider.decide()
  └─ 发布 IntentPayload
      ↓ EventBus.emit(DECISION_INTENT)
      ↓ 转换为 dict
FlowCoordinator._on_intent_ready() [flow_coordinator.py:154-199]
  ├─ ❌ 问题 #6: 手动 from_dict() 重建 Intent
  ├─ ExpressionGenerator.generate(intent) [176]
  │   └─ 生成 ExpressionParameters
  ├─ OutputPipelineManager.process() [181]
  │   └─ ❌ 问题 #1: OutputPipeline 列表为空，永远不会执行
  └─ 发布 ParametersGeneratedPayload
      ↓ EventBus.emit(OUTPUT_PARAMS)
      ↓ 转换为 dict
OutputProvider.render() [各个 OutputProvider]
  └─ 实际渲染（TTS、字幕、VTS 等）
```

### 3. 关闭流程 (main.py:381-436)

```
run_shutdown() [main.py:381-436]
  ├─ ❌ 问题 #5: 错误的关闭顺序
  ├─ flow_coordinator.cleanup() [390-396]
  │   └─ event_bus.off(DECISION_INTENT) [143]
  ├─ decision_manager.cleanup() [398-405]
  │   └─ event_bus.off(DATA_MESSAGE) [342]
  ├─ input_provider_manager.stop_all_providers() [408-414]
  │   └─ 设置 _stop_event [133]
  │   └─ asyncio.wait_for(gather(...), timeout=10.0) [146-150]
  ├─ input_domain.cleanup() [416-421]
  │   └─ event_bus.off(DATA_RAW) [68]
  ├─ llm_service.cleanup() [423-427]
  └─ core.disconnect() [426-433]
      └─ event_bus.cleanup() [隐式调用]
          └─ ❌ 问题 #2: await asyncio.sleep(0.1) 不安全
```

---

## 修复优先级矩阵

| 问题 | 严重程度 | 修复难度 | 影响范围 | 优先级 |
|------|---------|---------|---------|--------|
| #1: OutputPipeline 未加载 | 🔴 高 | 🟢 低 | 所有输出后处理 | **P0 - 立即修复** |
| #2: EventBus 清理竞态 | 🔴 高 | 🟡 中 | 关闭流程 | **P0 - 立即修复** |
| #5: 关闭顺序错误 | ⚠️ 中 | 🟢 低 | 关闭流程 | **P1 - 尽快修复** |
| #3: EventBus 类型不对称 | ⚠️ 中 | 🔴 高 | 所有事件处理 | **P2 - 计划修复** |
| #4: 管道错误处理 | ⚠️ 中 | 🟡 中 | Pipeline 系统 | **P2 - 计划修复** |
| #6: 手动反序列化 | ⚠️ 中 | 🟡 中 | Decision/Flow | **P2 - 计划修复** |
| #7: Normalization 失败 | ⚠️ 中 | 🟢 低 | InputDomain | **P3 - 有空修复** |
| #8: 架构约束缺失 | ⚠️ 低 | 🔴 高 | 整体架构 | **P3 - 长期改进** |
| #9: 未使用参数 | 💡 低 | 🟢 低 | InputDomain | **P4 - 代码清理** |
| #10: ProviderRegistry 全局 | 💡 低 | 🔴 高 | Provider 系统 | **P4 - 技术债务** |

---

## 总结

Amaidesu 的 3 域架构设计清晰，文档完善，但在实现细节上存在多个需要改进的地方：

### 必须立即修复（P0）
1. **OutputPipeline 未加载** - 这是功能缺失，导致所有输出后处理失效
2. **EventBus 清理竞态** - 可能导致关闭时的崩溃和资源泄漏

### 建议尽快修复（P1）
3. **关闭顺序错误** - 调整关闭流程的组件顺序，避免消息丢失

### 计划修复（P2）
4. **EventBus 类型安全** - 引入泛型处理器，自动反序列化
5. **管道错误处理** - 添加回滚机制，防止数据损坏
6. **移除手动反序列化** - 统一在 EventBus 层处理类型转换

### 长期改进（P3-P4）
7. **架构约束强制** - 实现运行时订阅验证
8. **可观测性增强** - 区分"无消息"和"失败"
9. **代码清理** - 移除未使用的参数
10. **去除全局状态** - 提高可测试性

通过系统性地解决这些问题，Amaidesu 的架构将更加健壮、可维护和类型安全。
