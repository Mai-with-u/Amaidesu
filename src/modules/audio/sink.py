"""音频分接协议（中性，无 avatar / TTS 平台知识）

TTS 播放路径上"复制一份音频"的接收方契约：播放器在开播/播完时
**同步**调用 ``start`` / ``stop``，每个音频块经 ``feed`` 递入。这是
帧级音频通道的预留接口（任何音频消费者——口型分析、电平表、录制——
都走它），协议本身对消费目的保持无知。

设计约束：

- **同步接口**——``feed`` 只允许追加缓冲、立即返回（非阻塞，不得拖慢
  播放写盘）；分析与后续处理由接收方自己的后台循环承担。
- **中性**——协议与实现位次分离：实现方（如口型分析器）住在自己的
  模块，经构造链（装配入口 → 引擎工厂 → 引擎 → 播放器）注入，TTS 侧
  不依赖任何具体消费者。
- **fail-soft 责任在播放器**——播放器对所有 sink 调用做异常兜底，
  口型等装饰性路径的故障不得影响播放。
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, List, Protocol, runtime_checkable

import numpy as np

from src.modules.logging import get_logger


@runtime_checkable
class AudioSink(Protocol):
    """音频分接接收方协议（结构类型，``@runtime_checkable``）。

    一次发言 = 一次 ``start → feed×N → stop``；停止后接收方自行收尾
    （如口型嘴部回到静止值）。
    """

    def start(self, utterance_id: str = "") -> None:
        """会话开始（播放器开播时同步调用）。"""
        ...

    def feed(self, chunk: "np.ndarray", sample_rate: int) -> None:
        """递入一个音频块（一维数组，单声道；同步、非阻塞）。"""
        ...

    def stop(self) -> None:
        """会话结束（播放器播完时同步调用）。"""
        ...


class FanOutAudioSink:
    """把同一份音频分接扇出给多个接收方；单个接收方的异常只记日志不传染。

    播放器只挂一个 sink，而分接消费者可以有多个（口型分析、句间隙检测等）：
    本类实现 ``AudioSink`` 会话形状，把每次 ``start`` / ``feed`` / ``stop``
    依次递给全部成员——任一成员抛异常被就地捕获记日志，其余成员照常收到
    递入，播放本身不受影响。
    """

    def __init__(self, sinks: Iterable[Any], logger_name: str = "AudioSinkFanOut") -> None:
        self._sinks: List[Any] = list(sinks)
        self.logger = get_logger(logger_name)

    def _fan_out(self, method_name: str, call: Callable[[Any], None]) -> None:
        for sink in self._sinks:
            try:
                call(sink)
            except Exception as e:  # noqa: BLE001 - 装饰性路径边界
                self.logger.warning(f"音频分接 {method_name} 异常（已忽略）: sink={type(sink).__name__}, err={e}")

    def start(self, utterance_id: str = "") -> None:
        self._fan_out("start", lambda sink: sink.start(utterance_id))

    def feed(self, chunk: "np.ndarray", sample_rate: int) -> None:
        self._fan_out("feed", lambda sink: sink.feed(chunk, sample_rate))

    def stop(self) -> None:
        self._fan_out("stop", lambda sink: sink.stop())


__all__ = ["AudioSink", "FanOutAudioSink"]
