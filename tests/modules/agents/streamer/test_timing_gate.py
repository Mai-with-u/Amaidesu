"""TimingGate 强制触发判定单元测试。

TimingGate 当前职责仅为按 message_type 判定强制触发（is_forced / batch_is_forced），
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


def test_batch_contains_forced() -> None:
    """批内任一强制消息即整批强制。"""
    gate = _gate(["super_chat"])
    batch = [_msg("danmaku", message_id="m1"), _msg("super_chat", message_id="m2")]
    assert gate.batch_is_forced(batch) is True


def test_batch_all_non_forced() -> None:
    """批内全部为普通弹幕则不强制。"""
    gate = _gate(["super_chat", "gift"])
    batch = [_msg("danmaku", message_id="m1"), _msg("enter", message_id="m2")]
    assert gate.batch_is_forced(batch) is False


def test_batch_empty_is_not_forced() -> None:
    """空批不强制（any 短路语义）。"""
    gate = _gate(["super_chat"])
    assert gate.batch_is_forced([]) is False


def test_batch_single_forced_type() -> None:
    """单消息批退化为 is_forced 同一判定。"""
    gate = _gate(["gift"])
    assert gate.batch_is_forced([_msg("gift")]) is True
    assert gate.batch_is_forced([_msg("danmaku")]) is False
