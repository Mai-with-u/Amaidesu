"""主播"看一眼游戏"的精简视图：从 ``observe(self)`` 与 ``observe(scene)`` 里取出直播叙事用得上的事实。

主播的职责是把身体在做什么、遇到了什么讲给观众听，不替身体做工程判断。观察里的观察编号、
相对方位、俯视网格是给游戏 Agent 规划用的，交给主播只会让它去猜坐标。这里只保留：身体状态、
背包物品与数量、身边的生物与设施（近处的才算"身边"）、远处的生物（只按种类报只数）、时段天气。
上游没给的字段不写（缺失是"未知"，不是"没有"），读取失败照实写出原因。
"""

from __future__ import annotations

from typing import Any, Dict, List

from .attention_matrix import biome_name, item_name
from .maicraft import MaicraftReply

__all__ = ["NEARBY_BLOCKS", "glance_brief", "glance_scene", "glance_self"]

# 直播里说"身边"的范围：再远的生物与设施观众在画面里也看不清，主播提起来只会让人困惑。
NEARBY_BLOCKS = 24

# 一句话现场的长度上限：它要塞进主播的现场卡，卡总共只有几百字
BRIEF_MAX_CHARS = 300

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
    friendly = [
        _mob_name(str(entity["type"])) + (f"（叫{entity['name']}）" if entity.get("name") else "")
        for entity in entities
        if entity.get("hostile") is not True
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


def glance_scene(reply: MaicraftReply) -> Dict[str, Any]:
    """周边：时段天气、生物群系、身边的生物与设施（各带距离和大致方位），远处的生物按种类计数。"""
    if not reply.ok:
        return {"nearby_error": _failure(reply)}
    data = reply.data
    result: Dict[str, Any] = {}
    for key in ("time", "weather", "biome"):
        if data.get(key):
            result[key] = data[key]
    entities = _nearby(data.get("entities"), ("type", "name", "direction", "distance", "hostile"))
    if entities:
        result["entities"] = entities
    distant = _distant(data.get("entities"), data.get("distant_entities"))
    if distant:
        result["distant_entities"] = distant
    facilities = _nearby(data.get("facilities"), ("block", "direction", "distance"))
    if facilities:
        result["facilities"] = facilities
    return result


def _distant(entities: Any, counts: Any) -> List[str]:
    """身边范围外的生物，按种类报只数，写成 ``种类 ×只数``；敌对的排前面，其余多的在前。

    远处的生物来自两处：Mod 不论远近逐只列出的（敌对的、盯着角色的、有名字的、玩家），
    以及它对远处不相干的生物只按种类计数的 ``distant_entities``。主播只需要"远处还有一只苦力怕、
    一群鱿鱼"这种程度，用不上逐只的方位距离，所以两处合在一起按种类数。
    有名字的（玩家、起了名的生物）把名字带在种类后面，主播才能叫得出是谁。
    """
    tally: Dict[str, int] = {}
    hostile: set[str] = set()
    if isinstance(entities, list):
        for item in entities:
            if not isinstance(item, dict) or not item.get("type"):
                continue
            distance = item.get("distance")
            # 没有距离的不知道远近，身边和远处都不算；身边范围内的已经在 entities 里逐只给了
            if not isinstance(distance, (int, float)) or distance <= NEARBY_BLOCKS:
                continue
            kind = f"{item['type']}（{item['name']}）" if item.get("name") else str(item["type"])
            tally[kind] = tally.get(kind, 0) + 1
            if item.get("hostile") is True:
                hostile.add(kind)
    if isinstance(counts, dict):
        for kind, count in counts.items():
            if isinstance(count, int) and not isinstance(count, bool) and count > 0:
                tally[str(kind)] = tally.get(str(kind), 0) + count
    # 远处的敌对生物是直播里值得提的事，放在一群鱿鱼、蝙蝠前面
    ordered = sorted(tally.items(), key=lambda pair: (pair[0] not in hostile, -pair[1], pair[0]))
    return [f"{kind} ×{count}" for kind, count in ordered]


def _nearby(items: Any, keys: tuple[str, ...]) -> List[Dict[str, Any]]:
    """身边范围内的条目，近的在前，只留叙事用得上的字段。"""
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
    return [{key: item[key] for key in keys if item.get(key) is not None} for item in close]
