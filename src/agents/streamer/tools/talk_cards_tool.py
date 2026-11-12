"""talk_draw_cards —— 主播主动抽话题卡的工具（主播内部工具，可见名单只有主播）。

观众点名"再抽一张提问箱"、或她自己想换个话头时，用这个工具从话题匣里抽出本场
还没用过的卡；抽卡只是"翻出来看看"，不标已用——她真拿某张卡开口时，在
``streamer_reply`` 里填 ``use_card_id``，那时才算本场用过。

注册形态：provider="talk"、声明名 draw_cards → 全名 ``talk_draw_cards``。
"""

from __future__ import annotations

import json
from typing import Any, Dict

from src.modules.logging import get_logger
from src.modules.storage.models.talk_card import TALK_CARD_KINDS
from src.modules.tools import ToolInvocation
from src.modules.tools.models import ToolExecutionResult, ToolSpec
from src.modules.tools.provider import ToolProvider, as_tool_impl, make_provider_from_specs

from ..talk.deck import TopicDeck, render_cards

__all__ = ["DRAW_CARDS_MAX_LIMIT", "build_talk_cards_tool_provider"]

# 一次最多抽几张：抽多了就成了念菜单
DRAW_CARDS_MAX_LIMIT = 5
_DEFAULT_LIMIT = 3

_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "kind": {
            "type": "string",
            "enum": list(TALK_CARD_KINDS),
            "description": "只抽某一类卡：marshmallow=提问箱，episode=往场趣事，opinion=二选一态度；不填则各类都抽",
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": DRAW_CARDS_MAX_LIMIT,
            "description": f"最多抽几张（默认 {_DEFAULT_LIMIT}）",
        },
    },
}

_DRAW_SPEC = ToolSpec(
    name="draw_cards",
    description=(
        "从自备话题匣里抽出本场还没用过的话题卡（提问箱问题、往场趣事、二选一态度）。"
        "观众点名要你抽提问箱、或你想换个话头时用；抽出来只是看看，"
        "真拿某张卡开口时在 streamer_reply 里填 use_card_id。"
    ),
    parameters_schema=_PARAMETERS_SCHEMA,
    kind="sync",
    provider="talk",
)

logger = get_logger("TalkDrawCardsTool")


def _parse_limit(raw: Any) -> int:
    """抽卡张数夹到 1..上限；乱填按默认张数。"""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_LIMIT
    return max(1, min(DRAW_CARDS_MAX_LIMIT, value))


async def _draw(deck: TopicDeck, args: Dict[str, Any]) -> Dict[str, Any]:
    """执行抽卡，返回观察字典：抽到的卡（含 id 供 use_card_id 引用）或空池说明。"""
    kind = args.get("kind")
    if kind is not None and kind not in TALK_CARD_KINDS:
        return {"ok": False, "error": f"未知卡种 {kind!r}，可选：{', '.join(TALK_CARD_KINDS)}"}
    limit = _parse_limit(args.get("limit", _DEFAULT_LIMIT))
    cards = await deck.available_cards(kind=kind, limit=limit)
    if not cards:
        return {"ok": True, "cards": [], "note": "话题匣里本场没有可用的卡了，换个话头或接弹幕"}
    return {
        "ok": True,
        "cards": [{"id": c.id, "kind": c.kind, "title": c.title, "body": c.body, "asker": c.asker} for c in cards],
        "text": render_cards(cards),
    }


def build_talk_cards_tool_provider(deck: TopicDeck) -> ToolProvider:
    """把话题匣包成注册形态的 ToolProvider（简单工具路径），由主播 Agent 注册。"""

    async def _run(inv: ToolInvocation) -> ToolExecutionResult:
        try:
            payload = await _draw(deck, dict(inv.arguments or {}))
        except Exception as exc:
            # 读卡池失败按业务失败回灌：她读到原因换个话头，不当执行故障计健康分
            logger.exception(f"抽话题卡失败: {exc}")
            payload = {"ok": False, "error": f"话题匣暂时读不出来：{exc}"}
        text = json.dumps(payload, ensure_ascii=False)
        ok = bool(payload.get("ok"))
        return ToolExecutionResult(
            tool_name=_DRAW_SPEC.full_name,
            success=ok,
            content=text,
            structured_content=payload,
            error_message="" if ok else str(payload.get("error", "")),
            failure_kind=None if ok else "business",
        )

    return make_provider_from_specs(
        "talk",
        [(_DRAW_SPEC, as_tool_impl(_DRAW_SPEC.full_name, _run))],
        category="framework",
    )
