"""streamer_social_fact —— 主播登记游戏社交事实的工具（Planner 决策层调用）。

一个动作：登记一条"当前游戏环境里人与人怎么相处"的事实，可以同时取代一条旧行。

**真工具**——注册到 ToolRegistry 供 Planner 调用（provider="streamer"，名单 ``["streamer"]``）；
只在社交事实仓储注入时注册，没注册时 Planner 的工具列表里没有它。

证据定位：模型填它看得见的消息号（弹幕行末、公屏行末的 ``[id:…]``），工具按"本场 + 消息号"查回
行号写进证据列。写库前全部校验在这里，任何一条不过就打回并说明原因，模型可以修正后重试：

1. 消息号在本场的弹幕观众行与公屏玩家行里恰好命中一行（她自己的话不能给自己作证）；
2. 命中行原文不为空（认不出是谁说的公屏只占位、没存原话，无法核对）；
3. ``quote`` 是命中行原文里一字不差的片段（压掉连续空白的写法也认）；
4. ``supersedes_id`` 指的行是现行、同一类，且没有被运营者锁定；
5. 同一类在当前存档里的现行行数不超过上限，满了要取代一条旧行。

成功回执带新行号与当前存档各类现行行数。不占配置段。
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, ClassVar, Dict, Iterable, Optional, Tuple

from src.agents.streamer.memory.settle_candidates import _verbatim
from src.modules.logging import get_logger
from src.modules.storage.models.social_facts import SOCIAL_FACT_KINDS
from src.modules.tools import ToolInvocation, ToolSpec
from src.modules.tools.models import ToolExecutionResult
from src.modules.tools.provider import BaseToolProvider

__all__ = [
    "MAX_FACTS_PER_KIND",
    "SocialFactToolProvider",
    "build_social_fact_tool_spec",
]

# 每一类在一个存档里至多几行现行事实（防刷屏；参考段总共只注入 12 行）
MAX_FACTS_PER_KIND = 4
# 一句事实与引文的长度上限
_TEXT_MAX_CHARS = 60
_QUOTE_MAX_CHARS = 80

_TOOL_NAME = "social_fact"
_PROVIDER = "streamer"

_KIND_LABELS: Dict[str, str] = {
    "setting": "环境形态",
    "members": "成员关系",
    "sharing": "共享惯例",
    "works": "进行中的事",
    "her_role": "她的角色",
    "open_question": "待弄清",
}

_DESCRIPTION = (
    "登记一条游戏社交事实：这个游戏环境里人与人现在怎么相处（谁在、什么关系、什么东西公用、谁在做什么事）。"
    "只登记玩家或观众明说的事，不从行为推测关系；依据必须是别人说的一句话，填那一行末尾 [id:…] 里的号，"
    "再抄一段原话。事实变了就用 supersedes_id 取代旧的那一行。"
)

_PARAMETERS: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "kind": {
            "type": "string",
            "enum": list(SOCIAL_FACT_KINDS),
            "description": (
                "类别：setting 这是个什么环境；members 谁在、什么关系；sharing 什么东西公用；"
                "works 进行中的事（每人或每件事一条）；her_role 她在这里做什么；open_question 看到了但还没定论的"
            ),
        },
        "text": {"type": "string", "description": "一句事实陈述，不带评价词（60 字以内）"},
        "quote": {"type": "string", "description": "依据那句话里的原文片段，照抄（80 字以内）"},
        "evidence_message_id": {
            "type": "string",
            "description": "依据那一行末尾 [id:…] 里的号（弹幕或游戏公屏里别人说的那一句）",
        },
        "supersedes_id": {
            "type": "integer",
            "description": "要取代的旧行行号（同一类、还在用的那一行）；新登记时不填",
        },
    },
    "required": ["kind", "text", "quote", "evidence_message_id"],
}


def build_social_fact_tool_spec() -> ToolSpec:
    """构造工具定义（声明名 social_fact；全名 streamer_social_fact 派生自 provider）。"""
    return ToolSpec(
        name=_TOOL_NAME,
        description=_DESCRIPTION,
        parameters_schema=_PARAMETERS,
        kind="sync",
        provider=_PROVIDER,
    )


_FULL_NAME = build_social_fact_tool_spec().full_name

#: 当前游戏环境：(游戏, 存档编号, 存档名)；没在玩游戏时游戏为空串
GameScopeProvider = Callable[[], Tuple[str, str, str]]


class SocialFactToolProvider(BaseToolProvider):
    """streamer_social_fact 的 Provider（满足 ``ToolProvider`` 协议）。

    依赖构造注入：社交事实仓储、进行中场次回调（"本场"是哪一场）、当前游戏环境回调
    （弹幕作证时事实挂在眼下在玩的游戏与存档上；公屏作证时取那句公屏所在的游戏与存档）。
    """

    category: ClassVar[str] = "framework"

    def __init__(
        self,
        *,
        repo: Any,
        session_provider: Callable[[], Optional[int]],
        game_scope_provider: GameScopeProvider,
        on_written: Optional[Callable[[], Awaitable[None]]] = None,
    ) -> None:
        self._repo = repo
        self._session_provider = session_provider
        self._game_scope_provider = game_scope_provider
        # 写成后的回调（让索引任务立刻补一轮等）；失败不影响回执
        self._on_written = on_written
        self._logger = get_logger("SocialFactTool")

    @property
    def name(self) -> str:
        return _PROVIDER

    def list_tools(self) -> Iterable[ToolSpec]:
        return [build_social_fact_tool_spec()]

    def _bounce(self, tool_name: str, reason: str) -> ToolExecutionResult:
        self._logger.info(f"社交事实登记打回：{reason}")
        return ToolExecutionResult(tool_name=tool_name, success=False, error_message=reason)

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        tool_name = invocation.tool_name
        if tool_name != _FULL_NAME:
            return ToolExecutionResult(
                tool_name=tool_name, success=False, error_message=f"SocialFactToolProvider 不处理工具 '{tool_name}'"
            )
        try:
            return await self._register(invocation)
        except Exception as exc:  # noqa: BLE001 - 工具边界：读写库失败转成失败回执，不反噬决策轮
            self._logger.exception(f"社交事实登记失败: {exc}")
            return ToolExecutionResult(
                tool_name=tool_name, success=False, error_message=f"登记失败：{type(exc).__name__}: {exc}"
            )

    async def _register(self, invocation: ToolInvocation) -> ToolExecutionResult:
        tool_name = invocation.tool_name
        args = invocation.arguments or {}
        kind = str(args.get("kind") or "").strip()
        text = str(args.get("text") or "").strip()
        quote = str(args.get("quote") or "").strip()
        message_id = str(args.get("evidence_message_id") or "").strip()
        if message_id.startswith("[id:") and message_id.endswith("]"):
            message_id = message_id[4:-1].strip()
        elif message_id.startswith("id:"):
            message_id = message_id[3:].strip()

        if kind not in SOCIAL_FACT_KINDS:
            return self._bounce(tool_name, f"kind 必须是这六类之一：{'、'.join(SOCIAL_FACT_KINDS)}")
        if not text or len(text) > _TEXT_MAX_CHARS:
            return self._bounce(tool_name, f"text 要写一句事实，{_TEXT_MAX_CHARS} 字以内")
        if not quote:
            return self._bounce(tool_name, "quote 必填：抄依据那句话里的原文片段")
        if not message_id:
            return self._bounce(tool_name, "evidence_message_id 必填：填依据那一行末尾 [id:…] 里的号")
        session_id = self._session_provider()
        if session_id is None:
            return self._bounce(tool_name, "现在不在直播，没有本场的话可以作依据")

        hits = await self._repo.find_evidence(live_session_id=int(session_id), message_id=message_id)
        if not hits:
            return self._bounce(
                tool_name,
                f"本场找不到消息号 {message_id} 的那句话（只认弹幕和游戏公屏里别人说的话，你自己说的不算依据）",
            )
        if len(hits) > 1:
            return self._bounce(tool_name, f"消息号 {message_id} 在本场对上了不止一句话，换一句作依据")
        (hit,) = hits
        if not hit.content.strip():
            return self._bounce(
                tool_name, f"消息号 {message_id} 这句没有存原话（认不出是谁说的），无法核对，换一句作依据"
            )
        verified = _verbatim(quote, hit.content, _QUOTE_MAX_CHARS)
        if verified is None:
            return self._bounce(
                tool_name, f"quote 不是消息号 {message_id} 那句话里一字不差的片段（或超过 {_QUOTE_MAX_CHARS} 字）"
            )

        if hit.source == "game_chat" and hit.game:
            game, save_id, save_name = hit.game, hit.save_id, hit.save_name
        else:
            game, save_id, save_name = self._game_scope_provider()

        supersedes_id = args.get("supersedes_id")
        target_id: Optional[int] = None
        if supersedes_id not in (None, ""):
            try:
                target_id = int(supersedes_id)
            except (TypeError, ValueError):
                return self._bounce(tool_name, "supersedes_id 要填旧行的行号（整数）")
            old = await self._repo.get_fact(fact_id=target_id)
            if old is None:
                return self._bounce(tool_name, f"没有行号 {target_id} 这一行")
            if old.human_locked:
                return self._bounce(tool_name, f"行号 {target_id} 是运营者定下的，不能改也不能取代")
            if old.status != "active":
                return self._bounce(tool_name, f"行号 {target_id} 已经被取代过了，取代现在还在用的那一行")
            if old.kind != kind:
                return self._bounce(
                    tool_name, f"行号 {target_id} 是{_KIND_LABELS.get(old.kind, old.kind)}，取代只能在同一类之间"
                )

        counts = await self._repo.count_facts_by_kind(game=game, save_id=save_id)
        if target_id is None and counts.get(kind, 0) >= MAX_FACTS_PER_KIND:
            oldest = await self._oldest(kind, game, save_id)
            hint = f"，最旧的是行号 {oldest}" if oldest is not None else ""
            return self._bounce(
                tool_name,
                f"{_KIND_LABELS[kind]}已经有 {MAX_FACTS_PER_KIND} 条了{hint}：用 supersedes_id 取代一条旧的再登记",
            )

        new_id = await self._repo.insert_fact(
            kind=kind,
            text=text,
            quote=verified,
            game=game,
            save_id=save_id,
            save_name=save_name,
            evidence_chat_id=hit.row_id if hit.source == "chat" else None,
            evidence_game_chat_id=hit.row_id if hit.source == "game_chat" else None,
            supersedes_id=target_id,
            created_session_id=int(session_id),
            updated_session_id=int(session_id),
        )
        if new_id is None:
            return self._bounce(tool_name, "没登记成：旧行在这期间已被改动或锁定，重新看一眼现有事实再试")
        counts = await self._repo.count_facts_by_kind(game=game, save_id=save_id)
        self._logger.info(f"社交事实已登记：#{new_id}（{kind}）{text}" + (f"，取代 #{target_id}" if target_id else ""))
        if self._on_written is not None:
            try:
                await self._on_written()
            except Exception as exc:  # noqa: BLE001 - 写后回调失败不影响登记结果
                self._logger.warning(f"社交事实写后回调失败（已忽略）: {exc}", exc=True)
        return ToolExecutionResult(
            tool_name=tool_name,
            success=True,
            content=f"已登记行号 {new_id}" + (f"，取代了行号 {target_id}" if target_id else ""),
            structured_content={"id": new_id, "superseded": target_id, "counts": counts},
        )

    async def _oldest(self, kind: str, game: str, save_id: str) -> Optional[int]:
        """当前存档里这一类最旧的一条现行行（超限时引导取代它）；运营者锁定的不算。"""
        rows = await self._repo.list_active_facts(game=game, save_id=save_id)
        candidates = [row for row in rows if row.kind == kind and not row.human_locked]
        if not candidates:
            return None
        return int(min(candidates, key=lambda row: (row.updated_at_ms, row.id)).id)
