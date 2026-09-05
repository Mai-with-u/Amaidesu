"""字幕基础设施装配入口

按 ``[tools.output.config.subtitle]`` 配置构造字幕基础组件并把 Tk GUI
后端注册到 ``SubtitleService``，供 ``StreamerAgent`` 直接持有并在
``streamer.speech`` 编排路径上调用 ``show`` / ``clear``。

设计要点
--------

- **基础模块自治装配**——字幕是基础设施（非工具），装配逻辑放在
  ``src/modules/subtitle/`` 包内（``src/modules/subtitle/assembly.py``），
  与 ``build_tts_infrastructure`` 同构
- **Tk GUI 服务生命周期归属装配层**——``SubtitleGuiService.start()``
  在本装配入口调用以启动 Tk 长驻线程（daemon）；Tk 线程是 Backend
  功能前提（``push_subtitle`` 入队后需 Tk 线程消费），不在此处启动
  则字幕静默失效（队列堆积不显示）。``SubtitleService.start()`` 是
  broadcast-ready 标志位翻转，**不**在本装配入口触发——与 TTS 不显式
  setup 引擎的语义一致，调用方按需在生命周期节点显式 ``start``
- **fail-soft 语义**——配置非 dict / 构造异常 / GUI 不可用时统一返回
  一个空 Backend 的 ``SubtitleService``（调用方按"已装配但无后端"
  语义降级，不抛错阻断冷启动）
- **不动既有 Tool 路径**——``SubtitleProvider`` / ``bind_core_tools``
  中字幕段装配仍按原路径走（尚未迁移），本入口只把 Tk GUI 服务接入
  新的 ``SubtitleService`` 基础设施路径；``SubtitleProvider`` 退役是
  后续独立片

装配流程示意::

    subtitle_config (dict)             #  tools.toml [tools.output.config.subtitle]
        ↓
    build_subtitle_infrastructure(subtitle_config)
        ├─ config 非 dict → WARN + 返回空 SubtitleService
        ├─ SubtitleGuiService(config) 失败 → ERROR + 返回空 SubtitleService
        ├─ SubtitleGuiService(config) 成功
        │   ├─ enabled=True → start() 启动 Tk daemon 线程
        │   └─ enabled=False → INFO 跳过（CustomTkinter 不可用）
        ├─ TkGuiBackend(service=gui_service)
        └─ SubtitleService.register_backend(backend) → 返回 service
"""

from __future__ import annotations

from typing import Any

from src.modules.logging import get_logger

from .backends import TkGuiBackend
from .service import SubtitleService

# 顶层 import 触发 ``tk_gui_service`` 模块加载（含 customtkinter 可用性
# 探测）；该服务是 Tk GUI 后端的渲染引擎，装配入口直接引用其类。
from .backends.tk_gui_service import SubtitleGuiService


def build_subtitle_infrastructure(
    subtitle_config: Any,
) -> SubtitleService:
    """按字幕配置构造 ``SubtitleService`` 并按 ``backends`` 列表注册后端。

    Args:
        subtitle_config: ``core.toml [subtitle]`` 段字典；缺失 / 非字典视
            为"无字幕配置"，仍返回空 Backend 的 ``SubtitleService``（装配
            幂等，不阻断 StreamerAgent 构造）。

    Returns:
        构造成功的 ``SubtitleService`` 实例（至少 0 个 Backend）。失败
        / 配置缺失统一返回空 Backend 的 ``SubtitleService``，fail-soft。

    副作用：
    - ``enabled=False`` 时零后端返回（字幕开关关闭）
    - ``backends`` 含 ``tk_gui`` 时构造并启动 SubtitleGuiService（Tk
      daemon 线程；CustomTkinter 不可用时跳过），注册 TkGuiBackend
    - Dashboard 后端由组合根注册（DashboardServer 构造之后）
    """
    logger = get_logger("SubtitleAssembly")

    # 输入防御：非 dict / None 一律视作无配置，返回空 service（装配
    # 边界 fail-soft，不阻断冷启动）
    if not isinstance(subtitle_config, dict):
        logger.warning(
            f"build_subtitle_infrastructure: config 不是 dict（type={type(subtitle_config).__name__}），"
            "返回无 Backend 的 SubtitleService"
        )
        return SubtitleService()

    if not subtitle_config.get("enabled", True):
        logger.info("build_subtitle_infrastructure: [subtitle].enabled=False，返回零后端服务")
        return SubtitleService()

    service = SubtitleService()

    backends = subtitle_config.get("backends", ["tk_gui"])
    if not isinstance(backends, list):
        backends = ["tk_gui"]

    if "tk_gui" in backends:
        gui_config = subtitle_config.get("tk_gui", {}) or {}
        if not isinstance(gui_config, dict):
            gui_config = {}
        try:
            gui_service = SubtitleGuiService(config=dict(gui_config))
        except Exception as exc:
            logger.error(
                f"build_subtitle_infrastructure: 构造 SubtitleGuiService 失败：{type(exc).__name__}: {exc}",
                exc_info=True,
            )
            return service

        # Tk GUI 线程启动——Backend 的功能前提：``push_subtitle`` 把文本入队，
        # 若 Tk 线程未启动则队列永不消费，字幕静默失效。Tk 线程为 daemon，
        # 进程退出时自动消亡，无需显式 stop。
        if gui_service.enabled:
            try:
                gui_service.start()
            except Exception as exc:
                logger.error(
                    f"build_subtitle_infrastructure: 启动 SubtitleGuiService Tk 线程失败：{type(exc).__name__}: {exc}",
                    exc_info=True,
                )
        else:
            logger.info("build_subtitle_infrastructure: CustomTkinter 不可用，GUI 后端 disabled（Tk 线程未启动）")

        service.register_backend(TkGuiBackend(service=gui_service))
        logger.info(f"已注册字幕后端: TkGuiBackend（GUI enabled={gui_service.enabled}）")

    return service


__all__ = ["build_subtitle_infrastructure"]
