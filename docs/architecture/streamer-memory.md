# 主播记忆（场次回顾 + 观众画像 + 长期记忆）

> 本文档是主播跨场次记忆的权威设计说明。
> 主播必须自己记住、现场又推不出来的东西有三样：**每场做了什么**（场次回顾）、**常客是谁**（观众画像）、**跨很多场一直记着的事**（长期记忆 MEMORY.md：答应过的事、老梗、长线目标、对自己的认识）。都在收播时整理，开播与决策时按场次序号、观众身份或整份直接取用。
> 决策依据见 [场次回顾 + 观众画像](../decisions/042-streamer-memory-recap-and-roster.md)、[长期记忆](../decisions/051-streamer-long-term-memory-file.md) 与 [身份复合键与货币口径](../decisions/029-viewer-identity-key-and-currency-unit.md)。当场的工作记忆（时间线、前情提要）见 [主播上下文构成](streamer-context.md)。

## 设计公理

1. **按身份取，不按文本搜**：场次回顾按场次时间序取（"往前第 n 场"），观众印象按观众身份取，长期记忆整份注入。主播不必猜关键词。
2. **收播时整理**：记忆的写入集中在 `live.ended` 之后的后台任务，直播进行中不为记忆额外调用模型。
3. **有印象才注入**：没形成印象的观众不占提示词——"不注入"是门槛过滤的自然结果，不是独立排除逻辑。
4. **正式与彩排各一条线**：场次开启时声明类型（正式 / 彩排，见 [场次分正式与彩排](../decisions/050-session-kind-live-and-rehearsal.md)）。回顾每场都写，读取只取同类型场次；长期记忆正式与彩排各一份文件；画像只更新真假与场次类型相符的观众。调试、回放、模拟的场次不会被当成正式直播的上一场。
5. **身份判断是确定性代码**：常客按内部观众 ID 定位（同一位观众换了数据源也是同一份画像），昵称只在注入与查询时经 `viewers` 实时解析，LLM 只负责读与写正文。

## 数据流

```mermaid
flowchart LR
    subgraph 直播中
        L[StorageLedger] --> D[(明细表<br/>live_chat / gifts / super_chats / guards)]
        L --> V[(viewers 统计)]
        L --> G[(game_events)]
        B[后台摘要循环] --> T[(timeline_summary)]
    end
    subgraph 收播 live.ended
        R[SessionRecapWriter] --> SR[(session_recaps)]
        M[LongMemoryWriter] --> MF[(memory/MEMORY.md)]
        W[RegularsRosterWriter] --> VP[(viewer_profiles)]
    end
    SR -->|本场回顾 + 备忘录| M
    D & T & G --> R
    D -->|本场原话 + 付费记录| W
    V -->|互动量门槛| W
    MF -->|开播时整份读出| LM[Planner 参考段 长期记忆-内部参考<br/>+ 快嘴系统提示词]
    SR -->|开播时读最近几场| P[Planner 参考段<br/>上场回顾-内部参考]
    VP -->|本批发言人·有印象| Q[Planner 参考段<br/>人物画像-内部参考]
    SR & VP --> TOOLS[memory_recall_session<br/>memory_recall_viewer]
```

生成、注入与工具逻辑在 `src/agents/streamer/memory/`；两张表的仓储 `SessionRecapRepo` / `ViewerProfileRepo` 在 `src/modules/storage/repos/`，长期记忆文件存储 `LongMemoryStore` 在 `src/modules/storage/long_memory.py`。收播整理的顺序是回顾 → 长期记忆 → 画像。

## 场次回顾

| 环节 | 规则 |
|---|---|
| 写入时机 | `live.ended` 后后台写本场；空场次（无明细行、场次行已删）不写。启动时补写最近一场"已结账却无回顾"的场次，并顺带补跑该场的观众画像更新（进程退出时收播整理没跑完的善后） |
| 原料 | 按场次主键取：主播自己说过的话（`live_chat` 的 assistant 行）、话题摘要轨迹（`timeline_summary`，相邻重复去重）、游戏里程碑（`game_events`）、本场弹幕最多的观众与付费三表。各类各自封顶，整体超限从尾部截断 |
| 生成 | summary 档位，提示词 `session_recap_system`；以主播第一人称写这场做了什么、答应过谁什么、聊过的梗、谁来得多；超长按 `recap_max_length` 截断 |
| 注入 | `live.started` 时读出最近 `recap_inject_count` 场**同类型**场次（排除本场；类型取开播事件里的场次类型），渲染为"【日期 的直播，时长】正文"，作为参考段**最前面**的"上场回顾-内部参考"整场固定 |
| 工具 | `memory_recall_session(n_back)`：往前第 n 场同类型场次（1 = 上一场；进行中的本场不算；未开播按正式场数） |

## 长期记忆

| 环节 | 规则 |
|---|---|
| 文件 | 数据库同目录 `memory/MEMORY.md`（正式）、`memory/MEMORY.rehearsal.md`（彩排）；纯文本 UTF-8，人可直接用编辑器改 |
| 写入时机 | 收播整理里排在场次回顾之后；本场没写成回顾就不动；启动补写回顾的场次同样补一次。直播进行中不写，现场的结论记在本场备忘录 |
| 原料 | 旧笔记 + 本场回顾 + 本场备忘录全文快照 |
| 生成 | summary 档位，提示词 `long_memory_system`；按节组织：答应过的事（标兑现状态）/ 老梗与口头禅 / 长线目标 / 关于我自己 |
| 上限 | `long_memory_max_chars`；超出让模型压缩一次，仍超出保留旧版本不写，不截断 |
| 注入 | `live.started` 时按场次类型读出整份，整场不变：决策参考段最前的"长期记忆-内部参考"，以及快嘴系统提示词 |
| 管理 | WebUI 主播记忆页顶部的长期记忆面板（`/api/v1/memory/long-term/{kind}`）：正式 / 彩排切换、整份编辑；改动从下一次开播起生效 |

## 观众画像

| 环节 | 规则 |
|---|---|
| 候选 | 本场弹幕最多的观众（按条数倒序扫描），且 `viewers.interaction_count` ≥ `roster_min_interactions`，且真假与场次类型相符（正式场只看真实观众，彩排场只看模拟观众）；每场最多 `roster_batch_max` 人 |
| 原料 | 旧印象 + 该观众本场在上次整理（`last_compressed_at_ms` 记录已整理到的时间点）之后说过的原话 + 付费与身份记录（累计付费、最近三条明细、最近一次付费时的牌子 / 舰队快照，金额经 `to_cny`；直接查付费三表取得） |
| 生成 | summary 档位，提示词 `profile_system`；不超过 `profile_max_length` 字；写回并把已整理时刻推进到当下。上次整理之后没有新话（同一场重复收尾）不调用模型 |
| 失败 | 逐人降级：该观众印象不动、已整理时刻不推，下场再来 |
| 注入 | 本批发言人的外部 ID 先换算成观众、按观众去重（发言时间倒序），逐人查印象，无印象跳过不占位；行渲染 `- {昵称}: {印象}`，改过名的写成 `- {昵称}（以前叫{曾用名}）: {印象}`，场次回顾按当时的名字写，主播靠它对上号 |
| 工具 | `memory_recall_viewer(nickname | viewer_id)`：昵称先比当前昵称、再比曾用名；命中曾用名时告诉主播对方现在叫什么 |
| 管理 | WebUI 主播记忆页的画像表（`/api/v1/memory/profiles`）：列表 / 搜索 / 人工纠正 / 删除。纠正不改已整理时刻，纠正版作为旧印象参与下次更新 |

## 配置（agents.toml `[agents.streamer.memory]`）

| 键 | 默认 | 含义 |
|---|---|---|
| `recap_enabled` | `true` | 收播写回顾、开播注入上场回顾 |
| `recap_max_length` | `500` | 单场回顾长度上限（字符） |
| `recap_inject_count` | `1` | 开播注入最近几场（0 = 只留工具按需查） |
| `roster_enabled` | `true` | 收播更新观众印象（关闭后已有印象仍注入） |
| `roster_min_interactions` | `3` | 建档的累计互动量门槛 |
| `roster_batch_max` | `5` | 每场最多更新几位常客 |
| `profile_max_length` | `400` | 单人印象长度上限（字符） |
| `long_memory_enabled` | `true` | 收播合并长期记忆、开播注入（关闭后既不更新也不注入，文件保留） |
| `long_memory_max_chars` | `2000` | 长期记忆全文长度上限（字符） |

## 降级

- 记忆两仓储任一未注入：对应写入与注入关闭；两者都在时才注册记忆工具。长期记忆需要文件存储与场次回顾仓储都在。
- summary 档位不可用：回顾与印象都不写（有日志），直播主链路不受影响。
- 读回顾、印象或长期记忆异常：该段整段省略，不阻断决策。

## 边界（不做什么）

- 不做自由文本或语义检索；不做 embedding。
- 不在直播进行中更新印象与长期记忆：本场新形成的认识靠对话上下文与备忘录本身，下场才进注入段。
- 不跨平台聚合：不同平台的观众始终是不同的人；同一平台内的账号 ID 与开放平台 ID 由观众表关联到同一人（见 [观众身份映射](../decisions/047-viewer-identity-mapping.md)）。
