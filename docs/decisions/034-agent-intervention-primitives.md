# ADR-034：Agent 干预原语三件套（递话 / 硬取消 / 运营直派）

- 状态：已采纳（2026-09-26 定案并实现）
- 日期：2026-09-26
- 实现提交：`a6872cbd50dcc42cc67522a0c3bbede8240f4e87`（递话契约与 minecraft 迁移）；`836fc60d0ef84b1a6909c1acd6bf575f4373a105`（递话两个调用面）；`275f8d4790d58f302acd16dcc65b2aea4bb226b4`（硬取消与 REST）；`e6243a24954e79e88b698942a62429c05ca23ba2`（任务卡快照与运营直派）；`44fe35c910cba84a4f818e18085726ce6ea944e8`（主播侧提醒队列消化）；`d1b25f0f64f022501b008d24fbf577eeb31c4466`（任务区界面）

## 背景（Context）

游戏 Agent 的委派任务挂起或异常后，运营者在后台既看不见全景也没有直接干预手段：任务卡缺失、无文本插话通道（ADR-013 收编 `minecraft_send_prompt` 进 `framework_delegate` 时删除了纯文本通道，当时无第二个消费者，属 YAGNI 留白）、发起方无取消原语（ADR-013 留白"跨 Agent 取消按需再加"）。现在两个真实消费者出现了：运营者中途纠偏（后台输入框）、主播对游戏 Agent 插话——正是兑现留白的时机。

## 决策（Decision）

**传输同路、记账分家**：一条 Agent 侧文本接缝，多种调用面，两种框架语义，各 Agent 自定消化方式。

1. **递话原语 `receive_prompt(*, content, source) -> bool`**（BaseAgent 默认拒收，与 `receive_delegation` 并列成对）：纯文本留言——不派新任务、不进任务账本；source 仅用于日志。委派 = 文本 + 任务号 + 受理回执 + 账本终态；递话 = 纯文本、零记账。理由：插话若走委派会铸造幻影任务（任务卡被非任务条目污染、还需回答"纠正类消息何时算终态"）；`receive_delegation` 的 prompt 框架语义是"这是你的任务"，插话的正确框架是"操作员/主播留言：调整你的理解"；主播是自驱 Agent、无账本可挂，运营提醒对主播只能是纯文本——纯文本通道必然是与委派并列的原语，不是委派的子集。
2. **递话两个调用面**：LLM 工具 `framework_prompt`（与 `framework_delegate` 并排进 framework provider，目标解析与禁自派复用委派同款逻辑，受理不记账）；运营 REST `POST /api/v1/agents/{name}/prompt`（source 固定 "operator"，200/404/409 三态）。否决复用调试 invoke 端点：技术可行 ≠ 语义正确（ADR-010 同判定——source 恒为 "dashboard-debug"、错误码无语义）。
3. **硬取消原语 `cancel_task(task_id, source) -> bool`**（默认拒收，三件套之三）：取消经执行 Agent 写账（单写者规则不动，ADR-013），框架不代写。minecraft 覆写：清委派追踪清单 + 账面写 cancelled 终态 + 注入停手通知；软取消不打断当前工具调用，终态粘滞保证 LLM 迟到的收尾写不进账。REST `POST /api/v1/agents/{name}/tasks/{task_id}/cancel`（404 = 不在名册或任务未知/已终态）。`waiting_for_decision` 僵尸账以取消为手动清场手段。
4. **运营直派 REST `POST /api/v1/agents/{name}/delegate`**：与 LLM 工具委派同一张任务账本（initiator="operator"、source="agent"），任务卡可见；拒收不登记（不留孤儿 accepted 条目）。
5. **主播侧消化**：递话入提醒队列（上限 5，满拒收 409——必达语义不允许静默挤掉旧条目），不冒充弹幕、不进对话历史；下个决策窗以【运营提醒】段注入 Planner 参考块，取空即送达一次制。催醒新增 reminder 触发源：仅受防接龙最小间隔一条约束——总开关/每小时上限/话题要求是"自主找话说"的规矩，不辖运营递话；阻塞时队列保留到下一 tick。直播控制台"幕后提醒"模式的传输从 trigger-proactive 切换到本通道（trigger-proactive 保留 API 作纯催话/限流测试用途，界面不再默认暴露）。
6. **任务卡**：`GET /api/v1/tasks` + WS 补订阅 `task.changed`。进行中读 TaskLedger 账本快照；已完结从事件环形缓冲按 task_id 聚合终态末次载荷——`task.changed` 不落库，历史仅本次运行内成立（跨重启历史等回看事实持久化线，本线不抢地盘）。界面挂 Agent 页：任务卡列表 + 干预输入条（递话默认/委派两模式），卡上取消按钮。

## 替代方案（Alternatives）

- **插话全部走委派**：被否——幻影任务、prompt 框架错位、主播侧无账本（结构性证伪，见决策 1）。
- **框架直写账本取消（B）**：被否——违反 ADR-013 单写者、账实两张皮；终态取决于谁写。
- **递话代替取消（C）**：被否——终态取决于 LLM 自觉（可能错账 succeeded）、唤醒整批重推理烧钱、"取消=强制清账"语义不容协商。
- **运营提醒复用 trigger-proactive**：被否——topic_hint 只进日志不进 prompt、四道限流任一不满足即静默丢弃，与"必达"语义相反。
- **复用调试 invoke 端点 / 任务卡只读账本不做事件聚合**：前者语义错误（见决策 2）；后者运营者看不到已完结任务，取消后任务卡凭空消失不可追溯。

## 后果（Consequences）

- BaseAgent 契约从"委派 + 任务通知"扩展为三件套 + 通知；新 Agent 默认拒收递话与取消，接入零成本，按需覆写。
- 运营者获得与 code agent 一致的干预体验：看得见（任务卡实时）、管得住（取消/递话/直派）、兜得住（提醒必达主播、主播可对游戏插话）。
- 任务历史仍是运行内观察窗：重启后已完结清零（用户接受的降级）；进行中账本重启即失（ADR-013 已接受的取舍，进程重启恢复方案挂起为未来项）。
- 主播 LLM 可见的 framework 工具从两件变三件；`framework_cancel` 薄封装留白（入口已通用）。
