# 主播 Agent 决策窗上下文

本文档描述 StreamerAgent 每次 Planner 决策窗喂给 LLM 的完整输入构成：消息形态、参考段、缓存硬要求与输入上限，以及历史读取的单一事实源。实现入口：`src/agents/streamer/planner.py`（消息序列组装）、`src/agents/streamer/canonical.py`（canonical 映射与截断机制）、`src/agents/streamer/planner_context.py`(参考段组装）、`src/agents/streamer/streamer_agent.py`（历史读取链）。

## 消息形态

一次决策窗的首轮消息序列为：

```
[system]  Planner ReAct 系统提示词（amaidesu_planner_react 模板渲染）
[user]    历史对话（live_chat 最近窗口，user/assistant 原生消息，旧→新）
[user]    本批弹幕（当前聚合窗内的观众消息，user 原生消息）
[user]    参考段（元数据参考内容，固定在序列尾部）
```

- **canonical 单一映射**：直播对话进入 LLM 消息数组只有一条序列化路径（`canonical.py`）。live_chat 历史行、历史 turn、弹幕批成员三种输入经同一组纯函数产出 `{role, content}` dict——两条链路（批 vs 历史）逐字同构，不存在"批格式 vs 历史格式"双轨。
- **内容格式**：观众行为 ``[类型前缀] 昵称: 内容 [id:消息ID]``（类型前缀由消息类型登记表的 prompt 模板承载；`[id:…]` 目标机制保留在 content 内）；主播行为原样内容（发言即正文，不加昵称前缀）。
- **批与历史同形**：本批弹幕与历史消息经同一函数序列化，跨窗追加时形态一致——这是缓存稳定的前提。
- **ReAct 循环的 assistant/tool 消息**追加在参考段之后（append-only），绝不插进既有前缀中间。
- **空窗兜底**：对话与参考全空时保底一条 user 消息（部分协议要求非 system 消息存在）。

## 参考段

参考段承载元数据（非对话）内容，由 `PlannerAssembler.assemble` 纯函数渲染，Planner 固定将其作为**一条 user 消息追加在消息序列尾部**：

| 段 | 内容来源 |
|----|---------|
| 环节描述 | 流程单环节说明 |
| 直播间快照 | 时刻（分钟桶）/ 已开播时长 / 当前环节 / 未读摘要 / 关键变化 |
| 记忆召回 | `memory.recall` 关键词召回结果 |

组装顺序固定：环节描述 → 直播间快照 → 记忆召回。**空段规则：有数据才渲染段**（无占位文本）。参考段只追加在尾部、不在中间插入——后续 ReAct 循环的 assistant/tool 消息继续 append-only 追加其后，保持整个消息序列的前缀稳定。

## 缓存硬要求

服务端 LLM 前缀缓存能命中，依赖以下三条硬约束（代码与模板改动都不得破坏）：

1. **跨窗逐字稳定**：同一历史消息跨决策窗的序列化结果必须字节一致。canonical 映射只依赖行自身字段（角色/昵称/内容/类型/消息 ID），不含时间等易变量；直播间快照内部时刻取分钟桶，避免秒级抖动毒化前缀。
2. **成块丢最旧**：历史超预算时从头部整条移除（块 = 单条消息），只丢整块、不切分内容——被保留的前缀与全量形态逐字一致，截断本身不破坏缓存。
3. **参考段不插中**：参考段固定在基座消息序列尾部，对话只在其前追加；运行期动态内容一律进参考段或其后的 ReAct 消息，不修改既有消息。

## 输入上限清单

| 项 | 上限 | 超出行为 |
|----|------|---------|
| 单项内容（弹幕/历史消息/游戏叙事等单条内容） | 2000 字符 | 裁剪至 2000 字符并追加"…（截断）"标记（与工具观察口径逐字统一） |
| 历史消息条数 | 30 条（`history_limit` 配置默认值） | 双上限先到先丢，成块丢最旧 |
| 历史消息总字符 | 12000 字符 | 同上 |
| 工具观察（单次） | 2000 字符 | 截断 |

双上限语义：条数与字符预算**先到先丢**——任一超限即从历史头部成块移除最旧整条，直到落回两项预算内。数值为常量（不引入 tokenizer，不做精确 token 计数；2000 字符单项目标是"正常内容永不截断，只兜病理输入"，12000 字符历史预算按 32K token 窗口倒推留余量）。

## live_chat 单一事实源

历史读取只有一条链，无内存副本、无二次写入：

- **表**：SQLite `live_chat`（全量直播消息流，含 `sender_role` / `sender_name` / `content` / `message_type` / `message_id` / `simulated` 等列），由 `StorageLedger` 订阅 `room.message.#` + `streamer.speech` 唯一写入。
- **索引**：`idx_live_chat_session_ts (live_session_id, timestamp_ms)`——按场次取最近窗口的组合索引。
- **读取链**：Planner 决策窗 / reply 工具 / 后台摘要均经 `StreamerAgent._read_history()` → `LiveSessionManager.resolve_pk()` 解析当前场次主键（与写路径 `StorageLedger` 落库同源）→ `SQLiteStore.list_recent_live_chat(live_session_id, limit=history_limit)` 按时间正序返回最近窗口。
- **空读语义**：无显式场次（首场/未开播）或存储缺失时返回空列表，**不抛错**——首场首决定窗的空读是常态而非异常；读取异常记录 warning 并走各消费方自身的失败路径。

## 相关文档

- [提示词管理](prompt-management.md) - PromptManager 与 strict-only 渲染契约
- [架构总览](../architecture/overview.md) - StreamerAgent 在系统中的位置
- [数据流规则](../architecture/data-flow.md) - 采集器→存储→Agent 的数据流约束
