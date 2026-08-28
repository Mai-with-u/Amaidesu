# 架构决策记录

本目录保存 Amaidesu 的架构决策记录（Architecture Decision Records，ADR）。ADR 用于记录重要架构选择及其形成原因，帮助后续开发者理解系统为何采用当前设计。

## ADR 格式

项目采用 Michael Nygard 提出的四段式格式：

1. **背景（Context）**：说明问题、约束条件和作出决策时已知的事实。
2. **决策（Decision）**：说明选择了什么方案，以及方案要解决的问题。
3. **替代方案（Alternatives）**：列出考虑过的其他方案，并说明拒绝或采纳的原因。
4. **后果（Consequences）**：说明决策带来的收益、代价和需要持续关注的影响。

格式参考：[adr.github.io](https://adr.github.io/)

## 何时编写 ADR

当一个决定会影响系统边界、组件协作方式、数据流、公共接口、运行时行为或后续维护成本时，应编写 ADR。尤其是以下情况：

- 存在多个合理方案，需要记录取舍；
- 决策会影响多个阶段或多个组件；
- 未来开发者可能需要了解决策背景；
- 变更难以仅通过代码或普通注释解释清楚。

ADR 记录已经作出的决定，不替代实现文档，也不用于记录迁移日志或每次代码变更的过程。

## 现有 ADR

- [ADR-001：Pipeline 使用依赖注入而非 Context Object](001-pipeline-uses-di-not-context.md)（已废弃：管道系统移除）
- [ADR-002：Pipeline[T] 泛型基类设计](002-pipeline-generic-abstraction.md)（已废弃：管道系统移除）
- [ADR-003：@pipeline 装饰器注册机制](003-pipeline-decorator-registration.md)（已废弃：管道系统移除）
- [ADR-004：OutputHandlerManager 直接调度 Handler](004-output-direct-dispatch.md)（已废弃：三阶段架构移除）
- [ADR-005：v2.0.0 采用 Agent + 工具 + 存储 + 编排架构](005-v2-agent-tool-architecture.md)
- [ADR-006：LLM 模拟器是官方开发基础设施，mock 采集器仅承担确定性回放](006-simulator-is-dev-infrastructure.md)
