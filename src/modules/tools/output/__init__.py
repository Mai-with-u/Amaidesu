"""渲染工具模块（Wave 4 顶层入口）

迁移自 ``src.stages/output/handlers/`` 的所有渲染实现，按组件族拆分到子包：

| 子包 | 内容 |
|---|---|
| ``vts/``     | VTS 全家桶 + VRChat（VTSHandler, LipSyncProcessor, ExpressionController, HotkeyMatcher, IdleMotionController, VRChatProvider） |
| ``warudo/``  | Warudo 全家桶（WarudoProvider + 5 state 类 + 5 后台任务 + SubtitleManager + Sender） |
| ``tts/``     | 4 个 TTS Provider（EdgeTTS / GPTSoVITS / OmniTTS / Voicebox） |
| ``subtitle/`` | Subtitle GUI 服务 + push_subtitle 工具（拆分） |
| ``obs/``     | OBS Provider（3 个工具：send_text / switch_scene / set_source_visibility） |
| ``remote_stream/`` | 消息协议 + 分发器（无 websocket 脚手架） |
| ``debug/``   | dump_intent 调试函数（替换 DebugConsoleHandler） |

迁移策略（与 .omo/drafts/amaidesu-v2-migration.md A 段对齐）:
- 引擎内部 verbatim（callback 解耦零改动）
- 去 base 继承（AvatarHandlerBase / AudioHandlerBase 消失）
- ToolProvider 协议统一入口
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
from src.modules.tools.output.subtitle import (
    SubtitleGuiService,
    SubtitleProvider,
    create_subtitle_provider,
    register_subtitle_tools,
)
from src.modules.tools.output.tts import (
    EdgeTTSProvider,
    GPTSoVITSProvider,
    OmniTTSProvider,
    VoiceboxProvider,
    create_edge_tts_provider,
    create_gptsovits_provider,
    create_omni_tts_provider,
    create_voicebox_provider,
    register_edge_tts_tools,
    register_gptsovits_tools,
    register_omni_tts_tools,
    register_voicebox_tools,
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
    # TTS
    "EdgeTTSProvider",
    "GPTSoVITSProvider",
    "OmniTTSProvider",
    "VoiceboxProvider",
    "create_edge_tts_provider",
    "create_gptsovits_provider",
    "create_omni_tts_provider",
    "create_voicebox_provider",
    "register_edge_tts_tools",
    "register_gptsovits_tools",
    "register_omni_tts_tools",
    "register_voicebox_tools",
    # Subtitle
    "SubtitleProvider",
    "SubtitleGuiService",
    "create_subtitle_provider",
    "register_subtitle_tools",
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
