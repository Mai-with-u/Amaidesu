# 组件开发指南（三范式）

本指南介绍如何在 Amaidesu 项目中扩展三类核心组件：**采集器（Collector）**、**工具（Tool）**、**Agent**。

> **范式说明**：项目的组件只有三类——**采集器 + Agent + 工具**：
> - **采集器** = 流型感知者（外部 → 系统入口，主动推 `room.message.*` 事件）
> - **Agent** = 决策主体（订阅事件 → 内部编排 Planner/Replyer → 调工具 → 出回复）
> - **工具** = 单一能力契约（被 Agent 调，可被任意 Agent 复用）
>
> 组件不使用"Input / Decision / Output 三阶段 + Decider / OutputHandler 装饰器"式的写法，不存在 `InputCollector / Decider / OutputHandler / Intent / @pipeline` 这些类。

## 三范式分篇

- [添加采集器](collector.md)
- [添加工具](tool.md)
- [添加 Agent](agent.md)

---

## 判据：组件该做成哪一类

新组件落地前，先按以下三个问题按序作答。三问归一答案即该组件的顶层类型（**采集器 / 工具 / Agent**）。

```
Q1  它持有一个「办事过程」吗？
    （一次调用返回后，它是否还在为某个目标干活，或被唤起继续干）
    │
    ├─ 否 ──▶ 工具
    │        每次调用独立完成。可以有内部状态、可以有维护性后台循环
    │        （MCP 重连、健康探活）——但**不持有跨调用的办事过程**。
    │
    └─ 是 ──▶ Q2
              过程怎么推进由谁决定？
              ├─ 「它自己」（命令方只给目标不给步骤）──▶ Agent
              │     ├─ 没人下命令也持续跑 ──▶ 主播 Agent（本项目唯一）
              │     └─ 收到命令才启动一段、完成即停 ──▶ 游戏 Agent
              └─ 「外部逐步规定」──▶ 这不是过程，是多次工具调用 ──▶ 回工具

Q3（数据源型并行判据）
    持续采数据并推送、无目标无起止、生命周期挂装配期 ──▶ 采集器
```

**关键**：Q2 的「自主决定」**不要求是 LLM**，代码决定也算——判定只看过程推进权，不用「有无 LLM 循环」当尺子。

**本仓组件验证表**（用真实组件回放三问，防止判据被抽象化误读）：

| 组件 | 三问作答 | 归类 |
|------|----------|------|
| streamer（`src/agents/streamer/`） | Q1 持过程 ✓（直播决策循环）；Q2 自己推进，没人叫也在跑 | 主播 Agent |
| minecraft（`src/agents/minecraft/`） | Q1 持过程 ✓；Q2 自己推进，收到命令才启动一段、完成即停 | 游戏 Agent |
| text_adv（`src/agents/text_adv/`） | Q1 持过程 ✓（auto 观察循环）；Q2 自己推进，循环由 `set_auto` 命令启停、完成即停 | 游戏 Agent |
| vision_look_at_screen（`src/modules/vision/look_at_screen.py`） | Q1 否——每次调用独立完成抓屏读屏 | 工具 |
| McpToolProvider（`src/modules/mcp/provider.py`） | Q1 否——重连属维护性后台循环，不持跨调用的办事过程 | 工具 |
| bili_danmaku（`src/modules/collectors/bilibili/`） | Q3 命中——持续采弹幕并推送，无目标无起止，生命周期挂装配期 | 采集器 |
| ProactiveTrigger（`src/agents/streamer/proactive_trigger.py`） | Q1 否——被调才干活，且是代码直连部件（无 `ToolSpec`、不进表） | 代码直连内部件 |

**与「红线三分」的区别**：本节判**组件顶层类型**（采集器 / 工具 / Agent）；Agent 内部的部件如何处置（注册为工具 / 留在 Agent 内 / 代码直连）是**另一轴**，见[红线三分](tool.md#红线三分)。

---

## 端到端消息流

下面以**控制台输入**为例，串起**采集器 → 事件拦截器 → Agent → 工具**的完整数据流。

```
1. 用户在 stdin 输入 "你好" + 回车
        ↓
2. ConsoleInputCollector._run_input_loop() 读到行
        ↓ 构造 RoomMessagePayload(message_type="danmaku") 并 emit
3. emit room.message.danmaku(payload=RoomMessagePayload{user, content, ...})
        ↓
4. EventBus 分发 → [拦截器链] RateLimitInterceptor / SimilarFilterInterceptor
        ↓ （返回 None = 丢弃；返回 dict = 放行）
5. StreamerAgent._on_room_message_received(payload) → handle_message(msg)
        ↓ 进入 MessageBuffer；TimingGate 判定是否强制响应
6. StreamerAgent._flush_loop 周期检查 → MessageBuffer.should_flush()
        ↓ 取出一批弹幕
7. DecisionRoundExecutor.execute(batch, forced=…, trigger_reason=…, proactive=…) → Planner.plan(batch, forced=…, proactive=…, history=…, rundown_text=…, …)
         ↓ Planner ReAct 循环：chat_messages + 工具列表（ToolRegistry 全量 + reply 局部工具）
         ↓ 每步 tool_calls 串行执行（查游戏状态/记忆等 → 观察 tool role 作为观察返回），max_steps=8 防失控
8. LLM 调 reply(意图参数 {topic_summary, reply_guidance, target})
         ↓ Planner 循环内直连 _reply_provider.invoke（局部工具，不进 ToolRegistry）
9. ReplyToolProvider.invoke(invocation)
         ↓ 注入依赖：history / rundown / game_chat / state_facts / recent_speeches（人设经 system 模板构造期渲染）
10. Replyer.generate(plan, batch, history=…, rundown=…, game_chat=…, state_facts=…, recent_speeches=…)
         ↓ LLM（llm profile，高质量模型）+ 人设 prompt + 敏感词净化；LLM 只见 reply
11. ToolExecutionResult{success=True, structured_content={speech, emotion, metadata}}
         ↓
12. Planner 循环收到 reply 成功即收尾 → outcome{replied/speech/...} → Agent 解析 speech 生成 utterance_id 入 UtteranceQueue（fire-and-forget，不阻塞决策循环）；
    emotion → 随 streamer.speech 业务事件发布；面捕合成器订阅后按播放时刻上脸
   （自动情绪路径，主播域零平台知识——见 [ADR-023：虚拟形象平台无关边界](../../decisions/023-avatar-platform-agnostic-boundary.md)）
         ↓
13. 队列 worker 串行 await speak(text, utterance_id)（speak 是构造期注入的适配器，绑定装配期由 build_tts_infrastructure 选中的 tts_engine.handle_speech）→ 引擎合成 + 播放
    （TTS 引擎自身——基础模块，非工具——发布 tts.utterance.started/finished/failed 事件供字幕等消费者订阅；ToolRegistry 中零 TTS 条目）
         ↓
14. 音频经 AudioDeviceManager（src/modules/audio/）输出到扬声器；播放器分接（AudioSink
    协议）把音频复制递入口型分析器（共享件，[avatar.lipsync]）——见 [ADR-024：口型分析帧通道](../../decisions/024-lipsync-frame-channel.md)
```

**关键要点**：

| 环节 | 实现位置 | 备注 |
|------|----------|------|
| 采集器 emit `room.message.danmaku` | `src/modules/collectors/console/console_input_collector.py::_emit_semantic_event` | 数据源换 = 替换采集器（`console_input` / `bili_danmaku_official` 等） |
| 拦截器配置 | `config/infra.toml` 的 `[interceptors.rate_limit]` / `[interceptors.similar_filter]` | 启停由 `enabled` 标志控制 |
| Agent 订阅 | `src/agents/streamer/streamer_agent.py::_subscribe_events` | 在 `_on_start` 中挂；priority=50 |
| 弹幕聚合 | `src/agents/streamer/message_buffer.py` + `timing_gate.py` | 批窗口 / 强制响应规则 |
| Planner ReAct 决策 | `src/agents/streamer/planner.py` | `planner_llm`（默认 llm 高质量模型）；工具列表=registry 全量+reply；`planner_max_steps=8` |
| Reply 工具 | `src/agents/streamer/tools/reply_tool.py` | 局部工具（不进 ToolRegistry）；Planner 循环内直连 invoke |
| Replyer 表达 | `src/agents/streamer/replyer.py` | 调 `replyer` profile + WordFilter；复古提示词生成纯文本，不发送工具；情绪由 Planner 提供 |
| 发声队列 | `src/agents/streamer/utterance_queue.py` | FIFO 串行；满时丢最旧；单条 render_timeout_ms 看门狗；构造期注入 `speak` 适配器（绑定 `tts_engine.handle_speech`） |
| TTS 播出 | `src/modules/tts/` 基础模块（4 引擎 Provider）+ `build_tts_infrastructure` 装配入口 | 引擎（`handle_speech`）发 `tts.utterance.*` 事件；详见 [ADR-007：TTS 基础设施管线](../../decisions/007-tts-infrastructure-pipeline.md) |

### 设计边界说明

- **TTS 是基础模块而非工具**：`infra.toml [tts].enabled = true` 后，`build_tts_infrastructure` 装配期按 `[tts].provider` 单选构造引擎实例，StreamerAgent 构造期接收并把 `engine.handle_speech` 注入 UtteranceQueue；reply 产出的 speech 经 UtteranceQueue → 引擎 `handle_speech` 播出（不走 ToolRegistry，零 TTS 工具条目）。设计决策见 [ADR-007：TTS 基础设施管线](../../decisions/007-tts-infrastructure-pipeline.md)。
- **工具注册路径唯一**：`AgentManager` 不聚合工具注册——真实注册只走两条：① Agent 子类 `_register_tools()` 中自己 `registry.register_provider(provider, visible_to=...)`；② 分类工具在 `main.py` 由 `bind_core_tools(registry, tools_cfg)` 按域开关装配。装配结束后 `AgentManager.audit_tools(registry)` 按派生全名对账（缺失即 warning），不写任何工具实现。

---

## 通用规范与引用

无论你开发哪种组件，以下规范统一适用（与具体范式无关）：

- **时间字段**：统一用 `int` Unix 毫秒（13 位整数）。**禁止**用秒。命名 `<name>_ms`（如 `timestamp_ms`、`render_timeout_ms`）。使用 `from src.modules.time_utils import now_ms, elapsed_ms, format_duration_ms, ms_to_datetime`
- **事件常量**：禁止硬编码事件名字符串，使用 `from src.modules.events.names import CoreEvents` 常量
- **日志**：使用 `from src.modules.logging import get_logger`；`get_logger("ClassName")` 或 `get_logger(self.__class__.__name__)`
- **配置 Schema**：每个组件**必须**定义 `class ConfigSchema(BaseConfig)`，字段用 Pydantic `Field(default=..., description="...")` 标注
- **错误处理**：`ToolRegistry.invoke()` 和 `ToolProvider.invoke()` **永不抛异常**——失败转为 `ToolExecutionResult(success=False, error_message=...)`；Agent / Collector 在边界处 `try/except` 后 `logger.error(..., exc_info=True)`
- **类型**：Pydantic `BaseModel` 用于数据模型 / 配置 Schema / 事件 Payload；`dataclass(slots=True)` 用于简单内部包装类；`Protocol` 用于接口契约
- **测试**：使用 `pytest`；异步用 `@pytest.mark.asyncio`；构造器注入便于 mock；测试用 `FakeBackend` / `FakeProvider` / `MockEventBus`

### 配置 Schema 约定（包内权威）

组件配置的**单一权威**是组件包内的 `ConfigSchema`（Collector/工具 Provider 为类内嵌定义，Agent 为包内模块级 `*Config`）；中央配置树（`src/modules/config/*_schemas.py`）只留槽位与聚合段，**不内联**组件字段定义——同一字段两处定义必然漂移。

- **定义**：`class ConfigSchema(BaseConfig)`，字段一律 `Field(default=..., description="...")`；具体值/空串哨兵表达（禁 `None`）；时间字段毫秒
- **登记**：新组件在 `src/modules/config/registry.py` 的显式 import 链登记（`EXPECTED_COMPONENTS` 清单内）；漏登记会在启动断言暴露（缺失清单随异常给出）
- **校验**：加载管线按注册表把采集器子段分发给包内 Schema 校验，漂移路径以 `<组件名>.<字段>` 前缀并入宿主文件报告；校验后的干净子段全量写回
- **消费**：组件运行时 `self.ConfigSchema.from_dict(raw)` 得到 typed 配置；缺键补默认、多键剥离并计入漂移
- **WebUI**：dashboard 按 `BaseConfig` 自描述协议（根 Schema 的 `__file_name__` / `__section_label__`）动态分组渲染；写路径统一走加载管线（`update_config_values`），勿自行读改写 TOML

### 相关文档

- [开发规范](../../../AGENTS.md#代码约定) — 代码风格与命名约定
- [测试指南](../testing.md) — 测试规范与技巧
- [架构总览](../../architecture/v2-architecture.md) — 顶层架构
- [事件系统](../../architecture/event-system.md) — EventBus 与事件拦截器
- [事件命名](../../conventions/event-naming.md) — 事件名动词链
- [数据流规则](../../conventions/data-flow.md) — 单向数据流约束
