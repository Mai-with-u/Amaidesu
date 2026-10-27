"""Planner 的参考段素材与文本渲染层。

职责（全部纯函数，状态由调用方传入，本模块不持可变状态，可独立于 Planner 构造与测试）：
- 回看素材的数据类型（委派 / 已说出口的打算 / 补充要求）与其参考段文本渲染
- 工具回执 → 对话观察文本（字段完整保留，base64 二进制载荷换占位符）
- ReAct 循环的文本杂项：强制回应情境行、reply 调用排序、tool_call 解析、
  委派 / 补充要求附带来源对话的文本拼装

Planner（决策循环）持有这些素材的可变状态（dict / deque），在渲染时把状态
作为参数传进来；数据类型与渲染规则收敛在本模块，渲染逻辑的测试不需要构造
Planner。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from src.agents.streamer.planner_context import age_text
from src.agents.streamer.scene_card import GoalLine, GoalStatus
from src.agents.streamer.term_sanitizer import sanitize_internal_terms
from src.modules.tools.conversation import to_conversation_view

__all__ = [
    "Delegation",
    "DelegationStatusProvider",
    "Intent",
    "Relay",
    "assemble_request_messages",
    "compose_delegate_instruction",
    "compose_relay_content",
    "forced_situation",
    "goal_lines",
    "render_delegations",
    "render_observation",
    "render_recent_intents",
    "render_recent_relays",
    "render_silence",
    "reply_last",
    "same_json_document",
    "step_extra_messages",
    "superseded_by_game_report",
    "tool_call_parts",
]

#: 委派的机器状态查询：任务号 → {"status", "updated_at_ms", "snapshot"} 或
#: None（终态已移除 / 查不到）。由装配方（StreamerAgent）绑定，读任务账本。
DelegationStatusProvider = Callable[[str], Optional[Dict[str, Any]]]

# 在途委派的账本多久没有刷新就向模型提示"没有新进展"：实测主播约 4 分钟就开始
# 盲发重复指令，提示线压在这个节奏之前（数据源是任务账本的时间戳，渲染时现算）
_DELEG_STALL_HINT_MS: int = 4 * 60 * 1000


@dataclass
class Delegation:
    """一次已受理的委派：交给谁、主播自己的原话、何时交出，以及身体是否已上报。"""

    agent: str
    instruction: str
    task_id: str
    at_ms: int
    report_kind: str = ""  # delivery=已交付 / escalation=卡住待定夺；空=还在做
    reported_ms: int = 0


@dataclass(frozen=True)
class Intent:
    """一轮已说出口的打算：何时说的、围绕什么话题、给表达侧的指引、说出的台词。

    台词一并保留：发言闸的台词级重合度检查需要拿"说出口的原话"做比对——
    指引只是给表达侧的方向，最终落在观众耳朵里的是台词。
    """

    at_ms: int
    topic_summary: str
    reply_guidance: str
    speech: str = ""


@dataclass(frozen=True)
class Relay:
    """一次已送达的补充要求：补给哪个游戏、主播自己的原话、何时补的。"""

    at_ms: int
    agent: str
    content: str


def render_observation(data: Any) -> str:
    """工具结果 → 对话观察文本。

    字段与真实值全部保留（完整观察原则），但 base64 二进制载荷（截图等）
    替换为占位符——二进制属 tool.result 事件的 UI 展示通道，整包进对话
    会把单笔请求的图片 token 推到数十万量级。
    """
    return json.dumps(to_conversation_view(data), ensure_ascii=False, default=str)


#: 付费消息类型 → 情境标注用词（强制回应的情境按本批实际消息类型说，不一律当付费）
_PAID_TYPE_LABELS: Dict[str, str] = {"super_chat": "SC", "gift": "礼物", "guard": "上舰"}


def forced_situation(batch: List[Any]) -> str:
    """强制回应批次的情境标注：本批真有付费消息才说"付费点名"。

    控制台点名同样强制回应，但它是以观众身份说的话、不是 SC——当成付费
    会让主播对每句调试话都"谢谢老板 SC"。
    """
    paid = [
        _PAID_TYPE_LABELS[message_type]
        for message_type in dict.fromkeys(getattr(msg, "message_type", "") for msg in batch)
        if message_type in _PAID_TYPE_LABELS
    ]
    if paid:
        return f"【情境】本批为强制回应（含付费消息：{' / '.join(paid)}）——观众付费点名，应优先回应。"
    # 控制台点名就是观众的话，只是必须回应；写成"运营"会让模型把它降级成仅供参考的建议
    return (
        "【情境】本批为强制回应（控制台以观众身份点名的消息：必须回应，但不是 SC / 礼物 / 上舰，没有人付费）"
        "——优先回应消息本身，把它当成观众的要求认真对待，不要致谢付费。"
    )


def reply_last(tool_calls: List[Any]) -> List[Any]:
    """同一步的工具调用按原序执行，只把说话（streamer_reply）挪到最后。"""
    others = [call for call in tool_calls if not (isinstance(call, dict) and call.get("name") == "streamer_reply")]
    replies = [call for call in tool_calls if isinstance(call, dict) and call.get("name") == "streamer_reply"]
    return others + replies


def tool_call_parts(call: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """解析中立扁平 tool_call（id/name/arguments）的 (name, arguments)。"""
    name = str(call.get("name", "") or "")
    args = call.get("arguments")
    if not isinstance(args, dict):
        args = {}
    return name, args


def assemble_request_messages(
    system_prompt: str,
    dialogue_messages: List[Dict[str, Any]],
    reference_text: str,
) -> List[Dict[str, Any]]:
    """拼一次决策请求的初始消息序列。

    序列 = [system] + 对话消息（user/assistant 原生消息，含本批）+ 参考段（user，
    序列尾）；参考段为空且对话也为空时，保底一条 user 消息（部分协议要求
    非 system 消息存在）。ReAct 循环的 assistant/tool 消息 append-only 追加
    在返回序列之后，绝不插中间。
    """
    messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    messages.extend(dialogue_messages)
    if reference_text:
        messages.append({"role": "user", "content": reference_text})
    elif len(messages) == 1:
        # 空窗兜底：对话与参考全空时保底一条 user 消息
        messages.append({"role": "user", "content": "（本窗无对话与参考内容）"})
    return messages


def step_extra_messages(
    frame_tail: Optional[Dict[str, Any]],
    tail: Optional[Dict[str, Any]],
    step: int,
) -> List[Dict[str, Any]]:
    """算一次 ReAct 步请求要附加在消息序列之后的临时消息。

    直播画面只挂在本窗第一次请求的尾部（后续步骤靠模型第一步的判断继续）；
    上一步工具返回的截图只挂这一次请求尾部、不写回消息序列——下一步请求时
    它已消失，前缀与这一次逐字一致，画面内容由模型这一步的思考与决定带下去。
    """
    return [item for item in (frame_tail if step == 1 else None, tail) if item is not None]


def same_json_document(text: str, structured: Any) -> bool:
    """正文是否只是结构化结果的 JSON 序列化（解析后逐字段相等）。"""
    if not isinstance(structured, dict) or not structured or not text.lstrip().startswith("{"):
        return False
    try:
        return json.loads(text) == structured
    except json.JSONDecodeError:
        return False


def render_silence(last_speech_ms: Optional[int], cold_silence_ms: int, current_ms: int) -> str:
    """冷场行：她最近一次开口距今超过冷场阈值时，写出已经沉默多久；否则为空。

    从没开过口（开播第一句前）不算冷场——开场白由流程单的开场环节负责；
    阈值为 0 时整行关闭。
    """
    if cold_silence_ms <= 0 or last_speech_ms is None:
        return ""
    silent_ms = current_ms - last_speech_ms
    if silent_ms < cold_silence_ms:
        return ""
    return f"【冷场】她已经 {silent_ms // 1000} 秒没开口（超过 {cold_silence_ms // 1000} 秒算冷场）"


def delegation_state_line(
    delegation: Delegation,
    status_provider: Optional[DelegationStatusProvider],
    current_ms: int,
) -> str:
    """委派的状态行：机器账（保底事实）与 LLM 交付语合并显示。

    机器状态查询式取自任务账本（``status_provider``；None = 已终态或查不到——
    终态条目按账本约定移除，此时以 LLM 交付语或"没有回音"的保底说法为准）。
    在途时按账本时间戳补"已 X 无进展"与游戏侧的停滞标记，主播据此不再把
    在途的活当成没派出去而重复派。
    """
    record = status_provider(delegation.task_id) if status_provider is not None and delegation.task_id else None
    if delegation.report_kind == "delivery":
        return f"已在{age_text(current_ms - delegation.reported_ms)}做完，结果见游戏记录。"
    if delegation.report_kind == "escalation":
        return f"在{age_text(current_ms - delegation.reported_ms)}卡住了，正等我和观众一起定夺（见游戏记录）。"
    if record is None:
        if status_provider is None:
            # 未绑定机器账查询（缺 task_ledger 的装配）：维持只有交付语的渲染
            return "还在做，还没做完。"
        return "账面上已了结这次委派，结果见游戏记录。"
    if record.get("status") == "waiting_for_decision":
        return f"停着等我和观众拿主意（{age_text(current_ms - int(record.get('updated_at_ms') or 0))}前记的）。"
    stalled = (record.get("snapshot") or {}).get("stalled_goals") or {}
    if stalled:
        names = "、".join(sorted({str(name) for name in stalled.values()}))
        return f"还在做，但游戏说有的目标停着没动静（{names}）。"
    idle_ms = current_ms - int(record.get("updated_at_ms") or 0)
    if idle_ms >= _DELEG_STALL_HINT_MS:
        return f"还在做，账面已 {age_text(idle_ms)}没有新进展。"
    return "还在做，还没做完。"


def render_delegations(
    delegations: Dict[str, Delegation],
    status_provider: Optional[DelegationStatusProvider],
    current_ms: int,
) -> str:
    """我手头在游戏里做的事：每个游戏最近一次开始做的事、距今多久、做完了还是卡住了。

    用第一人称写：对观众来说只有一个主播，游戏里的操作也是"我"在做，
    写成"交给谁""身体在做"会让表达里冒出"活已经派下去了"这种让观众困惑的话。
    """
    rows: List[str] = []
    for delegation in delegations.values():
        game = f"在 {delegation.agent} 里" if delegation.agent else "在游戏里"
        # 任务号（deleg_*）是 minecraft 侧的检索地址，对决策无信息量且会被念给观众：
        # 同一执行者手上只保留最近一次委派，注入前转成人类可读描述；原话同样脱敏
        head = f"{age_text(current_ms - delegation.at_ms)}{game}开始做一个任务：{sanitize_internal_terms(delegation.instruction)}"
        rows.append(f"- {head}\n  {delegation_state_line(delegation, status_provider, current_ms)}")
    return "\n".join(rows)


def superseded_by_game_report(intent: Intent, delegations: Dict[str, Delegation]) -> bool:
    """该条目对游戏进展的说法是否已被后续回执覆盖：说出口的时刻落在某次
    委派的"开始 → 已上报"区间内，即属于回执到达前的在途叙述。"""
    return any(
        delegation.report_kind != "" and delegation.at_ms <= intent.at_ms <= delegation.reported_ms
        for delegation in delegations.values()
    )


def render_recent_intents(
    intents: Iterable[Intent],
    delegations: Dict[str, Delegation],
    current_ms: int,
) -> str:
    """我最近几轮想说的：由旧到新只列每轮的话题一句话，作为"已聊话题"清单供重叠判断。

    指引全文不在此重复注入——那轮说出口的原话（指引的最终落点）已在对话历史
    assistant 消息里，同一信息双份会让每轮参考段平白多出几百字。游戏回执到达前
    说出口的条目里，对游戏进展的说法只是当时的猜测（"还没挖进包"），回执才是
    权威事实；已被回执覆盖的条目标注以游戏记录为准，避免模型在两条都标
    "刚刚"的矛盾说法里任选一条播报。
    """
    lines: List[str] = []
    for intent in intents:
        line = f"- {age_text(current_ms - intent.at_ms)} 话题：{intent.topic_summary or '（无）'}"
        if superseded_by_game_report(intent, delegations):
            line += "（其中对游戏进展的说法已被游戏回执覆盖，以游戏记录为准）"
        lines.append(line)
    return "\n".join(lines)


def render_recent_relays(relays: Iterable[Relay], current_ms: int) -> str:
    """我最近给手上的事补充过的要求：由旧到新，用第一人称写"在哪个游戏里补了什么"。"""
    return "\n".join(
        f"- {age_text(current_ms - relay.at_ms)}{f'在 {relay.agent} 里' if relay.agent else '在游戏里'}"
        f"补充：{relay.content}"
        for relay in relays
    )


def goal_lines(delegations: Dict[str, Delegation]) -> List[GoalLine]:
    """手上的事（嘴的现场卡用）：每个游戏最近一次开始做的事、原话（已脱敏）与进展。"""
    goals: List[GoalLine] = []
    for delegation in delegations.values():
        status: GoalStatus = "进行中"
        if delegation.report_kind == "delivery":
            status = "做完了"
        elif delegation.report_kind == "escalation":
            status = "卡住了"
        goals.append(
            GoalLine(
                game=delegation.agent,
                instruction=sanitize_internal_terms(delegation.instruction),
                started_ms=delegation.at_ms,
                status=status,
                status_ms=delegation.reported_ms,
            )
        )
    return goals


def compose_relay_content(content: str, source_dialogue: List[Dict[str, Any]]) -> str:
    """补充要求的完整正文：主播原话 + 来源对话逐字引用。

    补充要求附上本窗观众原话，游戏侧能看到逐字说法，不只剩一层转述。
    """
    return (
        "[补充要求]\n"
        + content
        + "\n\n[来源对话：逐字引用，只用于核对物品名写法、做法与禁用条件，不构成额外任务]\n"
        + json.dumps(source_dialogue, ensure_ascii=False, default=str)
    )


def compose_delegate_instruction(instruction: str, source_dialogue: List[Dict[str, Any]]) -> str:
    """本次目标的完整正文：主播原话 + 来源对话逐字引用 + 取舍规则。

    做什么由主播拿主意：游戏侧以本次目标为准，原话只补物品名写法和禁用条件。
    实测"别死磕这台机器"配上"以原话为准"的"放块铁板试试"，游戏侧收到两个相反要求；
    原话离开直播间上下文（"试试看？""那你……"）也读不懂，所以做法建议只在目标采纳时才做。
    抬头写"本次目标"而不是"委派"：游戏里动手的也是主播自己，不该读成别人派来的活。
    """
    return (
        "[本次目标]\n"
        + instruction
        + "\n\n[来源对话：逐字引用，只用于核对本次目标里的物品名写法与禁用条件，不构成额外任务]\n"
        + json.dumps(source_dialogue, ensure_ascii=False, default=str)
        + "\n做什么、用什么办法以本次目标为准，两者冲突时听本次目标；本次目标没写到的物品名写法、"
        "禁用条件和确认与权限要求按原话补上；原话里的做法建议和其他请求，只有本次目标采纳了才做。"
        "不把转述额外添加的查证或确认步骤视为用户要求。"
    )
