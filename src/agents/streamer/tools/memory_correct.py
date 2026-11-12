"""streamer_memory_correct —— 主播场内更正记忆的工具（D2-4，Planner 决策层调用）。

给决策环节一个当场纠错出口：对常驻简报里的某一行档案（``档#N``）或某条惦记的承诺（``约#N``）
标记 ``done``（做完了）/ ``invalid``（不成立了）/ ``wrong``（记错了），并附本场证据：
- ``game_receipt``：本场委派终态摘要或游戏事件/速览原文的逐字片段；
- ``player_chat``：本场玩家在游戏公屏所说原文的逐字片段（挂外键，删玩家随之级联删除）；
- ``operator``：本场运营提醒原文的逐字片段；
- ``self_seen``：她自己在游戏画面或速览里亲眼确认的事实（当场生效，跨场整理时仍须台词或记录佐证）。

观众弹幕一律不收作场内更正依据（回执提示观众的话进跨场记忆须经人工批准）。
更正只在本场生效：档案行当场附注"（本场更正：……）"，未了结承诺当场标了结或作废（记来源，人工可撤），
下播整理把本场更正作为必须处理的输入。
"""

from __future__ import annotations

import re
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    ClassVar,
    Dict,
    Iterable,
    List,
    Literal,
    Optional,
    Sequence,
    Tuple,
)

from src.agents.streamer.memory.textmatch import verbatim_quote
from src.modules.logging import get_logger
from src.modules.storage.repos.memory_corrections import CORRECTION_VERDICT_LABELS, format_correction_note
from src.modules.tools import ToolInvocation, ToolSpec
from src.modules.tools.models import ToolExecutionResult
from src.modules.tools.provider import BaseToolProvider

if TYPE_CHECKING:
    from src.modules.storage.repos import (
        ChatRepo,
        CommitmentRepo,
        EventRepo,
        GamePlayerRepo,
        MemoryCorrectionRepo,
    )

__all__ = [
    "MemoryCorrectionToolProvider",
    "build_memory_correct_tool_spec",
    "parse_memory_target",
]

_TOOL_NAME = "memory_correct"
_PROVIDER = "streamer"
_SUMMARY_MAX_CHARS = 60
_QUOTE_MAX_CHARS = 80
_RECORD_SCAN_LIMIT = 500

_VALID_VERDICTS = ("done", "invalid", "wrong")
_VALID_EVIDENCE_KINDS = ("game_receipt", "player_chat", "operator", "self_seen")

_TARGET_RE = re.compile(r"^\s*\[?\s*(档|约)\s*#\s*(\d+)\s*\]?\s*$")

_DESCRIPTION = (
    "场内更正一条常驻简报里的记忆（档#N）或惦记的承诺（约#N）：标明做完了（done）、"
    "不成立了（invalid，如换了新世界或地形重置）或记错了（wrong），并给本场证据"
    "（游戏回执、游戏玩家公屏的话、运营提醒，或你自己亲眼看到的画面/速览）。"
    "当场生效：本场后续不再按旧说法犯错；观众弹幕不能直接当依据。"
)

_PARAMETERS: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "target": {
            "type": "string",
            "description": "要更正的简报条目编号，写成 档#N 或 约#N",
        },
        "verdict": {
            "type": "string",
            "enum": list(_VALID_VERDICTS),
            "description": "done 做完了；invalid 不成立了（世界重置、墓碑消失等）；wrong 当初记错了",
        },
        "summary": {
            "type": "string",
            "description": "一句话说明现在是什么情况（60 字以内，不带评价词）",
        },
        "evidence_kind": {
            "type": "string",
            "enum": list(_VALID_EVIDENCE_KINDS),
            "description": (
                "证据来源：game_receipt 游戏交付/事件/速览回执；player_chat 游戏玩家公屏的话；"
                "operator 运营提醒；self_seen 你在画面或速览里亲眼确认的现状"
            ),
        },
        "evidence_quote": {
            "type": "string",
            "description": "依据里的逐字片段（80 字以内；game_receipt / player_chat / operator 必填）",
        },
        "evidence_message_id": {
            "type": "string",
            "description": "若依据是游戏玩家公屏的话，可填那一行末尾 [id:…] 里的号（观众弹幕号不收）",
        },
    },
    "required": ["target", "verdict", "summary", "evidence_kind"],
}


def build_memory_correct_tool_spec() -> ToolSpec:
    """构造场内更正工具定义（全名 streamer_memory_correct，仅对主播可见）。"""
    return ToolSpec(
        name=_TOOL_NAME,
        description=_DESCRIPTION,
        parameters_schema=_PARAMETERS,
        kind="sync",
        provider=_PROVIDER,
    )


_FULL_NAME = build_memory_correct_tool_spec().full_name


def parse_memory_target(raw: Any) -> Optional[Tuple[Literal["archive", "commitment"], int]]:
    """解析 ``档#42`` / ``[约#15]`` 形态的目标编号；无法识别或非正整数返回 None。"""
    match = _TARGET_RE.match(str(raw or ""))
    if not match:
        return None
    prefix, digits = match.group(1), match.group(2)
    target_id = int(digits)
    if target_id <= 0:
        return None
    return ("archive", target_id) if prefix == "档" else ("commitment", target_id)


def _strip_msg_id(raw: Any) -> str:
    text = str(raw or "").strip()
    if text.startswith("[id:") and text.endswith("]"):
        return text[4:-1].strip()
    if text.startswith("id:"):
        return text[3:].strip()
    return text


class MemoryCorrectionToolProvider(BaseToolProvider):
    """streamer_memory_correct 的 Provider：代码核对本场证据，写入更正并刷新本场简报。"""

    category: ClassVar[str] = "framework"

    def __init__(
        self,
        *,
        correction_repo: "MemoryCorrectionRepo",
        session_provider: Callable[[], Optional[int]],
        brief_archive_ids_provider: Callable[[], Sequence[int]],
        commitments_repo: Optional["CommitmentRepo"] = None,
        event_repo: Optional["EventRepo"] = None,
        game_player_repo: Optional["GamePlayerRepo"] = None,
        chat_repo: Optional["ChatRepo"] = None,
        overview_provider: Optional[Callable[[], str]] = None,
        on_written: Optional[Callable[[], Awaitable[None]]] = None,
    ) -> None:
        self._repo = correction_repo
        self._session_provider = session_provider
        self._brief_archive_ids_provider = brief_archive_ids_provider
        self._commitments = commitments_repo
        self._events = event_repo
        self._players = game_player_repo
        self._chat = chat_repo
        self._overview_provider = overview_provider
        self._on_written = on_written
        self._logger = get_logger("MemoryCorrectionTool")

    @property
    def name(self) -> str:
        return _PROVIDER

    def list_tools(self) -> Iterable[ToolSpec]:
        return [build_memory_correct_tool_spec()]

    def _bounce(self, tool_name: str, reason: str) -> ToolExecutionResult:
        self._logger.info(f"场内更正打回：{reason}")
        return ToolExecutionResult(tool_name=tool_name, success=False, error_message=reason)

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        tool_name = invocation.tool_name
        if tool_name != _FULL_NAME:
            return ToolExecutionResult(
                tool_name=tool_name,
                success=False,
                error_message=f"MemoryCorrectionToolProvider 不处理工具 '{tool_name}'",
            )
        try:
            return await self._correct(invocation)
        except Exception as exc:  # noqa: BLE001 - 工具边界异常转失败回执，不反噬决策轮
            self._logger.exception(f"场内更正执行异常: {exc}")
            return ToolExecutionResult(
                tool_name=tool_name,
                success=False,
                error_message=f"场内更正失败：{type(exc).__name__}: {exc}",
            )

    async def _correct(self, invocation: ToolInvocation) -> ToolExecutionResult:
        tool_name = invocation.tool_name
        args = invocation.arguments or {}

        session_id = self._session_provider()
        if session_id is None:
            return self._bounce(tool_name, "现在不在直播，不能做场内记忆更正")

        parsed = parse_memory_target(args.get("target"))
        if parsed is None:
            return self._bounce(tool_name, "target 必须写成简报里的 档#N 或 约#N")
        target_kind, target_id = parsed

        verdict_raw = str(args.get("verdict") or "").strip().lower()
        if verdict_raw not in _VALID_VERDICTS:
            return self._bounce(tool_name, f"verdict 必须是 {' / '.join(_VALID_VERDICTS)} 之一")
        verdict: Literal["done", "invalid", "wrong"] = verdict_raw  # type: ignore[assignment]

        summary = " ".join(str(args.get("summary") or "").split())
        if not summary or len(summary) > _SUMMARY_MAX_CHARS:
            return self._bounce(tool_name, f"summary 要写一句现状说明，{_SUMMARY_MAX_CHARS} 字以内")

        evidence_raw = str(args.get("evidence_kind") or "").strip().lower()
        if evidence_raw in ("danmaku", "viewer", "观众", "弹幕"):
            return self._bounce(
                tool_name,
                "观众弹幕的话要经人批准才进记忆，不能直接拿来更正简报或承诺",
            )
        if evidence_raw not in _VALID_EVIDENCE_KINDS:
            return self._bounce(tool_name, f"evidence_kind 必须是 {' / '.join(_VALID_EVIDENCE_KINDS)} 之一")
        evidence_kind: Literal["game_receipt", "player_chat", "operator", "self_seen"] = evidence_raw  # type: ignore[assignment]

        quote_raw = str(args.get("evidence_quote") or "").strip()
        msg_id = _strip_msg_id(args.get("evidence_message_id"))

        # 若填了消息号，先检查是不是误拿观众弹幕冒充依据
        if msg_id and await self._is_viewer_danmaku(int(session_id), msg_id):
            return self._bounce(
                tool_name,
                "这个消息号是观众弹幕；观众的话要经人批准才进跨场记忆，不能直接更正简报或承诺",
            )

        # 核对目标在本场可见且有效
        if target_kind == "archive":
            brief_ids = {int(item) for item in self._brief_archive_ids_provider()}
            if target_id not in brief_ids:
                return self._bounce(tool_name, f"档#{target_id} 不在本场常驻简报里，无需场内更正")
        else:
            if self._commitments is None:
                return self._bounce(tool_name, "承诺账本未接入，无法更正承诺")
            commitment = await self._commitments.get_commitment(commitment_id=target_id)
            if commitment is None or commitment.status != "open":
                return self._bounce(tool_name, f"约#{target_id} 不存在或已了结，无需重复更正")

        # 核对四类本场证据
        verified_quote = ""
        game_chat_id: Optional[int] = None
        if evidence_kind == "game_receipt":
            matched = await self._verify_game_receipt(int(session_id), quote_raw)
            if matched is None:
                return self._bounce(
                    tool_name,
                    f"evidence_quote 必须是本场游戏回执、里程碑或速览里的逐字片段（{_QUOTE_MAX_CHARS} 字以内）",
                )
            verified_quote = matched
        elif evidence_kind == "player_chat":
            matched_pair = await self._verify_player_chat(int(session_id), quote_raw, msg_id)
            if matched_pair is None:
                return self._bounce(
                    tool_name,
                    f"evidence_quote 必须是本场游戏玩家公屏原话里的逐字片段（{_QUOTE_MAX_CHARS} 字以内）",
                )
            verified_quote, game_chat_id = matched_pair
        elif evidence_kind == "operator":
            matched = await self._verify_operator_note(int(session_id), quote_raw)
            if matched is None:
                return self._bounce(
                    tool_name,
                    f"evidence_quote 必须是本场运营提醒原话里的逐字片段（{_QUOTE_MAX_CHARS} 字以内）",
                )
            verified_quote = matched
        else:
            # self_seen：她自己在画面或速览看到的现状，引文可选但限长
            verified_quote = quote_raw[:_QUOTE_MAX_CHARS]

        correction = await self._repo.insert_and_apply(
            live_session_id=int(session_id),
            target_kind=target_kind,
            archive_line_id=target_id if target_kind == "archive" else None,
            commitment_id=target_id if target_kind == "commitment" else None,
            verdict=verdict,
            summary=summary,
            evidence_kind=evidence_kind,
            evidence_quote=verified_quote,
            evidence_game_chat_id=game_chat_id,
            source="streamer",
        )
        if self._on_written is not None:
            try:
                await self._on_written()
            except Exception as exc:  # noqa: BLE001 - 刷新简报异常不回滚已写入的更正
                self._logger.warning(f"场内更正后刷新常驻简报失败: {exc}", exc=exc)

        target_label = f"档#{target_id}" if target_kind == "archive" else f"约#{target_id}"
        verdict_label = CORRECTION_VERDICT_LABELS.get(verdict, verdict)
        effect = (
            f"本场简报已为 {target_label} 附注「本场更正：{format_correction_note(verdict, summary)}」"
            if target_kind == "archive"
            else f"{target_label} 已在本场标为{verdict_label}，不再留在【还惦记的事】"
        )
        return ToolExecutionResult(
            tool_name=tool_name,
            success=True,
            structured_content={
                "ok": True,
                "correction_id": correction.id,
                "target": target_label,
                "verdict": verdict,
                "effect": effect,
            },
        )

    async def _is_viewer_danmaku(self, session_id: int, message_id: str) -> bool:
        if self._chat is None or not message_id:
            return False
        try:
            rows = await self._chat.list_recent_live_chat(live_session_id=session_id, limit=200, sender_role="viewer")
        except Exception as exc:  # noqa: BLE001 - 查不到时不误拦后续玩家公屏核对
            self._logger.debug(f"核对消息号是否观众弹幕失败: {exc}", exc=exc)
            return False
        return any(str(row.message_id or "") == message_id for row in rows)

    async def _verify_game_receipt(self, session_id: int, quote: str) -> Optional[str]:
        if not quote or len(quote) > _QUOTE_MAX_CHARS:
            return None
        candidates: List[str] = []
        if self._overview_provider is not None:
            try:
                overview = self._overview_provider() or ""
                if overview:
                    candidates.append(overview)
            except Exception as exc:  # noqa: BLE001 - 速览失败不挡事件回执核对
                self._logger.debug(f"读游戏速览核对更正失败: {exc}", exc=exc)
        if self._events is not None:
            events = await self._events.list_session_game_events(live_session_id=session_id, limit=_RECORD_SCAN_LIMIT)
            delegations = await self._events.list_session_delegations(live_session_id=session_id)
            candidates.extend(ev.message for ev in events if ev.message)
            candidates.extend(d.summary for d in delegations if d.summary)
            candidates.extend(d.instruction for d in delegations if d.instruction)
        for text in candidates:
            verified = verbatim_quote(quote, text, _QUOTE_MAX_CHARS)
            if verified is not None:
                return verified
        return None

    async def _verify_player_chat(self, session_id: int, quote: str, message_id: str) -> Optional[Tuple[str, int]]:
        if self._players is None or not quote or len(quote) > _QUOTE_MAX_CHARS:
            return None
        lines = await self._players.list_session_game_chat(live_session_id=session_id)
        player_lines = [line for line in reversed(lines) if line.direction == "player" and line.content]
        if message_id:
            player_lines = [line for line in player_lines if str(line.message_id or "") == message_id]
        for line in player_lines:
            verified = verbatim_quote(quote, line.content, _QUOTE_MAX_CHARS)
            if verified is not None:
                return verified, int(line.id)
        return None

    async def _verify_operator_note(self, session_id: int, quote: str) -> Optional[str]:
        if not quote or len(quote) > _QUOTE_MAX_CHARS:
            return None
        notes = await self._repo.list_active_by_session(live_session_id=session_id)
        for item in reversed(notes):
            if item.source != "operator":
                continue
            verified = verbatim_quote(quote, item.evidence_quote or item.summary, _QUOTE_MAX_CHARS)
            if verified is not None:
                return verified
        return None
