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
1. 场次档案：她的全部播出台词加游戏里的事，挑值得下次开播提起的至多 8 行新事；同时对上一份
   快照逐行挑留、去掉（要本场证据）或替换（要本场原话），代码再按固定规则凑成本场快照
   （只引用原行、至多 12 行，见 ``rolling``）；
2. 找新承诺：她的全部台词（附被回应的弹幕、回应的是哪位游戏玩家）、她在游戏公屏打的字加她能做的
   事，找出当众答应过、还没兑现的事；顺带列出她说欠着、账上却找不到的事（只进待审区，人批准才登记）；
3. 了结旧承诺：只在本场之前有未了结承诺时才调，判她说没说明白做完了或不做了。

校验全部是代码：逐字引文（原话必须是所引那一行文本的逐字子串且不超过上限）、所属游戏
（必须是本场玩过的）、对谁（对具体观众时那一行必须回应了真实观众的弹幕；对游戏玩家时要按
台词记下的回应对象、或公屏上点名与紧接着回话对上那位玩家，见 ``settle_players``）、不重复（与
已有承诺同一件事、或句子里已经提到某条承诺，都不新建）、只改未了结的承诺（运营者已判
误记或已了结的不受她的话影响）。校验没通过的条目连同原话写进整理报告，由运营者判断
要不要在 WebUI 手建。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, AbstractSet, Any, Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple, Union

from src.modules.context.tokens import TokenCounter
from src.modules.logging import get_logger
from src.modules.prompts import PromptManager
from src.modules.storage.models.chat import SettleSpeechLine
from src.modules.storage.models.events import GamePlaceMark
from src.modules.storage.models.memory import (
    ArchiveSnapshot,
    Commitment,
    CommitmentClosure,
    CueLogEntry,
    KeptSnapshotLine,
    PendingArchiveLine,
    PendingCommitment,
    PendingViewerCandidate,
    SettleReport,
    SideEffectStats,
    SnapshotChange,
    SnapshotLine,
    UnregisteredItem,
)
from src.modules.storage.models.sessions import LiveSession
from src.modules.storage.repos.sessions import MAX_SETTLE_ATTEMPTS
from src.modules.types.game_save import GameSave
from src.modules.types.session_kind import SESSION_KIND_LIVE
from src.agents.streamer.memory.render import memory_ref_numbers, render_commitment_number
from src.agents.streamer.memory.rolling import plan_from_ops, render_base, select_snapshot
from src.agents.streamer.memory.saves import SaveTimeline
from src.agents.streamer.memory.side_effects import compute_side_effects
from src.agents.streamer.memory.settle_candidates import (
    CandidateCheck,
    taken_from,
    validate_claimed_missing,
    validate_viewer_candidates,
)
from src.agents.streamer.memory.settle_players import PlayerChat, build_player_chat
from src.agents.streamer.memory.settle_cues import remembered_places, render_places, verified_keywords, verified_place
from src.modules.utils.time_utils import now_ms


if TYPE_CHECKING:
    from src.modules.storage.repos.archive import ArchiveRepo
    from src.modules.storage.repos.chat import ChatRepo
    from src.modules.storage.repos.commitments import CommitmentRepo
    from src.modules.storage.models.game_players import GameChatLine, GamePlayer
    from src.modules.storage.repos.events import EventRepo
    from src.modules.storage.repos.game_players import GamePlayerRepo
    from src.modules.storage.repos.memory_cue_log import MemoryCueLogRepo
    from src.modules.storage.repos.sessions import SessionRepo

__all__ = ["MAX_ATTEMPTS", "SessionSettler", "SettleError", "parse_json_object"]

logger = get_logger("SessionSettler")

# 下播整理单独的模型档位：一场只调几次，可以单独换更强的模型而不影响直播里的话题总结
SETTLE_PROFILE = "settle"

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
# 一次整理最多写几行本场新事（模型多给了就只取前几行；连同接住的行，一份快照的上限见 rolling）
MAX_ARCHIVE_LINES = 8
# 逐字引文与标题的上限（超出的引文整条丢弃，超出的标题截断）
MAX_QUOTE_CHARS = 80
MAX_TITLE_CHARS = 30
# 交给「找新承诺」的旧承诺标题上限：未了结的全给，判误记、已了结的只给最近的这些条
MAX_VOID_TITLES = 40
MAX_CLOSED_TITLES = 40
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
    # 上一份快照（滚动基底）与给模型看的旧行清单；没有上一份时为 None 与空串
    previous: Optional[ArchiveSnapshot] = None
    previous_text: str = ""
    # 本场开播时刻：本场新写或再提起的行，"最近一次提起"记这个时刻
    started_at_ms: int = 0
    # 本场的地点动静与记住的地点：档案行的地点只能从这里挑，按地名或说话时在不在跟前核对
    place_marks: List[GamePlaceMark] = field(default_factory=list)
    places: List[str] = field(default_factory=list)
    # 本场的游戏玩家：代号、她在公屏打的字、每一句能对上谁（对玩家的承诺按它核对）
    players: PlayerChat = field(default_factory=PlayerChat)
    # 重新整理这一场时留下的观众来源行（人批准的观众候选）：模型看不到、动不了，代码接着带进快照
    carried: List[SnapshotLine] = field(default_factory=list)
    # 已了结的旧承诺标题：找"她说欠着、账上却找不到的"时一并比对
    closed_titles: List[Tuple[int, str]] = field(default_factory=list)
    closed_text: str = ""


@dataclass(frozen=True)
class SettleOutcome:
    """一场整理的结果：要写进去的行、没能登记的条目。"""

    archive_lines: List[PendingArchiveLine] = field(default_factory=list)
    snapshot_kept: List[KeptSnapshotLine] = field(default_factory=list)
    snapshot_changes: List[SnapshotChange] = field(default_factory=list)
    commitments: List[PendingCommitment] = field(default_factory=list)
    closures: List[CommitmentClosure] = field(default_factory=list)
    unregistered: List[UnregisteredItem] = field(default_factory=list)
    input_note: Optional[str] = None
    # 观众候选（人批准才进记忆）与因个人信息丢掉的条数
    viewer_candidates: List[PendingViewerCandidate] = field(default_factory=list)
    candidates_personal_dropped: int = 0


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


def close_user_prompt(material: SettleMaterial) -> str:
    """了结那次调用的用户提示词：账上还没了结的承诺 + 本场台词 + 一句问法。

    问法列全四种了结（做完了、不做了、坦白做不到、换个方式兑现），与了结系统提示词的口径一致：
    只问"做完或不做了"的话，她当众说"那个我真弄不来"之后这条承诺了结不了，会一直留在简报里，
    她要么反复道歉，要么硬说做完了。离线评测按同一个函数拼提示词，量的就是生产实际发出去的文字。
    """
    return (
        f"【还没了结的承诺】\n{material.open_text}\n\n"
        f"【本场她说的话】\n{material.speech_text}\n\n"
        "请判断这些承诺里有哪些在这一场被她当众交代了结果：做完了、不做了、坦白做不到，或者换了个方式兑现。"
    )


def _clip(text: str, limit: int) -> str:
    """按字数截断（标题用；引文不截断，截过的引文已经不是逐字引文）。"""
    cleaned = (text or "").strip()
    return cleaned[:limit] if len(cleaned) > limit else cleaned


# "对谁"字段的已知取值：对具体观众 / 对全体观众。提示词只要求这两个值，
# 其余取值（含缺失、空串）一律不登记——认不出是谁就不能写进承诺账本
_TARGET_PERSON = frozenset({"person", "viewer", "观众"})
_TARGET_EVERYONE = frozenset({"everyone", "all", "全体", "所有人", "大家"})
_TARGET_PLAYER = frozenset({"player", "玩家"})


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


def _seen_line_ids(material: SettleMaterial) -> List[int]:
    """整理读原料时看到的旧档案行：上一份快照的各行，加这一场接着带的观众来源行。"""
    previous = material.previous.lines if material.previous is not None else []
    return sorted({int(item.line.id) for item in (*previous, *material.carried)})


def _reply_player_of(line: SettleSpeechLine) -> Optional[int]:
    """这一行台词在回应的玩家；它在回一条弹幕时不算（对象是那位观众）。

    台词落库时已经不再给回弹幕的行记回应玩家；升级前落库的行可能两样都记了，这里再挡一次，
    答应观众的事不会因为那一窗恰好只有一位玩家开口就记到玩家头上。
    """
    if line.reply_player_id is None or line.reply_chat_id is not None:
        return None
    return int(line.reply_player_id)


def _player_target(
    raw: Mapping[str, Any],
    line: Optional[SettleSpeechLine],
    chat: Optional["GameChatLine"],
    material: SettleMaterial,
) -> Tuple[Optional[int], str]:
    """对玩家的承诺是对哪位：模型写的代号必须按两条证据路径之一对上，返回 (玩家主键, 对不上的原因)。

    台词行：落库时记下的回应对象必须就是他。公屏那句：点了他的名字，或者紧接着他的话说的。
    """
    player_id = material.players.player_of(raw.get("player"))
    if player_id is None:
        return None, f"说的是对某位玩家答应，但玩家代号认不出（{str(raw.get('player') or '空')[:20]}），未登记"
    if line is not None:
        if _reply_player_of(line) != player_id:
            return None, "说的是对某位玩家答应，但这一行不是在回应这位玩家，未登记"
        return player_id, ""
    if chat is None or player_id not in material.players.targets.get(int(chat.id), frozenset()):
        return None, "公屏这句没点这位玩家的名字，也不是紧接着他的话说的，未登记"
    return player_id, ""


def _list_field(data: Mapping[str, Any], key: str) -> List[Any]:
    """模型输出里取一个列表字段：缺失当空列表，不是列表按输出读不懂处理。"""
    rows = data.get(key)
    if rows is None:
        return []
    if not isinstance(rows, list):
        raise SettleError(f"模型输出的 {key} 不是列表")
    return rows


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
        min_session_ms: int = 0,
        clock: Callable[[], int] = now_ms,
        game_player_repo: Optional["GamePlayerRepo"] = None,
        cue_log_repo: Optional["MemoryCueLogRepo"] = None,
    ) -> None:
        """仓储、模型与提示词由主播 Agent 构造时注入。

        event_repo 缺失时原料少游戏这一类，整理照常跑；archive_repo 缺失时没有上一份快照
        可接，本场快照只有本场的事；game_names_provider 返回当前对主播可见的游戏 Agent 名，
        用来写「她能做的事」；min_session_ms 与常驻简报的"上一场"时长下限同一个值，滚动之前
        的旧场次现拼基底时用来数"时长够的场次"。game_player_repo 缺失时原料里没有游戏玩家与公屏，
        对玩家的承诺一条也认不出（模型写了也进未登记）。cue_log_repo 缺失时唠叨指标只按台词
        点名算，线索卡那几项为空。
        """
        self._sessions = sessions_repo
        self._chat = chat_repo
        self._commitments = commitments_repo
        self._events = event_repo
        self._archive = archive_repo
        self._llm = llm_service
        self._prompt_manager = prompt_manager
        self._game_names_provider = game_names_provider
        self._players = game_player_repo
        self._cue_log = cue_log_repo
        self._min_session_ms = max(0, int(min_session_ms))
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
        try:
            # 待审的观众候选只按时间过期：列出后 14 天没人审就删
            expired = await self._sessions.expire_viewer_candidates(now_ms_value=int(self._clock()))
            if expired:
                logger.info(f"删掉 {expired} 条过期没人审的观众候选")
        except Exception as exc:  # noqa: BLE001 - 过期清理失败不挡整理，下次调度再删
            logger.warning(f"清理过期观众候选失败: {exc}", exc=exc)
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

        listed: Dict[str, int] = {}
        for candidate in outcome.viewer_candidates:
            listed[candidate.kind] = listed.get(candidate.kind, 0) + 1
        report = SettleReport(
            unregistered=outcome.unregistered,
            input_note=outcome.input_note,
            snapshot_changes=outcome.snapshot_changes,
            candidates_listed=listed,
            candidates_personal_dropped=outcome.candidates_personal_dropped,
            # 唠叨指标和本场整理一起写：同一件旧事提了几次、没人问时主动提几次、推的卡用上几张
            side_effects=await self._side_effects(session, delivered),
        )
        has_report = bool(
            outcome.unregistered
            or outcome.input_note
            or outcome.snapshot_changes
            or listed
            or outcome.candidates_personal_dropped
            or report.side_effects
        )
        now = int(self._clock())
        try:
            written = await self._sessions.apply_settlement(
                live_session_id=int(live_session_id),
                archive_lines=outcome.archive_lines,
                commitments=outcome.commitments,
                closures=outcome.closures,
                settled_at_ms=now,
                now_ms_value=now,
                settle_report=report.model_dump_json() if has_report else None,
                snapshot_kept=outcome.snapshot_kept,
                viewer_candidates=outcome.viewer_candidates,
                # 读原料时看到的旧行：调模型期间人批准、插进最新快照的观众来源行不在其中，落库时补进本场快照
                seen_line_ids=_seen_line_ids(material),
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
            f"未登记 {len(outcome.unregistered)} 条、观众候选 {written.get('candidates', 0)} 条"
        )
        return True

    async def _side_effects(self, session: LiveSession, delivered: List[SettleSpeechLine]) -> Optional[SideEffectStats]:
        """本场的唠叨指标；读线索卡记录失败时只按台词点名算，算的时候出错就不给这一节，都不拖累整理。

        指标是附带的：档案、承诺、了结已经由模型产出，指标算错（线索卡记录里有坏行之类）不能让这一场
        整理整个记失败、下次再白调一遍模型。
        """
        cards: List[CueLogEntry] = []
        if self._cue_log is not None:
            try:
                cards = await self._cue_log.list_session_entries(live_session_id=int(session.id))
            except Exception as exc:  # noqa: BLE001 - 指标是附带的，读不到卡照样整理
                logger.warning(f"场次 {session.id} 读线索卡记录失败，唠叨指标不含线索卡: {exc}", exc=exc)
        duration_ms = int(session.ended_at_ms or session.started_at_ms) - int(session.started_at_ms)
        try:
            return compute_side_effects(delivered, cards, duration_ms=duration_ms)
        except Exception as exc:  # noqa: BLE001 - 指标算不出来，本场报告不含这一节，整理照常写入
            logger.warning(f"场次 {session.id} 唠叨指标没算出来，本场报告不含这一节: {exc}", exc=exc)
            return None

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
        """把一场的原料全取出来：她说的话、游戏（连同待过的存档）、游戏玩家与公屏、她能做的事、旧承诺。"""
        # 先认本场的玩家：台词里"回应玩家N"的代号要和公屏段同一套
        players = await self._load_players(session, delivered)
        speech_text, lines_by_number, speech_note = self._render_speech(delivered, player_codes=players.codes)
        saves = await self._load_saves(session)
        games, games_text = await self._render_games(session, saves)
        game_agents = self._enabled_game_names()
        abilities_text = self._render_abilities(game_agents)
        open_commitments, open_text, void_titles, void_text = await self._load_commitments(session)
        closed_titles = await self._load_closed_titles(session)
        previous = await self._load_previous(session)
        carried = await self._load_carried(session)
        place_marks = await self._load_place_marks(session)
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
            previous=previous,
            previous_text=render_base(previous),
            started_at_ms=int(session.started_at_ms),
            place_marks=place_marks,
            places=remembered_places(place_marks),
            players=players,
            carried=carried,
            closed_titles=closed_titles,
            closed_text="\n".join(f"#{commitment_id} 「{what}」" for commitment_id, what in closed_titles),
        )

    async def _load_players(self, session: LiveSession, delivered: Sequence[SettleSpeechLine]) -> PlayerChat:
        """本场的游戏玩家与公屏聊天；读不到就当没有（对玩家的承诺这一场认不出，其余照常整理）。"""
        if self._players is None:
            return PlayerChat()
        try:
            lines: List["GameChatLine"] = list(
                await self._players.list_session_game_chat(live_session_id=int(session.id))
            )
            # 台词里回应过的玩家（在回弹幕的行不算）先拿代号
            spoken = [player for player in map(_reply_player_of, delivered) if player is not None]
            wanted = dict.fromkeys([*spoken, *(int(line.player_id) for line in lines if line.player_id is not None)])
            players: Dict[int, "GamePlayer"] = {}
            for player_id in wanted:
                player = await self._players.get_player(player_id=player_id)
                if player is not None:
                    players[player_id] = player
        except Exception as exc:  # noqa: BLE001 - 少了玩家只少一类承诺对象，整理照常跑
            logger.warning(f"场次 {session.id} 读游戏玩家与公屏失败，这一场认不出对玩家的承诺: {exc}", exc=exc)
            return PlayerChat()
        return build_player_chat(speech_player_ids=spoken, lines=lines, players=players, clock_text=_clock_text)

    async def _load_closed_titles(self, session: LiveSession) -> List[Tuple[int, str]]:
        """最近已了结的承诺标题（旧的在前）：她说"上次答应过"时，账上了结过的也算对得上。"""
        try:
            rows = await self._commitments.list_commitments(statuses=["closed"], limit=MAX_CLOSED_TITLES)
        except Exception as exc:  # noqa: BLE001 - 少一类比对原料：多列几条候选，人审时驳回即可
            logger.warning(f"场次 {session.id} 读已了结承诺失败: {exc}", exc=exc)
            return []
        return [(int(row.id), row.what) for row in reversed(rows)]

    async def _load_place_marks(self, session: LiveSession) -> List[GamePlaceMark]:
        """本场的地点动静（记住、忘掉、走近、离开）；读不到就当没有，档案行只是不挂地点。"""
        if self._events is None:
            return []
        try:
            return list(await self._events.list_session_place_marks(live_session_id=int(session.id)))
        except Exception as exc:  # noqa: BLE001 - 少了地点只少一层标注，整理照常跑
            logger.warning(f"场次 {session.id} 读地点动静失败，档案行不挂地点: {exc}", exc=exc)
            return []

    async def _load_previous(self, session: LiveSession) -> Optional[ArchiveSnapshot]:
        """本场要接着滚的上一份快照（开播早于本场的最近一份；旧场次由几场旧档案现拼）。

        读失败按整理失败处理、下次再试：没接住上一份就写，本场快照会顶掉前几场的事，
        简报从此再也看不到它们。
        """
        if self._archive is None:
            return None
        try:
            return await self._archive.snapshot_before(
                before_started_at_ms=int(session.started_at_ms),
                kind=SESSION_KIND_LIVE,
                min_duration_ms=self._min_session_ms,
            )
        except Exception as exc:  # noqa: BLE001 - 基底读不到就不写，免得顶掉前几场的事
            raise SettleError(f"取上一份档案快照失败：{exc}") from exc

    async def _load_carried(self, session: LiveSession) -> List[SnapshotLine]:
        """这一场重新整理时要接着带进快照的观众来源行；第一次整理时一条也没有。

        读失败按整理失败处理、下次再试：没带上就写，人批准过的事会从她的简报里消失。
        """
        if self._archive is None:
            return []
        try:
            return list(
                await self._archive.carried_viewer_lines(
                    live_session_id=int(session.id), started_at_ms=int(session.started_at_ms)
                )
            )
        except Exception as exc:  # noqa: BLE001 - 读不到就不写，免得冲掉人批准过的事
            raise SettleError(f"取人批准过的观众来源行失败：{exc}") from exc

    def _render_speech(
        self, delivered: List[SettleSpeechLine], *, player_codes: Optional[Mapping[int, str]] = None
    ) -> Tuple[str, Dict[int, SettleSpeechLine], Optional[str]]:
        """台词渲染成带编号的行；回应弹幕的把那句原文与发言人代号附在前面。

        编号是模型输出用来指回具体台词的键：整场台词超过模型窗口预算时只保留一部分，
        按保留顺序重新编号，台词行本身仍指向原行。返回的第三项是裁剪说明（未裁剪为 None），
        由调用方写进整理报告。同一观众整场用同一个代号，模特才能说清「对谁答应的」而不用昵称。
        回应游戏玩家的那一句标"回应玩家N"，代号与公屏段同一套（``player_codes``，玩家主键 → 代号）。
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
            player_note = ""
            # 在回弹幕的行只标回复了哪位观众，不再标回应玩家
            reply_player = _reply_player_of(line)
            player_code = (player_codes or {}).get(reply_player) if reply_player is not None else None
            if player_code:
                player_note = f"（回应{player_code}）"
            memory_note = ""
            used = sorted(memory_ref_numbers(line.memory_refs))
            if used:
                memory_note = "（提到" + "、".join(f"约#{item}" for item in used) + "）"
            bodies.append(f"{_clock_text(int(line.timestamp_ms))} 你{reply_note}{player_note}：{content}{memory_note}")
            # 回应观众、回应玩家、引用记忆的行是关键行：台词超预算裁剪时优先保留
            key_flags.append(bool(reply_note) or bool(player_note) or bool(used))

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
        """台词可用的 token 预算：按整理 profile 的上下文窗口换算，查不到按保守窗口。

        整理原料里台词占大头，其余（系统提示词、旧承诺清单、模型输出）留在剩余窗口里，
        所以只按窗口的一定比例给台词。
        """
        window = 0
        getter = getattr(self._llm, "get_profile_context_window", None)
        if callable(getter):
            try:
                value = getter(SETTLE_PROFILE)
                window = int(value) if isinstance(value, int) and value > 0 else 0
            except Exception as exc:  # noqa: BLE001 - 查不到窗口按保守预算，整理照常进行
                logger.warning(f"查询 {SETTLE_PROFILE} 上下文窗口失败，按保守窗口换算台词预算: {exc}")
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
                f"{render_places(material.places)}\n\n"
                "请写出这一场的档案。"
            ),
        )
        archive_rows = archive_data.get("archive")
        if archive_rows is None:
            archive_rows = []
        if not isinstance(archive_rows, list):
            raise SettleError("模型输出的 archive 不是列表")
        # 旧行只挑不改写：没列出的保留，去掉要本场证据，替换出的新行过同一个校验器
        try:
            plan = plan_from_ops(
                archive_data.get("previous"),
                material.previous.lines if material.previous is not None else [],
                session_started_at_ms=material.started_at_ms,
                line_of=lambda raw: self._line_of(raw, material),
                validate_new=lambda raw: self._validate_archive([raw], material),
            )
        except ValueError as exc:
            raise SettleError(str(exc)) from exc
        # 找承诺那次顺带列出"她说欠着、账上找不到的"：同一份原料，不多一次调用
        commitment_data = await self._ask(
            template=_COMMITMENT_TEMPLATE,
            prompt=(
                f"【她能做的事】\n{material.abilities_text}\n\n"
                f"{material.games_text or '【本场没有玩游戏】'}\n\n"
                f"【还没了结的承诺】\n{material.open_text or '没有'}\n\n"
                f"【已了结的承诺】\n{material.closed_text or '没有'}\n\n"
                f"【已判误记的承诺（这些话不是承诺）】\n{material.void_text or '没有'}\n\n"
                f"【本场她说的话】\n{material.speech_text}\n\n"
                + (f"{material.players.text}\n\n" if material.players.text else "")
                + "请找出这一场她新答应的、还没兑现的事。"
            ),
        )
        commitment_rows = _list_field(commitment_data, "commitments")
        closure_rows: List[Any] = []
        if material.open_commitments:
            closure_rows = await self._ask_for_list(
                template=_CLOSE_TEMPLATE,
                prompt=close_user_prompt(material),
                key="closed",
            )
        new_lines, archive_misses = self._validate_archive(archive_rows, material)
        # 先认了结、再认新承诺：一行台词点名了某条旧承诺时，那条旧承诺本场有没有了结，
        # 决定这一行里的新答应是"换了个做法"（照常登记）还是"又提了一遍旧事"（不登记、进报告）
        closures, closure_misses = self._validate_closures(closure_rows, material)
        commitments, commitment_misses = self._validate_commitments(
            commitment_rows, material, closed_ids={closure.commitment_id for closure in closures}
        )
        # 人批准的观众来源行：重新整理时接着带进快照，和接住的旧行一起按规则取舍；基底里已有的不重复带
        kept_ids = {int(item.line.id) for item in plan.kept}
        carried = [item for item in material.carried if int(item.line.id) not in kept_ids]
        selection = select_snapshot(
            [*new_lines, *plan.replacements], [*plan.kept, *carried], session_started_at_ms=material.started_at_ms
        )
        candidates = await self._check_candidates(archive_data.get("viewer_candidates"), material, selection.lines)
        claimed = await self._check_claimed(commitment_data.get("claimed_missing"), material, commitments, closures)
        return SettleOutcome(
            archive_lines=selection.lines,
            snapshot_kept=selection.kept,
            snapshot_changes=[*plan.changes, *selection.evicted],
            commitments=commitments,
            closures=closures,
            unregistered=[
                *archive_misses,
                *plan.misses,
                *commitment_misses,
                *closure_misses,
                *candidates.missed,
                *claimed.missed,
            ],
            input_note=material.speech_note,
            viewer_candidates=[*candidates.candidates, *claimed.candidates],
            candidates_personal_dropped=candidates.personal_dropped + claimed.personal_dropped,
        )

    async def _check_claimed(
        self,
        rows: Any,
        material: SettleMaterial,
        commitments: Sequence[PendingCommitment],
        closures: Sequence[CommitmentClosure],
    ) -> CandidateCheck:
        """核对找承诺那次顺带列出的"她说欠着、账上却找不到的"，只进待审区，人批准才登记成承诺。

        账上对得上的不列：未了结、已了结、本场新登记的标题，以及本场已经作了新承诺或了结证据的那几行。
        字段不是列表时只告警、当没有：这一项是附带的，不该让整场整理失败。
        """
        if rows is None or rows == []:
            return CandidateCheck()
        if not isinstance(rows, list):
            logger.warning(f"场次 {material.live_session_id}：模型输出的 claimed_missing 不是列表，这一项按没有处理")
            return CandidateCheck()
        rejected_lines: List[int] = []
        rejected_titles: List[str] = []
        try:
            rejected_lines, rejected_titles = await self._sessions.rejected_claim_keys()
        except Exception as exc:  # noqa: BLE001 - 去重依据读不到不挡整理
            logger.warning(f"读驳回过的「她说欠着的」候选失败，本次不按驳回记录去重: {exc}", exc=exc)
        return validate_claimed_missing(
            rows,
            line_of=lambda raw: self._line_of(raw, material),
            game_of=lambda raw: _game_of(raw, material),
            saves=material.saves,
            ledger_titles=[
                *(row.what for row in material.open_commitments),
                *(what for _commitment_id, what in material.closed_titles),
                *(item.what for item in commitments),
            ],
            taken_lines=[
                *(item.evidence_chat_id for item in commitments if item.evidence_chat_id is not None),
                *(closure.close_chat_id for closure in closures if closure.close_chat_id is not None),
            ],
            rejected_reply_chats=rejected_lines,
            rejected_titles=rejected_titles,
        )

    async def _check_candidates(
        self, rows: Any, material: SettleMaterial, new_lines: List[PendingArchiveLine]
    ) -> CandidateCheck:
        """核对档案那次调用顺带列出的观众候选：和本场新写、上一份档案、驳回过的都去重。

        驳回记录读不到时照常核对，只是少了这一道去重（下次整理还会再列，人再驳回一次即可）。
        """
        rejected_chats: List[int] = []
        rejected_titles: List[str] = []
        try:
            rejected_chats, rejected_titles = await self._sessions.rejected_candidate_keys()
        except Exception as exc:  # noqa: BLE001 - 去重依据读不到不挡整理
            logger.warning(f"读驳回过的观众候选失败，本次不按驳回记录去重: {exc}", exc=exc)
        # 去重依据：上一份档案、本场留下的观众来源行（重新整理时批准过的不再列）、本场新写的行
        base = [*(material.previous.lines if material.previous is not None else []), *material.carried]
        new_evidence, new_titles = taken_from(new_lines)
        old_evidence, old_titles = taken_from(base)
        return validate_viewer_candidates(
            rows,
            line_of=lambda raw: self._line_of(raw, material),
            game_of=lambda raw: _game_of(raw, material),
            saves=material.saves,
            taken_evidence=[*new_evidence, *old_evidence],
            taken_titles=[*new_titles, *old_titles],
            rejected_viewer_chats=rejected_chats,
            rejected_titles=rejected_titles,
        )

    async def _ask_for_list(self, *, template: str, prompt: str, key: str) -> List[Any]:
        """一次调用取一个列表字段；字段缺失当空列表，不是列表就按输出读不懂处理。"""
        return _list_field(await self._ask(template=template, prompt=prompt), key)

    async def _ask(self, *, template: str, prompt: str) -> Dict[str, Any]:
        """渲染系统提示词并调一次模型，返回解析出的 JSON 对象。"""
        system = self._prompt_manager.render(template)
        try:
            response = await self._llm.generate(prompt, profile=SETTLE_PROFILE, system=system)
        except Exception as exc:  # noqa: BLE001 - 调用异常统一转成整理失败原因
            raise SettleError(f"模型调用失败：{exc}") from exc
        content = getattr(response, "content", None)
        if not getattr(response, "ok", False) or not content:
            raise SettleError("模型调用失败或返回空内容")
        return parse_json_object(str(content))

    # ------------------------------------------------------------------
    # 校验（全部是代码）
    # ------------------------------------------------------------------

    def _verified_quote(self, line: Union[SettleSpeechLine, "GameChatLine"], quote: Any) -> Optional[str]:
        """逐字引文校验：原话必须是那一行文本的逐字子串、且不超过上限，否则返回 None。

        对玩家的承诺引的可以是她在公屏打的那句（同样按它的原文核对）。
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
        ``lasting`` 只认布尔真值（名字、住处、关系这类稳定的事，快照取舍时先留）。
        触发词与地点由代码核对（见 ``settle_cues``），对不上的丢掉、行照写；地点只给挂了游戏的行。
        替换旧行的新行也走这里，与新事同一个口径。
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
            place = ""
            if game_name is not None:
                place = verified_place(
                    raw.get("place"),
                    places=material.places,
                    texts=(title, line.content or ""),
                    marks=material.place_marks,
                    at_ms=int(line.timestamp_ms),
                    save_id=save.id,
                )
            written.append(
                PendingArchiveLine(
                    text=title,
                    quote=verified,
                    evidence_chat_id=int(line.id),
                    game=game_name,
                    save_id=save.id,
                    save_name=save.name,
                    lasting=raw.get("lasting") is True,
                    keywords=verified_keywords(raw.get("keywords"), title, verified),
                    place=place,
                )
            )
        return written, missed

    def _validate_commitments(
        self, rows: List[Any], material: SettleMaterial, *, closed_ids: AbstractSet[int] = frozenset()
    ) -> Tuple[List[PendingCommitment], List[UnregisteredItem]]:
        """新承诺的校验：逐字引文、所属游戏、对谁、不重复。

        对游戏玩家的承诺证据可以是台词行（记下了在回应他），也可以是她在公屏打的那句（``chat``），
        对谁按 ``_player_target`` 核对；公屏那句只登记对玩家的承诺。

        ``closed_ids`` 是本次整理了结的旧承诺编号。台词点名了某条旧承诺（记忆引用里有它的编号）时：
        那条本场被了结了，这一行里的新答应多半是换了个做法（"画我画不了，下次用方块给你拼一幅"），
        照常登记；没了结，就当她又提了一遍旧事，不新建，写进未登记清单，运营者在整理报告里看得见。
        """
        written: List[PendingCommitment] = []
        missed: List[UnregisteredItem] = []
        open_ids = {int(row.id) for row in material.open_commitments}
        void_ids = {commitment_id for commitment_id, _what in material.void_titles}
        used_lines: Set[int] = set()
        used_chats: Set[int] = set()
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            what = _clip(str(raw.get("what") or ""), MAX_TITLE_CHARS)
            quote = str(raw.get("quote") or "").strip()
            line = self._line_of(raw.get("line"), material)
            # 没给台词行时看是不是她在游戏公屏打的那句（只用来登记对玩家的承诺）
            chat = material.players.chat_line(raw.get("chat")) if line is None else None
            if line is None and chat is None:
                missed.append(
                    UnregisteredItem(
                        kind="commitment", what=what, quote=quote, reason="引用的不是本场她的台词或她在公屏打的字"
                    )
                )
                continue
            where = f"第 {raw.get('line')} 行" if line is not None else f"公屏 {raw.get('chat')}"
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
            referenced = memory_ref_numbers(line.memory_refs) if line is not None else set()
            if referenced and not referenced & closed_ids:
                # 点名的旧承诺本场都没了结：按又提起旧事处理，不新建；进报告而不是静默丢掉
                named = "、".join(render_commitment_number(number) for number in sorted(referenced))
                missed.append(
                    UnregisteredItem(
                        kind="commitment",
                        what=what,
                        quote=quote,
                        reason=f"这一行提到了旧承诺 {named}，本场没有了结，按又提起旧事处理，未登记",
                    )
                )
                continue
            if (line is not None and int(line.id) in used_lines) or (chat is not None and int(chat.id) in used_chats):
                missed.append(
                    UnregisteredItem(
                        kind="commitment",
                        what=what,
                        quote=quote,
                        reason="同一句台词只登记一条承诺",
                    )
                )
                continue
            verified = self._verified_quote(line if line is not None else chat, quote)
            if verified is None:
                missed.append(
                    UnregisteredItem(
                        kind="commitment",
                        what=what,
                        quote=quote,
                        reason=f"原话不是{where}的逐字片段（或超过 {MAX_QUOTE_CHARS} 字）",
                    )
                )
                continue
            target = str(raw.get("to") or "").strip().lower()
            viewer_id: Optional[int] = None
            player_id: Optional[int] = None
            if target not in _TARGET_PERSON and target not in _TARGET_EVERYONE and target not in _TARGET_PLAYER:
                missed.append(
                    UnregisteredItem(
                        kind="commitment",
                        what=what,
                        quote=quote,
                        reason=f"「对谁」字段认不出（{str(raw.get('to') or '空')[:20]}），未登记",
                    )
                )
                continue
            if chat is not None and target not in _TARGET_PLAYER:
                # 公屏上冲所有人说的话看不出是对哪位玩家，也不是播给观众的台词：不登记
                missed.append(
                    UnregisteredItem(
                        kind="commitment", what=what, quote=quote, reason="公屏上打的字只登记对某位玩家的承诺，未登记"
                    )
                )
                continue
            if target in _TARGET_PERSON and line is not None:
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
            if target in _TARGET_PLAYER:
                player_id, reason = _player_target(raw, line, chat, material)
                if player_id is None:
                    missed.append(UnregisteredItem(kind="commitment", what=what, quote=quote, reason=reason))
                    continue
            if chat is not None:
                # 公屏那句一定是在那个游戏、那个存档里打的：游戏与存档取它自己的
                game_name: Optional[str] = _game_of(chat.game, material) or chat.game
                save_id, save_name = chat.save_id, chat.save_name
            else:
                # 挂了游戏的，按答应那句话的时刻认是在哪个存档里答应的
                game_name = _game_of(raw.get("game"), material)
                save = material.saves.save_at(game_name, int(line.timestamp_ms))
                save_id, save_name = save.id, save.name
            written.append(
                PendingCommitment(
                    what=what,
                    quote=verified,
                    evidence_chat_id=int(line.id) if line is not None else None,
                    evidence_game_chat_id=int(chat.id) if chat is not None else None,
                    viewer_id=viewer_id,
                    target_player_id=player_id,
                    game=game_name,
                    save_id=save_id,
                    save_name=save_name,
                )
            )
            if line is not None:
                used_lines.add(int(line.id))
            if chat is not None:
                used_chats.add(int(chat.id))
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
