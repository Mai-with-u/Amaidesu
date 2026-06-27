# ADR-001: Pipeline 使用依赖注入而非 Context Object

## Context

Amaidesu 的 Pipeline 系统存在以下问题：

1. **OutputPipelineContext 是死代码**：`src/stages/output/pipelines/base.py:21-36` 定义的 `OutputPipelineContext` 类从未被任何 Pipeline 实现读取其字段（`context.llm_service`、`context.prompt_service`）。grep 整个 `src/` 目录，零次访问。

2. **Pipeline 是项目唯一异类**：Collector、Decider、Handler 都使用构造器注入 DI 模式（参见 `src/stages/decision/deciders/llm/llm_decider.py:69-77`），只有 Pipeline 使用 Context 容器。

3. **Context 模式与服务语义不匹配**：`OutputPipelineContext` 把 LLMManager、PromptManager 这种"有行为、有状态、被调用"的服务对象塞进属性包，违反了 Context Object 应当用于"请求级数据"的语义。

4. **类型安全丧失**：`Optional[Any]` 让 IDE 失明、运行时才发现属性错误、Mock 测试无法用 `spec=` 校验类型。

## Decision

**砍掉 `OutputPipelineContext`，所有 Pipeline 改用构造器注入（DI）模式**：

- Pipeline `__init__(self, config, ...)` 直接接收依赖（按需声明服务参数）
- PipelineManager 用反射 `__init__` 签名从可用服务字典中按名字匹配注入（参考 `src/stages/decision/manager.py:224-245` 的 `_instantiate_decider` 模式）
- 依赖通过 `TYPE_CHECKING` 块 + 字符串注解规避循环导入（参考 `llm_decider.py:26-31`）

**对齐项目标准**：Pipeline 与 Decider/Handler/Collector 一致使用构造器注入 DI。

## Considered Alternatives

### 替代方案 A：保留 OutputPipelineContext，添加类型注解

- **优点**：最小改动
- **缺点**：仍把服务塞进 Context 容器，违反语义边界；Context 字段会成为"上帝对象"风险
- **否决理由**：治标不治本，未来加新服务时 Context 会膨胀

### 替代方案 B：用 Protocol 组合替代 Context

- **优点**：类型安全、显式声明依赖
- **缺点**：增加抽象层；当前零个 Pipeline 使用 Context，YAGNI
- **否决理由**：当前痛点是"无 Pipeline 使用 Context"，不是"Context 类型不安全"。先解决核心问题（砍 Context），未来如果需要 Protocol 再单独设计

### 替代方案 C：保留 Context 并加强文档

- **优点**：零代码改动
- **缺点**：死代码仍是死代码，只是加了注释解释
- **否决理由**：用户明确要求"不要向后兼容 fallback"（直接砍）

## Consequences

### 短期收益

- 删除约 16 行死代码（`OutputPipelineContext` 整个类）
- 简化 Pipeline `__init__` 签名（去掉 `context` 参数）
- 统一项目 DI 模式（Pipeline 与 Decider/Handler/Collector 一致）

### 长期影响

- Pipeline 类型注解完整，IDE/mypy 可以静态分析依赖关系
- 测试时 Mock 可以用 `spec=LLMManager` 做类型校验
- 重构 Pipeline 时，IDE 会标红所有调用点（构造器签名变更）
- 项目内部组件不再有"上帝对象"风险

### 风险

- 现有 Pipeline 都简单（`ProfanityFilterPipeline` 不需要 LLM），重构后行为不变
- 未来如果有 Pipeline 需要 LLM，沿用 Decider 的 `TYPE_CHECKING` 字符串注解模式即可

## Reversibility

回滚方法：`git revert <commit-hash>`，因为：
- 改动集中（`base.py`、`profanity_filter/pipeline.py`、Manager 构造点）
- 没有数据库 schema 变更
- 没有配置文件格式变更（`[pipelines.output.xxx]` key 不变）
- 测试覆盖完整（`test_profanity_filter.py` 锁定了行为基线）

预计回滚成本：5 分钟（一个 git revert）。