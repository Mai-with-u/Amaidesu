"""force_data_types 运行时校验测试。

``AmaidesuDecider.__init__`` 校验 ``force_data_types`` 每个值必须在 message_type
登记表内——非法值启动即抛 ``MessageTypeNotRegistered``（不让拼写错误静默失效）。
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.modules.types.message_type import MessageTypeNotRegistered
from src.stages.decision.deciders.amaidesu.amaidesu_decider import AmaidesuDecider


def _make_decider(config: dict) -> AmaidesuDecider:
    """构造测试用 AmaidesuDecider（校验发生在 __init__，无需完整依赖）。"""
    event_bus = MagicMock()
    event_bus.emit = AsyncMock()

    llm_service = MagicMock()
    llm_service.chat = AsyncMock()

    prompt_service = MagicMock()

    return AmaidesuDecider(
        config=config,
        event_bus=event_bus,
        llm_service=llm_service,
        prompt_service=prompt_service,
    )


def test_invalid_force_data_type_raises():
    with pytest.raises(MessageTypeNotRegistered) as exc_info:
        _make_decider({"type": "amaidesu", "force_data_types": ["superchat"], "batch_window_ms": 0})
    assert "superchat" in str(exc_info.value)


def test_mixed_valid_invalid_force_data_types_raises():
    with pytest.raises(MessageTypeNotRegistered) as exc_info:
        _make_decider(
            {"type": "amaidesu", "force_data_types": ["gift", "not_a_type", "guard"], "batch_window_ms": 0}
        )
    assert "not_a_type" in str(exc_info.value)


def test_valid_force_data_types_constructs():
    decider = _make_decider({"type": "amaidesu", "force_data_types": ["super_chat", "guard", "gift"], "batch_window_ms": 0})
    assert decider.typed_config.force_data_types == ["super_chat", "guard", "gift"]


def test_empty_force_data_types_constructs():
    decider = _make_decider({"type": "amaidesu", "force_data_types": [], "batch_window_ms": 0})
    assert decider.typed_config.force_data_types == []
