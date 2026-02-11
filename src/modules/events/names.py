"""
事件名称常量定义（3域架构）

使用常量替代魔法字符串，提供 IDE 自动补全和重构支持。
"""


class CoreEvents:
    """核心事件名称常量（3域架构）"""

    # ========== 新的事件命名（简化版） ==========
    # Data Domain: 数据域
    DATA_RAW = "data.raw"
    DATA_MESSAGE = "data.message"

    # Decision Domain: 决策域
    DECISION_INTENT = "decision.intent"

    # Output Domain: 输出域
    OUTPUT_PARAMS = "output.params"

    # ========== Decision Domain 事件 ==========
    DECISION_REQUEST = "decision.request"
    DECISION_RESPONSE_GENERATED = "decision.response_generated"  # MaiCore响应事件
    DECISION_PROVIDER_CONNECTED = "decision.provider.connected"
    DECISION_PROVIDER_DISCONNECTED = "decision.provider.disconnected"

    # ========== Output Domain 事件 ==========
    RENDER_COMPLETED = "render.completed"
    RENDER_FAILED = "render.failed"

    # 系统事件
    CORE_STARTUP = "core.startup"
    CORE_SHUTDOWN = "core.shutdown"
    CORE_ERROR = "core.error"

    # STT (语音识别) 事件
    STT_AUDIO_RECEIVED = "stt.audio.received"
    STT_SPEECH_STARTED = "stt.speech.started"
    STT_SPEECH_ENDED = "stt.speech.ended"

    # 屏幕上下文事件
    SCREEN_CONTEXT_UPDATED = "screen.context.updated"
    SCREEN_CHANGED = "screen.changed"

    # 关键词匹配事件
    KEYWORD_MATCHED = "keyword.matched"

    # VRChat 事件
    VRCHAT_CONNECTED = "vrchat.connected"
    VRCHAT_DISCONNECTED = "vrchat.disconnected"
    VRCHAT_PARAMETER_SENT = "vrchat.parameter.sent"

    # Output Domain: 渲染相关事件
    RENDER_SUBTITLE = "render.subtitle"
    RENDER_STICKER = "render.sticker"

    # OBS 控制事件
    OBS_SEND_TEXT = "obs.send_text"
    OBS_SWITCH_SCENE = "obs.switch_scene"
    OBS_SET_SOURCE_VISIBILITY = "obs.set_source_visibility"

    # 远程流事件
    REMOTE_STREAM_REQUEST_IMAGE = "remote_stream.request_image"

    # VTS 相关事件（跨 Provider 通信）
    VTS_SEND_EMOTION = "vts.send_emotion"
    VTS_SEND_STATE = "vts.send_state"

    # 所有事件名集合（用于 emit_sync 校验等）
    ALL_EVENTS = (
        # 新事件命名（3域核心事件）
        DATA_RAW,
        DATA_MESSAGE,
        DECISION_INTENT,
        OUTPUT_PARAMS,
        # 其他事件
        DECISION_REQUEST,
        DECISION_RESPONSE_GENERATED,
        DECISION_PROVIDER_CONNECTED,
        DECISION_PROVIDER_DISCONNECTED,
        RENDER_COMPLETED,
        RENDER_FAILED,
        CORE_STARTUP,
        CORE_SHUTDOWN,
        CORE_ERROR,
        STT_AUDIO_RECEIVED,
        STT_SPEECH_STARTED,
        STT_SPEECH_ENDED,
        SCREEN_CONTEXT_UPDATED,
        SCREEN_CHANGED,
        KEYWORD_MATCHED,
        VRCHAT_CONNECTED,
        VRCHAT_DISCONNECTED,
        VRCHAT_PARAMETER_SENT,
        RENDER_SUBTITLE,
        RENDER_STICKER,
        OBS_SEND_TEXT,
        OBS_SWITCH_SCENE,
        OBS_SET_SOURCE_VISIBILITY,
        REMOTE_STREAM_REQUEST_IMAGE,
        VTS_SEND_EMOTION,
        VTS_SEND_STATE,
    )
