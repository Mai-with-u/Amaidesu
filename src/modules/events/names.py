"""
事件名称常量定义（v2 语义域事件）

使用常量替代魔法字符串，提供 IDE 自动补全和重构支持。

命名规范：
- 格式: 域.主体.动作（点分隔）
- 域为语义域（live / room / game / agenda / planner / tool / streamer），不是阶段（input / decision / output）
- 通配订阅：``*``=单层 ``#``=多层（MQTT 风格）

v2 收口删除（Stage-glue 胶水事件）：
- ~~decision.intent.generated~~（无 Intent；决策出口=工具调用，无事件）
- ~~output.intent.dispatched~~ / ~~output.intent.finished~~ / ~~output.handler.completed~~
  （无 OutputHandlerManager；统一为 ToolRegistry.invoke）
- ~~output.obs.command~~（→ 工具直接调用）
- ~~output.sticker.command~~（StickerHelper 零实例化零调用、消费端 VTSProvider
  仅空转订阅；接电线也救不了——没有 LLM 工具暴露贴纸触发，未来做表情功能
  时再重新设计）

详细规范请参考: docs/architecture/event-naming-convention.md
"""


class CoreEvents:
    """核心事件名称常量（Wave 6 语义域事件 + 核心系统事件）"""

    # ========== Core: 核心系统事件 ==========
    CORE_STARTUP = "core.startup"
    CORE_SHUTDOWN = "core.shutdown"
    CORE_ERROR = "core.error"

    # ========== v2 语义域事件（live 场次生命周期） ==========
    # 详见 .omo/drafts/amaidesu-v2-event-contract.md "live.*" 节
    LIVE_STARTED = "live.started"
    LIVE_ENDED = "live.ended"

    # ========== v2 语义域事件（room.message.* 直播间行为流） ==========
    # 行为流（发生的事）。注意：room.state.* 是**预留层**（契约决定，
    # 默认不实现任何事件；将来若需主动广播订阅的状态变更才会启用，
    # 不与行为流平铺同层）。详见 event-contract.md "room.*" 节。
    ROOM_MESSAGE_DANMAKU = "room.message.danmaku"
    ROOM_MESSAGE_GIFT = "room.message.gift"
    ROOM_MESSAGE_SUPER_CHAT = "room.message.super_chat"
    ROOM_MESSAGE_ENTER = "room.message.enter"

    # ========== v2 语义域事件（game.* 游戏里程碑） ==========
    # 低频、只发重大变化。三类：milestone / attention_required / error
    GAME_MILESTONE = "game.milestone"
    GAME_ATTENTION_REQUIRED = "game.attention_required"
    GAME_ERROR = "game.error"

    # ========== v2 语义域事件（agenda/planner） ==========
    AGENDA_UPDATE = "agenda.update"
    PLANNER_CHECKPOINT = "planner.checkpoint"

    # ========== v2 语义域事件（tts 一次发声实例生命周期） ==========
    # 由 TTS 工具自身发布（已持有 event_bus 的 create_xxx_provider 既有签名）。
    # 三个事件描述"同一次发声实例"在不同时间点的状态，ut utterance_id 串联为全链路关联键。
    # started 触发于"开始出声"时刻：流式引擎=首块 PCM 写声卡，全量引擎=play_audio 调用。
    # finished 触发于播放完成时刻（百毫秒级精度，硬件声卡缓冲残余不在信号内）。
    # failed 触发于合成或播放失败时刻。
    # 这三个事件是终点广播：消费者不得触发新决策（防环约束）。
    TTS_UTTERANCE_STARTED = "tts.utterance.started"
    TTS_UTTERANCE_FINISHED = "tts.utterance.finished"
    TTS_UTTERANCE_FAILED = "tts.utterance.failed"

    # ========== v2 语义域事件（streamer 主播发言业务事实） ==========
    # 主播 Agent 已生成一条发言的业务事实：与 TTS 启用与否正交，下游消费者
    # （Simulator 节奏唤醒、ContextService 历史写入、字幕器、未来回放）拿到
    # 同一份业务信号，不依赖声卡/TTS 引擎是否存在。
    # utterance_id 与 tts.utterance.* 共用同一关联键（编排层生成，全链路串联）。
    STREAMER_SPEECH = "streamer.speech"

    # ========== v2 语义域事件（tool 异步工具结果通配订阅模式） ==========
    # **这是通配订阅模式专用**，不是被 emit 的具体事件名。emit 时使用具体名
    # 如 "tool.result.speak"/"tool.result.summarize_timeline"。
    # 订阅者可以 `event_bus.on("tool.result.#", ...)` 一站式监听所有工具结果。
    # 详见 event-contract.md "tool.result.#" 节。
    TOOL_RESULT_WILDCARD = "tool.result.#"

    @classmethod
    def get_all_events(cls) -> tuple[str, ...]:
        """
        获取所有定义的事件名

        通过反射自动收集所有符合命名规范的事件常量。
        筛选条件：
        1. 不以下划线开头（排除私有属性）
        2. 值为字符串
        3. 值为小写（排除类名等）
        4. 包含点号（事件特征）
        """
        return tuple(
            value
            for name, value in vars(cls).items()
            if not name.startswith("_") and isinstance(value, str) and value.islower() and ("." in value)
        )

    # 所有事件名集合（用于事件验证等）
    # 模块加载时自动更新
    ALL_EVENTS: tuple[str, ...] = ()  # 占位符，模块末尾会被更新


# 在类定义后，模块级别自动更新 ALL_EVENTS
CoreEvents.ALL_EVENTS = CoreEvents.get_all_events()
