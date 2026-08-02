"""DecisionPlan 模型单元测试。

覆盖：
- 默认值构造
- 字段赋值
- `extra="forbid"` 拒绝未知字段（特别是已砍的 `wait_ms`）
- `version` 默认 "1.0"
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

# 预导入 config.schemas 以打破代码库既有的循环导入：
#   deciders/__init__ -> llm -> config.schemas.base -> config.schemas.__init__
#   -> decision_schemas -> llm_llm_decider (partially initialized)
# 仅在单独运行本测试文件时触发；完整测试套件因其他测试先导入而无此问题。
# 同样的限制存在于 test_amaidesu_decider.py。
import src.modules.config.schemas  # noqa: F401

from src.stages.decision.deciders.amaidesu.plan import DecisionPlan


class TestDecisionPlanDefaults:
    """默认值测试。"""

    def test_default_construction(self) -> None:
        """无参数构造，所有字段应为默认值。"""
        plan = DecisionPlan()
        assert plan.should_reply is False
        assert plan.target is None
        assert plan.topic_summary == ""
        assert plan.reply_guidance == ""
        assert plan.confidence == 0.0
        assert plan.version == "1.0"

    def test_version_defaults_to_1_0(self) -> None:
        """version 字段必须默认 "1.0"。"""
        plan = DecisionPlan()
        assert plan.version == "1.0"


class TestDecisionPlanAssignment:
    """字段赋值测试。"""

    def test_field_assignment(self) -> None:
        """所有字段都能被正确赋值。"""
        plan = DecisionPlan(
            should_reply=True,
            target="m1",
            topic_summary="打游戏",
            reply_guidance="回应",
            confidence=0.8,
        )
        assert plan.should_reply is True
        assert plan.target == "m1"
        assert plan.topic_summary == "打游戏"
        assert plan.reply_guidance == "回应"
        assert plan.confidence == pytest.approx(0.8)
        # version 未传，仍为默认
        assert plan.version == "1.0"

    def test_version_can_be_overridden(self) -> None:
        """version 可显式覆盖（未来 schema 迁移用）。"""
        plan = DecisionPlan(version="2.0")
        assert plan.version == "2.0"

    def test_model_dump_roundtrip(self) -> None:
        """model_dump() 返回的字典包含所有字段且语义正确。"""
        plan = DecisionPlan(should_reply=True, target="m1")
        dumped = plan.model_dump()
        assert dumped["should_reply"] is True
        assert dumped["target"] == "m1"
        assert dumped["version"] == "1.0"
        # 完整字段集
        assert set(dumped.keys()) == {
            "should_reply",
            "target",
            "topic_summary",
            "reply_guidance",
            "confidence",
            "version",
        }


class TestDecisionPlanExtraForbid:
    """`extra="forbid"` 严格性测试。"""

    def test_wait_ms_rejected(self) -> None:
        """wait_ms 已被砍，必须被 ValidationError 拒绝。"""
        with pytest.raises(ValidationError):
            DecisionPlan(wait_ms=100)

    def test_unknown_field_rejected(self) -> None:
        """任意未知字段都应被拒绝。"""
        with pytest.raises(ValidationError):
            DecisionPlan(unknown_field="xxx")

    def test_wait_ms_rejected_even_with_valid_fields(self) -> None:
        """即使其他字段合法，wait_ms 仍应被拒绝。"""
        with pytest.raises(ValidationError):
            DecisionPlan(should_reply=True, target="m1", wait_ms=100)
