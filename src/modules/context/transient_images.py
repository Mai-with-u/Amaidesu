"""易逝图：工具回执里只留文字，真图经入库换句柄后只挂在紧随其后的那次请求尾部。

截图是几秒就过期的易逝数据。图要是留在历史里，每次请求都会重新上传、重新
计费；等它过期再就地换成文字，又会改写历史中段、让之后的前缀缓存全部失效。
所以图从一开始就不进历史：工具回执写进历史的是文字观察（图位是占位说明），
真图经 ``VisionImageStore`` 入库换句柄后暂存在这里，下一次请求装配时作为一条
user 消息挂在序列最后，用完即弃。模型看图那一步写下的文字说明，就是画面在
之后历史里的形态。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from src.modules.logging import get_logger
from src.modules.tools.conversation import extract_image_data_urls
from src.modules.tools.models import ToolExecutionResult
from src.modules.vision.image_store import VisionImageStore, get_image_store

__all__ = ["TransientImages", "absorb_result_images", "is_visual_caller"]

logger = get_logger("ContextTransient")


def is_visual_caller(llm: Any, profile: str) -> bool:
    """本档位主模型能否看图（能力协商出口，全仓库唯一一份防御）。

    未注入（``llm=None``）或查询异常一律按非视觉——判错方向是多一次 VLM
    转述，而不是把图塞给看不懂的模型。注入了但缺方法的依赖由各调用方
    构造器装配校验拦下（见 ``llm.capabilities``），这里不做属性探测：
    引擎契约被破坏时启动即报错，而不是静默按非视觉处理。
    """
    if llm is None:
        return False
    try:
        return llm.is_profile_visual(profile) is True
    except Exception as e:
        logger.warning(f"视觉能力查询失败，按非视觉处理: {e}", exc=True)
        return False


def absorb_result_images(
    images: TransientImages,
    llm: Any,
    profile: str,
    tool_name: str,
    result: ToolExecutionResult,
    *,
    store: Optional[VisionImageStore] = None,
) -> None:
    """工具回执带图、模型能看图时，图入库换句柄并登记到尾部易逝图。

    只有成功回执才走看图路径——失败回执是异常信息，不该触发看图，也不该
    让异常进入决策热路径。图本体存进 ``VisionImageStore``，句柄登记到
    ``images``；历史里始终只有文字回执。
    """
    if not result.success:
        return
    if not is_visual_caller(llm, profile):
        return
    urls = extract_image_data_urls(result)
    if not urls:
        return
    image_store = store or get_image_store()
    images.add([image_store.put(url) for url in urls], source=tool_name)


class TransientImages:
    """待挂到下一次请求尾部的易逝图引用（``VisionImageStore`` 句柄）。"""

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
