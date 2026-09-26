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

ADR 编号按创建时间递增，不因条目删除而重排——001~004 因对应系统（管道 / 三阶段输出）已移除而不再列出，从 005 起保留编号语义（被删除条目的编号空缺以避免交叉引用失锚）。

- [ADR-005：v2.0.0 采用 Agent + 工具 + 存储 + 编排架构](005-v2-agent-tool-architecture.md)
- [ADR-006：LLM 模拟器是官方开发基础设施，mock 采集器仅承担确定性回放](006-simulator-is-dev-infrastructure.md)
- [ADR-007：TTS 作为配置驱动基础设施（reply → utterance 事件 → 引擎）](007-tts-infrastructure-pipeline.md)
- [ADR-008：主播思考流旁路通道（观察面流式 / 播出面整段）](008-streamer-thinking-stream-bypass.md)
- [ADR-009：Agent 私有 MCP 与工具归属限定（位置即归属，装配即声明）](009-agent-owned-mcp.md)（2026-09-11 修订：归属 + 名单双轴）
- [ADR-010：工具可用性手动操作（手动重连）](010-tool-availability-reconnect.md)
- [ADR-011：Rundown 流程单取代 Agenda 编排子系统](011-rundown-replaces-agenda.md)
- [ADR-012：工具可见名单机制（注册处生产侧声明）](012-tool-visibility-list.md)
- [ADR-013：异步任务基建与 Agent 委派原语](013-async-task-infrastructure-and-delegation.md)
- [ADR-014：配置体系六文件重构](014-config-six-file-refactor.md)
- [ADR-015：live_chat 单一事实源，删除 ContextService](015-live-chat-single-source.md)
- [ADR-016：版本号与发布模型（pyproject 单一声明 + tag 事实源 + main 发布线）](016-versioning-and-release-model.md)
- [ADR-017：主播 Agent 分包按接缝抽厚簇（执行抽、调度不抽）](017-streamer-agent-seam-split.md)
- [ADR-018：游戏无关边界——框架与主播侧不出现具体游戏名](018-game-agnostic-boundary.md)
- [ADR-019：ToolRegistry Provider 常驻登记与工具集刷新（工具页注册表驱动）](019-tool-registry-provider-refresh.md)
- [ADR-020：dashboard 服务层分层（api 协议转换 / services 业务逻辑 / schemas 契约）](020-dashboard-service-layer.md)
- [ADR-021：全站时间字段毫秒统一（Unix epoch 毫秒 int，原子切换）](021-millisecond-time-unification.md)
- [ADR-022：列表型配置是一等公民——整值编辑与索引回填](022-list-config-as-first-class.md)
- [ADR-023：皮套平台无关边界——平台差异全关进适配器](023-avatar-platform-agnostic-boundary.md)
- [ADR-024：口型帧级通道——AudioSink 分接 + 共享分析器 + 平台渲染器](024-lipsync-frame-channel.md)
- [ADR-025：字幕单一源——装配期择流 + Warudo 字幕面退役](025-subtitle-single-source.md)
- [ADR-026：皮套工具面契约——语义参数、发现协议与结构类型规范](026-avatar-tool-surface-contract.md)
- [ADR-027：动态配置段统一注册表机制——工具提供者 config 纳入校验与默认值补全](027-dynamic-config-section-registry.md)
- [ADR-028：avatar 配置迁出 tools 域——第七配置文件与皮套配置结构](028-avatar-domain-config-graduation.md)
- [ADR-029：观众身份复合键与平台虚拟货币口径（platform+user_id、金瓜子标价口径、raw 可复现性原则）](029-viewer-identity-key-and-currency-unit.md)
- [ADR-030：观众画像系统与记忆层重构（事实提取/增量压缩/有画像才注入/私有表机制废除）](030-viewer-profile-system.md)
- [ADR-031：组件间通信通道指派学说（五维框架 + 四角落裁决 + 两阶段装配）](031-communication-channel-doctrine.md)
- [ADR-032：LLM 运行时边界与入口收口（generate 双方法 / 绑定在代码 / 用途封闭）](032-llm-runtime-boundary-and-entry.md)
- [ADR-033：LLM 思考强度控制三原则（设了才发 / 自由字符串 / 方言逃生舱）](033-llm-reasoning-effort-three-principles.md)
- [ADR-034：Agent 干预原语三件套（递话/硬取消/运营直派，传输同路记账分家）](034-agent-intervention-primitives.md)
- [ADR-035：escalation 账面语义修正（failed 终态改写 waiting_for_decision）](035-escalation-ledger-semantics.md)
