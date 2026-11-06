"""被动想起的规则与地点两路：从档案行、承诺、老梗里找出这一批该想起来的条目。

只找候选，不管限频、用上即停、每批上限与卡面——那些归线索推送（``MemoryCueProvider``）。

- **规则一路**：触发词匹配（子串加拼音层，见 ``textmatch``），看本批观众弹幕与她最近 3 句播出的
  台词。候选是写了触发词的档案行和老梗栏里的梗；承诺还没有触发词（找承诺的提示词还没改），只走
  向量一路。命中它的来源全记下：观众弹幕、她的台词；只有她的台词命中的叫"自触发"，推送侧按更严的
  条件处理。老梗只认观众弹幕——老梗栏写明了"观众提起时才接，别自己翻出来"。
- **地点一路**：角色从 48 格外（或这场还没读到过）走进某个记住的地点 24 格内，且这份距离是
  最近读到的，就算"到了"。到了以后推挂在这个地点、这个存档里的档案行；每个地点每场只算一次到达
  （她大部分时间待在营地附近，按半径持续判断会让营地的事一场推好几遍）。

候选池（写了触发词或挂了地点的档案行、老梗）每场第一次用时读库并建好匹配器，
之后每 10 分钟重读一次：整理只在场与场之间写新行，运营者在审核里改了触发词，十分钟内跟上。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple, Union

from src.modules.logging import get_logger
from src.modules.storage.models.memory import ArchiveLine
from src.modules.types.game_save import GameSave
from src.modules.types.game_situation import GameSituation

from .saves import SavePlace, place_of
from .textmatch import KeywordMatcher

if TYPE_CHECKING:
    from src.modules.storage.repos import ArchiveRepo

__all__ = [
    "ARRIVE_WITHIN",
    "LEAVE_BEYOND",
    "CueText",
    "ItemKey",
    "PassiveHit",
    "PlaceArrivals",
    "RulePool",
    "SOURCE_DANMAKU",
    "SOURCE_GAME",
    "SOURCE_PLACE",
    "SOURCE_SELF",
    "SOURCE_SITUATION",
    "parse_inside_jokes",
]

logger = get_logger("MemoryCueProvider")

# 命中来源：观众弹幕 / 她的台词 / 走到记住的地点 / 游戏情境查询 / 游戏开着
SOURCE_DANMAKU = "danmaku"
SOURCE_SELF = "self"
SOURCE_PLACE = "place"
SOURCE_SITUATION = "situation"
SOURCE_GAME = "game"

# 条目键：("archive", 档案行编号) / ("commitment", 承诺编号) / ("joke", 老梗主词)
ItemKey = Tuple[str, Union[int, str]]

# 到达判定的两道距离（格）：进到 24 格内算到了；要先离开到 48 格外，下次进来才再算
ARRIVE_WITHIN = 24
LEAVE_BEYOND = 48
# 角色位置多久前读到的还算数：位置只在游戏 Agent 速览时更新，太旧的距离不判到达
SITUATION_STALE_MS = 3 * 60 * 1000
# 一个地点到达时最多推几行（新的在前）
LINES_PER_PLACE = 2
# 候选池多久重读一次、最多取多少行档案
POOL_REFRESH_MS = 10 * 60 * 1000
POOL_MAX_LINES = 500

# 老梗栏一行："倒立洗头/洗头挑战：她说过输了就倒立洗头"——冒号前是梗与别名，冒号后是说明
_JOKE_HEAD = re.compile(r"[：:]")
_JOKE_ALIASES = re.compile(r"[/／、|]")


def parse_inside_jokes(text: str) -> Dict[str, List[str]]:
    """老梗栏按行拆成触发词：冒号前用 / 分开的是梗与别名（第一个是主词），没有冒号的整行当主词。"""
    jokes: Dict[str, List[str]] = {}
    for raw in (text or "").splitlines():
        line = raw.strip().lstrip("-•*").strip()
        if not line:
            continue
        head = _JOKE_HEAD.split(line, maxsplit=1)[0]
        words = [word.strip() for word in _JOKE_ALIASES.split(head) if word.strip()]
        if words and words[0] not in jokes:
            jokes[words[0]] = words
    return jokes


@dataclass(frozen=True)
class CueText:
    """规则一路看的一段文本：观众弹幕（带弹幕编号）或她最近播出的一句台词。"""

    source: str
    text: str
    message_id: str = ""


@dataclass
class PassiveHit:
    """一条被想起的候选：条目、命中它的全部来源与依据。

    ``message_id`` 是第一条命中它的观众弹幕，只给卡面点名用，不进线索卡记录（记录不带观众身份）。
    """

    key: ItemKey
    sources: Set[str] = field(default_factory=set)
    keywords: List[str] = field(default_factory=list)
    hows: List[str] = field(default_factory=list)
    message_id: str = ""
    place: str = ""
    distance: Optional[int] = None

    @property
    def self_only(self) -> bool:
        """只由她自己的台词命中（防打转：推送侧对它更严）。"""
        return self.sources == {SOURCE_SELF}


class RulePool:
    """规则与地点两路的候选池：档案行、老梗与它们的触发词匹配器。"""

    def __init__(self, *, archive_repo: Optional["ArchiveRepo"], inside_jokes: str, clock: Callable[[], int]) -> None:
        self._archive = archive_repo
        self._jokes = parse_inside_jokes(inside_jokes)
        self._clock = clock
        self._loaded_at: Optional[int] = None
        self._lines: Dict[int, ArchiveLine] = {}
        self._matcher: KeywordMatcher[ItemKey] = KeywordMatcher({})

    def reset(self) -> None:
        """换场时作废，下一次用时重读。"""
        self._loaded_at = None

    async def refresh_if_stale(self) -> None:
        """这场还没读过、或上次读已过十分钟，就重读候选池；读失败沿用上一份。"""
        now = int(self._clock())
        if self._loaded_at is not None and now - self._loaded_at < POOL_REFRESH_MS:
            return
        self._loaded_at = now
        try:
            lines = await self._archive.list_cue_lines(limit=POOL_MAX_LINES) if self._archive is not None else []
        except Exception as exc:  # noqa: BLE001 - 读不到候选池就沿用上一份，不挡决策
            logger.warning(f"读被动想起的候选池失败（沿用上一份）: {exc}", exc=exc)
            return
        self._lines = {int(line.id): line for line in lines}
        entries: Dict[ItemKey, List[str]] = {}
        for line in self._lines.values():
            if line.keywords:
                entries[("archive", int(line.id))] = list(line.keywords)
        for main, words in self._jokes.items():
            entries[("joke", main)] = words
        self._matcher = KeywordMatcher(entries)

    def archive_line(self, line_id: int) -> Optional[ArchiveLine]:
        return self._lines.get(int(line_id))

    def lines_at(self, game: str, place: str, current_saves: Mapping[str, GameSave]) -> List[ArchiveLine]:
        """挂在这个游戏、这个地点上的档案行（新的在前）：只取眼下这个存档里的，或这个游戏不分存档。"""
        return [
            line
            for line in self._lines.values()
            if line.place == place
            and line.game == game
            and place_of(line.game, line.save_id, current_saves) in (SavePlace.SAME, SavePlace.UNSPLIT)
        ][:LINES_PER_PLACE]

    def match(self, texts: Sequence[CueText]) -> List[PassiveHit]:
        """逐段文本匹配触发词，同一条目合并成一条候选；老梗只收观众弹幕命中的。"""
        hits: Dict[ItemKey, PassiveHit] = {}
        for item in texts:
            for found in self._matcher.match(item.text):
                if found.key[0] == "joke" and item.source != SOURCE_DANMAKU:
                    continue
                hit = hits.setdefault(found.key, PassiveHit(key=found.key))
                hit.sources.add(item.source)
                if found.keyword not in hit.keywords:
                    hit.keywords.append(found.keyword)
                if found.how not in hit.hows:
                    hit.hows.append(found.how)
                if item.source == SOURCE_DANMAKU and not hit.message_id:
                    hit.message_id = item.message_id
        # 观众弹幕命中的排在只由她自己台词命中的前面
        return sorted(hits.values(), key=lambda hit: hit.self_only)


class PlaceArrivals:
    """地点到达判定：每个（游戏, 存档, 地点）记上一次读到的距离，本场到过的地点不再算。"""

    def __init__(self) -> None:
        self._last: Dict[Tuple[str, str, str], int] = {}
        self._arrived: Set[Tuple[str, str, str]] = set()

    def reset(self) -> None:
        self._last.clear()
        self._arrived.clear()

    def update(
        self, situations: Mapping[str, GameSituation], current_saves: Mapping[str, GameSave]
    ) -> List[Tuple[str, str, int]]:
        """读一次各游戏的情境，返回这一批刚到达的（游戏, 地点, 距离）。

        距离太旧的游戏这一批不判（也不更新记下的距离）；没出现在这次读数里的地点当"没读到"。
        """
        arrivals: List[Tuple[str, str, int]] = []
        for game, situation in situations.items():
            if situation.age_ms > SITUATION_STALE_MS:
                continue
            save = current_saves.get(game)
            save_id = save.id if save is not None and save.known else ""
            seen: Dict[Tuple[str, str, str], int] = {}
            for place in situation.places:
                key = (game, save_id, place.name)
                seen[key] = int(place.distance)
                before = self._last.get(key)
                came_in = place.distance <= ARRIVE_WITHIN and (before is None or before > LEAVE_BEYOND)
                if came_in and key not in self._arrived:
                    self._arrived.add(key)
                    arrivals.append((game, place.name, int(place.distance)))
            for key in [key for key in self._last if key[0] == game and key not in seen]:
                del self._last[key]
            self._last.update(seen)
        return arrivals
