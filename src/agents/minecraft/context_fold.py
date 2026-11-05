"""Minecraft 后台折叠：身体照常行动时，另发一次与行动步同前缀的请求，请模型把用完的区间折成摘要。

为什么放在后台：折叠要模型写摘要，放进行动路径，这一步角色就站着不动。后台请求与
行动步用同一份系统说明、同一套工具声明、同一段历史，只在末尾多一条整理请求，输入
几乎全部命中前缀缓存，只多付摘要的输出；写好的折叠在下一步开始前换上。

整理请求里模型只能调用折叠工具；它调任何游戏动作都丢弃不执行——整理请求不能让角色
做出动作。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence

from src.modules.context import FoldRange, FoldRequest
from src.modules.llm.payload import Response

__all__ = [
    "FOLD_GUIDE_FALLBACK",
    "FOLD_TOOL",
    "RECALL_HINT",
    "RECALL_MAX_CHARS",
    "RECALL_TOOL",
    "STOW_MIN_TOKENS",
    "FoldLines",
    "FoldProposal",
    "fold_lines",
    "fold_request_message",
    "parse_fold_calls",
    "propose_folds",
]

FOLD_TOOL = "minecraft_context_fold"
RECALL_TOOL = "minecraft_context_recall"
# 折叠块替身与收起占位里告诉模型怎么拿回原文的一句话
RECALL_HINT = f"原文可用 {RECALL_TOOL} 取回"
# 提示词模板不可用时系统提示词附上的折叠说明（模板正文见 amaidesu_minecraft_context_fold）
FOLD_GUIDE_FALLBACK = (
    "消息开头的 [m0012] 是消息编号；[已折叠 b3 …] 是折好的摘要，[原文已收起 b5 …] 是先收起的原文，"
    f"需要原文时用 {RECALL_TOOL} 读回。{FOLD_TOOL} 只在【宿主整理请求】里用，行动中不要调用。"
)
# 一次取回最多返回的原文字数：再长就让模型带关键词只看命中的片段
RECALL_MAX_CHARS = 20_000

# 到预算线收起旧记录时，单条至少这么大才收：更小的记录省下的还不如换上去的占位
STOW_MIN_TOKENS = 500
# 请过一次后，历史至少再涨预算的这一比例才再请：模型刚折完或说没东西可折时，马上再请只会白付输出
_GROWTH_SHARE = 0.1
# 单段区间至少占预算的这一比例才值得折（太小的区间省下的抵不过一次缓存失效），且不低于下限
_MIN_RANGE_SHARE = 0.03
_MIN_RANGE_FLOOR = 1_000


@dataclass(frozen=True)
class FoldLines:
    """按当前预算换算出的折叠节奏：到哪条线开始请、再涨多少再请、一段至少多大。"""

    line: int
    growth: int
    min_range: int


@dataclass
class FoldProposal:
    """模型在整理请求里给出的折叠；``ignored`` 是被丢弃的其他工具调用名，``error`` 是请求本身失败的原因。"""

    requests: List[FoldRequest] = field(default_factory=list)
    ignored: List[str] = field(default_factory=list)
    error: str = ""


def fold_lines(budget: int, fold_ratio: float) -> FoldLines:
    """预算 × 折叠线比例开始请模型折；再涨预算的一成再请；一段至少占预算的 3%（不少于 1000）。"""
    return FoldLines(
        line=int(budget * fold_ratio),
        growth=int(budget * _GROWTH_SHARE),
        min_range=max(_MIN_RANGE_FLOOR, int(budget * _MIN_RANGE_SHARE)),
    )


def fold_request_message(ranges: Sequence[FoldRange], tokens: int, max_chars: int) -> Dict[str, Any]:
    """整理请求：说明这不是游戏指令、列出可折区间，再讲清摘要怎么写。只挂在后台请求末尾，不进历史。"""
    listing = "\n".join(
        f"- {item.start}–{item.end}：约 {item.tokens} token"
        + (f"，调用过 {'、'.join(item.tools)}" if item.tools else "")
        for item in ranges
    )
    return {
        "role": "user",
        "content": (
            f"[宿主整理请求] 这不是游戏指令。身体正在照常行动，这次回复不要调用任何游戏工具，"
            f"只用 {FOLD_TOOL} 整理上下文。\n"
            f"现在历史约 {tokens} token，下面这些区间可以折叠（从早到晚）：\n{listing}\n\n"
            "挑出其中已经用完的部分折成摘要：可以一次折几段、也可以只折一段里的一部分，起止写消息编号"
            "（[已折叠 b3 …] 这类旧块可以用块号 b3 当起止），区间之间不能重叠。"
            "还要逐字用到的内容不要折：正在等结果的目标、刚查到还没用上的数据、主播最新的要求。\n\n"
            "摘要会替代原文，之后读它的人不看原文也要能接着干：\n"
            "- 原样照抄：坐标、物品和方块 ID、目标/观察/产物编号、错误码和报错原文；引用主播原话时带上消息编号。\n"
            "- 写清做了什么、为什么、结果怎样；失败和结果未知分开写，没确认的不写成做成了。\n"
            "- 取舍连同理由一起留：选了哪个办法、为什么不用另一个。\n"
            "- 区间里还没完成的要求单独写一行“未完成：……”，带消息编号。\n"
            "- 丢掉已经用完的查询原文、重复的背包和状态查询、来回试探的过程；"
            "走不通的路只留一行“试过 X，不行，因为 Y”。\n"
            "- 任务状态写成“截至这段的情况”，不写成要重新执行的指令。\n"
            f"- 用短条目，每段不超过 {max_chars} 字。\n"
            "没有值得折的区间就不调用工具，直接回复“无需整理”。"
        ),
    }


def parse_fold_calls(response: Response) -> FoldProposal:
    """从整理请求的响应里取出折叠调用；其他工具调用一律丢弃并记下名字，参数不合形的段跳过。"""
    proposal = FoldProposal()
    for call in response.tool_calls or []:
        if call.name != FOLD_TOOL:
            proposal.ignored.append(call.name)
            continue
        folds = call.arguments.get("folds") if isinstance(call.arguments, dict) else None
        for item in folds if isinstance(folds, list) else []:
            if not isinstance(item, dict):
                continue
            start, end, summary = item.get("start"), item.get("end"), item.get("summary")
            if isinstance(start, str) and isinstance(end, str) and isinstance(summary, str):
                proposal.requests.append(FoldRequest(start, end, summary))
    return proposal


async def propose_folds(
    llm: Any,
    history: Sequence[Dict[str, Any]],
    tools: List[Dict[str, Any]],
    ranges: Sequence[FoldRange],
    tokens: int,
    *,
    max_chars: int,
    profile: str,
) -> FoldProposal:
    """发一次与行动步同前缀的整理请求，返回模型给出的折叠（不改历史，由调用方择机套用）。"""
    request = [*history, fold_request_message(ranges, tokens, max_chars)]
    response = await llm.generate(request, profile=profile, tools=tools)
    if not response.success:
        return FoldProposal(error=response.error or "整理请求失败")
    return parse_fold_calls(response)
