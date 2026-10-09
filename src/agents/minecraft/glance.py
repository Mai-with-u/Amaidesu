"""主播"看一眼游戏"的精简视图：从 ``observe(self)`` 与 ``observe(scene)`` 里取出直播叙事用得上的事实。

主播的职责是把身体在做什么、遇到了什么讲给观众听，不替身体做工程判断。观察里的观察编号、
相对方位、俯视网格是给游戏 Agent 规划用的，交给主播只会让它去猜坐标。这里只保留：身体状态、
背包物品与数量、身边的生物与设施（近处的才算"身边"）、时段天气。上游没给的字段不写
（缺失是"未知"，不是"没有"），读取失败照实写出原因。
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
    """周边：时段天气、生物群系、身边的生物与设施（各带距离和大致方位）。"""
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
    facilities = _nearby(data.get("facilities"), ("block", "direction", "distance"))
    if facilities:
        result["facilities"] = facilities
    return result


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
