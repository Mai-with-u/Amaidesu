"""DelegationWorkbench - Planner 的"手头的事"工作台（Agent 内部件，不是工具）。

职责（Planner 内部拆分，不进 ToolRegistry、无 ToolSpec、不注册）：
- 状态承载：每个执行 Agent 最近一次受理的委派、最近几次补充过的要求、
  最近几轮说出口的打算、本决策窗的工具回执
- 拦截规则：``framework_delegate`` / ``framework_prompt`` 两个注册工具名的
  受理逻辑（参数整备 + 受理后的状态迁移）收成名字 → 规则的映射表；
  LLM 视角零变化——这两个名字仍是 Planner 内部拦截的工具，名单语义不变
- 状态迁移："accepted 才替换同一执行者的旧目标""delivered 才记补充要求"
  等规则长在这里，不再散在工具调用流的名字特判分支里

Planner 收缩为薄 ReAct 循环：名字命中 :meth:`handles` 的调用整体委托给
:meth:`handle`，其余工具仍走 Planner 的通用调用路径。
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

from src.agents.streamer.planner_render import (
    Delegation,
    Intent,
    Relay,
    compose_delegate_instruction,
    compose_relay_content,
    goal_lines,
    render_observation,
    same_json_document,
)
from src.agents.streamer.scene_card import GoalLine
from src.modules.logging import get_logger
from src.modules.tools.models import ToolExecutionResult, ToolInvocation
from src.modules.utils.time_utils import now_ms

__all__ = ["DelegationWorkbench", "observation_data"]

#: 委派型游戏的两个框架工具名（Planner 内部拦截的注册工具，名字与
#: src.modules.agents.control 的注册面一致）
FRAMEWORK_DELEGATE_TOOL: str = "framework_delegate"
FRAMEWORK_PROMPT_TOOL: str = "framework_prompt"


def observation_data(result: ToolExecutionResult) -> Dict[str, Any]:
    """成功的工具结果 → 观察数据 dict（结构化结果优先，content 文本一并透传）。

    content 文本透传：信息获取型工具（web_search / web_fetch_url 等）的产出
    就在 content，只回 ``{"ok": true}`` 会让 LLM 看不到任何内容；空 content
    不带键——动作执行型工具的观察形态维持原样。MCP 工具常把同一份结构化
    结果再序列化成正文返回一遍，完全相同时只给一份，避免上下文翻倍。
    """
    data = dict(result.structured_content) if isinstance(result.structured_content, dict) else {}
    data.setdefault("ok", True)
    if result.content and "content" not in data and not same_json_document(result.content, result.structured_content):
        data["content"] = result.content
    return data


@dataclass(frozen=True)
class _InterceptRule:
    """一个被拦截工具名的受理规则：调用前整备参数，成功受理后做状态迁移。"""

    prepare: Callable[[Dict[str, Any], List[Dict[str, Any]]], Tuple[Dict[str, Any], str]]
    settle: Callable[[Dict[str, Any], str, Dict[str, Any]], None]


class DelegationWorkbench:
    """ "手头的事"状态机：委派受理、递话回执、最近打算的记账与拦截规则。

    非线程安全；仅在 StreamerAgent 的单一 asyncio 事件循环内使用（随 Planner）。
    """

    def __init__(
        self,
        tool_registry: Any,
        *,
        intents_max: int,
        relays_max: int,
        action_recorder: Optional[Callable[[str, str], None]] = None,
        delegation_status_provider: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
    ) -> None:
        self._tool_registry = tool_registry
        # 每个执行 Agent 最近一次受理的委派：原话、时间、身体是否已上报。实测半小时委派
        # 27 次、目标来回反转——主播看不到身体手上正做着什么，就会把每条弹幕都改派成新目标
        self._delegations: Dict[str, Delegation] = {}
        # 最近几轮自己说出口的打算（话题 + 指引）。对话历史里只有最终台词，看不出
        # "我已经连着几轮在讲同一件事"——实测主播连续十分钟句句提掉血，每轮都把它当新消息开口
        self._recent_intents: Deque[Intent] = deque(maxlen=intents_max)
        # 最近几次给手上的游戏活补充过的要求（主播自己的原话，不含附带的来源对话）：
        # 看得到自己刚交代过什么，没有新要求时才不会把同样的话再补一遍
        self._recent_relays: Deque[Relay] = deque(maxlen=relays_max)
        # 本决策窗的工具回执（名字, 观察数据）：reply 交接时的状态跃迁事实块从这里取
        # 观察工具的关键字段，随 ReAct 循环逐步累积，begin_round() 在窗入口清空
        self._round_receipts: List[Tuple[str, Any]] = []
        # 委派受理后把"我在哪个游戏里开始做什么"记进时间线（经 PlannerEnv 构造期注入）；
        # 参考段只留每个游戏最近一次委派，时间线里留下整场的先后
        self._action_recorder = action_recorder
        # 委派的机器状态查询（读任务账本）：任务号 → {"status", "updated_at_ms",
        # "snapshot"} 或 None（终态已移除/查不到）
        self._delegation_status_provider = delegation_status_provider

        # 拦截规则表：工具名 → 受理规则。新拦截名在此登记，handle/settle 不加分支
        self._rules: Dict[str, _InterceptRule] = {
            FRAMEWORK_DELEGATE_TOOL: _InterceptRule(self._prepare_delegate, self._settle_delegate),
            FRAMEWORK_PROMPT_TOOL: _InterceptRule(self._prepare_prompt, self._settle_prompt),
        }

        self.logger = get_logger("DelegationWorkbench")

    # ==================== 状态只读面（渲染与消费方） ====================

    @property
    def delegations(self) -> Dict[str, Delegation]:
        """每个执行 Agent 最近一次受理的委派（活引用，渲染层只读消费）。"""
        return self._delegations

    @property
    def recent_intents(self) -> Deque[Intent]:
        """最近几轮说出口的打算（活引用）。"""
        return self._recent_intents

    @property
    def recent_relays(self) -> Deque[Relay]:
        """最近几次补充过的要求（活引用）。"""
        return self._recent_relays

    @property
    def round_receipts(self) -> List[Tuple[str, Any]]:
        """本决策窗累积的工具回执（reply 交接的状态事实块素材）。"""
        return self._round_receipts

    @property
    def delegation_status_provider(self) -> Optional[Callable[[str], Optional[Dict[str, Any]]]]:
        """委派的机器状态查询（渲染手头的事时合并任务账本事实）。"""
        return self._delegation_status_provider

    def handles(self, name: str) -> bool:
        """该工具名是否由工作台拦截受理（Planner 据此分派）。"""
        return name in self._rules

    # ==================== 记账动词 ====================

    def begin_round(self) -> None:
        """决策窗入口：清空本窗回执（委派/递话/打算是跨窗记忆，不清）。"""
        self._round_receipts = []

    def note_receipt(self, name: str, data: Dict[str, Any]) -> None:
        """记一条本窗工具回执（通用调用路径的成功结果由 Planner 送来）。"""
        self._round_receipts.append((name, data))

    def note_intent(self, intent: Intent) -> None:
        """记一轮说出口的打算（话说出口了才记；表达失败或被丢弃的不算）。"""
        self._recent_intents.append(intent)

    def note_game_report(self, game: str, report_kind: str) -> None:
        """游戏侧上报（交付/卡住待定夺）到达：把对应执行 Agent 手头那次委派标成已上报。

        游戏 Agent 以自己的注册名作为上报的 game 字段，按名字对上即可。
        """
        delegation = self._delegations.get(game)
        if delegation is not None and report_kind in {"delivery", "escalation"}:
            delegation.report_kind = report_kind
            delegation.reported_ms = now_ms()

    def current_goals(self) -> List[GoalLine]:
        """手上的事（嘴的现场卡用）：每个游戏最近一次开始做的事、原话（已脱敏）与进展。"""
        return goal_lines(self._delegations)

    # ==================== 拦截受理 ====================

    async def handle(
        self,
        name: str,
        args: Dict[str, Any],
        *,
        round_id: str = "",
        caller_profile: str = "",
        source_dialogue: Optional[List[Dict[str, Any]]] = None,
        relay_without_input: bool = False,
        on_result: Optional[Callable[[str, ToolExecutionResult], None]] = None,
    ) -> str:
        """受理一个被拦截的工具调用，返回观察 JSON 文本。

        流程：无新输入闸（仅递话）→ 参数整备（记下主播自己的原话、附来源对话）
        → 经 ToolRegistry 调用 → 成功受理则按规则做状态迁移并记本窗回执。
        ``on_result(name, result)`` 让调用方在结果返回时吸收副作用（如截图挂尾），
        成败结果都会到达。
        """
        if self._tool_registry is None:
            return json.dumps({"ok": False, "error": "tool_registry 未注入"}, ensure_ascii=False)
        if name == FRAMEWORK_PROMPT_TOOL and relay_without_input:
            # 没有新输入的主动窗不补充要求：游戏里的进展以游戏侧回执为准，这一窗只管说话。
            return json.dumps(
                {
                    "ok": False,
                    "error": "这一窗没有新的观众、运营或游戏聊天输入，手上的游戏活不用补充要求；"
                    "游戏进展以游戏侧回执为准，直接说话即可。",
                },
                ensure_ascii=False,
            )
        rule = self._rules[name]
        dialogue = source_dialogue or []
        args, own_text = rule.prepare(args, dialogue)
        try:
            result = await self._tool_registry.invoke(
                ToolInvocation(
                    tool_name=name,
                    arguments=args,
                    source="planner-react",
                    round_id=round_id,
                    caller_profile=caller_profile,
                )
            )
        except Exception as e:
            self.logger.warning(f"工具 '{name}' 执行异常: {e}", exc=True)
            return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)
        if on_result is not None:
            on_result(name, result)
        if not result.success:
            return render_observation({"ok": False, "error": result.error_message or "工具执行失败"})
        data = observation_data(result)
        rule.settle(args, own_text, data)
        self._round_receipts.append((name, data))
        return render_observation(data)

    # ----- 参数整备（调用前） -----

    def _prepare_delegate(
        self, args: Dict[str, Any], source_dialogue: List[Dict[str, Any]]
    ) -> Tuple[Dict[str, Any], str]:
        """委派参数整备：记下主播开始做事的原话（附来源对话之前），做什么由主播拿主意。"""
        own_instruction = str(args.get("instruction") or "")
        if source_dialogue and isinstance(args.get("instruction"), str) and args["instruction"].strip():
            # 游戏侧以本次目标为准，原话只补物品名写法和禁用条件（取舍规则见组装函数）
            args = dict(args)
            args["instruction"] = compose_delegate_instruction(args["instruction"], source_dialogue)
        return args, own_instruction

    def _prepare_prompt(
        self, args: Dict[str, Any], source_dialogue: List[Dict[str, Any]]
    ) -> Tuple[Dict[str, Any], str]:
        """递话参数整备：记下主播补充要求的原话（附来源对话之前）。"""
        own_relay = str(args.get("content") or "")
        if source_dialogue and isinstance(args.get("content"), str) and args["content"].strip():
            # 补充要求同样附上本窗观众原话，游戏侧能看到逐字说法，不只剩一层转述
            args = dict(args)
            args["content"] = compose_relay_content(args["content"], source_dialogue)
        return args, own_relay

    # ----- 状态迁移（成功受理后） -----

    def _settle_delegate(self, args: Dict[str, Any], own_instruction: str, data: Dict[str, Any]) -> None:
        """委派受理回执：accepted 才替换同一执行者手上的旧目标——只保留最近一次，
        状态从"还在做"重新开始；并把"我开始做什么"记进时间线。"""
        if not data.get("accepted"):
            return
        agent = str(data.get("executor") or args.get("agent") or "")
        self._delegations[agent] = Delegation(
            agent=agent, instruction=own_instruction, task_id=str(data.get("task_id") or ""), at_ms=now_ms()
        )
        if self._action_recorder is not None:
            try:
                self._action_recorder(agent, own_instruction)
            except Exception as exc:  # noqa: BLE001 - 记录失败不影响已受理的委派
                self.logger.warning(f"委派记录写入时间线失败：{exc}", exc=True)

    def _settle_prompt(self, args: Dict[str, Any], own_relay: str, data: Dict[str, Any]) -> None:
        """递话回执：delivered 且原话非空才记——被拒收的没交代到，不能当成"已经说过"。"""
        if not (data.get("delivered") and own_relay.strip()):
            return
        agent = str(data.get("executor") or args.get("agent") or "")
        self._recent_relays.append(Relay(at_ms=now_ms(), agent=agent, content=own_relay))
