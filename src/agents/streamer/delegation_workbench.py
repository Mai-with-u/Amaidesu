"""DelegationWorkbench - Planner 的"手头的事"工作台（Agent 内部件，不是工具）。

职责（Planner 内部拆分，不进 ToolRegistry、无 ToolSpec、不注册）：
- 状态承载：每个执行 Agent 最近一次受理的委派、最近几次补充过的要求、
  最近几轮说出口的打算、本决策窗的工具回执
- 拦截规则：``framework_delegate`` / ``framework_prompt`` 两个注册工具名的
  受理逻辑（参数整备 + 受理后的状态迁移）收成名字 → 规则的映射表；
  LLM 视角零变化——这两个名字仍是 Planner 内部拦截的工具，名单语义不变
- 状态迁移："accepted 才替换同一执行者的旧目标""delivered 才记补充要求"
  等规则长在这里，不再散在工具调用流的名字特判分支里

Planner 只保留薄 ReAct 循环：名字命中 :meth:`handles` 的调用整体委托给
:meth:`handle`，其余工具仍走 Planner 的通用调用路径。
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Deque, Dict, List, Optional, Sequence, Set, Tuple

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
from src.modules.tools.tasks import IN_PROGRESS_TASK_STATES
from src.modules.utils.time_utils import now_ms

__all__ = ["DelegationWorkbench", "observation_data"]

#: 委派型游戏的两个框架工具名（Planner 内部拦截的注册工具，名字与
#: src.modules.agents.control 的注册面一致）
FRAMEWORK_DELEGATE_TOOL: str = "framework_delegate"
FRAMEWORK_PROMPT_TOOL: str = "framework_prompt"
#: 经工作台受理的调用的来源名：受理核心把它记成委派的发起方，落库后续播据此认出"这件是我派的"
PLANNER_CALL_SOURCE: str = "planner-react"


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
        # 滑出上面那份的话题名与轮数（按最近一次滑出的先后排）：只留几十轮的话，同一个段子隔半小时
        # 又能当新话题讲——第 58 场"拴绳背了一整场""空智能板"各讲了六七遍
        self._earlier_topics: Dict[str, int] = {}
        # 最近几次给手上的游戏活补充过的要求（主播自己的原话，不含附带的来源对话）：
        # 看得到自己刚交代过什么，没有新要求时才不会把同样的话再补一遍
        self._recent_relays: Deque[Relay] = deque(maxlen=relays_max)
        # 每个执行 Agent 最近一次递达补充要求的时刻：判断"她求助之后我答复过没有"用。上面那份只留最近几条，
        # 给别的游戏补过几次话就会把这条挤掉，单靠它会把"答复过了"忘成"还没答复"，冷场窗又能递话
        self._last_relay_ms: Dict[str, int] = {}
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
    def earlier_topics(self) -> Dict[str, int]:
        """本场更早聊过、已滑出最近几轮的话题名 → 轮数（活引用；先滑出的在前）。"""
        return self._earlier_topics

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

    def reset(self) -> None:
        """开播清场：已了结的委派连同补充过的要求清掉，已聊话题与本窗回执全部清空。

        这几样大多只在一场之内有意义：上一场正常下播时游戏侧已经把委派结算掉，新场次的对话
        历史也从空白开始。留着它们，新的一场开头她会看到"两小时前在游戏里开始做某事"、把上一场
        聊过的话题当成刚聊过。例外是任务账本上还在做（含停着等定夺）的委派——开播前调试时派的、
        上一场没走下播留下的，游戏那边可能还在做；清掉它她不知道手上有事，再派新的会被拒，
        开场先碰一次壁。原地删改而不是换新容器：渲染层持有的是活引用。
        """
        kept = {agent for agent, delegation in self._delegations.items() if self._in_flight(delegation)}
        for agent in sorted(kept):
            self.logger.info(f"开播时 {agent} 手上的委派账面还在做，保留：{self._delegations[agent].instruction}")
        touched = set(self._delegations) | {relay.agent for relay in self._recent_relays} | set(self._last_relay_ms)
        self._drop(touched - kept)
        self._recent_intents.clear()
        self._earlier_topics.clear()
        self._round_receipts = []

    def forget_game(self, game: str) -> None:
        """某个游戏换了存档：这个游戏已了结的委派与补充过的要求是在旧世界里交代的，清掉。

        新世界里没有那座房子、那条矿道，留着它们她会接着催一件在眼前世界里无从做起的事。
        账面还在做的委派保留：游戏 Agent 换存档时会自己看眼前再决定接着做还是上报，在那之前
        身体手上确实还有这件事，清掉它她再派新的会被拒。已聊话题与本窗回执不分游戏，保留。
        """
        delegation = self._delegations.get(game)
        if delegation is not None and self._in_flight(delegation):
            self.logger.info(f"{game} 换了存档，手上的委派账面还在做，保留：{delegation.instruction}")
            return
        self._drop({game})

    def note_receipt(self, name: str, data: Dict[str, Any]) -> None:
        """记一条本窗工具回执（通用调用路径的成功结果由 Planner 送来）。"""
        self._round_receipts.append((name, data))

    def note_intent(self, intent: Intent) -> None:
        """记一轮说出口的打算（话说出口了才记；表达失败或被丢弃的不算）。

        最近几轮放满时，最老那轮的话题名挪进"更早聊过"：清单只剩名字和轮数，整场都看得到。
        """
        full = self._recent_intents.maxlen is not None and len(self._recent_intents) >= self._recent_intents.maxlen
        if full and self._recent_intents:
            tag = self._recent_intents[0].topic_tag.strip()
            if tag:
                # 重新插入排到最后：按最近一次滑出的先后排
                self._earlier_topics[tag] = self._earlier_topics.pop(tag, 0) + 1
        self._recent_intents.append(intent)

    def note_game_report(self, game: str, report_kind: str, delegation_ids: Sequence[str] = ()) -> None:
        """游戏侧上报（交付/卡住待定夺/停了没交付）到达：把对应执行 Agent 手头那次委派标成已上报。

        游戏 Agent 以自己的注册名作为上报的 game 字段，按名字找到手头那件；上报带了任务号时还要
        对得上——上一件的迟到上报不改写新派的这件。停了没交付只认点名到手头这件的，已经交付或
        卡住的不降级：交付后被计时叫醒开的新任务停下，不能把做完的活改写成"不确定做没做完"。
        """
        delegation = self._delegations.get(game)
        if delegation is None or report_kind not in {"delivery", "escalation", "undelivered"}:
            return
        if delegation_ids and delegation.task_id not in delegation_ids:
            return
        if report_kind == "undelivered" and (
            delegation.task_id not in delegation_ids or delegation.report_kind in {"delivery", "escalation"}
        ):
            return
        delegation.report_kind = report_kind
        delegation.reported_ms = now_ms()

    def restore_interrupted(self, agent: str, *, instruction: str, task_id: str, at_ms: int) -> None:
        """续播认领：重启前 ``agent`` 手上那件委派被打断了，记回手头的事并标成被打断。

        重启后内存里的手头的事是空的：不记回来，她不知道手上那件事断了，会接着说"还在做"，
        观众问起也答不上；记成被打断，参考段写着做到哪不确定，她可以重新派或者如实说。
        这一刻手上已经有更晚开始的事就不覆盖。
        """
        current = self._delegations.get(agent)
        if current is not None and current.at_ms > at_ms:
            return
        self._delegations[agent] = Delegation(
            agent=agent,
            instruction=instruction,
            task_id=task_id,
            at_ms=at_ms,
            report_kind="interrupted",
            reported_ms=now_ms(),
        )

    def current_goals(self) -> List[GoalLine]:
        """手上的事（嘴的现场卡用）：每个游戏最近一次开始做的事、原话（已脱敏）与进展。"""
        return goal_lines(self._delegations)

    def _in_flight(self, delegation: Delegation) -> bool:
        """任务账本上这件委派是否还在做（受理、在跑、停着等定夺）；没装账本、查不到或已结算都不算。"""
        if self._delegation_status_provider is None or not delegation.task_id:
            return False
        record = self._delegation_status_provider(delegation.task_id)
        return record is not None and record.get("status") in IN_PROGRESS_TASK_STATES

    def _drop(self, agents: Set[str]) -> None:
        """清掉这些执行者手上的委派、补充过的要求与"最近一次答复时刻"（原地删改，渲染层持有活引用）。

        三样随委派一起留或清：留下的委派还在等我拿主意时，"这次求助之后答复过没有"照旧按原来的
        答复时刻判，开播或换存档不会让闸门对同一次求助重新放行，也不会把没答复的算成答复过。
        """
        for agent in agents:
            self._delegations.pop(agent, None)
            self._last_relay_ms.pop(agent, None)
        relays = [relay for relay in self._recent_relays if relay.agent not in agents]
        if len(relays) != len(self._recent_relays):
            self._recent_relays.clear()
            self._recent_relays.extend(relays)

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

        流程：无新输入闸（仅递话；对象停着等我拿主意时放行）→ 参数整备（记下主播自己的原话、附来源对话）
        → 经 ToolRegistry 调用 → 成功受理则按规则做状态迁移并记本窗回执。
        ``on_result(name, result)`` 让调用方在结果返回时吸收副作用（如截图挂尾），
        成败结果都会到达。
        """
        if self._tool_registry is None:
            return json.dumps({"ok": False, "error": "tool_registry 未注入"}, ensure_ascii=False)
        if name == FRAMEWORK_PROMPT_TOOL and relay_without_input and not self._awaits_my_decision(args):
            # 没有新输入的主动窗不补充要求：游戏里的进展以游戏侧回执为准，这一窗只管说话。
            # 例外是她在游戏里卡住、停着等我拿主意：这时补充要求就是我的决定，不放行就成了死结——
            # 派新事因"手上有一件没做完"被拒，补充又因"没有新输入"被拦，主播嘴上说了改主意，游戏那边一直停着。
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
                    source=PLANNER_CALL_SOURCE,
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

    def awaits_decision(self, agent: str) -> bool:
        """``agent`` 手上的委派此刻是否停着等我拿主意，而且这次求助之后我还没递达过答复。

        顺序：她在游戏里试过几条路都不通 → 上报卡住、停手 → 账面记成等定夺 → 我这一窗补的要求就是答复，
        闸门放行；答复递达一次就算回应过了，后面的空窗照常拦，冷场窗不能借"还在等"反复递话。
        "在等"有任务账本时以账面状态为准，没装账本时看身体有没有上报卡住；求助时刻取上报时刻，
        没有上报记录时取账面最近一次变动。``agent`` 为空串时看手上任意一件委派。
        """
        delegations = list(self._delegations.values()) if not agent else []
        if agent in self._delegations:
            delegations = [self._delegations[agent]]
        for delegation in delegations:
            record = None
            if self._delegation_status_provider is not None and delegation.task_id:
                record = self._delegation_status_provider(delegation.task_id)
                waiting = record is not None and record.get("status") == "waiting_for_decision"
            else:
                waiting = delegation.report_kind == "escalation"
            if not waiting:
                continue
            asked_ms = delegation.reported_ms or int((record or {}).get("updated_at_ms") or 0)
            answered = self._last_relay_ms.get(delegation.agent, -1) >= asked_ms
            if not answered:
                return True
        return False

    def _awaits_my_decision(self, args: Dict[str, Any]) -> bool:
        """补充要求的对象（参数里点名的执行者，没点名时看任意一件）是否在等我拿主意、还没回应过。"""
        return self.awaits_decision(str(args.get("agent") or ""))

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
        at_ms = now_ms()
        self._recent_relays.append(Relay(at_ms=at_ms, agent=agent, content=own_relay))
        self._last_relay_ms[agent] = at_ms
