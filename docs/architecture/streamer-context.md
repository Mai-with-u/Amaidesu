# 主播 Agent 决策窗上下文

本文档描述 StreamerAgent 每次 Planner 决策窗喂给 LLM 的完整输入构成：消息形态、参考段、缓存要求、历史时间线与预算，以及历史读取的单一事实源。实现入口：`src/agents/streamer/planner.py`（消息序列组装）、`src/agents/streamer/canonical.py`（canonical 映射）、`src/agents/streamer/planner_context.py`(参考段组装）、`src/agents/streamer/timeline.py`（历史时间线）、`src/agents/streamer/streamer_agent.py`（历史读取链）。分层布局与预算原语见 `src/modules/context/`。

## 消息形态

一次决策窗的首轮消息序列为：

```
[system]  Planner ReAct 系统提示词（amaidesu_planner_react 模板渲染）+ 长期笔记 + 上场回顾
[user]    历史对话（时间线：前情提要各章 + 工作层原文，user/assistant 原生消息，旧→新）
[user]    本批弹幕（当前聚合窗内的观众消息，user 原生消息）
[user]    参考段（元数据参考内容，固定在序列尾部）
```

- **系统提示词末尾的记忆段**：长期笔记（全场条目，按开着的游戏筛）与上场回顾都在开播时读出、整场不变，接在模板渲染结果之后，与人设一起吃前缀缓存（见[主播记忆](streamer-memory.md)）。
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
| 人物画像 | `viewer_profiles` 观众印象与长期笔记里对这位观众的条目（本批发言人、有内容才注入；见[主播记忆](streamer-memory.md)） |
| 备忘录 | 本场备忘录全文（模型经 `streamer_notebook` 写） |

组装顺序固定：环节描述 → 直播间快照 → 人物画像 → 备忘录。参考段前面另有情境标注、私密游戏聊天、运营提醒与只读参考工具的结果；整场不变的长期笔记与上场回顾不在参考段，在系统提示词末尾。**空段规则：有数据才渲染段**（无占位文本）。参考段只追加在尾部、不在中间插入——后续 ReAct 循环的 assistant/tool 消息继续 append-only 追加其后，保持整个消息序列的前缀稳定。

## 直播画面

`[agents.streamer.live_frame]` 启用、collectors.toml 启用 `live_frame` 采集器、且 planner 主模型支持视觉时，决策窗第一次请求的末尾多挂一条 user 消息：【直播画面】说明 + 最新一帧画面（图像暂存 `VisionImageStore` 的句柄，查询式取自 latest 槽位）。规则：

- 只挂第一次请求，同窗后续 ReAct 步骤不带；不写进消息序列，前缀不受影响；
- 画面超过 `max_age_ms` 视为过时不看；看过的那一帧不再看；两次之间至少隔 `min_interval_ms`；
- 画面只用于描述观感与氛围，游戏里的数值和进展以游戏记录与查询结果为准。

## 缓存硬要求

服务端 LLM 前缀缓存能命中，依赖以下三条硬约束（代码与模板改动都不得破坏）：

1. **跨窗逐字稳定**：同一历史消息跨决策窗的序列化结果必须字节一致。canonical 映射只依赖行自身字段（角色/昵称/内容/类型/消息 ID），不含时间等易变量；直播间快照内部时刻取分钟桶，避免秒级抖动毒化前缀。
2. **历史只追加**：当前场次的已有消息完整保留，新消息追加到尾部；相同消息跨轮逐字一致。
3. **参考段不插中**：参考段固定在基座消息序列尾部，对话只在其前追加；运行期动态内容一律进参考段或其后的 ReAct 消息，不修改既有消息。

## 历史时间线与预算

Planner 与 Replyer 共用同一份历史投影（`StreamerTimeline`）：

- **只追加**：记录按主播第一次看到的先后进入时间线，进去后不挪位置、不改写；相邻两个决策窗的历史前缀逐字一致。
- **预算**：`[agents.streamer.context]` 的 `window_ratio` / `max_tokens` 按 planner、replyer 档位里最小的模型窗口换算（窗口未配置按 128k 计）。
- **按章压缩**：历史越过提前整理线（`prepare_ratio`）时，后台用 summary 档位把最老的一段写成一章前情提要，写好后的下一窗换上，压回到回落线（`target_ratio`）；最近 20 条始终保持原文。章节只追加，两次换章之间前缀不变。
- **兜底**：摘要迟迟出不来、历史涨到预算两倍时，同步把最老一段换成占位说明，避免撞上模型窗口。
- **场次边界**：开播、下播、读到的场次变化时时间线清空重来。

时间线里除了对话，还有两类游戏记录（`message_type = "game"` 的 user 条目，行首是发生时刻的钟点）：游戏里程碑（`game.*` 的进展、上报、需要注意、异常）与"我在哪个游戏里开始做什么"（委派受理时记下主播自己的原话）。参考段的【游戏叙事】只回放最近一阵并标"新"，时间线留下整场的先后，换章时一起写进前情提要。前情提要是 `message_type = "chapter"` 的 user 条目。两类条目 canonical 都原样渲染。时间线内置前缀自检：两次换章之外稳定层被改写时记告警。

## 输入完整性

本批弹幕、游戏叙事和观众画像均完整注入；工作层内的对话原文完整保留，只有越过预算的最老一段换成前情提要。消息尾部的要求、身份与目标标识不会因字符数被移除。历史按当前直播场次隔离，整场对话保持时间正序。

工具观察保留完整字段与正文，序列化后直接追加到 ReAct 消息序列。读取某个字段的结果由工具真实返回值决定，Planner 不再自行丢弃字段或替换成截断标记。

LLM 请求完整发送消息与工具声明，宿主不设置生成 token 上限或请求截止时间。调用方主动取消时回收请求，服务端返回失败时按原有错误分类处理。

## live_chat 单一事实源

历史读取只有一条链，无内存副本、无二次写入：

- **表**：SQLite `live_chat`（全量直播消息流，含 `sender_role` / `sender_name` / `content` / `message_type` / `message_id` / `simulated` 等列），由 `StorageLedger` 订阅 `room.message.#` + `streamer.speech` 唯一写入。
- **索引**：`idx_live_chat_session_ts (live_session_id, timestamp_ms)`——按场次取最近窗口的组合索引。
- **读取链**：Planner 决策窗 / reply 工具均经 `StreamerAgent._read_history()` → `LiveSessionManager.resolve_pk()` 解析当前场次主键（与写路径 `StorageLedger` 落库同源）→ `ChatRepo.list_recent_live_chat(live_session_id, limit=None)` 按时间正序返回当前场次完整对话 → 时间线投影（见上节）。
- **空读语义**：无显式场次（首场/未开播）或存储缺失时返回空列表，**不抛错**——首场首决定窗的空读是常态而非异常；读取异常记录 warning 并走各消费方自身的失败路径。

## 相关文档

- [提示词管理](../guides/prompt.md) - PromptManager 与 strict-only 渲染契约
- [v2 架构叙事](../architecture/v2-architecture.md) - StreamerAgent 在系统中的位置
- [数据流规则](../architecture/data-flow.md) - 采集器→存储→Agent 的数据流约束
