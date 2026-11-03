"""主播时间线：只追加、按章压缩、前缀稳定、兜底、场次边界，以及章节层上限与前情目录。"""

from __future__ import annotations

import asyncio
import re
from dataclasses import replace
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer import canonical
from src.agents.streamer.config import StreamerContextConfig
from src.agents.streamer.plan import DecisionPlan
from src.agents.streamer.planner import Planner
from src.agents.streamer.replyer import Replyer
from src.agents.streamer.room_state import RoomState
from src.agents.streamer.timeline import (
    CHAPTER_MESSAGE_TYPE,
    GAME_MESSAGE_TYPE,
    StreamerTimeline,
    TimelineTurn,
    note_turn,
)
from src.modules.context import TokenCounter, resolve_watermarks
from src.modules.llm.payload import Response
from tests.agents.streamer.helpers import stub_prompt
from tests.modules.llm.fakes import FakeLLMManager

pytestmark = pytest.mark.usefixtures("chars_as_tokens")  # conftest 共享 fixture：按字符数计 token


def _turns(count: int, start: int = 1) -> List[TimelineTurn]:
    """观众与主播交替发言，每条约 100 字。"""
    turns = []
    for i in range(start, start + count):
        role = "viewer" if i % 2 else "assistant"
        turns.append(
            TimelineTurn(
                role=role,
                content=f"第{i}句" + "聊" * 100,
                sender_name="小明" if role == "viewer" else "",
                message_id=f"m{i}",
                timestamp_ms=1_791_000_000_000 + i * 60_000,
            )
        )
    return turns


def _timeline(generate: Any = None) -> StreamerTimeline:
    """预算 4000 token：提前整理线 2800、回落线 1600（窗口未配置，按上限计）。"""
    llm = MagicMock()
    llm.get_profile_context_window = MagicMock(return_value=0)
    llm.generate = generate or AsyncMock(
        return_value=Response(success=True, content="小明一直在聊天，主播答应下播前唱歌。")
    )
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="SYSTEM")
    return StreamerTimeline(
        budget=StreamerContextConfig(max_tokens=4000),
        llm_service=llm,
        prompt_service=prompt,
        profiles=("planner", "replyer"),
        summary_profile="summary",
    )


async def _settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


async def test_entries_append_in_first_seen_order_without_duplicates() -> None:
    """同一条记录跨窗只进一次；新记录追加在尾部，旧前缀不动。"""
    timeline = _timeline()
    turns = _turns(6)
    first = await timeline.project(turns)
    second = await timeline.project([*turns, *_turns(2, start=7)])
    assert [t.message_id for t in first] == [f"m{i}" for i in range(1, 7)]
    assert second[: len(first)] == first and len(second) == 8
    # 没有消息 ID 的调试对话按角色 + 时刻 + 正文识别，重复读不重复进
    loose = TimelineTurn(role="viewer", content="不落库的话", timestamp_ms=5)
    third = await timeline.project([*turns, *_turns(2, start=7), loose])
    fourth = await timeline.project([*turns, *_turns(2, start=7), loose])
    assert third == fourth and third[-1] is loose


async def test_chapter_replaces_oldest_segment_after_background_summary() -> None:
    """越过提前整理线后台写前情提要；写好后的下一窗换上，最近 20 条保持原文。"""
    timeline = _timeline()
    turns = _turns(30)
    before = await timeline.project(turns)
    assert len(before) == 30 and timeline._pending.busy
    await _settle()
    after = await timeline.project(turns)
    assert after[0].message_type == CHAPTER_MESSAGE_TYPE
    assert after[0].content.startswith("[前情提要·第 1 章") and "答应下播前唱歌" in after[0].content
    assert [t.message_id for t in after[1:]] == [f"m{i}" for i in range(11, 31)]
    # 下一章生成时把上一章交给模型衔接，记录里不再出现已压缩的条目
    request = timeline._llm.generate.call_args.args[0]
    assert "第1句" in request and "第11句" not in request


async def test_prefix_stays_stable_between_checkpoints() -> None:
    """换章之后只追加：下一窗的历史以上一窗的历史为前缀。"""
    timeline = _timeline()
    await timeline.project(_turns(30))
    await _settle()
    settled = await timeline.project(_turns(30))
    grown = await timeline.project([*_turns(30), *_turns(2, start=31)])
    assert grown[: len(settled)] == settled


async def test_hard_fold_when_summary_never_arrives() -> None:
    """摘要一直不回来、历史涨到预算两倍时，同步把最老一段换成占位说明，最近记录仍是原文。"""
    hang = asyncio.Event()

    async def slow(*_: Any, **__: Any) -> Response:
        await hang.wait()
        return Response(success=True, content="不会用到")

    timeline = _timeline(generate=slow)
    await timeline.project(_turns(30))
    view = await timeline.project(_turns(80))
    assert view[0].message_type == CHAPTER_MESSAGE_TYPE and "来不及整理" in view[0].content
    assert view[-1].message_id == "m80"
    await timeline.reset()


async def test_failed_summary_is_retried_later() -> None:
    """摘要失败不换章，下次越过提前整理线时重新发起。"""
    generate = AsyncMock(
        side_effect=[Response(success=False, error="超时"), Response(success=True, content="第二次成功")]
    )
    timeline = _timeline(generate=generate)
    await timeline.project(_turns(30))
    await _settle()
    retry = await timeline.project(_turns(30))
    assert retry[0].message_type != CHAPTER_MESSAGE_TYPE and timeline._pending.busy
    await _settle()
    done = await timeline.project(_turns(30))
    assert "第二次成功" in done[0].content


async def test_scope_change_restarts_timeline() -> None:
    """读到的场次变了（换场没收到开播事件）时从空白重新长，不把上一场的话带进来。"""
    timeline = _timeline()
    await timeline.project(_turns(5), scope=1)
    fresh = await timeline.project(_turns(2, start=101), scope=2)
    assert [t.message_id for t in fresh] == ["m101", "m102"]


def test_planner_maps_chapter_as_plain_user_message() -> None:
    """Planner 把前情提要当一条原样的 user 消息，不加昵称与消息 ID。"""
    planner = Planner(config=None, llm_service=MagicMock(), prompt_service=MagicMock(), room_state=RoomState())
    chapter = TimelineTurn(role="user", content="[前情提要·第 1 章]\n聊了天", message_type=CHAPTER_MESSAGE_TYPE)
    messages = planner._build_dialogue_messages([], [chapter, *_turns(1)])
    assert messages[0] == {"role": "user", "content": "[前情提要·第 1 章]\n聊了天"}


async def test_replyer_renders_chapter_line_verbatim() -> None:
    """回退开关（history_items=0，整份历史原样交给 Replyer）下，前情提要与游戏记录原样成行，不被拆成"某人说"。

    测的是回退路径：Replyer 默认（history_items>0）只带最近的原文、不带前情提要，
    那条默认路径由 test_replyer.py 覆盖；回退开关一开，章节层条目照旧整行进聊天记录。
    """
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="PROMPT")
    llm = MagicMock()
    llm.generate = AsyncMock(return_value=Response(success=True, content="好"))
    replyer = Replyer(config={"history_items": 0}, llm_service=llm, prompt_service=prompt)
    chapter = TimelineTurn(role="user", content="[前情提要·第 1 章]\n主播: 答应唱歌", message_type=CHAPTER_MESSAGE_TYPE)
    note = note_turn("游戏·minecraft·report", "交付: 木头砍好了", 0)
    assert note.message_type == GAME_MESSAGE_TYPE and note.content == "[游戏·minecraft·report] 交付: 木头砍好了"
    plan = DecisionPlan(should_reply=True, target="", topic_summary="唱歌", reply_guidance="兑现")
    await replyer.generate(plan, [], history=[chapter, note])
    dialogue = next(
        call.kwargs["dialogue_prompt"] for call in prompt.render.call_args_list if "dialogue_prompt" in call.kwargs
    )
    assert dialogue == "[前情提要·第 1 章]\n主播: 答应唱歌\n[游戏·minecraft·report] 交付: 木头砍好了"


# ---- 章节层上限与前情目录 ----

_CATALOG_HEADER = "【前情目录】（这些段落已移出上下文；细节记不清就坦白，或问问弹幕）"
_CATALOG_LINE = re.compile(r"^\[(?P<span>[^\]]+)\] (?P<gist>.+)$")
# 时间线条目带时刻时标题里有时段；一条时刻都没有时标题只有章号
_CHAPTER_TITLE = re.compile(r"^\[前情提要·第 (?P<number>\d+) 章(?: (?P<span>[^\]]+))?\]$")
_UNWRITTEN = "这一段没来得及整理"
_NO_GIST = "这一段没写出概要"


def _chapter_body(index: int) -> str:
    """假模型写的第 index 章：奇数章第一句超过 30 字（验证截断），偶数章第一句很短。

    全章贴着章节提示词"不超过 500 字"的上限写：章节越长越容易把没有上限的章节层撑爆，
    长场测试才测得出上限是否起作用（不设上限时这个长度会在 4 小时内退化出一串占位章）。
    """
    if index % 2:
        first = f"第{index}段主要和小明聊了挖矿、建房、给新猫起名，还答应了下播前唱一首歌"
    else:
        first = f"第{index}段聊了挖矿"
    return f"{first}。" + "细节" * 230


def _first_sentence(index: int) -> str:
    return _chapter_body(index).split("。", 1)[0]


class _ResizableWindowLLM(FakeLLMManager):
    """可以中途改窗口的假模型：``window`` 是消费档位当前的上下文窗口（0 = 未配置，按 128k 换算）。

    测"Dashboard 换了更小窗口的模型"用：全库的 FakeLLMManager 统一返回 0，这里只加一个可改的窗口，
    其余（应答队列、闸门、调用记录）全部沿用。
    """

    def __init__(self, window: int = 0, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.window = window

    def get_profile_context_window(self, profile: Optional[str] = None) -> int:
        return self.window


def _chapter_llm(
    count: int = 400,
    *,
    gate: Optional[asyncio.Event] = None,
    window: int = 0,
    body: Callable[[int], str] = _chapter_body,
) -> _ResizableWindowLLM:
    """章节写手的假模型：第 k 次调用返回 ``body(k)``（缺省 ``_chapter_body``；不经网络，调用当场返回）。"""
    return _ResizableWindowLLM(
        window,
        responses=[Response(success=True, content=body(index)) for index in range(1, count + 1)],
        gate=gate,
    )


def _fake_timeline(llm: FakeLLMManager, budget: Optional[StreamerContextConfig] = None) -> StreamerTimeline:
    """默认预算 4000 token：章节层上限 1200（窗口未配置，按上限计）。"""
    return StreamerTimeline(
        budget=budget or StreamerContextConfig(max_tokens=4000),
        llm_service=llm,
        prompt_service=stub_prompt(),
        profiles=("planner",),
        summary_profile="summary",
    )


def _layer_limit(budget: StreamerContextConfig, window: int = 0) -> int:
    """章节层上限：预算的 30%（``window`` 是模型上下文窗口，0 = 未配置）。"""
    return int(resolve_watermarks(budget, window).budget * 0.3)


def _catalog_lines(view: Sequence[Any]) -> List[str]:
    """历史最前面的前情目录（没有时返回空）；抬头一行必须与约定逐字一致。"""
    if not view or view[0].message_type != CHAPTER_MESSAGE_TYPE or not view[0].content.startswith("【前情目录】"):
        return []
    header, *lines = view[0].content.split("\n")
    assert header == _CATALOG_HEADER
    return lines


def _visible_chapters(view: Sequence[Any]) -> Dict[int, Tuple[str, str]]:
    """视野里各章：章号 → (标题里的时段, 正文)。"""
    chapters: Dict[int, Tuple[str, str]] = {}
    for turn in view:
        if turn.message_type != CHAPTER_MESSAGE_TYPE or turn.content.startswith("【前情目录】"):
            continue
        title, body = turn.content.split("\n", 1)
        match = _CHAPTER_TITLE.match(title)
        assert match is not None, title
        chapters[int(match["number"])] = (match["span"] or "", body)
    return chapters


def _chapter_layer(view: Sequence[Any]) -> List[Any]:
    return [turn for turn in view if turn.message_type == CHAPTER_MESSAGE_TYPE]


def _layer_tokens(view: Sequence[Any]) -> int:
    return TokenCounter().messages(canonical.turn_to_message(turn) for turn in _chapter_layer(view))


async def _run_windows(
    timeline: StreamerTimeline, windows: int, per_window: int = 10, *, timed: bool = True
) -> List[List[Any]]:
    """连跑若干决策窗：每窗新进 per_window 条记录，投影后让后台写章跑完（假模型当场返回）。

    每窗只递新记录即可：时间线只追加没见过的条目，已进去的不受影响。
    ``timed=False`` 时记录都没有发生时刻（调试对话那样），章节标题与目录行只能用章号。
    """
    views = []
    for window in range(windows):
        turns = _turns(per_window, start=window * per_window + 1)
        if not timed:
            turns = [replace(turn, timestamp_ms=0) for turn in turns]
        views.append(await timeline.project(turns))
        await _settle()
    return views


def _prefix_warnings(loguru_capture: Any) -> List[str]:
    """前缀守卫的"稳定层被意外改写"告警（预告过的改写不会出现在这里）。"""
    return [
        record["message"]
        for record in loguru_capture.records
        if record["level"] == "WARNING" and "稳定层在第" in record["message"]
    ]


async def test_oldest_chapters_move_into_catalog_when_layer_exceeds_limit() -> None:
    """章节合计超过预算 30% 时，最老的章移出视野，前情目录里留一行"时段 + 第一句（≤30 字）"。"""
    budget = StreamerContextConfig(max_tokens=4000)
    timeline = _fake_timeline(_chapter_llm(), budget)
    views = await _run_windows(timeline, 12)
    seen: Dict[int, Tuple[str, str]] = {}
    for view in views:
        seen.update(_visible_chapters(view))
        assert _layer_tokens(view) <= _layer_limit(budget)
    final = views[-1]
    lines = _catalog_lines(final)
    visible = _visible_chapters(final)
    assert lines and visible
    # 目录在章节层最前，移出的章不再出现：目录里是最老的几章，视野里接着往后数
    assert final[0].content.startswith(_CATALOG_HEADER)
    moved = list(range(1, len(lines) + 1))
    assert min(visible) == len(lines) + 1
    for number, line in zip(moved, lines, strict=True):
        match = _CATALOG_LINE.match(line)
        assert match is not None, line
        span, _ = seen[number]
        assert match["span"] == span  # 时段与这一章的标题一致
        assert match["gist"] == _first_sentence(number)[:30]
        assert len(match["gist"]) <= 30
    # 长第一句截到 30 字，短第一句原样保留
    assert {len(_CATALOG_LINE.match(line)["gist"]) for line in lines} >= {30, len(_first_sentence(2))}


async def test_catalog_drops_oldest_lines_when_it_outgrows_the_limit() -> None:
    """目录行与章节共用同一个 30% 上限：放不下时先丢最老的目录行，留下的仍按先后连续排列。"""
    budget = StreamerContextConfig(max_tokens=4000)
    timeline = _fake_timeline(_chapter_llm(), budget)
    views = await _run_windows(timeline, 50)
    seen: Dict[int, Tuple[str, str]] = {}
    for view in views:
        seen.update(_visible_chapters(view))
        assert _layer_tokens(view) <= _layer_limit(budget)
    final = views[-1]
    lines = _catalog_lines(final)
    spans = {span: number for number, (span, _) in seen.items()}
    numbers = [spans[_CATALOG_LINE.match(line)["span"]] for line in lines]
    assert numbers[0] > 1  # 第 1 章的目录行已经丢了
    # 留下的是紧挨着视野的那一串：从最老的留存行到最新移出的章，中间不缺号
    assert numbers == list(range(numbers[0], numbers[0] + len(numbers)))
    visible = _visible_chapters(final)
    assert (min(visible) if visible else max(seen) + 1) == numbers[-1] + 1


async def test_placeholder_chapter_leaves_unwritten_catalog_line() -> None:
    """被占位说明顶替的那一段，移出视野后目录行只写时段和"这一段没来得及整理"。"""
    gate = asyncio.Event()
    llm = _chapter_llm(gate=gate)
    timeline = _fake_timeline(llm)
    await timeline.project(_turns(30))
    await llm.gate_reached.wait()  # 第一章的摘要卡在半路，迟迟不回
    folded = await timeline.project(_turns(80))
    placeholder_span, placeholder_text = _visible_chapters(folded)[1]
    assert "来不及整理" in placeholder_text  # 占位说明原文不变
    gate.set()  # 之后的摘要都正常回来，章节越积越多，最老的占位章先被移出视野
    views = []
    for window in range(8, 20):
        views.append(await timeline.project(_turns(10, start=window * 10 + 1)))
        await _settle()
    lines = next((_catalog_lines(view) for view in views if _catalog_lines(view)), [])
    assert lines and lines[0] == f"[{placeholder_span}] {_UNWRITTEN}"


async def test_busy_four_hour_stream_keeps_layer_bounded_without_placeholders() -> None:
    """热闹长场（每分钟 40 条弹幕，连播 4 小时），章节写手及时返回时：零占位章，章节层始终不超过预算 30%。"""
    budget = StreamerContextConfig()  # 默认配置：窗口未配置时预算 32k
    limit = _layer_limit(budget)
    llm = _chapter_llm()
    timeline = _fake_timeline(llm, budget)
    start_ms = 1_791_000_000_000
    window_ms = 30_000  # 每 30 秒一个决策窗：20 条弹幕 + 主播一句回话
    windows = 4 * 60 * 60 * 1000 // window_ms
    peak = 0
    view: List[Any] = []
    for window in range(windows):
        base = start_ms + window * window_ms
        batch: List[TimelineTurn] = [
            TimelineTurn(
                role="viewer",
                content=f"第{window}窗第{index}条：主播今天挖到钻石了吗哈哈",
                sender_name=f"观众{(window * 20 + index) % 37}",
                message_id=f"d{window}-{index}",
                timestamp_ms=base + index * 1_500,
            )
            for index in range(20)
        ]
        batch.append(
            TimelineTurn(
                role="assistant",
                content=f"第{window}窗回话：还没挖到呢，再往下挖几层看看，大家帮我盯着岩浆。",
                message_id=f"r{window}",
                timestamp_ms=base + window_ms - 1,
            )
        )
        view = await timeline.project(batch)
        await _settle()
        assert not any("来不及整理" in turn.content for turn in _chapter_layer(view))
        layer = _layer_tokens(view)
        assert layer <= limit
        peak = max(peak, layer)
    # 上限确实起了作用：长场后段已有章移进前情目录，章节层逼近而不越过上限
    assert _catalog_lines(view) and peak > limit // 2
    assert all(call["profile"] == "summary" for call in llm.calls)


async def test_prefix_stays_verbatim_between_moves() -> None:
    """只有章节移出视野时目录才变；两次移出之间，上一窗的章节层原样是下一窗的前缀。"""
    timeline = _fake_timeline(_chapter_llm(), StreamerContextConfig(max_tokens=8000))
    views = await _run_windows(timeline, 40, per_window=5)
    moves = appended_chapters = pure_appends = 0
    for previous, current in zip(views, views[1:], strict=False):
        if _catalog_lines(current) != _catalog_lines(previous):
            moves += 1
            continue
        layer = _chapter_layer(previous)
        assert current[: len(layer)] == layer
        if len(_chapter_layer(current)) == len(layer):
            assert current[: len(previous)] == previous  # 没换章的窗只在尾部追加
            pure_appends += 1
        else:
            appended_chapters += 1
    assert moves >= 2 and appended_chapters >= 1 and pure_appends >= 1


async def test_oversized_chapter_moves_out_without_wiping_the_catalog() -> None:
    """最新一章自己就超上限（写手没守住字数）时，先把它移出、在目录留一行；已有的目录行不白丢。

    章节层放不下这一章，丢多少旧目录行它也留不住。先丢目录行的话，每次换章后目录都只剩一行、
    视野里一章也没有——旧目录行白白丢光。这里每章都比上限还长，目录要一章一行地累积起来。
    """
    budget = StreamerContextConfig(max_tokens=4000)
    limit = _layer_limit(budget)
    llm = _chapter_llm(body=lambda index: _chapter_body(index) + "细节" * 400)
    views = await _run_windows(_fake_timeline(llm, budget), 16)
    for view in views:
        assert _layer_tokens(view) <= limit
    final = views[-1]
    lines = _catalog_lines(final)
    # 每章单独都超上限，视野里一章也留不住：已换上的章全在目录里，按章号连续、一章一行
    assert not _visible_chapters(final)
    assert len(lines) >= 5
    for number, line in enumerate(lines, start=1):
        match = _CATALOG_LINE.match(line)
        assert match is not None, line
        assert match["gist"] == _first_sentence(number)[:30]
    assert not any(_UNWRITTEN in line for line in lines)


async def test_catalog_message_disappears_when_not_even_one_line_fits() -> None:
    """上限降到连抬头加一行目录都放不下时，目录行全部丢光，整条前情目录消息不再出现，不留只有抬头的空目录。"""
    budget = StreamerContextConfig(max_tokens=8000)
    llm = _chapter_llm()
    timeline = _fake_timeline(llm, budget)
    views = await _run_windows(timeline, 16)
    before = await timeline.project([])
    assert _catalog_lines(views[-1]) and _catalog_lines(before)
    llm.window = 400  # 换成只有 400 token 窗口的模型：预算 100，章节层上限 30
    after = await timeline.project([])
    assert _layer_limit(budget, llm.window) < len(_CATALOG_HEADER)
    assert _chapter_layer(after) == []  # 没有前情目录，也没有前情提要
    assert after == before[len(_chapter_layer(before)) :]  # 剩下的只有工作层原文，一条没少


async def test_catalog_anchors_on_chapter_number_when_records_have_no_clock() -> None:
    """时间线条目都没有发生时刻时，章节标题里没有时段，目录行用"第 N 章"代替时段，仍按先后排列。"""
    budget = StreamerContextConfig(max_tokens=4000)
    views = await _run_windows(_fake_timeline(_chapter_llm(), budget), 12, timed=False)
    final = views[-1]
    lines = _catalog_lines(final)
    visible = _visible_chapters(final)
    assert lines and visible
    for number, line in enumerate(lines, start=1):
        match = _CATALOG_LINE.match(line)
        assert match is not None, line
        assert match["span"] == f"第 {number} 章"
        assert match["gist"] == _first_sentence(number)[:30]
    assert {span for span, _ in visible.values()} == {""}  # 视野里的章标题也只有章号
    assert min(visible) == len(lines) + 1
    assert all(_layer_tokens(view) <= _layer_limit(budget) for view in views)


async def test_shrinking_model_window_moves_chapters_out_without_prefix_warning(loguru_capture: Any) -> None:
    """换成窗口更小的模型、章节层上限跟着降低时，没换章的窗里也把超出的章移进目录；这次改写是预告过的。

    空窗里既没有新记录、也没有后台整理在跑，唯一的变化就是上限降低：视野里的章少了、目录多了行，
    工作层原文一条没动。目录消息变了，前缀守卫要事先知道这是预期的移出，不能报"稳定层被改写"。
    """
    budget = StreamerContextConfig(max_tokens=8000)
    llm = _chapter_llm()
    timeline = _fake_timeline(llm, budget)
    await _run_windows(timeline, 16)
    await timeline.project([])  # 空窗：最后一章摘要写好后换上
    await _settle()
    settled = await timeline.project([])
    calls_before = len(llm.calls)
    llm.window = 16_000  # 预算 8000 降到 4000：章节层上限 2400 降到 1200
    new_limit = _layer_limit(budget, llm.window)
    assert new_limit < _layer_tokens(settled) <= _layer_limit(budget)

    shrunk = await timeline.project([])
    await _settle()

    assert _layer_tokens(shrunk) <= new_limit
    assert len(_visible_chapters(shrunk)) < len(_visible_chapters(settled))
    assert len(_catalog_lines(shrunk)) > len(_catalog_lines(settled))
    # 只有章节层变了：没有新章换上（也没有新的后台整理发起），工作层原文逐条不变
    assert len(llm.calls) == calls_before
    assert shrunk[len(_chapter_layer(shrunk)) :] == settled[len(_chapter_layer(settled)) :]
    assert _prefix_warnings(loguru_capture) == []


# 比章节层上限（4000 预算下是 1200）还长的纯句号：这一章一换上就直接进前情目录，
# 同时全是空句，不会混进概要
_OVERSIZED_FILLER = "。" * 1300


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        pytest.param("**话题**：挖矿、建房；后来……", "话题：挖矿、建房", id="成对加粗不留右半边"),
        pytest.param("- **聊了挖矿**，还建了房。后来……", "聊了挖矿，还建了房", id="列表符号加加粗"),
        pytest.param("`memory_recall` 查过一次。", "memory_recall 查过一次", id="行内代码记号去掉、下划线保留"),
        pytest.param("1. 聊了挖矿。后来……", "聊了挖矿", id="阿拉伯数字加点"),
        pytest.param("2) 聊了挖矿。后来……", "聊了挖矿", id="阿拉伯数字加右括号"),
        pytest.param("（3）聊了挖矿。后来……", "聊了挖矿", id="括号数字"),
        pytest.param("一、聊了挖矿。后来……", "聊了挖矿", id="中文数字加顿号"),
        pytest.param("2.5 倍速下聊了挖矿。", "2.5 倍速下聊了挖矿", id="小数不当序号"),
        pytest.param("**\n1.\n聊了挖矿。后来……", "聊了挖矿", id="只剩记号和序号的行跳过、取下一行"),
        pytest.param("**。\n1.\n", _NO_GIST, id="全是记号和标点时用兜底文字"),
    ],
)
async def test_catalog_line_gist_is_clean_text_with_fallback(body: str, expected: str) -> None:
    """目录行内容是去掉加粗/代码记号和开头序号的第一句；摘不出话时写固定兜底文字，每行都有内容。"""
    llm = _chapter_llm(body=lambda _index: body + _OVERSIZED_FILLER)
    timeline = _fake_timeline(llm, StreamerContextConfig(max_tokens=4000))
    await timeline.project(_turns(30))
    await _settle()
    view = await timeline.project(_turns(30))
    lines = _catalog_lines(view)
    assert len(lines) == 1
    match = _CATALOG_LINE.match(lines[0])
    assert match is not None, lines[0]  # "[时段] 内容"：时段和内容都在
    assert match["gist"] == expected
