# ADR-005：v2.0.0 采用 Agent + 工具 + 存储 + 编排架构

- 状态：已采纳
- 日期：2026-08-23（定案）/ 2026-08-25（实现落库）
- 实现提交：`22519a5c057b8675fdd83faca2e6e0ac0a070f1b`（feat(v2): 主播 Agent——planner/replyer 决策内核与 Agenda 子系统）；配套落地见 `1012b31`（采集器主动推事件）、`4187a54`（webui 适配）等 v2 提交链

> **完整叙事**（四代架构史、主体性判据推导、防换皮铁闸、九 Wave 落地过程）见 [v2-architecture.md](../v2-architecture.md)；本文仅保留决策记录的标准四段式。

## 背景（Context）

Amaidesu v1 采用 Input → Decision → Output 三阶段架构：InputCollector 采集数据标准化为 NormalizedMessage，Decider 将其决策为 Intent（回复内容+情绪+动作），OutputHandlerManager 直接调度各 OutputHandler 渲染。该架构在长期演进中暴露出结构性问题：

1. **Intent 是虚构的中间表示**：决策出口本质是"调用渲染能力"，Intent 数据类与 `decision.intent.generated` 事件链是为此虚构的管道件，实际消费方只有输出调度点。
2. **三套参与者生命周期**：Collector（start/stop/collect）、Decider（setup/cleanup/decide）、Handler（init/cleanup/handle）三种范式并存，理解成本高。
3. **内容扩展必须动框架**：新增直播玩法或游戏代理需要修改阶段管理器、注册表与事件链，框架无法做到"加内容零改动"。
4. **主体性错位**：主播的核心循环（聚合弹幕→判断是否说话→生成表达→驱动渲染）被拆散在三个阶段的 Manager 里，没有一处代码拥有"自主驱动"的完整闭环。

与此同时，MaiBot 桥接路线已确认放弃（maibot_decider 移除），项目需要一条独立演进的主干架构。

## 决策（Decision）

重构为 **Agent（自主主体）+ 工具（能力契约）+ 存储（状态/记忆）+ 编排（Agenda 节目单）** 架构：

1. **主体性判据为最高约束**——Agent 与工具的唯一判别是"谁驱动谁"：
   - Agent：自我驱动、有循环/目标、没人调也在跑（主播 Planner 决策循环、游戏代理 AI 玩家）
   - 工具：被动驱动、被调才干活、无循环（Replyer 表达引擎、TTS、屏幕捕捉、VLM）
   - 直播内容是编排配置 + Planner 上下文/行为模式的变化，不是代码模块
2. **组件收敛为三类范式**：采集器（BaseCollector，`collect()` AsyncIterator + 主动推语义域事件）、业务 Agent（BaseAgent 协议六面 + `list_tools()` 抽象，自包含包放 `src/agents/<family>/<name>/`）、工具（ToolProvider Protocol + ToolSpec，经 ToolRegistry 统一调度，失败兜底不抛异常）。
3. **Planner/Replyer 是主播 Agent 内脏**，不注册为工具；决策出口 = Agent 调用自有 `reply` 工具，删除 Intent 中间表示与 `decision.intent.generated` / `output.intent.*` 事件链。
4. **事件系统升级为语义域命名 + 通配订阅**：`live.*` / `room.message.*` / `game.*` / `agenda.*` / `planner.checkpoint` / `tool.result.#`；旧三阶段事件名全部删除；输入净化职责由 EventBus 分发层的事件拦截器承担（见 ADR-001~003 废弃记录与 §1.46.1 定案）。
5. **防插件换皮红线**：内容特有逻辑内聚 `src/agents/<family>/<name>/` 包内，加内容=加包+配置，框架层（src/modules/）零改动、不含内容逻辑。
6. **配置收敛为七文件**（core/model/agents/tools/memory/storage/background），Schema 即真相 + 版本化迁移钩子。

## 替代方案（Alternatives）

### 继续演进三阶段架构

拒绝。Intent 抽象、三套生命周期与"加内容动框架"是结构问题，修补无法消除；主播自主性被 Manager 链条肢解的问题在原架构下无解。

### 回归插件系统 / 服务注册机制

拒绝。插件系统已在早期重构中移除；服务注册机制隐藏依赖关系，EventBus 显式订阅更可审计。

### 以 MaiBot 为决策核心

拒绝。两库架构已显著分化，桥接层维护成本高于收益；Amaidesu 需要面向直播场景的自主决策循环，而非通用对话决策。相关桥接组件（maibot_decider 等）已删除。

## 后果（Consequences）

- 收益：单一 Agent 包内聚完整决策闭环；扩展内容 = 新增自包含包；工具生态统一 ToolSpec 契约（约 60 个工具可被任意 Agent 复用）；事件名自带语义便于监控与通配订阅。
- 代价：一次性迁移成本高（九个 Wave 的渐进重构）；Agent 内部复杂度上升（Planner 循环、Agenda 子系统、后台双任务都在一个包里），需要靠包内模块边界自律。
- 需持续关注：渲染工具的注册接线尚未完全自动化（已知缺口）；`@tool` 装饰器与 ToolProvider 双路径并存需在文档中明确主路径；旧 `schemas/input_schemas.py`、`output_schemas.py` 与 `src/modules/simulator/` 为迁移期遗留，待清理。
