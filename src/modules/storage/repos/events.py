"""EventRepo —— 游戏事件记录仓储（game_events）。

``game_events``：游戏里程碑/安全阀/异常（StorageLedger 从 ``game.*``
事件落库），以及地点动静（``game.place.noted`` 落成 ``place_*`` 类型的行）。
事件流的持久观察由业务表承担（消息流落 live_chat 等），本仓储不再承接事件历史副本。

地点动静只给下播整理认地点用：按场读游戏进展、按类型计数（复盘页）都不含它们，
免得记地点、走近地点挤掉真正的游戏进展。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Set

from src.modules.storage.models.events import (
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
    ) -> int:
        """插入一条 game_events 行，返回 lastrowid；``save_id`` 空串 = 不分存档或存档不明。"""

        def _exec() -> int:
            with self._manager.transaction() as conn:
                cur = conn.execute(
                    "INSERT INTO game_events (live_session_id, game, event_type, message, scene, timestamp_ms,"
                    " save_id, save_name) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (live_session_id, game, event_type, message, scene, timestamp_ms, save_id or "", save_name or ""),
                )
                return int(cur.lastrowid or 0)

        return await self._run_in_executor(_exec)

    async def list_session_game_events(self, *, live_session_id: int, limit: int = 60) -> List[GameEventRecord]:
        """取指定场次的游戏事件（时间正序，最多 ``limit`` 条最近的）——场次回顾的游戏进展原料；不含地点动静。"""
        page_limit = max(1, min(int(limit), 500))

        def _exec() -> List[GameEventRecord]:
            with self._manager.transaction() as conn:
                rows = conn.execute(
                    "SELECT game, event_type, message, scene, timestamp_ms, save_id, save_name FROM game_events"
                    f" WHERE live_session_id=? AND {_NOT_PLACE_EVENT} ORDER BY timestamp_ms DESC LIMIT ?",  # noqa: S608
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
        """按类型计数场次游戏事件（多场 IN 合并，复盘页游戏侧指标数据源）；不含地点动静。"""

        def _exec() -> List[GameEventTypeCount]:
            placeholders = ",".join("?" for _ in live_session_ids)
            with self._manager.transaction() as conn:
                rows = conn.execute(
                    "SELECT event_type, COUNT(*) AS n FROM game_events"
                    f" WHERE live_session_id IN ({placeholders}) AND {_NOT_PLACE_EVENT}"  # noqa: S608 子句为代码内常量
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


__all__ = ["EventRepo"]
