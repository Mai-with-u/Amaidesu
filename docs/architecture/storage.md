# 存储

一句话形状：**SQLite 单库存状态，建表 DDL 以 `src/modules/storage/schema.py` 为唯一事实源（任何 CREATE TABLE 必须落在此文件），访问层按域拆分仓储；所有写入走旁路语义——存储失败记日志，不阻断直播。**

## 表地图

全集以 schema.py 的建表清单为准，按域分组：

**场次与消息**

- `live_sessions`：一场直播一行，开播/下播边界与人数、热度实时状态，本场备忘录全文（主播每次写备忘录同步落进来，收播整理从这里读），以及下播整理的进度：`settled_at_ms`（整理完成的时刻，空 = 待整理）、`settle_attempts`（自动重试次数，用满后等运营者手动重排）、`settle_report`（失败原因或未登记清单，JSON 文本）。
- `live_chat`：全量消息流，观众/主播/搭档同表（`sender_role` 区分）；`message_id` ↔ `reply_to_message_id` 构成"主播回复了哪条弹幕"的关联键。主播行另带 `utterance_id`（与 `tts.utterance.*` 共用的全链路关联键）、`dropped_at_ms`（播放队列确知这句没送到观众面前的时刻；为空即送到过，算"她说过"）与 `memory_refs`（这句点名引用了哪条记忆）。
- `gifts` / `super_chats` / `guards`：付费明细三表，按平台原始单位入库（口径见 [观众身份复合键与货币口径](../decisions/029-viewer-identity-key-and-currency-unit.md)）。
- `timeline_summary`：一场内的分段摘要轨迹（后台维护者写入）。

**观众与画像**

- `viewers`：观众，一行一人（按平台），同一行记账号 ID 与开放平台 ID，带跨场统计（其中 `sessions_count` / `last_session_id` / `prev_seen_ms` 是正式场来访计数与档位依据，由观众本场第一条真弹幕在同一事务里推进）；新出现的 ID 按当前昵称找同名老观众，默认只挂一条合并建议、由人工确认，配置了关联时限才在时限内自动关联（见 [观众身份映射](../decisions/047-viewer-identity-mapping.md) 与 [按昵称关联默认关](../decisions/064-viewer-name-match-off-by-default.md)）。明细四表经 `viewer_id` 挂到观众，并保留收到时的原始外部 ID。
- `viewer_names`：观众用过的昵称（含曾用名），只在新建、改名、合并时写入。
- `forget_list`：不记录名单，只存 `HMAC-SHA256(库外密钥, 种类|外部ID)` 摘要与登记时刻，不落明文外部 ID；密钥在库外文件（默认 `secrets/forget.key`）。文件丢失而名单非空时存储启动硬报错，避免被遗忘的观众静默恢复记录。
- `viewer_profiles`：观众印象文本，按观众 ID 存，收播时批量整理（见 [场次回顾与观众画像](../decisions/042-streamer-memory-recap-and-roster.md)）。
- `session_recaps`：场次回顾，收播写、开播注入。

**主播记忆账本**

- `commitments`：承诺账本，一行是她当众答应过、还没了结的一件事。带 `quote`（她播出原句的逐字片段，登记时对着台词行校验过）与 `evidence_chat_id`（那句台词的行号，场次被删后成空引用）；`viewer_id` 记对谁说的、`game` 记挂在哪个游戏；状态三分：`open` 未了结 / `closed` 已了结（做完或放弃，看了结原话）/ `void` 运营者判误记。判误记留墓碑而不删行，之后整理遇到同一件事才认得出、不重复登记。
- `archive_lines`：场次档案，一场至多 8 行，标题加她播出原句的逐字片段，写完不改（运营者可删单行）；它是"她记得自己上一场干了什么"的唯一来源。

**流程单（五表族，设计见 [流程单设计决策](../decisions/011-rundown-replaces-agenda.md)）**

- `rundowns` / `rundown_segments`：流程单模板与环节行。
- `rundown_instances` / `rundown_instance_segments`：每场实例快照（崩溃恢复的游标与计时锚点存这里）。
- `rundown_transitions`：只增不改的变更事实行（"实际停留 vs 预期停留"的分析面）。

**游戏与诊断**

- `game_events`：游戏里程碑、安全阀、异常。
- `topics`：当前话题快照投影（每场清旧插新）。
- `llm_usage` / `llm_requests`：LLM 用量统计与请求历史，同事务写入两表。

**观众模拟器**

- `sim_personas` / `sim_gifts`：模拟观众人设与礼物目录（开发基础设施，见 [模拟器是开发基础设施](../decisions/006-simulator-is-dev-infrastructure.md)）。

`live_sessions.kind` 标记场次类型：正式（面向真实观众）或彩排（模拟、回放、调试），开场时声明。主播记忆按类型分两条线，对外同步只认正式场（见 [场次分正式与彩排](../decisions/050-session-kind-live-and-rehearsal.md)）。`live_chat` / `gifts` / `super_chats` / `guards` 四表另带 `simulated` 贯穿列，标记单条消息是不是造出来的。

## 迁移机制

- 版本声明 `SCHEMA_VERSION` 在 schema.py；`schema_migrations` 表记录已应用版本。
- 迁移一版本一文件、编号严格 +1（`src/modules/storage/migrations/`），注册表完整性由守护测试断言。
- 启动时先执行完整 DDL（`IF NOT EXISTS` 幂等），再按当前版本到目标版本的区间依次跑迁移回调；回调原地修改、自带列存在性检查，重复执行无副作用。
- 升版本前自动在线备份到 `data/backups/`（`<库名>-pre-v<版本>-<时间戳>.db`）；备份失败仅告警不阻塞迁移，备份不自动清理。
- 库版本高于代码版本（回滚到旧程序）只告警不拒启。

## 访问层

- 仓储按域拆分在 `src/modules/storage/repos/`（sessions / chat / viewers / viewer_profiles / recaps / topics / events / llm / rundowns / rundown_instances / rundown_transitions / sim），清单以 `repos/__init__.py` 导出为准；同步 SQLite 调用统一经 `asyncio.to_thread` 包装，事件循环零阻塞。
- 主要写入方：
  - **StorageLedger**：订阅 `room.message.#` 落明细表与观众计数、订阅 `streamer.speech` 写主播发言行（带回复关联、落库前遮名）、订阅 `tts.utterance.dropped` 写没送达标记、订阅 `game.*` 写游戏事件；观众四类消息在判场次之前先过隐私入口（遗忘命令、撤回短语、不记录名单）。不建场次行（归 LiveSessionManager）。
  - **LLM 观察面**：`src/modules/llm/observation.py` 每次调用同事务写 usage 与 requests 两表。
  - **收播整理**：场次回顾与观众印象由主播 Agent 收播钩子写入；承诺账本与场次档案由同一次收播整理的后半段写入（跨场次表的一次性写入口是 `SessionRepo.apply_settlement`，一个事务写档案行、新承诺、旧承诺了结与整理完成时刻）。
  - **场内摘要**：timeline_summary / topics 由后台维护者写入。

## 隐私与保留期

存储层自己做两件事，不依赖主播 Agent 是否在跑——观众能不能删掉自己的数据不该取决于直播间状态。

- **遗忘命令**：观众发"忘了我"后，记账入口在判场次之前就把 ta 的外部 ID 摘要写进 `forget_list` 并在 `viewers.forget_requested_at_ms` 记下时刻，此后 ta 的弹幕不再落库、不建观众行，付费只留金额与类型。删除推迟到发令之后第一次下播后的清理（最迟 24 小时），执行前本人发撤回短语即取消。判定与撤回短语表都在 `storage.toml` 的 `[privacy]` 段。
- **删除函数**：`PrivacyService.delete_viewer` 是唯一入口，一个事务内删观众行与曾用名、ta 的弹幕与对 ta 的承诺、相同外部 ID 的模拟回放副本，清空付费三表的身份与留言列，并按每个昵称的使用期把相关场次里主播台词、档案、承诺、话题与摘要中的昵称换成"一位观众"。同名昵称只在 ta 出现过的那几场替换，避免误伤日常用词。
- **保留期清理**：`[retention].raw_days` / `viewer_absence_days` 默认都是 0（不自动删除），由使用者按自己的接入方式与合规判断配置；清理在启动时与每次下播后跑，同时执行到期的遗忘删除。
- **删得干净**：所有连接开 `PRAGMA secure_delete=ON`，删除与清理后 `PRAGMA wal_checkpoint(TRUNCATE)` 截断预写日志，避免已删内容留在库文件或 `-wal` 里。

诊断日志、LLM 请求正文与迁移前备份的保留期同样交给使用者，方案不替任何人改默认值。

## 诊断出库

请求大字段不进库，落 `logs/`：

- `logs/llm_requests/<日期>/<时>.jsonl`：每请求一条完整档案（参数、正文、原始用量）。
- `logs/llm_context/latest.json`：每模型上下文水位快照。
- `logs/events/`：事件诊断流，场次回看的事件侧事实源（见 [回看以诊断事件流为事实源](../decisions/039-event-replay-diagnostics-source.md)）。

库内 `llm_requests` 只保留统计列与正文存档；`infra.toml` 的 `[diagnostics].retention_days = 0`（默认）表示诊断文件永不清理，设为正数启用按天滚删。

## 配置与降级

- 配置只有两键：`storage.toml` 的 `[sqlite].db_path`（默认 `data/amaidesu.db`）与 `busy_timeout_ms`（默认 5000）；权威定义在 `src/modules/config/storage_schemas.py`。
- 降级语义：单条写入失败记日志不抛出；无进行中场次时明细不落库；数据库不可用时 LLM 历史与统计返回空、收播整理跳过——直播主链路的正确性不依赖存储成功。

## 相关决策

- [live_chat 单一事实源](../decisions/015-live-chat-single-source.md)
- [观众身份复合键与货币口径](../decisions/029-viewer-identity-key-and-currency-unit.md)
- [回看以诊断事件流为事实源](../decisions/039-event-replay-diagnostics-source.md)
- [场次回顾 + 观众画像](../decisions/042-streamer-memory-recap-and-roster.md)
- [观众身份映射](../decisions/047-viewer-identity-mapping.md)
