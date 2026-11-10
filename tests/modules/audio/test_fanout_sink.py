"""音频分接扇出（FanOutAudioSink）：同一份会话递给全部成员，单成员异常只记日志不传染。"""

from __future__ import annotations

import numpy as np

from src.modules.audio.sink import FanOutAudioSink
from tests.modules.audio.fakes import RecordingSink


def test_fanout_forwards_session_to_all_members() -> None:
    first, second = RecordingSink(), RecordingSink()
    fanout = FanOutAudioSink([first, second])
    chunk = np.zeros(10, dtype=np.float32)
    fanout.start("utt_1")
    fanout.feed(chunk, 24000)
    fanout.stop()
    assert first.calls == [("start", "utt_1"), ("feed", (chunk, 24000)), ("stop", None)]
    assert second.calls == first.calls


def test_fanout_isolates_member_exception() -> None:
    good, bad = RecordingSink(), RecordingSink(boom=True)
    fanout = FanOutAudioSink([bad, good])
    chunk = np.zeros(10, dtype=np.float32)
    fanout.start("utt_2")  # bad 抛异常，good 照常收到
    fanout.feed(chunk, 24000)
    fanout.stop()
    assert good.calls == [("start", "utt_2"), ("feed", (chunk, 24000)), ("stop", None)]


def test_fanout_empty_and_single() -> None:
    FanOutAudioSink([]).start()  # 空成员：不抛
    only = RecordingSink()
    FanOutAudioSink([only]).feed(np.zeros(4, dtype=np.float32), 24000)
    assert len(only.fed_chunks()) == 1
