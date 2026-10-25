"""REST 契约抽样对账（契约测试）。

``dashboard/src/types/index.ts`` 的 interface 是后端 REST payload 的手抄副本，
后端改字段前端无编译告警。本测试把"前端依赖面"物化为清单——每个被前端消费的
端点，列出它实际读取的字段路径——并与后端 FastAPI 路由的 response_model 逐层
对账：前端依赖的每个字段必须真实存在于后端响应模型链上。

边界与取舍：

- 只比字段名存在性，不比类型（类型对账成本高收益低，先立存在性护栏）；
- 后端声明为 ``Dict[str, Any]`` 的透传字段（如 rundown 快照、streamer config）
  内部结构静态不可查，清单只收到该字段本身，深层形状由运行期行为保证；
- 清单覆盖的是"前端读了什么"的抽样，不是端点全集——补录新端点时把前端
  依赖的字段路径抄进 ``REST_CONTRACT`` 即可。

路径语法：点分段逐层下钻；列表（``List[T]``）与可空（``Optional[T]``）自动
解包到元素模型，字典（``Dict[str, T]``）解包到值模型；``*`` 段表示"字典键名
运行期确定、不校验具体键"，停在值模型上继续校验后续段。对联合模型（如时间线
条目 Union）字段命中任一成员即通过。失败信息携带端点标签、完整路径与断裂
位置，能直接定位到具体端点与字段名。
"""

from __future__ import annotations

from types import UnionType
from typing import Any, Union, get_args, get_origin

import pytest
from pydantic import BaseModel

from src.modules.dashboard.schemas import (
    agent as agent_schemas,
    component as component_schemas,
    config as config_schemas,
    debug as debug_schemas,
    event as event_schemas,
    llm as llm_schemas,
    logs as logs_schemas,
    review as review_schemas,
    rundown as rundown_schemas,
    session as session_schemas,
    simulator as simulator_schemas,
    streamer as streamer_schemas,
    system as system_schemas,
)
from src.modules.dashboard.api import memory as memory_api
from src.modules.dashboard.api import tasks as tasks_api
from src.modules.dashboard.api import viewers as viewers_api

# 兼容两类联合注解写法：typing.Union（含 Optional）与 PEP 604 的 X | Y
_UNION_TYPES: tuple[Any, ...] = (Union, UnionType)

# 前端依赖面清单：端点标签 → (后端 response_model, 前端读取的字段路径)。
# 字段路径从 TS interface 手抄——抄写动作本身就是"前端依赖面"的过目登记，
# 后端删改其中任一字段会让对应条目变红，强制前端同步。
REST_CONTRACT: list[tuple[str, type[BaseModel], tuple[str, ...]]] = [
    (
        "systemApi.getStatus → GET /system/status",
        system_schemas.SystemStatusResponse,
        (
            "running",
            "uptime_ms",
            "version",
            "python_version",
            "groups.*.enabled",
            "groups.*.started",
            "groups.*.total",
            "event_bus.total_events",
        ),
    ),
    (
        "systemApi.getHealth → GET /system/health",
        system_schemas.HealthResponse,
        ("status", "timestamp_ms"),
    ),
    (
        "componentApi.getAll → GET /components",
        component_schemas.ComponentListResponse,
        (
            "collectors.name",
            "collectors.type",
            "collectors.is_started",
            "collectors.is_enabled",
            "agents.name",
            "agents.is_started",
            "agents.is_enabled",
        ),
    ),
    (
        "configApi.getConfig → GET /config",
        config_schemas.ConfigResponse,
        ("config",),
    ),
    (
        "configApi.saveBatch → POST /config/batch",
        config_schemas.BatchConfigUpdateResponse,
        (
            "success",
            "message",
            "requires_restart",
            "results.key",
            "results.success",
            "errors.key",
            "errors.message",
        ),
    ),
    (
        "eventsApi.list → GET /events",
        event_schemas.EventListResponse,
        (
            "events.type",
            "events.timestamp_ms",
            "events.data",
            "events.id",
            "total",
            "has_more",
            "has_more_newer",
        ),
    ),
    (
        "debugApi.getEventBusStats → GET /debug/event-bus/stats",
        debug_schemas.EventBusStatsResponse,
        ("total_events", "total_subscribers", "events_by_name"),
    ),
    (
        "llmApi.getUsage → GET /llm/usage（Dict 值模型）",
        llm_schemas.LLMUsageStatsResponse,
        (
            "model_name",
            "total_prompt_tokens",
            "total_completion_tokens",
            "total_tokens",
            "total_calls",
            "total_cost",
            "cache_hit_tokens",
            "cache_miss_tokens",
            "cache_hit_rate",
            "first_call_time",
            "last_call_time",
            "last_updated",
            "context_window",
            "last_call_prompt_tokens",
        ),
    ),
    (
        "llmApi.getUsageSummary → GET /llm/usage/summary",
        llm_schemas.LLMUsageSummaryResponse,
        (
            "total_cost",
            "total_prompt_tokens",
            "total_completion_tokens",
            "total_tokens",
            "total_calls",
            "cache_hit_tokens",
            "cache_miss_tokens",
            "cache_hit_rate",
            "model_count",
        ),
    ),
    (
        "llmApi.getStatistics → GET /llm/history/statistics",
        llm_schemas.LLMHistoryStatisticsResponse,
        (
            "total_requests",
            "successful_requests",
            "failed_requests",
            "success_rate",
            "total_prompt_tokens",
            "total_completion_tokens",
            "total_tokens",
            "total_cost",
            "cache_hit_tokens",
            "cache_miss_tokens",
            "cache_hit_rate",
            "avg_latency_ms",
            "model_stats.count",
            "model_stats.total_tokens",
            "model_stats.total_cost",
            "model_stats.cache_hit_rate",
        ),
    ),
    (
        "agentsApi.listAgents → GET /agents",
        agent_schemas.AgentListResponse,
        (
            "agents.name",
            "agents.description",
            "agents.state",
            "agents.heartbeat_ms",
            "agents.is_alive",
            "agents.restart_count",
            "agents.enabled",
        ),
    ),
    (
        "agentsApi.getAgentState → GET /agents/{name}/state",
        agent_schemas.AgentStateResponse,
        ("name", "state", "heartbeat_ms", "is_alive", "restart_count"),
    ),
    (
        "agentsApi.cancelAgentTask → POST /agents/{name}/tasks/{id}/cancel",
        agent_schemas.AgentCancelTaskResponse,
        ("cancelled",),
    ),
    (
        "liveSessionsApi.list → GET /live-sessions",
        session_schemas.SessionListResponse,
        (
            "items.live_session_id",
            "items.source",
            "items.title",
            "items.room_id",
            "items.platform",
            "items.started_at_ms",
            "items.ended_at_ms",
            "items.message_count",
            "items.is_active",
            "active_session_id",
        ),
    ),
    (
        "liveSessionsApi.open → POST /live-sessions/open",
        session_schemas.SessionOpenResponse,
        ("live_session_id",),
    ),
    (
        "liveSessionsApi.close → POST /live-sessions/{id}/close",
        session_schemas.SessionActionResponse,
        ("success", "detail"),
    ),
    (
        "liveSessionsApi.timeline → GET /live-sessions/{id}/timeline",
        session_schemas.SessionTimelineResponse,
        (
            "live_session_id",
            "items.kind",
            "items.ts_ms",
            "items.event_type",
            "items.data",
            "items.message_id",
            "has_more",
            "events_pruned",
        ),
    ),
    (
        "liveSessionsApi.rundownExecution → GET /live-sessions/{id}/rundown-execution",
        session_schemas.SessionRundownExecutionResponse,
        (
            "live_session_id",
            "rundown_id",
            "segments.seg_id",
            "segments.seg_title",
            "segments.entered_at_ms",
            "segments.left_at_ms",
            "segments.dwell_ms",
            "segments.expected_ms",
            "segments.pause_ms",
            "pause_total_ms",
            "transitions_count",
        ),
    ),
    (
        "rundownApi.getState → GET /rundown/state",
        rundown_schemas.RundownStateResponse,
        (
            "available",
            "message",
            "snapshot",
            "transitions",
            "segments.id",
            "segments.title",
            "segments.task_description",
            "segments.expected_ms",
            "segments.min_duration_ms",
            "segments.notes",
            "config.rundown_id",
        ),
    ),
    (
        "streamerApi.getStatus → GET /streamer/status",
        streamer_schemas.StreamerStatusResponse,
        ("available", "message", "config", "statistics"),
    ),
    (
        "viewersApi.list → GET /viewers",
        viewers_api.ViewerListResponse,
        (
            "total",
            "items.viewer_id",
            "items.user_id",
            "items.user_name",
            "items.link_status",
            "items.message_count",
            "items.gift_count",
            "items.replied_count",
            "items.interaction_count",
            "items.paid_count",
            "items.paid_amount",
            "items.last_active_ms",
        ),
    ),
    (
        "viewersApi.insights → GET /viewers/insights",
        viewers_api.ViewerInsightsResponse,
        (
            "total_viewers",
            "active_today",
            "active_week",
            "active_month",
            "active_older",
            "never_replied",
            "gift_viewers",
            "daily_danmaku.day",
            "daily_danmaku.count",
        ),
    ),
    (
        "viewersApi.detail → GET /viewers/{viewerId}",
        viewers_api.ViewerDetailResponse,
        (
            "viewer_id",
            "user_id",
            "account_id",
            "open_id",
            "open_app_id",
            "user_name",
            "names.user_name",
            "names.first_seen_ms",
            "names.last_seen_ms",
            "link_status",
            "link_candidate.viewer_id",
            "link_candidate.user_name",
            "message_count",
            "replied_count",
            "paid_amount",
            "paid_count",
            "last_active_ms",
            "first_seen_ms",
            "gift_total_count",
            "sc_total_amount",
            "sc_total_count",
            "session_count",
        ),
    ),
    (
        "viewersApi.pending → GET /viewers/pending",
        viewers_api.PendingViewersResponse,
        (
            "total",
            "items.viewer_id",
            "items.user_name",
            "items.account_id",
            "items.open_id",
            "items.message_count",
            "items.interaction_count",
            "items.paid_amount",
            "items.last_active_ms",
            "items.candidate.viewer_id",
            "items.candidate.user_name",
            "items.candidate_account_id",
            "items.candidate_open_id",
            "items.candidate_name_seen_ms",
            "items.candidate_message_count",
            "items.candidate_interaction_count",
            "items.candidate_paid_amount",
            "items.candidate_last_active_ms",
            "items.candidate_names",
        ),
    ),
    (
        "viewersApi.merge|keepSeparate|unlink → POST /viewers/{viewerId}/merge|keep|unlink",
        viewers_api.IdentityActionResponse,
        ("viewer_id",),
    ),
    (
        "reviewApi.get → GET /review",
        review_schemas.SessionReviewResponse,
        (
            "sessions",
            "window_start_ms",
            "window_end_ms",
            "chips",
            "speech",
            "decisions",
            "llm",
            "waterline",
            "game",
            "viewers.top_speakers.viewer_id",
            "viewers.top_speakers.user_name",
            "viewers.top_speakers.message_count",
            "degraded",
        ),
    ),
    (
        "simulatorApi.listPersonas → GET /simulator/personas",
        simulator_schemas.PersonaListResponse,
        (
            "personas.user_id",
            "personas.user_nickname",
            "personas.role",
            "personas.personality",
            "personas.speaking_style",
            "personas.fans_medal_level",
            "personas.guard_level",
            "personas.is_temporary",
            "personas.is_active",
            "personas.messages_generated",
            "is_available",
        ),
    ),
    (
        "simulatorApi.listGifts → GET /simulator/gifts",
        simulator_schemas.GiftListResponse,
        (
            "gifts.gift_id",
            "gifts.gift_name",
            "gifts.category",
            "gifts.weight",
            "gifts.data_type",
            "gifts.unit_price",
            "gifts.sc_amount_rmb",
            "is_available",
        ),
    ),
    (
        "simulatorApi.listReplayDates → GET /simulator/replay/dates",
        simulator_schemas.ReplayDatesResponse,
        ("dates.date", "dates.danmaku_count"),
    ),
    (
        "tasksApi.listTasks → GET /tasks",
        tasks_api.TaskSnapshotResponse,
        (
            "running.task_id",
            "running.instruction",
            "running.status",
            "running.initiator",
            "running.executor",
            "running.owner_agent",
            "running.summary",
            "running.created_at_ms",
            "running.updated_at_ms",
            "finished.task_id",
            "finished.status",
        ),
    ),
    (
        "logsApi.list → GET /logs",
        logs_schemas.LogQueryResponse,
        (
            "entries.timestamp",
            "entries.level",
            "entries.module",
            "entries.message",
            "entries.exception",
            "total",
            "has_more_older",
            "has_more_newer",
            "persist_available",
            "window_start_ms",
        ),
    ),
    (
        "memoryApi.listProfiles → GET /memory/profiles",
        memory_api.ViewerProfileListResponse,
        (
            "total",
            "items.viewer_id",
            "items.platform",
            "items.user_name",
            "items.profile_text",
            "items.last_compressed_at_ms",
            "items.updated_at_ms",
        ),
    ),
    (
        "memoryApi.getStats → GET /memory/stats",
        memory_api.MemoryStatsResponse,
        ("profile_count", "latest_updated_ms"),
    ),
]


def _contained_models(annotation: Any) -> list[type[BaseModel]]:
    """解包注解，返回它承载的响应模型集合。

    ``Optional[T]`` / ``List[T]`` / ``Dict[str, T]`` 逐层解包到 T；联合注解
    （如时间线条目的五成员 Union）收集全部成员模型。
    """
    origin = get_origin(annotation)
    if origin in _UNION_TYPES:
        models: list[type[BaseModel]] = []
        for arg in get_args(annotation):
            models.extend(_contained_models(arg))
        return models
    if origin in (list, dict):
        value_arg = get_args(annotation)[0 if origin is list else 1]
        return _contained_models(value_arg)
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation]
    return []


def _walk_fields(model: type[BaseModel], path: str, label: str) -> None:
    """沿字段路径逐段下钻，任一段缺失即断言失败并指明断裂位置。"""
    segments = path.split(".")
    candidates: list[type[BaseModel]] = [model]
    walked: list[str] = []
    for index, segment in enumerate(segments):
        if segment == "*":
            # 字典键名运行期确定：跳过键校验，候选停在当前值模型上
            continue
        hits = [m for m in candidates if segment in m.model_fields]
        assert hits, (
            f"{label}：前端依赖字段 '{path}' 在后端 response_model 上不存在"
            f"（断裂于 '{'.'.join(walked + [segment])}' 段，候选模型 "
            f"{[m.__name__ for m in candidates]}）——后端字段变更需同步前端类型"
        )
        walked.append(segment)
        if index == len(segments) - 1:
            return
        candidates = [nested for hit in hits for nested in _contained_models(hit.model_fields[segment].annotation)]
        assert candidates, (
            f"{label}：字段路径 '{path}' 在 '{'.'.join(walked)}' 处到达标量/无模型字典，"
            "无法继续下钻——检查清单路径是否抄错"
        )


@pytest.mark.parametrize(("label", "model", "fields"), REST_CONTRACT, ids=[entry[0] for entry in REST_CONTRACT])
def test_frontend_fields_exist_in_response_model(label: str, model: type[BaseModel], fields: tuple[str, ...]) -> None:
    """端点的每个前端依赖字段都必须存在于后端 response_model 链上。"""
    assert fields, f"{label}：依赖面清单为空等于没有护栏，请补录前端读取的字段路径"
    for path in fields:
        _walk_fields(model, path, label)
