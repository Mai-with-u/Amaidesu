"""
事件名称常量定义

使用常量替代魔法字符串，提供 IDE 自动补全和重构支持。

命名规范：
- 格式: 域.主体.动作（点分隔）
- 域为语义域（live / room / game / rundown / planner / tool / streamer），不是阶段（input / decision / output）
- 通配订阅：``*``=单层 ``#``=多层（MQTT 风格）
"""


class CoreEvents:
    """核心事件名称常量"""

    # ========== Core: 核心系统事件 ==========
    CORE_STARTUP = "core.startup"
    CORE_SHUTDOWN = "core.shutdown"
    CORE_ERROR = "core.error"

    # ========== v2 语义域事件（live 场次生命周期） ==========
    # 发布者：LiveSessionManager（场次管理唯一事实源）。
    # 场次 = 一段有开始/结束边界的直播时间段；房间是场次之上的静态属性。
    LIVE_STARTED = "live.started"
    LIVE_ENDED = "live.ended"

    # ========== v2 语义域事件（room.message.* 直播间行为流） ==========
    # 行为流（发生的事）。注意：room.state.* 是**预留层**（契约决定，
    # 默认不实现任何事件；将来若需主动广播订阅的状态变更才会启用，
    # 不与行为流平铺同层）。
    ROOM_MESSAGE_DANMAKU = "room.message.danmaku"
    ROOM_MESSAGE_GIFT = "room.message.gift"
    ROOM_MESSAGE_SUPER_CHAT = "room.message.super_chat"
    ROOM_MESSAGE_ENTER = "room.message.enter"

    # ========== v2 语义域事件（game.* 游戏里程碑） ==========
    # 低频、只发重大变化。四类：milestone / attention_required / error / report。
    # report 是游戏 Agent 主动向派发方（主播）的上报通道：交付总结（delivery）
    # / 升级决策（escalation）——见 GamePayload.report_kind。
    GAME_MILESTONE = "game.milestone"
    GAME_ATTENTION_REQUIRED = "game.attention_required"
    GAME_ERROR = "game.error"
    GAME_REPORT = "game.report"

    # ========== 流程单（Rundown）子系统事件 ==========
    # 唯一发布者：``RundownState`` 变更边界。``rundown.changed`` 涵盖
    # load / goto / next（含 finish）/ pause / resume 五种状态变更；
    # by 字段区分 agent/human/system；finish 时 segment_id="" 且 index==total。
    RUNDOWN_CHANGED = "rundown.changed"

    # ========== v2 语义域事件（task 异步任务生命周期） ==========
    # 唯一发布者：任务记录表（``src/modules/tools/tasks.py``）的写入边界——
    # 状态**真的变化**时发一条（同状态幂等不重发）。payload 带 task_id /
    # 状态 / 摘要 / 发起方 / 执行者；镜像 ``rundown.changed`` 的单事件 +
    # payload 判别形态。BaseAgent 默认按 ``payload.initiator == self.name``
    # 过滤唤醒（跨 Agent 委派与回执型工具共用）。
    TASK_CHANGED = "task.changed"

    # ========== v2 语义域事件（planner 决策轮记录） ==========
    # 每轮两阶段决策结束发一条（成功/失败/低置信度降级全覆盖），观察器的
    # 决策卡数据源；round_id 为本轮弹幕批次/决策/发言/工具结果的共同关联键。
    PLANNER_DECISION = "planner.decision"
    # 裁决时刻即时事件：reply 工具被调用（Planner 决定回应）时发一条，
    # 表达生成之前到达；观察器实时渲染裁决卡，轮末 decision 按轮回填统计。
    PLANNER_VERDICT = "planner.verdict"

    # ========== v2 语义域事件（streamer 决策管线阶段状态） ==========
    # 决策管线阶段变化即发射（planning/replying/idle），观察器状态条数据源：
    # LLM 挂起时状态条停格即证据。
    STREAMER_STAGE = "streamer.stage"

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
    TOOL_RESULT_WILDCARD = "tool.result.#"

    # ========== v2 语义域事件（tool 健康状态变更通配订阅模式） ==========
    # **这是通配订阅模式专用**，不是被 emit 的具体事件名。emit 时使用具体名
    # 如 "tool.health.maicraft_speak"。仅在状态切换时发射：连续失败达阈值熔断（open）、
    # 探活通过或冷却期满恢复（closed）。
    # Dashboard 转发层订阅 `event_bus.on("tool.health.#", ...)` 一站式监听。
    TOOL_HEALTH_WILDCARD = "tool.health.#"

    # ========== v2 语义域事件（直播间行为流通配订阅模式） ==========
    # 与 TOOL_RESULT_WILDCARD 同性质的通配订阅标识，不是被 emit 的具体事件名。
    # 覆盖 room.message.danmaku / gift / super_chat / enter 四类；
    # 持久层订阅 `event_bus.on(CoreEvents.ROOM_MESSAGE_WILDCARD, ...)`
    # 一站式落业务表。
    ROOM_MESSAGE_WILDCARD = "room.message.#"

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
