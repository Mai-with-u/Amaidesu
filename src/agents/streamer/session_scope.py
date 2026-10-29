"""场次作用域：以 live_session 为界的进程内状态与决策窗材料收集。

「这一场主播记得什么」在此单点承载——游戏账本（出账与清场经本模块动词，
入账动词见 :attr:`SessionScope.ledger`）、待入私聊行、未落库对话轮、时间线
notes、本窗游戏批次与上场回顾快照。对外动词：开窗（``open_window``，出账
先于读历史的顺序不变量内化其中）、读历史（``read_history``，纯读）、补记
一轮（``remember_round``）、清场（``begin_session`` / ``end_session``）。
live_chat 是已落库对话的事实源，本模块只读不写；回顾刷新等记忆域动作仍归
StreamerAgent，经 :meth:`SessionScope.set_session_recap` 写入槽位。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, List, Optional

from src.modules.events.payloads.room import RoomMessagePayload
from src.modules.logging import ModuleLogger, get_logger
from src.modules.utils.time_utils import now_ms

from .game_ledger import GameLedger
from .timeline import GameEventBatch, StreamerTimeline, TimelineTurn

__all__ = ["LiveChatTurn", "SessionScope", "WindowMaterials"]

# 私聊暂存上限（行）。只进本窗决策参考段，不进时间线
_PRIVATE_CHAT_LINES_MAX = 10


@dataclass(frozen=True)
class LiveChatTurn:
    """live_chat 行的对话历史视图（鸭子类型：仅需 ``role`` / ``content`` 属性）。

    ``role`` 直接承载 live_chat 的 ``sender_role``（viewer / assistant），
    下游按字符串取值；昵称/类型/消息 ID 供 canonical 映射序列化
    （content 格式 = ``昵称: 内容 [id:…]``，批与历史同形）。
    """

    role: str
    content: str
    sender_name: str = ""
    message_type: str = "danmaku"
    message_id: str = ""
    # 发生时刻：落库行与内存对话轮按它合并成同一条时间线
    timestamp_ms: int = 0


@dataclass(frozen=True)
class WindowMaterials:
    """一次决策窗的材料包（:meth:`SessionScope.open_window` 产物）。

    字段语义（调用方按此消费，None 与空不同义）：
    - ``history``：``None`` = 读库失败，宁可本轮无历史，不拿残缺冒充完整；
      空列表 = 真的无历史。
    - ``game_batch``：空批次 = 本窗无新游戏事件（不用 None 表达）。
    - ``session_recap``：空串 = 不注入（未启用或没有上一场）。
    - ``timeline_notes``：本窗开窗时点的时间线 notes 累积快照（只读观察面，
      写入仍只经本模块动词）。
    """

    history: Optional[List[Any]]
    game_batch: GameEventBatch
    session_recap: str
    timeline_notes: List[TimelineTurn] = field(default_factory=list)


def _text_field(item: Any, name: str) -> str:
    """鸭子字段取文本：只接受 str，其余（None/Mock 等）按空串处理。"""
    value = getattr(item, name, "")
    return value if isinstance(value, str) else ""


class SessionScope:
    """场次作用域状态 + 决策窗开窗（合并投影/出账/清场/补记的单一承载）。"""

    def __init__(
        self,
        *,
        ledger: GameLedger,
        timeline: StreamerTimeline,
        chat: Any = None,
        session_manager: Any = None,
        logger: Optional[ModuleLogger] = None,
    ) -> None:
        self._ledger = ledger
        self._timeline = timeline
        self._chat = chat
        self._session_manager = session_manager
        self._logger = logger or get_logger("StreamerAgent.SessionScope")
        self._pending_private_chat: deque[str] = deque(maxlen=_PRIVATE_CHAT_LINES_MAX)
        self._unpersisted_turns: List[LiveChatTurn] = []
        self._timeline_notes: List[TimelineTurn] = []
        self._window_game_batch = GameEventBatch()
        self._session_recap: str = ""
        self._long_memory: str = ""

    # -- 状态便道 ------------------------------------------------------

    @property
    def ledger(self) -> GameLedger:
        """游戏账本。入账动词（record_event/record_encounter/record_chat）归账本
        接口本身，本模块不复制；出账（drain）与清场（reset）走本模块动词。"""
        return self._ledger

    @property
    def session_recap(self) -> str:
        """上场回顾段（开播时装入，整场不变；空串 = 不注入）。"""
        return self._session_recap

    def set_session_recap(self, text: str) -> None:
        """回顾槽位写入（记忆域刷新完成时调；本模块不背仓储依赖）。"""
        self._session_recap = text

    @property
    def long_memory(self) -> str:
        """长期记忆全文（开播时装入，整场不变；空串 = 不注入）。"""
        return self._long_memory

    def set_long_memory(self, text: str) -> None:
        """长期记忆槽位写入（开播或启动善后时由主播 Agent 读文件后调）。"""
        self._long_memory = text

    # -- 入账便道 ------------------------------------------------------

    def record_private_chat(self, line: str) -> None:
        """私聊行入待入栈：不进时间线，随本窗批次交决策参考段（私下的话不当众说）。"""
        self._pending_private_chat.append(line)

    def note_delegation(self, agent_name: str, instruction: str) -> TimelineTurn:
        """委派行：账本入账 + 直写时间线（不进决策窗批次，批次只收三类游戏事件通道）。"""
        turn = self._ledger.record_delegation(agent_name, instruction)
        self._timeline_notes.append(turn)
        return turn

    # -- 清场 ----------------------------------------------------------

    async def begin_session(self) -> None:
        """开播清场：场次作用域状态归零，本场从空白开始记。"""
        await self._reset()

    async def end_session(self) -> None:
        """下播清场：与开播同一份实现——下播后的调试对话不混进已结账场次。"""
        await self._reset()

    async def _reset(self) -> None:
        self._unpersisted_turns.clear()
        self._ledger.reset()
        self._pending_private_chat.clear()
        self._timeline_notes.clear()
        await self._timeline.reset()

    # -- 开窗与读取 ----------------------------------------------------

    def drain_window(self) -> GameEventBatch:
        """决策窗出账半：账本整批出账，本批时间线条目落进时间线，批次行交决策链。

        收口只发生在窗边界恰好一处（``open_window`` 首步 / 测试显式驱动）；
        读历史（``read_history``）是纯读。折叠过多条的行附计数说明（"另 N 次
        相同"）后再入时间线；时间线条目只追加不改写，相邻窗口的历史前缀逐字
        一致。私聊行不进时间线，单独随批次交本窗决策参考段。
        """
        batch = self._ledger.drain(private_chat=self._pending_private_chat)
        self._pending_private_chat.clear()
        self._timeline_notes.extend(batch.timeline_turns)
        self._window_game_batch = batch
        return batch

    def current_window_batch(self) -> GameEventBatch:
        """本窗新入账的游戏事件批次（出账半写入；无新事件为空批次）。"""
        return self._window_game_batch

    async def open_window(self) -> WindowMaterials:
        """开一个决策窗：出账（先）→ 读历史（后）→ 附本窗批次与上场回顾。

        "出账先于读取"的顺序不变量由本方法内化——调用方无法错序。
        """
        game_batch = self.drain_window()
        history = await self.read_history()
        return WindowMaterials(
            history=history,
            game_batch=game_batch,
            session_recap=self._session_recap,
            timeline_notes=list(self._timeline_notes),
        )

    async def read_history(self) -> Optional[List[Any]]:
        """读取当前对话并经时间线投影：前情提要各章 + 工作层原文，按主播看到的先后排列。

        纯读，无副作用——出账见 ``drain_window``（``open_window`` 首步）。显式
        场次进行中时，live_chat 是已落库对话的事实源；没开场次时的对话与调试
        注入的观众消息不会落库，由内存对话轮按发生时刻并入同一条时间线。读库
        失败返回 ``None``（宁可本轮无历史，也不拿残缺历史冒充完整对话）。
        """
        stored: List[LiveChatTurn] = []
        live_pk: Optional[int] = None
        if self._chat is not None and self._session_manager is not None:
            try:
                live_pk = await self._session_manager.resolve_pk()
                rows = (
                    await self._chat.list_recent_live_chat(live_session_id=live_pk, limit=None)
                    if live_pk is not None
                    else []
                )
            except Exception as exc:
                self._logger.warning(f"读取会话历史失败: {exc}")
                return None
            stored = [
                LiveChatTurn(
                    role=row.sender_role,
                    content=row.content,
                    sender_name=row.sender_name or "",
                    message_type=row.message_type,
                    message_id=row.message_id or "",
                    timestamp_ms=row.timestamp_ms or 0,
                )
                for row in rows
            ]
        if self._unpersisted_turns or self._timeline_notes:
            # 对话、未落库的对话轮与游戏记录按发生时刻合并；稳定排序保持同一时刻各自的先后。
            # 已进时间线的条目不再挪位置，排序只决定同一窗里新条目的先后
            stored = sorted(
                [*stored, *self._unpersisted_turns, *self._timeline_notes], key=lambda turn: turn.timestamp_ms
            )
        return await self._timeline.project(stored, scope=live_pk)

    # -- 补记 ----------------------------------------------------------

    async def remember_round(
        self,
        batch: List[RoomMessagePayload],
        speech: Optional[str],
        *,
        batch_persisted: bool,
    ) -> None:
        """决策轮结束后补记不会从 live_chat 读回的对话：观众先说、主播后答。

        ``batch_persisted`` 表示本批消息是否经 room.message 事件进了落库链路；
        调试注入传 False——即使开着场次，这些消息也不在 live_chat 里。
        """
        chat_persisted = await self._chat_persisted()
        if not (batch_persisted and chat_persisted):
            for msg in batch:
                user = getattr(msg, "user", None)
                nickname = getattr(user, "name", None) or getattr(user, "id", None)
                timestamp = getattr(msg, "timestamp_ms", None)
                self._unpersisted_turns.append(
                    LiveChatTurn(
                        role="viewer",
                        content=_text_field(msg, "content"),
                        sender_name=nickname if isinstance(nickname, str) else "",
                        message_type=_text_field(msg, "message_type") or "danmaku",
                        message_id=_text_field(msg, "message_id"),
                        timestamp_ms=timestamp if isinstance(timestamp, int) and timestamp > 0 else now_ms(),
                    )
                )
        # 开着场次时主播发言由 StorageLedger 写进 live_chat，这里只补没有落库的那一份
        if speech and not chat_persisted:
            self._unpersisted_turns.append(
                LiveChatTurn(
                    role="assistant",
                    content=str(speech),
                    sender_name="主播",
                    message_type="speak",
                    timestamp_ms=now_ms(),
                )
            )

    async def _chat_persisted(self) -> bool:
        """当前对话是否会写进 live_chat 并能被历史读回（显式场次进行中且有聊天仓储）。"""
        if self._chat is None or self._session_manager is None:
            return False
        try:
            return await self._session_manager.resolve_pk() is not None
        except Exception as exc:
            self._logger.warning(f"解析当前场次失败（本轮对话按未落库记入内存）: {exc}", exc=exc)
            return False
