"""字幕服务 SubtitleService - 多 Backend 并行广播 + 幂等生命周期

字幕基础设施包的核心服务，负责：

- 维护一组 ``SubtitleBackend`` 实例（多 Backend 可同时启用，并行
  调度）
- ``show`` / ``clear`` 调用时向**全部** Backend 并行广播，单 Backend
  故障隔离（抛异常仅记 ERROR，不拖垮其他 Backend）
- 暴露 ``start`` / ``stop`` 幂等生命周期（与 ``src.modules.simulator`` 的
  服务同模式：``_is_started`` 标志位 + 早退）

设计决策：

- **并行广播**：用 ``asyncio.gather(..., return_exceptions=True)``
  并行派发——后端各自执行（GUI 入队 / HTTP POST / 文件写入），串行
  等待会让总延迟叠加为各后端之和，无必要。
- **故障隔离**：单后端抛异常时仅记 ERROR + 继续，不向上传播——与
  TTS Provider 的"失败 raise"语义相反，因为字幕多后端的语义是
  "任一后端坏了不挡其他后端"，与单选 TTS 引擎不同。
- **Backend 名取自类名**：协议最小契约不含 ``name``，服务用
  ``type(backend).__name__`` 作为诊断标识（隔离协议最小性与日志
  可观测性的冲突）。
- **构造时不做 setup**：注册 Backend 即可用；装配根在工厂返回后
  显式 ``start``，不强制服务自治生命周期。
"""

from __future__ import annotations

import asyncio
from typing import List, Optional

from src.modules.logging import get_logger

from .protocol import SubtitleBackend


class SubtitleService:
    """字幕基础设施服务 - 多 Backend 并行广播 + 幂等生命周期。

    通过 ``register_backend`` 追加后端；通过 ``show`` / ``clear`` 并行
    广播到全部已注册后端。``start`` / ``stop`` 控制生命周期，幂等
    可重复调用。
    """

    def __init__(self, *, logger_name: str = "SubtitleService") -> None:
        self._backends: List[SubtitleBackend] = []
        self._is_started: bool = False
        self.logger = get_logger(logger_name)

    # ------------------------------------------------------------------
    # 注册与诊断
    # ------------------------------------------------------------------

    def register_backend(self, backend: SubtitleBackend) -> None:
        """注册一个 Backend（追加到末尾，不去重）。

        Args:
            backend: 实现 ``SubtitleBackend`` 协议的对象。重复注册同
                一对象是允许的（调用方负责去重），本方法不做去重判断。

        Raises:
            TypeError: ``backend`` 不具备 ``SubtitleBackend`` 结构契约时
                （装配边界 fail-fast，避免坏后端混入广播链）。
        """
        if not isinstance(backend, SubtitleBackend):
            raise TypeError(f"register_backend 需要 SubtitleBackend 实现，得到 {type(backend).__name__}")
        self._backends.append(backend)
        self.logger.info(f"已注册字幕后端: {type(backend).__name__}（当前共 {len(self._backends)} 个）")

    @property
    def backend_count(self) -> int:
        """当前已注册的 Backend 数量（诊断用）。"""
        return len(self._backends)

    @property
    def is_running(self) -> bool:
        """服务是否处于已启动状态。"""
        return self._is_started

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """启动字幕服务（幂等，重复调用早退）。

        若尚无任何 Backend 注册则 WARN 提示但不抛错——允许装配期
        留空后续追加。
        """
        if self._is_started:
            self.logger.debug("字幕服务已启动，忽略重复 start")
            return
        if not self._backends:
            self.logger.warning("字幕服务启动时无任何 Backend 已注册（show / clear 将被跳过）")
        self._is_started = True
        self.logger.info(f"字幕服务已启动（{len(self._backends)} 个后端）")

    async def stop(self) -> None:
        """停止字幕服务（幂等，未启动早退）。

        仅翻转内部状态标志位——各 Backend 的资源释放由装配层各自
        管理（GUI 长驻 Tk 线程由 ``SubtitleGuiService`` 自己 stop），
        本服务不做强制清理，避免与 Backend 自身的生命周期冲突。
        """
        if not self._is_started:
            return
        self._is_started = False
        self.logger.info("字幕服务已停止")

    # ------------------------------------------------------------------
    # 广播
    # ------------------------------------------------------------------

    async def show(self, text: str, utterance_id: Optional[str] = None) -> None:
        """向全部已注册 Backend 并行推送字幕文本。

        Args:
            text: 要显示的字幕内容。
            utterance_id: 编排层关联键（与 ``tts.utterance`` /
                ``streamer.speech`` 的 ``utterance_id`` 一致时的关联
                目的）；None 表示无关联上下文。

        单 Backend 异常被捕获并记 ERROR（带后端类名），不影响其他
        Backend。无 Backend 时 debug 跳过，不报错。
        """
        if not self._backends:
            self.logger.debug("字幕 show 跳过：无任何 Backend 已注册")
            return
        results = await asyncio.gather(
            *(backend.show(text, utterance_id) for backend in self._backends),
            return_exceptions=True,
        )
        for backend, result in zip(self._backends, results, strict=False):
            if isinstance(result, BaseException):
                self.logger.error(
                    f"字幕后端 {type(backend).__name__} show 失败: {result}",
                    exc_info=result,
                )

    async def clear(self) -> None:
        """向全部已注册 Backend 并行发送清空指令。

        单 Backend 异常隔离（与 ``show`` 同模式）。无 Backend 时
        debug 跳过。
        """
        if not self._backends:
            self.logger.debug("字幕 clear 跳过：无任何 Backend 已注册")
            return
        results = await asyncio.gather(
            *(backend.clear() for backend in self._backends),
            return_exceptions=True,
        )
        for backend, result in zip(self._backends, results, strict=False):
            if isinstance(result, BaseException):
                self.logger.error(
                    f"字幕后端 {type(backend).__name__} clear 失败: {result}",
                    exc_info=result,
                )


__all__ = ["SubtitleService"]
