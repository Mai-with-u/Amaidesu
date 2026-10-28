"""TimingGate 强制触发判定单元测试。

TimingGate 当前职责仅为按 message_type 判定单条消息的强制触发（is_forced），
条数阈值与时间窗判定已归属 MessageBuffer（见 test_message_buffer.py）。
测试使用 RoomMessagePayload 的 Literal 枚举：danmaku / gift / super_chat / guard / enter / partner_speech。
"""

from __future__ import annotations

from typing import List

from src.agents.streamer.timing_gate import TimingGate
from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser


def _msg(message_type: str, content: str = "hi", message_id: str = "m1") -> RoomMessagePayload:
    return RoomMessagePayload(
        message_type=message_type,
        user=RoomMessageUser(id="u1", name="小明"),
        content=content,
        message_id=message_id,
    )


def _gate(types: List[str]) -> TimingGate:
    return TimingGate(force_message_types=types)


def test_forced_type_hits() -> None:
    """强制集合内的类型立即判为强制。"""
    gate = _gate(["super_chat", "gift"])
    assert gate.is_forced(_msg("super_chat")) is True
    assert gate.is_forced(_msg("gift")) is True


def test_non_forced_type_misses() -> None:
    """强制集合外的类型（普通弹幕 / 进场 / 结束语）不触发。"""
    gate = _gate(["super_chat", "gift"])
    assert gate.is_forced(_msg("danmaku")) is False
    assert gate.is_forced(_msg("enter")) is False
    assert gate.is_forced(_msg("partner_speech")) is False


def test_empty_force_set_never_forces() -> None:
    """强制集合为空时任何类型都不触发（配置关闭强制通道）。"""
    gate = _gate([])
    assert gate.is_forced(_msg("super_chat")) is False
    assert gate.is_forced(_msg("danmaku")) is False


def test_default_config_types_force() -> None:
    """默认配置集合（super_chat / gift）在合法枚举内逐个命中。"""
    gate = _gate(["super_chat", "guard", "gift"])
    assert gate.is_forced(_msg("super_chat")) is True
    assert gate.is_forced(_msg("gift")) is True
    # "guard" 同为 Literal 合法值，且属于付费强制集合
    assert gate.is_forced(_msg("danmaku")) is False
