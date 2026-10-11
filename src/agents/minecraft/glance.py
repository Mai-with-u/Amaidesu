"""主播"看一眼游戏"的精简视图：从 ``observe(self)`` 与 ``observe(scene)`` 里取出直播叙事用得上的事实。

主播的职责是把身体在做什么、遇到了什么讲给观众听，不替身体做工程判断。观察里的观察编号、
相对方位、俯视网格是给游戏 Agent 规划用的，交给主播只会让它去猜坐标。这里只保留：身体状态、
背包物品与数量、身边的生物与设施（近处的才算"身边"）、远处的生物（只按种类报只数）、时段天气。
上游没给的字段不写（缺失是"未知"，不是"没有"），读取失败照实写出原因。
"""

from __future__ import annotations

from typing import Any, Dict, List

from .maicraft import MaicraftReply

__all__ = ["NEARBY_BLOCKS", "glance_scene", "glance_self"]

# 直播里说"身边"的范围：再远的生物与设施观众在画面里也看不清，主播提起来只会让人困惑。
NEARBY_BLOCKS = 24


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
