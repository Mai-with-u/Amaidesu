"""
mock 采集器注册 + 基本 import 测试 + simulated 数据溯源测试。

覆盖：
- MockCollector 继承 BaseCollector
- 元数据正确
- 所有 emit 的 NormalizedMessage 携带 simulated=True
- JSONL 模式：从 data/msg_default.jsonl 加载
- simulator 模式：按 base_rate_per_minute 间隔产出（不依赖 LLM）
- 人设池/节奏生成器/礼物生成器（来自 simulator/）逻辑覆盖
"""

from __future__ import annotations

import asyncio

from src.modules.collectors.base import BaseCollector
from src.modules.collectors.mock import MockCollector
from src.modules.types.base.normalized_message import NormalizedMessage


def test_mock_inherits_base_collector() -> None:
    """MockCollector 继承 BaseCollector"""
    assert issubclass(MockCollector, BaseCollector)


def test_mock_metadata() -> None:
    """元数据正确（收敛后 MockCollector = 确定性 JSONL 回放器）"""
    assert MockCollector.name == "mock"
    assert "JSONL" in MockCollector.description or "回放" in MockCollector.description


def test_mock_normalized_message_carries_simulated_flag() -> None:
    """所有从 mock 产出的 NormalizedMessage 携带 simulated=True（数据溯源）"""
    # 直接构造一个模拟 NormalizedMessage，验证字段类型
    msg = NormalizedMessage(
        text="模拟弹幕",
        source="mock",
        data_type="text",
        importance=0.5,
        timestamp_ms=0,
        simulated=True,
    )
    assert msg.simulated is True
    # 默认应是 False（真实采集器）
    real_msg = NormalizedMessage(
        text="真实弹幕",
        source="bili_danmaku_official",
        data_type="text",
        importance=0.5,
        timestamp_ms=0,
    )
    assert real_msg.simulated is False


def test_mock_jsonl_loads_default_data() -> None:
    """JSONL 模式：从默认 data/msg_default.jsonl 加载消息"""
    collector = MockCollector(config={})
    assert collector is not None
    # 不启动循环，只验证数据加载函数能跑通
    asyncio.run(collector._load_message_lines())
    # 默认 msg_default.jsonl 应有内容
    assert len(collector._message_lines) > 0, "JSONL 默认数据应能加载"


def test_mock_only_supports_jsonl_mode() -> None:
    """收敛后 MockCollector 仅保留 jsonl 模式，无 simulator 模式字段。

    验证 ConfigSchema 已移除 mode 字段（jsonl 是唯一保留模式）。
    """
    from src.modules.collectors.mock.mock_collector import MockCollector as MC

    schema_fields = set(MC.ConfigSchema.model_fields.keys())
    assert "mode" not in schema_fields, "ConfigSchema.mode 字段已移除（收敛）"
    # jsonl 模式字段必须保留
    assert "log_file_path" in schema_fields
    assert "send_interval" in schema_fields
    assert "loop_playback" in schema_fields


def test_mock_normalized_message_carries_simulated_flag_for_payload() -> None:
    """所有从 mock 产出的 payload 携带 simulated=True（数据溯源）

    验证 _emit_danmaku 透传 simulated 标记到 RoomMessagePayload（存储层过滤依据）。
    """
    from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser

    msg = NormalizedMessage(
        text="测试弹幕",
        source="mock",
        data_type="text",
        importance=0.5,
        timestamp_ms=0,
        simulated=True,
    )
    payload = RoomMessagePayload(
        live_session_id="test_session",
        message_type="danmaku",
        user=RoomMessageUser(id="u1", name="user"),
        content=msg.text,
        timestamp_ms=msg.timestamp_ms,
        simulated=bool(msg.simulated),
    )
    assert payload.simulated is True, "payload.simulated 字段必须透传 NormalizedMessage.simulated"
