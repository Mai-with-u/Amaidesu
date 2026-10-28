"""身体事件通知路由：每类事件走哪条通道、对模型说什么话，一处定义。

MinecraftAgent 把 Mod 的身体事件（生存急事、死亡、跟随、天色、计时）转给
模型与主播。"哪类事件唤醒谁、说什么"的知识收进本模块一张路由表：
一行一个种类，通道与文案常量同处，Agent 的执行器只认通道、不认种类
（Mod 加一种事件时只在这里加一行）。

通道（执行器在 Agent，持有消息队列与唤醒事件）：
- ``carry_sky``：挂进消息队列不唤醒，队列里只留最新一条（天色）；
- ``wake_even_idle``：排成闲着也开批的通知并唤醒（计时到点、Mod 决策）；
- ``inject_if_busy``：任务进行中才注入唤醒（跟随、身体急事、死亡）；
- ``attention_event``：任务进行中经注意流分类摘要转告主播，按（种类,原文）
  去重——未知种类也走这里。

文案组装是纯函数：模板渲染、need_unhandled 的重复指回、分类摘要的开头
句式，都可脱离 Agent 直测。递话来源标签与身体事件上下文上限也在此。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Dict, List, Literal, Optional

from src.agents.minecraft.attention_matrix import classify, summarize
from src.agents.minecraft.repeated_notice import notice_present, repeat_reference

__all__ = [
    "BodyEventRoute",
    "MAX_BATCH_BODY_EVENTS",
    "PROMPT_SOURCE_LABELS",
    "GAME_NOT_CONNECTED",
    "attention_notice",
    "body_notice_content",
    "route_body_event",
]

# 递话来源标签：运营在控制面直接说的话与主播决策循环递来的补充分开标注。
PROMPT_SOURCE_LABELS = {"operator": "[运营原话]", "planner-react": "[主播补充]"}

# 本批任务期间保留的身体事件条数上限（上报携带的任务上下文，多了只会淹没重点）
MAX_BATCH_BODY_EVENTS = 5

# 需要告诉任务的身体事件模板：正文 = 模板 + Mod 原话
_BODY_NOTICE_TEMPLATES = {
    "need_unhandled": ("[身体] {message}。这是观察不是命令：身体自己处理不了它，需要时调整计划或上报主播。"),
    "character_died": (
        "[身体] {message}。Mod 会挂出一条死亡恢复决策等你回答，按问题里给的选项选（选重生才会复活）；"
        "目标保留着，复活后接着做。复活后先 maicraft_observe(what=self) 看一眼自己"
        "（位置变了，背包可能也变了），再决定要不要调整计划或上报主播。"
    ),
}

# 跟随中的处境变化：跟随还在进行，角色自己会等、会找，模型据此决定要不要换许可重下、取消或跟主播说一声。
_FOLLOW_NOTICE = (
    "[跟随] {message}。这是观察不是命令：跟随还在进行，跟不过去会原地等、跟丢了会自己找；"
    "需要时换许可重下（例如放开 change_blocks）、取消，或跟主播说一声。"
)

# 天色变了（天黑、天亮、下雨、雨停）：身体照常做手上的事，夜里能弄到床会自己找空当去睡、
# 弄不到床又打不过会挖坑封顶熬到天亮；为一次天色把等结果的模型叫醒只会白花一次推理。
_SKY_NOTICE = (
    "[天色] {message}。这是观察不是命令：身体照常做手上的事，夜里能弄到床会自己找空当去睡，"
    "弄不到床又打不过会挖坑封顶熬到天亮；要专门安排睡觉、回家或赶在天黑前收尾就调整计划，不用就接着做。"
)

# 计时到点：模型自己用 timer 设的"过 N 秒叫我"到了，Mod 那句话原样带上；
# 计时到点就用掉了，还要再等得重新设，说清这一点免得模型以为它还挂着。
# 不能像跟随那样只在任务进行中才叫：模型设完计时多半去干别的，只在任务中叫就永远等不到。
_TIMER_NOTICE = (
    "[计时] {message}。这是你自己用 timer 设的到点提醒：按备注去看一眼、做决定；"
    "这个计时已经用掉了，还要再等就重新设一个。"
)

# 游戏没连上时拒收委派的原因：经委派工具原样回给派活的一方，它据此等连上再派。
GAME_NOT_CONNECTED = (
    "游戏没连上：maicraft 一个工具都没有（Minecraft 客户端没开，或 MaiCraft 的 MCP 没连上），没法操作角色；连上后再派"
)


@dataclass(frozen=True, slots=True)
class BodyEventRoute:
    """一个身体事件种类的路由决策：走哪条通道、给模型的话、附带处置。

    Attributes:
        kind: 事件种类（Mod 报的 ``kind`` 字段）。
        channel: 通道名（见模块 docstring）。
        template: 给模型的文案模板（``{message}`` 占位）；``attention_event``
            通道不用模板，正文走注意流分类摘要。
        dedup_content: 正文与历史/待读队列一字不差重复时只留一句指回
            （need_unhandled 的逐来源排查说明常有几千字，复读无益）。
        publish_material: Mod 原话同时发主播素材（长文由素材发布器先概述）。
        urgent_marker: 收尾句留给紧接着的暂停通知，解释主任务为什么停在半路。
        execution_context_prefix: 摘要开头点明"执行任务时碰上的"。
        already_resolved: 素材标记急事已自行收尾（观众视角无需再渲染）。
    """

    kind: str
    channel: Literal["carry_sky", "wake_even_idle", "inject_if_busy", "attention_event"]
    template: Optional[str] = None
    dedup_content: bool = False
    publish_material: bool = False
    urgent_marker: bool = False
    execution_context_prefix: bool = False
    already_resolved: bool = False


# 路由表：一行一个种类。加一种身体事件 = 加一行（模板就地可写）。
_BODY_EVENT_ROUTES: Dict[str, BodyEventRoute] = {
    # 天色：挂队列不唤醒、只留最新，给观众的叙述由注意流采集器出
    "sky_changed": BodyEventRoute(kind="sky_changed", channel="carry_sky", template=_SKY_NOTICE),
    # 跟随：任务进行中才告诉模型，给观众的叙述由注意流采集器出
    "follow_status": BodyEventRoute(kind="follow_status", channel="inject_if_busy", template=_FOLLOW_NOTICE),
    # 计时到点：闲着也叫醒（模型给自己定的回头看一眼）
    "timer_due": BodyEventRoute(kind="timer_due", channel="wake_even_idle", template=_TIMER_NOTICE),
    # 处理不了的需求：任务进行中通知模型；Mod 原话发主播素材（长文先概述）
    "need_unhandled": BodyEventRoute(
        kind="need_unhandled",
        channel="inject_if_busy",
        template=_BODY_NOTICE_TEMPLATES["need_unhandled"],
        dedup_content=True,
        publish_material=True,
    ),
    # 角色死亡：任务进行中通知模型（死亡恢复决策另由决策回调唤醒）；原话发主播素材
    "character_died": BodyEventRoute(
        kind="character_died",
        channel="inject_if_busy",
        template=_BODY_NOTICE_TEMPLATES["character_died"],
        publish_material=True,
    ),
    # 急事开始：任务进行中转告主播，开头点明是做事时碰上的
    "temporary_task_started": BodyEventRoute(
        kind="temporary_task_started", channel="attention_event", execution_context_prefix=True
    ),
    # 急事收尾：收尾句留给暂停通知；转告主播并标记已自行收尾
    "temporary_task_finished": BodyEventRoute(
        kind="temporary_task_finished", channel="attention_event", urgent_marker=True, already_resolved=True
    ),
}

# 未知种类的兜底路由：与急事同通道（任务进行中转告主播，分类不认识就按 unknown 摘要）
_UNKNOWN_KIND_ROUTE = BodyEventRoute(kind="", channel="attention_event")


def route_body_event(kind: str) -> BodyEventRoute:
    """按种类取路由；未知种类走 attention_event 兜底（不静默丢弃）。"""
    route = _BODY_EVENT_ROUTES.get(kind)
    if route is not None:
        return route
    return replace(_UNKNOWN_KIND_ROUTE, kind=kind)


def body_notice_content(route: BodyEventRoute, message: str, *, history: List[Any], queue: Any) -> str:
    """身体通知交给模型的正文。

    处理不了的需求（饿了没吃上的说明会列出每个来源没弄到的原因，常有几千字）和对话历史里、
    或待读队列里一字不差的那条重复时，只留一句指回前面那条；前面那条已被历史整理换成摘要、
    或根本还没读到时，指过去没有意义，完整展示。其余通知（角色死亡等）每次都是新事实，原样给。
    """
    content = (route.template or "{message}").format(message=message)
    if not route.dedup_content or not notice_present(content, history, queue):
        return content
    return repeat_reference(message)


def attention_notice(route: BodyEventRoute, message: str) -> str:
    """attention_event 通道给主播的正文：白话化的 Mod 原话，急事开始时点明任务语境。"""
    spoken = summarize(classify(route.kind) or "unknown", route.kind, message)
    return f"执行任务时{spoken}" if route.execution_context_prefix else spoken
