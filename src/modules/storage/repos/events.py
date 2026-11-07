"""EventRepo —— 游戏事件记录仓储（game_events）。

``game_events``：游戏里程碑/安全阀/异常（StorageLedger 从 ``game.*``
事件落库），以及地点动静（``game.place.noted`` 落成 ``place_*`` 类型的行）。
事件流的持久观察由业务表承担（消息流落 live_chat 等），本仓储不再承接事件历史副本。

地点动静只给下播整理认地点用：按场读游戏进展、按类型计数（复盘页）都不含它们，
免得记地点、走近地点挤掉真正的游戏进展。

委派账（``task.changed`` 里委派型任务的受理与终态）落成 ``delegation_*`` 类型的行：受理行正文是
发起方自己的原话（附带的来源对话不存），终态行正文是终态摘要，两行的场景列都写任务号、按它配对，
发起方列写是谁派的。下播整理据此核对"她说做好了"，
续播时据此认出重启前还没了结的委派。它们同样不算游戏进展、不进按类型计数，也不进记忆检索
（检索只收里程碑与阶段汇报）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Set

from src.modules.storage.models.events import (
    DelegationRecord,
    GameEventRecord,
    GameEventTypeCount,
    GamePlaceMark,
    GameSaveSpan,
    IndexedGameEvent,
)
from src.modules.storage.repos._base import BaseRepo

# 地点动静落库时的类型前缀：place_remembered / place_forgotten / place_near / place_left
PLACE_EVENT_PREFIX = "place_"
PLACE_EVENT_TYPES = tuple(f"{PLACE_EVENT_PREFIX}{kind}" for kind in ("remembered", "forgotten", "near", "left"))
# 读游戏进展、按类型计数时排除地点动静的条件（类型名是代码内常量，不来自外部输入）
_NOT_PLACE_EVENT = "event_type NOT IN (" + ", ".join(f"'{event_type}'" for event_type in PLACE_EVENT_TYPES) + ")"

# 委派账落库时的类型前缀：delegation_accepted（受理）/ delegation_<终态>（succeeded、undelivered、
# cancelled 等任务账本终态）/ delegation_interrupted（重启前没了结、续播时认领成被打断）
DELEGATION_EVENT_PREFIX = "delegation_"
DELEGATION_ACCEPTED = f"{DELEGATION_EVENT_PREFIX}accepted"
DELEGATION_INTERRUPTED = f"{DELEGATION_EVENT_PREFIX}interrupted"
# 按前缀认委派行：前缀是代码内常量；不用 LIKE，下划线在 LIKE 里是通配符
_IS_DELEGATION_EVENT = f"substr(event_type, 1, {len(DELEGATION_EVENT_PREFIX)}) = '{DELEGATION_EVENT_PREFIX}'"
# 读游戏进展、按类型计数只看真正的游戏事件：地点动静与委派账都不算
_GAME_PROGRESS_EVENT = f"{_NOT_PLACE_EVENT} AND NOT ({_IS_DELEGATION_EVENT})"


class EventRepo(BaseRepo):
    """game_events 表的写入与按场读取。"""

    async def insert_game_event(
        self,
        *,
        live_session_id: int,
        game: str,
        event_type: str,
        message: str,
        scene: Optional[str] = None,
        timestamp_ms: int = 0,
        save_id: str = "",
        save_name: str = "",
        initiator: str = "",
    ) -> int:
        """插入一条 game_events 行，返回 lastrowid；``save_id`` 空串 = 不分存档或存档不明；
        ``initiator`` 只有委派账行填（发起方），其余为空串。"""

        def _exec() -> int:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "INSERT INTO game_events (live_session_id, game, event_type, message, scene, timestamp_ms,"
                    " save_id, save_name, initiator) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        live_session_id,
                        game,
                        event_type,
                        message,
                        scene,
                        timestamp_ms,
                        save_id or "",
                        save_name or "",
                        initiator or "",
                    ),
                )
                return int(cur.lastrowid or 0)

        return await self._run_in_executor(_exec)

    async def list_session_game_events(self, *, live_session_id: int, limit: int = 60) -> List[GameEventRecord]:
        """取指定场次的游戏事件（时间正序，最多 ``limit`` 条最近的）——场次回顾的游戏进展原料；不含地点动静与委派账。"""
        page_limit = max(1, min(int(limit), 500))

        def _exec() -> List[GameEventRecord]:
            with self._manager.transaction() as conn:
                rows = conn.execute(
                    "SELECT game, event_type, message, scene, timestamp_ms, save_id, save_name FROM game_events"
                    f" WHERE live_session_id=? AND {_GAME_PROGRESS_EVENT} ORDER BY timestamp_ms DESC LIMIT ?",  # noqa: S608
                    (int(live_session_id), page_limit),
                ).fetchall()
                return [GameEventRecord.model_validate(dict(row)) for row in reversed(rows)]

        return await self._run_in_executor(_exec)

    async def insert_place_mark(
        self,
        *,
        live_session_id: int,
        game: str,
        kind: str,
        place: str,
        distance: Optional[int] = None,
        timestamp_ms: int = 0,
        save_id: str = "",
        save_name: str = "",
    ) -> int:
        """记一条地点动静：类型写成 ``place_<种类>``，地点名进正文，走近时的距离（格）进场景列。"""
        return await self.insert_game_event(
            live_session_id=live_session_id,
            game=game,
            event_type=f"{PLACE_EVENT_PREFIX}{kind}",
            message=place,
            scene=str(int(distance)) if distance is not None else None,
            timestamp_ms=timestamp_ms,
            save_id=save_id,
            save_name=save_name,
        )

    async def list_session_place_marks(self, *, live_session_id: int) -> List[GamePlaceMark]:
        """取一场里的全部地点动静（时间正序）：下播整理认"这句话是在哪个地点说的"用。"""

        def _exec() -> List[GamePlaceMark]:
            placeholders = ",".join("?" for _ in PLACE_EVENT_TYPES)
            with self._manager.transaction() as conn:
                rows = conn.execute(
                    "SELECT game, event_type, message, scene, timestamp_ms, save_id, save_name FROM game_events"
                    f" WHERE live_session_id=? AND event_type IN ({placeholders})"  # noqa: S608 占位符由代码按个数拼
                    " ORDER BY timestamp_ms ASC, id ASC",
                    (int(live_session_id), *PLACE_EVENT_TYPES),
                ).fetchall()
            marks: List[GamePlaceMark] = []
            for row in rows:
                scene = str(row["scene"] or "")
                marks.append(
                    GamePlaceMark(
                        game=str(row["game"]),
                        kind=str(row["event_type"])[len(PLACE_EVENT_PREFIX) :],
                        place=str(row["message"]),
                        distance=int(scene) if scene.isdigit() else None,
                        timestamp_ms=int(row["timestamp_ms"]),
                        save_id=str(row["save_id"] or ""),
                        save_name=str(row["save_name"] or ""),
                    )
                )
            return marks

        return await self._run_in_executor(_exec)

    async def insert_delegation_event(
        self,
        *,
        live_session_id: int,
        game: str,
        task_id: str,
        kind: str,
        text: str,
        timestamp_ms: int,
        initiator: str = "",
    ) -> int:
        """记一条委派账：类型 ``delegation_<kind>``，正文是发起方原话（受理）或终态摘要，场景列写任务号，
        发起方列写是谁派的。"""
        return await self.insert_game_event(
            live_session_id=live_session_id,
            game=game,
            event_type=f"{DELEGATION_EVENT_PREFIX}{kind}",
            message=text,
            scene=task_id,
            timestamp_ms=timestamp_ms,
            initiator=initiator,
        )

    async def list_session_delegations(self, *, live_session_id: int) -> List[DelegationRecord]:
        """取一场里经手过的委派（按受理先后）：指令、终态、终态摘要、受理与了结时刻。

        这一场里受理或了结过的任务号都算；同一任务号在别的场次的那一行也一并配上
        （开播前受理、开播后才了结的委派，受理行不在这一场）。下播整理据此核对"她说做好了"。
        """

        def _exec() -> List[DelegationRecord]:
            with self._manager.transaction() as conn:
                return _delegations_of(conn, live_session_id=int(live_session_id))

        return await self._run_in_executor(_exec)

    async def settle_interrupted_delegations(
        self, *, text: str, timestamp_ms: int, accepted_before_ms: int
    ) -> List[DelegationRecord]:
        """进程启动时收尾：``accepted_before_ms`` 之前受理、到现在还没有终态的委派，各记一条被打断的终态。

        任务账本只在内存里，进程一重启就空了，游戏那边也不会接着做原来的活：库里还挂着受理的委派
        只可能是上一个进程留下的。不论这一场随后续播、直接开新场还是手动收尾残留场，它们都不会
        再有终态；不在这里了结，下播整理会一直当它还在做。被打断的那一行记在它受理的那一场里，
        返回记好之后的样子。查与写在同一个事务里，重复收尾不会重复记。
        """

        def _exec() -> List[DelegationRecord]:
            with self._manager.transaction() as conn:
                open_ones = [
                    record
                    for record in _delegations_of(conn, live_session_id=None)
                    if record.status == "accepted" and record.started_ms < accepted_before_ms
                ]
                settled: List[DelegationRecord] = []
                for record in open_ones:
                    conn.execute(
                        "INSERT INTO game_events (live_session_id, game, event_type, message, scene, timestamp_ms,"
                        " save_id, save_name, initiator) VALUES (?, ?, ?, ?, ?, ?, '', '', ?)",
                        (
                            record.live_session_id,
                            record.game,
                            DELEGATION_INTERRUPTED,
                            text,
                            record.task_id,
                            timestamp_ms,
                            record.initiator,
                        ),
                    )
                    update = {"status": "interrupted", "summary": text, "ended_ms": int(timestamp_ms)}
                    settled.append(record.model_copy(update=update))
                return settled

        return await self._run_in_executor(_exec)

    async def list_events_by_ids(self, *, event_ids: Sequence[int]) -> List[IndexedGameEvent]:
        """按主键读回游戏事件（记忆检索命中后还原成行；顺序不保证，调用方按自己的名次排）。"""
        ids = [int(event_id) for event_id in event_ids]
        if not ids:
            return []

        def _exec() -> List[IndexedGameEvent]:
            placeholders = ",".join("?" for _ in ids)
            with self._manager.transaction() as conn:
                rows = conn.execute(
                    "SELECT id, live_session_id, game, event_type, message, timestamp_ms, save_id, save_name"
                    f" FROM game_events WHERE id IN ({placeholders})",  # noqa: S608 占位符由代码按个数拼
                    ids,
                ).fetchall()
            return [IndexedGameEvent.model_validate(dict(row)) for row in rows]

        return await self._run_in_executor(_exec)

    async def list_session_save_spans(self, *, live_session_id: int) -> List[GameSaveSpan]:
        """取一场里每个游戏按时间先后待过的存档段（只看记下了存档的事件）。

        同一游戏连续几条事件在同一个存档里就并成一段，A → B → 回 A 是三段；段名取这段里最后
        一次报的名字（存档改过名时显示新名）。只取四列、不限条数：长场次前半段待过的存档不能
        因为只看最近几十条事件而丢掉，否则前半场说的话会被认成是在后来的存档里说的。
        """

        def _exec() -> List[GameSaveSpan]:
            with self._manager.transaction() as conn:
                rows = conn.execute(
                    "SELECT game, save_id, save_name, timestamp_ms FROM game_events"
                    " WHERE live_session_id=? AND save_id != '' ORDER BY timestamp_ms ASC, id ASC",
                    (int(live_session_id),),
                ).fetchall()
            spans: List[GameSaveSpan] = []
            # 每个游戏眼下这一段在 spans 里的位置：存档没变就延长这一段，变了就另起一段
            current: Dict[str, int] = {}
            for row in rows:
                game, save_id = str(row["game"]), str(row["save_id"])
                name, at_ms = str(row["save_name"] or ""), int(row["timestamp_ms"])
                index = current.get(game)
                if index is not None and spans[index].save_id == save_id:
                    span = spans[index]
                    spans[index] = span.model_copy(update={"ended_ms": at_ms, "save_name": name or span.save_name})
                    continue
                current[game] = len(spans)
                spans.append(GameSaveSpan(game=game, save_id=save_id, save_name=name, started_ms=at_ms, ended_ms=at_ms))
            return spans

        return await self._run_in_executor(_exec)

    async def count_game_events_by_type(self, *, live_session_ids: List[int]) -> List[GameEventTypeCount]:
        """按类型计数场次游戏事件（多场 IN 合并，复盘页游戏侧指标数据源）；不含地点动静与委派账。"""

        def _exec() -> List[GameEventTypeCount]:
            placeholders = ",".join("?" for _ in live_session_ids)
            with self._manager.transaction() as conn:
                rows = conn.execute(
                    "SELECT event_type, COUNT(*) AS n FROM game_events"
                    f" WHERE live_session_id IN ({placeholders}) AND {_GAME_PROGRESS_EVENT}"  # noqa: S608 子句为代码内常量
                    " GROUP BY event_type",
                    [int(sid) for sid in live_session_ids],
                ).fetchall()
                return [GameEventTypeCount.model_validate(dict(row)) for row in rows]

        return await self._run_in_executor(_exec)

    async def list_games_by_session(self, *, live_session_ids: Sequence[int]) -> Dict[int, Set[str]]:
        """每场留下过游戏事件的游戏名（多场 IN 合并）；没有游戏事件的场次不在结果里。

        长期笔记按"隔了几场没人提"淡出时，只跟某个游戏有关的条目只数真玩过那个游戏的场次。
        """
        ids = [int(sid) for sid in live_session_ids]
        if not ids:
            return {}

        def _exec() -> Dict[int, Set[str]]:
            placeholders = ",".join("?" for _ in ids)
            with self._manager.transaction() as conn:
                rows = conn.execute(
                    f"SELECT DISTINCT live_session_id, game FROM game_events WHERE live_session_id IN ({placeholders})",  # noqa: S608 子句为代码内常量
                    ids,
                ).fetchall()
            games: Dict[int, Set[str]] = {}
            for row in rows:
                games.setdefault(int(row["live_session_id"]), set()).add(str(row["game"]))
            return games

        return await self._run_in_executor(_exec)


def _delegations_of(conn: Any, *, live_session_id: Optional[int]) -> List[DelegationRecord]:
    """按任务号把受理行与终态行配成一条委派（同一连接内读，供读取与启动收尾共用）。

    ``live_session_id`` 给了只取这一场经手过的任务号，None 取库里全部委派账（启动收尾用：
    委派账行不多，一次扫完在内存里配对，比逐条关联子查询省）。终态取最早写下的那一行：
    终态粘滞，之后再有（如启动收尾与迟到的终态撞上）也以先到的为准。
    """
    columns = "SELECT live_session_id, game, event_type, message, scene, timestamp_ms, initiator FROM game_events"
    if live_session_id is None:
        rows = conn.execute(
            f"{columns} WHERE {_IS_DELEGATION_EVENT} ORDER BY timestamp_ms ASC, id ASC"  # noqa: S608 子句为代码内常量
        ).fetchall()
    else:
        rows = conn.execute(
            f"{columns} WHERE {_IS_DELEGATION_EVENT} AND scene IN ("  # noqa: S608 子句为代码内常量
            f"SELECT scene FROM game_events WHERE live_session_id=? AND {_IS_DELEGATION_EVENT})"
            " ORDER BY timestamp_ms ASC, id ASC",
            (live_session_id,),
        ).fetchall()
    records: Dict[str, DelegationRecord] = {}
    for row in rows:
        task_id = str(row["scene"] or "")
        kind = str(row["event_type"])[len(DELEGATION_EVENT_PREFIX) :]
        record = records.get(task_id) or DelegationRecord(
            live_session_id=int(row["live_session_id"]),
            task_id=task_id,
            game=str(row["game"]),
            initiator=str(row["initiator"] or ""),
        )
        if kind == "accepted":
            accepted = {
                "live_session_id": int(row["live_session_id"]),
                "instruction": str(row["message"] or ""),
                "initiator": str(row["initiator"] or ""),
                "started_ms": int(row["timestamp_ms"]),
            }
            record = record.model_copy(update=accepted)
        elif record.status == "accepted":
            ended = {"status": kind, "summary": str(row["message"] or ""), "ended_ms": int(row["timestamp_ms"])}
            record = record.model_copy(update=ended)
        records[task_id] = record
    return sorted(records.values(), key=lambda record: (record.started_ms or record.ended_ms, record.task_id))


__all__ = ["DELEGATION_ACCEPTED", "DELEGATION_EVENT_PREFIX", "DELEGATION_INTERRUPTED", "EventRepo"]
