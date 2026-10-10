"""
调试 Schema

定义调试相关的数据模型。
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class InjectMessageRequest(BaseModel):
    """注入消息请求

    ``source`` 在直播间语境即观众昵称（payload 的 user.id/user.name 同取此值）；
    消息恒按模拟数据处理（``simulated=True``），无类型/权重概念。
    """

    source: str = "debug_inject"
    text: str


class InjectMessageResponse(BaseModel):
    """注入消息响应"""

    success: bool
    message_id: Optional[str] = None
    error: Optional[str] = None


class EventBusStatsResponse(BaseModel):
    """EventBus 统计响应"""

    total_events: int = 0
    total_subscribers: int = 0
    events_by_name: Dict[str, int] = {}


class SubtitleTestRequest(BaseModel):
    """字幕测试请求：推送到全部已注册后端的文本"""

    text: str


class SubtitleTestResponse(BaseModel):
    """字幕测试响应（``show`` 盲发：逐后端成败以 status 端点与目视为准）"""

    success: bool
    backend_count: int = 0
    error: Optional[str] = None


class SubtitleClearResponse(BaseModel):
    """字幕清空响应"""

    success: bool
    error: Optional[str] = None


class SubtitleBackendInfo(BaseModel):
    """单个字幕后端诊断项"""

    name: str
    enabled: bool


class SubtitleStatusResponse(BaseModel):
    """字幕服务状态：后端清单专治"静默降级"（如 CustomTkinter 不可用）"""

    available: bool
    backend_count: int = 0
    backends: List[SubtitleBackendInfo] = []


class TtsTestRequest(BaseModel):
    """TTS 试说请求：文本经真实引擎合成并从音频汇播放"""

    text: str


class TtsTestResponse(BaseModel):
    """TTS 试说响应（同步长请求；合成失败时引擎已发 tts.utterance.failed）"""

    success: bool
    utterance_id: Optional[str] = None
    error: Optional[str] = None


class TtsStatusResponse(BaseModel):
    """TTS 引擎状态：透传 ``get_stats()``（name/is_connected/计数等）"""

    available: bool
    stats: Dict[str, Any] = {}
