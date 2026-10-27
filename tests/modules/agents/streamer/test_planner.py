"""Planner ReAct 循环：工具列表、reply 收尾 / 自然终止 / 超步、registry 路由与观察回灌、降级、上下文与记忆、消息构成"""

from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.streamer.planner import Planner, PlannerConfig, PlannerEnv
from src.agents.streamer.room_state import RoomState
from src.agents.streamer.timeline import GameEventBatch
from src.modules.llm.payload import Response, ToolCall
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
    profile_repo: Any | None = None,
    viewer_repo: Any | None = None,
    context_enabled: bool = True,
    max_steps: int = 8,
    elapsed_live_provider: Any | None = None,
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
        profile_repo=profile_repo,
        viewer_repo=viewer_repo,
        context_enabled=context_enabled,
        env=PlannerEnv(reply_provider_factory=lambda: prov, elapsed_live_provider=elapsed_live_provider),
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


def _viewer_repo(former: Dict[int, List[str]] | None = None) -> MagicMock:
    """观众仓储替身：外部 ID "u_<n>" 换算成观众 n（其余为 1），昵称"老王"，曾用名按 ``former`` 给。"""

    async def find(*, platform: str, external_id: str) -> int:
        return int(external_id[2:]) + 100 if external_id.startswith("u_") and external_id[2:].isdigit() else 1

    async def former_names(*, viewer_id: int, current_name: str) -> List[str]:
        return (former or {}).get(viewer_id, [])

    repo = MagicMock()
    repo.find_viewer_id = AsyncMock(side_effect=find)
    repo.get_viewer = AsyncMock(return_value={"user_name": "老王"})
    repo.list_former_names = AsyncMock(side_effect=former_names)
    return repo


async def test_person_profiles_are_injected_without_placeholders_and_failures_do_not_block() -> None:
    profile_repo = MagicMock()
    profile_repo.get_profile_text = AsyncMock(return_value="老粉，喜欢工作台话题")
    planner, llm, _prompt = _make_planner([_resp()], profile_repo=profile_repo, viewer_repo=_viewer_repo({1: ["王哥"]}))
    await planner.plan([_msg("工作台")])
    # 昵称从 viewers 实时取，画像行与弹幕侧"老王: ..."同源可关联；改过名的带上以前的名字
    assert "- 老王（以前叫王哥）: 老粉，喜欢工作台话题" in _reference_message(llm)["content"]

    # 无画像的候选不占位，其余照常注入
    async def profile(*, viewer_id: int) -> str:
        return "" if viewer_id == 107 else "有画像"

    profile_repo.get_profile_text = AsyncMock(side_effect=profile)
    portraitless = _msg("随便聊聊")
    portraitless.user.id = "u_7"
    section = await planner._collect_person_profiles([portraitless, _msg("有画像的人说话")])
    assert section.count("- ") == 1 and "有画像" in section

    # 本批全部观众画像完整注入，不丢尾部经历
    profile_repo.get_profile_text = AsyncMock(return_value="画" * 900)
    batch = [_msg(f"第{i}条") for i in range(5)]
    for i, msg in enumerate(batch):
        msg.user.id = f"u_{i}"
    section = await planner._collect_person_profiles(batch)
    assert section.startswith("（内部参考") and section.count("- ") == 5 and section.count("画" * 900) == 5

    # 同一位观众的两个外部 ID（换过数据源）只注入一次
    twins = [_msg("账号 ID 说话"), _msg("开放平台 ID 说话")]
    twins[0].user.id, twins[1].user.id = "uid-x", "open-x"
    section = await planner._collect_person_profiles(twins)
    assert section.count("- ") == 1

    profile_repo.get_profile_text = AsyncMock(side_effect=RuntimeError("db down"))  # 画像查询异常静默降级
    planner, _llm, _prompt = _make_planner([_resp()], profile_repo=profile_repo, viewer_repo=_viewer_repo())
    assert (await planner.plan([_msg()])).silent_reason == "natural"


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


async def test_session_recap_goes_first_in_the_reference_and_is_omitted_when_absent() -> None:
    """上场回顾进参考段，排在环节描述与直播间快照之前（整场不变的背景放最前）；没有回顾则整段省略。"""
    planner, llm, _prompt = _make_planner([_resp(), _resp()])

    await planner.plan([_msg()], rundown_text="## 开场", session_recap="【2026-10-13 的直播】这场我挖到了钻石")
    reference = _reference_message(llm)["content"]
    assert "这场我挖到了钻石" in reference
    assert reference.index("## 上场回顾-内部参考") < reference.index("## 环节描述")

    await planner.plan([_msg()])
    assert "上场回顾" not in _reference_message(llm)["content"]


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
