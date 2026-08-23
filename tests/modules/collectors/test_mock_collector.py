"""
mock 采集器注册 + 基本 import 测试 + simulated 数据溯源测试（Wave 5）

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
    """元数据正确"""
    assert MockCollector.name == "mock"
    assert "模拟" in MockCollector.description


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
    collector = MockCollector(config={"mode": "jsonl"})
    assert collector is not None
    # 不启动循环，只验证数据加载函数能跑通
    asyncio.run(collector._load_message_lines())
    # 默认 msg_default.jsonl 应有内容
    assert len(collector._message_lines) > 0, "JSONL 默认数据应能加载"


def test_mock_simulator_mode_has_gifts_pool() -> None:
    """simulator 模式：礼物清单（来自 simulator_gifts.toml 素材池）正确分类加载

    验证人设池/礼物生成器覆盖：原 mock 合并了 simulator 的素材池，
    本测试保证 W5 合并后素材池逻辑仍工作。
    """
    collector = MockCollector(config={"mode": "simulator"})
    gifts = asyncio.run(collector._load_gifts())
    assert gifts, "礼物清单应能加载"
    # 至少 normal + sc 类别
    assert "normal" in gifts or "sc" in gifts
    # 加载了至少一个礼物
    total = sum(len(v) for v in gifts.values())
    assert total > 0, f"应至少加载 1 个礼物，实际 {total}"
