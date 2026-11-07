"""MemoryIndexRepo —— 记忆检索派生索引的读写（memory_fts_* 全文表与 memory_vectors_* 向量表）。

正本永远是来源行（档案行、承诺、她的台词、游戏事件），这两类表随时能按正本重算。索引任务按
"缺什么补什么"工作：每轮问仓储哪些该收的行还没有全文行、哪些还没有当前模型的向量，算好再写回。

**哪些行该收**（收什么、不收什么在这里一处定死，索引任务和检索都不另写一份）：

- 档案行：全收（彩排场的档案行在改彩排时已删掉）；
- 承诺：运营者判了误记的不收；
- 她的台词：只收正式场里她播出了的台词，并且要播出满 30 秒（没播出的标记可能晚到）；只取她自己说的
  正文，从不带上她回应的那条观众弹幕——观众的话进不了任何跨场记忆；
- 游戏事件：白名单，只收里程碑与阶段汇报。安全阀、异常不是她该记得的事；游戏聊天、玩家肢体动作
  带着玩家原话与身份，不能经索引绕进她的记忆。

**写回时再核一次**：从读出到写回之间来源行可能被删、被改（删观众替换昵称、台词标成没播出）或场次改成
彩排，所以写回语句带条件——来源行还在、仍然该收、正文和算的时候一字不差，才写；否则这一行丢掉，
下一轮按新正文重算。删除与改正文时清掉旧索引行由 v29 的触发器负责；场次改成彩排时来源行不变、
触发器拦不住，由 :func:`delete_session_index_rows` 在改类型的同一个事务里删。
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from typing import Dict, List, Literal, Optional, Sequence, Tuple

import numpy as np

from src.modules.storage.models.memory import IndexCandidate, IndexDoc, IndexVector
from src.modules.storage.repos._base import BaseRepo

IndexSource = Literal["archive", "commitment", "speech", "event"]
INDEX_SOURCES: Tuple[IndexSource, ...] = ("archive", "commitment", "speech", "event")

# 游戏事件白名单：只收里程碑与阶段汇报
INDEXED_GAME_EVENT_TYPES: Tuple[str, ...] = ("milestone", "report")
# 她的台词播出满这么久才收：台词被打断、标成没播出的标记会晚一点到
SPEECH_SETTLE_MS = 30_000


@dataclass(frozen=True)
class _Spec:
    """一类来源的索引口径：来源表、两张派生表、正文怎么拼、哪些行该收。"""

    table: str
    fts: str
    vectors: str
    key: str
    # 建索引用的正文（SQL 表达式，来源表别名 t）
    document: str
    # 该收的行（SQL 条件，来源表别名 t、场次表别名 s）
    eligible: str
    # 条件里要不要用到场次表
    joins_session: bool
    # 来源行属于哪一场的列（只查本场时按它限定）
    session_column: str = "live_session_id"


_SPECS: Dict[IndexSource, _Spec] = {
    "archive": _Spec(
        table="archive_lines",
        fts="memory_fts_archive",
        vectors="memory_vectors_archive",
        key="archive_line_id",
        document="t.text || char(10) || t.quote",
        eligible="1 = 1",
        joins_session=False,
    ),
    "commitment": _Spec(
        table="commitments",
        fts="memory_fts_commitment",
        vectors="memory_vectors_commitment",
        key="commitment_id",
        document="t.what || char(10) || t.quote",
        eligible="t.status <> 'void'",
        joins_session=False,
        session_column="created_session_id",
    ),
    "speech": _Spec(
        table="live_chat",
        fts="memory_fts_speech",
        vectors="memory_vectors_speech",
        key="chat_id",
        document="t.content",
        eligible="t.sender_role = 'assistant' AND t.dropped_at_ms IS NULL AND s.kind = 'live'",
        joins_session=True,
    ),
    "event": _Spec(
        table="game_events",
        fts="memory_fts_event",
        vectors="memory_vectors_event",
        key="event_id",
        document="t.message",
        eligible="t.event_type IN ("
        + ", ".join(f"'{event_type}'" for event_type in INDEXED_GAME_EVENT_TYPES)
        + ") AND s.kind = 'live'",
        joins_session=True,
    ),
}


def text_hash(document: str) -> str:
    """正文指纹（写进向量表，核对向量是不是按现在的正文算的）。"""
    return hashlib.sha256(document.encode("utf-8")).hexdigest()


def _from(spec: _Spec) -> str:
    """来源表连同场次表的 FROM 片段。"""
    join = " JOIN live_sessions s ON s.id = t.live_session_id" if spec.joins_session else ""
    return f"FROM {spec.table} t{join}"


def _settled_clause(source: IndexSource) -> str:
    """台词另加"播出满 30 秒"的条件（参数是截止时刻）；别的来源没有这一条。"""
    return " AND t.timestamp_ms <= ?" if source == "speech" else ""


def _settled_params(source: IndexSource, now_ms: int) -> Tuple[int, ...]:
    return (int(now_ms) - SPEECH_SETTLE_MS,) if source == "speech" else ()


def _session_clause(spec: _Spec, live_session_id: Optional[int]) -> Tuple[str, Tuple[int, ...]]:
    """只查某一场时的附加条件与参数；不限定时为空。"""
    if live_session_id is None:
        return "", ()
    return f" AND t.{spec.session_column} = ?", (int(live_session_id),)


def delete_session_index_rows(conn: sqlite3.Connection, *, live_session_id: int) -> None:
    """删掉某一场台词与游戏事件的全文行和向量行（场次改成彩排时，在改类型的同一个事务里调用）。

    彩排的话观众没听过，她不该"记得"；来源行本身不变，触发器管不到，只能在这里删。改回正式场后
    这些行重新变成"该收的"，由索引任务补回来。
    """
    for source in ("speech", "event"):
        spec = _SPECS[source]
        rows = f"SELECT id FROM {spec.table} WHERE live_session_id = ?"  # noqa: S608 常量表名
        conn.execute(f"DELETE FROM {spec.fts} WHERE rowid IN ({rows})", (int(live_session_id),))  # noqa: S608
        conn.execute(f"DELETE FROM {spec.vectors} WHERE {spec.key} IN ({rows})", (int(live_session_id),))  # noqa: S608


class MemoryIndexRepo(BaseRepo):
    """派生索引的读写入口（索引任务用）。"""

    async def pending_full_text(self, *, source: IndexSource, now_ms: int, limit: int) -> List[IndexDoc]:
        """该收、却还没有全文行的来源行（按主键从小到大，至多 ``limit`` 行）。"""
        spec = _SPECS[source]
        sql = (
            f"SELECT t.id, {spec.document} AS document {_from(spec)}"  # noqa: S608 常量片段
            f" WHERE {spec.eligible}{_settled_clause(source)}"
            f" AND NOT EXISTS (SELECT 1 FROM {spec.fts} f WHERE f.rowid = t.id)"
            " ORDER BY t.id LIMIT ?"
        )
        params = (*_settled_params(source, now_ms), int(limit))
        return await self._run_in_executor(self._read_docs, sql, params)

    async def pending_vectors(self, *, source: IndexSource, model_key: str, now_ms: int, limit: int) -> List[IndexDoc]:
        """该收、却还没有当前模型向量的来源行（没有向量，或向量是别的模型、别的维度算的）。"""
        spec = _SPECS[source]
        sql = (
            f"SELECT t.id, {spec.document} AS document {_from(spec)}"  # noqa: S608 常量片段
            f" LEFT JOIN {spec.vectors} v ON v.{spec.key} = t.id"
            f" WHERE {spec.eligible}{_settled_clause(source)}"
            # 空白正文算不出向量（服务端会拒），不排进队列，免得永远堵在队头
            f" AND length(trim({spec.document})) > 0"
            " AND (v.model IS NULL OR v.model <> ?)"
            " ORDER BY t.id LIMIT ?"
        )
        params = (*_settled_params(source, now_ms), model_key, int(limit))
        return await self._run_in_executor(self._read_docs, sql, params)

    async def pending_total(self, *, model_key: Optional[str], now_ms: int) -> int:
        """全部来源"该收还没收"的行总数：至少缺一样（全文行或当前模型向量）的来源行数。

        索引任务拿它判断要不要在后台任务面板登记补算（缺行数超门槛才登记，
        归零即补齐）；``model_key`` 为 None（没配嵌入服务）时只数缺全文行的行。
        补算中正文被改的行写回时会被条件拦下留在"缺"里，下一轮按新正文重算，
        这里的计数口径与 ``pending_full_text`` / ``pending_vectors`` 一致。
        """

        def _count() -> int:
            total = 0
            with self._manager.transaction() as conn:
                for source in INDEX_SOURCES:
                    spec = _SPECS[source]
                    # 缺向量的行只在正文非空时才算"还缺"：空白正文算不出向量（同 pending_vectors 的排除）
                    vector_missing = ""
                    params: List[object] = list(_settled_params(source, now_ms))
                    if model_key is not None:
                        vector_missing = (
                            " OR (length(trim(" + spec.document + ")) > 0 AND (v.model IS NULL OR v.model <> ?))"
                        )
                        params.append(model_key)
                    sql = (
                        f"SELECT COUNT(*) {_from(spec)}"  # noqa: S608 常量片段
                        + (f" LEFT JOIN {spec.vectors} v ON v.{spec.key} = t.id" if model_key is not None else "")
                        + f" WHERE {spec.eligible}{_settled_clause(source)}"
                        + f" AND (NOT EXISTS (SELECT 1 FROM {spec.fts} f WHERE f.rowid = t.id){vector_missing})"
                    )
                    total += int(conn.execute(sql, tuple(params)).fetchone()[0])
            return total

        return await self._run_in_executor(_count)

    def _read_docs(self, sql: str, params: Tuple[object, ...]) -> List[IndexDoc]:
        with self._manager.transaction() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [IndexDoc(source_id=int(row[0]), document=str(row[1] or "")) for row in rows]

    async def write_full_text(self, *, source: IndexSource, items: Sequence[Tuple[IndexDoc, str]]) -> int:
        """写全文行（每项是来源行与切好的词）；来源行已删、不再该收或正文变了的跳过，返回写了几行。"""
        spec = _SPECS[source]
        sql = (
            f"INSERT INTO {spec.fts}(rowid, tokens) SELECT ?, ?"  # noqa: S608 常量片段
            f" WHERE EXISTS (SELECT 1 {_from(spec)} WHERE t.id = ? AND {spec.eligible} AND {spec.document} = ?)"
            f" AND NOT EXISTS (SELECT 1 FROM {spec.fts} WHERE rowid = ?)"
        )
        rows = [(doc.source_id, tokens, doc.source_id, doc.document, doc.source_id) for doc, tokens in items]
        return await self._run_in_executor(self._write_rows, sql, rows)

    async def write_vectors(
        self, *, source: IndexSource, model_key: str, items: Sequence[Tuple[IndexDoc, Sequence[float]]]
    ) -> int:
        """写向量行（小端 float32），同样只在来源行仍然该收、正文没变时写；返回写了几行。"""
        spec = _SPECS[source]
        sql = (
            f"INSERT OR REPLACE INTO {spec.vectors}({spec.key}, model, dim, text_hash, vector)"  # noqa: S608
            f" SELECT ?, ?, ?, ?, ?"
            f" WHERE EXISTS (SELECT 1 {_from(spec)} WHERE t.id = ? AND {spec.eligible} AND {spec.document} = ?)"
        )
        rows = [
            (
                doc.source_id,
                model_key,
                len(vector),
                text_hash(doc.document),
                np.asarray(vector, dtype="<f4").tobytes(),
                doc.source_id,
                doc.document,
            )
            for doc, vector in items
        ]
        return await self._run_in_executor(self._write_rows, sql, rows)

    async def keyword_candidates(
        self, *, source: IndexSource, match_query: str, limit: int, live_session_id: Optional[int] = None
    ) -> List[IndexCandidate]:
        """全文检索：按 FTS5 的 BM25 排序（分数取正，越大越相关），至多 ``limit`` 行。

        查询时再按"该收"的条件核一遍（正式场、没标没播出、不是误记……），索引行即便因为
        什么原因没删干净，也查不出彩排场的话。
        """
        spec = _SPECS[source]
        session_clause, session_params = _session_clause(spec, live_session_id)
        # FTS5 的 MATCH 与 bm25() 只认表名本身，不认别名
        fts = spec.fts
        sql = (
            f"SELECT t.id, -bm25({fts}) AS score FROM {fts}"  # noqa: S608 常量片段
            f" JOIN {spec.table} t ON t.id = {fts}.rowid"
            + (" JOIN live_sessions s ON s.id = t.live_session_id" if spec.joins_session else "")
            + f" WHERE {fts} MATCH ? AND {spec.eligible}{session_clause}"
            f" ORDER BY bm25({fts}) LIMIT ?"
        )
        params = (match_query, *session_params, int(limit))
        return await self._run_in_executor(self._read_candidates, sql, params)

    async def like_candidates(
        self, *, source: IndexSource, needle: str, limit: int, live_session_id: Optional[int] = None
    ) -> List[IndexCandidate]:
        """子串兜底（查询词里有单个汉字、按词查必漏时用）：正文包含 ``needle`` 的行，新的在前。"""
        spec = _SPECS[source]
        session_clause, session_params = _session_clause(spec, live_session_id)
        # LIKE 的通配符按字面量查：用 ! 作转义符（避开反斜杠在各层字符串里的转义）
        escaped = needle.replace("!", "!!").replace("%", "!%").replace("_", "!_")
        sql = (
            f"SELECT t.id, 0.0 AS score {_from(spec)}"  # noqa: S608 常量片段
            f" WHERE {spec.eligible}{session_clause} AND {spec.document} LIKE ? ESCAPE '!'"
            " ORDER BY t.id DESC LIMIT ?"
        )
        params = (*session_params, f"%{escaped}%", int(limit))
        return await self._run_in_executor(self._read_candidates, sql, params)

    async def stored_vectors(
        self, *, source: IndexSource, model_key: str, live_session_id: Optional[int] = None
    ) -> List[IndexVector]:
        """当前模型算的全部向量（同样按"该收"的条件核过），供检索算余弦。"""
        spec = _SPECS[source]
        session_clause, session_params = _session_clause(spec, live_session_id)
        sql = (
            f"SELECT t.id, v.vector {_from(spec)}"  # noqa: S608 常量片段
            f" JOIN {spec.vectors} v ON v.{spec.key} = t.id"
            f" WHERE v.model = ? AND {spec.eligible}{session_clause}"
        )
        params = (model_key, *session_params)

        def _exec() -> List[IndexVector]:
            with self._manager.transaction() as conn:
                rows = conn.execute(sql, params).fetchall()
            return [IndexVector(source_id=int(row[0]), vector=bytes(row[1])) for row in rows]

        return await self._run_in_executor(_exec)

    def _read_candidates(self, sql: str, params: Tuple[object, ...]) -> List[IndexCandidate]:
        with self._manager.transaction() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [IndexCandidate(source_id=int(row[0]), score=float(row[1] or 0.0)) for row in rows]

    def _write_rows(self, sql: str, rows: List[Tuple[object, ...]]) -> int:
        written = 0
        with self._manager.transaction() as conn:
            for row in rows:
                written += int(conn.execute(sql, row).rowcount or 0)
        return written


__all__ = [
    "INDEXED_GAME_EVENT_TYPES",
    "INDEX_SOURCES",
    "SPEECH_SETTLE_MS",
    "IndexSource",
    "MemoryIndexRepo",
    "delete_session_index_rows",
    "text_hash",
]
