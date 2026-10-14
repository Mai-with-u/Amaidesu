"""尾部临时图片：工具回执里只留文字，真图只挂在紧随其后的那一次请求尾部。

截图是几秒就过期的易逝数据。图要是留在历史里，每次请求都会重新上传、重新
计费；等它过期再就地换成文字，又会改写历史中段、让之后的前缀缓存全部失效。
所以图从一开始就不进历史：工具回执写进历史的是文字观察（图位是占位说明），
真图暂存在这里，下一次请求装配时作为一条 user 消息挂在序列最后，用完即弃。
模型看图那一步写下的说明，就是画面在之后历史里的文字形态。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

__all__ = ["TailImages"]


class TailImages:
    """待挂到下一次请求尾部的图片引用（``VisionImageStore`` 句柄）。"""

    def __init__(self) -> None:
        self._refs: List[str] = []
        self._sources: List[str] = []

    def __bool__(self) -> bool:
        return bool(self._refs)

    def add(self, refs: Sequence[str], *, source: str) -> None:
        """登记一个工具刚返回的图；同一步多个工具带图时合并成一条尾部消息。"""
        if not refs:
            return
        self._refs.extend(refs)
        if source and source not in self._sources:
            self._sources.append(source)

    def drain(self) -> Optional[Dict[str, Any]]:
        """取出尾部消息并清空；没有待挂的图返回 None。

        消息不写回历史：调用方只把它拼在本次请求的消息列表末尾。
        """
        if not self._refs:
            return None
        origin = "、".join(self._sources) or "工具"
        note = f"[刚才 {origin} 返回的画面，只在这一次请求里可见；之后历史里只剩文字回执，需要记住的内容请在回复里写明]"
        content: List[Dict[str, Any]] = [{"type": "text", "text": note}]
        content.extend({"type": "image_ref", "ref": ref} for ref in self._refs)
        self._refs = []
        self._sources = []
        return {"role": "user", "content": content}
