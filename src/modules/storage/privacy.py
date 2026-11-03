"""观众隐私与自主遗忘服务：命令判定、不记录名单与密钥、场内遮名、宽限删除与完整删除函数。

核心契约：
- **命令与撤回判定**：存储与决策侧共用纯函数 ``matches_phrase_table``。先把弹幕里的 ``@某人`` 提及剥除，
  再按非汉字、非字母数字字符切成小句，每个小句剥掉开头的称呼（"主播"与配置里的主播名），空小句丢弃；
  剩余小句非空且**全部**在短语表内才算命中。
- **不记录名单（forget_list）**：只存 ``HMAC-SHA256(库外密钥, 种类|外部ID)``，不含应用编号。启动时校验库外密钥文件：
  文件缺失且名单为空时自动生成 32 字节随机密钥；文件缺失但名单非空时直接抛错阻止启动，给出找回密钥或清空名单两条出路。
- **宽限删除**：观众发"忘了我"后立即进不记录名单、停止记录并在观众行记 ``forget_requested_at_ms``；
  实际删除推迟到发令之后第一次下播后的清理（或最迟发令后 24 小时），执行前本人发撤回短语即可移出名单、清掉待删标记。
- **删除函数**：单一事务清除观众行、曾用名、弹幕、对 ta 的承诺、相同外部 ID 的模拟回放副本，清空付费三表的身份/留言/原始消息，
  按每个昵称的使用期找出重叠或出现过的场次，把主播台词、档案、承诺、话题摘要、时间线摘要及过渡期旧回顾里的昵称替换为"一位观众"，
  并同步清理过渡期旧画像与 ``memory/`` 目录下的旧笔记及历史留底快照，最后执行 ``PRAGMA wal_checkpoint(TRUNCATE)``。
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, List, Literal, Optional, Sequence, Set, Tuple

from src.modules.config.storage_schemas import DEFAULT_FORGET_PHRASES, DEFAULT_RETRACT_PHRASES
from src.modules.logging import get_logger
from src.modules.storage.connection import SQLiteConnectionManager
from src.modules.storage.migrations._common import table_exists
from src.modules.storage.repos.viewers import IdKind, is_placeholder_name
from src.modules.storage.run_in_executor import run_in_executor
from src.modules.utils.time_utils import now_ms

logger = get_logger("PrivacyService")

# 替换昵称、遮名、回想遮名的最短长度（单字昵称不替换，防误伤日常用字）
MIN_MASK_NAME_LEN: int = 2
# 宽限删除最迟执行时限（发令后 24 小时）
GRACE_DELETE_MAX_MS: int = 24 * 3_600_000
MASKED_VIEWER_LABEL: str = "一位观众"

IncomingDisposition = Literal["forget_command", "retracted", "forget_listed", "normal"]

# 剥除句首或分隔符后的 @提及（如 "@麦麦 忘了我"）
_AT_MENTION_RE = re.compile(r"(?:^|(?<=[\s,，。！!?？~～、：:；;]))@[^\s,，。！!?？~～、：:；;@]+")
# 按一切非汉字、非 ASCII 字母数字的字符切小句（标点、空格、符号、表情均作为分隔）
_CLAUSE_SPLIT_RE = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff\u3400-\u4dbf]+")
# 过渡期旧笔记 MEMORY.md 行尾标签块
_LEGACY_NOTE_TAG_RE = re.compile(r"〔([^〔〕]*)〕\s*$")


def _normalize_phrase(text: str) -> str:
    return text.strip().casefold()


def matches_phrase_table(
    content: str,
    phrases: Sequence[str],
    *,
    bot_name: str = "",
) -> bool:
    """判定弹幕是否严格由短语表中的短语组成。

    步骤：剥掉 ``@某人`` → 按一切非汉字非字母数字字符切小句 → 每小句剥掉开头的称呼
    （"主播"与 ``bot_name``）→ 丢弃空小句 → 剩余至少一句且每句都在 ``phrases`` 中。
    """
    if not content or not phrases:
        return False
    phrase_set = {_normalize_phrase(p) for p in phrases if p and p.strip()}
    if not phrase_set:
        return False

    stripped_at = _AT_MENTION_RE.sub(" ", content)
    raw_clauses = [part for part in _CLAUSE_SPLIT_RE.split(stripped_at) if part]
    if not raw_clauses:
        return False

    callouts = ["主播"]
    clean_bot = (bot_name or "").strip()
    if clean_bot:
        for part in _CLAUSE_SPLIT_RE.split(clean_bot):
            if part and part not in callouts:
                callouts.append(part)
    callouts.sort(key=len, reverse=True)

    cleaned: List[str] = []
    for clause in raw_clauses:
        cur = clause.strip()
        while cur and _normalize_phrase(cur) not in phrase_set:
            matched_prefix = False
            for callout in callouts:
                if cur.casefold().startswith(callout.casefold()):
                    cur = cur[len(callout) :].strip()
                    matched_prefix = True
                    break
            if not matched_prefix:
                break
        if cur:
            cleaned.append(_normalize_phrase(cur))

    return bool(cleaned) and all(item in phrase_set for item in cleaned)


def mask_names_in_text(
    text: str,
    names: Iterable[str],
    replacement: str = MASKED_VIEWER_LABEL,
) -> str:
    """把文本中已知观众昵称（2 字以上、非占位名）按从长到短替换为指定代称。"""
    if not text:
        return text
    valid = sorted(
        {
            name.strip()
            for name in names
            if name and len(name.strip()) >= MIN_MASK_NAME_LEN and not is_placeholder_name(name)
        },
        key=len,
        reverse=True,
    )
    if not valid:
        return text
    result = text
    for name in valid:
        if name in result:
            result = result.replace(name, replacement)
    return result


def compute_forget_hash(secret_key: bytes, id_kind: str, external_id: str) -> bytes:
    """计算不记录名单摘要：``HMAC-SHA256(库外密钥, 种类|外部ID)``（不含应用编号）。"""
    payload = f"{id_kind}|{external_id}".encode("utf-8")
    return hmac.new(secret_key, payload, hashlib.sha256).digest()


@dataclass(frozen=True)
class PrivacyStats:
    """生产计数快照（供日志与运维排查）。"""

    forget_requests: int
    retractions: int
    late_retractions: int
    deletions_executed: int


class PrivacyService:
    """观众遗忘命令、不记录名单、场内遮名、宽限删除与完整删除执行器。"""

    def __init__(
        self,
        manager: SQLiteConnectionManager,
        *,
        db_path: Path,
        key_path: Path,
        forget_phrases: Sequence[str] = DEFAULT_FORGET_PHRASES,
        retract_phrases: Sequence[str] = DEFAULT_RETRACT_PHRASES,
        bot_name: str = "",
        clock: Callable[[], int] = now_ms,
    ) -> None:
        self._manager = manager
        self._db_path = Path(db_path)
        self._key_path = Path(key_path)
        self._forget_phrases: Tuple[str, ...] = tuple(forget_phrases)
        self._retract_phrases: Tuple[str, ...] = tuple(retract_phrases)
        self._bot_name = bot_name
        self._clock = clock
        self._secret_key: Optional[bytes] = None

        self._state_lock = threading.Lock()
        # 本场发言过的不记录名单观众昵称（下播清空，落库前替换主播台词中的称呼）
        self._session_masked_names: Set[str] = set()
        # 本场触发过有效撤回的消息编号与发送者键，供稍后执行的 Planner 线索推送生成回执行
        self._session_retracted_msg_ids: Set[str] = set()
        self._session_retracted_senders: Set[Tuple[str, str, str]] = set()
        self._session_forget_senders: Set[Tuple[str, str, str]] = set()

        # 生产计数
        self._forget_requests = 0
        self._retractions = 0
        self._late_retractions = 0
        self._deletions_executed = 0

        # 24 小时宽限期满兜底延时任务集合
        self._timer_tasks: Set[asyncio.Task[None]] = set()
        self._closed = False

    @property
    def forget_phrases(self) -> Tuple[str, ...]:
        return self._forget_phrases

    @property
    def retract_phrases(self) -> Tuple[str, ...]:
        return self._retract_phrases

    @property
    def bot_name(self) -> str:
        return self._bot_name

    def set_bot_name(self, bot_name: str) -> None:
        """更新主播称呼（供热更新或装配阶段注入人设名）。"""
        self._bot_name = bot_name or ""

    def get_stats(self) -> PrivacyStats:
        with self._state_lock:
            return PrivacyStats(
                forget_requests=self._forget_requests,
                retractions=self._retractions,
                late_retractions=self._late_retractions,
                deletions_executed=self._deletions_executed,
            )

    async def initialize(self) -> None:
        """校验或初始化库外密钥文件；缺失且名单非空时抛 RuntimeError 拒启。"""
        await run_in_executor(self._initialize_blocking)

    def _initialize_blocking(self) -> None:
        with self._manager.transaction() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM forget_list").fetchone()
            list_count = int(row["n"]) if row else 0

        if self._key_path.exists():
            raw = self._key_path.read_bytes()
            if not raw:
                raise RuntimeError(f"不记录名单密钥文件为空: {self._key_path}")
            self._secret_key = raw
            return

        if list_count > 0:
            raise RuntimeError(
                f"不记录名单密钥文件缺失（{self._key_path}），但数据库中已有 {list_count} 条不记录摘要。"
                "为防止被遗忘的观众静默恢复记录，已阻止启动。两条处理出路："
                f"（1）从备份找回原密钥文件放到 {self._key_path}；"
                "（2）若确认放弃现有不记录名单（名单上的人再次发言会被重新记录），"
                "请对数据库执行 DELETE FROM forget_list; 后重新启动。"
            )

        self._key_path.parent.mkdir(parents=True, exist_ok=True)
        generated = secrets.token_bytes(32)
        tmp_path = self._key_path.with_name(self._key_path.name + ".tmp")
        tmp_path.write_bytes(generated)
        os.replace(tmp_path, self._key_path)
        self._secret_key = generated
        logger.info(f"已生成不记录名单密钥文件（请单独备份）: {self._key_path}")

    def _require_key(self) -> bytes:
        if self._secret_key is None:
            self._initialize_blocking()
        assert self._secret_key is not None
        return self._secret_key

    async def close(self) -> None:
        """取消挂起的 24 小时宽限删除延时任务。"""
        self._closed = True
        tasks = list(self._timer_tasks)
        self._timer_tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # -------------------- 场内遮名与会话状态 --------------------

    def add_masked_name(self, name: Optional[str]) -> None:
        """把不记录名单观众当前的显示名加入本场遮名集合（2 字以上且非占位名）。"""
        text = (name or "").strip()
        if len(text) >= MIN_MASK_NAME_LEN and not is_placeholder_name(text):
            with self._state_lock:
                self._session_masked_names.add(text)

    def mask_speech(self, speech_text: str) -> str:
        """主播台词落库前，将本场遮名集合里的昵称替换为"一位观众"。"""
        with self._state_lock:
            names = list(self._session_masked_names)
        if not names:
            return speech_text
        return mask_names_in_text(speech_text, names, replacement=MASKED_VIEWER_LABEL)

    def clear_session_masks(self) -> None:
        """下播时清空本场内存遮名集合与当场回执追踪。"""
        with self._state_lock:
            self._session_masked_names.clear()
            self._session_retracted_msg_ids.clear()
            self._session_retracted_senders.clear()
            self._session_forget_senders.clear()

    def is_forget_command(self, content: str) -> bool:
        return matches_phrase_table(content, self._forget_phrases, bot_name=self._bot_name)

    def is_retract_command(self, content: str) -> bool:
        return matches_phrase_table(content, self._retract_phrases, bot_name=self._bot_name)

    def was_retracted_in_session(
        self,
        *,
        message_id: Optional[str] = None,
        platform: str = "",
        id_kind: str = "",
        external_id: str = "",
    ) -> bool:
        """供决策层线索推送核对：本批某条撤回短语是否刚刚在记账侧完成了有效撤回。"""
        with self._state_lock:
            if message_id and message_id in self._session_retracted_msg_ids:
                return True
            if external_id and (platform, id_kind, external_id) in self._session_retracted_senders:
                return True
            return False

    # -------------------- 记账入口判定（判场次之前） --------------------

    async def process_incoming_viewer_message(
        self,
        *,
        platform: str,
        id_kind: str,
        external_id: str,
        open_app_id: str = "",
        user_name: str = "",
        message_type: str,
        content: str = "",
        message_id: Optional[str] = None,
        simulated: bool = False,
        timestamp_ms: Optional[int] = None,
    ) -> IncomingDisposition:
        """观众四类消息进入记账器时的第一步处理。"""
        now = int(timestamp_ms) if timestamp_ms is not None else int(self._clock())
        disposition, schedule_timer = await run_in_executor(
            self._process_incoming_blocking,
            platform,
            id_kind,
            external_id,
            open_app_id,
            user_name,
            message_type,
            content,
            message_id,
            simulated,
            now,
        )
        if schedule_timer and not self._closed:
            self._schedule_grace_fallback()
        return disposition

    def _process_incoming_blocking(
        self,
        platform: str,
        id_kind: str,
        external_id: str,
        open_app_id: str,
        user_name: str,
        message_type: str,
        content: str,
        message_id: Optional[str],
        simulated: bool,
        at_ms: int,
    ) -> Tuple[IncomingDisposition, bool]:
        secret_key = self._require_key()
        valid_id = id_kind in ("account", "open") and bool(external_id)

        # 第一步：非模拟弹幕检查"忘了我"与撤回命令
        if not simulated and message_type == "danmaku":
            if self.is_forget_command(content):
                with self._manager.transaction(immediate=True) as conn:
                    viewer = _find_viewer_row(conn, platform, id_kind, external_id, open_app_id) if valid_id else None
                    pairs: Set[Tuple[str, str]] = set()
                    if valid_id:
                        pairs.add((id_kind, external_id))
                    now_val = int(self._clock())
                    if viewer is not None:
                        if viewer["account_id"]:
                            pairs.add(("account", str(viewer["account_id"])))
                        if viewer["open_id"]:
                            pairs.add(("open", str(viewer["open_id"])))
                        conn.execute(
                            "UPDATE viewers SET forget_requested_at_ms = COALESCE(forget_requested_at_ms, ?)"
                            " WHERE id = ?",
                            (now_val, int(viewer["id"])),
                        )
                    for k, ext in pairs:
                        digest = compute_forget_hash(secret_key, k, ext)
                        conn.execute(
                            "INSERT INTO forget_list(id_hash, at_ms) VALUES (?, ?)"
                            " ON CONFLICT(id_hash) DO UPDATE SET at_ms = excluded.at_ms",
                            (sqlite3.Binary(digest), now_val),
                        )
                self.add_masked_name(user_name)
                with self._state_lock:
                    self._forget_requests += 1
                    req_total = self._forget_requests
                    if valid_id:
                        self._session_forget_senders.add((platform, id_kind, external_id))
                        self._session_retracted_senders.discard((platform, id_kind, external_id))
                logger.info(f"已受理观众遗忘命令（立即停止记录，下播后或 24h 内删除；累计请求={req_total}）")
                return "forget_command", viewer is not None

            if self.is_retract_command(content) and valid_id:
                with self._manager.transaction(immediate=True) as conn:
                    viewer = _find_viewer_row(conn, platform, id_kind, external_id, open_app_id)
                    if viewer is not None and viewer["forget_requested_at_ms"] is not None:
                        pairs_to_remove: Set[Tuple[str, str]] = {(id_kind, external_id)}
                        if viewer["account_id"]:
                            pairs_to_remove.add(("account", str(viewer["account_id"])))
                        if viewer["open_id"]:
                            pairs_to_remove.add(("open", str(viewer["open_id"])))
                        for k, ext in pairs_to_remove:
                            digest = compute_forget_hash(secret_key, k, ext)
                            conn.execute("DELETE FROM forget_list WHERE id_hash = ?", (sqlite3.Binary(digest),))
                        conn.execute(
                            "UPDATE viewers SET forget_requested_at_ms = NULL WHERE id = ?",
                            (int(viewer["id"]),),
                        )
                        with self._state_lock:
                            self._retractions += 1
                            ret_total = self._retractions
                            if message_id:
                                self._session_retracted_msg_ids.add(message_id)
                            self._session_retracted_senders.add((platform, id_kind, external_id))
                        logger.info(f"观众在宽限期内撤回遗忘请求（恢复正常记录；累计撤回={ret_total}）")
                        return "retracted", False

                    digest = compute_forget_hash(secret_key, id_kind, external_id)
                    in_list = (
                        conn.execute(
                            "SELECT 1 FROM forget_list WHERE id_hash = ?", (sqlite3.Binary(digest),)
                        ).fetchone()
                        is not None
                    )
                if in_list:
                    self.add_masked_name(user_name)
                    with self._state_lock:
                        self._late_retractions += 1
                        late_total = self._late_retractions
                    logger.info(
                        f"不记录名单观众发撤回短语但无待执行请求（宽限期已过或未建档，不予撤回；"
                        f"累计过期撤回={late_total}）"
                    )
                    return "forget_listed", False

        # 第二步：查不记录名单（覆盖四类观众消息）
        if valid_id:
            digest = compute_forget_hash(secret_key, id_kind, external_id)
            with self._manager.transaction() as conn:
                hit = (
                    conn.execute("SELECT 1 FROM forget_list WHERE id_hash = ?", (sqlite3.Binary(digest),)).fetchone()
                    is not None
                )
            if hit:
                self.add_masked_name(user_name)
                return "forget_listed", False

        return "normal", False

    def _schedule_grace_fallback(self) -> None:
        """挂一个 24 小时后的宽限删除检查定时任务（进程在线且一直没新场次下播时兜底）。"""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        async def _delayed() -> None:
            try:
                await asyncio.sleep(GRACE_DELETE_MAX_MS / 1000.0)
                if not self._closed:
                    await self.execute_due_forget_deletions()
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001 后台定时兜底失败记日志
                logger.error(f"24 小时宽限删除兜底检查失败: {exc}", exc=True)

        task = loop.create_task(_delayed(), name="PrivacyService.grace_delete_fallback")
        self._timer_tasks.add(task)
        task.add_done_callback(self._timer_tasks.discard)

    # -------------------- 宽限删除执行与运营者操作 --------------------

    async def execute_due_forget_deletions(
        self,
        *,
        now_ms: Optional[int] = None,
        force_after_stream_end: bool = False,
    ) -> int:
        """执行所有已满足宽限期条件（发令后已有场次下播，或已满 24 小时）的待删除请求。"""
        current = int(now_ms) if now_ms is not None else int(self._clock())
        due_ids = await run_in_executor(self._list_due_forget_viewer_ids_blocking, current, force_after_stream_end)
        deleted_count = 0
        for vid in due_ids:
            if await self.delete_viewer(viewer_id=vid, add_to_forget_list=True, at_ms=current):
                deleted_count += 1
        if deleted_count > 0:
            with self._state_lock:
                self._deletions_executed += deleted_count
                exec_total = self._deletions_executed
            logger.info(f"宽限期满已执行观众删除: 本次={deleted_count}, 累计执行={exec_total}")
        return deleted_count

    def _list_due_forget_viewer_ids_blocking(self, current_ms: int, force_after_stream_end: bool) -> List[int]:
        with self._manager.transaction() as conn:
            if force_after_stream_end:
                rows = conn.execute(
                    "SELECT id FROM viewers WHERE forget_requested_at_ms IS NOT NULL ORDER BY id ASC"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id FROM viewers"
                    " WHERE forget_requested_at_ms IS NOT NULL"
                    "   AND ((? - forget_requested_at_ms) >= ?"
                    "        OR EXISTS ("
                    "            SELECT 1 FROM live_sessions ls"
                    "            WHERE ls.ended_at_ms IS NOT NULL"
                    "              AND ls.ended_at_ms >= viewers.forget_requested_at_ms"
                    "        ))"
                    " ORDER BY id ASC",
                    (current_ms, GRACE_DELETE_MAX_MS),
                ).fetchall()
            return [int(r["id"]) for r in rows]

    async def retract_pending_forget(self, *, viewer_id: int) -> bool:
        """运营者撤回某位观众尚未执行的删除请求，并移出不记录名单。"""
        return await run_in_executor(self._retract_pending_forget_blocking, int(viewer_id))

    def _retract_pending_forget_blocking(self, viewer_id: int) -> bool:
        secret_key = self._require_key()
        with self._manager.transaction(immediate=True) as conn:
            row = conn.execute(
                "SELECT id, account_id, open_id, forget_requested_at_ms FROM viewers WHERE id = ?",
                (viewer_id,),
            ).fetchone()
            if row is None or row["forget_requested_at_ms"] is None:
                return False
            if row["account_id"]:
                digest = compute_forget_hash(secret_key, "account", str(row["account_id"]))
                conn.execute("DELETE FROM forget_list WHERE id_hash = ?", (sqlite3.Binary(digest),))
            if row["open_id"]:
                digest = compute_forget_hash(secret_key, "open", str(row["open_id"]))
                conn.execute("DELETE FROM forget_list WHERE id_hash = ?", (sqlite3.Binary(digest),))
            conn.execute("UPDATE viewers SET forget_requested_at_ms = NULL WHERE id = ?", (viewer_id,))
        with self._state_lock:
            self._retractions += 1
        logger.info(f"已撤回待执行的观众遗忘请求: viewer_id={viewer_id}")
        return True

    async def count_forget_list(self) -> int:
        """返回不记录名单当前条数（只暴露数量，不返回摘要本身）。"""

        def _exec() -> int:
            with self._manager.transaction() as conn:
                row = conn.execute("SELECT COUNT(*) AS n FROM forget_list").fetchone()
                return int(row["n"]) if row else 0

        return await run_in_executor(_exec)

    async def remove_from_forget_list(self, *, id_kind: IdKind, external_id: str) -> bool:
        """按外部 ID 种类与明文 ID 从不记录名单移出；若仍有未执行的宽限删除标记一并清除。"""
        ext = (external_id or "").strip()
        if not ext:
            return False

        def _exec() -> bool:
            secret_key = self._require_key()
            digest = compute_forget_hash(secret_key, id_kind, ext)
            with self._manager.transaction(immediate=True) as conn:
                cur = conn.execute("DELETE FROM forget_list WHERE id_hash = ?", (sqlite3.Binary(digest),))
                col = "account_id" if id_kind == "account" else "open_id"
                conn.execute(
                    f"UPDATE viewers SET forget_requested_at_ms = NULL WHERE {col} = ?",  # noqa: S608 常量列名
                    (ext,),
                )
                return cur.rowcount > 0

        removed = await run_in_executor(_exec)
        if removed:
            logger.info(f"运营者已解除不记录名单条目（id_kind={id_kind}）")
        return removed

    async def is_external_id_forgotten(self, *, id_kind: str, external_id: str) -> bool:
        """查询某个外部 ID 是否在不记录名单中。"""
        if id_kind not in ("account", "open") or not external_id:
            return False

        def _exec() -> bool:
            secret_key = self._require_key()
            digest = compute_forget_hash(secret_key, id_kind, external_id)
            with self._manager.transaction() as conn:
                return (
                    conn.execute("SELECT 1 FROM forget_list WHERE id_hash = ?", (sqlite3.Binary(digest),)).fetchone()
                    is not None
                )

        return await run_in_executor(_exec)

    # -------------------- 删除函数 --------------------

    async def delete_viewer(
        self,
        *,
        viewer_id: int,
        add_to_forget_list: bool = False,
        at_ms: Optional[int] = None,
    ) -> bool:
        """删除一位观众及其全部关联痕迹（唯一删除入口）。"""
        stamp = int(at_ms) if at_ms is not None else int(self._clock())
        return await run_in_executor(self._delete_viewer_blocking, int(viewer_id), add_to_forget_list, stamp)

    def _delete_viewer_blocking(self, viewer_id: int, add_to_forget_list: bool, at_ms: int) -> bool:
        secret_key = self._require_key() if add_to_forget_list else None
        all_valid_names: Set[str] = set()
        all_viewer_ids: List[int] = []

        with self._manager.transaction(immediate=True) as conn:
            target = conn.execute("SELECT * FROM viewers WHERE id = ?", (viewer_id,)).fetchone()
            if target is None:
                return False

            ext_ids: Set[str] = set()
            if target["account_id"]:
                ext_ids.add(str(target["account_id"]))
                if add_to_forget_list and secret_key is not None:
                    h = compute_forget_hash(secret_key, "account", str(target["account_id"]))
                    conn.execute(
                        "INSERT INTO forget_list(id_hash, at_ms) VALUES (?, ?)"
                        " ON CONFLICT(id_hash) DO UPDATE SET at_ms = excluded.at_ms",
                        (sqlite3.Binary(h), at_ms),
                    )
            if target["open_id"]:
                ext_ids.add(str(target["open_id"]))
                if add_to_forget_list and secret_key is not None:
                    h = compute_forget_hash(secret_key, "open", str(target["open_id"]))
                    conn.execute(
                        "INSERT INTO forget_list(id_hash, at_ms) VALUES (?, ?)"
                        " ON CONFLICT(id_hash) DO UPDATE SET at_ms = excluded.at_ms",
                        (sqlite3.Binary(h), at_ms),
                    )

            sim_ids: List[int] = []
            if ext_ids:
                placeholders = ",".join("?" for _ in ext_ids)
                ext_list = list(ext_ids)
                sim_rows = conn.execute(
                    f"SELECT id FROM viewers WHERE simulated = 1 AND id <> ?"  # noqa: S608
                    f" AND (account_id IN ({placeholders}) OR open_id IN ({placeholders}))",
                    [viewer_id, *ext_list, *ext_list],
                ).fetchall()
                sim_ids = [int(r["id"]) for r in sim_rows]

            all_viewer_ids = [viewer_id, *sim_ids]
            vid_placeholders = ",".join("?" for _ in all_viewer_ids)

            name_windows: dict[str, Tuple[int, int]] = {}
            for r in conn.execute(
                f"SELECT user_name, MIN(first_seen_ms) AS f_ms, MAX(last_seen_ms) AS l_ms"  # noqa: S608
                f" FROM viewer_names WHERE viewer_id IN ({vid_placeholders}) GROUP BY user_name",
                all_viewer_ids,
            ).fetchall():
                uname = str(r["user_name"] or "").strip()
                if len(uname) >= MIN_MASK_NAME_LEN and not is_placeholder_name(uname):
                    name_windows[uname] = (int(r["f_ms"]), int(r["l_ms"]))

            for vrow in conn.execute(
                f"SELECT user_name, name_seen_ms, last_active_ms FROM viewers WHERE id IN ({vid_placeholders})",  # noqa: S608
                all_viewer_ids,
            ).fetchall():
                uname = str(vrow["user_name"] or "").strip()
                if len(uname) >= MIN_MASK_NAME_LEN and not is_placeholder_name(uname):
                    lo = int(vrow["name_seen_ms"] or 0)
                    hi = max(lo, int(vrow["last_active_ms"] or 0))
                    if uname in name_windows:
                        prev_lo, prev_hi = name_windows[uname]
                        name_windows[uname] = (min(prev_lo, lo), max(prev_hi, hi))
                    else:
                        name_windows[uname] = (lo, hi)

            all_valid_names = set(name_windows.keys())

            # 按昵称从长到短处理，避免短曾用名把长当前名截半
            for uname in sorted(name_windows.keys(), key=len, reverse=True):
                first_ms, last_ms = name_windows[uname]
                session_ids = _find_sessions_for_nickname(
                    conn,
                    viewer_ids=all_viewer_ids,
                    user_name=uname,
                    first_ms=first_ms,
                    last_ms=last_ms,
                )
                if session_ids:
                    _replace_nickname_in_sessions(conn, session_ids=session_ids, nickname=uname)

            conn.execute(
                f"DELETE FROM live_chat WHERE sender_role = 'viewer' AND viewer_id IN ({vid_placeholders})",  # noqa: S608
                all_viewer_ids,
            )
            if ext_ids:
                ext_ph = ",".join("?" for _ in ext_ids)
                conn.execute(
                    f"DELETE FROM live_chat WHERE sender_role = 'viewer' AND simulated = 1 AND sender_id IN ({ext_ph})",  # noqa: S608
                    list(ext_ids),
                )

            _scrub_payment_tables_for_viewer(conn, viewer_ids=all_viewer_ids, external_ids=list(ext_ids))

            if table_exists(conn, "commitments"):
                conn.execute(
                    f"DELETE FROM commitments WHERE viewer_id IN ({vid_placeholders})",  # noqa: S608
                    all_viewer_ids,
                )

            if table_exists(conn, "viewer_profiles"):
                conn.execute(
                    f"DELETE FROM viewer_profiles WHERE viewer_id IN ({vid_placeholders})",  # noqa: S608
                    all_viewer_ids,
                )

            conn.execute(
                f"UPDATE viewers SET link_status = 'confirmed', link_candidate_id = NULL"  # noqa: S608
                f" WHERE link_candidate_id IN ({vid_placeholders})",
                all_viewer_ids,
            )
            conn.execute(f"DELETE FROM viewer_names WHERE viewer_id IN ({vid_placeholders})", all_viewer_ids)  # noqa: S608
            conn.execute(f"DELETE FROM viewers WHERE id IN ({vid_placeholders})", all_viewer_ids)  # noqa: S608

        _scrub_legacy_memory_files(
            memory_dir=self._db_path.parent / "memory",
            viewer_ids=set(all_viewer_ids),
            nicknames=all_valid_names,
        )

        conn = self._manager.connection()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return True


# -------------------- 内部 SQL 辅助函数 --------------------


def _find_viewer_row(
    conn: sqlite3.Connection,
    platform: str,
    id_kind: str,
    external_id: str,
    open_app_id: str,
) -> Optional[sqlite3.Row]:
    if id_kind == "account":
        return conn.execute(
            "SELECT id, account_id, open_id, forget_requested_at_ms FROM viewers WHERE platform=? AND account_id=?",
            (platform, external_id),
        ).fetchone()
    return conn.execute(
        "SELECT id, account_id, open_id, forget_requested_at_ms FROM viewers"
        " WHERE platform=? AND open_id=? AND (open_app_id=? OR open_app_id='')"
        " ORDER BY (open_app_id=?) DESC LIMIT 1",
        (platform, external_id, open_app_id, open_app_id),
    ).fetchone()


def _find_sessions_for_nickname(
    conn: sqlite3.Connection,
    *,
    viewer_ids: Sequence[int],
    user_name: str,
    first_ms: int,
    last_ms: int,
) -> List[int]:
    """查找与某昵称使用期有时间重叠、或该观众曾以该昵称留下明细行的全部场次 ID。"""
    sids: Set[int] = set()
    for row in conn.execute(
        "SELECT id FROM live_sessions WHERE started_at_ms <= ? AND (ended_at_ms IS NULL OR ended_at_ms >= ?)",
        (last_ms, first_ms),
    ).fetchall():
        sids.add(int(row["id"]))

    vid_ph = ",".join("?" for _ in viewer_ids)
    for table, name_col in (
        ("live_chat", "sender_name"),
        ("gifts", "user_name"),
        ("super_chats", "user_name"),
        ("guards", "user_name"),
    ):
        for row in conn.execute(
            f"SELECT DISTINCT live_session_id FROM {table}"  # noqa: S608 常量表名/列名
            f" WHERE viewer_id IN ({vid_ph}) AND {name_col} = ?",
            [*viewer_ids, user_name],
        ).fetchall():
            if row["live_session_id"] is not None:
                sids.add(int(row["live_session_id"]))
    return sorted(sids)


def _replace_nickname_in_sessions(
    conn: sqlite3.Connection,
    *,
    session_ids: Sequence[int],
    nickname: str,
) -> None:
    """在指定场次集合内把主播台词、话题、摘要、档案、承诺及旧回顾里的昵称替换为"一位观众"。"""
    sid_ph = ",".join("?" for _ in session_ids)
    like_pat = f"%{nickname}%"

    conn.execute(
        f"UPDATE live_chat SET content = REPLACE(content, ?, ?)"  # noqa: S608
        f" WHERE live_session_id IN ({sid_ph}) AND sender_role = 'assistant' AND content LIKE ?",
        [nickname, MASKED_VIEWER_LABEL, *session_ids, like_pat],
    )
    conn.execute(
        f"UPDATE topics SET label = REPLACE(label, ?, ?)"  # noqa: S608
        f" WHERE live_session_id IN ({sid_ph}) AND label LIKE ?",
        [nickname, MASKED_VIEWER_LABEL, *session_ids, like_pat],
    )
    conn.execute(
        f"UPDATE timeline_summary SET summary = REPLACE(summary, ?, ?)"  # noqa: S608
        f" WHERE live_session_id IN ({sid_ph}) AND summary LIKE ?",
        [nickname, MASKED_VIEWER_LABEL, *session_ids, like_pat],
    )

    if table_exists(conn, "archive_lines"):
        conn.execute(
            f"UPDATE archive_lines SET"  # noqa: S608
            f" text = REPLACE(text, ?, ?), quote = REPLACE(quote, ?, ?)"
            f" WHERE live_session_id IN ({sid_ph}) AND (text LIKE ? OR quote LIKE ?)",
            [nickname, MASKED_VIEWER_LABEL, nickname, MASKED_VIEWER_LABEL, *session_ids, like_pat, like_pat],
        )

    if table_exists(conn, "commitments"):
        conn.execute(
            f"UPDATE commitments SET"  # noqa: S608
            f" what = REPLACE(what, ?, ?),"
            f" quote = REPLACE(quote, ?, ?),"
            f" close_quote = CASE WHEN close_quote IS NOT NULL THEN REPLACE(close_quote, ?, ?) ELSE NULL END"
            f" WHERE (created_session_id IN ({sid_ph})"
            f"        OR close_chat_id IN (SELECT id FROM live_chat WHERE live_session_id IN ({sid_ph})))"
            f"   AND (what LIKE ? OR quote LIKE ? OR close_quote LIKE ?)",
            [
                nickname,
                MASKED_VIEWER_LABEL,
                nickname,
                MASKED_VIEWER_LABEL,
                nickname,
                MASKED_VIEWER_LABEL,
                *session_ids,
                *session_ids,
                like_pat,
                like_pat,
                like_pat,
            ],
        )

    if table_exists(conn, "session_recaps"):
        conn.execute(
            f"UPDATE session_recaps SET recap_text = REPLACE(recap_text, ?, ?)"  # noqa: S608
            f" WHERE live_session_id IN ({sid_ph}) AND recap_text LIKE ?",
            [nickname, MASKED_VIEWER_LABEL, *session_ids, like_pat],
        )


def _scrub_payment_tables_for_viewer(
    conn: sqlite3.Connection,
    *,
    viewer_ids: Sequence[int],
    external_ids: Sequence[str],
) -> None:
    vid_ph = ",".join("?" for _ in viewer_ids)
    ext_clause = ""
    ext_params: List[object] = []
    if external_ids:
        ext_ph = ",".join("?" for _ in external_ids)
        ext_clause = f" OR user_id IN ({ext_ph})"
        ext_params = list(external_ids)

    params: List[object] = [*viewer_ids, *ext_params]
    conn.execute(
        f"UPDATE gifts SET viewer_id = NULL, user_id = '', user_name = '', id_kind = '',"  # noqa: S608
        f" fans_medal_name = '', raw_data = NULL WHERE viewer_id IN ({vid_ph}){ext_clause}",
        params,
    )
    conn.execute(
        f"UPDATE super_chats SET viewer_id = NULL, user_id = '', user_name = '', id_kind = '',"  # noqa: S608
        f" message = '', fans_medal_name = '', raw_data = NULL WHERE viewer_id IN ({vid_ph}){ext_clause}",
        params,
    )
    conn.execute(
        f"UPDATE guards SET viewer_id = NULL, user_id = '', user_name = '', id_kind = '',"  # noqa: S608
        f" fans_medal_name = '', raw_data = NULL WHERE viewer_id IN ({vid_ph}){ext_clause}",
        params,
    )


def _scrub_legacy_note_markdown(text: str, *, viewer_ids: Set[int], nicknames: Set[str]) -> str:
    """移除旧 MEMORY.md 中指向指定观众编号的条目，并对其余文本替换昵称（过渡期辅助，阶段 4 删除）。"""
    if not text:
        return text
    viewer_tag_re = re.compile(r"^对\s*.*?#(\d+)$")

    def _targets_deleted_viewer(line: str) -> bool:
        m = _LEGACY_NOTE_TAG_RE.search(line)
        if not m:
            return False
        for token in (part.strip() for part in m.group(1).split("·")):
            vm = viewer_tag_re.match(token)
            if vm and int(vm.group(1)) in viewer_ids:
                return True
        return False

    preamble: List[str] = []
    sections: List[Tuple[str, List[str]]] = []
    current_header: Optional[str] = None
    current_lines: List[str] = []

    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("## "):
            if current_header is not None and any(item.strip() for item in current_lines):
                sections.append((current_header, current_lines))
            current_header = raw_line
            current_lines = []
            continue
        if current_header is None:
            if not _targets_deleted_viewer(stripped):
                preamble.append(raw_line)
        else:
            if not stripped:
                continue
            if _targets_deleted_viewer(stripped):
                continue
            current_lines.append(raw_line)

    if current_header is not None and any(item.strip() for item in current_lines):
        sections.append((current_header, current_lines))

    blocks: List[str] = []
    pre_text = "\n".join(preamble).strip()
    if pre_text:
        blocks.append(pre_text)
    for header, lines in sections:
        blocks.append("\n".join([header, *lines]))
    rebuilt = "\n\n".join(blocks)
    rebuilt = mask_names_in_text(rebuilt, nicknames, replacement=MASKED_VIEWER_LABEL)
    return (rebuilt + "\n") if rebuilt else ""


def _scrub_legacy_memory_files(
    *,
    memory_dir: Path,
    viewer_ids: Set[int],
    nicknames: Set[str],
) -> None:
    """清理 data/memory/ 下 MEMORY.md、MEMORY.rehearsal.md 与 history/ 快照及变更记录（阶段 2–3b 过渡期）。"""
    if not memory_dir.exists() or (not viewer_ids and not nicknames):
        return
    for md_file in memory_dir.rglob("*.md"):
        try:
            original = md_file.read_bytes().decode("utf-8-sig")
            cleaned = _scrub_legacy_note_markdown(original, viewer_ids=viewer_ids, nicknames=nicknames)
            if cleaned != original:
                tmp = md_file.with_name(md_file.name + ".tmp")
                tmp.write_bytes(cleaned.encode("utf-8"))
                os.replace(tmp, md_file)
        except Exception as exc:  # noqa: BLE001 单个快照文件清理异常记录日志继续
            logger.warning(f"清理旧记忆文件失败（{md_file}）: {exc}", exc=True)

    for jsonl_file in memory_dir.rglob("*.jsonl"):
        try:
            original = jsonl_file.read_bytes().decode("utf-8-sig")
            cleaned = mask_names_in_text(original, nicknames, replacement=MASKED_VIEWER_LABEL)
            for vid in viewer_ids:
                cleaned = re.sub(rf"对\s*[^\s#·\"]*#{vid}\b", f"对 {MASKED_VIEWER_LABEL}", cleaned)
            if cleaned != original:
                tmp = jsonl_file.with_name(jsonl_file.name + ".tmp")
                tmp.write_bytes(cleaned.encode("utf-8"))
                os.replace(tmp, jsonl_file)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"清理旧记忆变更记录失败（{jsonl_file}）: {exc}", exc=True)


__all__ = [
    "GRACE_DELETE_MAX_MS",
    "IncomingDisposition",
    "MASKED_VIEWER_LABEL",
    "MIN_MASK_NAME_LEN",
    "PrivacyService",
    "PrivacyStats",
    "compute_forget_hash",
    "mask_names_in_text",
    "matches_phrase_table",
]
