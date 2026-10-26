"""
调试 API

提供调试和测试接口。
"""

import base64
import uuid
from typing import TYPE_CHECKING, Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException

from src.modules.dashboard.dependencies import get_dashboard_server, server_component
from src.modules.dashboard.schemas.debug import (
    EventBusStatsResponse,
    InjectMessageRequest,
    InjectMessageResponse,
    SubtitleBackendInfo,
    SubtitleClearResponse,
    SubtitleStatusResponse,
    SubtitleTestRequest,
    SubtitleTestResponse,
    TtsSayRequest,
    TtsSayResponse,
    TtsStatusResponse,
    TtsTestRequest,
    TtsTestResponse,
)
from src.modules.events.names import CoreEvents
from src.modules.events.payloads import RoomMessagePayload, RoomMessageUser
from src.modules.logging import get_logger
from src.modules.tts.protocol import TtsObservation
from src.modules.utils.time_utils import now_ms

if TYPE_CHECKING:
    from src.modules.dashboard.server import DashboardServer

router = APIRouter()
logger = get_logger("DebugAPI")


# 类型别名，用于依赖注入
ServerDep = Annotated["DashboardServer", Depends(get_dashboard_server)]


@router.post("/inject-message", response_model=InjectMessageResponse)
async def inject_message(
    request: InjectMessageRequest,
    server: ServerDep,
) -> InjectMessageResponse:
    """注入测试消息到系统（发布 room.message.danmaku，走真实弹幕链路）。

    会话语义：弹幕经 StorageLedger 落 live_chat（单一事实源），主播
    Agent 决策/表达历史直接读 live_chat——注入消息天然进入决策上下文。
    注入属手动测试行为，payload 恒标记 ``simulated=True``：统计口径中
    不算真实观众（"模拟观众不是观众"），批量造数请走世界模拟器。
    """
    event_bus = server.event_bus
    if not event_bus:
        return InjectMessageResponse(success=False, error="Event bus not available")

    try:
        # 通过 EventBus 发布 room.message.danmaku（v2 语义域事件）。
        # user.name 用 source 承载昵称——前端注入的"来源标识"在直播间语境
        # 就是观众昵称，Agent 侧统一读 user_nickname。
        # 场次归属（live_session_id）由场次盖章拦截器统一注入；message_id
        # 现场生成，与响应回传同一 ID（可对账"注入 → 决策 → 回复"链路）。
        message_id = str(uuid.uuid4())
        payload = RoomMessagePayload(
            message_id=message_id,
            message_type="danmaku",
            user=RoomMessageUser(
                id=request.source,
                name=request.source,
            ),
            content=request.text,
            timestamp_ms=now_ms(),
            simulated=True,
        )
        await event_bus.emit(
            CoreEvents.ROOM_MESSAGE_DANMAKU,
            payload,
            source="dashboard.debug",
        )

        logger.info(f"注入消息成功: {message_id}")
        return InjectMessageResponse(success=True, message_id=message_id)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"注入消息失败: {e}")
        return InjectMessageResponse(success=False, error=str(e))


@router.get("/event-bus/stats", response_model=EventBusStatsResponse)
async def get_event_bus_stats(
    server: ServerDep,
) -> EventBusStatsResponse:
    """获取 EventBus 统计"""
    event_bus = server.event_bus
    if not event_bus:
        return EventBusStatsResponse()

    try:
        all_stats = event_bus.get_all_stats() if hasattr(event_bus, "get_all_stats") else {}

        total_events = 0
        total_subscribers = 0
        events_by_name: dict[str, int] = {}

        for event_name, stats in all_stats.items():
            total_events += stats.emit_count
            total_subscribers += stats.listener_count
            events_by_name[event_name] = stats.emit_count

        return EventBusStatsResponse(
            total_events=total_events,
            total_subscribers=total_subscribers,
            events_by_name=events_by_name,
        )
    except Exception as e:
        logger.exception(f"获取 EventBus 统计失败: {e}")
        return EventBusStatsResponse()


# ---------------------------------------------------------------------------
# 基础设施直测（调试页数据面）：不经业务链（无 LLM / 采集器），直接驱动
# 字幕服务 / TTS 引擎。与 inject-message 的分工——那边走真实业务链仿真，
# 这里秒级反馈零 token。未注入基础设施时 503（装配缺失非业务失败）。
# ---------------------------------------------------------------------------


# 组件访问声明：基础设施直测端点必需字幕服务 / TTS 引擎（未装配统一 503）
SubtitleServiceDep = Annotated[Any, Depends(server_component("subtitle_service", missing_detail="字幕服务未注入"))]
# TTS 引擎完整执行契约（合成入口 handle_speech / debug_synthesize），装配缺席 503
TtsEngineDep = Annotated[Any, Depends(server_component("tts_engine", missing_detail="TTS 引擎未装配"))]
# TTS 引擎观测面契约（只读 get_stats），装配缺席 503（/tts/status 的历史语义）
TtsObservationDep = Annotated[TtsObservation, Depends(server_component("tts_engine", missing_detail="TTS 引擎未装配"))]


@router.post("/subtitle/test", response_model=SubtitleTestResponse)
async def test_subtitle(
    request: SubtitleTestRequest,
    service: SubtitleServiceDep,  # noqa: B008
) -> SubtitleTestResponse:
    """推送一条测试字幕到全部已注册后端（Tk 悬浮窗 / Dashboard 字幕位同亮）。

    ``show`` 盲发：SubtitleService 对逐后端异常做故障隔离（只记 ERROR），
    本端点拿不到逐后端成败——程序性反馈看 ``GET /subtitle/status``，
    视觉验收由用户目视。``utterance_id`` 打 debug-test 前缀便于日志检索。
    """
    utterance_id = f"debug-test-{now_ms()}"
    try:
        await service.show(request.text, utterance_id=utterance_id)
    except Exception as e:
        logger.exception(f"测试字幕推送失败: {e}")
        return SubtitleTestResponse(success=False, error=str(e))
    logger.info(f"测试字幕已推送: utterance_id={utterance_id}, backends={service.backend_count}")
    return SubtitleTestResponse(success=True, backend_count=service.backend_count)


@router.post("/subtitle/clear", response_model=SubtitleClearResponse)
async def clear_subtitle(
    service: SubtitleServiceDep,  # noqa: B008
) -> SubtitleClearResponse:
    """清空全部已注册后端的字幕显示。

    真实链路由 ``tts.utterance.finished`` 事件清空；测试无 finished，
    清空路径本身即测试对象，故独立成端点。
    """
    try:
        await service.clear()
    except Exception as e:
        logger.exception(f"测试字幕清空失败: {e}")
        return SubtitleClearResponse(success=False, error=str(e))
    return SubtitleClearResponse(success=True)


@router.get("/subtitle/status", response_model=SubtitleStatusResponse)
async def get_subtitle_status(
    service: SubtitleServiceDep,  # noqa: B008
) -> SubtitleStatusResponse:
    """字幕服务诊断：后端清单（name + enabled），专治后端静默降级。"""
    try:
        diagnostics = service.backend_diagnostics
    except Exception as e:
        logger.exception(f"读取字幕服务诊断失败: {e}")
        return SubtitleStatusResponse(available=False)
    backends = [SubtitleBackendInfo(**item) for item in diagnostics if isinstance(item, dict)]
    return SubtitleStatusResponse(
        available=True,
        backend_count=len(backends),
        backends=backends,
    )


async def _play_utterance(engine: Any, text: str, id_prefix: str) -> tuple[str, Optional[str]]:
    """驱动 TTS 引擎试说一句（合成 + 声卡播放 + utterance 事件 + 字幕联动）。

    返回 ``(utterance_id, error)``：成功时 error 为 None；失败时引擎协议
    约定已主动发 ``tts.utterance.failed``，此处只回传 HTTP 面的失败原因。
    test（试说）与 say（台前重播）共用本调用路径。
    """
    utterance_id = f"{id_prefix}-{now_ms()}"
    try:
        await engine.handle_speech(text, utterance_id=utterance_id)
    except Exception as e:
        logger.exception(f"TTS 试说失败: {e}")
        return utterance_id, str(e)
    logger.info(f"TTS 试说完成: utterance_id={utterance_id}")
    return utterance_id, None


@router.post("/tts/test", response_model=TtsTestResponse)
async def test_tts(
    request: TtsTestRequest,
    engine: TtsEngineDep,  # noqa: B008
) -> TtsTestResponse:
    """驱动 TTS 引擎调试合成。

    双模式：``play_on_device=True`` 试说一句（合成 + 真实音频汇播放 +
    tts.utterance.* 事件 + 字幕联动，忽略覆盖参数）；``False`` 生成试
    听音频（配置参数 + 覆盖参数合成，返回 base64 wav，不占声卡、不驱
    动字幕）。同步长请求，前端需放宽超时并做 loading 防连点。
    """
    if not request.play_on_device:
        synth = getattr(engine, "debug_synthesize", None)
        if not callable(synth):
            return TtsTestResponse(success=False, error="当前引擎不支持音频合成接口（仅 GPT-SoVITS 支持）")
        overrides = {
            k: v
            for k, v in {
                "temperature": request.temperature,
                "speed_factor": request.speed_factor,
                "sample_steps": request.sample_steps,
                "top_k": request.top_k,
                "top_p": request.top_p,
            }.items()
            if v is not None
        }
        try:
            audio = await synth(request.text, overrides)
        except Exception as e:
            logger.exception(f"TTS 调试合成失败: {e}")
            return TtsTestResponse(success=False, error=str(e))
        audio_base64 = base64.b64encode(audio).decode("ascii")
        logger.info(f"TTS 调试合成完成: {len(audio)} bytes, overrides={overrides}")
        return TtsTestResponse(success=True, audio_base64=audio_base64)

    utterance_id, error = await _play_utterance(engine, request.text, "debug-test")
    if error is not None:
        return TtsTestResponse(success=False, utterance_id=utterance_id, error=error)
    return TtsTestResponse(success=True, utterance_id=utterance_id)


@router.post("/tts/say", response_model=TtsSayResponse)
async def say_tts(
    request: TtsSayRequest,
    engine: TtsEngineDep,  # noqa: B008
) -> TtsSayResponse:
    """台前重播：把给定文本重新合成并在声卡播放（发言卡"重播"按钮数据面）。

    重播 = 同文本重合成：utterance 生命周期事件不留 utterance_id → 文本的
    服务端留档，前端持原文本直接回传。与 ``/tts/test`` 试说共用
    ``handle_speech`` 完整生产链路（utterance 事件 + 字幕联动）。同步长
    请求，前端需放宽超时并做 loading 防连点。
    """
    utterance_id, error = await _play_utterance(engine, request.text, "debug-say")
    if error is not None:
        return TtsSayResponse(success=False, utterance_id=utterance_id, error=error)
    return TtsSayResponse(success=True, utterance_id=utterance_id)


@router.get("/tts/status", response_model=TtsStatusResponse)
async def get_tts_status(
    engine: TtsObservationDep,  # noqa: B008
) -> TtsStatusResponse:
    """TTS 引擎状态：透传 ``get_stats()``（name/is_connected/计数等）。

    ``engine`` 按观测面协议 ``TtsObservation`` 收窄：装配边界已保证在册
    引擎实现 ``get_stats``，这里只需兜读取异常（统计读失败按空 stats 展示）。
    """
    try:
        stats = dict(engine.get_stats())
    except Exception as e:
        logger.exception(f"读取 TTS 引擎状态失败: {e}")
        stats = {}
    return TtsStatusResponse(available=True, stats=stats)
