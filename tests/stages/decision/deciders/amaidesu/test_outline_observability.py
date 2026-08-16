"""AmaidesuDecider 调试可观察性测试（T12）

覆盖：
- ``outline_transitions()`` 鸭子类型方法
  - 未激活 → loaded=False + 空列表
  - 已激活 → 返回 ``OutlineState.get_transitions()`` 内容
- ``outline_segments()`` 返回 ``expanded_cache`` 字段
- IntentMetadata 在 Intent 发布时填充 ``trigger_reason`` / ``outline_segment_id``

参考：
- src/stages/decision/deciders/amaidesu/amaidesu_decider.py
- src/stages/decision/deciders/amaidesu/outline_state.py
- src/modules/types/intent.py
"""

from __future__ import annotations

# 先导入 config.schemas 种子，规避 deciders/__init__ 的预存在循环导入
import src.modules.config.schemas  # noqa: F401  # isort:skip

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules.types.capabilities import (
    ParameterSpec,
    UnifiedActionEntry,
    UnifiedCapabilitiesView,
)
from src.modules.types.base.normalized_message import NormalizedMessage
from src.stages.decision.deciders.amaidesu.amaidesu_decider import AmaidesuDecider
from src.stages.decision.deciders.amaidesu.outline import OutlineSegment, StreamOutline
from src.stages.decision.deciders.amaidesu.outline_state import OutlineState


# ==================== 辅助 ====================


def make_message(text: str = "你好", **kwargs) -> NormalizedMessage:
    return NormalizedMessage(
        text=text,
        source=kwargs.get("source", "bili_danmaku"),
        data_type=kwargs.get("data_type", "text"),
        importance=kwargs.get("importance", 0.5),
        timestamp_ms=kwargs.get("timestamp_ms", 1700000000000),
        user_nickname=kwargs.get("nickname", "观众A"),
    )


def make_llm_response(success: bool = True, content: str = "", error: str = "") -> SimpleNamespace:
    return SimpleNamespace(success=success, content=content, error=error)


def _planner_json(should_reply: bool = True, target: str = "用户", confidence: float = 0.8) -> str:
    return json.dumps(
        {
            "should_reply": should_reply,
            "target": target,
            "topic_summary": "",
            "reply_guidance": "",
            "confidence": confidence,
        }
    )


def _replyer_json(text: str = "好的呀", emotion: str = "happy") -> str:
    return json.dumps({"text": text, "emotion": emotion, "action": "", "action_parameters": {}})


def _make_outline(*seg_ids: str) -> StreamOutline:
    return StreamOutline(
        outline_id="test_outline",
        title="测试大纲",
        segments=[
            OutlineSegment(
                id=seg_id,
                title=f"环节 {seg_id}",
                task_description=f"任务 {seg_id}",
                duration_ms=60_000,
            )
            for seg_id in seg_ids
        ],
    )


def make_decider(config: dict, llm_responses: list | None = None) -> AmaidesuDecider:
    """构造测试用 AmaidesuDecider（不带 outline 真实加载，手动塞组件）"""
    event_bus = MagicMock()
    event_bus.emit = AsyncMock()

    llm_service = MagicMock()
    if llm_responses is not None:
        llm_service.chat = AsyncMock(side_effect=llm_responses)
    else:
        llm_service.chat = AsyncMock(return_value=make_llm_response(success=True, content="{}"))

    prompt_service = MagicMock()
    prompt_service.render_safe = MagicMock(return_value="rendered-prompt")

    return AmaidesuDecider(
        config=config,
        event_bus=event_bus,
        llm_service=llm_service,
        prompt_service=prompt_service,
        config_service=None,
        context_service=None,
        capabilities_provider=None,
    )


# ==================== outline_transitions() 鸭子类型方法 ====================


class TestOutlineTransitionsDuckMethod:
    """:meth:`AmaidesuDecider.outline_transitions` 行为覆盖"""

    @pytest.mark.asyncio
    async def test_outline_transitions_returns_loaded_false_when_uninitialized(self):
        """大纲未初始化时 → loaded=False + 空列表（不返回 501 风格）"""
        decider = make_decider(config={"type": "amaidesu"})
        # _outline_state 默认为 None
        assert decider._outline_state is None

        result = await decider.outline_transitions()
        assert result == {"loaded": False, "transitions": []}

    @pytest.mark.asyncio
    async def test_outline_transitions_returns_history_when_initialized(self):
        """大纲已激活时 → 返回 get_transitions() 内容"""
        decider = make_decider(config={"type": "amaidesu"})
        outline = _make_outline("a", "b")
        state = OutlineState()
        state.start(outline, now_ms=1000)
        state.skip(now_ms=2000)
        decider._outline_state = state

        result = await decider.outline_transitions()
        assert result["loaded"] is True
        transitions = result["transitions"]
        assert len(transitions) == 2
        assert transitions[0]["reason"] == "start"
        assert transitions[1]["reason"] == "manual:skip"
        assert transitions[1]["stayed_ms"] == 1000

    @pytest.mark.asyncio
    async def test_outline_transitions_handles_exception(self):
        """get_transitions() 抛异常时被吞, 返回 loaded=True + 空列表(避免 500)"""
        decider = make_decider(config={"type": "amaidesu"})
        state = MagicMock()
        state.get_transitions = MagicMock(side_effect=RuntimeError("oops"))
        decider._outline_state = state

        result = await decider.outline_transitions()
        # 异常被隔离, 返回 loaded=True + 空列表（dashboard 知道加载了但暂无数据）
        assert result == {"loaded": True, "transitions": []}


# ==================== outline_segments() 含 expanded_cache ====================


class TestOutlineSegmentsIncludesExpandedCache:
    """:meth:`AmaidesuDecider.outline_segments` 透传 expanded_cache"""

    @pytest.mark.asyncio
    async def test_outline_segments_returns_expanded_cache_when_loaded(self):
        """大纲已加载时 → 响应含 expanded_cache 字段"""
        decider = make_decider(config={"type": "amaidesu"})
        outline = _make_outline("a", "b")
        state = OutlineState()
        state.start(outline, now_ms=1000)
        # 注入一段 expanded cache
        state.cache_expanded(
            SimpleNamespace(
                segment_id="a",
                opening_line="A 开场",
                topic_guidance="A 引导",
                talking_points=["A1"],
            )
        )
        decider._outline_state = state
        decider._outline_loader_path = "outline.toml"

        result = await decider.outline_segments()
        assert result["loaded"] is True
        assert "expanded_cache" in result
        assert "a" in result["expanded_cache"]
        assert result["expanded_cache"]["a"].opening_line == "A 开场"

    @pytest.mark.asyncio
    async def test_outline_segments_empty_expanded_cache_when_unloaded(self):
        """大纲未加载时 → 响应含空 expanded_cache"""
        decider = make_decider(config={"type": "amaidesu"})
        # 默认 outline_state is None
        result = await decider.outline_segments()
        assert result["loaded"] is False
        assert result["segments"] == []
        assert result["expanded_cache"] == {}


# ==================== IntentMetadata 填充新字段 ====================


class TestIntentMetadataObservabilityFields:
    """amaidesu decider 发布 Intent 时填充 trigger_reason / outline_segment_id"""

    @pytest.mark.asyncio
    async def test_intent_metadata_includes_trigger_reason_from_batch(self):
        """弹幕批次触发：trigger_reason 来自 batch flush_reason（不强制开启大纲）"""
        decider = make_decider(
            config={"type": "amaidesu", "batch_window_ms": 0, "force_data_types": ["text"]},
            llm_responses=[
                make_llm_response(success=True, content=_planner_json(should_reply=True)),
                make_llm_response(
                    success=True, content=_replyer_json(text="好嘞", emotion="happy")
                ),
            ],
        )
        await decider.decide(make_message("hi"))
        await decider._maybe_flush()

        decider._event_bus.emit.assert_awaited_once()
        payload = decider._event_bus.emit.call_args[0][1]
        intent = payload.to_intent()
        # trigger_reason 来自 _maybe_flush 中的 flush_reason（此处为 'forced' 因为文本触发 force）
        assert intent.metadata.trigger_reason is not None
        assert intent.metadata.trigger_reason in ("forced", "window_expired", "batch_full", "idle_compensation")
        # 大纲未启用 → outline_segment_id 应为 None
        assert intent.metadata.outline_segment_id is None

    @pytest.mark.asyncio
    async def test_intent_metadata_includes_proactive_trigger_reason(self):
        """主动发言触发：trigger_reason 形如 proactive:cold / proactive:schedule"""
        decider = make_decider(
            config={"type": "amaidesu", "batch_window_ms": 0, "force_data_types": ["text"]},
            llm_responses=[
                make_llm_response(success=True, content=_planner_json(should_reply=True)),
                make_llm_response(
                    success=True, content=_replyer_json(text="（主动）", emotion="neutral")
                ),
            ],
        )
        # 模拟主动发言触发
        decider._external_proactive_pending = True
        # bypass buffer 内容的检查，让 proactive 走完
        decider._booted_for_test = True  # 标记（仅用于本测试断言）
        # 直接调 _make_two_stage_decision 模拟 proactive 路径
        await decider._make_two_stage_decision(
            batch=[],
            forced=False,
            trigger_reason="proactive:cold",
            proactive=True,
        )

        decider._event_bus.emit.assert_awaited_once()
        payload = decider._event_bus.emit.call_args[0][1]
        intent = payload.to_intent()
        assert intent.metadata.trigger_reason == "proactive:cold"
        assert intent.metadata.outline_segment_id is None

    @pytest.mark.asyncio
    async def test_intent_metadata_outline_segment_id_when_active(self):
        """大纲激活时：outline_segment_id 等于当前 current_segment_id"""
        decider = make_decider(
            config={"type": "amaidesu", "batch_window_ms": 0, "force_data_types": ["text"]},
            llm_responses=[
                make_llm_response(success=True, content=_planner_json(should_reply=True)),
                make_llm_response(
                    success=True, content=_replyer_json(text="在大纲里", emotion="happy")
                ),
            ],
        )
        # 手动构造一个 outline_state 并 current_segment_id = "main"
        outline = _make_outline("intro", "main")
        state = OutlineState()
        state.start(outline, now_ms=1000)
        state.jump_to("main", now_ms=2000)
        # current_segment_id = "main"
        decider._outline_state = state

        await decider._make_two_stage_decision(
            batch=[make_message("hi")],
            forced=True,
            trigger_reason="forced",
            proactive=False,
        )

        decider._event_bus.emit.assert_awaited_once()
        payload = decider._event_bus.emit.call_args[0][1]
        intent = payload.to_intent()
        assert intent.metadata.trigger_reason == "forced"
        assert intent.metadata.outline_segment_id == "main"

    @pytest.mark.asyncio
    async def test_intent_metadata_outline_segment_id_none_when_completed(self):
        """大纲已完成（current_segment_id=None）→ outline_segment_id=None"""
        decider = make_decider(
            config={"type": "amaidesu", "batch_window_ms": 0, "force_data_types": ["text"]},
            llm_responses=[
                make_llm_response(success=True, content=_planner_json(should_reply=True)),
                make_llm_response(
                    success=True, content=_replyer_json(text="完成了", emotion="happy")
                ),
            ],
        )
        outline = _make_outline("intro")
        state = OutlineState()
        state.start(outline, now_ms=1000)
        state.skip(now_ms=2000)  # 末段 skip → current_segment_id=None
        decider._outline_state = state

        await decider._make_two_stage_decision(
            batch=[make_message("hi")],
            forced=True,
            trigger_reason="forced",
            proactive=False,
        )

        decider._event_bus.emit.assert_awaited_once()
        payload = decider._event_bus.emit.call_args[0][1]
        intent = payload.to_intent()
        # 此时 _is_outline_active() == False（status=COMPLETED）
        assert intent.metadata.outline_segment_id is None
