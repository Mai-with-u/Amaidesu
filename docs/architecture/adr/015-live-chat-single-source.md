# ADR-015：live_chat 单一事实源，删除 ContextService

- 状态：已接受（2026-09-12 定案并实现）
- 日期：2026-09-12
- 实现提交：`09cfa7cc`（ContextService 删除、读取改道、组装器并入主播包、对话原生化、表达面收口与输入预算）

## 背景（Context）

ContextService 曾按"四层"设计：事实源（SQLite `live_chat`）→ 内存配对窗口 → 启动回灌 → 观众画像。实装与其偏差持续扩大：

1. **配对窗口只有半个输入**：主播自己发言写入，弹幕全仓零生产写入——窗口内容系统性失真。下游`topic_summary` 摘要因读不到弹幕恒空，三路主动发言被话题闸永久阻塞（行为级缺陷链）。
2. **回灌被有意删除**（防跨场污染），启动重建 API 与测试留存原地成为死面。
3. **第二事实源成本**：内存窗口与 `live_chat` 双写漂移、float 秒时间字段违反全仓毫秒约定、`build_context(max_tokens)` 等承诺面未实现。
4. **读取无索引**：`live_chat` 按 `(live_session_id, timestamp_ms)` 窗口读取是热路径，但只有 message_id 单列索引。

## 决策（Decision）

核心一句：**删除 ContextService，`live_chat` 是对话历史的唯一事实源；所有读取点直接走存储层窗口查询。**

- 三个活消费点（决策窗、回复路径、后台摘要）改经 `SQLiteStore.list_recent_live_chat`（角色过滤 + limit）；补组合索引 `idx_live_chat_session_ts(live_session_id, timestamp_ms)`（幂等 DDL，随表 DDL 块创建，不升 SCHEMA_VERSION）。
- 场次标识统一经 `LiveSessionManager.resolve_pk()`，与写路径（StorageLedger）同源；无显式场次（首场首窗）读取返回空列表，不抛错。
- 配套结构收口：组装器收缩并入 `src/agents/streamer/planner_context.py`（`src/modules/context/` 目录消失）；对话改为原生 user/assistant 消息形态（canonical 单一映射，见 `docs/development/streamer-context.md`）；`/api/messages` 死端点、`session_selector` 死模块与配套测试一并清除；context 域秒单位字段随删清零。

## 替代方案（Alternatives）

### 修复 ContextService（弹幕写入 + 重建回灌）

**拒绝**。两个事实源的结构性漂移不解决：窗口语义（配对、保留期、退场策略）与存储窗口查询重复维护；回灌重新引入跨场污染与落库时序耦合。直播场景的对话历史天然有 SQLite 承载，内存层是多余中间层。

### 窗口查询加缓存层（读性能）

**拒绝**。读取频率为每分钟个位数到十几次，组合索引后查询成本趋近常数；YAGNI。

## 后果

- 对话历史的读写只剩一条路径，弹幕进历史由落库订阅保证，`topic_summary` 断链随之修复。
- 未开场次的"历史"不再可见（接受：直播外无对话语义）。
- 需要消息级关联（如配对）时用 `live_chat` 现有 `message_id` / `reply_to_message_id` 列重建，不复活独立服务。

## 参考

- [开发指南 · 主播上下文构成](../../development/streamer-context.md)
- [架构总览](../overview.md)
