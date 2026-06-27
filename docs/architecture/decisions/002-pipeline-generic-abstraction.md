# ADR-002: Pipeline[T] 泛型基类设计

## Context

Amaidesu 的 Pipeline 系统存在严重的代码重复：

- **基类重复**：`InputPipelineBase`（`src/stages/input/pipelines/manager.py:16-114`）与 `OutputPipelineBase`（`src/stages/output/pipelines/base.py:39-101`）90% 代码相同（priority、enabled、error_handling、timeout_seconds、process 模板方法、get_info、get_stats）
- **Manager 重复**：`InputPipelineManager`（`src/stages/input/pipelines/manager.py:115-381`）与 `OutputPipelineManager`（`src/stages/output/pipelines/manager.py:29-258`）90% 代码相同（register_pipeline、_ensure_pipelines_sorted、process 主循环、load_pipelines）
- **重复量估算**：约 600 行重复代码

两个 Pipeline 系统虽然处理不同对象类型（`NormalizedMessage` vs `Intent`），但行为契约完全一致：`process(T) -> Optional[T]`，返回 None 表示丢弃。

## Decision

**引入 `Pipeline[T]` 泛型基类和 `PipelineManager[T]` 泛型 Manager**：

```python
T = TypeVar("T")

class Pipeline(ABC, Generic[T]):
    priority: int = 500
    enabled: bool = True
    error_handling: PipelineErrorHandling = PipelineErrorHandling.CONTINUE
    timeout_seconds: float = 5.0

    async def process(self, item: T) -> Optional[T]:
        """统一入口：统计 + 超时 + 错误处理"""
        ...

    @abstractmethod
    async def _process(self, item: T) -> Optional[T]:
        """子类实现具体处理逻辑"""
        ...
```

阶段特化：
- `InputPipeline = Pipeline[NormalizedMessage]`（`src/stages/input/pipelines/__init__.py`）
- `OutputPipeline = Pipeline[Intent]`（`src/stages/output/pipelines/__init__.py`）

`PipelineManager[T]` 同理封装注册、排序、超时、错误处理、统计逻辑。

**泛型约束**：仅 1 个类型参数（T），不加 `TConfig`、`TContext` 等额外参数。多态通过 Protocol 表达（不在本次重构范围）。

## Considered Alternatives

### 替代方案 A：保留独立基类，提取 mixin

- **优点**：最小破坏性
- **缺点**：mixin 在 Python 中模式混乱；现有代码已经两个基类分裂，再加 mixin 会更复杂
- **否决理由**：泛型是 Python 3.12+ 的成熟特性（项目要求 Python 3.12+，参见 README.md），可以直接用

### 替代方案 B：用 Protocol 替代基类

- **优点**：更轻量、鸭子类型友好
- **缺点**：失去 `register_pipeline`、`get_info` 等共享方法的基类实现
- **否决理由**：基类提供了大量共享代码（~600 行），Protocol 无法承载

### 替代方案 C：用 ABC + 抽象方法，但不加泛型

- **优点**：避免泛型复杂度
- **缺点**：Input 和 Output 仍然需要两个独立基类，重复代码问题未解决
- **否决理由**：未解决根本问题

### 替代方案 D：泛型参数超过 1 个（Pipeline[T, TCfg]）

- **优点**：支持配置类型差异化
- **缺点**：增加复杂度；当前配置统一用 `PipelineItemConfig`（见 ADR-003 关联）
- **否决理由**：YAGNI，统一 `PipelineItemConfig` 已经足够

## Consequences

### 短期收益

- 删除约 600 行重复代码（两个基类 + 两个 Manager）
- 新增阶段（如 Decision 阶段管道）只需一行：`class DecisionPipeline(Pipeline[NewType])`
- 类型系统更严格：`Pipeline[NormalizedMessage]` 和 `Pipeline[Intent]` 不会互相赋值

### 长期影响

- 泛型在 runtime 是 no-op（`__class_getitem__`），性能无影响
- IDE/mypy 可以在静态层面校验 Pipeline 的输入输出类型
- Pipeline 文档自动包含类型信息

### 风险

- 现有 Pipeline 实现需要从 `InputPipelineBase`/`OutputPipelineBase` 改为 `InputPipeline`/`OutputPipeline`（仅继承链改动）
- 测试需要小幅更新（基类名引用）
- 行为完全不变（基类合并后只是代码集中，逻辑一致）

## Reversibility

回滚方法：`git revert <commit-hash>`，因为：
- 改动集中在 `src/modules/pipeline/`（新建）+ 现有基类/Manager 文件（替换）
- 现有 Pipeline 实现可临时重新指向旧基类（如需要快速回滚）
- 测试是行为基线，行为不变测试就通过

预计回滚成本：10 分钟（一个 git revert + 可能需要重写一些继承声明）。