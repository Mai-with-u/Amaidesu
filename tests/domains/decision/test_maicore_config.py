"""测试 MaiCoreDecisionProvider 配置 Schema"""

import pytest

from src.domains.decision.providers.maicore.maicore_decision_provider import MaiCoreDecisionProvider


def test_maicore_action_suggestions_config():
    """测试 Action 建议配置字段"""
    schema = MaiCoreDecisionProvider.ConfigSchema

    # 验证字段存在
    assert "action_suggestions_enabled" in schema.model_fields
    assert "action_confidence_threshold" in schema.model_fields
    assert "action_cooldown_seconds" in schema.model_fields
    assert "max_suggested_actions" in schema.model_fields

    # 验证默认值
    default_instance = schema()
    assert default_instance.action_suggestions_enabled is False
    assert default_instance.action_confidence_threshold == 0.6
    assert default_instance.action_cooldown_seconds == 5.0
    assert default_instance.max_suggested_actions == 3


def test_maicore_config_validation():
    """测试配置验证"""
    # 有效配置
    valid_config = MaiCoreDecisionProvider.ConfigSchema(
        action_suggestions_enabled=True,
        action_confidence_threshold=0.7,
        action_cooldown_seconds=3.0,
        max_suggested_actions=5,
    )
    assert valid_config.action_suggestions_enabled is True

    # 无效配置（超出范围）
    with pytest.raises(ValueError):  # Pydantic 验证失败会抛出 ValidationError
        MaiCoreDecisionProvider.ConfigSchema(
            action_confidence_threshold=1.5,  # 超出范围
        )
