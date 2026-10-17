# 存储

一句话形状：**SQLite 单库存状态，建表 DDL 以 `src/modules/storage/schema.py` 为唯一事实源（任何 CREATE TABLE 必须落在此文件），访问层按域拆分仓储；所有写入走旁路语义——存储失败记日志，不阻断直播。**

## 表地图

全集以 schema.py 的建表清单为准，按域分组：

**场次与消息**

- `live_sessions`：一场直播一行，开播/下播边界与人数、热度实时状态。
- `live_chat`：全量消息流，观众/主播/搭档同表（`sender_role` 区分）；`message_id` ↔ `reply_to_message_id` 构成"主播回复了哪条弹幕"的关联键。
- `gifts` / `super_chats` / `guards`：付费明细三表，按平台原始单位入库（口径见 ADR-029）。
- `timeline_summary`：一场内的分段摘要轨迹（后台维护者写入）。

**观众与画像**

- `viewers`：跨场观众统计，身份键 `UNIQUE(platform, user_id)`（ADR-029）。
- `viewer_profiles`：观众印象文本，收播时批量整理（ADR-042）。
- `session_recaps`：场次回顾，收播写、开播注入。

**流程单（五表族，ADR-011）**

- `rundowns` / `rundown_segments`：流程单模板与环节行。
- `rundown_instances` / `rundown_instance_segments`：每场实例快照（崩溃恢复的游标与计时锚点存这里）。
- `rundown_transitions`：只增不改的变更事实行（"实际停留 vs 预期停留"的分析面）。

**游戏与诊断**

- `game_events`：游戏里程碑、安全阀、异常。
- `topics`：当前话题快照投影（每场清旧插新）。
- `llm_usage` / `llm_requests`：LLM 记账与请求历史，同事务双账。

**模拟器**

- `sim_personas` / `sim_gifts`：模拟观众人设与礼物目录（ADR-006，开发基础设施）。

`live_chat` / `gifts` / `super_chats` / `guards` 四表带 `simulated` 贯穿列，统计查询一律 `WHERE simulated = 0`——模拟观众不是观众。

## 迁移机制

- 版本声明 `SCHEMA_VERSION` 在 schema.py；`schema_migrations` 表记录已应用版本。
- 迁移一版本一文件、编号严格 +1（`src/modules/storage/migrations/`），注册表完整性由守护测试断言。
- 启动时先执行完整 DDL（`IF NOT EXISTS` 幂等），再按当前版本到目标版本的区间依次跑迁移回调；回调原地修改、自带列存在性检查，重复执行无副作用。
- 升版本前自动在线备份到 `data/backups/`（`<库名>-pre-v<版本>-<时间戳>.db`）；备份失败仅告警不阻塞迁移，备份不自动清理。
- 库版本高于代码版本（回滚到旧程序）只告警不拒启。

## 访问层

- 仓储按域拆分在 `src/modules/storage/repos/`（sessions / chat / viewers / viewer_profiles / recaps / topics / events / llm / rundowns / rundown_instances / rundown_transitions / sim），清单以 `repos/__init__.py` 导出为准；同步 SQLite 调用统一经 `asyncio.to_thread` 包装，事件循环零阻塞。
- 主要写入方：
  - **StorageLedger**：订阅 `room.message.#` 落明细表与观众计数、订阅 `streamer.speech` 写主播发言行（带回复关联）、订阅 `game.*` 写游戏事件；不建场次行（归 LiveSessionManager）。
  - **LLM 观察面**：`src/modules/llm/observation.py` 每次调用同事务写 usage 与 requests 两账。
  - **收播整理**：场次回顾与观众印象由主播 Agent 收播钩子写入。
  - **场内摘要**：timeline_summary / topics 由后台维护者写入。

## 诊断出库

请求大字段不进库，落 `logs/`：

- `logs/llm_requests/<日期>/<时>.jsonl`：每请求一条完整档案（参数、正文、原始用量）。
- `logs/llm_context/latest.json`：每模型上下文水位快照。
- `logs/events/`：事件诊断流，场次回看的事件侧事实源（ADR-039）。

库内 `llm_requests` 只保留统计列与正文存档；`infra.toml` 的 `[diagnostics].retention_days = 0`（默认）表示诊断文件永不清理，设为正数启用按天滚删。

## 配置与降级

- 配置只有两键：`storage.toml` 的 `[sqlite].db_path`（默认 `data/amaidesu.db`）与 `busy_timeout_ms`（默认 5000）；权威定义在 `src/modules/config/storage_schemas.py`。
- 降级语义：单条写入失败记日志不抛出；无进行中场次时明细不落库；数据库不可用时 LLM 历史与统计返回空、收播整理跳过——直播主链路的正确性不依赖存储成功。

## 相关决策

- [ADR-015](../decisions/015-live-chat-single-source.md)　live_chat 单一事实源
- [ADR-029](../decisions/029-viewer-identity-key-and-currency-unit.md)　观众身份复合键与货币口径
- [ADR-039](../decisions/039-event-replay-diagnostics-source.md)　回看以诊断事件流为事实源
- [ADR-042](../decisions/042-streamer-memory-recap-and-roster.md)　场次回顾 + 观众画像
