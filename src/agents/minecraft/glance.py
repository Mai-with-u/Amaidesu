"""主播"看一眼游戏"的精简视图：从 ``observe(self)`` 与 ``observe(scene)`` 里取出直播叙事用得上的事实。

主播的职责是把身体在做什么、遇到了什么讲给观众听，不替身体做工程判断。观察里的观察编号、
相对方位、俯视网格是给游戏 Agent 规划用的，交给主播只会让它去猜坐标。这里只保留：身体状态、
背包物品与数量、身边的生物与设施（近处的才算"身边"）、远处的生物（只按种类报只数）、时段天气。
上游没给的字段不写（缺失是"未知"，不是"没有"），读取失败照实写出原因。

身边的生物带上认得出它是谁的事实：驯服的是谁的、坐着没有、命名牌上的名字，以及模组报的身份
（车万女仆的皮肤名、女仆墓碑上写的名字）。主播靠这些才分得清"跟着我的那只女仆"和路过的生物，
看到墓碑也知道是谁的。观察编号只用来把模组的这些信息并到同一只实体上，不交给主播。

另附一行记住的地点（``observe(world_memory)`` 里的地点名，只列几个、不带坐标）：被问到"传送门在哪"时，
主播得先知道记没记过，才分得清是说"记过，我去看看"还是"还没记"；坐标与怎么去交给游戏 Agent 查。
这一行不进一句话现场——嘴只讲眼前的事，不翻记忆。
"""

from __future__ import annotations

import math
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.modules.types.game_situation import NearbyPlace

from .attention_matrix import biome_name, item_name
from .maicraft import MaicraftReply

__all__ = [
    "NEARBY_BLOCKS",
    "OWNER_SELF",
    "described_entity",
    "STATUS_AROUND",
    "STATUS_AWAY",
    "STATUS_DEAD",
    "STATUS_NEARBY",
    "entity_label",
    "entity_things",
    "kind_of",
    "glance_brief",
    "glance_scene",
    "glance_self",
    "glance_world_memory",
    "nearby_player_names",
    "REMEMBERED_PLACES_MAX",
    "REMEMBERED_PLACES_TTL_S",
    "RememberedPlacesCache",
]

# 直播里说"身边"的范围：再远的生物与设施观众在画面里也看不清，主播提起来只会让人困惑。
NEARBY_BLOCKS = 24

# 一句话现场的长度上限：它要塞进主播的现场卡，卡总共只有几百字
BRIEF_MAX_CHARS = 300

# 速览里最多列几个记住的地点：主播只需要知道"记过哪些"，速览每轮都进她的参考段，列全了会越攒越长
REMEMBERED_PLACES_MAX = 6

# 记住的地点最多隔多久真读一次（秒）：Mod 读世界记忆要在游戏客户端线程上把几千条记录排序、拼成一大段，
# 速览却十几秒就要一次、只用得上几个地点名；记地点、换存档、重连时另有作废，不靠这个时长兜
REMEMBERED_PLACES_TTL_S = 300.0

_DIMENSIONS: Dict[str, str] = {
    "minecraft:overworld": "主世界",
    "minecraft:the_nether": "下界",
    "minecraft:the_end": "末地",
}

#: 常见生物的中文名（模组生物不在表里，原样去掉命名空间交给主播念）
_MOBS: Dict[str, str] = {
    "zombie": "僵尸",
    "husk": "尸壳",
    "drowned": "溺尸",
    "skeleton": "骷髅",
    "stray": "流浪者",
    "creeper": "苦力怕",
    "spider": "蜘蛛",
    "cave_spider": "洞穴蜘蛛",
    "enderman": "末影人",
    "witch": "女巫",
    "slime": "史莱姆",
    "phantom": "幻翼",
    "pillager": "掠夺者",
    "vindicator": "卫道士",
    "cow": "牛",
    "pig": "猪",
    "sheep": "羊",
    "chicken": "鸡",
    "horse": "马",
    "wolf": "狼",
    "cat": "猫",
    "fox": "狐狸",
    "bee": "蜜蜂",
    "goat": "山羊",
    "rabbit": "兔子",
    "villager": "村民",
    "iron_golem": "铁傀儡",
    "squid": "鱿鱼",
    "glow_squid": "发光鱿鱼",
    "cod": "鳕鱼",
    "salmon": "鲑鱼",
    "axolotl": "美西螈",
    "bat": "蝙蝠",
    "player": "玩家",
}

#: 常见设施方块的中文名
_BLOCKS: Dict[str, str] = {
    "chest": "箱子",
    "barrel": "木桶",
    "crafting_table": "工作台",
    "furnace": "熔炉",
    "blast_furnace": "高炉",
    "smoker": "烟熏炉",
    "anvil": "铁砧",
    "enchanting_table": "附魔台",
    "brewing_stand": "酿造台",
    "smithing_table": "锻造台",
}

#: 模组报的东西（``observe(scene)`` 的 ``mod_things`` 分类）里和实体同号的那几类：分类 → 主播的叫法
_ENTITY_THING_KINDS: Dict[str, str] = {
    "maid": "女仆",
    "maid_tombstone": "女仆墓碑",
}

#: 实体特征里与"它是谁"有关的两项：驯服关系（你的 / 自家人的 / 别人的）、听令坐下了没有
_TRAIT_OWNER = "驯服"
_TRAIT_SITTING = "坐着"
#: 驯服关系里表示"是角色自己的"那个说法
OWNER_SELF = "你的"
#: 驯服关系里表示"不是自家人的"那个说法：知道驯服者时直接写成"谁的"
_OWNER_OTHERS = "别人的"
#: 实体特征里的驯服者：只在"自家人的""别人的"时给，玩家在线写玩家名，不在线或还没同步时写 UUID
_TRAIT_TAMER = "驯服者"
#: 玩家 UUID 的常见写法（8-4-4-4-12 位十六进制）：UUID 是给模型认人用的，主播看到会照着念出来
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")

#: 生物名册里一只生物此刻的状态：在身边（近处）、在附近（列出来了但不在身边范围内）、这次没看到、死了
STATUS_NEARBY = "在身边"
STATUS_AROUND = "在附近"
STATUS_AWAY = "没看到"
STATUS_DEAD = "已死"


def _mob_name(raw: str) -> str:
    plain = item_name(raw)
    return _MOBS.get(plain, plain)


def _block_name(raw: str) -> str:
    plain = item_name(raw)
    if plain.endswith("_bed"):
        return "床"
    return _BLOCKS.get(plain, plain)


def _health_words(health: Any) -> str:
    """血量（满 20）→ 白话；不知道就不说。"""
    if not isinstance(health, (int, float)) or isinstance(health, bool):
        return ""
    if health >= 18:
        return "血是满的"
    if health >= 12:
        return "血还行"
    return "掉了不少血" if health >= 6 else "血快没了"


def _food_words(food: Any) -> str:
    """饥饿值（满 20）→ 白话；不知道就不说。"""
    if not isinstance(food, (int, float)) or isinstance(food, bool):
        return ""
    if food >= 17:
        return "不饿"
    return "有点饿" if food >= 10 else "很饿"


def kind_of(entity: Dict[str, Any]) -> str:
    """实体的种类叫法：模组报的叫法（女仆、女仆墓碑）优先，其次是常见生物的中文名，再不行是去掉原版命名空间的 ID。"""
    return str(entity.get("kind") or _mob_name(str(entity.get("type") or "")))


def _owner_words(owner: str, tamer: str) -> str:
    """驯服关系的说法：知道是哪个玩家驯服的，就把名字写进去。

    "别人的" → "玩家甲的"；"自家人的" → "自家人玩家甲的"（自家人这层留着）；"你的"和不知道驯服者的照旧。
    """
    if not tamer or not owner or owner == OWNER_SELF:
        return owner
    if owner == _OWNER_OTHERS:
        return f"{tamer}的"
    return f"{owner.removesuffix('的')}{tamer}的"


def entity_label(entity: Dict[str, Any]) -> str:
    """一只身边的实体 → 主播叫得出的称呼：主播起的名字在前，括号里是认出它的事实。

    例："阿缕（你的女仆，莫莫酒狐皮肤，坐着）""你的狼（叫小白）""女仆墓碑（上面写着莫莫酒狐）"。
    驯服关系写成"你的 / 自家人的 / 别人的"放在种类前面，知道驯服者名字时写成"玩家甲的狼""自家人玩家甲的女仆"，
    主播在游戏里说"他的狗""我家女仆"时接得上；没起名时种类就是称呼本身。
    墓碑带的名字是死者的："阿缕的女仆墓碑（上面写着莫莫酒狐）"。
    """
    kind = kind_of(entity)
    owner = _owner_words(str(entity.get("owner") or ""), str(entity.get("tamer") or ""))
    whose = f"{owner}{kind}" if owner else kind
    facts: List[str] = []
    if entity.get("name"):
        facts.append(f"叫{entity['name']}")
    if entity.get("skin"):
        facts.append(f"{entity['skin']}皮肤")
    if entity.get("grave_of"):
        facts.append(f"上面写着{entity['grave_of']}")
    if entity.get("sitting"):
        facts.append("坐着")
    nickname = str(entity.get("nickname") or "")
    if nickname and entity.get("grave_of"):
        # 墓碑上的名字是死者的：称呼写成"谁的墓碑"，不把墓碑叫成她
        return f"{nickname}的{whose}（{'，'.join(facts)}）"
    if nickname:
        # 起过名的：名字当称呼，"你的女仆"退到括号里当说明
        return f"{nickname}（{'，'.join([whose, *facts])}）"
    return f"{whose}（{'，'.join(facts)}）" if facts else whose


def _list_words(names: List[str], limit: int) -> str:
    """去重后取前几个，用顿号连起来；多出来的说"等"。"""
    unique = list(dict.fromkeys(name for name in names if name))
    head = "、".join(unique[:limit])
    return f"{head}等" if len(unique) > limit else head


def glance_brief(view: Dict[str, Any]) -> str:
    """一句话现场：把速览里的事实写成主播一眼读得懂的白话，不带数字、坐标和观察编号。

    主播的现场卡只放这一句（几千字的速览原文塞不下，也会被逐字念出来）。物品与模组生物认不出中文名时
    保留 ID 去掉原版命名空间，主播说话时自己换成叫法；上游没给的事就不写，不猜。
    """
    if view.get("unavailable"):
        return str(view["unavailable"])
    sentences: List[str] = []
    body = view.get("body") if isinstance(view.get("body"), dict) else {}
    where = _DIMENSIONS.get(str(body.get("dimension") or ""), "")
    biome = biome_name(str(view["biome"])) if view.get("biome") else ""
    place = f"在{where}的{biome}" if where and biome else (f"在{where or biome}" if where or biome else "")
    if body.get("in_water"):
        place = f"{place}，泡在水里" if place else "泡在水里"
    if body.get("air"):
        place = f"{place}，正在憋气" if place else "正在憋气"
    if place:
        sentences.append(place)
    sky = "，".join(str(view[key]) for key in ("time", "weather") if view.get(key))
    if sky:
        sentences.append(sky)
    state = "，".join(words for words in (_health_words(body.get("health")), _food_words(body.get("food"))) if words)
    if state:
        sentences.append(state)
    if body.get("held"):
        sentences.append(f"手上拿着{item_name(str(body['held']))}")
    if body.get("control"):
        sentences.append(str(body["control"]))
    inventory = [str(entry).split(" ×", 1)[0] for entry in view.get("inventory") or []]
    if inventory:
        sentences.append("背包里有" + _list_words([item_name(item) for item in inventory], 5))
    entities = [entity for entity in view.get("entities") or [] if isinstance(entity, dict) and entity.get("type")]
    hostile = [entity for entity in entities if entity.get("hostile") is True]
    if hostile:
        nearest = hostile[0]
        where_from = f"在{nearest['direction']}" if nearest.get("direction") else ""
        sentences.append(f"身边有{_mob_name(str(nearest['type']))}{where_from}，是敌对的")
    # 自己的生物单独成句、排在路人前面：跟着我的女仆、狼是直播里的角色，不能被"等"字吞掉
    own = [entity for entity in entities if entity.get("hostile") is not True and entity.get("owner") == OWNER_SELF]
    if own:
        sentences.append(_list_words([entity_label(entity) for entity in own], 4) + "在身边")
    sentences.extend(_creature_sentences(view.get("creatures")))
    friendly = [
        entity_label(entity)
        for entity in entities
        if entity.get("hostile") is not True and entity.get("owner") != OWNER_SELF
    ]
    if friendly:
        sentences.append("身边还有" + _list_words(friendly, 4))
    facilities = [
        _block_name(str(item["block"]))
        for item in view.get("facilities") or []
        if isinstance(item, dict) and item.get("block")
    ]
    if facilities:
        sentences.append("附近有" + _list_words(facilities, 4))
    distant = [
        _mob_name(str(entry).split(" ×", 1)[0].split("（", 1)[0]) for entry in view.get("distant_entities") or []
    ]
    if distant:
        sentences.append("远处还有" + _list_words(distant, 3))
    work = view.get("work") if isinstance(view.get("work"), dict) else {}
    goals = [goal for goal in work.get("goals") or [] if isinstance(goal, dict) and goal.get("purpose")]
    if goals:
        doing = f"，眼下{goals[0]['doing']}" if goals[0].get("doing") else ""
        sentences.append(f"手上在{goals[0]['purpose']}{doing}")
    elif work.get("state"):
        sentences.append(f"手上：{work['state']}")
    brief = ""
    for sentence in sentences:
        if len(brief) + len(sentence) + 1 > BRIEF_MAX_CHARS:
            break
        brief += sentence + "。"
    return brief


def _creature_sentences(creatures: Any) -> List[str]:
    """生物名册里不在身边的那几只怎样了：死了的写死因与墓碑，起过名的写没在身边或在附近。

    在身边的已经在"在身边"那句里，这里不重复；没起名、没看到的自己的生物不提（家里一圈马不该每次都报）。
    """
    sentences: List[str] = []
    for item in creatures or []:
        if not isinstance(item, dict) or not item.get("who"):
            continue
        who, status = str(item["who"]), item.get("status")
        if status == STATUS_DEAD:
            grave = "，墓碑就在身边" if item.get("grave_nearby") else ""
            sentences.append(f"{who}{item.get('note') or '死了'}{grave}")
        elif status == STATUS_AWAY and item.get("named"):
            sentences.append(f"{who}这会儿没在身边")
        elif status == STATUS_AROUND and item.get("named"):
            sentences.append(f"{who}在附近")
    return sentences


def _failure(reply: MaicraftReply) -> str:
    return reply.error_message or reply.error_code or "读取失败"


def glance_self(reply: MaicraftReply) -> Dict[str, Any]:
    """身体现状：维度、整格位置、血量饥饿、手上拿的、护甲与背包物品数量，以及谁在操作角色。"""
    if not reply.ok:
        return {"body_error": _failure(reply)}
    data = reply.data
    body: Dict[str, Any] = {}
    position = data.get("position") if isinstance(data.get("position"), dict) else {}
    if position.get("dimension"):
        body["dimension"] = position["dimension"]
    axes = [position.get(axis) for axis in ("x", "y", "z")]
    if all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in axes):
        # 叙事只需要整格位置，小数点后的站位偏移对观众没有意义
        body["position"] = [int(round(value)) for value in axes]
    for key in ("health", "food", "facing", "held"):
        if data.get(key) is not None:
            body[key] = data[key]
    air, max_air = data.get("air"), data.get("max_air")
    if isinstance(air, int) and isinstance(max_air, int) and air < max_air:
        # 氧气满格时不提，憋气时才是值得讲的事
        body["air"] = f"{air}/{max_air}"
    if data.get("in_water") is True:
        body["in_water"] = True
    if data.get("control") == "player":
        # 角色在真人手上时身体不会自己动，主播要知道这件事才不会以为它卡住了
        body["control"] = "玩家在操作"
    result: Dict[str, Any] = {"body": body}
    if isinstance(data.get("name"), str) and data["name"]:
        # 角色在游戏里的账号名：聊天、死亡播报、玩家列表里出现这个名字，说的就是主播自己，不是另一个玩家
        result["self_account"] = data["name"]
    armor = [item for item in data.get("armor") or [] if isinstance(item, str) and item]
    if armor:
        result["armor"] = armor
    effects = [item for item in data.get("effects") or [] if isinstance(item, str) and item]
    if effects:
        result["effects"] = effects
    inventory = data.get("inventory")
    if isinstance(inventory, list):
        result["inventory"] = [
            f"{item['item']} ×{item.get('count', '?')}"
            for item in inventory
            if isinstance(item, dict) and item.get("item")
        ]
    return result


def nearby_player_names(reply: MaicraftReply) -> Tuple[str, ...]:
    """这次看周围看到的玩家名（按出现先后、去重）；没读到周围时为空。

    主播把游戏情境（委派原话等）发给外部服务前，用它把玩家名换成"玩家"。
    """
    if not reply.ok:
        return ()
    names: List[str] = []
    for entity in reply.data.get("entities") or []:
        if not isinstance(entity, dict) or entity.get("type") != "minecraft:player":
            continue
        name = str(entity.get("name") or "").strip()
        if name and name not in names:
            names.append(name)
    return tuple(names)


def glance_scene(reply: MaicraftReply, nicknames: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """周边：时段天气、生物群系、身边的生物与设施（各带距离和大致方位），远处的生物按种类计数。

    身边的生物带上认出它是谁的事实（驯服关系、坐着、名字、女仆皮肤、墓碑上的名字），见 ``described_entity``；
    ``nicknames`` 是生物名册给的"观察编号 → 主播起的名字"（墓碑对到死者），对上的条目带 ``nickname``。
    """
    if not reply.ok:
        return {"nearby_error": _failure(reply)}
    data = reply.data
    result: Dict[str, Any] = {}
    for key in ("time", "weather", "biome"):
        if data.get(key):
            result[key] = data[key]
    things = entity_things(data.get("mod_things"))
    names = nicknames or {}
    entities = []
    for entity in _close(data.get("entities")):
        described = described_entity(entity, things)
        nickname = names.get(str(entity.get("id") or ""))
        if nickname:
            described = {**described, "nickname": nickname}
        entities.append(described)
    if entities:
        result["entities"] = entities
    distant = _distant(data.get("entities"), data.get("distant_entities"))
    if distant:
        result["distant_entities"] = distant
    facilities = _nearby(data.get("facilities"), ("block", "direction", "distance"))
    if facilities:
        result["facilities"] = facilities
    return result


def glance_world_memory(reply: MaicraftReply, dimension: str = "") -> Dict[str, Any]:
    """记住的地点（``observe(world_memory)`` 的 ``places``）→ 一行地点名，最多 ``REMEMBERED_PLACES_MAX`` 个。

    只给名字：被问到"传送门在哪"时，主播看这一行就知道记没记过；坐标、距离不给她（念出来没意思，
    她也不用自己认路），要具体位置或要过去时，委派游戏 Agent 按地点名去查、去那里。
    Mod 已按水平距离把近的排在前面，但这个距离不分维度（下界里的地点按坐标算也可能"很近"），
    所以先把和角色同一个维度（``dimension``，取自看自己的结果）的挑到前面，各自保持 Mod 给的先后；
    多出来的说"等共 N 处"。一个都没记过、或回复里没有地点清单时不写这一项；读取失败照实写出原因。
    """
    if not reply.ok:
        return {"remembered_places_error": _failure(reply)}
    places = reply.data.get("places")
    if not isinstance(places, list):
        return {}
    named = [item for item in places if isinstance(item, dict) and str(item.get("name") or "").strip()]
    # 同一维度的在前、别的维度（或没写维度的旧记录）在后：先后各按 Mod 给的远近
    ordered = [item for item in named if _in_dimension(item, dimension)]
    ordered += [item for item in named if not _in_dimension(item, dimension)]
    names = list(dict.fromkeys(str(item["name"]).strip() for item in ordered))
    if not names:
        return {}
    line = "、".join(names[:REMEMBERED_PLACES_MAX])
    if len(names) > REMEMBERED_PLACES_MAX:
        line += f"等共 {len(names)} 处"
    return {"remembered_places": line}


# 游戏情境里最多列几个最近的记住的地点
SITUATION_PLACES_MAX = 5


def self_position(reply: MaicraftReply) -> Optional[Tuple[str, float, float]]:
    """看自己的回复里角色的维度与水平坐标（x、z）；读失败或缺字段时为 None。"""
    if not reply.ok:
        return None
    position = reply.data.get("position") if isinstance(reply.data.get("position"), dict) else {}
    dimension = str(position.get("dimension") or "")
    x, z = position.get("x"), position.get("z")
    if not dimension or not all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in (x, z)):
        return None
    return dimension, float(x), float(z)


def nearest_places(
    places: List[Any], position: Tuple[str, float, float], limit: int = SITUATION_PLACES_MAX
) -> Tuple[NearbyPlace, ...]:
    """记住的地点里和角色同一维度的，按水平距离由近到远取前 ``limit`` 个（同名只留最近的）。

    Mod 给的 ``distance`` 是读世界记忆那一刻的、而且不分维度；这里用角色眼下的位置现算，
    别的维度、没写维度或坐标不全的旧记录一律不算。
    """
    dimension, x, z = position
    best: Dict[str, float] = {}
    for item in places:
        if not isinstance(item, dict) or not _in_dimension(item, dimension):
            continue
        name = str(item.get("name") or "").strip()
        where = item["position"]
        px, pz = where.get("x"), where.get("z")
        if not name or not all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in (px, pz)):
            continue
        distance = math.hypot(float(px) - x, float(pz) - z)
        if name not in best or distance < best[name]:
            best[name] = distance
    ranked = sorted(best.items(), key=lambda pair: pair[1])[: max(0, limit)]
    return tuple(NearbyPlace(name=name, distance=int(round(distance))) for name, distance in ranked)


def _in_dimension(place: Dict[str, Any], dimension: str) -> bool:
    """一个记住的地点是否和角色在同一个维度；不知道角色在哪个维度时一律不算。"""
    position = place.get("position") if isinstance(place.get("position"), dict) else {}
    return bool(dimension) and position.get("dimension") == dimension


class RememberedPlacesCache:
    """速览里记住的地点的读取缓存：速览十几秒一次，世界记忆却难得变一回。

    存的是 Mod 给的原始地点清单，不是排好的那一行字：同一维度排前面要按每次速览读到的维度现排。
    清单到期（``ttl_s``）或被作废后，下一次速览才真读；什么时候作废由 Agent 定：

    - ``invalidate``：游戏 Agent 记地点、忘掉地点成功了——清单旧了，但还是这个存档的，读失败时可以顶上；
    - ``forget``：换了存档、MaiCraft 连接断开或重连——旧清单可能是另一个存档的，整份丢掉，免得串档。

    时间经注入的单调时钟（秒）走，测试推进假时钟验证到期。
    """

    def __init__(self, clock: Callable[[], float], ttl_s: float = REMEMBERED_PLACES_TTL_S) -> None:
        self._clock = clock
        self._ttl_s = ttl_s
        # 最后一次读到的地点清单；None = 这个存档还没读到过
        self._places: Optional[List[Any]] = None
        self._read_at = 0.0
        self._stale = True
        # 作废了几次：读世界记忆是异步的，读的途中作废过（刚记了地点、换了存档），读回来的可能是作废前的样子
        self._generation = 0

    @property
    def generation(self) -> int:
        """开始真读前记下它，读完交回 ``store``，用来认出读的途中作废过。"""
        return self._generation

    def fresh(self) -> Optional[List[Any]]:
        """没到期、没被作废的清单；该真读一次了返回 None。"""
        if self._places is None or self._stale or self._clock() - self._read_at >= self._ttl_s:
            return None
        return self._places

    def last_known(self) -> Optional[List[Any]]:
        """最后一次读到的清单，到期、作废了也给（读失败时顶上）；丢掉过或从没读到过为 None。"""
        return self._places

    def store(self, places: List[Any], generation: int) -> None:
        """真读成功：记下清单与读的时刻，从此刻起算到期。

        ``generation`` 是开始读之前的 ``generation``；读的途中作废过就不记——这份可能是记地点之前、
        或上一个存档的样子，记下来会让主播五分钟里都看不到刚记的地点，甚至看到别的存档的。
        """
        if generation != self._generation:
            return
        self._places, self._read_at, self._stale = list(places), self._clock(), False

    def invalidate(self) -> None:
        """清单旧了：下一次速览重读，读失败时仍可用这份旧的。"""
        self._stale = True
        self._generation += 1

    def forget(self) -> None:
        """清单可能不是眼下这个存档的：整份丢掉，下一次速览重读，读失败也不拿它顶。"""
        self._places, self._stale = None, True
        self._generation += 1


def _distant(entities: Any, counts: Any) -> List[str]:
    """身边范围外的生物，按种类报只数，写成 ``种类 ×只数``，多的在前；敌对生物不进远处清单。

    远处的生物来自两处：Mod 不论远近逐只列出的（敌对的、盯着角色的、有名字的、玩家），
    以及它对远处不相干的生物只按种类计数的 ``distant_entities``。主播只需要"远处有一群鱿鱼"
    这种程度，用不上逐只的方位距离，所以两处合在一起按种类数。
    敌对生物不进远处清单：远处条目只有种类×只数，没有距离与动向，提了也做不成事，只会喂出
    提完就没有下文的断头提及；近处威胁由"身边有……是敌对的"一句与挨打事件承载。
    有名字的（玩家、起了名的生物）把名字带在种类后面，主播才能叫得出是谁。
    """
    tally: Dict[str, int] = {}
    if isinstance(entities, list):
        for item in entities:
            if not isinstance(item, dict) or not item.get("type"):
                continue
            distance = item.get("distance")
            # 没有距离的不知道远近，身边和远处都不算；身边范围内的已经在 entities 里逐只给了
            if not isinstance(distance, (int, float)) or distance <= NEARBY_BLOCKS:
                continue
            # 敌对生物不进远处清单，近处威胁由身边敌对句与挨打事件承载
            if item.get("hostile") is True:
                continue
            kind = f"{item['type']}（{item['name']}）" if item.get("name") else str(item["type"])
            tally[kind] = tally.get(kind, 0) + 1
    if isinstance(counts, dict):
        for kind, count in counts.items():
            if isinstance(count, int) and not isinstance(count, bool) and count > 0:
                tally[str(kind)] = tally.get(str(kind), 0) + count
    ordered = sorted(tally.items(), key=lambda pair: (-pair[1], pair[0]))
    return [f"{kind} ×{count}" for kind, count in ordered]


def entity_things(mod_things: Any) -> Dict[str, Dict[str, Any]]:
    """模组报的东西里和实体同号的那几类，按观察编号索引：编号 → ``{"kind": 叫法, 其余原字段}``。

    车万女仆的女仆与墓碑和场景里那只实体共用一个编号（e#），实体条目本身只有种类 ID；
    按编号对上，才知道这只 ``touhou_little_maid:maid`` 是什么皮肤、这块墓碑上写着谁。
    """
    found: Dict[str, Dict[str, Any]] = {}
    if not isinstance(mod_things, dict):
        return found
    for category, kind in _ENTITY_THING_KINDS.items():
        for item in mod_things.get(category) or []:
            if isinstance(item, dict) and item.get("id"):
                found[str(item["id"])] = {**item, "kind": kind}
    return found


def described_entity(entity: Dict[str, Any], things: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """一只身边的实体 → 主播看的条目：种类、认出它是谁的事实、方位距离、敌对与否；不带观察编号。"""
    thing = things.get(str(entity.get("id") or ""), {})
    traits = entity.get("traits") if isinstance(entity.get("traits"), dict) else {}
    described: Dict[str, Any] = {"type": entity.get("type")}
    if thing.get("kind"):
        described["kind"] = thing["kind"]
    if entity.get("name"):
        described["name"] = entity["name"]
    if thing.get("skin_name") or thing.get("skin_id"):
        # 皮肤名是本机模型表的翻译，表里没有时退回皮肤 ID，总比只剩种类强
        described["skin"] = thing.get("skin_name") or thing.get("skin_id")
    if thing.get("maid_name"):
        described["grave_of"] = thing["maid_name"]
    if traits.get(_TRAIT_OWNER):
        described["owner"] = traits[_TRAIT_OWNER]
    tamer = str(traits.get(_TRAIT_TAMER) or "").strip()
    if tamer and not _UUID.fullmatch(tamer):
        # 驯服者是玩家名才记（主播的称呼里写成"玩家甲的狼"）；是 UUID 的不记，称呼退回"别人的狼"
        described["tamer"] = tamer
    if traits.get(_TRAIT_SITTING) == "是":
        described["sitting"] = True
    for key in ("direction", "distance", "hostile"):
        if entity.get(key) is not None:
            described[key] = entity[key]
    return described


def _close(items: Any) -> List[Dict[str, Any]]:
    """身边范围内的条目原样取出，近的在前。"""
    if not isinstance(items, list):
        return []
    close = [
        item
        for item in items
        if isinstance(item, dict)
        and isinstance(item.get("distance"), (int, float))
        and item["distance"] <= NEARBY_BLOCKS
    ]
    close.sort(key=lambda item: item["distance"])
    return close


def _nearby(items: Any, keys: tuple[str, ...]) -> List[Dict[str, Any]]:
    """身边范围内的条目，近的在前，只留叙事用得上的字段。"""
    return [{key: item[key] for key in keys if item.get(key) is not None} for item in _close(items)]
