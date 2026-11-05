"""游戏 Agent 看画面（``observe(what="picture")``）的宿主侧处理：限次限间隔、取出图片、给视觉模型的问题。

MaiCraft 的画面视图回两个内容块：先是 JSON 文字（镜头朝哪、转没转过去、图上标了哪些观察编号），
再是一张 JPEG。文字那一块照旧当观察进历史；图怎么给模型由 Agent 定——能看图的模型把图挂在下一次请求
尾部看一眼就丢，不能看图的经视觉模型转成文字接在观察后面。

画面贵：一张图一两百 KB，能看图的模型按图计费，转文字还要多调一次视觉模型。Mod 只管不拖慢游戏
（同时只拍一张、至少隔 1 秒），一个任务里看几次、多久看一次是宿主的成本，归这里管：
超了不调 Mod，直接回一句改用结构化数据。
"""

from __future__ import annotations

import base64
import binascii
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.modules.tools.models import ToolExecutionResult

from .maicraft import OBSERVE

__all__ = ["PICTURE_VIEW", "PictureBudget", "is_picture_call", "picture_images", "picture_question"]

#: observe 的画面视图名
PICTURE_VIEW = "picture"

#: 模型不能看图、视觉模型也没转出文字时接在观察后面的说法
PICTURE_NOT_DESCRIBED = "画面没能转成文字"


def is_picture_call(name: str, arguments: Dict[str, Any]) -> bool:
    """这次工具调用是不是看画面：``maicraft_observe`` 且 ``what=picture``。"""
    return name == OBSERVE and arguments.get("what") == PICTURE_VIEW


class PictureBudget:
    """一个任务里看画面的次数与间隔：超了就不调 Mod，回一句改用结构化数据。

    次数按真拿到画面的算（Mod 回 busy、编号不认识这些没拍成的不算）；间隔从上一张拍成的时刻算。
    开新任务时清零。时间经注入的单调时钟（秒）走，测试推进假时钟验证间隔。
    """

    def __init__(self, clock: Callable[[], float], *, max_per_task: int, min_interval_ms: int) -> None:
        self._clock = clock
        self._max_per_task = max_per_task
        self._min_interval_ms = min_interval_ms
        # 这个任务里已经拍成了几张、上一张是什么时候拍成的（没拍过为 None）
        self._taken = 0
        self._last_at: Optional[float] = None

    def refusal(self) -> str:
        """这一次该不该拦下：该拦就返回给模型的一句话，放行返回空串。"""
        if self._max_per_task <= 0:
            return "看画面已经关掉了（配置里每个任务最多看 0 次），用 observe 的结构化数据"
        if self._taken >= self._max_per_task:
            return f"这个任务已经看了 {self._taken} 次画面，接下来用 observe 的结构化数据"
        if self._last_at is not None:
            elapsed_ms = int((self._clock() - self._last_at) * 1000)
            if elapsed_ms < self._min_interval_ms:
                wait_s = (self._min_interval_ms - elapsed_ms + 999) // 1000
                return (
                    f"离上次看画面还不到 {self._min_interval_ms // 1000} 秒，{wait_s} 秒后才能再看；"
                    "这会儿先用 observe 的结构化数据，或者先做别的"
                )
        return ""

    def taken(self) -> None:
        """拍成了一张：计数加一，间隔从此刻起算。"""
        self._taken += 1
        self._last_at = self._clock()

    def reset(self) -> None:
        """开新任务：次数与间隔都重新算。"""
        self._taken = 0
        self._last_at = None


def picture_images(result: ToolExecutionResult) -> List[Tuple[bytes, str]]:
    """工具结果里的图：每张解成 ``(字节, MIME 类型)``；base64 解不开的那张跳过。"""
    images: List[Tuple[bytes, str]] = []
    for block in result.blocks:
        if block.kind != "image" or not block.data:
            continue
        try:
            images.append((base64.b64decode(block.data, validate=True), block.mime_type or "image/jpeg"))
        except (binascii.Error, ValueError):
            # 解不开就当这张没有：转文字那一步照"画面没能转成文字"如实说，不让坏数据打断这一步
            continue
    return images


def _thing_label(item: Dict[str, Any]) -> str:
    """图上一个编号 → "b5（minecraft:chest）""e4（minecraft:player，Alex）"：编号加种类，有名字带名字。"""
    kind = next((str(item[key]) for key in ("type", "block", "kind", "category") if item.get(key)), "")
    facts = [fact for fact in (kind, str(item.get("name") or "")) if fact]
    return f"{item['id']}（{'，'.join(facts)}）" if facts else str(item["id"])


def picture_question(arguments: Dict[str, Any], data: Dict[str, Any]) -> str:
    """模型不能看图时问视觉模型的话：按这次调用拼——看的是哪个编号、图上标了哪些编号。

    请它说清画面里有什么、要看的那样东西与各编号的东西看起来怎样、有没有不对劲的地方；
    数量、血量、小字以结构化数据为准，所以让它看不清就说看不清，不要猜。
    """
    target = str(arguments.get("id") or "").strip()
    picture = data.get("picture") if isinstance(data.get("picture"), dict) else {}
    labeled = [
        _thing_label(item)
        for item in data.get("in_picture") or []
        if isinstance(item, dict) and item.get("id") and item.get("labeled") is True
    ]
    parts = ["这是 Minecraft 游戏里角色眼前的一张画面，只拍了游戏世界，没有物品栏和聊天栏。"]
    if picture.get("menu_included") is True:
        parts.append("这张连同开着的界面一起拍了。")
    if target:
        parts.append(f"这次是转过去看「{target}」拍的。")
    if labeled:
        parts.append("图上用编号标出了这些东西：" + "、".join(labeled) + "。")
    asks = ["画面里有什么"]
    if target:
        asks.append(f"「{target}」看起来怎样")
    if labeled:
        asks.append("各个编号的东西看起来怎样（按编号说）")
    if picture.get("menu_included") is True:
        asks.append("界面上写着什么")
    asks.append("有没有不对劲的地方（缺了一块、歪了、颜色不搭、东西不在该在的地方）")
    parts.append("请说清：" + "；".join(asks) + "。")
    parts.append("只说看得见的；数量、血量、小字看不清就说看不清，不要猜。")
    return "".join(parts)
