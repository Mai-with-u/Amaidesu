"""验证后台折叠：过了折叠线另发同前缀的整理请求，身体照常行动，折叠在下一步前换上，原文可取回。"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

import pytest
from pydantic import ValidationError

from src.agents.minecraft.config import MinecraftConfig, MinecraftContextConfig
from src.agents.minecraft.context import INSTRUCTION_KEY
from src.modules.context import BLOCK_KEY
from src.modules.llm.payload import Response

from .minecraft_v1_fakes import (
    FakeMaicraft,
    _call,
    _reports,
    _resp,
    _wait_until,
    make_minecraft_agent,
    offline_config,
)

FOLD_REQUEST = "[宿主整理请求]"


@pytest.fixture(autouse=True)
def _chars_as_tokens(chars_as_tokens: None) -> None:
    """整个文件按字符数计 token：转用 tests/conftest.py 的共享夹具。"""


def _config(**context: Any) -> MinecraftConfig:
    """离线配置 + 小预算：几次大观察就能越过折叠线，又碰不到集中整理的预算线。"""
    settings: Dict[str, Any] = {"max_tokens": 40_000, "fold_keep_recent": 1, **context}
    return offline_config(events_wait_ms=1000).model_copy(update={"context": MinecraftContextConfig(**settings)})


def _big_server() -> FakeMaicraft:
    """每次看现场都回一份两千字的观察，历史涨得快。"""
    server = FakeMaicraft()
    server.scene_view["summary"] = "前方：e1（minecraft:cow，6格）" + "草" * 2000
    return server


def _is_fold_request(messages: List[Dict[str, Any]]) -> bool:
    content = messages[-1].get("content")
    return isinstance(content, str) and content.startswith(FOLD_REQUEST)


def _listed_ranges(messages: List[Dict[str, Any]]) -> List[Tuple[str, str]]:
    return re.findall(r"^- (\S+)–(\S+)：", str(messages[-1]["content"]), re.M)


def _has_text(messages: List[Dict[str, Any]], text: str) -> bool:
    return any(text in str(message.get("content")) for message in messages)


async def test_fold_runs_beside_actions_with_same_prefix_and_original_comes_back() -> None:
    """过了折叠线：整理请求与行动步同前缀同工具；模型顺手调的游戏动作不执行；折叠落地后行动照常，原文读得回来。"""
    server = _big_server()
    progress = {"steps": 0}

    def script(messages: List[Dict[str, Any]]) -> Response:
        if _is_fold_request(messages):
            start, end = _listed_ranges(messages)[0]
            fold = {"folds": [{"start": start, "end": end, "summary": "开局与几次看现场：前方有牛，左侧有箱子"}]}
            gather = {"goal": {"ability": "gather"}}
            return _resp(calls=[_call("minecraft_context_fold", fold, "f1"), _call("maicraft_execute", gather, "f2")])
        progress["steps"] += 1
        if _has_text(messages, '"block":"b1"') or _has_text(messages, '"block": "b1"') or progress["steps"] > 30:
            return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "看完了"}, "d1")])
        if _has_text(messages, "[已折叠 b1"):
            return _resp(calls=[_call("minecraft_context_recall", {"block": "b1"}, "r1")])
        return _resp(calls=[_call("maicraft_observe", {"what": "scene"}, f"c{progress['steps']}")])

    agent, llm, bus = await make_minecraft_agent(server, script, config=_config())
    try:
        agent.receive_prompt(content="把周围看清楚", source="test")
        await _wait_until(lambda: bool(_reports(bus)))
    finally:
        await agent.stop()

    # 历史折完又涨过一截时会再请一次，这里只看第一次整理请求
    folds = [index for index, seen in enumerate(llm.seen) if _is_fold_request(seen)]
    assert folds
    fold_request = llm.seen[folds[0]]
    # 整理请求就是某一次行动步的完整历史再加一条请求：前缀逐字相同、工具声明相同，缓存能整段命中
    twins = [
        index
        for index, seen in enumerate(llm.seen)
        if index != folds[0] and seen[: len(fold_request) - 1] == fold_request[:-1]
    ]
    assert twins and llm.tools_given[twins[0]] == llm.tools_given[folds[0]]
    # 整理请求里顺手调的游戏动作被丢弃，身体只执行了行动步里的观察
    assert not server.calls_to("execute")
    # 开任务的原话不编号、不折；其余新消息开头写着编号
    instruction = fold_request[1]
    assert instruction.get(INSTRUCTION_KEY) and not str(instruction["content"]).startswith("[m")
    assert "[m0001] [开局资料]" in str(fold_request[2]["content"])
    # 折叠落地后的行动步里，原区间换成了一条替身；读回的原文里有开局资料
    later = [seen for seen in llm.seen[twins[0] + 1 :] if not _is_fold_request(seen)]
    assert any(_has_text(seen, "[已折叠 b1") for seen in later)
    recalled = [message for message in llm.seen[-1] if message.get("tool_call_id") == "r1"]
    assert recalled and "开局资料" in str(recalled[0]["content"])


async def test_fold_tool_called_while_acting_is_refused() -> None:
    """行动中调折叠工具不执行，回执让它继续推进任务。"""
    server = FakeMaicraft()

    def script(messages: List[Dict[str, Any]]) -> Response:
        if any(message.get("tool_call_id") == "f1" for message in messages):
            return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "好了"}, "d1")])
        fold = {"folds": [{"start": "m0001", "end": "m0001", "summary": "开局"}]}
        return _resp(calls=[_call("minecraft_context_fold", fold, "f1")])

    agent, llm, bus = await make_minecraft_agent(server, script, config=_config())
    try:
        agent.receive_prompt(content="干活", source="test")
        await _wait_until(lambda: bool(_reports(bus)))
    finally:
        await agent.stop()
    receipt = [message for message in llm.seen[-1] if message.get("tool_call_id") == "f1"]
    assert "折叠由宿主在后台安排" in str(receipt[0]["content"])
    assert not any(BLOCK_KEY in message for message in llm.seen[-1])


async def test_switch_off_keeps_history_untouched() -> None:
    """关掉后台折叠：不编号、不声明折叠与取回工具、过了折叠线也不发整理请求。"""
    server = _big_server()
    progress = {"steps": 0}

    def script(messages: List[Dict[str, Any]]) -> Response:
        progress["steps"] += 1
        if progress["steps"] > 6:
            return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "看完了"}, "d1")])
        return _resp(calls=[_call("maicraft_observe", {"what": "scene"}, f"c{progress['steps']}")])

    agent, llm, bus = await make_minecraft_agent(server, script, config=_config(active_fold=False))
    try:
        agent.receive_prompt(content="把周围看清楚", source="test")
        await _wait_until(lambda: bool(_reports(bus)))
    finally:
        await agent.stop()
    assert not any(_is_fold_request(seen) for seen in llm.seen)
    assert not any(_has_text(seen, "[m0001]") for seen in llm.seen)
    names = {tool["name"] for tool in llm.tools_given[-1]}
    assert not {"minecraft_context_fold", "minecraft_context_recall"} & names


async def test_budget_line_stows_instead_of_waiting_and_original_comes_back() -> None:
    """模型一直说无需整理、集中整理也写不出摘要：到预算线当场收起旧记录，任务照常交付，原文读得回来。"""
    server = _big_server()
    progress = {"steps": 0}

    def script(messages: List[Dict[str, Any]]) -> Response:
        if _is_fold_request(messages):
            return _resp(content="无需整理")
        if str(messages[0].get("content")).startswith("你在整理 Minecraft"):
            # 集中整理的摘要请求：回一个工具调用，摘要必然失败；走同步整理的话任务会被挂起
            return _resp(calls=[_call("maicraft_observe", {"what": "scene"}, "s1")])
        progress["steps"] += 1
        if _has_text(messages, '"block":"b') or _has_text(messages, '"block": "b') or progress["steps"] > 40:
            return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "看完了"}, "d1")])
        # 只认真实占位（块号后紧跟冒号）；系统提示词里举例的块号后面没有冒号
        stowed = re.findall(r"\[原文已收起 (b\d+)：", "".join(str(message.get("content")) for message in messages))
        if stowed:
            return _resp(calls=[_call("minecraft_context_recall", {"block": stowed[0]}, "r1")])
        return _resp(calls=[_call("maicraft_observe", {"what": "scene"}, f"c{progress['steps']}")])

    agent, llm, bus = await make_minecraft_agent(server, script, config=_config())
    try:
        agent.receive_prompt(content="把周围看清楚", source="test")
        await _wait_until(lambda: bool(_reports(bus)))
    finally:
        await agent.stop()
    assert _reports(bus)[0].message == "看完了"
    recalled = [message for message in llm.seen[-1] if message.get("tool_call_id") == "r1"]
    assert recalled and "草草草" in str(recalled[0]["content"])


def test_fold_line_must_stay_below_prepare_line() -> None:
    """折叠线不低于提前整理线时，集中整理会抢在模型折叠之前出手，配置期拦下。"""
    with pytest.raises(ValidationError):
        MinecraftContextConfig(fold_ratio=0.7, prepare_ratio=0.7, target_ratio=0.6)


def _running_goal(server: FakeMaicraft) -> int:
    """还在跑的目标编号；没有时为 0。"""
    return next((goal_id for goal_id, run in server.goals.items() if run["state"] == "running"), 0)


async def test_wakeups_do_not_cut_foldable_range_and_state_is_restated() -> None:
    """一次次被叫醒续做：插进来的任务状态不把可折区间切碎，折掉之后宿主补一份完整的任务状态。"""
    server = FakeMaicraft()
    # 开局资料本身不大：可折的量要靠后面一轮轮叫醒攒出来，每一轮都不到单段下限
    server.scene_view["summary"] = "前方：e1（minecraft:cow，6格）"

    def script(messages: List[Dict[str, Any]]) -> Response:
        if _is_fold_request(messages):
            start, end = _listed_ranges(messages)[0]
            fold = {"folds": [{"start": start, "end": end, "summary": "开局看过现场，之后一棵棵砍，都砍完了"}]}
            return _resp(calls=[_call("minecraft_context_fold", fold, "f1")])
        if _running_goal(server):
            return _resp(content="等这棵砍完")
        if _has_text(messages, "[当前任务状态]"):
            return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "砍完了"}, "d1")])
        return _resp(calls=[_call("maicraft_execute", {"goal": {"ability": "gather"}}, f"g{len(server.goals)}")])

    # 预算 3 万：折叠线 12000，单段下限 1000；每轮叫醒只添几百字，要攒好几轮才过线
    agent, llm, bus = await make_minecraft_agent(server, script, config=_config(max_tokens=30_000))
    try:
        agent.receive_prompt(content="一棵一棵砍，砍到我说停", source="test")
        for _ in range(80):
            await _wait_until(lambda: bool(_reports(bus)) or (not agent.batch_active and bool(_running_goal(server))))
            if _reports(bus):
                break
            server.finish(_running_goal(server), summary="砍了一棵" + "木" * 100)
        assert _reports(bus)
    finally:
        await agent.stop()

    folds = [index for index, seen in enumerate(llm.seen) if _is_fold_request(seen)]
    assert folds
    request = llm.seen[folds[0]]
    start, end = _listed_ranges(request)[0]
    # 列出的那一段跨过了好几次叫醒：终点之前已经有不止三条任务状态
    end_at = next(index for index, message in enumerate(request) if str(message.get("content")).startswith(f"[{end}]"))
    states_before_end = [m for m in request[:end_at] if "[继续原游戏任务]" in str(m.get("content"))]
    assert len(states_before_end) >= 3
    # 任务状态消息有编号（不算固定层），开任务的原话仍然不编号
    assert all(re.match(r"\[m\d+\] \[继续原游戏任务\]", str(m["content"])) for m in states_before_end)
    # 折掉之后的请求里有一份完整的任务状态，带着原话
    restated = [
        message
        for seen in llm.seen[folds[0] + 1 :]
        for message in seen
        if "[当前任务状态]" in str(message.get("content"))
    ]
    assert restated and "一棵一棵砍，砍到我说停" in str(restated[0]["content"])


async def test_fold_resumes_after_background_compaction() -> None:
    """模型先说无需整理、历史涨到提前整理线由后台集中整理压小：之后历史再过折叠线，照样再请模型折叠。"""
    server = _big_server()
    progress = {"steps": 0, "compacted": False, "fold_after": False}

    def script(messages: List[Dict[str, Any]]) -> Response:
        if str(messages[0].get("content")).startswith("你在整理 Minecraft"):
            progress["compacted"] = True
            return _resp(content="看过很多次现场，前方一直是牛")
        if _is_fold_request(messages):
            progress["fold_after"] = progress["fold_after"] or progress["compacted"]
            return _resp(content="无需整理")
        progress["steps"] += 1
        if progress["fold_after"] or progress["steps"] > 60:
            return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "看完了"}, "d1")])
        return _resp(calls=[_call("maicraft_observe", {"what": "scene"}, f"c{progress['steps']}")])

    agent, llm, bus = await make_minecraft_agent(server, script, config=_config())
    try:
        agent.receive_prompt(content="把周围看清楚", source="test")
        await _wait_until(lambda: bool(_reports(bus)))
    finally:
        await agent.stop()
    # 集中整理确实在后台跑过，而且它之后又发过整理请求（没被跑完的那次整理一直占着位置）
    assert progress["compacted"] and progress["fold_after"]


async def test_overlong_fold_summary_is_truncated_with_minecraft_recall_note_instead_of_discarded() -> None:
    """第 66 场回归：后台折叠产出的摘要超出缓冲上限时，在句号处安全截断并附注 minecraft_context_recall 取回提示，不整单报废。"""
    server = _big_server()
    progress = {"steps": 0}
    overlong_summary = (
        "阶段勘察：" + "树" * 2400 + "坐标 x=16 z=-32 的箱子已清点完毕。" + "超出上限的赘述部分" + "草" * 1200
    )

    def script(messages: List[Dict[str, Any]]) -> Response:
        if _is_fold_request(messages):
            start, end = _listed_ranges(messages)[0]
            fold = {"folds": [{"start": start, "end": end, "summary": overlong_summary}]}
            return _resp(calls=[_call("minecraft_context_fold", fold, "f1")])
        progress["steps"] += 1
        if _has_text(messages, '"block":"b1"') or _has_text(messages, '"block": "b1"') or progress["steps"] > 30:
            return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "清点好了"}, "d1")])
        if _has_text(messages, "[已折叠 b1"):
            return _resp(calls=[_call("minecraft_context_recall", {"block": "b1"}, "r1")])
        return _resp(calls=[_call("maicraft_observe", {"what": "scene"}, f"c{progress['steps']}")])

    agent, llm, bus = await make_minecraft_agent(server, script, config=_config(fold_summary_max_chars=3000))
    try:
        agent.receive_prompt(content="清点营地周围", source="test")
        await _wait_until(lambda: bool(_reports(bus)))
    finally:
        await agent.stop()

    fold_prompts = [seen[-1]["content"] for seen in llm.seen if _is_fold_request(seen)]
    assert fold_prompts and "省不下足够空间会被拒收" in str(fold_prompts[0])
    stand_ins = [
        str(message.get("content"))
        for seen in llm.seen
        for message in seen
        if str(message.get("content")).startswith("[已折叠 b1")
    ]
    assert stand_ins
    assert "坐标 x=16 z=-32 的箱子已清点完毕。" in stand_ins[0]
    assert "超出上限的赘述部分" not in stand_ins[0]
    assert "（超长部分已截断，原文可用 minecraft_context_recall 取回）" in stand_ins[0]
    recalled = [message for message in llm.seen[-1] if message.get("tool_call_id") == "r1"]
    assert recalled and "开局资料" in str(recalled[0]["content"])
