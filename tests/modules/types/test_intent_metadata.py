"""IntentMetadata 新增字段的单元测试（T12 调试可观察性）。

覆盖：
- 新字段 ``trigger_reason`` / ``outline_segment_id`` 默认 None
- 显式设置后保留
- extra="forbid" 不影响新字段（新增显式字段非未知字段）
- IntentMetadata.model_dump() 完整序列化
- IntentMetadata.model_validate() 接受新字段
"""

from __future__ import annotations

import pytest

from src.modules.types.intent import IntentMetadata


class TestIntentMetadataNewFields:
    """IntentMetadata 新增 trigger_reason / outline_segment_id"""

    def test_default_values_are_none(self):
        """不显式提供时,新字段默认为 None"""
        meta = IntentMetadata(source_id="test", decision_time_ms=1700000000000)
        assert meta.trigger_reason is None
        assert meta.outline_segment_id is None

    def test_explicit_trigger_reason_string(self):
        meta = IntentMetadata(
            source_id="amaidesu",
            decision_time_ms=1700000000000,
            trigger_reason="forced",
        )
        assert meta.trigger_reason == "forced"

    def test_explicit_outline_segment_id(self):
        meta = IntentMetadata(
            source_id="amaidesu",
            decision_time_ms=1700000000000,
            outline_segment_id="intro",
        )
        assert meta.outline_segment_id == "intro"

    def test_both_fields_set(self):
        meta = IntentMetadata(
            source_id="amaidesu",
            decision_time_ms=1700000000000,
            trigger_reason="proactive:outline",
            outline_segment_id="chapter-2",
        )
        assert meta.trigger_reason == "proactive:outline"
        assert meta.outline_segment_id == "chapter-2"

    def test_extra_forbid_still_works(self):
        """extra="forbid" 仍然对未知字段严格拒绝,但不影响新增显式字段"""
        # 已知字段 OK
        meta = IntentMetadata(
            source_id="x",
            decision_time_ms=1,
            trigger_reason="forced",
            outline_segment_id="seg",
        )
        assert meta.trigger_reason == "forced"

        # 未知字段仍应被 forbid
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            IntentMetadata(
                source_id="x",
                decision_time_ms=1,
                unknown_field="should_fail",
            )

    def test_extra_forbid_does_not_reject_new_fields(self):
        """新增 trigger_reason / outline_segment_id 字段不被 extra="forbid" 拒绝"""
        from pydantic import ValidationError

        # 验证 Pydantic 接受这些字段名（不会因 extra="forbid" 报错）
        try:
            IntentMetadata(
                source_id="x",
                decision_time_ms=1,
                trigger_reason="r",
                outline_segment_id="s",
            )
        except ValidationError as e:
            pytest.fail(
                f"IntentMetadata 应接受 trigger_reason / outline_segment_id,实际报错: {e}"
            )

    def test_model_dump_includes_new_fields(self):
        meta = IntentMetadata(
            source_id="amaidesu",
            decision_time_ms=1700000000000,
            trigger_reason="window_expired",
            outline_segment_id="chapter-1",
        )
        data = meta.model_dump()
        assert data["trigger_reason"] == "window_expired"
        assert data["outline_segment_id"] == "chapter-1"
        assert data["source_id"] == "amaidesu"
        assert data["decision_time_ms"] == 1700000000000

    def test_model_validate_with_new_fields(self):
        """从 dict 反序列化可接受新字段"""
        data = {
            "source_id": "amaidesu",
            "decision_time_ms": 1700000000000,
            "trigger_reason": "proactive:cold",
            "outline_segment_id": "chapter-3",
            "intent_id": "abcd1234",
        }
        meta = IntentMetadata.model_validate(data)
        assert meta.trigger_reason == "proactive:cold"
        assert meta.outline_segment_id == "chapter-3"
        assert meta.intent_id == "abcd1234"

    def test_model_validate_without_new_fields(self):
        """从 dict 反序列化,无新字段时默认为 None"""
        data = {"source_id": "amaidesu", "decision_time_ms": 1700000000000}
        meta = IntentMetadata.model_validate(data)
        assert meta.trigger_reason is None
        assert meta.outline_segment_id is None

    def test_reassign_after_creation(self):
        """构造后修改字段（与现有 source_message_id 模式一致）"""
        meta = IntentMetadata(source_id="x", decision_time_ms=1)
        assert meta.trigger_reason is None
        meta.trigger_reason = "forced"
        meta.outline_segment_id = "intro"
        assert meta.trigger_reason == "forced"
        assert meta.outline_segment_id == "intro"

    def test_intent_id_unaffected_by_new_fields(self):
        """intent_id 自动生成仍工作"""
        meta1 = IntentMetadata(source_id="x", decision_time_ms=1)
        meta2 = IntentMetadata(source_id="x", decision_time_ms=1)
        assert meta1.intent_id != meta2.intent_id
        assert len(meta1.intent_id) == 8
