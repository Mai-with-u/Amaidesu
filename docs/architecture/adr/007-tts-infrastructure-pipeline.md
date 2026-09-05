# ADR-007：TTS 作为配置驱动基础设施（reply → utterance 事件 → 引擎）

- 状态：已采纳（2026-09-04 定案 / 2026-09-05 实现落库；同日 §8 概念修正：TTS 退役出工具池，本 ADR 已按最终状态修订）
- 日期：2026-09-04（定案）/ 2026-09-05（实现落库 + 概念修正并修订本 ADR）
- 实现提交：`d86b640964cb5a1898e757a8cdd3506ed1e3eb94`（refactor(tts): TTS 退役出工具池重构为基础模块）

## 背景（Context）

v2.0.0 重构后，Amaidesu 已确立 Agent + 工具 + 存储 + 编排的主体性架构（ADR-005）。但发言管线的最后一公里——reply 文本到语音播放——长期存在三层遗漏：

1. **reply → TTS 断链为 v2 设计遗漏**。`reply` 工具通过 ToolRegistry 返回 `ToolExecutionResult`（含 `success`），StreamerAgent 原本只读 `result.success` 推进决策；`result.content`（`{speech, emotion, action}` 三元组 JSON）从未被下游消费。[数据流规则 §5](data-flow.md) 在原 §5 链路示例结尾曾写"回复文本就绪 → 如需 TTS 渲染，需另行 await edge_tts_synthesize 工具"——但"另行"的调用方从未定案，是文档描述了一个不存在的接线。
2. **四引擎注册但零调用**。`src/modules/tools/output/tts/` 下四个 Provider（EdgeTTS / GPTSoVITS / Voicebox / OmniTTS）已实现并暴露 `register_*_tools(registry, config)`，但组合根 `main.py` 没有任何调用点（架构总览"已知缺口"第 1 条），导致 ToolRegistry 中查无 TTS 工具；OmniTTS 历史 bug：HTTP 流式响应解码后从未 `play_audio`，即注册了也不出声。
3. **`emotion` / `action` 三元组死字段**。`replyer.py` 生成 `{speech, emotion, action}` 三元组后，emotion 与 action 两字段没有任何下游消费者，沦为日志条目。

数据流图的虚线箭头（`RT -.->|invoke 后再渲染| TTS`）曾暗示这条路存在，实际代码从未实现——文档替死人说话。

与此同时，架构总览记录的"渲染工具 `register_*_tools` 无自动调用点"作为已知缺口横跨整个 v2 时代，既影响 TTS 一族，也影响 subtitle / vts / warudo / obs / vrchat 等渲染工具族。

## §8 概念修正（2026-09-05 落地，ADR-007 据此修订）

实现落库前最后一道评审发现：**TTS 在形态上确实是"被 Agent 工具调用"，但实质上没有任何 Agent / LLM 主动调用它**——reply 工具返回 `result.content` 后，由 StreamerAgent 自身解析并直接驱动下游渲染，**完全不经过 ToolRegistry**。

> **主体性判据**（ADR-005）：Agent 与工具的唯一判别是"谁驱动谁"——自我驱动的是 Agent（拥有主循环/心跳/状态机），被调才干活的是工具。TTS 引擎没有自主循环、没有 Agent 调用入口，**事实上是基础设施而非工具**——之前把它塞进 ToolRegistry 只是因为"渲染调用天然看起来像工具调用"。

修正后，TTS 引擎从工具池**整体退役**，成为 `src/modules/tts/` 包内自治的基础模块：
- 引擎自身暴露 `handle_speech(text, utterance_id=None)` / `setup()` / `cleanup()` / `get_stats()` / `ConfigSchema`，不再实现 `ToolProvider` 协议；
- 装配入口为 `src/modules/tts/assembly.py::build_tts_infrastructure(tts_config, event_bus=None)`，按 `core.toml [tts].provider` 构造唯一引擎实例并直接注入 StreamerAgent；
- StreamerAgent 构造期接收 `tts_engine` 实例并把 `engine.handle_speech` 适配为 `speak` 可调用对象注入 `UtteranceQueue`；
- ToolRegistry 中**零 TTS 条目**——Facade 路由层（曾以单一工具名对外）与各引擎 Provider（`EdgeTTSProvider` / `GPTSoVITSProvider` / `VoiceboxProvider` / `OmniTTSProvider` 的 ToolProvider 形态实现）等历史工具彻底消失。

四层对齐（概念 / 配置 / 代码位置 / 文档）保持一致：

| 层 | 修正前 | 修正后 |
|---|---|---|
| 概念 | TTS 是工具（ToolProvider 形态） | TTS 是基础设施（基础模块，Engine 实例） |
| 配置 | `[tools.output.tts.*]` + `[tools.output.config].enabled` 双向管控 | `[tts]` 自包含（开关/目标引擎/队列/超时 + 四个引擎子段；`tools.toml` 无任何 TTS 段） |
| 代码位置 | `src/modules/tools/output/tts/`（ToolProvider 协议） | `src/modules/tts/`（Provider 类 + `assembly.py`） |
| 文档 | 本 ADR §决策 2 Facade 路由层 + §配置 `tools.toml [tools.output.config]` | `build_tts_infrastructure` 直接构造 + `[tts]` 自包含段 |

## 决策（Decision）

将 TTS 重塑为**配置驱动的基础设施**，由主播 Agent 包内的发言管线统一调度，而非独立的"渲染组件"或"工具"。具体决策：

1. **TTS 是基础设施而非工具**（§8 修正）。reply 落库即发声——`reply.result.content` 的 `speech` 字段由 `StreamerAgent._dispatch_speech_and_emotion` 主动解析并投入 `UtteranceQueue`，**不依赖** LLM 决策调用 TTS 工具（事实上也不再存在 TTS 工具）；"每句必出声"是**机制保证**而非**概率保证**（LLM 即使想跳过 TTS 也跳不过——队列已构造、enable 状态由配置决定而非 LLM）。同时 TTS 引擎自身也不再以 ToolProvider 形态注册到 ToolRegistry——它通过 `build_tts_infrastructure` 装配后由 StreamerAgent 构造期注入，由编排队列调用其 `handle_speech`。
2. **`build_tts_infrastructure` 装配入口**。`src/modules/tts/assembly.py::build_tts_infrastructure(tts_config, event_bus=None)` 按 `core.toml [tts].provider` 字段（`edge_tts` / `gptsovits` / `voicebox` / `omni_tts`）单选构造；返回 Provider 实例或 `None`（`enabled=False` / 配置缺失 / 子配置构造异常时统一返回 `None` 降级）；未知 provider 名记 ERROR 并回退到 `edge_tts`（装配兜底，与历史 bootstrap 装配语义一致）。
3. **StreamerAgent 直接持有引擎实例**。StreamerAgent 构造签名新增 `tts_engine: Optional[Any] = None` 参数（来自 `build_tts_infrastructure` 装配结果）；`_on_start` 阶段按 `speech_config.enabled` + `tts_engine is not None` 双闸门决定是否构造 `UtteranceQueue`；构造时把 `engine.handle_speech` 包装为 `speak` 可调用对象注入队列——编排队列对外引擎无关（SpeakCallable = `Callable[[str, Optional[str]], Aw]`，传入真实引擎或测试 mock 均可）。
4. **配置独立段 `core.toml [tts]` 完全自包含**。TTS 是主播级基础设施，其配置独立于 `tools.toml`（工具族清单）与 `agents.toml`（Agent 配置）：
   - 行为参数：`enabled` / `provider` / `max_queue`（默认 3）/ `render_timeout_ms`（默认 60s，覆盖合成+播放全周期）
   - 引擎子段：`[tts.edge_tts]` / `[tts.gptsovits]` / `[tts.voicebox]` / `[tts.omni_tts]`，每个子段为自由 dict，具体键的校验由各引擎 `ConfigSchema` 在装配期补全
   - `tools.toml` **零 TTS 段**——TTS 引擎作为基础模块后连接/合成参数不再外泄到工具配置；迁移由 `CROSS_FILE_MIGRATIONS`（`multi_file_loader.py`）从 `tools.toml [tools.output.config.<engine>]` 整体搬运到 `core.toml [tts.<engine>]`，源段被切除
   - 配置版本同步升至 `CONFIG_VERSION = "2.0.12"`
5. **消费者通道三分法**（与语音的时间耦合度匹配）：
   - **帧级耦合**（未来）皮套口型精准同步 → 工具 invoke 参数流式接口（**留白**，YAGNI 暂不建，待真需求）
   - **起止对齐**（字幕、记账）→ 订阅 `tts.utterance.{started,finished,failed}` 三事件
   - **无耦合**（emotion 表情）→ StreamerAgent 直接 invoke `vts_set_expression`，不经事件、不入队列
   三分法依据详见 [数据流规则 §6](data-flow.md) "TTS 消费者通道三分法"节。
6. **`UtteranceQueue` 位于主播 Agent 包内**（`src/agents/streamer/utterance_queue.py`），不抽到框架层。这条决策引用 ADR-005 §决策 1 主体性判据——UtteranceQueue 是 StreamerAgent 的内部器官（"谁的发言谁管播放节奏"），不是跨 Agent 的基础设施；抽到 `src/modules/` 会引入 `agents/` 反向依赖（违反 [数据流规则 §2 分层规则](data-flow.md)）。队列对外只持有 `SpeakCallable` 与可选的 EventBus 引用，**完全不知道底层是 EdgeTTS 还是 GPTSoVITS**——引擎切换 / 测试 mock 均不需要改队列代码。
7. **`tts.utterance.*` 三事件是终点广播**。消费者不得基于这些事件触发新一轮决策（防环约束）；可做的记账 / 释放锁 / 字幕对齐不构成新决策。三事件 Payload 类（`UtteranceStartedPayload` / `UtteranceFinishedPayload` / `UtteranceFailedPayload`）分开定义（形状不同：`started` 含 `speech_text` + `duration_ms` Optional；`finished` 强调播放时长 int；`failed` 强调 `error_message`）——分开定义比统一形状加判别字段更不易误填。**发布者**：TTS 引擎自身（基础模块，非工具）；仅在收到非空 `utterance_id` 入参时发布；流式引擎 = 首块 PCM 写声卡时发 started；全量引擎 = `play_audio` 调用时发 started。
8. **队列策略：FIFO 串行 + 丢最旧**。单 worker 保证播放顺序（避免叠加/打断），队列满时丢最旧（保证新鲜度，丢弃项不进入 TTS 引擎，自然不触发 utterance 事件——丢消息由队列全权负责）；单 utterance 由 `render_timeout_ms` 看门狗保护（防合成/播放卡死拖垮队列）。默认 `max_queue=3` / `render_timeout_ms=60000`（60s 覆盖合成+播放全周期）。
9. **TTS 引擎是 publish-only 角色**：只发 utterance 事件、不订阅任何事件；事件总线单向数据流约束（数据流规则 §1）不破。
10. **OmniTTS 修复**。原 HTTP 流式响应解码后从未 `play_audio`（`AudioDeviceManager.start_stream` / `write_chunk` / `stop_stream` 流式三步漏调）；本次同步补齐，流式与全量两类引擎共用 `common.py` 函数级共享（不放基类——`ToolProvider` 协议鸭子类型已不适用，强抽基类会抽象泄漏；现以引擎类各自实现 `handle_speech` 即可）。

## 替代方案（Alternatives）

### LLM function-calling 决策调用 TTS

让 Planner 在决策时主动调用 TTS 工具（例如 reply 完成后追加一次 `edge_tts_synthesize` 调用入 Plan）。

**拒绝**。"每句必出声"应是不变式（invariant），不变式必须由机制而非概率保证——LLM 即使偶尔决定跳过 TTS，"主播沉默"也是产品缺陷。FIFO 串行 + 丢最旧由机制保证播放节奏；让 LLM 决定会变成"概率化发声"，回归应答机陷阱。此条与 §8 概念修正后的取舍方向一致：TTS 不在 LLM 决策调用路径上。

### 独立编排组件 / TTS Director

抽象一个独立 `TTSDirector` 类（或 `OutputOrchestrator`），跨 Agent 管理所有 TTS 调用。

**拒绝**。这是 v1 OutputHandlerManager 的换皮——v1 的三阶段架构正是因主体性错位（Agent 主循环被 Manager 链条肢解）被推翻（ADR-005 §决策 1）。把 UtteranceQueue 提到框架层会让 StreamerAgent 失去"发言节奏的自主权"，沦为"调一次 director"的应答机；引用 ADR-005 主体性判据，"谁驱动谁"——UtteranceQueue 服务于 StreamerAgent 的发言节奏，由 StreamerAgent 持有是主体性回归。

### Push 式音频总线（AudioStreamChannel 复活）

恢复 v2.0.6 已拆除的 AudioStreamChannel（pub-sub 推 PCM 流给多个订阅者）。

**拒绝**。理由见 [数据流规则 §6](data-flow.md) "已拆除的 AudioStreamChannel"段：v2 是 pull-style 工具编排，音频数据走 ToolRegistry 调用的返回值（`ToolExecutionResult`）即可；push 通道当年被拆除就是因为无人是消费者。v2.0.10 加 `tts.utterance.*` 三事件已足够覆盖帧级以外的元数据订阅需求（字幕 / 记账）。

### 立刻为流式 PCM 建立 AudioSink 抽象

为 OmniTTS / GPTSoVITS 的流式音频设计 `AudioSink` 接口，下游可以订阅 PCM 块（如皮套口型同步）。

**拒绝**。YAGNI——**当前没有任何真实消费者**承接逐块 PCM。皮套口型同步短期由皮套软件自取本地音频流（系统声音 / WASAPI loopback）兜底，留白待真需求。当第一个真实消费者出现时，再设计接口（参见决策 5 三分法的"帧级"留白）。

### Facade 作为 ToolProvider 路由层（v2.0.10 初版方案，已退役）

实现一个 TTS Facade 工具，ToolProvider 协议，`invoke` 时按 `core.toml [tts].provider` 路由到 edge_tts / gptsovits / voicebox / omni_tts 中激活的那一个；LLM 与上层 Agent 只见该 Facade，引擎身份从配置层透出。

**曾采纳、后 §8 评审否定**。v2.0.10 定案时此方案在形态上自洽（"渲染调用天然像工具调用"），但实现落库前最后一道评审暴露了两个实质问题：
- TTS **从未被任何 Agent / LLM 主动调用**——Facade 注册进 ToolRegistry 后实际零调用方，理由与"LLM 决策调用 TTS"同源（"每句必出声"机制保证）。
- 注册 Facade 仅剩"装配路由"职责，但装配路由在 `build_tts_infrastructure` 已经完成——Facade 是把"装配时已知"重新挂回运行时再"动态路由"的多余一跳。

§8 修正后 Facade 退役：TTS 引擎通过 `build_tts_infrastructure` 在装配期直接构造唯一实例并注入 StreamerAgent，运行时按构造结果直接调用 `engine.handle_speech`——零 Facade 路由层、零 ToolRegistry 条目。**本条如实记录**（先实施、后修正），便于后续看到 git 历史时不误解为"两套设计并存"。

## 后果（Consequences）

- **收益**：
  - **每句必出声由机制保证**。reply 落库即发声，不再依赖 LLM 决定——产品体验的硬指标。
  - **TTS 引擎彻底基础模块化**（§8 修正）。`src/modules/tts/` 包内自治，`handle_speech` / `setup` / `cleanup` / `get_stats` / `ConfigSchema` 五件套直白可用；ToolRegistry 中零 TTS 条目——审计/注册语义回归"Agent-callable 能力"而不是"渲染调用包装"，与 ADR-005 主体性判据自洽。
  - **装配入口单一**。`build_tts_infrastructure(tts_config, event_bus)` 一处返回 Provider 实例或 None；StreamerAgent 构造期注入，无运行时路由层、无 Facade 间接；未知 provider 回退 `edge_tts` 装配兜底，与历史 bootstrap 语义一致。
  - **配置自包含**。`core.toml [tts]` 一段全管（行为参数 + 四个引擎子段），`tools.toml` 不再有任何 TTS 配置；迁移钩子自动搬运用户原值。
  - **事件契约可扩展消费者**。`tts.utterance.*` 终点点播，字幕对齐、reply 耗时记账、失败重试等都是终点广播的安全订阅者。
  - **OmniTTS 修复**。原 HTTP 流式响应解码后从未 `play_audio`，修复后 OmniTTS 端到端可用，与其他三引擎平起平坐。
  - **架构总览已知缺口第 1 条闭环**。TTS 族由 `core [tts]` 驱动装配（基础模块自治，非 TTS 工具族由 `bind_core_tools` 按 `[tools.output.config] enabled` 列表驱动自注册——v2.0.10 同期落地。
- **代价**：
  - **队列串行等待**。丢最旧意味着如果决策循环节奏超过队列容量 + 渲染吞吐之和，新发言会覆盖旧发言；这是为简单性付出的可控代价（FIFO 串行 + 容量上限 + 看门狗）。
  - **`tts.utterance.*` 事件精度百毫秒级**（不是 DAC 采样点精度）。声卡硬件缓冲残余**不在**信号内——事件是引擎回调信号而非播放端物理信号；记账消费者应明确这点（百毫秒级足够会计时）。
  - **配置层级多一段**。`core.toml` 新增 `[tts]` 段是必要的（独立基础设施级调度字段 + 引擎子段），但增加了配置面；与 `tools.toml [tools.output.config]` 的边界需在文档中明确（详见 [架构总览 §④ 配置驱动](overview.md#④-配置驱动)）。
- **遗留**（**如实记录，不掩盖**）：
  - **帧级 PCM 流接口留白**。皮套口型精准同步的帧级消费者接口未建，待真需求；当前由皮套软件自取本地音频流兜底。
  - **`action` 字段仍未消费**。本次范围明确不接入决策调用（独立议题——"是否需要 LLM 决策驱动具体动作执行"是更大讨论），StreamerAgent 解析但 `_ = action` 显式标注未使用。
  - **`tts.utterance.*` 订阅接线待实现**。当前生产代码**暂无订阅者**——字幕 Provider 由 StreamerAgent 通过 `speech` 文本直接 fire-and-forget，不订阅 utterance 事件（事件与字幕存在双轨，待字幕子系统接入事件总线后可统一）；详见 [架构总览 已知缺口第 3 条](overview.md#已知缺口)。接入后的事件契约本身已就绪，不需要再次改动 ADR-007。

---

*最后更新：2026-09-05（§8 概念修正落库后修订——TTS 引擎从工具池整体退役为 `src/modules/tts/` 基础模块；ToolProvider 形态 / TTS Facade / ToolRegistry 条目全部清除；装配入口改为 `build_tts_infrastructure(tts_config, event_bus=None)` 按 `[tts].provider` 单选构造并直接注入 StreamerAgent；配置 `core.toml [tts]` 自包含行为参数 + 四引擎子段、`tools.toml` 零 TTS 段、`CONFIG_VERSION` 升至 2.0.12；§决策 1 主体性、§替代方案"Facade 路由"条改为"曾采纳后 §8 否定"如实记录；§后果增"TTS 彻底基础模块化 / 装配入口单一 / 配置自包含"三条收益；文末状态行补"§8 修正已按最终状态修订"）*