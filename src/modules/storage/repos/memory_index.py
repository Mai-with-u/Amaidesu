"""MemoryIndexRepo —— 记忆检索派生索引的读写（memory_fts_* 全文表与 memory_vectors_* 向量表）。

正本永远是来源行（档案行、承诺、她的台词、游戏事件），这两类表随时能按正本重算。索引任务按
"缺什么补什么"工作：每轮问仓储哪些该收的行还没有全文行、哪些还没有当前模型的向量，算好再写回。

**哪些行该收**（收什么、不收什么在这里一处定死，索引任务和检索都不另写一份）：

- 档案行：全收（彩排场的档案行在改彩排时已删掉）；
- 承诺：运营者判了误记的不收；
- 她的台词：只收正式场里她播出了的台词，并且要播出满 30 秒（没播出的标记可能晚到）；只取她自己说的
  正文，从不带上她回应的那条观众弹幕——观众的话进不了任何跨场记忆；
- 游戏社交事实：只收现行行（被取代的不收）；
- 游戏事件：白名单，只收里程碑与阶段汇报。安全阀、异常不是她该记得的事；游戏聊天、玩家肢体动作
  带着玩家原话与身份，不能经索引绕进她的记忆。

**写回时再核一次**：从读出到写回之间来源行可能被删、被改（删观众替换昵称、台词标成没播出）或场次改成
彩排，所以写回语句带条件——来源行还在、仍然该收、正文和算的时候一字不差，才写；否则这一行丢掉，
下一轮按新正文重算。删除与改正文时清掉旧索引行由 v29 的触发器负责；场次改成彩排时来源行不变、
触发器拦不住，由 :func:`delete_session_index_rows` 在改类型的同一个事务里删。
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from dataclasses import dataclass
from threading import RLock
from typing import Dict, List, Literal, Optional, Sequence, Tuple

import numpy as np

from src.modules.logging import get_logger
from src.modules.storage.connection import SQLiteConnectionManager
from src.modules.storage.models.memory import IndexCandidate, IndexDoc, IndexVector
from src.modules.storage.repos._base import BaseRepo

logger = get_logger("MemoryIndexRepo")

IndexSource = Literal["archive", "commitment", "speech", "event", "social_fact"]
INDEX_SOURCES: Tuple[IndexSource, ...] = ("archive", "commitment", "speech", "event", "social_fact")

# 游戏事件白名单：只收里程碑与阶段汇报
INDEXED_GAME_EVENT_TYPES: Tuple[str, ...] = ("milestone", "report")
# 她的台词播出满这么久才收：台词被打断、标成没播出的标记会晚一点到
SPEECH_SETTLE_MS = 30_000
# 同场相邻主播台词的滑窗间隔上限（毫秒）：只拼她自己已播出的前一句，绝不纳入观众弹幕
SPEECH_NEIGHBOR_WINDOW_MS = 30_000
# SQLite 的 trim(x) 默认只去半角空格，连同换行、回车、制表符一起剥掉才能识破纯空白正文
_SQL_WHITESPACE = "' ' || char(9) || char(10) || char(13)"


def _nonblank_sql(expr: str) -> str:
    """SQL 非空白条件：剥掉空格、制表符与换行后仍有字符。"""
    return f"length(trim({expr}, {_SQL_WHITESPACE})) > 0"


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
        eligible=(
            "t.sender_role = 'assistant' AND t.dropped_at_ms IS NULL"
            f" AND {_nonblank_sql('t.content')} AND s.kind = 'live'"
        ),
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
    # 游戏社交事实：只收现行行（被取代的行不再是"现在成立"的事）
    "social_fact": _Spec(
        table="game_social_facts",
        fts="memory_fts_social_fact",
        vectors="memory_vectors_social_fact",
        key="social_fact_id",
        document="t.text || char(10) || t.quote",
        eligible="t.status = 'active'",
        joins_session=False,
        session_column="created_session_id",
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
    这些行重新变成"该收的"，由索引任务补回来。同时递增进程内向量矩阵缓存版本号，避免语义检索读到脏缓存。
    """
    for source in ("speech", "event"):
        spec = _SPECS[source]
        rows = f"SELECT id FROM {spec.table} WHERE live_session_id = ?"  # noqa: S608 常量表名
        conn.execute(f"DELETE FROM {spec.fts} WHERE rowid IN ({rows})", (int(live_session_id),))  # noqa: S608
        conn.execute(f"DELETE FROM {spec.vectors} WHERE {spec.key} IN ({rows})", (int(live_session_id),))  # noqa: S608
    _bump_vector_cache_epoch(_db_cache_key_from_conn(conn), ("speech", "event"))


@dataclass(frozen=True)
class StoredVectorMatrix:
    """按 ``(source, model_key)`` 缓存在进程内的归一化 ``float32`` 向量矩阵。

    ``source_ids``、``session_ids`` 与 ``matrix`` 的行一一对应；``matrix`` 每行已完成 L2 单位化，
    检索引擎可直接与单位化查询向量做矩阵点积求余弦；``session_ids`` 用 ``-1`` 表示未归属场次，
    支持带 ``live_session_id`` 限定时直接在内存切片筛选子集。
    """

    source_ids: Tuple[int, ...]
    matrix: np.ndarray
    session_ids: np.ndarray


@dataclass(frozen=True)
class _CachedVectorMatrix:
    """单份缓存矩阵及其一致性凭证（显式版本号 + 数据库轻量标量指纹）。"""

    epoch: Tuple[int, int]
    fingerprint: Tuple[int, int, int, int, int]
    data: StoredVectorMatrix


# 跨同库实例共享的向量缓存显式失效版本号（按主库规范化路径隔离，内存库走全局版本号）
_CACHE_EPOCH_LOCK = RLock()
_DB_SOURCE_EPOCHS: Dict[str, Dict[IndexSource, int]] = {}
_GLOBAL_SOURCE_EPOCHS: Dict[IndexSource, int] = {source: 0 for source in INDEX_SOURCES}


def _normalize_db_cache_key(raw_path: str) -> str:
    """把 SQLite 文件路径归一化为进程内缓存键；内存库与空路径返回空串。"""
    cleaned = (raw_path or "").strip()
    if not cleaned or cleaned == ":memory:":
        return ""
    return os.path.normcase(os.path.abspath(cleaned))


def _db_cache_key_from_conn(conn: sqlite3.Connection) -> str:
    """从 SQLite 连接反查主库文件路径，供跨仓储事务删除索引后精准失效对应库的向量缓存。"""
    try:
        rows = conn.execute("PRAGMA database_list").fetchall()
    except sqlite3.Error as exc:
        logger.warning(f"查询数据库连接文件路径失败，改走全局向量缓存失效: {exc}", exc=True)
        return ""
    for row in rows:
        if len(row) >= 3 and str(row[1]) == "main":
            return _normalize_db_cache_key(str(row[2] or ""))
    return ""


def _bump_vector_cache_epoch(db_key: str, sources: Sequence[IndexSource]) -> None:
    """递增指定来源的向量缓存版本号：有具体库路径时按库递增，内存库或路径不明时递增全局版本号。"""
    with _CACHE_EPOCH_LOCK:
        if not db_key:
            for source in sources:
                _GLOBAL_SOURCE_EPOCHS[source] = _GLOBAL_SOURCE_EPOCHS.get(source, 0) + 1
            return
        bucket = _DB_SOURCE_EPOCHS.setdefault(db_key, {})
        for source in sources:
            bucket[source] = bucket.get(source, 0) + 1


def _read_vector_cache_epoch(db_key: str, source: IndexSource) -> Tuple[int, int]:
    """读取当前库与全局的向量缓存版本二元组，任一侧递增即判定旧缓存失效。"""
    with _CACHE_EPOCH_LOCK:
        db_epoch = _DB_SOURCE_EPOCHS.get(db_key, {}).get(source, 0) if db_key else 0
        global_epoch = _GLOBAL_SOURCE_EPOCHS.get(source, 0)
        return (db_epoch, global_epoch)


def _empty_vector_matrix() -> StoredVectorMatrix:
    """空向量矩阵（只读，零行零列）。"""
    empty_matrix = np.empty((0, 0), dtype=np.float32)
    empty_matrix.setflags(write=False)
    empty_sessions = np.empty((0,), dtype=np.int64)
    empty_sessions.setflags(write=False)
    return StoredVectorMatrix(source_ids=(), matrix=empty_matrix, session_ids=empty_sessions)


def _build_normalized_matrix(
    source: IndexSource, rows: Sequence[Tuple[int, Optional[int], bytes]]
) -> StoredVectorMatrix:
    """把 SQLite 读出的原始小端 float32 字节一次性拼装并做 L2 行归一化，设为只读供缓存复用。"""
    source_ids: List[int] = []
    session_ids: List[int] = []
    vectors: List[np.ndarray] = []
    expected_dim: Optional[int] = None
    for source_id, session_id, raw_bytes in rows:
        if not raw_bytes:
            continue
        vec = np.frombuffer(raw_bytes, dtype="<f4")
        if vec.size == 0:
            continue
        if expected_dim is None:
            expected_dim = int(vec.shape[0])
        elif int(vec.shape[0]) != expected_dim:
            logger.warning(
                f"{source} 向量行 id={source_id} 维度 {vec.shape[0]} 与首行维度 {expected_dim} 不一致，已跳过"
            )
            continue
        source_ids.append(int(source_id))
        session_ids.append(int(session_id) if session_id is not None else -1)
        vectors.append(vec)
    if not vectors:
        return _empty_vector_matrix()
    stacked = np.stack(vectors).astype(np.float32, copy=False)
    norms = np.linalg.norm(stacked, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    normalized = (stacked / norms).astype(np.float32, copy=False)
    normalized.setflags(write=False)
    session_array = np.asarray(session_ids, dtype=np.int64)
    session_array.setflags(write=False)
    return StoredVectorMatrix(source_ids=tuple(source_ids), matrix=normalized, session_ids=session_array)


def _slice_session_matrix(data: StoredVectorMatrix, live_session_id: int) -> StoredVectorMatrix:
    """从已缓存的全量矩阵按场次号内存切片，避免重复查库与反序列化。"""
    if not data.source_ids:
        return data
    mask = data.session_ids == int(live_session_id)
    if not bool(np.any(mask)):
        return _empty_vector_matrix()
    indices = np.flatnonzero(mask)
    sliced_ids = tuple(data.source_ids[int(idx)] for idx in indices)
    sliced_matrix = np.ascontiguousarray(data.matrix[indices], dtype=np.float32)
    sliced_matrix.setflags(write=False)
    sliced_sessions = np.ascontiguousarray(data.session_ids[indices], dtype=np.int64)
    sliced_sessions.setflags(write=False)
    return StoredVectorMatrix(source_ids=sliced_ids, matrix=sliced_matrix, session_ids=sliced_sessions)


def _vector_fingerprint(conn: sqlite3.Connection, spec: _Spec, model_key: str) -> Tuple[int, int, int, int, int]:
    """只扫索引与轻量标量列算一致性指纹（不碰大 BLOB 内容），兜底触发器删行与外部 SQL 变更。

    对 ``v.text_hash`` 采样前 12 位十六进制字符（拆为两组 6 位多项式和，单行上界远低于 64 位整型上限），
    大幅压低外部 SQL 原位改动正文哈希时的理论碰撞率。
    """
    sql = (
        "SELECT COUNT(*), COALESCE(SUM(t.id), 0), COALESCE(MAX(t.id), 0),"
        " COALESCE(SUM("
        "  unicode(substr(v.text_hash, 1, 1)) * 1048576"
        "  + unicode(substr(v.text_hash, 2, 1)) * 65536"
        "  + unicode(substr(v.text_hash, 3, 1)) * 4096"
        "  + unicode(substr(v.text_hash, 4, 1)) * 256"
        "  + unicode(substr(v.text_hash, 5, 1)) * 16"
        "  + unicode(substr(v.text_hash, 6, 1))"
        "  + length(v.vector)"
        " ), 0),"
        " COALESCE(SUM("
        "  unicode(substr(v.text_hash, 7, 1)) * 1048576"
        "  + unicode(substr(v.text_hash, 8, 1)) * 65536"
        "  + unicode(substr(v.text_hash, 9, 1)) * 4096"
        "  + unicode(substr(v.text_hash, 10, 1)) * 256"
        "  + unicode(substr(v.text_hash, 11, 1)) * 16"
        "  + unicode(substr(v.text_hash, 12, 1))"
        " ), 0)"
        f" {_from(spec)}"  # noqa: S608 常量片段
        f" JOIN {spec.vectors} v ON v.{spec.key} = t.id"
        f" WHERE v.model = ? AND {spec.eligible}"
    )
    row = conn.execute(sql, (model_key,)).fetchone()
    if row is None:
        return (0, 0, 0, 0, 0)
    return (
        int(row[0] or 0),
        int(row[1] or 0),
        int(row[2] or 0),
        int(row[3] or 0),
        int(row[4] or 0),
    )


class MemoryIndexRepo(BaseRepo):
    """派生索引的读写入口（索引任务与混合检索共用，含按 ``(source, model_key)`` 缓存的归一化向量矩阵）。"""

    def __init__(self, manager: SQLiteConnectionManager) -> None:
        super().__init__(manager)
        self._db_cache_key = _normalize_db_cache_key(str(getattr(manager, "db_path", "")))
        self._cache_lock = RLock()
        self._vector_cache: Dict[Tuple[IndexSource, str], _CachedVectorMatrix] = {}
        # 累计从 SQLite 读取全量向量并构建归一化矩阵的次数（便于诊断与测试验证缓存命中）
        self._vector_cache_loads = 0

    @property
    def vector_cache_loads(self) -> int:
        """累计发生的全量向量矩阵构建次数（缓存命中时不递增）。"""
        with self._cache_lock:
            return self._vector_cache_loads

    def invalidate_vector_cache(self, *, source: Optional[IndexSource] = None) -> None:
        """精准失效进程内向量矩阵缓存；``source`` 为 None 时清空全部来源。"""
        sources: Tuple[IndexSource, ...] = (source,) if source is not None else INDEX_SOURCES
        with self._cache_lock:
            if source is None:
                self._vector_cache.clear()
            else:
                for key in [k for k in self._vector_cache if k[0] == source]:
                    self._vector_cache.pop(key, None)
        _bump_vector_cache_epoch(self._db_cache_key, sources)

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
            f" AND {_nonblank_sql(spec.document)}"
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
                    # 缺向量的行只在正文非空白时才算"还缺"：空白正文算不出向量（同 pending_vectors 的排除）
                    vector_missing = ""
                    params: List[object] = list(_settled_params(source, now_ms))
                    if model_key is not None:
                        vector_missing = f" OR ({_nonblank_sql(spec.document)} AND (v.model IS NULL OR v.model <> ?))"
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
        written = await self._run_in_executor(self._write_rows, sql, rows)
        if written > 0:
            # 向量写入后立刻失效该来源的进程内矩阵缓存，确保紧接着的语义查询看到最新向量
            self.invalidate_vector_cache(source=source)
        return written

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

    async def stored_vector_matrix(
        self, *, source: IndexSource, model_key: str, live_session_id: Optional[int] = None
    ) -> StoredVectorMatrix:
        """返回按 ``(source, model_key)`` 缓存的归一化 float32 向量矩阵，避免每次语义检索全表反序列化。

        执行顺序：
        1. 先核对显式版本号（向量写入、场次改彩排清索引、外部手动失效均会递增）与轻量标量指纹
           （兜底触发器删行或绕过本仓储的 SQL 改动）；
        2. 全量缓存新鲜时直接返回（带 ``live_session_id`` 则直接在内存按场次切片）；
        3. 缓存缺失或失效时：若限定了单场则走单场精确查询避免把全库拉入内存；未限定场次则一次加载、
           归一化并缓存供后续复用。
        """
        spec = _SPECS[source]
        cache_key = (source, model_key)

        def _exec() -> StoredVectorMatrix:
            current_epoch = _read_vector_cache_epoch(self._db_cache_key, source)
            with self._cache_lock:
                cached = self._vector_cache.get(cache_key)
                if cached is not None and cached.epoch != current_epoch:
                    self._vector_cache.pop(cache_key, None)
                    cached = None

            with self._manager.transaction() as conn:
                if cached is not None:
                    fp = _vector_fingerprint(conn, spec, model_key)
                    if fp == cached.fingerprint:
                        if live_session_id is None:
                            return cached.data
                        return _slice_session_matrix(cached.data, int(live_session_id))
                    # 轻量指纹失配：说明存在外部表变更或触发器删除，立即清掉脏缓存
                    with self._cache_lock:
                        if self._vector_cache.get(cache_key) is cached:
                            self._vector_cache.pop(cache_key, None)

                # 限定了单场且当前没有可用的全量缓存：只读该场向量，不把跨场全库拖进内存
                if live_session_id is not None:
                    session_clause, session_params = _session_clause(spec, live_session_id)
                    sql = (
                        f"SELECT t.id, t.{spec.session_column}, v.vector {_from(spec)}"  # noqa: S608 常量片段
                        f" JOIN {spec.vectors} v ON v.{spec.key} = t.id"
                        f" WHERE v.model = ? AND {spec.eligible}{session_clause}"
                    )
                    rows = conn.execute(sql, (model_key, *session_params)).fetchall()
                    raw_rows = [
                        (int(row[0]), int(row[1]) if row[1] is not None else None, bytes(row[2])) for row in rows
                    ]
                    return _build_normalized_matrix(source, raw_rows)

                # 未限定场次：在同一事务内同时读取标量指纹与全量向量，构建只读归一化矩阵并入缓存
                epoch_before = _read_vector_cache_epoch(self._db_cache_key, source)
                fp = _vector_fingerprint(conn, spec, model_key)
                sql = (
                    f"SELECT t.id, t.{spec.session_column}, v.vector {_from(spec)}"  # noqa: S608 常量片段
                    f" JOIN {spec.vectors} v ON v.{spec.key} = t.id"
                    f" WHERE v.model = ? AND {spec.eligible}"
                )
                rows = conn.execute(sql, (model_key,)).fetchall()
                raw_rows = [(int(row[0]), int(row[1]) if row[1] is not None else None, bytes(row[2])) for row in rows]
                built = _build_normalized_matrix(source, raw_rows)

            with self._cache_lock:
                self._vector_cache_loads += 1
                # 若构建期间未发生新的显式失效，写入缓存；否则只返回本次结果不留旧快照
                if _read_vector_cache_epoch(self._db_cache_key, source) == epoch_before:
                    self._vector_cache[cache_key] = _CachedVectorMatrix(
                        epoch=epoch_before,
                        fingerprint=fp,
                        data=built,
                    )
            return built

        return await self._run_in_executor(_exec)

    async def speech_window_rows(self, *, chat_ids: Sequence[int]) -> Dict[int, Tuple[str, Optional[int], str]]:
        """按主键取台词本句正文与同场前 30 秒紧邻上一句（供检索层区分跨句互补命中与蹭词影子）。"""
        ids = [int(chat_id) for chat_id in dict.fromkeys(chat_ids)]
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        prev_where = (
            "prev.live_session_id = t.live_session_id"
            " AND prev.sender_role = 'assistant'"
            " AND prev.dropped_at_ms IS NULL"
            f" AND {_nonblank_sql('prev.content')}"
            " AND prev.id < t.id"
            f" AND t.timestamp_ms - prev.timestamp_ms BETWEEN 0 AND {SPEECH_NEIGHBOR_WINDOW_MS}"
        )
        sql = (
            "SELECT t.id, t.content,"
            f" (SELECT prev.id FROM live_chat prev WHERE {prev_where}"  # noqa: S608 常量片段
            " ORDER BY prev.timestamp_ms DESC, prev.id DESC LIMIT 1) AS prev_id,"
            f" (SELECT prev.content FROM live_chat prev WHERE {prev_where}"
            " ORDER BY prev.timestamp_ms DESC, prev.id DESC LIMIT 1) AS prev_content"
            f" FROM live_chat t WHERE t.id IN ({placeholders})"
        )

        def _exec() -> Dict[int, Tuple[str, Optional[int], str]]:
            with self._manager.transaction() as conn:
                rows = conn.execute(sql, tuple(ids)).fetchall()
            return {
                int(row[0]): (
                    str(row[1] or ""),
                    int(row[2]) if row[2] is not None else None,
                    str(row[3] or ""),
                )
                for row in rows
            }

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
    "SPEECH_NEIGHBOR_WINDOW_MS",
    "SPEECH_SETTLE_MS",
    "IndexSource",
    "MemoryIndexRepo",
    "StoredVectorMatrix",
    "delete_session_index_rows",
    "text_hash",
]
