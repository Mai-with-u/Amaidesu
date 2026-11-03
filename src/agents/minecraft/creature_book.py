"""生物名册：角色自己的生物、主播起过名的生物，各是谁、叫什么、在不在身边、死没死。

为什么要有它：速览是一次性快照，只能说"此刻身边有什么"。直播里的同伴（跟着的女仆、狗）是
贯穿整场的角色，主播会给她起名、会关心她去哪了；她死在身边时，游戏只发一条只给主人的死亡播报，
然后实体从观察里消失、原地多出一块墓碑。没有一份跨快照的名册，就没人把"阿缕"、"莫莫酒狐"、
"那块墓碑"和"刚才跟着我的那只女仆"连成同一只——复盘里主播因此对着墓碑问"是你熟人吗"。

谁进名册：驯服关系是"你的"的生物第一次出现在观察里就自动建档；别的生物只有主播起了名才建档，
路人不进。每只按身份键记一条：有 UUID 用 UUID（跨重进世界不变）；Mod 还没报 UUID 时，用
"种类 + 皮肤 + 命名牌名"临时拼一个键（同皮肤的两只会被当成一只，归因时按"认不准就不认"处理）。

状态只在三处变：每次观察（在身边 / 在附近 / 没看到，看到写着它名字的墓碑就确认它死了）、一条认得出
是它的死亡播报、主播起名。离开视野只改状态不发事件：女仆被设成驻守、走远、区块卸载都会这样，
不能当成走了；死亡是唯一要当场讲给主播的变化，由调用方据 ``note_death`` 的返回发游戏进展事件。

跨场次记得：名册按世界分开落库（世界编号由游戏报出），进一个世界时整份读回，之后只把变了的几只交给
调用方写回（``take_changes``）。死了的只在死的那一场和之后的第一场里提起，再往后只留在名册里占着名字，
免得"阿缕"被安到别的生物身上；场次由调用方在开播、下播时告知。

本模块是 Minecraft Agent 的内部件（代码直连，不是工具），只在 Agent 的事件循环里用，非线程安全。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.modules.storage.models import GameCreatureRecord

from .glance import (
    NEARBY_BLOCKS,
    OWNER_SELF,
    STATUS_AROUND,
    STATUS_AWAY,
    STATUS_DEAD,
    STATUS_NEARBY,
    described_entity,
    entity_label,
    entity_things,
    kind_of,
)

__all__ = ["Creature", "CreatureBook", "NameResult", "creature_from_record", "creature_record", "identity_key"]

# 死亡播报的谓语里会有的字眼（中文语言包的死亡句都以受害者名字开头）。Mod 还没给消息模板编号时，
# 名字对上了还得有这些字眼才认作死讯：车万女仆还会以她的名字开头发别的灰字消息，不能当成死了。
_DEATH_WORDS = re.compile(r"死|杀|亡|炸|击中|摔|落地过猛|焚|熔岩|窒息|淹|冻|饿|掉出|刺|射|烧|撞|压扁|消灭|干掉")

# 起名时 who 里的这些修饰词不参与比对："你的女仆""身边那只狼"说的都是种类本身
_WHO_NOISE = re.compile(r"^(你的|我的|身边的|身边那只|身边这只|那只|这只|那个|这个|那位|这位)|(这只|那只)$")


def _clock(timestamp_ms: int) -> str:
    """毫秒时刻 → 本地 HH:MM；没有时刻返回空串。"""
    if timestamp_ms <= 0:
        return ""
    return datetime.fromtimestamp(timestamp_ms / 1000).strftime("%H:%M")


@dataclass
class Creature:
    """名册里的一只生物。``name`` 是命名牌上的名字，``skin`` 是车万女仆的皮肤名，
    ``nickname`` 是主播起的名字（只在本系统里有效，游戏里看不到）。"""

    key: str
    type: str
    kind: str = ""
    name: str = ""
    skin: str = ""
    owner: str = ""
    nickname: str = ""
    uuid: str = ""
    status: str = STATUS_AWAY
    last_seen_ms: int = 0
    died_ms: int = 0
    death_note: str = ""
    grave_nearby: bool = False
    # 死的那一场与死后开的第一场（场次主键）：已死的只在这两场里提起
    died_session_id: Optional[int] = None
    next_session_id: Optional[int] = None

    @property
    def game_name(self) -> str:
        """游戏里显示的名字：命名牌名，没有用女仆皮肤名，再没有用种类叫法。死亡播报与墓碑上写的就是它。"""
        return self.name or self.skin or self.kind

    def label(self, *, with_nickname: bool = True) -> str:
        """主播叫得出的称呼，与速览里身边实体的写法一致（起过名的名字在前）；``with_nickname=False`` 只说是哪一只。"""
        return entity_label(
            {
                "type": self.type,
                "kind": self.kind,
                "owner": self.owner,
                "name": self.name,
                "skin": self.skin,
                "nickname": self.nickname if with_nickname else "",
            }
        )

    def facts(self) -> Tuple[Any, ...]:
        """要落库的事实（不含最后看到的时刻与此刻在不在身边）：前后不同就说明这只要写回。"""
        return (
            self.kind,
            self.name,
            self.skin,
            self.owner,
            self.nickname,
            self.uuid,
            self.died_ms,
            self.death_note,
            self.died_session_id,
            self.next_session_id,
        )


def creature_record(creature: Creature) -> GameCreatureRecord:
    """名册条目 → 落库的一行（活着的死亡时刻写空）。"""
    return GameCreatureRecord(
        creature_key=creature.key,
        type=creature.type,
        kind=creature.kind,
        name=creature.name,
        skin=creature.skin,
        owner=creature.owner,
        nickname=creature.nickname,
        uuid=creature.uuid,
        last_seen_ms=creature.last_seen_ms,
        died_ms=creature.died_ms or None,
        death_note=creature.death_note,
        died_session_id=creature.died_session_id,
        next_session_id=creature.next_session_id,
    )


def creature_from_record(record: GameCreatureRecord) -> Creature:
    """落库的一行 → 名册条目。此刻在不在身边要等下一次观察：活着的先记没看到。"""
    return Creature(
        key=record.creature_key,
        type=record.type,
        kind=record.kind,
        name=record.name,
        skin=record.skin,
        owner=record.owner,
        nickname=record.nickname,
        uuid=record.uuid,
        status=STATUS_DEAD if record.died_ms else STATUS_AWAY,
        last_seen_ms=record.last_seen_ms,
        died_ms=record.died_ms or 0,
        death_note=record.death_note,
        died_session_id=record.died_session_id,
        next_session_id=record.next_session_id,
    )


@dataclass(frozen=True)
class NameResult:
    """起名的结果：登记了就带上那一只；没登记写清原因（对不上、分不清是哪只、名字被占了）。"""

    named: Optional[Creature] = None
    reason: str = ""


@dataclass(frozen=True)
class _Sighting:
    """最近一次观察里的一只：观察编号、身份键、上游原条目、给主播看的条目。"""

    scene_id: str
    key: str
    entity: Dict[str, Any]
    described: Dict[str, Any]


def identity_key(entity: Dict[str, Any], thing: Dict[str, Any]) -> str:
    """一只实体的身份键：有 UUID 用 UUID；没有时用"种类 + 皮肤 + 命名牌名"临时拼。"""
    uuid = str(entity.get("uuid") or "")
    if uuid:
        return f"uuid:{uuid}"
    skin = str(thing.get("skin_id") or "")
    return f"look:{entity.get('type') or ''}|{skin}|{entity.get('name') or ''}"


class CreatureBook:
    """生物名册：内存里是当前世界的一份，落库由调用方按 ``take_changes`` 写回。"""

    def __init__(self) -> None:
        # 身份键 → 名册条目，按建档先后
        self._creatures: Dict[str, Creature] = {}
        # 最近一次观察里看到的生物（起名找对象用）与身边的墓碑（观察编号 → 上面写的名字）
        self._sightings: List[_Sighting] = []
        self._graves: Dict[str, str] = {}
        self._observed_ms = 0
        # 名册属于哪个世界：None = 还没认出世界、没读过落库的名册；空串 = 游戏没报世界编号
        self._world_key: Optional[str] = None
        # 当前场次主键（不在场次里为 None）：死讯记在哪一场、已死的提到哪一场为止
        self._session_id: Optional[int] = None
        # 上次写回之后变了的条目、被移出名册的条目（身份键）
        self._changed: set[str] = set()
        self._removed: set[str] = set()

    # ==================== 世界与场次 ====================

    @property
    def world_key(self) -> Optional[str]:
        """名册属于哪个世界；还没认出世界为 None。"""
        return self._world_key

    def load(self, world_key: str, creatures: Iterable[Creature]) -> None:
        """换到一个世界：换上这个世界落库的名册，忘掉上一个世界的观察（编号在新世界里不算数）。

        顺序：调用方先把旧世界的变化写回 → 读出新世界的名册 → 交给这里；
        读回的已死条目按当前场次推算还提不提。
        """
        self._world_key = world_key
        self._creatures = {creature.key: creature for creature in creatures}
        self._sightings = []
        self._graves = {}
        self._changed.clear()
        self._removed.clear()
        self._age_deaths()

    def start_session(self, session_id: int) -> None:
        """开播：死在之前场次的，这一场是它死后的第一场就记下，再往后的场次不再提。"""
        self._session_id = session_id
        self._age_deaths()

    def end_session(self) -> None:
        """下播：之后的观察不属于任何场次。"""
        self._session_id = None

    def _age_deaths(self) -> None:
        """已死、还没记"死后第一场"的，当前场次不是它死的那一场，就把当前场次记成死后第一场。"""
        if self._session_id is None:
            return
        for creature in self._creatures.values():
            if (
                creature.status == STATUS_DEAD
                and creature.next_session_id is None
                and creature.died_session_id != self._session_id
            ):
                creature.next_session_id = self._session_id
                self._changed.add(creature.key)

    def _is_past(self, creature: Creature) -> bool:
        """死在两场以前的：只留在名册里占着名字，速览与游戏 Agent 的上下文里都不再提。"""
        if creature.status != STATUS_DEAD or creature.next_session_id is None:
            return False
        return self._session_id not in (creature.died_session_id, creature.next_session_id)

    def take_changes(self) -> Tuple[List[Creature], List[str]]:
        """取走上次写回之后的变化：变了的条目，被移出名册的身份键。取走即清空。"""
        changed = [self._creatures[key] for key in self._changed if key in self._creatures]
        removed = [key for key in self._removed if key not in self._creatures]
        self._changed.clear()
        self._removed.clear()
        return changed, removed

    # ==================== 观察 ====================

    def observe(self, scene: Dict[str, Any], now_ms: int) -> None:
        """按一次 ``observe(scene)`` 更新名册：自己的生物建档，看到的标在身边或在附近，
        这次没看到的标没看到，身边有写着它名字的墓碑就确认它死了。"""
        things = entity_things(scene.get("mod_things"))
        seen: set[str] = set()
        sightings: List[_Sighting] = []
        graves: Dict[str, str] = {}
        for entity in scene.get("entities") or []:
            if not isinstance(entity, dict) or not entity.get("type"):
                continue
            scene_id = str(entity.get("id") or "")
            described = described_entity(entity, things)
            if described.get("grave_of"):
                if _close(entity):
                    graves[scene_id] = str(described["grave_of"])
                continue
            key = identity_key(entity, things.get(scene_id, {}))
            creature = self._creatures.get(key)
            if creature is None and described.get("owner") == OWNER_SELF:
                creature = self._creatures[key] = Creature(key=key, type=str(entity["type"]))
                self._changed.add(key)
            if creature is not None:
                self._refresh(creature, entity, described, now_ms)
                seen.add(key)
            sightings.append(_Sighting(scene_id=scene_id, key=key, entity=entity, described=described))
        for creature in self._creatures.values():
            creature.grave_nearby = False
            if creature.key not in seen and creature.status in (STATUS_NEARBY, STATUS_AROUND):
                # 刚离开视野：最后看到的时刻要写回，重启后还说得出"几点以后没见到"
                creature.status = STATUS_AWAY
                self._changed.add(creature.key)
        for grave_of in graves.values():
            owner = self.grave_owner(grave_of)
            if owner is None:
                continue
            if owner.status != STATUS_DEAD:
                self._mark_dead(owner, now_ms, "")
            owner.grave_nearby = True
        self._sightings = sightings
        self._graves = graves
        self._observed_ms = now_ms

    def _refresh(self, creature: Creature, entity: Dict[str, Any], described: Dict[str, Any], now_ms: int) -> None:
        """这次看到了它：更新认得出它的事实与在场状态；记过死了又看到（认错了或复活了）就当活着。"""
        before = creature.facts()
        creature.kind = kind_of(described)
        creature.name = str(entity.get("name") or "")
        creature.skin = str(described.get("skin") or creature.skin)
        creature.owner = str(described.get("owner") or creature.owner)
        creature.uuid = str(entity.get("uuid") or creature.uuid)
        creature.status = STATUS_NEARBY if _close(entity) else STATUS_AROUND
        creature.last_seen_ms = now_ms
        creature.died_ms = 0
        creature.death_note = ""
        creature.died_session_id = None
        creature.next_session_id = None
        if creature.facts() != before:
            self._changed.add(creature.key)

    def _mark_dead(self, creature: Creature, now_ms: int, note: str) -> None:
        """记下它死了：死亡时刻与场次只记第一次（墓碑先看到、死讯后到也是同一次死），死因有就补上。"""
        if creature.status != STATUS_DEAD:
            creature.status = STATUS_DEAD
            creature.died_ms = now_ms
            creature.died_session_id = self._session_id
            creature.next_session_id = None
        creature.death_note = note or creature.death_note
        self._changed.add(creature.key)

    def grave_owner(self, grave_of: str) -> Optional[Creature]:
        """墓碑上写的名字 → 名册里那只；名册里不止一只叫这个名字时认不准，返回 None。"""
        return self._only_one_called(grave_of)

    def _only_one_called(self, game_name: str) -> Optional[Creature]:
        """名册里游戏名是这个的那一只；没有或不止一只（同皮肤、同种类没起名）都返回 None。"""
        matches = [creature for creature in self._creatures.values() if creature.game_name == game_name]
        return matches[0] if len(matches) == 1 else None

    def nicknames_by_scene_id(self) -> Dict[str, str]:
        """最近一次观察里各实体的称呼补充：观察编号 → 主播起的名字（墓碑按上面的名字对到死者）。"""
        names: Dict[str, str] = {}
        for sighting in self._sightings:
            creature = self._creatures.get(sighting.key)
            if creature is not None and creature.nickname:
                names[sighting.scene_id] = creature.nickname
        for scene_id, grave_of in self._graves.items():
            owner = self.grave_owner(grave_of)
            if owner is not None and owner.nickname:
                names[scene_id] = owner.nickname
        return names

    # ==================== 死讯 ====================

    def note_death(self, message: str, now_ms: int) -> Optional[Creature]:
        """一条系统消息若是名册里某只的死讯，记下它死了并返回它；认不准或已经记过就返回 None。

        Mod 还没给结构化的死讯身份时按文字认：消息以名册里某只的游戏名开头、后面是死亡谓语，
        且名册里只有一只叫这个名字（同皮肤的两只分不清，宁可不认也不认错）。
        """
        text = message.strip()
        for creature in self._creatures.values():
            name = creature.game_name
            if not name or not text.startswith(name):
                continue
            rest = text[len(name) :].strip()
            if not rest or not _DEATH_WORDS.search(rest):
                continue
            if self._only_one_called(name) is None:
                return None
            if creature.status == STATUS_DEAD and creature.death_note:
                return None
            self._mark_dead(creature, now_ms, rest)
            return creature
        return None

    # ==================== 起名 ====================

    def name(self, who: str, nickname: str) -> NameResult:
        """按 who（速览里的称呼）在最近一次观察看到的生物里找到一只，登记主播起的名字。

        找的顺序：主播起过的名字、命名牌名、皮肤名、种类；几只都对得上时先挑自己的，仍不止一只就不登记，
        列出区分它们的称呼与方位。``nickname`` 给空串表示不再这么叫。名字已经属于名册里另一只时不登记。
        """
        nickname = nickname.strip()
        candidates = self._match(who)
        if not candidates:
            return NameResult(reason=f"身边没看到对得上「{who}」的生物；先看一眼现场，用速览里的称呼再说一次")
        own = [sighting for sighting in candidates if sighting.described.get("owner") == OWNER_SELF]
        if len(candidates) > 1 and len(own) == 1:
            candidates = own
        if len(candidates) > 1:
            listed = "；".join(self._distinguish(sighting) for sighting in candidates[:5])
            return NameResult(reason=f"身边有 {len(candidates)} 只对得上「{who}」，分不清是哪一只：{listed}")
        sighting = candidates[0]
        holder = next(
            (c for c in self._creatures.values() if nickname and c.nickname == nickname and c.key != sighting.key),
            None,
        )
        if holder is not None and holder.status == STATUS_DEAD:
            # 死了的同伴名字仍归它：换给新来的会让回顾和老观众把两只混成一只
            return NameResult(reason=f"「{nickname}」是{holder.label()}的名字，它已经死了，名字留给它，换一个名字")
        if holder is not None:
            return NameResult(reason=f"「{nickname}」已经是{holder.label()}的名字了，换一个名字")
        creature = self._creatures.get(sighting.key)
        if creature is None:
            if not nickname:
                return NameResult(reason="它本来就没有主播起的名字")
            creature = self._creatures[sighting.key] = Creature(key=sighting.key, type=str(sighting.entity["type"]))
            self._refresh(creature, sighting.entity, sighting.described, self._observed_ms)
        creature.nickname = nickname
        self._changed.add(creature.key)
        if not nickname and creature.owner != OWNER_SELF:
            # 路人只因起了名才进名册：名字取消了就不再记它，落库的那一行也删掉
            del self._creatures[creature.key]
            self._removed.add(creature.key)
        return NameResult(named=creature)

    def _match(self, who: str) -> List[_Sighting]:
        """who → 最近一次观察里对得上的生物（近的在前）。who 为空时指自己的生物。"""
        target = _WHO_NOISE.sub("", who.strip()).strip()
        if not target:
            return [s for s in self._sightings if s.described.get("owner") == OWNER_SELF]
        for value_of in (self._nickname_of, self._name_of, self._skin_of):
            exact = [s for s in self._sightings if value_of(s) == target]
            if exact:
                return exact
        return [s for s in self._sightings if target in self._kind_words(s)]

    def _nickname_of(self, sighting: _Sighting) -> str:
        creature = self._creatures.get(sighting.key)
        return creature.nickname if creature is not None else ""

    @staticmethod
    def _name_of(sighting: _Sighting) -> str:
        return str(sighting.described.get("name") or "")

    @staticmethod
    def _skin_of(sighting: _Sighting) -> str:
        return str(sighting.described.get("skin") or "")

    @staticmethod
    def _kind_words(sighting: _Sighting) -> str:
        """种类的几种说法拼在一起：叫法（女仆）、去掉命名空间的种类 ID、完整称呼。"""
        described = sighting.described
        return " ".join([kind_of(described), str(described.get("type") or "").split(":")[-1], entity_label(described)])

    def _distinguish(self, sighting: _Sighting) -> str:
        """分不清时列出一只：称呼加方位。"""
        described = dict(sighting.described)
        nickname = self._nickname_of(sighting)
        if nickname:
            described["nickname"] = nickname
        where = f"在{described['direction']}" if described.get("direction") else ""
        return f"{entity_label(described)}{where}"

    # ==================== 给主播看 ====================

    def status_view(self) -> List[Dict[str, Any]]:
        """名册里各只此刻的状态，给速览用：称呼、状态、时刻、死因、墓碑在不在身边。

        起过名的全列；没起名的自己的生物只列在场与死了的（家里一圈马不该每次都报"没看到"）；
        死在两场以前的不再列。
        """
        view: List[Dict[str, Any]] = []
        for creature in self._creatures.values():
            if (not creature.nickname and creature.status == STATUS_AWAY) or self._is_past(creature):
                continue
            item: Dict[str, Any] = {"who": creature.label(), "status": creature.status}
            if creature.status == STATUS_DEAD:
                item["since"] = _clock(creature.died_ms)
                if creature.death_note:
                    item["note"] = creature.death_note
                if creature.grave_nearby:
                    item["grave_nearby"] = True
            elif creature.status == STATUS_AWAY:
                item["since"] = _clock(creature.last_seen_ms)
            if creature.nickname:
                item["named"] = True
            view.append(item)
        return view

    def named_view(self) -> Dict[str, str]:
        """主播起过的名字 → 是哪一只、此刻怎样。给游戏 Agent：主播用名字下指令时（"带上阿缕"）它认得出是谁。

        死在两场以前的不再列：游戏 Agent 用不上，名字仍由名册占着。
        """
        view: Dict[str, str] = {}
        for creature in self._creatures.values():
            if not creature.nickname or self._is_past(creature):
                continue
            state = creature.status
            if creature.status == STATUS_DEAD and creature.death_note:
                state = f"{state}：{creature.death_note}"
            view[creature.nickname] = f"{creature.label(with_nickname=False)}，{state}"
        return view

    def creatures(self) -> List[Creature]:
        """名册全部条目（只读快照：测试、落库与游戏 Agent 上下文用）。"""
        return list(self._creatures.values())


def _close(entity: Dict[str, Any]) -> bool:
    """这只在不在身边范围内。"""
    distance = entity.get("distance")
    return isinstance(distance, (int, float)) and distance <= NEARBY_BLOCKS
