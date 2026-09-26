# MinecraftAgent 设计

Minecraft 游戏 Agent（AI 玩家）的架构设计。定位：事件驱动的 ReAct Agent——用 MCP 工具玩 Minecraft，主播 Agent 是它的用户。

## 驱动原则

- 只有主播 Agent 自我驱动；游戏 Agent 命令驱动（类 Code Agent）——收到命令启动任务循环，完成即停、空闲零消耗
- 因有自身状态与任务内自主决策，游戏 Agent 仍是 Agent 而非工具（三分判据见 [v2-architecture.md](v2-architecture.md)）

## 核心意象

**MinecraftAgent = 一个用 MCP 工具玩 Minecraft 的普通 ReAct Agent。**

- 系统提示词 + 工具列表 = 全部"编程"，不发明任何特殊协议
- 主播 Agent 是它的用户：发指令（跨 Agent 派活走框架委派 `framework_delegate`，中途插话走递话 `framework_prompt`）/读工作文档（`minecraft_get_work_log`）/收上报（`game.report`）
- 异步任务统一采用受理回执：Mod 施工由后台 tick 驱动，建筑设计由包内子 Agent 推进；系统跟踪任务并在有结果时唤醒游戏 Agent，LLM 不用推理步数轮询
- LLM 可一次返回多个 tool_calls（批量请求 → 串行执行 → 批量作为观察返回，标准 function calling 循环）

## 任务生命周期（事件驱动 ReAct）

```
空闲（事件挂起，零消耗，无 LLM/无 MCP 调用）
  │ 主播经 framework_delegate 委派("建一座房子")
  ▼
消息入队 → 唤醒
  ▼
任务批次（每步 = 一次 LLM 推理）：
  ├─ flush 新消息（主播提示词 / 系统注入的任务快照）→ user 消息
  ├─ 规整对话历史（旧观察 → 占位符，保留最近 N 条）
  ├─ LLM 推理（系统提示词 + 对话历史 + 工具列表）→ tool_calls（可多个）
  ├─ 串行执行（局部工具直接落状态；其余经 ToolRegistry 透传 = MCP 调用）
  │    └─ execute 受理回执（accepted=true + task_id）→ 登记 handoff 跟踪
  ├─ 工具结果作为观察作为观察返回（OpenAI tool role + tool_call_id 关联）
  └─ 批次终止语义（五条，全部系统可判定，见下节）
  → 回空闲（todo/notebook/reports 保留；handoff 跟踪跨批次持续）
```

暂停语义：平台 pause 在步骤间与工具调用间挂起（不打断当前执行中的工具调用），resume 后继续。

后台唤醒会恢复同一逻辑任务的原始指令、待办、笔记和已受理任务编号。推理步数跨批次累计用于记录进展，任务持续推进到交付、等待后台结果或实际阻塞。上报困难或执行中断后，系统通知只能更新状态，新的主播指令才能恢复行动。任务交付后，新指令建立新任务；通知不会独立创建游戏目标。

### 批次终止语义

| # | 情形 | 行为 |
|---|------|------|
| 1 | LLM 调 `minecraft_report(kind=delivery)` | 有未决 handoff 或未完成待办时拒绝交付，返回错误观察；通过门禁后停止 |
| 2 | LLM 调 `minecraft_report(kind=escalation)` | 停止；账面写 `waiting_for_decision`（升级=受阻上报待定夺，非 failed 终态），委派追踪保留，等递话唤醒 / 恢复续跑 / 硬取消清账 |
| 3 | 自然终止，无 report、无未决 handoff 且待办完成 | 系统兜底交付，并结算原委派任务 |
| 4 | 自然终止，仅剩实际运行中的 handoff | 静默让出回合，等 handoff 唤醒 |
| 5 | 仍需行动却在提醒后继续停顿 | `game.attention_required` 挂起，保留原任务等待继续指令 |

待决策、设计已交付但尚未施工、以及没有执行者推进的待办属于需要行动的状态。模型仅输出文本结束，或整轮只重复读取已有证据时，父循环先携带当前决策事实提示它推进或上报具体阻塞；提醒后仍无新进展才挂起。新的现场证据、未读资料或实际操作可以继续推进，重复查询同一任务或来回读相同原文不能解除提醒。

### handoff 跟踪（受理 → 唤醒）

execute 受理 ≠ 完成：等待期 LLM 自由行动（推进其他 todo / 记笔记 / 响应主播），系统负责把后台任务的真实进展送回来。

- **订阅通知**：handoff 登记时订阅 MaiCraft attention 资源（`maicraft://attention`，标准 MCP resources/subscribe，通道能力见 `McpClient.subscribe_resource`）；handoff 清空即退订
- **周期兜底**：订阅通知是提示（advisory，单槽合并、可丢）——`execute_poll_interval_ms` 到点也核实一次（防丢通知/断连）
- **事实核实**：通知/到点后查询任务快照；简短事件仅提示需要核实，同一页每个任务核实一次。状态变化或新的原生决策才唤醒模型，查询失败继续由跟踪器兜底，不能凭简短完成通知移除交付门禁。
- **wait_timeout**：`execute_wait_timeout_ms` 长期无进展 → 注入告警消息（不杀任务，deadline 顺延），LLM 自行决定后续
- 终态（success/failed/timeout/cancelled）注入后移除跟踪；决策点（waiting_for_decision）/暂停注入后保留跟踪（LLM 用 `maicraft_task(action="answer")` 应答后任务恢复后台跑）

### 运营干预入口（递话 / 硬取消 / 断连恢复）

委派之外，minecraft 另有两个干预入口与一条自愈路径（原语定案见 ADR-034，账面语义见 ADR-035）：

- **递话 `receive_prompt`**：纯文本留言（主播经 `framework_prompt` 工具、运营经 REST），不派新任务、不进账本——文本入消息队列（任务号空串）+ 唤醒。任务执行中下一步推理前被 flush 吸收；挂起中唤醒重新判断；任务已交付时它不构成新任务起点（派正式新活走委派，任务卡可见）。
- **硬取消 `cancel_task`**：任务在委派追踪清单 → 清清单 + 账面写 cancelled 终态 + 注入"[系统] 任务已被取消"通知，LLM 下一步自行停手走既有终止语义；终态粘滞保证其后的迟到收尾写不进账。软取消（与 pause 同哲学）：不打断当前工具调用。`waiting_for_decision` 僵尸账以取消为清场手段。
- **MCP 断连恢复续跑**：私有 MCP 恢复循环装配成功后注入"连接已恢复"通知 + 解锁挂起态 + 唤醒——批次在跑则通知被下一步吸收，已挂起则被唤醒重跑；账面由批次重启对追踪清单旧委派重写 running（escalation 留账不再是死账，交付终态有处可写）。

## 工具契约

### 按需建筑设计

Minecraft Agent 把建筑设计委派给包内的 `MinecraftBuilderAgent`，收到回执后继续处理游戏工作。
子 Agent 具有独立的模型用途、对话历史和总任务预算；根据 Mod 资源目录按需读取资料，生成并校验设计。
建造生成不施加宿主的固定输出 token 上限；非正常结束或任一工具参数不完整时，整轮不执行并反馈重试。
设计通过现有 Mod 场景操作创建、按名编辑和检查，受理后必须核实终态；对象编辑允许分次提交完整 JSON。
程序通过标准资源读取取得完整 Schema 和原文，模型工具查询可以只收到概要与引用。建筑适配器遇到省略的终态证据时，从同一已结束任务按路径取回，确认版本、场景身份和未施工标记后才交付设计；归档清单不能被当成无约束 Schema。
子 Agent 不控制角色、不启动施工；设计交付后由父 Agent 按产物引用发起 Mod 施工任务，核实真实施工终态后才报告建好。
用户已经要求建好且设计可用、符合要求时，父 Agent 继续备料与施工；用户只要求设计时保持只设计。审阅的成功回执必须结合实际可施工性判断，不能代替施工或产出验收。

受理与结果复用通用任务账本，设计任务由执行 Agent 写入状态，施工任务由 Mod 查询适配器核实。
设计结果保存在 Minecraft 当前会话，账本移除终态条目后仍可查询；完整建筑内容不随任务通知复制到父级对话。
父 Agent 停止时先收束子任务再关闭共享 MCP，未启用 Minecraft 时建造入口也不存在。

这类受管子 Agent 不独立加入顶层名册，配置与业务代码内聚于 Minecraft 包。
能力与工具协议见[新建造器接入指南](../guides/minecraft-builder.md)，具体参数以该指南链接的代码契约为准。

**注册名 = `<Provider名字>_<工具名字>`**，分隔符 `_`（满足 LLM function calling 工具名字符集约束）。Provider 名全局唯一、用全名（`minecraft` 禁缩写）；工具名 Provider 内唯一、语义化。前缀由模块声明（provider 值）、ToolRegistry 一处拼接——工具名里不手写前缀。

**决策工具列表**（LLM 可见，注册名）：

| 工具 | 说明 |
|---|---|
| `minecraft_todo` | 待办文档（read/write 全量读写，无 id）。任务分解与推进由 LLM 自主决策 |
| `minecraft_notebook` | 工作笔记（read/write）。持久记忆：对话历史会压缩、笔记不会——重要发现写这里 |
| `minecraft_report` | 上报通道（玩家→主播唯一发声出口）：delivery 交付总结 / escalation 升级决策 |
| `maicraft_perceive` / `maicraft_execute` / `maicraft_task` 等 | registry 动态发现的 MCP 工具（每任务重新拉取）；参数按 Mod 定义填写 |

**对外工具**（经 ToolRegistry 注册、主播工具列表可见，不进玩家 LLM 工具列表）：
- `framework_delegate`：跨 Agent 委派通道——把工作交给另一 Agent（指令只当自然语言，不给步骤）；BaseAgent 默认拒收，minecraft 实现接收入口（指令入队带任务号 + 唤醒）。跨 Agent 派活的发送侧走框架委派而非 mcp 工具
- `minecraft_get_work_log`：工作文档读服务——只读返回 `{todo, notebook, recent_reports}` 三元组；本工具不查异步任务记录表，查任务进度用 `framework_task_status`（跨 Agent 委派 + 回执型工具的当前状态与快照）

## 事件契约（确定性系统事件，无 LLM 自觉汇报）

| 事件 | 触发 |
|---|---|
| `game.report` | LLM 调 `minecraft_report`（delivery/escalation）或批次终止系统兜底交付；kind 见 `GamePayload.report_kind` |
| `game.attention_required` | 任务需要行动但模型经提醒仍未推进，或上下文整理失败 |
| `game.error` | 工具执行异常 / LLM 调用失败 / 无 LLM fail-fast |

事件 payload 复用 `GamePayload`（`game="minecraft"`）；上报同时进内存 `recent_reports`（状态查询数据源，保留最近 10 条）。`game.milestone` 不再由本 Agent 发射（todo-diff 自动里程碑已移除，防主播叙事刷屏）。

## 对话管理与压缩

- 消息累积：任务内多轮，经 `LLMManager.generate(messages, profile=..., tools=...)`（OpenAI 格式消息）；工具结果以 `tool` role + `tool_call_id` 关联作为观察返回
- 保存的是实际收到的回执，可能含省略引用；本地原件不能代替尚未取得的远端内容。历史超过整理阈值时只总结旧调用组，最新调用组保持可读。
- 原始指令、工作文档、有效计划和待应答决策独立于摘要保留。目录只带有限的近期引用与短请求摘要，完整参数按引用分页读取，旧记录可搜索；目录访问时间和排序变化不算新游戏进展。
- 保留原生决策编号、选项、消费不确定性和重试限制，并区分本地原文路径与远端证据入口。程序补读验证页序与同一快照身份；事件未完整取回时不推进游标。模型只读取影响当前判断的内容。
- 流式不做：决策 Agent 非聊天 Agent，文本断续；对外叙事经事件→主播侧已有流式表达；WebUI 观察走事件快照

## 配置

```toml
[agents.minecraft]
execute_poll_interval_ms = 2000   # handoff 周期兜底核实间隔
execute_wait_timeout_ms = 1800000 # 后台任务单轮 wait_timeout 上限（告警不杀任务）

[agents.minecraft.mcp]            # Agent 私有 MCP（位置即归属，owner_agent="minecraft"）
enabled = true
url = "http://127.0.0.1:8766/mcp"
```

配版本同步规则：配置结构变更升该文件 `[meta].version` 并保证漂移写回（见 ADR-014）。

## 解耦边界

- 通用 MCP 层只归一化协议数据；MaiCraft 的工具别名、页引用和场景证据语义留在 Minecraft 包。实际调用使用服务声明的原名，仅兼容已发布的别名，用户自定义绑定保持原样。
- 工具失败作为错误观察返回 LLM，由模型根据错误原因调整行动，确实无法推进时上报具体阻塞
- 内容特有逻辑内聚 `src/agents/minecraft/` 包（加内容=加包+配置，框架零改动）

## 相关文档

- 组件图与目录结构以代码为唯一事实源（`src/`、`ToolRegistry`）
- [架构叙事](v2-architecture.md) — Agent/Tool 判据推导
- [事件系统](event-system.md) — game.* 事件语义单一事实源
- [数据流规则](data-flow.md) — 事件流约束
- [组件开发指南](../guides/component.md) — 游戏 Agent 范式
- [ADR-034](../decisions/034-agent-intervention-primitives.md) — 递话/硬取消/运营直派原语定案
- [ADR-035](../decisions/035-escalation-ledger-semantics.md) — escalation 账面语义与断连恢复续跑
