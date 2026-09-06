"""TkGuiBackend - 包装现有 SubtitleGuiService 的字幕 Backend

包装 ``SubtitleGuiService``——长驻 Tk 线程 + 文本队列 + 自动隐藏的
GUI 渲染服务，实现 ``SubtitleBackend`` 协议，**只组合引用**。
GUI 服务本体（``tk_gui_service``）与本 Backend 同包。

职责：

- ``show`` → 转发到 ``SubtitleGuiService.push_subtitle(text)``
  （线程安全入队）
- ``clear`` → 转发到 ``SubtitleGuiService._clear_content()``
  （既有私有方法，调用方约定；包装层只做协议形态适配）
- ``enabled`` → 转发 ``SubtitleGuiService.enabled`` 属性
  （CustomTkinter 不可用时为 False）

生命周期：

- Backend **不**实例化 ``SubtitleGuiService``——装配层在构造
  GUI 服务并 ``start()`` 启动 Tk 线程后，再注入本 Backend。
- Backend 自身无 setup / cleanup；本协议未要求生命周期方法。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from src.modules.logging import get_logger

if TYPE_CHECKING:
    from .tk_gui_service import SubtitleGuiService


class TkGuiBackend:
    """字幕 Tk/CustomTkinter Backend，包装既有 ``SubtitleGuiService``。

    满足 ``SubtitleBackend`` 协议（结构类型，不显式继承）：

    - ``async def show(text, utterance_id=None)`` → 转发入队
    - ``async def clear()`` → 转发清空
    - ``enabled`` 属性 → 转发 GUI 服务可用性
    """

    def __init__(self, service: "SubtitleGuiService") -> None:
        """组合引用外部注入的 ``SubtitleGuiService`` 实例。

        Args:
            service: 由装配层持有并已 ``start()`` 的 GUI 服务实例。
                构造 Backend 不触发 Tk 线程启动，不修改 service
                的任何状态。
        """
        self._service: "SubtitleGuiService" = service
        self.logger = get_logger(self.__class__.__name__)

    @property
    def enabled(self) -> bool:
        """GUI 后端是否可用（CustomTkinter 缺失时为 False）。"""
        return bool(self._service.enabled)

    async def show(self, text: str, utterance_id: Optional[str] = None) -> None:
        """推送字幕文本——线程安全入队。

        ``SubtitleGuiService.push_subtitle`` 是同步方法，内部走
        ``queue.Queue.put``；本方法直接 await 调用即可（无 await
        不挂起，但保留 async 形态以满足协议签名）。
        """
        # utterance_id 暂不传给 GUI 服务（GUI 不消费该键，留作未来
        # 在 GUI 上做关联展示如"发言 N"标记时再用）。
        self._service.push_subtitle(text)

    async def clear(self) -> None:
        """清空字幕显示。

        调用 ``SubtitleGuiService._clear_content()``——属既有私有
        API，由 ``SubtitleProvider.invoke`` 也同样调用；保持同一
        调用约定。
        """
        self._service._clear_content()


__all__ = ["TkGuiBackend"]
