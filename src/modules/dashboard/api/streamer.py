"""Streamer 测试台 API（主播发言调试）

提供主播 Agent 手动触发与状态观测端点，服务于 DevTools「主播发言测试」：

- ``GET  /api/v1/streamer/status``         主播 Agent 状态 + 运行统计 + 配置摘要
- ``POST /api/v1/streamer/test-decision``  手动驱动一次两阶段决策（Planner → Replyer）
- ``POST /api/v1/streamer/trigger-proactive`` 真实限流链路的外部主动发言触发

数据来源与边界
--------------
- 运行时态 → :class:`StreamerAgent` 公开门面（``debug_test_decision`` /
  ``trigger_external_proactive`` / ``get_statistics``），不触碰 Agent 私有属性。
- 配置只读展示 → ``server.config_service.main_config["agents"]["streamer"]``，
  与 agenda.py 的读取方式一致。
- ``test-decision`` 为同步长请求（两段 LLM，典型 10~30s）；仅限开发调试面使用。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Dict, Optional, Protocol, cast

from fastapi import APIRouter, Depends

from src.modules.dashboard.dependencies import get_dashboard_server
from src.modules.dashboard.schemas.streamer import (
    StreamerStatusResponse,
    StreamerTestDecisionRequest,
    StreamerTestDecisionResponse,
    TriggerProactiveRequest,
    TriggerProactiveResponse,
)
from src.modules.logging import get_logger

if TYPE_CHECKING:
    from src.modules.dashboard.server import DashboardServer


router = APIRouter()
logger = get_logger("StreamerAPI")

ServerDep = Annotated["DashboardServer", Depends(get_dashboard_server)]


class _DebugTestDecisionCallable(Protocol):
    """StreamerAgent.debug_test_decision 的鸭子接口。

    Dashboard 只依赖这一最小契约；agent_manager 返回的实例满足该契约即可被消费，
    不强制继承 StreamerAgent（与 agenda.py 的门面依赖模式一致）。
    """

    async def __call__(
        self,
        *,
        batch: Optional[list] = None,
        forced: bool = False,
        proactive: bool = False,
    ) -> Dict[str, Any]: ...


def _resolve_streamer_agent(server: "DashboardServer") -> Optional[Any]:
    """从 agent_manager 中取出 ``streamer`` 实例；无则返回 None。"""
    am = getattr(server, "agent_manager", None)
    if am is None:
        return None
    getter = getattr(am, "get_agent_by_name", None) or getattr(am, "get", None)
    if getter is None:
        return None
    try:
        return getter("streamer")
    except Exception:
        return None


def _read_streamer_config(server: "DashboardServer") -> Dict[str, Any]:
    """从 main_config 读 agents.streamer 关键字段；缺字段用默认值。"""
    main_config = server.config_service.main_config if server.config_service else {}
    streamer_cfg = ((main_config or {}).get("agents") or {}).get("streamer") or {}
    return {
        "proactive_enabled": bool(streamer_cfg.get("proactive_enabled", False)),
        "agenda_enabled": bool(streamer_cfg.get("agenda_enabled", False)),
        "batch_window_ms": int(streamer_cfg.get("batch_window_ms", 3000) or 0),
        "planner_llm": str(streamer_cfg.get("planner_llm", "llm_fast") or ""),
        "replyer_llm": str(streamer_cfg.get("replyer_llm", "llm") or ""),
    }


@router.get("/status", response_model=StreamerStatusResponse)
async def get_streamer_status(server: ServerDep) -> StreamerStatusResponse:
    """主播 Agent 状态（降级安全：未注册返回 available=false，不抛 404）。"""
    agent = _resolve_streamer_agent(server)
    if agent is None:
        return StreamerStatusResponse(
            available=False,
            message="主播 Agent 未启用（请在 agents.toml 的 [agents].enabled 启用 streamer）",
        )
    try:
        statistics = dict(agent.get_statistics())
    except Exception as exc:
        logger.warning(f"读取 streamer statistics 失败: {exc}")
        statistics = {}
    return StreamerStatusResponse(
        available=True,
        message=None,
        config=_read_streamer_config(server),
        statistics=statistics,
    )


@router.post("/test-decision", response_model=StreamerTestDecisionResponse)
async def test_decision(
    request: StreamerTestDecisionRequest,
    server: ServerDep,
) -> StreamerTestDecisionResponse:
    """手动驱动一次两阶段决策（同步长请求；前端需放宽超时）。

    错误约定（不抛 HTTPException）：
    - agent 未注册 / 门面缺失 → ``success=false`` + 原因
    - batch 与 proactive 互斥、batch 为空 → ``success=false`` + 校验消息
    - 决策失败（Planner 拒绝/LLM 异常/reply 失败）→ ``success=true`` 但
      ``error``/``plan`` 如实回传——"没回复"本身是有效测试结果
    """
    agent = _resolve_streamer_agent(server)
    if agent is None:
        return StreamerTestDecisionResponse(success=False, message="主播 Agent 未启用")
    if not callable(getattr(agent, "debug_test_decision", None)):
        return StreamerTestDecisionResponse(success=False, message="Agent 不支持调试决策门面")

    if request.proactive and request.batch:
        return StreamerTestDecisionResponse(
            success=False,
            forced=request.forced,
            proactive=True,
            message="proactive 模式不接受弹幕批次",
        )
    if not request.proactive and not request.batch:
        return StreamerTestDecisionResponse(
            success=False,
            forced=request.forced,
            message="弹幕批次为空：请至少提供一条弹幕",
        )

    debug_fn = cast(_DebugTestDecisionCallable, agent.debug_test_decision)
    batch_dicts: Optional[list] = None
    if not request.proactive:
        batch_dicts = [{"nickname": item.nickname, "text": item.text} for item in request.batch]
    try:
        result = await debug_fn(
            batch=batch_dicts,
            forced=request.forced,
            proactive=request.proactive,
        )
    except Exception as exc:
        logger.error(f"test-decision 调用异常: {exc}", exc_info=True)
        return StreamerTestDecisionResponse(
            success=False,
            forced=request.forced,
            proactive=request.proactive,
            message=f"调用失败: {exc}",
        )

    if not result.get("success"):
        return StreamerTestDecisionResponse(
            success=False,
            forced=request.forced,
            proactive=request.proactive,
            error=result.get("error"),
            message=result.get("error") or "决策未执行",
        )

    return StreamerTestDecisionResponse(
        success=True,
        trigger_reason=result.get("trigger_reason"),
        proactive=bool(result.get("proactive")),
        forced=request.forced,
        elapsed_ms=result.get("elapsed_ms"),
        plan=result.get("plan"),
        speech=result.get("speech"),
        emotion=result.get("emotion"),
        utterance_id=result.get("utterance_id"),
        error=result.get("error"),
    )


@router.post("/trigger-proactive", response_model=TriggerProactiveResponse)
async def trigger_proactive(
    request: TriggerProactiveRequest,
    server: ServerDep,
) -> TriggerProactiveResponse:
    """真实限流链路的外部主动发言触发（置位语义）。

    仅置位 pending flag，下个 flush tick 由 ProactiveTrigger 判定：受
    ``proactive_enabled`` / 防接龙间隔 / 每小时上限 / 话题要求四道约束，
    任一不满足则静默丢弃——这是**有意保留的真实行为**（测限流本身）；
    想绕过限流立即开口请用 ``/test-decision`` 的 proactive 模式。
    """
    agent = _resolve_streamer_agent(server)
    if agent is None:
        return TriggerProactiveResponse(success=False, message="主播 Agent 未启用")
    if not callable(getattr(agent, "trigger_external_proactive", None)):
        return TriggerProactiveResponse(success=False, message="Agent 不支持外部主动发言触发")

    try:
        agent.trigger_external_proactive(request.topic_hint)
    except Exception as exc:
        logger.error(f"trigger-proactive 调用异常: {exc}", exc_info=True)
        return TriggerProactiveResponse(success=False, message=f"触发失败: {exc}")

    cfg = _read_streamer_config(server)
    if not cfg["proactive_enabled"]:
        return TriggerProactiveResponse(
            success=True,
            message=(
                "已置位，但 proactive_enabled=false：真实链路会在下个 tick 静默丢弃。"
                "如需立即开口请改用「主动发言（直跑）」模式。"
            ),
        )
    return TriggerProactiveResponse(
        success=True,
        message="已置位：等待下个 flush tick 限流判定（受防接龙/每小时上限/话题要求约束）",
    )
