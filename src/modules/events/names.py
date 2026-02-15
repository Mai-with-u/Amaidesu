"""
事件名称常量定义（3域架构）

使用常量替代魔法字符串，提供 IDE 自动补全和重构支持。
"""


class CoreEvents:
    """核心事件名称常量（3域架构）"""

    # ========== Data Domain: 数据域 ==========
    DATA_MESSAGE = "data.message"

    # ========== Decision Domain: 决策域 ==========
    DECISION_INTENT = "decision.intent"

    # ========== Output Domain: 输出域 ==========
    OUTPUT_INTENT = "output.intent"  # 过滤后的 Intent（新架构）

    # ========== Decision Domain 事件 ==========
    DECISION_REQUEST = "decision.request"
    DECISION_RESPONSE_GENERATED = "decision.response_generated"  # MaiCore响应事件
    DECISION_PROVIDER_CONNECTED = "decision.provider.connected"
    DECISION_PROVIDER_DISCONNECTED = "decision.provider.disconnected"

    # ========== Input Domain Provider 事件 ==========
    INPUT_PROVIDER_CONNECTED = "input.provider.connected"
    INPUT_PROVIDER_DISCONNECTED = "input.provider.disconnected"

    # ========== Output Domain Provider 事件 ==========
    OUTPUT_PROVIDER_CONNECTED = "output.provider.connected"
    OUTPUT_PROVIDER_DISCONNECTED = "output.provider.disconnected"

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

    @classmethod
    def get_all_events(cls) -> tuple[str, ...]:
        """
        获取所有定义的事件名

        通过反射自动收集所有符合命名规范的事件常量。
        筛选条件：
        1. 不以下划线开头（排除私有属性）
        2. 值为字符串
        3. 值为小写（排除类名等）
        4. 包含点号或以 CORE_ 开头（事件特征）
        """
        return tuple(
            value
            for name, value in vars(cls).items()
            if not name.startswith("_")
            and isinstance(value, str)
            and value.islower()
            and ("." in value or name.startswith("CORE_"))
        )

    # 所有事件名集合（用于事件验证等）
    # 模块加载时自动更新
    ALL_EVENTS = ()  # 占位符，模块末尾会被更新


# 在类定义后，模块级别自动更新 ALL_EVENTS
CoreEvents.ALL_EVENTS = CoreEvents.get_all_events()
