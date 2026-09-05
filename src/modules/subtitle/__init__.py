"""字幕基础设施包（与 ``src.modules.tts`` 同构的基建层，多后端可并行启用）

字幕作为基础设施：配置启用即百分百工作，不由 Agent 决定用不用。其
调用方为编排层（订阅 ``streamer.speech`` 业务事件后驱动字幕更新），
由装配根在工厂返回后注入 ``StreamerAgent`` 或编排器直接调用
``SubtitleService.show`` / ``clear``。

包结构：

- ``SubtitleBackend``：最小契约协议（结构类型），声明 ``show`` /
  ``clear`` 两个成员。无流式方法——打字机观感由前端模板动画承担。
- ``SubtitleService``：核心服务，持有多个 Backend 并行广播，单
  Backend 故障隔离；暴露幂等 ``start`` / ``stop`` 生命周期。
- ``backends.TkGuiBackend``：包装既有 ``SubtitleGuiService``（长驻
  Tk 线程 + 字幕渲染后端），把 Tool 系统遗留的 GUI 服务接入新协议。
- ``backends.DashboardBackend``：包装 Dashboard 的弹幕小部件字幕展示
  位（``/ws/subtitle`` WebSocket），由主组合根注入 widget_service。

设计要点：

- **不抽基类**：与 ``TTSProvider`` 同源决策——历史拒绝 ``BaseSubtitleBackend``
  抽象类抽取（不同后端执行模型差异大，强抽基类 = 抽象泄漏）。
- **故障隔离**：单 Backend 抛异常被 ``SubtitleService`` 在并行
  广播时捕获并记 ERROR（带后端类名），不向上传播——多后端语义是
  "任一后端坏了不挡其他后端"，与 TTS 单选引擎不同。
- **协议最小化**：``SubtitleBackend`` 只含业务方法（``show`` /
  ``clear``），不含 ``name`` / ``setup`` / ``cleanup``——后端标识
  由服务用 ``type(backend).__name__`` 兜底，生命周期由装配层
  各自管理（GUI 长驻 Tk 线程由 ``SubtitleGuiService`` 自己 stop）。
"""

from .assembly import build_subtitle_infrastructure
from .backends import DashboardBackend, TkGuiBackend
from .protocol import SubtitleBackend
from .service import SubtitleService

__all__ = [
    # 协议
    "SubtitleBackend",
    # 核心服务
    "SubtitleService",
    # 内置 Backend
    "DashboardBackend",
    "TkGuiBackend",
    # 装配入口
    "build_subtitle_infrastructure",
]
