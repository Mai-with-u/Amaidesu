"""渲染工具模块顶层入口

按组件族拆分到子包：

| 子包 | 内容 |
|---|---|
| ``vts/``     | VTS 全家桶 + VRChat（VTSHandler, LipSyncProcessor, ExpressionController, HotkeyMatcher, IdleMotionController, VRChatProvider） |
| ``warudo/``  | Warudo 全家桶（WarudoProvider + 5 state 类 + 5 后台任务 + SubtitleManager + Sender） |
| ``subtitle/`` | Subtitle GUI 服务导出（``SubtitleGuiService``）——工具形态已退役，字幕基础设施由 ``src/modules/subtitle/`` 自治装配 |
| ``obs/``     | OBS Provider（3 个工具：send_text / switch_scene / set_source_visibility） |
| ``remote_stream/`` | 消息协议 + 分发器（无 websocket 脚手架） |
| ``debug/``   | dump_intent 调试函数（替换 DebugConsoleHandler） |

注：TTS 与字幕均已提升为基础设施。TTS 由 ``src/modules/tts/`` 自治装配；
字幕由 ``src/modules/subtitle/build_subtitle_infrastructure`` 自治装配。两者
均不通过 ``ToolRegistry`` 注册为可调用工具，而是配置驱动的语音/字幕组件。
"""

from src.modules.tools.output.debug import DebugConfig, dump_intent
from src.modules.tools.output.obs import (
    OBSProvider,
    create_obs_provider,
    register_obs_tools,
)
from src.modules.tools.output.remote_stream import (
    AudioConfig,
    ImageConfig,
    MessageType,
    RemoteStreamTypes,
    StreamMessage,
)
from src.modules.tools.output.vts import (
    ExpressionController,
    HotkeyMatcher,
    IdleMotionController,
    LipSyncProcessor,
    VRChatProvider,
    VTSProvider,
    create_vrchat_provider,
    create_vts_provider,
    register_vrchat_tools,
    register_vts_tools,
)
from src.modules.tools.output.warudo import (
    ActionSender,
    BlinkTask,
    ShiftTask,
    TalkingHeadTask,
    ThrowFishTask,
    TypingActionTask,
    WarudoProvider,
    WarudoStateManager,
    WarudoSubtitleManager,
    create_warudo_provider,
    register_warudo_tools,
)

__all__ = [
    # VTS
    "VTSProvider",
    "VRChatProvider",
    "LipSyncProcessor",
    "ExpressionController",
    "HotkeyMatcher",
    "IdleMotionController",
    "create_vts_provider",
    "create_vrchat_provider",
    "register_vts_tools",
    "register_vrchat_tools",
    # Warudo
    "WarudoProvider",
    "WarudoStateManager",
    "WarudoSubtitleManager",
    "BlinkTask",
    "ShiftTask",
    "TalkingHeadTask",
    "ThrowFishTask",
    "TypingActionTask",
    "ActionSender",
    "create_warudo_provider",
    "register_warudo_tools",
    # OBS
    "OBSProvider",
    "create_obs_provider",
    "register_obs_tools",
    # RemoteStream
    "MessageType",
    "StreamMessage",
    "AudioConfig",
    "ImageConfig",
    "RemoteStreamTypes",
    # Debug
    "DebugConfig",
    "dump_intent",
]
