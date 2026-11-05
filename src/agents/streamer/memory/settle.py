"""下播整理：把一场正式直播整理成承诺账本与场次档案。

触发与调度
----------
live.ended 之后、道别句播完才排进后台，一次整理待整理的场次；进程启动时也调一次，
补上上次没整理完的。只挑已结束、settled_at_ms 为空、自动重试次数没用完的正式场，按
开播时刻从早到晚逐场整理。彩排场永远不在候选里。

一场失败不挡后面的场：任何一次调用失败、输出读不懂或输入超出模型窗口（调用本身报错），
这一场整场不提交，重试次数加一并把原因写进整理报告；次数用满后不再自动补跑，等运营者
在 WebUI 点「重新整理」把次数清零。一场一个事务：校验全部通过才落进 apply_settlement，
重跑时先在同一事务里清掉本场旧档案与本场登记的未了结承诺再写，不会重复登记。

同一场的整理可能被下播后台、开播补整理与隐私清理前的等待同时触发：进程内按场次共享
同一个在途任务，只调一次模型；存储侧再用「整理完成时刻为空」抢占，两个进程同时跑也只
有一个写事务成功。

三次调用各只做一种判断
----------------------
1. 场次档案：她的全部播出台词加游戏里的事，挑值得下次开播提起的至多 8 行新事；同时把上一份
   档案里仍然成立的行接过来（滚动档案，取舍见 ``rolling``），一份至多 12 行；
2. 找新承诺：她的全部台词（附被回应的弹幕）加她能做的事，找出当众答应过、还没兑现的事；
3. 了结旧承诺：只在本场之前有未了结承诺时才调，判她说没说明白做完了或不做了。

校验全部是代码：逐字引文（原话必须是所引那一行文本的逐字子串且不超过上限）、所属游戏
（必须是本场玩过的）、对谁（对具体观众时那一行必须回应了真实观众的弹幕）、不重复（与
已有承诺同一件事、或句子里已经提到某条承诺，都不新建）、只改未了结的承诺（运营者已判
误记或已了结的不受她的话影响）。校验没通过的条目连同原话写进整理报告，由运营者判断
要不要在 WebUI 手建。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from src.modules.context.tokens import TokenCounter
from src.modules.logging import get_logger
from src.modules.prompts import PromptManager
from src.modules.storage.models.chat import SettleSpeechLine
from src.modules.storage.models.memory import (
    ArchiveLine,
    Commitment,
    CommitmentClosure,
    PendingArchiveLine,
    PendingCommitment,
    SettleReport,
    UnregisteredItem,
)
from src.modules.storage.models.sessions import LiveSession
from src.modules.storage.repos.sessions import MAX_SETTLE_ATTEMPTS
from src.modules.types.game_save import GameSave
from src.modules.types.session_kind import SESSION_KIND_LIVE
from src.agents.streamer.memory.render import memory_ref_numbers
from src.agents.streamer.memory.rolling import keep_from_base, render_base, select_rolling
from src.agents.streamer.memory.saves import SaveTimeline
from src.modules.utils.time_utils import now_ms

from ..background import SUMMARY_PROFILE

if TYPE_CHECKING:
    from src.modules.storage.repos.archive import ArchiveRepo
    from src.modules.storage.repos.chat import ChatRepo
    from src.modules.storage.repos.commitments import CommitmentRepo
    from src.modules.storage.repos.events import EventRepo
    from src.modules.storage.repos.sessions import SessionRepo

__all__ = ["MAX_ATTEMPTS", "SessionSettler", "SettleError", "parse_json_object"]

logger = get_logger("SessionSettler")

# 三份系统提示词（正文见 src/agents/streamer/prompts/）
_ARCHIVE_TEMPLATE = "memory_archive_system"
_COMMITMENT_TEMPLATE = "memory_commitment_system"
_CLOSE_TEMPLATE = "memory_commitment_close_system"

# 自动重试上限：用满后不再补跑，等运营者在 WebUI 点「重新整理」。
# 与存储层共用同一个数（WebUI 整理状态接口读它做展示与重置口径），不各写一份
MAX_ATTEMPTS = MAX_SETTLE_ATTEMPTS
# 台词预算占模型上下文窗口的比例：其余留给系统提示词、旧承诺与模型输出
_SPEECH_WINDOW_RATIO = 0.45
# 窗口查不到时的保守默认窗口（token），按它换算台词预算
_DEFAULT_CONTEXT_WINDOW = 32_768
# 裁剪台词时首尾各至少保留几行：预算再紧也留着，模型才看得到这一场的开场与收尾
_EDGE_LINES_KEPT = 3
# 每行台词的行号前缀（"#N "）按固定 token 计，避免逐行分词量前缀
_LINE_PREFIX_TOKENS = 4
# 一次调度最多整理几场（启动补跑时可能有积压）
MAX_SESSIONS_PER_RUN = 4
# 一次整理最多写几行本场新事（模型多给了就只取前几行；连同接过来的行，一份档案的上限见 rolling）
MAX_ARCHIVE_LINES = 8
# 上一份还是滚动之前的旧档案时，往前至多取几份一起接（开始滚动的第一场）
MAX_LEGACY_BASE_SESSIONS = 3
# 逐字引文与标题的上限（超出的引文整条丢弃，超出的标题截断）
MAX_QUOTE_CHARS = 80
MAX_TITLE_CHARS = 30
# 交给「找新承诺」的旧承诺标题上限：未了结的全给，判误记的只给最近的这些条
MAX_VOID_TITLES = 40
# 她能做的事里，不需要额外能力就能做的几件（游戏 Agent 名单由外面传进来）
_BASE_ABILITIES = ("聊天", "讲故事", "陪观众说话")

# 说话人代号前缀；本场内按首次出现顺序编号，同一观众整场用同一个代号
_VIEWER_CODE_PREFIX = "观众"


class SettleError(Exception):
    """这一场整理不下去（模型调用失败、输出读不懂、取料失败）。"""


@dataclass(frozen=True)
class SettleMaterial:
    """一次整理要交给模型的三份原料，全部来自库表。"""

    live_session_id: int
    speech_text: str
    lines_by_number: Dict[int, SettleSpeechLine]
    speech_note: Optional[str]
    games: List[str]
    games_text: str
    # 启用了的游戏 Agent：承诺的 game 也可以是它们（短场次可能一条游戏事件都没记下）
    game_agents: List[str]
    # 各游戏这一场先后待过的存档：档案与承诺挂了游戏时，按台词时刻认是在哪个存档里说的
    saves: SaveTimeline
    abilities_text: str
    open_commitments: List[Commitment]
    void_titles: List[Tuple[int, str]]
    open_text: str
    void_text: str
    # 上一份档案（滚动底稿）与给模型看的旧行清单；没有上一份时都为空
    previous_archive: List[ArchiveLine] = field(default_factory=list)
    previous_text: str = ""


@dataclass(frozen=True)
class SettleOutcome:
    """一场整理的结果：要写进去的行、没能登记的条目。"""

    archive_lines: List[PendingArchiveLine] = field(default_factory=list)
    commitments: List[PendingCommitment] = field(default_factory=list)
    closures: List[CommitmentClosure] = field(default_factory=list)
    unregistered: List[UnregisteredItem] = field(default_factory=list)
    input_note: Optional[str] = None


def parse_json_object(text: str) -> Dict[str, Any]:
    """从模型输出里取出 JSON 对象；取不出抛 SettleError。

    只按第一个左花括号到最后一个右花括号截取：模型常见地加一句开场白、或者把 JSON
    包在代码块里，这两种都能救回来；截出来的不是合法对象就算这一场输出读不懂。
    """
    cleaned = (text or "").strip()
    if not cleaned:
        raise SettleError("模型返回空内容")
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise SettleError("模型输出里没有 JSON 对象")
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise SettleError(f"模型输出的 JSON 读不懂：{exc}") from exc
    if not isinstance(data, dict):
        raise SettleError("模型输出的不是 JSON 对象")
    return data


def _clip(text: str, limit: int) -> str:
    """按字数截断（标题用；引文不截断，截过的引文已经不是逐字引文）。"""
    cleaned = (text or "").strip()
    return cleaned[:limit] if len(cleaned) > limit else cleaned


# "对谁"字段的已知取值：对具体观众 / 对全体观众。提示词只要求这两个值，
# 其余取值（含缺失、空串）一律不登记——认不出是谁就不能写进承诺账本
_TARGET_PERSON = frozenset({"person", "viewer", "观众"})
_TARGET_EVERYONE = frozenset({"everyone", "all", "全体", "所有人", "大家"})


def _select_speech_lines(*, costs: Sequence[int], key_flags: Sequence[bool], budget: int) -> List[int]:
    """按 token 预算选出要喂给模型的台词行下标（保留首尾与关键行，中间整段省略）。

    优先级：关键行（回应了观众的弹幕、点名引用了记忆）→ 首尾各 _EDGE_LINES_KEPT 行
    （预算再紧也留，模型至少看得到开场与收尾）→ 余量先给最近的台词，再给最早的。
    返回升序下标；调用方按保留顺序重新编号，台词行本身仍指向原行。
    """
    total = sum(costs)
    indices = list(range(len(costs)))
    if total <= budget:
        return indices
    keep: Set[int] = set()
    remaining = max(0, int(budget))
    for index in reversed(indices):
        if key_flags[index] and costs[index] <= remaining:
            keep.add(index)
            remaining -= costs[index]
    for index in [*indices[:_EDGE_LINES_KEPT], *indices[-_EDGE_LINES_KEPT:]]:
        if index not in keep:
            keep.add(index)
            remaining -= costs[index]
    for index in reversed(indices):
        if remaining <= 0:
            break
        if index in keep or costs[index] > remaining:
            continue
        keep.add(index)
        remaining -= costs[index]
    for index in indices:
        if remaining <= 0:
            break
        if index in keep or costs[index] > remaining:
            continue
        keep.add(index)
        remaining -= costs[index]
    return sorted(keep)


def _clock_text(timestamp_ms: int) -> str:
    """毫秒时刻 → 本场内的 HH:MM（原料里只需要相对先后）。"""
    return datetime.fromtimestamp(max(0, int(timestamp_ms)) / 1000).strftime("%H:%M")


def _as_line_number(raw: Any) -> Optional[int]:
    """模型给的台词编号：整数、纯数字或 #N 才认，其余当没给。

    给模型的台词逐行写作 ``#18 12:00 你：……``，模型照抄成 ``"#18"`` 也放行；
    带别的前缀（如 约#18、档#18）不是台词编号，不认。
    """
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if text.startswith("#"):
        text = text[1:].strip()
    return int(text) if text.isdigit() else None


def _as_commitment_ref(raw: Any) -> Optional[int]:
    """模型给的承诺编号：整数、纯数字、#N、约#N 都认；档# 与其余写法返回 None。

    给模型的旧承诺清单写作 #12，台词里的记忆引用写作 约#12，模型照抄哪一种都放行；
    档# 是场次档案行的编号空间，不能落进承诺编号。
    """
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return int(raw)
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    if "#" in text:
        head, _sep, tail = text.rpartition("#")
        if head.strip():
            # 带前缀的只认承诺前缀；用渲染层的解析器判断，避免在两处各写一份编号格式
            refs = memory_ref_numbers(json.dumps([text], ensure_ascii=False))
            if len(refs) != 1:
                return None
            return int(next(iter(refs)))
        text = tail.strip()
    return int(text) if text.isdigit() else None


# 模型表达"没有"时常写成字符串：这些写法都按没填处理，不当成认不出的编号
_EMPTY_LITERALS = frozenset({"none", "null", "nil", "n/a", "na", "-", "无", "没有", "空", "否"})


def _game_of(raw: Any, material: SettleMaterial) -> Optional[str]:
    """模型给的游戏名换成本场认得的游戏；认不出就当与游戏无关。

    挂的游戏要么本场有游戏事件，要么是启用了的游戏 Agent：开错后马上重开的一两分钟里
    常常一条游戏事件都没记下，明明是游戏里的事却挂不上游戏，就会进常驻简报、不玩这个
    游戏时也每场被念叨。事件行前面标着存档（"minecraft「新档」"），模型偶尔把存档名
    一起抄进游戏名，这时只认前面的游戏名。
    """
    if not isinstance(raw, str) or not raw.strip():
        return None
    name = raw.strip()
    known = [*material.games, *material.game_agents]
    if name in known:
        return name
    for game in known:
        if name.startswith(f"{game}「"):
            return game
    return None


def _place_of(row: Commitment) -> str:
    """旧承诺挂在哪：给模型看的后缀（"；挂在 minecraft 的存档「生存一档」"），没挂游戏为空串。

    同一句"把桥修完"在不同存档里是两件事；标出存档，模型判"又说了一遍"和"做完了"时才分得清。
    """
    if not row.game:
        return ""
    save = GameSave(id=row.save_id, name=row.save_name)
    return f"；挂在 {row.game} 的存档「{save.label}」" if save.known else f"；挂在 {row.game}"


def _has_value(raw: Any) -> bool:
    """模型是否真的填了这个字段（None、空串、纯空白与"无/none/null"一类写法都算没填）。

    same_as 填了却认不出时整条新承诺进未登记清单；模型把"不是同一件事"写成字符串
    ``"无"``、``"null"`` 很常见，按认不出处理会把一条正常的新承诺丢掉。
    """
    if raw is None:
        return False
    if isinstance(raw, str):
        text = raw.strip()
        return bool(text) and text.lower() not in _EMPTY_LITERALS
    return True


class SessionSettler:
    """下播整理的执行者：取料 → 三次调用 → 代码校验 → 一个事务写入。"""

    def __init__(
        self,
        *,
        sessions_repo: "SessionRepo",
        chat_repo: "ChatRepo",
        commitments_repo: "CommitmentRepo",
        llm_service: Any,
        prompt_manager: PromptManager,
        event_repo: Optional["EventRepo"] = None,
        archive_repo: Optional["ArchiveRepo"] = None,
        game_names_provider: Optional[Callable[[], Sequence[str]]] = None,
        clock: Callable[[], int] = now_ms,
    ) -> None:
        """仓储、模型与提示词由主播 Agent 构造时注入。

        event_repo 缺失时原料少游戏这一类，整理照常跑；archive_repo 缺失时没有上一份档案
        可接，本场档案只写本场的事；game_names_provider 返回当前对主播可见的游戏 Agent 名，
        用来写「她能做的事」。
        """
        self._sessions = sessions_repo
        self._chat = chat_repo
        self._commitments = commitments_repo
        self._events = event_repo
        self._archive = archive_repo
        self._llm = llm_service
        self._prompt_manager = prompt_manager
        self._game_names_provider = game_names_provider
        self._clock = clock
        # 场次主键 → 在途整理任务：同一场的并发触发共享这个任务，只付一次模型调用
        self._inflight: Dict[int, asyncio.Task[bool]] = {}

    # ------------------------------------------------------------------
    # 调度
    # ------------------------------------------------------------------

    async def settle_pending(self) -> int:
        """整理待整理的场次（最多几场），返回成功的场数；单场失败只记录不抛出。"""
        try:
            pending = await self._sessions.list_sessions_pending_settle(
                limit=MAX_SESSIONS_PER_RUN, max_attempts=MAX_ATTEMPTS
            )
        except Exception as exc:  # noqa: BLE001 - 取待办失败不能挡住调用方（下播回调 / 启动）
            logger.warning(f"查询待整理的场次失败，本次不整理: {exc}", exc=exc)
            return 0
        settled = 0
        for session in pending:
            if await self.settle_session(int(session.id)):
                settled += 1
        if pending:
            logger.info(f"下播整理：待整理 {len(pending)} 场，成功 {settled} 场")
        return settled

    async def settle_session(self, live_session_id: int) -> bool:
        """整理一场：同一场的并发调用共享同一个在途任务，只付一次模型调用。

        下播后台整理、开播补整理与隐私清理前的等待可能同时指向同一场：第一个调用创建
        任务，其余调用等它结束，拿同一个结果。成功返回 True；失败或跳过返回 False，
        内部异常转成 False 并记日志，不向调用方抛。
        """
        session_id = int(live_session_id)
        current = asyncio.current_task()
        task = self._inflight.get(session_id)
        if task is not None and not task.done() and task is not current:
            return await asyncio.shield(task)
        if task is not None and task.done():
            self._inflight.pop(session_id, None)
        task = asyncio.create_task(self._settle_session_guarded(session_id), name=f"settle-session-{session_id}")
        self._inflight[session_id] = task

        def _forget(done: asyncio.Task[bool]) -> None:
            # 调用方超时取消只取消它自己的 await（shield 住任务）；任务本身跑完后在这里
            # 从在途表摘除，避免等待方把“还在跑”误判成“没在跑”又起一份
            if self._inflight.get(session_id) is done:
                self._inflight.pop(session_id, None)

        task.add_done_callback(_forget)
        return await asyncio.shield(task)

    async def wait_settled(self, live_session_id: int) -> bool:
        """等某场正在进行的整理结束（成功或失败都算）；没有在途整理时立即返回 False。

        只等已经开始的整理，不会等将来才排进来的调度。调用方若要「等这一场整理完」，
        直接 await settle_session(live_session_id) 更稳：它会创建或加入该场任务。
        """
        task = self._inflight.get(int(live_session_id))
        if task is None or task.done():
            return False
        await asyncio.shield(task)
        return True

    async def _settle_session_guarded(self, live_session_id: int) -> bool:
        """在途任务的统一异常边界：意外异常记日志并按失败返回，等待方不被炸。"""
        try:
            return await self._settle_session_once(live_session_id)
        except Exception as exc:  # noqa: BLE001 - 整理失败只影响这一场，不能炸穿调度与等待方
            logger.warning(f"场次 {live_session_id} 整理异常: {exc}", exc=exc)
            return False

    async def _settle_session_once(self, live_session_id: int) -> bool:
        """整理一场：成功返回 True；失败或跳过时把原因写进整理报告并返回 False。"""
        session = await self._sessions.get_live_session(live_session_id=int(live_session_id))
        if session is None:
            logger.debug(f"场次 {live_session_id} 不存在，跳过整理")
            return False
        if session.kind != SESSION_KIND_LIVE:
            logger.debug(f"场次 {live_session_id} 不是正式场（{session.kind}），不整理")
            return False
        if session.ended_at_ms is None:
            logger.debug(f"场次 {live_session_id} 还没结束，不整理")
            return False
        if session.settled_at_ms is not None:
            # 已整理过的场次不再白跑两次模型调用；运营者要重跑先经 WebUI 重新整理（清进度）
            logger.debug(f"场次 {live_session_id} 已整理过，跳过重复整理")
            return False

        try:
            speech = await self._chat.list_session_speech_for_settle(live_session_id=int(live_session_id))
        except Exception as exc:  # noqa: BLE001 - 取料失败按整理失败记账
            await self._record_failure(session, f"取本场台词失败：{exc}")
            return False
        delivered = [line for line in speech if line.dropped_at_ms is None]
        if not delivered:
            return await self._mark_done(session, note="本场没有播出台词，不用整理")

        try:
            material = await self._collect_material(session, delivered)
            outcome = await self._extract(material)
        except SettleError as exc:
            await self._record_failure(session, str(exc))
            return False
        except Exception as exc:  # noqa: BLE001 - 取料或模型调用的意外异常同样按失败记账
            logger.warning(f"场次 {live_session_id} 整理异常: {exc}", exc=exc)
            await self._record_failure(session, f"整理异常：{exc}")
            return False

        report = SettleReport(unregistered=outcome.unregistered, input_note=outcome.input_note)
        now = int(self._clock())
        try:
            written = await self._sessions.apply_settlement(
                live_session_id=int(live_session_id),
                archive_lines=outcome.archive_lines,
                commitments=outcome.commitments,
                closures=outcome.closures,
                settled_at_ms=now,
                now_ms_value=now,
                settle_report=(report.model_dump_json() if (outcome.unregistered or outcome.input_note) else None),
            )
        except Exception as exc:  # noqa: BLE001 - 写入失败按整理失败记账，事务已回滚
            logger.warning(f"场次 {live_session_id} 整理写入失败: {exc}", exc=exc)
            await self._record_failure(session, f"写入整理结果失败：{exc}")
            return False

        if not int(written.get("claimed", 0)):
            # 整理期间场次被改成彩排，或另一进程已先一步写完整理完成时刻：不算失败，也不清账
            logger.info(f"场次 {live_session_id} 的整理结果未写入（场次类型已改或已被别的整理抢先），不重复提交")
            return False

        logger.info(
            f"场次 {live_session_id} 整理完成：档案 {written.get('archive', 0)} 行、"
            f"新承诺 {written.get('commitments', 0)} 条、了结 {written.get('closed', 0)} 条、"
            f"未登记 {len(outcome.unregistered)} 条"
        )
        return True

    async def _mark_done(self, session: LiveSession, *, note: str) -> bool:
        """直接标成已整理（没有播出台词、不用调模型）。"""
        try:
            ok = await self._sessions.mark_settle_done(
                live_session_id=int(session.id),
                settled_at_ms=int(self._clock()),
                settle_report=None,
            )
            logger.info(f"场次 {session.id} {note}")
            return bool(ok)
        except Exception as exc:  # noqa: BLE001 - 落库失败下次调度会重来
            logger.warning(f"场次 {session.id} 标记整理完成失败: {exc}", exc=exc)
            return False

    async def _record_failure(self, session: LiveSession, reason: str) -> None:
        """记一次失败：重试次数加一、写原因；次数用满后只告警不再自动补跑。"""
        report = SettleReport(failure_reason=reason)
        try:
            attempts = await self._sessions.record_settle_failure(
                live_session_id=int(session.id),
                settle_report=report.model_dump_json(),
            )
        except Exception as exc:  # noqa: BLE001 - 记账失败也不该让调用方崩掉
            logger.warning(f"场次 {session.id} 记录整理失败原因时出错: {exc}", exc=exc)
            return
        if attempts >= MAX_ATTEMPTS:
            logger.warning(
                f"场次 {session.id} 整理失败（第 {attempts} 次，已达上限，不再自动补跑）：{reason}。"
                "修好模型或提示词后可在 WebUI 点「重新整理」"
            )
        else:
            logger.warning(f"场次 {session.id} 整理失败（第 {attempts} 次，下次调度再试）：{reason}")

    # ------------------------------------------------------------------
    # 取料
    # ------------------------------------------------------------------

    async def _collect_material(self, session: LiveSession, delivered: List[SettleSpeechLine]) -> SettleMaterial:
        """把一场的原料全取出来：她说的话、游戏（连同待过的存档）、她能做的事、旧承诺。"""
        speech_text, lines_by_number, speech_note = self._render_speech(delivered)
        saves = await self._load_saves(session)
        games, games_text = await self._render_games(session, saves)
        game_agents = self._enabled_game_names()
        abilities_text = self._render_abilities(game_agents)
        open_commitments, open_text, void_titles, void_text = await self._load_commitments(session)
        previous_archive = await self._load_previous_archive(session)
        return SettleMaterial(
            live_session_id=int(session.id),
            speech_text=speech_text,
            lines_by_number=lines_by_number,
            speech_note=speech_note,
            games=games,
            games_text=games_text,
            game_agents=game_agents,
            saves=saves,
            abilities_text=abilities_text,
            open_commitments=open_commitments,
            void_titles=void_titles,
            open_text=open_text,
            void_text=void_text,
            previous_archive=previous_archive,
            previous_text=render_base(previous_archive),
        )

    async def _load_previous_archive(self, session: LiveSession) -> List[ArchiveLine]:
        """本场要接着滚的上一份档案（开播早于本场的最近一份；开始滚动时连同更早几份旧档案）。

        读失败按整理失败处理、下次再试：没接住上一份就写，本场档案会顶掉前几场的事，
        简报从此再也看不到它们。
        """
        if self._archive is None:
            return []
        try:
            return await self._archive.list_rolling_base(
                before_started_at_ms=int(session.started_at_ms),
                kind=SESSION_KIND_LIVE,
                max_legacy_sessions=MAX_LEGACY_BASE_SESSIONS,
            )
        except Exception as exc:  # noqa: BLE001 - 底稿读不到就不写，免得顶掉前几场的事
            raise SettleError(f"取上一份档案失败：{exc}") from exc

    def _render_speech(
        self, delivered: List[SettleSpeechLine]
    ) -> Tuple[str, Dict[int, SettleSpeechLine], Optional[str]]:
        """台词渲染成带编号的行；回应弹幕的把那句原文与发言人代号附在前面。

        编号是模型输出用来指回具体台词的键：整场台词超过模型窗口预算时只保留一部分，
        按保留顺序重新编号，台词行本身仍指向原行。返回的第三项是裁剪说明（未裁剪为 None），
        由调用方写进整理报告。同一观众整场用同一个代号，模特才能说清「对谁答应的」而不用昵称。
        """
        codes: Dict[int, str] = {}
        bodies: List[str] = []
        key_flags: List[bool] = []
        for line in delivered:
            content = " ".join((line.content or "").split())
            reply_note = ""
            reply_viewer_id = line.reply_viewer_id
            if line.reply_content and reply_viewer_id is not None:
                code = codes.setdefault(int(reply_viewer_id), f"{_VIEWER_CODE_PREFIX}{len(codes) + 1}")
                reply_text = " ".join((line.reply_content or "").split())
                reply_note = f"（回复{code}「{reply_text}」）"
            memory_note = ""
            used = sorted(memory_ref_numbers(line.memory_refs))
            if used:
                memory_note = "（提到" + "、".join(f"约#{item}" for item in used) + "）"
            bodies.append(f"{_clock_text(int(line.timestamp_ms))} 你{reply_note}：{content}{memory_note}")
            key_flags.append(bool(reply_note) or bool(used))

        counter = TokenCounter()
        costs = [counter.text(f"#{index} {body}") + _LINE_PREFIX_TOKENS for index, body in enumerate(bodies, start=1)]
        kept = _select_speech_lines(costs=costs, key_flags=key_flags, budget=self._speech_token_budget())
        lines_by_number: Dict[int, SettleSpeechLine] = {}
        lines: List[str] = []
        for number, index in enumerate(kept, start=1):
            lines.append(f"#{number} {bodies[index]}")
            lines_by_number[number] = delivered[index]
        note: Optional[str] = None
        if len(kept) < len(delivered):
            note = (
                f"本场台词 {len(delivered)} 行超出模型上下文窗口，已省略中间 {len(delivered) - len(kept)} 行"
                f"（保留首尾与回应观众、引用记忆的关键行），本次整理只看到 {len(kept)} 行"
            )
            logger.warning(f"下播整理台词已裁剪：{note}")
        return "\n".join(lines), lines_by_number, note

    def _speech_token_budget(self) -> int:
        """台词可用的 token 预算：按 summary profile 的上下文窗口换算，查不到按保守窗口。

        整理原料里台词占大头，其余（系统提示词、旧承诺清单、模型输出）留在剩余窗口里，
        所以只按窗口的一定比例给台词。
        """
        window = 0
        getter = getattr(self._llm, "get_profile_context_window", None)
        if callable(getter):
            try:
                value = getter(SUMMARY_PROFILE)
                window = int(value) if isinstance(value, int) and value > 0 else 0
            except Exception as exc:  # noqa: BLE001 - 查不到窗口按保守预算，整理照常进行
                logger.warning(f"查询 {SUMMARY_PROFILE} 上下文窗口失败，按保守窗口换算台词预算: {exc}")
        if window <= 0:
            window = _DEFAULT_CONTEXT_WINDOW
        return max(512, int(window * _SPEECH_WINDOW_RATIO))

    async def _load_saves(self, session: LiveSession) -> SaveTimeline:
        """这一场各游戏先后待过的存档；读不到就当都认不出（挂游戏的条目按"不确定是哪个存档"记）。"""
        if self._events is None:
            return SaveTimeline()
        try:
            spans = await self._events.list_session_save_spans(live_session_id=int(session.id))
        except Exception as exc:  # noqa: BLE001 - 认不出存档只少一层区分，整理照常跑
            logger.warning(f"场次 {session.id} 读游戏存档段失败，挂游戏的条目不标存档: {exc}", exc=exc)
            return SaveTimeline()
        return SaveTimeline(spans)

    async def _render_games(self, session: LiveSession, saves: SaveTimeline) -> Tuple[List[str], str]:
        """本场玩过的游戏名与游戏里的关键事件；没有游戏事件时两者都空。

        事件前面标着当时在哪个存档；一场里换过存档的游戏另起一行说明，让模型知道那是两个
        互不相干的世界，不把两个存档里的事写成同一件。
        """
        if self._events is None:
            return [], ""
        try:
            records = await self._events.list_session_game_events(live_session_id=int(session.id))
        except Exception as exc:  # noqa: BLE001 - 少一类原料不影响整理
            logger.warning(f"场次 {session.id} 读游戏事件失败，原料少游戏这一类: {exc}", exc=exc)
            return [], ""
        games: List[str] = []
        for record in records:
            game = (getattr(record, "game", "") or "").strip()
            if game and game not in games:
                games.append(game)
        if not games:
            return [], ""
        game_lines = [f"#{index} {name}" for index, name in enumerate(games, start=1)]
        for name in games:
            visited = saves.saves_of(name)
            if len(visited) > 1:
                labels = "".join(f"「{save.label}」" for save in visited)
                game_lines.append(
                    f"（{name} 这一场待过不止一个存档：{labels}。不同存档是互不相干的世界，事件前面标着是哪个存档）"
                )
        event_lines: List[str] = []
        for record in records:
            message = " ".join((getattr(record, "message", "") or "").split())
            if not message:
                continue
            game = getattr(record, "game", "")
            save = GameSave(id=getattr(record, "save_id", "") or "", name=getattr(record, "save_name", "") or "")
            where = f"{game}「{save.label}」" if save.known else game
            event_lines.append(f"- [{where}] {message}")
        text = "【本场玩过的游戏】\n" + "\n".join(game_lines)
        if event_lines:
            text += "\n\n【游戏里的关键事件】\n" + "\n".join(event_lines)
        return games, text

    def _enabled_game_names(self) -> List[str]:
        """启用了的游戏 Agent 名单；拿不到就空（只少写几个名字，承诺照常整理）。"""
        if self._game_names_provider is None:
            return []
        try:
            return [str(name).strip() for name in self._game_names_provider() if str(name).strip()]
        except Exception as exc:  # noqa: BLE001 - 拿不到名单只少写几个名字
            logger.warning(f"取游戏 Agent 名单失败: {exc}", exc=exc)
            return []

    @staticmethod
    def _render_abilities(game_agents: Sequence[str]) -> str:
        """她能做的事：启用的游戏 Agent 名单，加上不需要额外能力就能做的事。"""
        abilities = list(dict.fromkeys([*game_agents, *_BASE_ABILITIES]))
        return "、".join(abilities)

    async def _load_commitments(self, session: LiveSession) -> Tuple[List[Commitment], str, List[Tuple[int, str]], str]:
        """本场之前就有的未了结承诺（按场次先后，不含更晚场次登记的）与判误记的旧承诺标题。"""
        try:
            open_commitments = await self._commitments.list_open_before_session(live_session_id=int(session.id))
        except Exception as exc:  # noqa: BLE001 - 取不到就当没有旧承诺，整理照常跑
            logger.warning(f"场次 {session.id} 读未了结承诺失败: {exc}", exc=exc)
            open_commitments = []
        try:
            void_rows = await self._commitments.list_commitments(statuses=["void"], limit=MAX_VOID_TITLES)
        except Exception as exc:  # noqa: BLE001 - 少一类原料不影响整理
            logger.warning(f"场次 {session.id} 读误记承诺失败: {exc}", exc=exc)
            void_rows = []
        void_titles = [(int(row.id), row.what) for row in reversed(void_rows)]
        open_lines = [
            f"#{row.id} 「{row.what}」（当初的原话「{row.quote or '无'}」{_place_of(row)}）" for row in open_commitments
        ]
        void_lines = [f"#{commitment_id} 「{what}」" for commitment_id, what in void_titles]
        return open_commitments, "\n".join(open_lines), void_titles, "\n".join(void_lines)

    # ------------------------------------------------------------------
    # 三次模型调用
    # ------------------------------------------------------------------

    async def _extract(self, material: SettleMaterial) -> SettleOutcome:
        """跑三次调用并逐条校验；调用失败抛 SettleError（整场不提交）。"""
        archive_data = await self._ask(
            template=_ARCHIVE_TEMPLATE,
            prompt=(
                f"【上一份档案】\n{material.previous_text or '没有'}\n\n"
                f"【本场她说的话】\n{material.speech_text}\n\n"
                f"{material.games_text or '【本场没有玩游戏】'}\n\n"
                "请写出这一场的档案。"
            ),
        )
        archive_rows = archive_data.get("archive")
        if archive_rows is None:
            archive_rows = []
        if not isinstance(archive_rows, list):
            raise SettleError("模型输出的 archive 不是列表")
        try:
            kept, keep_misses = keep_from_base(archive_data, material.previous_archive)
        except ValueError as exc:
            raise SettleError(str(exc)) from exc
        commitment_rows = await self._ask_for_list(
            template=_COMMITMENT_TEMPLATE,
            prompt=(
                f"【她能做的事】\n{material.abilities_text}\n\n"
                f"{material.games_text or '【本场没有玩游戏】'}\n\n"
                f"【还没了结的承诺】\n{material.open_text or '没有'}\n\n"
                f"【已判误记的承诺（这些话不是承诺）】\n{material.void_text or '没有'}\n\n"
                f"【本场她说的话】\n{material.speech_text}\n\n"
                "请找出这一场她新答应的、还没兑现的事。"
            ),
            key="commitments",
        )
        closure_rows: List[Any] = []
        if material.open_commitments:
            closure_rows = await self._ask_for_list(
                template=_CLOSE_TEMPLATE,
                prompt=(
                    f"【还没了结的承诺】\n{material.open_text}\n\n"
                    f"【本场她说的话】\n{material.speech_text}\n\n"
                    "请判断这些承诺里有哪些在这一场被她明确说做完了、或明确说不做了。"
                ),
                key="closed",
            )
        new_lines, archive_misses = self._validate_archive(archive_rows, material)
        commitments, commitment_misses = self._validate_commitments(commitment_rows, material)
        closures, closure_misses = self._validate_closures(closure_rows, material)
        return SettleOutcome(
            archive_lines=select_rolling(new_lines, kept),
            commitments=commitments,
            closures=closures,
            unregistered=[*archive_misses, *keep_misses, *commitment_misses, *closure_misses],
            input_note=material.speech_note,
        )

    async def _ask_for_list(self, *, template: str, prompt: str, key: str) -> List[Any]:
        """一次调用取一个列表字段；字段缺失当空列表，不是列表就按输出读不懂处理。"""
        data = await self._ask(template=template, prompt=prompt)
        rows = data.get(key)
        if rows is None:
            return []
        if not isinstance(rows, list):
            raise SettleError(f"模型输出的 {key} 不是列表")
        return rows

    async def _ask(self, *, template: str, prompt: str) -> Dict[str, Any]:
        """渲染系统提示词并调一次模型，返回解析出的 JSON 对象。"""
        system = self._prompt_manager.render(template)
        try:
            response = await self._llm.generate(prompt, profile=SUMMARY_PROFILE, system=system)
        except Exception as exc:  # noqa: BLE001 - 调用异常统一转成整理失败原因
            raise SettleError(f"模型调用失败：{exc}") from exc
        content = getattr(response, "content", None)
        if not getattr(response, "ok", False) or not content:
            raise SettleError("模型调用失败或返回空内容")
        return parse_json_object(str(content))

    # ------------------------------------------------------------------
    # 校验（全部是代码）
    # ------------------------------------------------------------------

    def _verified_quote(self, line: SettleSpeechLine, quote: Any) -> Optional[str]:
        """逐字引文校验：原话必须是那一行文本的逐字子串、且不超过上限，否则返回 None。

        给模型看的台词把连续空白压成了一个空格，模型照抄压缩后的文本也算逐字引文；
        原文与压缩文本两种口径都接受，否则换行/多空格的句子会被系统性地误判。
        """
        text = str(quote or "").strip()
        if not text or len(text) > MAX_QUOTE_CHARS:
            return None
        content = line.content or ""
        if text in content:
            return text
        return text if text in " ".join(content.split()) else None

    def _line_of(self, raw: Any, material: SettleMaterial) -> Optional[SettleSpeechLine]:
        """模型给的行号换成台词行；行号缺失或不是本场她的台词时返回 None。"""
        number = _as_line_number(raw)
        if number is None:
            return None
        return material.lines_by_number.get(number)

    def _validate_archive(
        self, rows: List[Any], material: SettleMaterial
    ) -> Tuple[List[PendingArchiveLine], List[UnregisteredItem]]:
        """本场新写的档案行的校验：逐字引文 + 至多 8 行；没通过的连同原话进未登记清单。

        挂的游戏与承诺同一口径；挂了游戏的，按那行台词的时刻认是在哪个存档里说的。
        ``lasting`` 只认布尔真值（名字、关系、住处这类长期有效的事，滚动时先占位）。
        """
        written: List[PendingArchiveLine] = []
        missed: List[UnregisteredItem] = []
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            if len(written) >= MAX_ARCHIVE_LINES:
                break
            title = _clip(str(raw.get("title") or ""), MAX_TITLE_CHARS)
            quote = str(raw.get("quote") or "").strip()
            line = self._line_of(raw.get("line"), material)
            if line is None:
                missed.append(
                    UnregisteredItem(kind="archive", what=title, quote=quote, reason="引用的不是本场她的台词")
                )
                continue
            if not title:
                missed.append(UnregisteredItem(kind="archive", quote=quote, reason="没有标题"))
                continue
            verified = self._verified_quote(line, quote)
            if verified is None:
                missed.append(
                    UnregisteredItem(
                        kind="archive",
                        what=title,
                        quote=quote,
                        reason=f"原话不是第 {raw.get('line')} 行的逐字片段（或超过 {MAX_QUOTE_CHARS} 字）",
                    )
                )
                continue
            game_name = _game_of(raw.get("game"), material)
            save = material.saves.save_at(game_name, int(line.timestamp_ms))
            written.append(
                PendingArchiveLine(
                    text=title,
                    quote=verified,
                    evidence_chat_id=int(line.id),
                    game=game_name,
                    save_id=save.id,
                    save_name=save.name,
                    lasting=raw.get("lasting") is True,
                )
            )
        return written, missed

    def _validate_commitments(
        self, rows: List[Any], material: SettleMaterial
    ) -> Tuple[List[PendingCommitment], List[UnregisteredItem]]:
        """新承诺的校验：逐字引文、所属游戏、对谁、不重复。"""
        written: List[PendingCommitment] = []
        missed: List[UnregisteredItem] = []
        open_ids = {int(row.id) for row in material.open_commitments}
        void_ids = {commitment_id for commitment_id, _what in material.void_titles}
        used_lines: Set[int] = set()
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            what = _clip(str(raw.get("what") or ""), MAX_TITLE_CHARS)
            quote = str(raw.get("quote") or "").strip()
            line = self._line_of(raw.get("line"), material)
            if line is None:
                missed.append(
                    UnregisteredItem(kind="commitment", what=what, quote=quote, reason="引用的不是本场她的台词")
                )
                continue
            if not what:
                missed.append(UnregisteredItem(kind="commitment", quote=quote, reason="没有动作标题"))
                continue
            same_as_raw = raw.get("same_as")
            same_as = _as_commitment_ref(same_as_raw)
            if same_as is None and _has_value(same_as_raw):
                # 认不出编号就不猜：宁可进未登记清单让运营者判断，也不把重申的事又登记一遍
                missed.append(
                    UnregisteredItem(
                        kind="commitment",
                        what=what,
                        quote=quote,
                        reason=f"same_as 编号认不出（{str(same_as_raw)[:20]}），未登记",
                    )
                )
                continue
            if same_as is not None and same_as in open_ids:
                logger.info(f"场次 {material.live_session_id}：模型认出这是又说了一遍 #{same_as}，不新建")
                continue
            if same_as is not None and same_as in void_ids:
                missed.append(
                    UnregisteredItem(
                        kind="commitment",
                        what=what,
                        quote=quote,
                        reason=f"与已判误记的 #{same_as} 相近，未登记",
                    )
                )
                continue
            if memory_ref_numbers(line.memory_refs):
                logger.info(f"场次 {material.live_session_id}：第 {raw.get('line')} 行已经提到某条旧承诺，不新建")
                continue
            if int(line.id) in used_lines:
                missed.append(
                    UnregisteredItem(
                        kind="commitment",
                        what=what,
                        quote=quote,
                        reason="同一句台词只登记一条承诺",
                    )
                )
                continue
            verified = self._verified_quote(line, quote)
            if verified is None:
                missed.append(
                    UnregisteredItem(
                        kind="commitment",
                        what=what,
                        quote=quote,
                        reason=f"原话不是第 {raw.get('line')} 行的逐字片段（或超过 {MAX_QUOTE_CHARS} 字）",
                    )
                )
                continue
            target = str(raw.get("to") or "").strip().lower()
            viewer_id: Optional[int] = None
            if target not in _TARGET_PERSON and target not in _TARGET_EVERYONE:
                missed.append(
                    UnregisteredItem(
                        kind="commitment",
                        what=what,
                        quote=quote,
                        reason=f"「对谁」字段认不出（{str(raw.get('to') or '空')[:20]}），未登记",
                    )
                )
                continue
            if target in _TARGET_PERSON:
                reply_viewer_id = line.reply_viewer_id
                if reply_viewer_id is None:
                    missed.append(
                        UnregisteredItem(
                            kind="commitment",
                            what=what,
                            quote=quote,
                            reason="说的是对某位观众答应，但这一行没有回应任何弹幕",
                        )
                    )
                    continue
                if bool(line.reply_simulated):
                    missed.append(
                        UnregisteredItem(
                            kind="commitment",
                            what=what,
                            quote=quote,
                            reason="回应的是模拟观众的弹幕，不当成对真实观众的承诺",
                        )
                    )
                    continue
                viewer_id = int(reply_viewer_id)
            # 挂了游戏的，按答应那句话的时刻认是在哪个存档里答应的
            game_name = _game_of(raw.get("game"), material)
            save = material.saves.save_at(game_name, int(line.timestamp_ms))
            written.append(
                PendingCommitment(
                    what=what,
                    quote=verified,
                    evidence_chat_id=int(line.id),
                    viewer_id=viewer_id,
                    game=game_name,
                    save_id=save.id,
                    save_name=save.name,
                )
            )
            used_lines.add(int(line.id))
        return written, missed

    def _validate_closures(
        self, rows: List[Any], material: SettleMaterial
    ) -> Tuple[List[CommitmentClosure], List[UnregisteredItem]]:
        """了结的校验：只能了结本场之前就有的未了结承诺，且引文逐字。"""
        written: List[CommitmentClosure] = []
        missed: List[UnregisteredItem] = []
        open_ids = {int(row.id) for row in material.open_commitments}
        closed_ids: Set[int] = set()
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            commitment_id_raw = raw.get("id")
            commitment_id = _as_commitment_ref(commitment_id_raw)
            quote = str(raw.get("quote") or "").strip()
            if commitment_id is None:
                if _has_value(commitment_id_raw):
                    missed.append(
                        UnregisteredItem(
                            kind="commitment",
                            what=f"了结 {str(commitment_id_raw)[:20]}",
                            quote=quote,
                            reason="承诺编号认不出，未登记",
                        )
                    )
                continue
            if commitment_id not in open_ids:
                # 运营者已判误记或已了结的条目不归模型改：记下来便于排查，不改库
                logger.info(
                    f"场次 {material.live_session_id}：模型了结的 #{commitment_id} 不是未了结的旧承诺"
                    "（可能已了结或判误记），跳过"
                )
                continue
            if commitment_id in closed_ids:
                continue
            line = self._line_of(raw.get("line"), material)
            if line is None:
                missed.append(
                    UnregisteredItem(
                        kind="commitment",
                        what=f"了结 #{commitment_id}",
                        quote=quote,
                        reason="引用的不是本场她的台词",
                    )
                )
                continue
            verified = self._verified_quote(line, quote)
            if verified is None:
                missed.append(
                    UnregisteredItem(
                        kind="commitment",
                        what=f"了结 #{commitment_id}",
                        quote=quote,
                        reason="原话不是所引那一行的逐字片段",
                    )
                )
                continue
            written.append(
                CommitmentClosure(commitment_id=int(commitment_id), close_chat_id=int(line.id), close_quote=verified)
            )
            closed_ids.add(int(commitment_id))
        return written, missed
