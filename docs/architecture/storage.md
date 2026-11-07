# 存储

一句话形状：**SQLite 单库存状态，建表 DDL 以 `src/modules/storage/schema.py` 为唯一事实源（任何 CREATE TABLE 必须落在此文件），访问层按域拆分仓储；所有写入走旁路语义——存储失败记日志，不阻断直播。**

## 表地图

全集以 schema.py 的建表清单为准，按域分组：

**场次与消息**

- `live_sessions`：一场直播一行，开播/下播边界与人数、热度实时状态，以及下播整理的进度：`settled_at_ms`（整理完成的时刻，空 = 待整理）、`settle_attempts`（自动重试次数，用满后等运营者手动重排）、`settle_report`（失败原因或未登记清单，JSON 文本）。
- `live_chat`：全量消息流，观众/主播/搭档同表（`sender_role` 区分）；`message_id` ↔ `reply_to_message_id` 构成"主播回复了哪条弹幕"的关联键。主播行另带 `utterance_id`（与 `tts.utterance.*` 共用的全链路关联键）、`dropped_at_ms`（播放队列确知这句没送到观众面前的时刻；为空即送到过，算"她说过"）与 `memory_refs`（这句点名引用了哪条记忆）。
- `gifts` / `super_chats` / `guards`：付费明细三表，按平台原始单位入库（口径见 [观众身份复合键与货币口径](../decisions/029-viewer-identity-key-and-currency-unit.md)）。
- `timeline_summary`：一场内的分段摘要轨迹（后台维护者写入）。

**观众与身份**

- `viewers`：观众，一行一人（按平台），同一行记账号 ID 与开放平台 ID，带跨场统计（其中 `sessions_count` / `last_session_id` / `prev_seen_ms` 是正式场来访计数与档位依据，由观众本场第一条真弹幕在同一事务里推进）；新出现的 ID 按当前昵称找同名老观众，默认只挂一条合并建议、由人工确认，配置了关联时限才在时限内自动关联（见 [观众身份映射](../decisions/047-viewer-identity-mapping.md) 与 [按昵称关联默认关](../decisions/064-viewer-name-match-off-by-default.md)）。明细四表经 `viewer_id` 挂到观众，并保留收到时的原始外部 ID。
- `viewer_names`：观众用过的昵称（含曾用名），只在新建、改名、合并时写入。
- `forget_list`：不记录名单，只存 `HMAC-SHA256(库外密钥, 种类|外部ID)` 摘要与登记时刻，不落明文外部 ID；密钥在库外文件（默认 `secrets/forget.key`）。文件丢失而名单非空时存储启动硬报错，避免被遗忘的观众静默恢复记录。

**主播记忆账本**

- `commitments`：承诺账本，一行是她当众答应过、还没了结的一件事。带 `quote`（她播出原句的逐字片段，登记时对着台词行校验过）与 `evidence_chat_id`（那句台词的行号，场次被删后成空引用）；`viewer_id` 记对谁说的、`game` 记挂在哪个游戏、`save_id`/`save_name` 记答应时角色在哪个存档（空串 = 不分存档或存档不明）；状态三分：`open` 未了结 / `closed` 已了结（做完或放弃，看了结原话）/ `void` 运营者判误记。判误记留墓碑而不删行，之后整理遇到同一件事才认得出、不重复登记。
- `archive_lines`：场次档案，每场只写本场的事，标题加她播出原句的逐字片段，游戏里的事另记 `game` 与存档两列，写完不改（运营者可删单行）。`archive_snapshots` / `archive_snapshot_lines`：每个整理过的正式场一份档案快照，每行引用一条 `archive_lines`（外键级联，原行删掉就从所有快照撤掉），另记位置、是否稳定的事与最近被提起的时刻；删除场次或改成彩排时连同它的快照一起删。两者是"她记得前几场干了什么"的唯一来源。文档与运维面见 [主播记忆](streamer-memory.md)。
- 复盘页的"删除单句"走 `ChatRepo.delete_speech_and_references`：同一个事务里删那句主播台词、删引用它的档案行、清空引用它的承诺的原话（承诺行保留）。
- 档案行与承诺另有 `keywords`（被动想起的触发词，JSON 字符串列表）；档案行另有 `place`（挂在哪个记住的地点）。
- `memory_vectors_*` / `memory_fts_*`：记忆检索的派生索引，档案行、承诺、她播出的台词、游戏事件各一张向量表、一张 FTS5 全文表，行号即来源行主键。正本始终是来源行，这些表随时能重算。向量表外键级联；全文表是虚表，靠触发器跟着来源行删除，来源正文被改（删观众替换昵称、台词标成没播出、清空承诺原话）时连同向量一起删掉等重算；全文表打开了 FTS5 的 `secure-delete`，删掉的词元不留在影子表里。收什么由 `MemoryIndexRepo` 一处定：台词只收正式场里她播出满 30 秒的正文（不带她回应的弹幕），游戏事件只收里程碑与阶段汇报（白名单，游戏聊天与玩家肢体动作不收），判了误记的承诺不收；场次改成彩排时来源行不变，在改类型的同一个事务里删掉那一场的台词与事件索引。
- `memory_cue_log`：每张推给她的线索卡一行（哪一场、什么卡、被哪几路命中、分数、用上的时刻），只记条目编号、不记观众身份；条目或场次被删时外键级联删除。
- 记忆页人工审核：`live_sessions.reviewed_at_ms`（标已审的时刻，空即待审）、`archive_lines.human_locked`（运营者改过或新增的行，之后的整理不能去掉或替换）、`archive_snapshot_lines.pinned`（运营者放回快照或新增的行，接进新快照时跟着带过去；一份快照至多 4 行，可以取消放回）。整理照旧自动生效，人只在事后改。`memory_edits` 记每次操作（场次、对象、动作、改前改后、时刻；批准、驳回候选时另记候选编号，不记观众身份），与档案同寿命：场次被删时级联删除；档案行或候选被删时记录留着、改前改后的文本由触发器清空；删观众与"忘了我"把提到 ta 昵称的记录文本整段清空；挂在 ta 自己弹幕上的候选随弹幕整条删，挂在别人弹幕上、只是原话或标题里提到 ta 的候选不删，只把昵称换成"一位观众"（驳回过的仍是驳回，同一条以后不再列）。按昵称匹配时 `%`、`_` 按原字比。
- `viewer_candidates`：待审候选，一条一行，类型分"观众定下的"与"她说欠着的"，挂在观众那条弹幕与她那句回应两条 `live_chat` 记录上（外键级联：删观众、"忘了我"、按句删、删场次一起删；改彩排时由场次仓储撤掉本场候选）；状态待审 / 已批 / 已驳回，批准后记下生成的档案行或承诺；观众定下的一类另存观众定下的那个词（`word`），批准时抄到生成的档案行上（`archive_lines.viewer_word`），渲染时原话里这一段不遮；读档案的仓储不碰候选表。待审的列出后 14 天没人审自动删掉。保留期清理不删批准进记忆的那条弹幕，否则档案行会被级联删掉。`archive_lines` 另有来源（主播 / 观众）与观众那条弹幕两列，批准来的行挂着那条弹幕。决策见 [观众定下的事经人批准](../decisions/074-viewer-defined-memory-needs-approval.md)。

**游戏里的人**（决策见 [游戏里的人](../decisions/075-game-players-by-uuid.md)）

- `game_players`：她在游戏里遇到过的玩家，一个游戏账号 UUID 一行：最近一次的名字、见过的名字、见过的存档、首次与最近一次见到的场次与时刻；`forget_requested_at_ms` 记他在公屏发"忘了我"的时刻。
- `game_player_interactions`：和她的互动（打招呼一场合并一行、送东西、切磋），挂玩家，外键级联；场次改成彩排时撤掉本场互动。
- `game_chat`：公屏游戏聊天，方向分玩家说的（挂玩家，外键级联）、她说的、认不出是谁说的（只占一行位置、不存原话）；和弹幕一样受保留期管，私聊不落库。
- `viewer_player_links`：运营者确认过的观众与玩家关联，只用来连带删除，不告诉她。
- `player_candidates`：玩家自述的待审候选：玩家在公屏说的关于他自己的事、她当场接住了，下播整理找承诺那次顺带列出，挂在他那句公屏和她的回应上（外键级联）；人在记忆页批准后只写在这位玩家的玩家卡上，不进常驻简报。待审的 14 天没人审就删；保留期不删批准过的自述挂着的那句公屏。
- `commitments` 另有 `target_player_id`（对哪位玩家答应的，外键级联）与 `evidence_game_chat_id`（证据是她在公屏打的那句）；`live_chat` 的主播行另有 `reply_player_id`（这句在回应哪位玩家：Planner 点名回应、认得出是谁的那位；没点名时，那一窗只有他一位在游戏里开过口、她又正在回他才记）。

**流程单（五表族，设计见 [流程单设计决策](../decisions/011-rundown-replaces-agenda.md)）**

- `rundowns` / `rundown_segments`：流程单模板与环节行。
- `rundown_instances` / `rundown_instance_segments`：每场实例快照（崩溃恢复的游标与计时锚点存这里）。
- `rundown_transitions`：只增不改的变更事实行（"实际停留 vs 预期停留"的分析面）。

**游戏与诊断**

- `game_events`：游戏里程碑、安全阀、异常；`save_id`/`save_name` 由事件发布方填（发生时角色在哪个存档），下播整理按它认档案与承诺是在哪个存档里说的。
- `topics`：当前话题快照投影（每场清旧插新）。
- `llm_usage` / `llm_requests`：LLM 用量统计与请求历史，同事务写入两表。

**观众模拟器**

- `sim_personas` / `sim_gifts`：模拟观众人设与礼物目录（开发基础设施，见 [模拟器是开发基础设施](../decisions/006-simulator-is-dev-infrastructure.md)）。

`live_sessions.kind` 标记场次类型：正式（面向真实观众）或彩排（模拟、回放、调试），开场时声明。只有正式场会被整理成承诺与场次档案，读取也按类型过滤；对外同步只认正式场（见 [场次分正式与彩排](../decisions/050-session-kind-live-and-rehearsal.md)）。`live_chat` / `gifts` / `super_chats` / `guards` 四表另带 `simulated` 贯穿列，标记单条消息是不是造出来的。

## 迁移机制

- 版本声明 `SCHEMA_VERSION` 在 schema.py；`schema_migrations` 表记录已应用版本。
- 迁移一版本一文件、编号严格 +1（`src/modules/storage/migrations/`），注册表完整性由守护测试断言。
- 启动时先执行完整 DDL（`IF NOT EXISTS` 幂等），再按当前版本到目标版本的区间依次跑迁移回调；回调原地修改、自带列存在性检查，重复执行无副作用。
- 升版本前自动在线备份到 `data/backups/`（`<库名>-pre-v<版本>-<时间戳>.db`）；备份失败仅告警不阻塞迁移。备份默认不自动清理，配置了 `[retention].raw_days` 后按保留期统一清理：整库备份与迁移时移入的旧记忆目录都按条目自身的修改时刻判定。
- 库版本高于代码版本（回滚到旧程序）只告警不拒启。
- **存在不可逆的清理版本**：记忆改按账本记录之后，删除旧表（`session_recaps`、`viewer_profiles`）与场次表的备忘录列，并把库文件同级的 `memory/` 目录（旧长期笔记与历史留底）整目录移入 `backups/` 下的 `memory-pre-v25-<时间戳>/`——移走后不再被任何代码读取，内容留在备份目录里，可手工取回。这一版之后旧版本二进制无法正确回退（它会把旧表按建库语句重建出来），退路是升级前自动留下的整库备份与这次移入备份目录的旧记忆目录；升级前请确认备份可用。设计见 [清掉旧记忆的存储面](../decisions/067-irreversible-memory-cleanup.md)。

## 访问层

- 仓储按域拆分在 `src/modules/storage/repos/`（sessions / chat / viewers / commitments / archive / memory_index / memory_cue_log / memory_review / viewer_candidates / game_players / game_creatures / topics / events / llm / rundowns / rundown_instances / rundown_transitions / sim 等），清单以 `repos/__init__.py` 导出为准；隐私删除与保留期在 `storage/privacy.py` 与 `storage/retention.py`。同步 SQLite 调用统一经 `asyncio.to_thread` 包装，事件循环零阻塞。
- 主要写入方：
  - **StorageLedger**：订阅 `room.message.#` 落明细表与观众计数、订阅 `streamer.speech` 写主播发言行（带回复关联与回应的玩家、落库前遮名）、订阅 `tts.utterance.dropped` 写没送达标记、订阅 `game.*` 写游戏事件、订阅 `game.place.noted` 写地点动静；观众四类消息在判场次之前先过隐私入口（遗忘命令、撤回短语、不记录名单）。不建场次行（归 LiveSessionManager）。
  - **GamePlayerLedger**：订阅 `game.chat.received`、`game.self.chatted`、`game.player.interacted`，按 UUID 认人写玩家表、互动表与公屏聊天表；只给带 UUID、核对过在线的发言人建档，玩家的公屏消息先过玩家那一套隐私入口（"忘了我"、撤回短语、不记录名单）。
  - **LLM 观察面**：`src/modules/llm/observation.py` 每次调用同事务写 usage 与 requests 两表。
  - **收播整理**：承诺账本与场次档案由主播 Agent 的下播整理写入（跨场次表的一次性写入口是 `SessionRepo.apply_settlement`，一个事务写档案行、档案快照、新承诺、旧承诺了结、本场待审候选与整理完成时刻；失败与整理报告写在 `live_sessions` 的整理三列）。旧的场次回顾、观众印象与长期笔记写入器已随阶段 3b 移除。
  - **场内摘要**：timeline_summary / topics 由后台维护者写入。

## 隐私与保留期

存储层自己做两件事，不依赖主播 Agent 是否在跑——观众能不能删掉自己的数据不该取决于直播间状态。

- **遗忘命令**：观众发"忘了我"后，记账入口在判场次之前就把 ta 的外部 ID 摘要写进 `forget_list` 并在 `viewers.forget_requested_at_ms` 记下时刻，此后 ta 的弹幕不再落库、不建观众行，付费只留金额与类型。删除推迟到发令之后第一次下播后的清理，且有最长等待上限（上限常量在 `storage/privacy.py`），执行前本人发撤回短语即取消。判定与撤回短语表都在 `storage.toml` 的 `[privacy]` 段。
- **删除函数**：`PrivacyService.delete_viewer` 是唯一入口，一个事务内删观众行与曾用名、ta 的弹幕与对 ta 的承诺、相同外部 ID 的模拟回放副本，清空付费三表的身份与留言列，并按每个昵称的使用期把相关场次里主播台词、档案（快照只引用原行，不另存文本）、承诺、话题与摘要中的昵称换成"一位观众"。同名昵称只在 ta 出现过的那几场替换，避免误伤日常用词。删观众时连带删运营者确认过关联的游戏玩家。
- **删玩家**：`PrivacyService.delete_player` 一个事务内删玩家行（互动、他的公屏聊天、对他的承诺随外键级联），并在他出现过的场次里把她的台词、档案、承诺、游戏事件与她在公屏打的字中他的名字（含以前的名字）换成"一位玩家"；默认把 UUID 摘要写进 `forget_list`（种类按游戏区分），之后他再出现也不记。玩家在公屏发"忘了我"和观众同一套口令与宽限期，到期删除时连带删确认过关联的观众。
- **保留期清理**：`[retention].raw_days` / `viewer_absence_days` 默认都是 0（不自动删除），由使用者按自己的接入方式与合规判断配置；清理在启动时与每次下播后跑，同时执行到期的遗忘删除。下播那次由主播 Agent 的下播收尾在道别句播完、下播整理结束后触发（顺序见[主播记忆 - 下播收尾](streamer-memory.md#合规删除与遗忘)）。
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
- [主播记忆改按账本记录](../decisions/065-memory-ledger-shadow-run.md)
- [记忆注入切到账本](../decisions/066-memory-injection-switch.md)
- [清掉旧记忆的存储面](../decisions/067-irreversible-memory-cleanup.md)
- [观众数据可自行删除与保留期](../decisions/068-memory-privacy-retention-and-forget.md)
- [观众身份映射](../decisions/047-viewer-identity-mapping.md)
- [混合检索与被动想起](../decisions/072-memory-hybrid-search-and-passive-recall.md)
- [整理结果的人工审核](../decisions/073-settlement-human-review.md)
- [观众定下的事经人批准进记忆](../decisions/074-viewer-defined-memory-needs-approval.md)
- [游戏里的人按 UUID 认](../decisions/075-game-players-by-uuid.md)
