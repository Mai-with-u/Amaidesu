"""观众隐私与自主遗忘服务：命令判定、不记录名单与密钥、场内遮名、宽限删除与完整删除函数。

核心契约：
- **命令与撤回判定**：存储与决策侧共用纯函数 ``matches_phrase_table``。先剥掉认得的 B 站文字表情码
  （``[微笑]`` / ``[doge]`` 这类；认不出的方括号内容按正文算）与 ``@某人`` 提及，再按 Unicode
  意义切小句——字母、数字、汉字、假名等连续段算一个词，标点/空格/符号/表情作分隔；每个小句剥掉开头的称呼（"主播"与配置里的主播名），
  空小句丢弃；剩余小句非空且**全部**在短语表内才算命中。
- **不记录名单（forget_list）**：只存 ``HMAC-SHA256(库外密钥, 种类|外部ID)``，不含应用编号。启动时校验库外密钥文件：
  文件缺失且名单为空时自动生成 32 字节随机密钥；文件缺失但名单非空时直接抛错阻止启动，给出找回密钥或清空名单两条出路。
- **宽限删除**：观众发"忘了我"后立即进不记录名单、停止记录并在观众行记 ``forget_requested_at_ms``；
  实际删除推迟到发令之后第一次下播后的清理（或最迟发令后 24 小时），执行前本人发撤回短语即可移出名单、清掉待删标记。
- **删除函数**：单一事务清除观众行、曾用名、弹幕、对 ta 的承诺、相同外部 ID 的模拟回放副本，清空付费三表的身份/留言/原始消息，
  按每个昵称的使用期找出重叠或出现过的场次，把这些场次的主播台词、档案、承诺、话题摘要、时间线摘要、整理报告、游戏事件正文与生物死亡说明里的昵称
  替换为"一位观众"，最后执行 ``PRAGMA wal_checkpoint(TRUNCATE)``。过渡期的旧画像表与旧笔记目录已由 v25 迁移整体删除。
- **游戏玩家**：删玩家时他的互动、公屏聊天、对他的承诺、待审自述随外键级联删掉，名字在他出现过的场次里换成
  "一位玩家"，UUID 的摘要（种类 ``<游戏>_uuid``）进不记录名单，之后他再出现也不再记。删观众、"忘了我"时，
  运营者确认过的关联玩家一并删掉。玩家在游戏公屏发"忘了我"也算：同一套口令、同一个宽限期，到期删这位
  玩家以及运营者确认过的关联观众。
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Dict, Iterable, List, Literal, Optional, Sequence, Set, Tuple

from src.modules.config.storage_schemas import DEFAULT_FORGET_PHRASES, DEFAULT_RETRACT_PHRASES
from src.modules.logging import get_logger
from src.modules.storage.connection import SQLiteConnectionManager
from src.modules.storage.migrations._common import column_exists, table_exists
from src.modules.storage.repos.viewers import IdKind, is_placeholder_name
from src.modules.storage.run_in_executor import run_in_executor
from src.modules.utils.time_utils import now_ms

logger = get_logger("PrivacyService")

# 替换昵称、遮名、回想遮名的最短长度（单字昵称不替换，防误伤日常用字）
MIN_MASK_NAME_LEN: int = 2
# 宽限删除最迟执行时限（发令后 24 小时）
GRACE_DELETE_MAX_MS: int = 24 * 3_600_000
MASKED_VIEWER_LABEL: str = "一位观众"
MASKED_PLAYER_LABEL: str = "一位玩家"

IncomingDisposition = Literal["forget_command", "retracted", "forget_listed", "normal"]

# 方括号包起来的短标签（[微笑]、[doge]…）：是认得的 B 站文字表情码才在判定命令前整段剥掉
_TEXT_EMOTICON_RE = re.compile(r"[\[［]([^\[\]［］]{1,20})[\]］]")
# 认得的 B 站文字表情码名（小黄脸与常用系统表情；比较时忽略大小写）。只剥认得的：方括号里
# 也可能是正文（"忘了我[不是]"），一律剥掉会把它当成遗忘命令；认不出的表情最多让这条
# 命令没生效、观众去掉表情再发一次即可——误删一位观众的代价远大于少认一次命令
_KNOWN_TEXT_EMOTICONS = frozenset(
    name.casefold()
    for name in (
        "微笑 呲牙 笑 大笑 偷笑 笑哭 喜极而泣 哈哈 滑稽 奸笑 坏笑 阴险 调皮 歪嘴 傲娇 害羞 星星眼 喜欢 爱心 爱 "
        "比心 给心心 亲亲 嘟嘟 惊喜 惊讶 吃惊 疑惑 思考 无语 尴尬 捂脸 囧 呆 抠鼻 委屈 难过 大哭 哭泣 生气 "
        "抓狂 撇嘴 嫌弃 翻白眼 辣眼睛 呕吐 吐 晕 疼 冷 生病 口罩 吓 灵魂出窍 哈欠 睡觉 奋斗 加油 鼓掌 赞 点赞 "
        "打call OK 妙啊 妙 哇 哦呼 酸了 吃瓜 嗑瓜子 墨镜 再见 捂眼 支持 花 鲜花 礼物 手机 dog doge "
        "狗头 脱单doge"
    ).split()
)
# 小电视系列表情码统一带这个前缀（[tv_微笑]、[tv_doge]…）
_TV_EMOTICON_PREFIX = "tv_"
# 剥除独立书写的 @提及（如 "@路人甲 忘了我"）：@ 在句首或分隔符后，一直取到下一个分隔符
_AT_MENTION_RE = re.compile(r"(?:^|(?<=[\s,，。！!?？~～、：:；;]))@[^\s,，。！!?？~～、：:；;@]+")


def _normalize_phrase(text: str) -> str:
    return text.strip().casefold()


def _split_clauses(text: str) -> List[str]:
    """按 Unicode 意义切小句：字母、数字、汉字、假名等连续段算一个词，其余字符作分隔。

    用字符自身的 Unicode 类别（``str.isalnum``）而不是 ASCII 白名单：全角数字/字母、假名
    都算词内字符，不会被误当成标点切开（老口径只认 ASCII，会把这几种写成两个小句）。
    """
    clauses: List[str] = []
    current: List[str] = []
    for char in text:
        if char.isalnum():
            current.append(char)
        elif current:
            clauses.append("".join(current))
            current = []
    if current:
        clauses.append("".join(current))
    return clauses


def _strip_known_emoticon(match: "re.Match[str]") -> str:
    """认得的文字表情码换成分隔用的空格；认不出的原样留下，方括号里的字照常参与判定。"""
    name = match.group(1).strip().casefold()
    if name in _KNOWN_TEXT_EMOTICONS or name.startswith(_TV_EMOTICON_PREFIX):
        return " "
    return match.group(0)


def _strip_mentions(content: str, callouts: Sequence[str]) -> str:
    """剥掉命令判定里的 ``@提及``，含与正文粘连的写法（@麦麦忘了我）。

    先剥 "@ + 称呼"（称呼表 = 主播与配置的主播名）：剥完只留下称呼后面的正文，
    所以粘连写法也能命中；再剥独立书写的 @某人；最后把残余的 @ 当分隔符，
    避免漏掉无法判断名字边界的提及。
    """
    text = _TEXT_EMOTICON_RE.sub(_strip_known_emoticon, content)
    for callout in callouts:
        text = re.sub(rf"[@＠]\s*{re.escape(callout)}", " ", text, flags=re.IGNORECASE)
    text = _AT_MENTION_RE.sub(" ", text)
    for mark in ("@", "＠"):
        text = text.replace(mark, " ")
    return text


def matches_phrase_table(
    content: str,
    phrases: Sequence[str],
    *,
    bot_name: str = "",
) -> bool:
    """判定弹幕是否严格由短语表中的短语组成。

    步骤：剥掉认得的 B 站文字表情码（``[微笑]`` 这类）与 ``@某人`` 提及（含粘连写法）→ 按 Unicode
    意义切小句 → 每小句剥掉开头的称呼（"主播"与 ``bot_name``）→ 丢弃空小句 → 剩余至少一句
    且每句都在 ``phrases`` 中。表情码、@提及、标点都只起分隔作用，不参与命中判断。
    """
    if not content or not phrases:
        return False
    phrase_set = {_normalize_phrase(p) for p in phrases if p and p.strip()}
    if not phrase_set:
        return False

    callouts = ["主播"]
    clean_bot = (bot_name or "").strip()
    if clean_bot:
        for part in _split_clauses(clean_bot):
            if part and part not in callouts:
                callouts.append(part)
    callouts.sort(key=len, reverse=True)

    raw_clauses = _split_clauses(_strip_mentions(content, callouts))
    if not raw_clauses:
        return False

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


def player_forget_kind(game: str) -> str:
    """游戏玩家在不记录名单里的种类：游戏名加 ``_uuid``（如 ``minecraft_uuid``），和观众的账号种类分开。"""
    return f"{game}_uuid"


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
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._manager = manager
        self._db_path = Path(db_path)
        self._key_path = Path(key_path)
        self._forget_phrases: Tuple[str, ...] = tuple(forget_phrases)
        self._retract_phrases: Tuple[str, ...] = tuple(retract_phrases)
        self._bot_name = bot_name
        self._clock = clock
        # 24 小时兜底延时的等待函数：生产用 asyncio.sleep，测试注入假等待以确定性地推进时间
        self._sleep = sleep
        self._secret_key: Optional[bytes] = None

        self._state_lock = threading.Lock()
        # 本场发言过的不记录名单观众昵称（下播清空，落库前替换主播台词中的称呼）
        self._session_masked_names: Set[str] = set()
        # 本场在公屏开过口的不记录名单玩家名（下播清空；她的台词与公屏话落库前换成"一位玩家"）
        self._session_masked_player_names: Set[str] = set()
        # 发"忘了我"时还没有玩家行的玩家（不记录名单摘要 → 发令时刻）：宽限期里撤回要认得出他。
        # 只在内存里：进程重启后认不出，他就留在名单里（宁可多忘，不让别人删过的人借撤回恢复）
        self._rowless_player_forgets: Dict[bytes, int] = {}
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

    async def initialize(self, *, persist_key: bool = True) -> None:
        """校验或初始化库外密钥文件；缺失且名单非空时抛 RuntimeError 拒启。

        ``persist_key=False`` 供组合根冒烟（--dry）用：密钥文件缺失且名单为空时只用
        本次进程内的临时密钥，不建目录、不写文件；缺真密钥而名单非空时仍照常拒启。
        """
        await run_in_executor(self._initialize_blocking, persist_key)

    def _initialize_blocking(self, persist_key: bool = True) -> None:
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

        generated = secrets.token_bytes(32)
        if not persist_key:
            self._secret_key = generated
            logger.info(f"冒烟装配：密钥文件缺失且不记录名单为空，本次运行使用临时密钥、不落盘: {self._key_path}")
            return
        self._key_path.parent.mkdir(parents=True, exist_ok=True)
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

    async def wait_grace_timers(self) -> None:
        """等当前挂起的 24 小时宽限兜底任务结束（没有任务时立即返回）。

        正常关闭走 :meth:`close`（取消并回收）；本方法供确定性地等兜底检查跑完
        （例如测试注入假等待后直接观察删除结果），不改变任务本身的调度方式。
        """
        tasks = list(self._timer_tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

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

    def add_masked_player_name(self, name: Optional[str]) -> None:
        """把不记录名单玩家的名字加入本场遮名集合（2 字以上）：她之后的台词与公屏话里遮成"一位玩家"。"""
        text = (name or "").strip()
        if len(text) >= MIN_MASK_NAME_LEN:
            with self._state_lock:
                self._session_masked_player_names.add(text)

    def mask_speech(self, speech_text: str) -> str:
        """主播台词或她的公屏话落库前，把本场遮名集合里的观众昵称换成"一位观众"、玩家名换成"一位玩家"。"""
        with self._state_lock:
            names = list(self._session_masked_names)
            players = list(self._session_masked_player_names)
        text = speech_text
        if names:
            text = mask_names_in_text(text, names, replacement=MASKED_VIEWER_LABEL)
        if players:
            text = mask_names_in_text(text, players, replacement=MASKED_PLAYER_LABEL)
        return text

    def clear_session_masks(self) -> None:
        """下播时清空本场内存遮名集合与当场回执追踪。"""
        with self._state_lock:
            self._session_masked_names.clear()
            self._session_masked_player_names.clear()
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
                await self._sleep(GRACE_DELETE_MAX_MS / 1000.0)
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
        # 在游戏公屏发过"忘了我"的玩家：同一个宽限期，连同运营者确认过的关联观众一起删
        due_players = await run_in_executor(self._list_due_forget_player_ids_blocking, current, force_after_stream_end)
        for player_id in due_players:
            if await self.delete_player(
                player_id=player_id, add_to_forget_list=True, with_linked_viewers=True, at_ms=current
            ):
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

    def _list_due_forget_player_ids_blocking(self, current_ms: int, force_after_stream_end: bool) -> List[int]:
        """宽限期满的玩家：发令后已有场次下播，或已满 24 小时（与观众同一个判据）。"""
        with self._manager.transaction() as conn:
            if not table_exists(conn, "game_players"):
                return []
            if force_after_stream_end:
                rows = conn.execute(
                    "SELECT id FROM game_players WHERE forget_requested_at_ms IS NOT NULL ORDER BY id ASC"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id FROM game_players"
                    " WHERE forget_requested_at_ms IS NOT NULL"
                    "   AND ((? - forget_requested_at_ms) >= ?"
                    "        OR EXISTS ("
                    "            SELECT 1 FROM live_sessions ls"
                    "            WHERE ls.ended_at_ms IS NOT NULL"
                    "              AND ls.ended_at_ms >= game_players.forget_requested_at_ms"
                    "        ))"
                    " ORDER BY id ASC",
                    (current_ms, GRACE_DELETE_MAX_MS),
                ).fetchall()
            return [int(row["id"]) for row in rows]

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

    async def process_incoming_player_chat(
        self, *, game: str, uuid: str, name: str, content: str, at_ms: Optional[int] = None
    ) -> IncomingDisposition:
        """游戏公屏上一位核对过的玩家说的话进落库器时的第一步：发"忘了我"、撤回，或已经在不记录名单里。

        和观众同一套口令、同一个宽限期：发"忘了我"立即进不记录名单、停止记录，在他的玩家行记下时刻，
        发令后第一次下播的清理（最迟 24 小时）删掉他和运营者确认过的关联观众；宽限期内发撤回短语就移出
        名单、清掉时刻。返回值同观众：命令、撤回、在名单里、正常。
        """
        now = int(at_ms) if at_ms is not None else int(self._clock())
        disposition, schedule_timer = await run_in_executor(
            self._process_player_chat_blocking, game, uuid, name, content, now
        )
        if schedule_timer and not self._closed:
            self._schedule_grace_fallback()
        return disposition

    def _process_player_chat_blocking(
        self, game: str, uuid: str, name: str, content: str, at_ms: int
    ) -> Tuple[IncomingDisposition, bool]:
        digest = sqlite3.Binary(compute_forget_hash(self._require_key(), player_forget_kind(game), uuid))
        if self.is_forget_command(content):
            with self._manager.transaction(immediate=True) as conn:
                conn.execute(
                    "INSERT INTO forget_list(id_hash, at_ms) VALUES (?, ?)"
                    " ON CONFLICT(id_hash) DO UPDATE SET at_ms = excluded.at_ms",
                    (digest, at_ms),
                )
                marked = conn.execute(
                    "UPDATE game_players SET forget_requested_at_ms = COALESCE(forget_requested_at_ms, ?)"
                    " WHERE game = ? AND uuid = ?",
                    (at_ms, game, uuid),
                )
            # 她这一场之后的台词与公屏话里再提到他，落库前遮成"一位玩家"
            self.add_masked_player_name(name)
            with self._state_lock:
                self._forget_requests += 1
                if marked.rowcount == 0:
                    # 开口第一句就是"忘了我"：没有玩家行可以记时刻，记在内存里，宽限期内照样能撤回
                    self._rowless_player_forgets[bytes(digest)] = at_ms
            logger.info("已受理游戏玩家的遗忘命令（立即停止记录，下播后或 24h 内删除）")
            return "forget_command", marked.rowcount > 0
        if self.is_retract_command(content):
            with self._manager.transaction(immediate=True) as conn:
                cur = conn.execute(
                    "UPDATE game_players SET forget_requested_at_ms = NULL"
                    " WHERE game = ? AND uuid = ? AND forget_requested_at_ms IS NOT NULL",
                    (game, uuid),
                )
                rowless = cur.rowcount == 0 and self._rowless_forget_pending(conn, bytes(digest), at_ms)
                if cur.rowcount > 0 or rowless:
                    conn.execute("DELETE FROM forget_list WHERE id_hash = ?", (digest,))
            if cur.rowcount > 0 or rowless:
                with self._state_lock:
                    self._retractions += 1
                    self._rowless_player_forgets.pop(bytes(digest), None)
                logger.info("游戏玩家在宽限期内撤回遗忘请求（恢复记录）")
                return "retracted", False
        with self._manager.transaction() as conn:
            hit = conn.execute("SELECT 1 FROM forget_list WHERE id_hash = ?", (digest,)).fetchone() is not None
        if hit:
            self.add_masked_player_name(name)
            return "forget_listed", False
        return "normal", False

    def _rowless_forget_pending(self, conn: sqlite3.Connection, digest: bytes, now_ms: int) -> bool:
        """没有玩家行的那位发过的"忘了我"还在宽限期里吗：发令后还没有场次下播、也没满 24 小时。

        宽限期一过就不认（和有玩家行时到期删除同一个判据），名单里那一条从此留着。
        """
        with self._state_lock:
            requested = self._rowless_player_forgets.get(digest)
        if requested is None:
            return False
        ended = conn.execute(
            "SELECT 1 FROM live_sessions WHERE ended_at_ms IS NOT NULL AND ended_at_ms >= ? LIMIT 1", (requested,)
        ).fetchone()
        if ended is not None or now_ms - requested >= GRACE_DELETE_MAX_MS:
            with self._state_lock:
                self._rowless_player_forgets.pop(digest, None)
            return False
        return True

    async def is_player_forgotten(self, *, game: str, uuid: str) -> bool:
        """某位游戏玩家的 UUID 是否在不记录名单里（删过、或他发过"忘了我"）：在的话他再出现也不记。"""
        if not game or not uuid:
            return False

        def _exec() -> bool:
            digest = compute_forget_hash(self._require_key(), player_forget_kind(game), uuid)
            with self._manager.transaction() as conn:
                return (
                    conn.execute("SELECT 1 FROM forget_list WHERE id_hash = ?", (sqlite3.Binary(digest),)).fetchone()
                    is not None
                )

        return await run_in_executor(_exec)

    async def delete_player(
        self,
        *,
        player_id: int,
        add_to_forget_list: bool = True,
        with_linked_viewers: bool = False,
        at_ms: Optional[int] = None,
    ) -> bool:
        """删一位游戏玩家：他的互动、公屏聊天、对他的承诺、待审自述随外键级联删掉，名字换成"一位玩家"。

        默认把他的 UUID 加进不记录名单，之后他再出现也不再记。``with_linked_viewers`` 为真时，运营者
        确认过的关联观众一并删掉（他自己在游戏公屏发"忘了我"的情形，和观众同一个口径）。
        """
        stamp = int(at_ms) if at_ms is not None else int(self._clock())
        linked = await run_in_executor(
            self._delete_player_blocking, int(player_id), add_to_forget_list, with_linked_viewers, stamp
        )
        if linked is None:
            return False
        # 关联观众走完整的删观众路径（弹幕、对 ta 的承诺、昵称替换都在那里）
        for viewer_id in linked:
            await self.delete_viewer(viewer_id=viewer_id, add_to_forget_list=add_to_forget_list, at_ms=stamp)
        logger.info(f"已删除游戏玩家 player_id={player_id}（连带关联观众 {len(linked)} 位）")
        return True

    def _delete_player_blocking(
        self, player_id: int, add_to_forget_list: bool, with_linked_viewers: bool, at_ms: int
    ) -> Optional[List[int]]:
        secret_key = self._require_key() if add_to_forget_list else None
        with self._manager.transaction(immediate=True) as conn:
            linked = _linked_viewer_ids(conn, player_id) if with_linked_viewers else []
            if not _delete_player_rows(conn, player_id=player_id, secret_key=secret_key, at_ms=at_ms):
                return None
        self._truncate_wal("删除游戏玩家")
        return linked

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
                # 兜底：v18 回填按 (platform, sender_id) 精确匹配，平台不一致的历史行 viewer_id 仍为空。
                # 这些真实弹幕按外部 ID 再删一遍，否则 ta 的弹幕与昵称会留在库里
                conn.execute(
                    f"DELETE FROM live_chat WHERE sender_role = 'viewer' AND simulated = 0"  # noqa: S608
                    f" AND viewer_id IS NULL AND sender_id IN ({ext_ph})",
                    list(ext_ids),
                )
                conn.execute(
                    f"DELETE FROM live_chat WHERE sender_role = 'viewer' AND simulated = 1 AND sender_id IN ({ext_ph})",  # noqa: S608
                    list(ext_ids),
                )

            _scrub_payment_tables_for_viewer(conn, viewer_ids=all_viewer_ids, external_ids=list(ext_ids))

            # 运营者确认过"这位观众就是游戏里的某某"的，那位玩家一并删：他在游戏里的互动、公屏聊天、
            # 对他的承诺随之级联；加不加进不记录名单跟这位观众一致
            for player_id in _linked_player_ids(conn, all_viewer_ids):
                _delete_player_rows(conn, player_id=player_id, secret_key=secret_key, at_ms=at_ms)

            if table_exists(conn, "commitments"):
                conn.execute(
                    f"DELETE FROM commitments WHERE viewer_id IN ({vid_placeholders})",  # noqa: S608
                    all_viewer_ids,
                )

            conn.execute(
                f"UPDATE viewers SET link_status = 'confirmed', link_candidate_id = NULL"  # noqa: S608
                f" WHERE link_candidate_id IN ({vid_placeholders})",
                all_viewer_ids,
            )
            conn.execute(f"DELETE FROM viewer_names WHERE viewer_id IN ({vid_placeholders})", all_viewer_ids)  # noqa: S608
            conn.execute(f"DELETE FROM viewers WHERE id IN ({vid_placeholders})", all_viewer_ids)  # noqa: S608

        self._truncate_wal("删除观众")
        return True

    def _truncate_wal(self, what: str) -> None:
        """删除后截断预写日志，让删掉的内容不再留在 -wal 文件里。"""
        conn = self._manager.connection()
        checkpoint = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        # PRAGMA 首列是 busy：1 表示还有别的连接在用，删掉的内容可能仍留在 -wal 里
        if checkpoint and int(checkpoint[0]) != 0:
            logger.warning(
                f"{what}后截断预写日志未完成（busy={checkpoint[0]}）："
                "有其它连接占用数据库，已删内容可能暂留在 -wal，稍后随检查点落盘"
            )


# -------------------- 内部 SQL 辅助函数 --------------------


def _linked_player_ids(conn: sqlite3.Connection, viewer_ids: Sequence[int]) -> List[int]:
    """运营者确认过和这些观众是同一个人的游戏玩家。"""
    if not viewer_ids or not table_exists(conn, "viewer_player_links"):
        return []
    placeholders = ",".join("?" for _ in viewer_ids)
    rows = conn.execute(
        f"SELECT DISTINCT player_id FROM viewer_player_links WHERE viewer_id IN ({placeholders})",  # noqa: S608
        list(viewer_ids),
    ).fetchall()
    return [int(row["player_id"]) for row in rows]


def _linked_viewer_ids(conn: sqlite3.Connection, player_id: int) -> List[int]:
    """运营者确认过和这位玩家是同一个人的直播间观众。"""
    rows = conn.execute("SELECT viewer_id FROM viewer_player_links WHERE player_id = ?", (player_id,)).fetchall()
    return [int(row["viewer_id"]) for row in rows]


def _player_sessions(conn: sqlite3.Connection, player_id: int) -> List[int]:
    """这位玩家出现过的场次：和她有过互动、在公屏说过话、她的台词回应过他、首次与最近一次见到他。"""
    rows = conn.execute(
        "SELECT live_session_id FROM game_player_interactions WHERE player_id = ?"
        " UNION SELECT live_session_id FROM game_chat WHERE player_id = ?"
        " UNION SELECT live_session_id FROM live_chat WHERE reply_player_id = ?"
        " UNION SELECT first_session_id FROM game_players WHERE id = ?"
        " UNION SELECT last_session_id FROM game_players WHERE id = ?",
        (player_id, player_id, player_id, player_id, player_id),
    ).fetchall()
    return sorted({int(row[0]) for row in rows if row[0] is not None})


def _delete_player_rows(conn: sqlite3.Connection, *, player_id: int, secret_key: Optional[bytes], at_ms: int) -> bool:
    """在调用方的事务里删一位玩家：先把他的名字在他出现过的场次里换成"一位玩家"，再删玩家行（其余级联）。

    ``secret_key`` 给了才把 UUID 摘要加进不记录名单。名字替换与观众同一个范围和口径（台词、档案、承诺、
    整理报告、游戏事件正文、她的公屏话），按名字从长到短替换；短于两个字的名字不替换，防误伤日常用字。
    """
    if not table_exists(conn, "game_players"):
        return False
    row = conn.execute("SELECT game, uuid, name, names FROM game_players WHERE id = ?", (player_id,)).fetchone()
    if row is None:
        return False
    if secret_key is not None:
        digest = compute_forget_hash(secret_key, player_forget_kind(str(row["game"])), str(row["uuid"]))
        conn.execute(
            "INSERT INTO forget_list(id_hash, at_ms) VALUES (?, ?)"
            " ON CONFLICT(id_hash) DO UPDATE SET at_ms = excluded.at_ms",
            (sqlite3.Binary(digest), at_ms),
        )
    try:
        former = json.loads(row["names"] or "[]")
    except (TypeError, ValueError) as exc:
        logger.warning(f"玩家 {player_id} 的曾用名不是合法 JSON，只替换现在的名字：{exc}")
        former = []
    names = {str(row["name"] or "").strip(), *(str(name).strip() for name in former if isinstance(name, str))}
    session_ids = _player_sessions(conn, player_id)
    for name in sorted((name for name in names if len(name) >= MIN_MASK_NAME_LEN), key=len, reverse=True):
        if session_ids:
            _replace_nickname_in_sessions(conn, session_ids=session_ids, nickname=name, label=MASKED_PLAYER_LABEL)
        # 她在公屏打出去的话：所有场次都换，不只他留过记录的那几场（她可能在他没开口的场次里点他的名）
        _replace_name_in_her_game_chat(conn, nickname=name, label=MASKED_PLAYER_LABEL)
    conn.execute("DELETE FROM game_players WHERE id = ?", (player_id,))
    return True


def _replace_name_in_her_game_chat(conn: sqlite3.Connection, *, nickname: str, label: str) -> None:
    """把她所有公屏话里的这个名字换成代称（玩家说的那几行随玩家删除级联掉，不在这里）。"""
    if not nickname or not table_exists(conn, "game_chat"):
        return
    conn.execute(
        "UPDATE game_chat SET content = REPLACE(content, ?, ?) WHERE direction = 'streamer' AND content LIKE ?",
        (nickname, label, f"%{nickname}%"),
    )


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
    label: str = MASKED_VIEWER_LABEL,
) -> None:
    """在指定场次集合内把主播台词、话题、摘要、档案、承诺、整理报告与游戏侧文本里的名字换成代称 ``label``。

    整理报告是 JSON 文本，逐个文本字段替换后再序列化回去，保证 WebUI 还能解析；游戏侧
    自由文本（生物名/主人/死亡说明、游戏事件正文）不在场次集合里，按昵称全表替换。
    """
    sid_ph = ",".join("?" for _ in session_ids)
    like_pat = f"%{nickname}%"

    conn.execute(
        f"UPDATE live_chat SET content = REPLACE(content, ?, ?)"  # noqa: S608
        f" WHERE live_session_id IN ({sid_ph}) AND sender_role = 'assistant' AND content LIKE ?",
        [nickname, label, *session_ids, like_pat],
    )
    conn.execute(
        f"UPDATE topics SET label = REPLACE(label, ?, ?)"  # noqa: S608
        f" WHERE live_session_id IN ({sid_ph}) AND label LIKE ?",
        [nickname, label, *session_ids, like_pat],
    )
    conn.execute(
        f"UPDATE timeline_summary SET summary = REPLACE(summary, ?, ?)"  # noqa: S608
        f" WHERE live_session_id IN ({sid_ph}) AND summary LIKE ?",
        [nickname, label, *session_ids, like_pat],
    )

    if table_exists(conn, "archive_lines"):
        # 观众定下的那个词也换：别的观众拿 ta 的名字给东西起名（"给小猫取名阿杰"），批准后词抄在这一行上
        conn.execute(
            f"UPDATE archive_lines SET"  # noqa: S608
            f" text = REPLACE(text, ?, ?), quote = REPLACE(quote, ?, ?), viewer_word = REPLACE(viewer_word, ?, ?)"
            f" WHERE live_session_id IN ({sid_ph}) AND (text LIKE ? OR quote LIKE ? OR viewer_word LIKE ?)",
            [nickname, label, nickname, label, nickname, label, *session_ids, like_pat, like_pat, like_pat],
        )
        _scrub_archive_cues(conn, session_ids=session_ids, nickname=nickname)

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
                label,
                nickname,
                label,
                nickname,
                label,
                *session_ids,
                *session_ids,
                like_pat,
                like_pat,
                like_pat,
            ],
        )

    _clear_memory_edits_with_nickname(conn, session_ids=session_ids, nickname=nickname)
    _mask_candidates_with_nickname(conn, session_ids=session_ids, nickname=nickname, label=label)
    _replace_nickname_in_settle_reports(conn, session_ids=session_ids, nickname=nickname, label=label)
    _replace_nickname_in_game_data(conn, session_ids=session_ids, nickname=nickname, label=label)


def _scrub_archive_cues(conn: sqlite3.Connection, *, session_ids: Sequence[int], nickname: str) -> None:
    """档案行的触发词与地点名里出现这个昵称的：触发词整个去掉，地点名清空。

    不替换成代称：触发词换成"某位观众"会被日常弹幕误触发，地点名改了也对不上游戏里记的名字，
    留着只剩泄露。触发词列是 JSON 文本（引号等会被转义），所以解开后逐个比对，不靠 LIKE 判断。
    """
    if not nickname or not session_ids:
        return
    sid_ph = ",".join("?" for _ in session_ids)
    rows = conn.execute(
        f"SELECT id, keywords, place FROM archive_lines WHERE live_session_id IN ({sid_ph})"  # noqa: S608
        " AND (keywords <> '[]' OR place <> '')",
        [*session_ids],
    ).fetchall()
    for row in rows:
        try:
            words = json.loads(row["keywords"] or "[]")
        except json.JSONDecodeError as exc:
            logger.warning(f"档案行 {row['id']} 的触发词不是合法 JSON，整列清空：{exc}")
            words = []
        words = [str(word) for word in words] if isinstance(words, list) else []
        kept = [word for word in words if nickname not in word]
        place = "" if nickname in str(row["place"] or "") else str(row["place"] or "")
        if kept == words and place == str(row["place"] or ""):
            continue
        conn.execute(
            "UPDATE archive_lines SET keywords = ?, place = ? WHERE id = ?",
            (json.dumps(kept, ensure_ascii=False), place, int(row["id"])),
        )


def _like_contains(text: str) -> str:
    """LIKE 的"包含这段字"模式，配 ``ESCAPE '\\'`` 用：名字里的 ``%``、``_`` 按原字比，不当通配符。"""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _mask_candidates_with_nickname(
    conn: sqlite3.Connection, *, session_ids: Sequence[int], nickname: str, label: str
) -> None:
    """待审区里提到这个名字的候选：弹幕那一段、她那句回应、标题里的名字换成代称，候选本身留着。

    挂在 ta 自己弹幕上的候选随 ta 的弹幕按外键整条删（删弹幕在这一步之后）；这里管的是挂在别人弹幕上、
    只是提到了 ta 的——那是别人的数据，不因为撞上名字整条删，驳回过的也要留着，同一条以后才不会再列。
    名字按子串换，和档案、承诺同一口径（隐私遮挡宁可多遮）；匹配时 ``%``、``_`` 按原字比。
    玩家自述同理：他自己的候选随他的玩家行级联删，别的玩家的自述里提到这个名字的只换名字。
    """
    if not nickname or not session_ids:
        return
    sid_ph = ",".join("?" for _ in session_ids)
    pattern = _like_contains(nickname)
    if table_exists(conn, "player_candidates"):
        conn.execute(
            "UPDATE player_candidates SET title = REPLACE(title, ?, ?),"  # noqa: S608 占位符由代码拼
            " player_quote = REPLACE(player_quote, ?, ?), reply_quote = REPLACE(reply_quote, ?, ?)"
            f" WHERE live_session_id IN ({sid_ph})"
            " AND (player_quote LIKE ? ESCAPE '\\' OR reply_quote LIKE ? ESCAPE '\\' OR title LIKE ? ESCAPE '\\')",
            [nickname, label] * 3 + [*session_ids, pattern, pattern, pattern],
        )
    if not table_exists(conn, "viewer_candidates"):
        return
    conn.execute(
        "UPDATE viewer_candidates SET title = REPLACE(title, ?, ?),"  # noqa: S608 占位符由代码拼
        " viewer_quote = REPLACE(viewer_quote, ?, ?), reply_quote = REPLACE(reply_quote, ?, ?),"
        " word = REPLACE(word, ?, ?)"
        f" WHERE live_session_id IN ({sid_ph})"
        " AND (viewer_quote LIKE ? ESCAPE '\\' OR reply_quote LIKE ? ESCAPE '\\' OR title LIKE ? ESCAPE '\\'"
        " OR word LIKE ? ESCAPE '\\')",
        [nickname, label] * 4 + [*session_ids, pattern, pattern, pattern, pattern],
    )


def _clear_memory_edits_with_nickname(
    conn: sqlite3.Connection,
    *,
    session_ids: Sequence[int],
    nickname: str,
) -> None:
    """记忆页改动记录里提到这个昵称的，改前改后的文本整段清空，动作与时刻留着。

    删观众、"忘了我"走同一条路：改动记录只用于审计和评测，不值得为它保留观众相关的文字，
    所以不做替换、直接清空。范围是 ta 出现过的场次的记录，加上改的是这些场次档案行的记录
    （放回快照这类操作记在最新那一场，改的却是更早场次的行）。
    """
    if not nickname or not session_ids or not table_exists(conn, "memory_edits"):
        return
    sid_ph = ",".join("?" for _ in session_ids)
    # 名字里的 %、_ 按原字比：不然"阿_杰"会把写着"阿x杰"的别人的改动记录也清空
    pattern = _like_contains(nickname)
    conn.execute(
        "UPDATE memory_edits SET before_text = '', after_text = ''"  # noqa: S608 占位符由代码拼
        f" WHERE (live_session_id IN ({sid_ph})"
        f"        OR archive_line_id IN (SELECT id FROM archive_lines WHERE live_session_id IN ({sid_ph})))"
        "   AND (before_text LIKE ? ESCAPE '\\' OR after_text LIKE ? ESCAPE '\\')",
        [*session_ids, *session_ids, pattern, pattern],
    )


def _replace_nickname_in_settle_reports(
    conn: sqlite3.Connection,
    *,
    session_ids: Sequence[int],
    nickname: str,
    label: str = MASKED_VIEWER_LABEL,
) -> None:
    """把场次整理报告 JSON 里的昵称换成代称，报告仍保持可解析。

    报告的未登记清单存着模型给的原话与标题，失败原因也可能回显模型输出，都可能夹着
    观众昵称；按字段替换而不是整段文本替换，避免破坏 JSON 结构导致 WebUI 读不出报告。
    """
    if not column_exists(conn, "live_sessions", "settle_report"):
        return
    sid_ph = ",".join("?" for _ in session_ids)
    like_pat = f"%{nickname}%"
    rows = conn.execute(
        f"SELECT id, settle_report FROM live_sessions"  # noqa: S608 常量列名
        f" WHERE id IN ({sid_ph}) AND settle_report IS NOT NULL AND settle_report LIKE ?",
        [*session_ids, like_pat],
    ).fetchall()
    for row in rows:
        cleaned = _replace_nickname_in_report_json(str(row["settle_report"]), nickname, label)
        conn.execute("UPDATE live_sessions SET settle_report=? WHERE id=?", (cleaned, int(row["id"])))


def _replace_nickname_in_report_json(raw: str, nickname: str, label: str = MASKED_VIEWER_LABEL) -> str:
    """整理报告 JSON 的文本字段逐个替换昵称；读不懂时退回原文替换（至少不残留明文）。"""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return raw.replace(nickname, label)
    if not isinstance(data, dict):
        return raw.replace(nickname, label)
    for key in ("failure_reason", "input_note"):
        value = data.get(key)
        if isinstance(value, str):
            data[key] = value.replace(nickname, label)
    # 未登记清单与档案快照改动清单的条目都带标题、原话与理由
    for list_key in ("unregistered", "snapshot_changes"):
        items = data.get(list_key)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            for key in ("what", "quote", "reason"):
                value = item.get(key)
                if isinstance(value, str):
                    item[key] = value.replace(nickname, label)
    return json.dumps(data, ensure_ascii=False)


def _replace_nickname_in_game_data(
    conn: sqlite3.Connection,
    *,
    session_ids: Sequence[int],
    nickname: str,
    label: str = MASKED_VIEWER_LABEL,
) -> None:
    """游戏侧按场次记下的文本里的名字换成代称：ta 出现过的场次里的游戏事件正文、生物死亡说明与她打在公屏上的话。

    与台词替换同一个范围（ta 出现过、或昵称使用期重叠的场次）：全表按昵称替换会误改与
    ta 同名、却和 ta 无关的内容。生物名册里的名字、称呼与主人名不替换——名册跨场次、
    记不得名字是哪一场起的，名字又常是常见词（观众叫"小白"，她的狗也叫"小白"），按名字
    替换会把无关的生物改成"一位观众"；名字与主人名还由游戏每次照原样报回，改了下次就被
    写回。这部分列在运维文档的残留边界里。
    """
    if not nickname or not session_ids:
        return
    like_pat = f"%{nickname}%"
    sid_ph = ",".join("?" for _ in session_ids)
    if table_exists(conn, "game_creatures"):
        conn.execute(
            "UPDATE game_creatures SET death_note = REPLACE(death_note, ?, ?)"  # noqa: S608 常量列名
            f" WHERE died_session_id IN ({sid_ph}) AND death_note LIKE ?",
            [nickname, label, *session_ids, like_pat],
        )
    if table_exists(conn, "game_events"):
        conn.execute(
            "UPDATE game_events SET message = REPLACE(message, ?, ?)"  # noqa: S608 常量列名
            f" WHERE live_session_id IN ({sid_ph}) AND message LIKE ?",
            [nickname, label, *session_ids, like_pat],
        )
    if table_exists(conn, "game_chat"):
        # 她在公屏打出去的话里点过的名字（玩家说的那几行随玩家删除级联掉）
        conn.execute(
            "UPDATE game_chat SET content = REPLACE(content, ?, ?)"  # noqa: S608 常量列名
            f" WHERE live_session_id IN ({sid_ph}) AND direction = 'streamer' AND content LIKE ?",
            [nickname, label, *session_ids, like_pat],
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


__all__ = [
    "GRACE_DELETE_MAX_MS",
    "IncomingDisposition",
    "MASKED_PLAYER_LABEL",
    "MASKED_VIEWER_LABEL",
    "MIN_MASK_NAME_LEN",
    "PrivacyService",
    "PrivacyStats",
    "compute_forget_hash",
    "mask_names_in_text",
    "matches_phrase_table",
    "player_forget_kind",
]
