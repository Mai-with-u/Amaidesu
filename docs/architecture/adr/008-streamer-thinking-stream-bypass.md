# ADR-008：主播思考流旁路通道（观察面流式 / 播出面整段）

- 状态：已采纳（2026-09-08 定案 / 同日实现落库）
- 日期：2026-09-08
- 实现提交：`8ab78692c07fb51ce2817ed144cc912e62b8854e`（feat(streamer): 新增主播思考流旁路通道）

## 背景（Context）

直播控制台时间线是**事件粒度实时、文本粒度空白**：从弹幕进入到 `planner.decision` / `streamer.speech` 落地之间的数秒里，观察者只有一行"决策中/生成中"状态，无法看到 Agent 在想什么、为什么最终选择沉默。

为发言（speech）做流式存在三重约束，经排查均不可绕：

1. **输出协议**：Replyer 的 LLM 调用采用 function calling 形态拿结构化输出（`reply(speech, emotion)` 是纯结构化输出口，非真实工具），speech 埋在 tool call 的 arguments JSON 里；流式下到达的是 JSON 碎片，实时提取需要部分 JSON 解析。
2. **净化约束**：敏感词净化在整段 speech 上做（含 `drop_on_match` 整条丢弃），流式上屏后无法撤回——直播公开画面"说出的话收不回"。
3. **下游整段语义**：TTS 入队、字幕、落库、`streamer.speech` 业务事件全部按整段设计。

与之相对，**思考内容（`reasoning_content`）天然适合流式**：它是流 delta 的顶层字段（逐帧独立文本，无 JSON 嵌套），消费端只有控制台观察面板，无净化与撤回约束。

设计讨论中曾提议思考流 delta 走 EventBus 事件，评审否决：delta 是过程噪声而非业务事实，混入事件总线既污染语义，也迫使每个存储订阅方（storage_ledger、事件历史）各自记得跳过——脆弱约定。

## 决策（Decision）

**核心原则：观察面流式，播出面整段。** 流式的限制只来自播出面（净化/撤回/TTS）；观察面（控制台）是管理面板，不在"播出"语义内，可以流式。读写路径分离，互不渗透。

### 1. 思考流走旁路通道，不走 EventBus

三分结构，依赖方向干净：

```
Planner / Replyer（Agent 侧）
    │ 同步直调（构造器注入的 Protocol）
    ▼
ThinkingStreamSink.on_thinking_delta(...)        ← agents/streamer 定义 Protocol
    ▼
StreamPreviewHub（dashboard 侧实现，结构化鸭子匹配）  ← 内存环形缓冲 + 合帧节流
    ▼
WebSocket 直推（独立消息信封，非事件广播）
```

- **零 EventBus 事件**：不经过事件总线，storage_ledger / 事件历史 / 场次盖章拦截器均不可达；EventBus 继续只承载整段业务事实
- **零持久化**：hub 仅持内存环形缓冲，进程重启即失；思考历史不落库不回看（决策卡 `planner_raw` 截断版仍在，回看语义不变）
- **依赖倒置**：`ThinkingStreamSink` Protocol 定义在 `src/agents/streamer/thinking_stream.py`（消费侧），dashboard 的 hub 实现同形方法——Python 结构化类型，dashboard 无需 import agents；装配根（main.py）构造 hub 实例注入 StreamerAgent 构造签名（新增 `thinking_sink: Optional[ThinkingStreamSink] = None`）
- **回调同步化**：`on_thinking_delta` 是同步方法，实现方内部仅做缓冲 append，禁止慢操作——不引入回调内并发问题

### 2. Sink 接口契约

```python
class ThinkingStreamSink(Protocol):
    """思考流旁路出口（best-effort 观测通道，非可靠传输）。

    契约：
    - 同步调用，实现方内部仅缓冲（禁止回调内慢操作）
    - best-effort：不保证送达、断线丢尾部、不持久化、不回填
    - seq 在 (round_id, phase, step) 内单调递增
    """

    def on_thinking_delta(
        self, *, round_id: str, phase: str, step: int, seq: int, text_delta: str
    ) -> None: ...
```

`phase` 取值 `planner` / `replyer`；`step` 为 Planner ReAct 循环步号（Replyer 恒为 1）。`round_id` 复用决策轮现有关联键——现状已在轮开始处生成（`StreamerAgent` 决策入口 `_next_round_id()`，先于 `_decide_round`），无需改动。

Agent 侧组装：sink 注入 Agent 构造器一次，LLM 调用时以回调参数传递（`on_delta` 闭包捕获 round_id/phase/step/sink），不在中间层穿透。

### 3. LLM 层增量回调（双入口统一）

- `llm/manager` 底层执行处统一增加 `on_delta` 回调出口，`chat_messages`（Planner）与 `call_tools`（Replyer）两个入口全部透传——漏一个即出现"半边思考流"
- 流式消费分离三类增量：`reasoning_delta`（外发 sink）/ `content_delta`（现状行为不变）/ tool call arguments 碎片（**Agent 侧直接丢弃**，不转发）
- **中断能力剥离出本期**（YAGNI）："手动停止生成"涉及决策循环半截流取消语义，另立项

### 4. 输出协议保持 function calling 不变

speech 的 JSON 碎片在 Agent 侧丢弃，**不做 speech 实时预览**——控制台 speech 区域在流结束前为空（维持"生成中"占位），流完整段卡片出现。用户可见的新增内容只有思考滚动区，永远看不到 JSON 源码。

### 5. WS 流消息独立信封

现有事件广播信封 `{type, timestamp, data, id?}` 增加来源标记：

```json
{
  "kind": "stream",
  "type": "thinking.delta",
  "timestamp": 1757300000000,
  "data": { "round_id": "round_...", "phase": "planner", "step": 1, "seq": 42, "text_delta": "..." }
}
```

- 现有事件消息补 `kind: "event"`，**缺省视为 event**（向后兼容）
- 前端 events store **只收 `kind === "event"`**——`thinking.delta` 若混入事件缓冲（去重 id、400 条上限、回看混合）即是"delta 进事件"的前端换皮，此为本设计明确禁止项
- 前端按 `round_id` 聚合思考滚动区；`planner.decision` 事件（含 error 形态）到达即终态，思考区收起定格为决策卡摘要；WS 重连时清空全部悬空思考区（思考流无游标回填，best-effort 契约的另一半）

### 6. Hub 节流与缓冲

- 合帧节流：`flush_interval_ms`（默认 100ms）窗口内 delta 合帧后一次 WS 推送，防帧风暴
- 环形缓冲：`buffer_max`（默认 400 条），超限丢最旧；仅服务断线前的最后窗口，不做可靠重放

### 7. 配置

`StreamerAgentConfig`（`agents.toml [agents.streamer]`）平铺新增三字段，沿用现有 schema 风格：

| 字段 | 默认 | 说明 |
|------|------|------|
| `thinking_stream_enabled` | `true` | 思考流总开关；关闭即完全现状 |
| `thinking_stream_flush_interval_ms` | `100` | hub 合帧窗口 |
| `thinking_stream_buffer_max` | `400` | hub 环形缓冲上限 |

纯新增字段，写回机制自动补默认值，无迁移 hook；`CONFIG_VERSION` 升 `2.0.23` → `2.0.24`。

### 8. 退化矩阵

| 情形 | 行为 |
|------|------|
| 模型无 reasoning 输出 | 无 delta 产生，前端维持现状"生成中"状态行，无感 |
| LLM 流式中途断/失败 | 已发 delta 不撤回，思考区截断标记；决策走现有失败路径（decision 事件带 error 终态） |
| 开关关闭 / hub 未注入 | Agent 侧短路，零旁路调用，完全现状 |
| WS 断线重连 | 前端清空悬空思考区，事件通道照常游标回填 |

## 替代方案（Alternatives）

### 思考流 delta 走 EventBus 事件

**拒绝（本 ADR 的起源讨论）**。delta 是过程噪声，EventBus 承载业务事实——混入即语义污染；落库虽被白名单式订阅挡住（storage_ledger 只订阅选定事件），但"每个存储订阅方记得跳过 delta"是脆弱约定，新增订阅方即踩坑。旁路通道从通道层面根除该类问题。

### 标签文本输出协议（`<speech>...</speech>`）替换 function calling

**本期拒绝，speech 流式预览立项时重估**。标签协议能让 speech 成为顶层文本流（自然流式），但放弃 provider 侧格式保证（枚举/必填校验），格式遵从靠 prompt + 解析容错兜底。当前 speech 无实时预览需求，不值得为它换掉结构化输出的可靠性。

### 部分 JSON 提取（流式 tool call arguments 抠字段）

**P2 候选，本期不做**。技术上可行（speech 是 arguments 首字段），但引入部分 JSON 解析器（跨帧断字、转义、不完整序列边界）是本期唯一新增复杂度组件，且净化约束决定了预览只能是"未净化预览"标注形态。待思考流上线验证观察价值后再议。

### REST 轮询拉取思考流

**拒绝**。延迟、垃圾请求、无推送语义；WS 通道现成。

### 复用现有事件广播通道推送 delta（不加 kind 字段）

**拒绝**。前端 events store 按 type 全收事件消息，delta 混入即污染事件缓冲与回看——"delta 进事件"的前端换皮形态。独立信封 + store 过滤是硬边界。

## 后果（Consequences）

**收益**：

- 决策等待期从"一行状态字"变为"能看着它想"——沉默轮可以看到"为什么沉默"，观察价值直接命中控制台"实时观察 · 决策回看"的页面定位
- 播出管线零改动：净化 / TTS / 字幕 / `streamer.speech` 事件 / 落库 / 回看时间线全部保持现状语义
- 零事件垃圾、零存储成本、数据库无感
- 无 reasoning 模型与关闭开关场景均无感退化，升级风险趋零

**代价**：

- LLM 层改造：双入口增量回调 + reasoning/content/碎片三分，manager 与 openai_client 均需动
- dashboard 新增 hub 组件与 WS 信封 `kind` 字段（协议扩展，前端类型同步）
- 思考流是 best-effort 通道：断线丢尾部、不回填、不持久化——与事件通道（可靠 + 游标回填）的心智模型不同，接口 docstring 与前端实现都要显式声明

**遗留（如实记录，不掩盖）**：

- **speech 实时预览留白**（P2 候选）：需部分 JSON 提取器或标签协议重估，另立项
- **中断能力留白**："手动停止生成"涉及决策循环取消语义，另立项
- **分句 TTS / 字幕流式留白**（P3）：涉及净化边界与首响语义，缓行
- **思考历史不落库**：跨重启后思考过程不可回看；决策卡 `planner_raw` 截断版仍在，回看语义维持
