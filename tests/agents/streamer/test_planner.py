"""Planner ReAct 循环：工具列表、reply 收尾 / 自然终止 / 超步、registry 路由与观察回灌、降级、上下文与记忆、消息构成"""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.planner import Planner, PlannerConfig, PlannerEnv
from src.agents.streamer.room_state import RoomState
from src.agents.streamer.timeline import GameEventBatch
from src.modules.llm.payload import Response, ToolCall
from src.modules.storage.models.memory import ArchiveLine, Commitment
from src.modules.tools.models import ToolExecutionResult, ToolSpec
from src.modules.tools.provider import make_provider_from_specs
from src.modules.tools.registry import ToolRegistry


def _tc(name: str, args: dict, call_id: str = "c1") -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments=args)


def _resp(content: str = "", tool_calls: list | None = None) -> Response:
    return Response(success=True, content=content, tool_calls=tool_calls or [])


def _spec(name: str, provider: str = "x", schema: dict | None = {"type": "object"}) -> ToolSpec:  # noqa: B006
    return ToolSpec(name=name, description=name, parameters_schema=schema, kind="sync", provider=provider)


def _registry_mock(*specs: ToolSpec, result: ToolExecutionResult | None = None) -> MagicMock:
    registry = MagicMock()
    registry.list_tools.return_value = list(specs)
    if result is not None:
        registry.invoke = AsyncMock(return_value=result)
    return registry


_REPLY_RESULT = ToolExecutionResult(
    tool_name="streamer_reply",
    success=True,
    structured_content={
        "speech": "测试回复",
        "emotion": {"name": "happy", "intensity": 0.6},
        "actions": [],
        "metadata": {"target": "u1"},
    },
)


def _make_planner(
    chat_responses: list | None = None,
    registry: Any | None = None,
    reply_provider: Any | None = None,
    context_enabled: bool = True,
    max_steps: int = 8,
    elapsed_live_provider: Any | None = None,
    brief_provider: Any | None = None,
    brief_ids_provider: Any | None = None,
    commitment_lookup: Any | None = None,
    brief_archive_ids_provider: Any | None = None,
    archive_lookup: Any | None = None,
    memory_cues_provider: Any | None = None,
) -> tuple[Planner, MagicMock, MagicMock]:
    """mock LLM（generate）+ mock prompt_service；缺省真注册表 + 注册成功的 reply（reply 经 registry.invoke）。"""
    llm = MagicMock()
    llm.generate = AsyncMock(side_effect=list(chat_responses or []))
    prompt = MagicMock()
    prompt.render = MagicMock(return_value="SYSTEM_PROMPT")

    reg = registry if registry is not None else ToolRegistry()
    prov = reply_provider
    if prov is None:
        prov = MagicMock()
        prov.invoke = AsyncMock(return_value=_REPLY_RESULT)
    if isinstance(reg, ToolRegistry) and not reg.has("streamer_reply"):
        spec = ToolSpec(name="reply", description="主播发言出口", kind="sync", provider="streamer")
        reg.register_provider(make_provider_from_specs("streamer", [(spec, lambda inv: prov.invoke(inv))]))

    planner = Planner(
        config=PlannerConfig(planner_max_steps=max_steps),
        llm_service=llm,
        prompt_service=prompt,
        room_state=RoomState(),
        tool_registry=reg,
        context_enabled=context_enabled,
        env=PlannerEnv(
            reply_provider_factory=lambda: prov,
            elapsed_live_provider=elapsed_live_provider,
            brief_provider=brief_provider,
            brief_ids_provider=brief_ids_provider,
            commitment_lookup=commitment_lookup,
            brief_archive_ids_provider=brief_archive_ids_provider,
            archive_lookup=archive_lookup,
            memory_cues_provider=memory_cues_provider,
        ),
    )
    return planner, llm, prompt


def _msg(text: str = "hi", mid: str = "m1") -> Any:
    msg = MagicMock()
    msg.content, msg.message_id, msg.message_type, msg.platform = text, mid, "danmaku", "bilibili"
    msg.user.name, msg.user.id = "观众", "u_观众"
    return msg


def _history_msg(role: str, content: str, *, message_id: str = "") -> Any:
    msg = MagicMock()
    msg.role = MagicMock(value=role)
    msg.content, msg.message_id = content, message_id
    msg.sender_name = "观众A" if role == "user" else "主播"
    msg.message_type = "danmaku" if role == "user" else "speak"
    return msg


def _reference_message(llm: Any) -> Any:
    """参考段 = 发给 LLM 的消息序列中最后一条 user 消息（序列尾，循环追加在其后）。"""
    return next((m for m in reversed(llm.generate.await_args.args[0]) if m["role"] == "user"), None)


def _tool_messages(llm: Any, call_index: int) -> list:
    return [m for m in llm.generate.await_args_list[call_index].args[0] if m.get("role") == "tool"]


def test_tool_list_comes_straight_from_the_registry() -> None:
    """for_agent("streamer") 注册表结果，全名直出，无二次筛选；registry 缺失时为空。"""
    registry = _registry_mock(_spec("get_work_log", "minecraft"), _spec("reply", "streamer", None))
    # 定义形状由工具域 llm_defs 统一生成（planner 直调模块函数，不经 registry 方法）
    planner, _llm, _prompt = _make_planner(registry=registry)
    tool_list = planner._build_tool_list()
    assert [f["name"] for f in tool_list] == ["minecraft_get_work_log", "streamer_reply"]
    assert all(f["context_section"] == "system_tools" for f in tool_list)

    bare = Planner(
        config=PlannerConfig(), llm_service=MagicMock(), prompt_service=MagicMock(), room_state=RoomState(), tool_registry=None,
        env=PlannerEnv(reply_provider_factory=MagicMock),
    )  # fmt: skip
    assert bare._build_tool_list() == []


async def test_reply_ends_loop_and_natural_termination_is_silent() -> None:
    planner, llm, _prompt = _make_planner(
        [_resp(tool_calls=[_tc("streamer_reply", {"topic_summary": "t", "target": "m1"})])]
    )
    outcome = await planner.plan([_msg()])
    assert (outcome.replied, outcome.speech, outcome.silent_reason) == (True, "测试回复", None)
    assert (outcome.steps, outcome.tool_trace) == (1, ["streamer_reply"])
    assert llm.generate.await_count == 1  # 说话即收尾

    planner, llm, _prompt = _make_planner([_resp("这轮不说话")])
    outcome = await planner.plan([_msg()])
    assert (outcome.replied, outcome.silent_reason, llm.generate.await_count) == (False, "natural", 1)


async def test_registry_tool_observation_is_fed_back_then_reply_ends() -> None:
    registry = _registry_mock(
        _spec("minecraft_get_state", "minecraft"),
        result=ToolExecutionResult(tool_name="minecraft_get_state", success=True, structured_content={"todo": []}),
    )
    steps = [
        _resp(tool_calls=[_tc("minecraft_get_state", {}, "c1")]),
        _resp(tool_calls=[_tc("streamer_reply", {"topic_summary": "t"}, "c2")]),
    ]
    planner, llm, _prompt = _make_planner(steps, registry=registry)

    outcome = await planner.plan([_msg()])
    assert outcome.replied is True and outcome.tool_trace == ["minecraft_get_state", "streamer_reply"]
    observation = _tool_messages(llm, 1)[0]  # 第二轮 messages 带 tool role + tool_call_id 关联
    assert observation["tool_call_id"] == "c1" and '"todo"' in observation["content"]


async def test_tool_failure_is_fed_back_and_max_steps_goes_silent() -> None:
    failing = ToolExecutionResult(tool_name="tool_x", success=False, error_message="world not loaded")
    planner, llm, _prompt = _make_planner(
        [_resp(tool_calls=[_tc("tool_x", {}, "c1")]), _resp()], registry=_registry_mock(_spec("tool_x"), result=failing)
    )
    outcome = await planner.plan([_msg()])
    assert outcome.replied is False and "world not loaded" in _tool_messages(llm, 1)[0]["content"]

    ok = ToolExecutionResult(tool_name="tool_x", success=True, structured_content={"ok": True})
    looping = [_resp(tool_calls=[_tc("tool_x", {}, f"c{i}")]) for i in range(20)]
    planner, llm, _prompt = _make_planner(looping, registry=_registry_mock(_spec("tool_x"), result=ok), max_steps=3)
    outcome = await planner.plan([_msg()])
    assert (outcome.silent_reason, outcome.steps, llm.generate.await_count) == ("max_steps", 3, 3)


async def test_failures_degrade_without_raising() -> None:
    # reply Provider 未绑定：观察报错，LLM 可调整或自然终止
    unbound = Planner(
        config=PlannerConfig(planner_max_steps=3), llm_service=MagicMock(), prompt_service=MagicMock(), room_state=RoomState(),
        tool_registry=MagicMock(),
    )  # fmt: skip
    unbound._llm_service.generate = AsyncMock(
        side_effect=[_resp(tool_calls=[_tc("streamer_reply", {"topic_summary": "t"})]), _resp()]
    )
    outcome = await unbound.plan([_msg()])
    assert (outcome.replied, outcome.silent_reason) == (False, "natural")

    planner, _llm, prompt = _make_planner([_resp(content="思考中")])
    outcome = await planner.plan([_msg()])
    assert outcome.raw_content == "思考中"  # 原始输出随 outcome 带回
    planner._llm_service.generate = AsyncMock(side_effect=RuntimeError("boom"))
    outcome = await planner.plan([_msg()])
    assert (outcome.silent_reason, outcome.error is not None) == ("llm_error", True)
    assert "boom" in outcome.error

    prompt.render = MagicMock(side_effect=RuntimeError("template missing"))
    outcome = await planner.plan([_msg()])
    assert outcome.silent_reason == "prompt_render_failed" and "template missing" in (outcome.error or "")


async def test_context_assembly_paths() -> None:
    # 禁用上下文：裸消息路径，系统提示词只渲染 behavior_style
    planner, llm, prompt = _make_planner([_resp()], context_enabled=False)
    await planner.plan([_msg("主播好")], history=[])
    messages = llm.generate.await_args.args[0]
    assert messages[1]["role"] == "user" and "主播好" in messages[1]["content"]
    prompt.render.assert_called_once()
    assert prompt.render.call_args.args[0] == "amaidesu_planner_react"

    # forced 标注进参考段（序列尾）；本轮新入账的游戏事件不再重放进参考段——
    # 它们已在决策窗边界追加进对话历史，只有状态跃迁事实块汇总进参考段；字符级 topics 不进 prompt
    planner, llm, _prompt = _make_planner([_resp()])
    planner._room_state.update(_msg("来个落地水"))
    planner._room_state.update(_msg("来个落地水"))
    await planner.plan(
        [_msg()],
        forced=True,
        # 事实块随批次由账本携带（facts_text），planner 不再自己从行提取
        game_batch=GameEventBatch(
            events=("[20:01 游戏·minecraft·report] 刚挖到钻石",),
            facts_text="[20:01 游戏·minecraft·report] 刚挖到钻石",
        ),
    )
    reference = _reference_message(llm)["content"]
    assert "强制回应" in reference and "【当前状态（客观事实）】" in reference and "关键变化" not in reference


@pytest.mark.parametrize(
    ("provider", "expected"),
    [(lambda: 75 * 60_000, "已开播时长: 1 小时 15 分钟"), (None, None), ("boom", None)],
    ids=["provided", "missing", "raises"],
)
async def test_elapsed_live_duration_line(provider, expected) -> None:
    def _boom() -> int:
        raise RuntimeError("rundown down")

    planner, llm, _prompt = _make_planner([_resp()], elapsed_live_provider=_boom if provider == "boom" else provider)
    outcome = await planner.plan([_msg()])  # provider 异常降级为省略该行，不阻断决策
    reference = _reference_message(llm)["content"]
    assert outcome.silent_reason == "natural"
    assert (expected in reference) if expected else ("已开播时长" not in reference)


def test_dialogue_messages_use_native_roles_and_dedup_only_the_tail() -> None:
    planner, _llm, _prompt = _make_planner()
    history = [
        _history_msg("user", "大家好呀", message_id="m1"),
        _history_msg("assistant", "晚上好啊", message_id="m2"),
    ]
    messages = planner._build_dialogue_messages([_msg("主播好", mid="m9")], history)
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert "大家好呀" in messages[0]["content"] and "晚上好啊" in messages[1]["content"]
    assert "主播好" in messages[2]["content"] and "[id:m9]" in messages[2]["content"]

    # 历史尾部与本批同 id 的消息剔除；更早的同文保留
    history = [
        _history_msg("user", "上次也说过落地水", message_id="m1"),
        _history_msg("user", "来个落地水", message_id="m9"),
    ]
    joined = "\n".join(m["content"] for m in planner._build_dialogue_messages([_msg("来个落地水", mid="m9")], history))
    assert joined.count("来个落地水") == 1 and "上次也说过落地水" in joined


async def test_batch_is_not_duplicated_from_history_tail_in_the_sent_messages() -> None:
    planner, llm, _prompt = _make_planner([_resp()])
    history = [_history_msg("user", "大家好呀", message_id="m1"), _history_msg("user", "来个落地水", message_id="m9")]
    await planner.plan([_msg("来个落地水", mid="m9")], history=history)

    dialogue = [m for m in llm.generate.await_args.args[0] if m["role"] in ("user", "assistant")][:-1]  # 去掉参考段
    joined = "\n".join(m["content"] for m in dialogue)
    assert joined.count("来个落地水") == 1 and "大家好呀" in joined


def _system_message(llm: Any, call_index: int = -1) -> str:
    return llm.generate.await_args_list[call_index].args[0][0]["content"]


async def test_brief_rides_at_system_prompt_tail_not_in_reference() -> None:
    """常驻简报接在系统提示词末尾（整场不变，跨决策窗逐字一致可吃前缀缓存），参考段里不出现。"""
    brief = "# 她记得的事（内部参考；以眼前为准，用不上就不提）\n上一场：\n- [档#41] 挖到了钻石"
    planner, llm, _prompt = _make_planner([_resp(), _resp()], brief_provider=lambda: brief)

    await planner.plan([_msg("第一窗")], rundown_text="## 开场")
    await planner.plan([_msg("第二窗", mid="m2")], rundown_text="## 开场")

    system = _system_message(llm)
    assert system == "SYSTEM_PROMPT\n\n" + brief
    assert _system_message(llm, 0) == system
    assert "挖到了钻石" not in _reference_message(llm)["content"]


async def test_brief_omitted_when_absent_or_unreadable() -> None:
    """没有简报时系统提示词保持原样；简报读失败只省这一段，决策照常。"""
    planner, llm, _prompt = _make_planner([_resp()])
    await planner.plan([_msg()])
    assert _system_message(llm) == "SYSTEM_PROMPT"

    def broken() -> str:
        raise RuntimeError("简报坏了")

    planner, llm, _prompt = _make_planner([_resp()], brief_provider=broken)
    await planner.plan([_msg()])
    assert _system_message(llm) == "SYSTEM_PROMPT"


# ---------------------------------------------------------------------------
# 当前游戏的玩法：按工具面说明拼装，经 game_rules 注入系统提示词
# ---------------------------------------------------------------------------


class _GuideProvider:
    """最小工具面桩：一个对主播可见的工具 + 一段使用说明。"""

    category = "game"

    def __init__(self, name: str, guide: str, visible_to: list[str] | None = None) -> None:
        self._name = name
        self._guide = guide
        self._visible_to = visible_to

    @property
    def name(self) -> str:
        return self._name

    def list_tools(self):
        return [ToolSpec(name="act", description="动作", kind="sync", provider=self._name, visible_to=self._visible_to)]

    async def invoke(self, invocation):  # type: ignore[no-untyped-def]
        return ToolExecutionResult(tool_name=invocation.tool_name, success=True)

    def guide_for(self, agent_name: str) -> str:
        return self._guide


def test_game_rules_without_guides_says_no_game() -> None:
    """没有任何游戏工具面：玩法段明说没有游戏，模型不对不存在的游戏工具硬凑动作。"""
    planner, _llm, _prompt = _make_planner()

    assert planner._render_game_rules() == Planner.NO_GAME_RULES


def test_game_rules_lists_remote_game_guide_without_delegate_rules() -> None:
    """只有遥控型游戏：玩法段就是它的说明，不追加委派守则（prompt 只渲染主模板）。"""
    reg = ToolRegistry()
    reg.register_provider(_GuideProvider("game_x", "X 是遥控型游戏：用 game_x_act 按一下"))
    planner, _llm, prompt = _make_planner(registry=reg)

    rules = planner._render_game_rules()

    assert rules == "- X 是遥控型游戏：用 game_x_act 按一下"
    prompt.render.assert_not_called()


def test_game_rules_appends_delegate_rules_when_framework_guide_present() -> None:
    """framework 工具面给出说明（有能接收委派的 Agent）：玩法段追加委派守则子模板。"""
    reg = ToolRegistry()
    reg.register_provider(_GuideProvider("framework", "委派型游戏：mc=...", visible_to=["*"]))
    reg.register_provider(_GuideProvider("mc", "Minecraft 走委派"))
    planner, _llm, prompt = _make_planner(registry=reg)
    prompt.render = MagicMock(return_value="【委派守则】")

    rules = planner._render_game_rules()

    assert rules.split(chr(10)) == ["- 委派型游戏：mc=...", "- Minecraft 走委派", "【委派守则】"]
    prompt.render.assert_called_once_with(Planner.DELEGATE_RULES_TEMPLATE)


@pytest.mark.asyncio
async def test_system_prompt_render_receives_game_rules() -> None:
    """plan() 渲染系统提示词时把拼好的玩法段作为 game_rules 变量传给模板。"""
    reg = ToolRegistry()
    reg.register_provider(_GuideProvider("game_x", "X 的玩法"))
    planner, _llm, prompt = _make_planner(chat_responses=[_resp()], registry=reg)

    await planner.plan([_msg("主播好")])

    kwargs = prompt.render.call_args.kwargs
    assert prompt.render.call_args.args[0] == "amaidesu_planner_react"
    assert kwargs["game_rules"] == "- X 的玩法"


# ---------------------------------------------------------------------------
# 点名 use_memory：编号前缀与回传形态
# ---------------------------------------------------------------------------

_BRIEF_WITH_COMMITMENT = "# 她记得的事（内部参考）\n- [约#7] 讲完鬼故事的结局"


def _commitment(commitment_id: int = 7) -> Commitment:
    return Commitment(
        id=commitment_id,
        what="讲完鬼故事的结局",
        quote="下次接着讲那个鬼故事的结局",
        status="open",
        created_at_ms=1,
        updated_at_ms=1,
    )


def _reply_with_memory(use_memory: Any) -> list:
    return [
        _resp(
            tool_calls=[
                _tc(
                    "streamer_reply",
                    {
                        "topic_summary": "t",
                        "is_novel": True,
                        "emotion": "happy",
                        "intensity": 0.5,
                        "use_memory": use_memory,
                    },
                )
            ]
        )
    ]


@pytest.mark.parametrize(
    ("speakers", "expected"),
    [
        ((("minecraft", "uuid-a"),), ("minecraft", "uuid-a")),
        ((("minecraft", "uuid-a"), ("minecraft", "uuid-b")), ("", "")),
        ((("minecraft", "uuid-a"), ("minecraft", "?")), ("", "")),
        ((), ("", "")),
    ],
    ids=["只有一位玩家开口", "两位玩家开口", "有认不出的人插话", "没人开口"],
)
async def test_her_line_answers_a_player_only_when_he_alone_spoke(speakers: tuple, expected: tuple) -> None:
    """本窗公屏聊天只来自一位核对过的玩家时，这一窗说的话记成在回应他；否则不猜。"""
    provider = MagicMock()
    provider.invoke = AsyncMock(return_value=_REPLY_RESULT)
    planner, _llm, _prompt = _make_planner(_reply_with_memory([]), reply_provider=provider)

    outcome = await planner.plan([_msg()], game_batch=GameEventBatch(chat_speakers=speakers))

    assert outcome.replied is True and outcome.reply_player == expected


@pytest.mark.parametrize(
    ("batch", "target", "expected"),
    [
        (True, "m1", ("", "")),
        (True, "[id:m1]", ("", "")),
        (True, "要不要一起", ("", "")),
        (True, "Alex 说的房子", ("minecraft", "uuid-a")),
        (False, "m1", ("minecraft", "uuid-a")),
    ],
    ids=[
        "按编号点了本窗的弹幕：对象是观众",
        "照提示词抄成 [id:…] 写法点了本窗的弹幕：对象是观众",
        "按原文片段点了本窗的弹幕：对象是观众",
        "拿公屏原话当目标、对不上弹幕：照旧在回应玩家",
        "本窗没有弹幕：点的不是弹幕，照旧在回应玩家",
    ],
)
async def test_her_line_answering_a_danmaku_is_not_recorded_as_answering_a_player(
    batch: bool, target: str, expected: tuple
) -> None:
    """回应目标对得上本窗一条弹幕（编号或原文）时，即使本窗只有一位玩家在公屏开口，也不记成在回应他。"""
    provider = MagicMock()
    provider.invoke = AsyncMock(return_value=_REPLY_RESULT)
    reply = [
        _resp(
            tool_calls=[
                _tc(
                    "streamer_reply",
                    {"topic_summary": "t", "is_novel": True, "emotion": "happy", "intensity": 0.5, "target": target},
                )
            ]
        )
    ]
    planner, _llm, _prompt = _make_planner(reply, reply_provider=provider)

    outcome = await planner.plan(
        [_msg("主播要不要一起挖矿")] if batch else [],
        game_batch=GameEventBatch(chat_speakers=(("minecraft", "uuid-a"),)),
    )

    assert outcome.replied is True and outcome.reply_player == expected


async def test_use_memory_accepts_single_item_list_and_renders_spoken_form() -> None:
    provider = MagicMock()
    provider.invoke = AsyncMock(return_value=_REPLY_RESULT)
    commitments = {7: _commitment()}

    async def lookup(commitment_id: int) -> Any:
        return commitments.get(commitment_id)

    planner, _llm, _prompt = _make_planner(
        _reply_with_memory(["约#7"]),
        reply_provider=provider,
        brief_provider=lambda: _BRIEF_WITH_COMMITMENT,
        brief_ids_provider=lambda: [7],
        commitment_lookup=lookup,
    )

    outcome = await planner.plan([_msg()])

    assert outcome.replied is True and outcome.memory_refs == ["约#7"]
    attachment = provider.invoke.await_args.args[0].attachment
    assert "讲完鬼故事的结局" in attachment.remembered
    assert "约#7" not in attachment.remembered, "说话档不带编号"


async def test_use_memory_rejects_archive_prefix_and_multi_values(loguru_capture: Any) -> None:
    """档#7 是档案行编号，不能借承诺 7 的编号混进来；认不出的前缀不猜；一次只能点名一条。"""
    provider = MagicMock()
    provider.invoke = AsyncMock(return_value=_REPLY_RESULT)
    commitments = {7: _commitment(), 8: _commitment(8)}

    async def lookup(commitment_id: int) -> Any:
        return commitments.get(commitment_id)

    responses = [
        *_reply_with_memory("档#7"),
        *_reply_with_memory("话#7"),
        *_reply_with_memory(["约#7", "约#8"]),
        *_reply_with_memory(42),
    ]
    planner, _llm, _prompt = _make_planner(
        responses,
        reply_provider=provider,
        brief_provider=lambda: _BRIEF_WITH_COMMITMENT,
        brief_ids_provider=lambda: [7, 8],
        commitment_lookup=lookup,
    )

    for _index in range(4):
        outcome = await planner.plan([_msg()])
        assert outcome.memory_refs == []

    messages = [record["message"] for record in loguru_capture.records]
    assert any("档案行编号 7 不在本窗" in message for message in messages)
    assert any("认不出" in message and "话#7" in message for message in messages)
    assert any("一次只能点名一条" in message for message in messages)
    assert any("类型认不出" in message for message in messages)


_ARCHIVE_LINE = ArchiveLine(
    id=41, live_session_id=3, text="Minecraft：女仆取名团子", quote="就叫团子", evidence_chat_id=5
)


async def _archive_lookup(line_id: int) -> Any:
    return _ARCHIVE_LINE if line_id == _ARCHIVE_LINE.id else None


async def test_use_memory_accepts_archive_line_in_the_brief() -> None:
    """简报里标着 档#41 的行能点名：台词拿到这一行的标题（不带编号），编号写法随台词回传。"""
    provider = MagicMock()
    provider.invoke = AsyncMock(return_value=_REPLY_RESULT)
    planner, _llm, _prompt = _make_planner(
        _reply_with_memory("档#41"),
        reply_provider=provider,
        brief_archive_ids_provider=lambda: [41],
        archive_lookup=_archive_lookup,
    )

    outcome = await planner.plan([_msg()])

    assert outcome.replied is True and outcome.memory_refs == ["档#41"]
    attachment = provider.invoke.await_args.args[0].attachment
    assert "女仆取名团子" in attachment.remembered
    assert "档#41" not in attachment.remembered and "就叫团子" not in attachment.remembered


async def test_use_memory_accepts_archive_line_from_this_batch_cards() -> None:
    """本批线索卡带来的档案行也能点名；不在简报也不在卡片里的编号按没填处理。"""
    cues = SimpleNamespace(text="【这一批想起来的事】\n- [档#41] 女仆取名团子", archive_ids=(41,), labels={})

    async def cues_provider(batch: Any, open_games: Any, game_players: Any) -> Any:
        return cues

    provider = MagicMock()
    provider.invoke = AsyncMock(return_value=_REPLY_RESULT)
    planner, _llm, _prompt = _make_planner(
        [*_reply_with_memory("档#41"), *_reply_with_memory("档#41")],
        reply_provider=provider,
        archive_lookup=_archive_lookup,
        memory_cues_provider=cues_provider,
    )

    assert (await planner.plan([_msg()])).memory_refs == ["档#41"]

    cues = SimpleNamespace(text="", archive_ids=(), labels={})
    assert (await planner.plan([_msg(mid="m2")])).memory_refs == [], "下一批没有这张卡、简报也没列，不认"
