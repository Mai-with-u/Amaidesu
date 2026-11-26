"""AI 选段：按行号定起止与收边、真实播放时段优先于估算、空结果与重试语义、合并记录的取料与渲染。"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

import pytest

from src.modules.clipping.config_schema import ClippingConfigSchema
from src.modules.clipping.selector import (
    ClipSelectionError,
    ClipSelector,
    MaterialLine,
    _extract_json_array,
    _suggestions_of,
)
from src.modules.diagnostics.tts_reader import TtsDiagnosticsReader
from src.modules.diagnostics.writer import DiagnosticsWriter
from src.modules.llm.payload import Response
from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.models.sessions import LiveSession
from tests.modules.diagnostics.helpers import append_tts_record
from tests.modules.llm.fakes import FakeLLMManager

_SESSION_START = 1_000_000
_SESSION = LiveSession(
    id=1,
    started_at_ms=_SESSION_START,
    ended_at_ms=_SESSION_START + 3600_000,
    updated_at_ms=_SESSION_START + 3600_000,
)

# 一行记录在观众面前的时间形态：(出声或显示前的延迟, 停留时长, 延迟是否来自真实记录, 观众是否看得到听得到)。
# 一句有真实播放记录的台词：落库后 3.5 秒出声、说 2.2 秒
_SPEECH = (3500, 2222, True, True)
# 一句没有真实播放记录、只能估算的台词：出声延迟按未知处理
_SPEECH_ESTIMATED = (3500, 2222, False, True)
# 一行游戏公屏：落库即显示、停 10 秒
_TEXT = (0, 10_000, True, True)
# 一行游戏事件：观众看不到
_EVENT = (0, 0, True, False)

_Spec = Tuple[int, Tuple[int, int, bool, bool]]

_BAD_ANCHOR = '{"first_line": 1, "last_line": 99, "title": "越界", "reason": "行号超出记录"}'


def _item(first: int, last: int, title: str) -> str:
    return f'{{"first_line": {first}, "last_line": {last}, "title": "{title}", "reason": "经过"}}'


def _payload(*items: str) -> str:
    return f"[{', '.join(items)}]"


def _lines(*specs: _Spec) -> List[MaterialLine]:
    """按 (自开播秒数, 时间形态) 造一串记录行，行号从 1 起顺排。"""
    return [
        MaterialLine(
            index=index,
            timestamp_ms=_SESSION_START + second * 1000,
            text="一行",
            onset_ms=onset,
            hold_ms=hold,
            onset_known=onset_known,
            visible=visible,
        )
        for index, (second, (onset, hold, onset_known, visible)) in enumerate(specs, start=1)
    ]


def _speech(second: int, hold: int = _SPEECH[1]) -> _Spec:
    return (second, (_SPEECH[0], hold, _SPEECH[2], _SPEECH[3]))


def _text(second: int) -> _Spec:
    return (second, _TEXT)


def _event(second: int) -> _Spec:
    return (second, _EVENT)


def test_extract_json_array_tolerates_fences_and_chatter() -> None:
    assert _extract_json_array('```json\n[{"a": 1}]\n```') == '[{"a": 1}]'
    assert _extract_json_array("好的，如下：[1, 2] 请查收") == "[1, 2]"
    with pytest.raises(ClipSelectionError, match="JSON 数组"):
        _extract_json_array("没有数组")


def test_cut_runs_from_just_before_the_first_line_to_after_the_last_line() -> None:
    lines = _lines(_speech(60), _speech(90))

    (only,) = _suggestions_of(
        _payload(_item(1, 2, "标题")), session=_SESSION, lines=lines, min_ms=30_000, max_ms=300_000
    )

    # 起点：首行出声前 2 秒；终点：末行出声前 3.5 秒 + 说 2.2 秒 + 余量 2 秒
    assert (only.start_ms, only.end_ms) == (_SESSION_START + 61_500, _SESSION_START + 90_000 + 3500 + 2222 + 2000)
    assert (only.title, only.reason) == ("标题", "经过")


@pytest.mark.parametrize(
    ("next_line", "expected_end_second_ms"),
    [
        (_speech(66), 66_000 + 3500),  # 下一行是台词：让到它出声那一刻
        (_text(66), 67_500),  # 下一行是画面上的字：本该它一出现就收，但末行要到 67.5 秒才说完，以说完为先
        (_event(66), 69_500),  # 下一行观众看不到：不拦收尾，说完再留余量
    ],
)
def test_end_yields_to_the_next_visible_line_but_lets_the_last_line_finish(
    next_line: _Spec, expected_end_second_ms: int
) -> None:
    # 首行 60 秒落库、63.5 秒出声、说 4 秒到 67.5 秒，说完再留余量是 69.5 秒，会压到 66 秒的下一行
    lines = _lines(_speech(60, hold=4000), next_line, _speech(300))

    (only,) = _suggestions_of(_payload(_item(1, 1, "标题")), session=_SESSION, lines=lines, min_ms=1000, max_ms=300_000)

    assert only.end_ms == _SESSION_START + expected_end_second_ms


def test_start_does_not_open_on_the_previous_line() -> None:
    # 上一行 50 秒落库、53.5 秒出声、说 9 秒：62.5 秒才说完，首行前 2 秒（61.5 秒）还响着它的尾音
    lines = _lines(_speech(50, hold=9000), _speech(60), _speech(300))

    (only,) = _suggestions_of(
        _payload(_item(2, 3, "标题")), session=_SESSION, lines=lines, min_ms=30_000, max_ms=300_000
    )

    assert only.start_ms == _SESSION_START + 62_500


def test_line_without_playback_record_starts_just_before_its_timestamp() -> None:
    # 出声延迟未知：不能按估算的延迟往后挪起点（真出声可能早得多），只提前 1 秒
    lines = _lines(_text(40), (60, _SPEECH_ESTIMATED), _speech(300))

    (only,) = _suggestions_of(
        _payload(_item(2, 3, "标题")), session=_SESSION, lines=lines, min_ms=30_000, max_ms=300_000
    )

    assert only.start_ms == _SESSION_START + 59_000


def test_over_cap_keeps_the_result_and_moves_start_to_a_line() -> None:
    lines = _lines(_speech(0), _speech(600), _speech(1200), _speech(1700), _speech(1800))

    (only,) = _suggestions_of(
        _payload(_item(1, 5, "标题")), session=_SESSION, lines=lines, min_ms=30_000, max_ms=900_000
    )

    # 末行（结果）不动；起点挪到仍放得下的第一行
    assert only.end_ms == _SESSION_START + 1800_000 + 3500 + 2222 + 2000
    assert only.start_ms == _SESSION_START + 1201_500
    assert only.end_ms - only.start_ms <= 900_000


def test_short_and_overlapping_proposals_are_dropped() -> None:
    lines = _lines(_speech(60), _speech(90), _speech(110), _speech(300))
    payload = _payload(_item(1, 2, "甲"), _item(2, 3, "乙"), _item(3, 3, "丙"))

    suggestions = _suggestions_of(payload, session=_SESSION, lines=lines, min_ms=30_000, max_ms=300_000)

    # 乙与甲重叠，挪到甲之后只剩 20 秒；丙只有一行 6 秒
    assert [s.title for s in suggestions] == ["甲"]


def test_overlapping_proposal_is_trimmed_to_after_the_kept_one() -> None:
    lines = _lines(_speech(60), _speech(90), _speech(300))
    payload = _payload(_item(1, 2, "甲"), _item(2, 3, "乙"))

    suggestions = _suggestions_of(payload, session=_SESSION, lines=lines, min_ms=30_000, max_ms=300_000)

    first, second = suggestions
    assert (first.title, second.title) == ("甲", "乙")
    assert second.start_ms == first.end_ms


def test_empty_array_means_nothing_worth_clipping() -> None:
    lines = _lines(_speech(60), _speech(90))

    assert _suggestions_of("[]", session=_SESSION, lines=lines, min_ms=30_000, max_ms=300_000) == []


def test_keeps_at_most_three_and_returns_them_in_time_order() -> None:
    lines = _lines(_speech(60), _speech(300), _speech(600), _speech(900))
    # 模型按值得剪的先后给出四件；第四件超出件数上限
    payload = _payload(_item(3, 3, "丙"), _item(1, 1, "甲"), _item(2, 2, "乙"), _item(4, 4, "丁"))

    suggestions = _suggestions_of(payload, session=_SESSION, lines=lines, min_ms=1000, max_ms=300_000)

    assert [s.title for s in suggestions] == ["甲", "乙", "丙"]


def test_rejects_outputs_that_are_not_a_json_array() -> None:
    lines = _lines(_speech(60))
    with pytest.raises(ClipSelectionError, match="不是合法 JSON"):
        _suggestions_of("[{first_line: 1}]", session=_SESSION, lines=lines, min_ms=1000, max_ms=60_000)
    with pytest.raises(ClipSelectionError, match="找不到 JSON 数组"):
        _suggestions_of('{"a": 1}', session=_SESSION, lines=lines, min_ms=1000, max_ms=60_000)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (_payload('{"last_line": 2, "title": "t"}'), "缺 first_line"),
        (_payload('{"first_line": 3, "last_line": 2, "title": "t"}'), "晚于 last_line"),
        (_payload('{"first_line": 0, "last_line": 2, "title": "t"}'), "行号范围"),
        (_payload('{"first_line": 1, "last_line": 99, "title": "t"}'), "行号范围"),
        (_payload('{"first_line": "第三行", "last_line": 3, "title": "t"}'), "不是行号"),
    ],
)
def test_unusable_anchors_are_rejected(payload: str, message: str) -> None:
    lines = _lines(_speech(60), _speech(90), _speech(120))

    with pytest.raises(ClipSelectionError, match=message):
        _suggestions_of(payload, session=_SESSION, lines=lines, min_ms=1000, max_ms=300_000)


def _selector(llm: FakeLLMManager) -> ClipSelector:
    """只测 _ask 时仓储用不到，传占位。"""
    return ClipSelector(
        llm,  # type: ignore[arg-type]
        chat_repo=None,  # type: ignore[arg-type]
        event_repo=None,  # type: ignore[arg-type]
        session_repo=None,  # type: ignore[arg-type]
        topic_repo=None,  # type: ignore[arg-type]
        config=ClippingConfigSchema(),
    )


def _wired_selector(
    llm: FakeLLMManager,
    store: SQLiteDatabase,
    *,
    voice_log: Optional[TtsDiagnosticsReader] = None,
) -> ClipSelector:
    return ClipSelector(
        llm,  # type: ignore[arg-type]
        chat_repo=store.chat,
        event_repo=store.events,
        session_repo=store.sessions,
        topic_repo=store.topics,
        game_player_repo=store.game_players,
        config=ClippingConfigSchema(min_segment_seconds=5),
        voice_log=voice_log,
    )


_LINES = _lines(_speech(60), _speech(90))
_CONSTRAINTS = {"min_ms": 30_000, "max_ms": 300_000}


@pytest.mark.parametrize("first_output", ["瞎写的", _BAD_ANCHOR])
async def test_ask_retries_once_on_unusable_output(first_output: str) -> None:
    # 不是 JSON、行号越界都算不可用，各重试一次为限
    llm = FakeLLMManager(
        responses=[
            Response(success=True, content=first_output),
            Response(success=True, content=_payload(_item(1, 2, "标题"))),
        ]
    )

    result = await _selector(llm)._ask(
        "素材", system="s", profile="settle", session=_SESSION, lines=_LINES, **_CONSTRAINTS
    )

    assert [s.title for s in result] == ["标题"]
    assert len(llm.calls) == 2


async def test_ask_gives_up_after_two_failures() -> None:
    llm = FakeLLMManager(responses=[Response(success=True, content="x"), Response(success=True, content=_BAD_ANCHOR)])
    with pytest.raises(ClipSelectionError, match="两次都无法采用"):
        await _selector(llm)._ask("素材", system="s", profile="settle", session=_SESSION, lines=_LINES, **_CONSTRAINTS)
    assert len(llm.calls) == 2


async def _seed_record(store: SQLiteDatabase) -> Tuple[int, List[int]]:
    """造一场：三句台词（一句没送到）、三条游戏公屏（一条认不出是谁说的）、三条游戏事件（一条是生存打断）。

    返回场次主键与各台词行的主键（按落库先后）。
    """
    session_id = await store.sessions.insert_live_session(started_at_ms=_SESSION_START, source="manual")
    await store.sessions.close_live_session(live_session_id=session_id, ended_at_ms=_SESSION_START + 3600_000)

    def at(second: int) -> int:
        return _SESSION_START + second * 1000

    speech_rows = (
        (90, "第二句台词", "happy", 0.6, None, "utt_second"),
        (30, "第一句台词", "neutral", 0.5, None, "utt_first"),
        (60, "没送到观众面前的一句", "neutral", 0.5, at(61), None),
        (150, "吓了一跳的台词", "surprised", 0.7, None, None),
    )
    speech_ids: List[int] = []
    for second, content, emotion, intensity, dropped_at_ms, utterance_id in speech_rows:
        speech_ids.append(
            await store.chat.insert_live_chat(
                live_session_id=session_id,
                timestamp_ms=at(second),
                sender_role="assistant",
                content=content,
                message_type="text",
                emotion=emotion,
                emotion_intensity=intensity,
                dropped_at_ms=dropped_at_ms,
                utterance_id=utterance_id,
            )
        )
    sheep = await store.game_players.meet_player(
        game="minecraft", uuid="sheep-uuid", name="阿羊", live_session_id=session_id, at_ms=at(45)
    )
    await store.game_players.add_game_chat(
        live_session_id=session_id,
        game="minecraft",
        direction="player",
        content="需要帮忙吗",
        player_id=sheep.id,
        at_ms=at(45),
    )
    await store.game_players.add_game_chat(
        live_session_id=session_id, game="minecraft", direction="streamer", content="我自己来", at_ms=at(50)
    )
    await store.game_players.add_game_chat(
        live_session_id=session_id, game="minecraft", direction="other", content="认不出是谁说的", at_ms=at(55)
    )
    for second, event_type, message in (
        (55, "milestone", "台阶搭好了"),
        (70, "milestone", "按图建好了"),
        (75, "attention_required", "生存需求「饥饿」插进临时任务"),
    ):
        await store.events.insert_game_event(
            live_session_id=session_id,
            game="minecraft",
            event_type=event_type,
            message=message,
            timestamp_ms=at(second),
        )
    return session_id, speech_ids


_RECORD_ROWS = [
    "#1 [0:30] 她说：第一句台词",
    "#2 [0:45] 玩家 阿羊：需要帮忙吗",
    "#3 [0:50] 她打字：我自己来",
    "#4 [0:55] 游戏事件：台阶搭好了",
    "#5 [1:10] 游戏事件：按图建好了",
    "#6 [1:30] 她说（开心）：第二句台词",
    "#7 [2:30] 她说（惊讶，情绪很强）：吓了一跳的台词",
]


async def test_select_reads_the_whole_record_and_cuts_on_lines(store: SQLiteDatabase) -> None:
    """取料合并成按时间排序的带行号记录：游戏公屏、全程游戏事件、情绪标签都在，没送到的话与生存打断不在。"""
    session_id, _ = await _seed_record(store)
    llm = FakeLLMManager(response=Response(success=True, content=_payload(_item(2, 3, "开工"), _item(6, 6, "收尾"))))
    selector = _wired_selector(llm, store)

    suggestions = await selector.select(live_session_id=session_id)

    material = llm.calls[0]["input"]
    assert [row for row in material.splitlines() if row.startswith("#")] == _RECORD_ROWS
    for placeholder in ("{min_s}", "{max_s}", "{max_segments}"):
        assert placeholder not in llm.calls[0]["system"]

    def at(second: int) -> int:
        return _SESSION_START + second * 1000

    opened, closed = suggestions
    # 末行是一行游戏公屏：显示 10 秒后留 2 秒余量；紧随其后的游戏事件观众看不到，不把它截短
    assert (opened.title, opened.start_ms, opened.end_ms) == ("开工", at(45) - 2000, at(50) + 12_000)
    # 末行是没有播放记录的台词：出声延迟未知，起点只提前 1 秒；终点按估算的延迟与说话时长往后留
    assert (closed.title, closed.start_ms, closed.end_ms) == (
        "收尾",
        at(90) - 1000,
        at(90) + 6500 + 3000 + 2000,
    )


async def test_select_uses_real_playback_windows_when_records_exist(store: SQLiteDatabase, tmp_path: Path) -> None:
    """有 TTS 诊断记录的台词按真实出声与播完时刻定起止；没记录（当时没播成）的仍按估算。"""
    session_id, speech_ids = await _seed_record(store)
    second_id, first_id = speech_ids[0], speech_ids[1]
    assert await store.chat.list_session_speech_utterance_ids(live_session_id=session_id) == {
        second_id: "utt_second",
        first_id: "utt_first",
    }
    writer = DiagnosticsWriter(base_dir=tmp_path, retention_days=0)

    def at(second: int) -> int:
        return _SESSION_START + second * 1000

    append_tts_record(
        writer,
        event="tts.utterance.finished",
        utterance_id="utt_second",
        live_session_id=session_id,
        timestamp_ms=at(99),
        duration_ms=7000,
    )
    append_tts_record(
        writer,
        event="tts.utterance.failed",
        utterance_id="utt_first",
        live_session_id=session_id,
        timestamp_ms=at(31),
        error_message="合成失败",
    )
    llm = FakeLLMManager(response=Response(success=True, content=_payload(_item(6, 6, "收尾"), _item(1, 1, "开场"))))
    selector = _wired_selector(llm, store, voice_log=TtsDiagnosticsReader(writer))

    opened, closed = await selector.select(live_session_id=session_id)

    # 第一句没播成、没有播放时段：仍按估算
    assert (opened.title, opened.start_ms, opened.end_ms) == (
        "开场",
        at(30) - 1000,
        at(30) + 6500 + 3000 + 2000,
    )
    # 第二句 92 秒出声、99 秒播完：起点在出声前 2 秒，终点在播完后 2 秒
    assert (closed.title, closed.start_ms, closed.end_ms) == ("收尾", at(90), at(101))


async def test_select_without_any_record_skips_the_model(store: SQLiteDatabase) -> None:
    """一场里没有任何带时刻的记录：无从选起，不调模型，按没有值得剪的处理。"""
    session_id = await store.sessions.insert_live_session(started_at_ms=_SESSION_START, source="manual")
    await store.sessions.close_live_session(live_session_id=session_id, ended_at_ms=_SESSION_START + 600_000)
    llm = FakeLLMManager()
    selector = _wired_selector(llm, store)

    assert await selector.select(live_session_id=session_id) == []
    assert llm.calls == []
