"""Provider 统一依赖上下文 - 类型安全的依赖注入"""

from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from src.modules.llm.manager import LLMManager
    from src.modules.prompts.manager import PromptManager
    from src.modules.context import ContextService
    from src.modules.tts.audio_device_manager import AudioDeviceManager
    from src.modules.llm.clients.token_usage_manager import TokenUsageManager
    from src.modules.events.event_bus import EventBus
    from src.modules.streaming.audio_stream_channel import AudioStreamChannel
    from src.modules.config.service import ConfigService


@dataclass(frozen=True)
class ProviderContext:
    """所有 Provider 的统一依赖上下文（不可变）

    使用 frozen=True 确保线程安全和一致性。
    所有 Provider 通过构造函数接收此上下文实例。
    """

    # 核心服务
    event_bus: Optional["EventBus"] = None
    config_service: Optional["ConfigService"] = None

    # 音频服务
    audio_stream_channel: Optional["AudioStreamChannel"] = None
    audio_device_service: Optional["AudioDeviceManager"] = None

    # LLM 服务
    llm_service: Optional["LLMManager"] = None
    token_usage_service: Optional["TokenUsageManager"] = None

    # 提示词服务
    prompt_service: Optional["PromptManager"] = None

    # 上下文服务
    context_service: Optional["ContextService"] = None
