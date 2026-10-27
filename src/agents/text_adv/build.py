"""text_adv 生产装配入口：感知/动作四后端的选型逻辑收在包内。

框架（factory）只负责"名字 → 包内 build"，后端具体选什么（读屏走注册表的
vision_look_at_screen、窗口用 pygetwindow、采集用 mss、键鼠用 Win32）由
本包自持——加内容 = 加包 + 配置，框架零改动。
"""

from __future__ import annotations

from src.modules.agents.deps import AgentDeps
from src.modules.logging import get_logger
from src.modules.vision import MssScreenCapture

from . import input as input_backends
from .agent import TextAdvGameAgent
from .config import TextAdvConfig
from .vlm import RegistryVisionReader
from .window import PyGetWindowBackend

__all__ = ["build"]

_logger = get_logger("TextAdvBuild")


def build(config: TextAdvConfig, deps: AgentDeps) -> TextAdvGameAgent:
    """按基建依赖装配 text_adv Agent（生产构造的唯一装配入口）。

    感知与动作四后端在此选型装配：读屏走 ToolRegistry 的
    ``vision_look_at_screen``（registry 未装配时调用期按感知失败降级）；
    ``tool_registry`` 同时透传给 Agent 供启动期自注册工具面。

    Args:
        config: 已解析的 TextAdvConfig 实例
        deps: 基建依赖值对象（取 tool_registry / event_bus）

    Returns:
        未启动的 TextAdvGameAgent 实例（生命周期由调用方管理）
    """
    _logger.debug("装配 text_adv：读屏经 ToolRegistry、窗口 pygetwindow、采集 mss、键鼠 Win32")
    return TextAdvGameAgent(
        config,
        vision_reader=RegistryVisionReader(deps.tool_registry, source="text_adv"),
        window_backend=PyGetWindowBackend(),
        capture=MssScreenCapture(),
        # 经模块取后端类（不顶部 from-import 绑定）：测试经 monkeypatch
        # 替换 src.agents.text_adv.input.Win32InputBackend 后此处才生效
        input_backend=input_backends.Win32InputBackend(),
        tool_registry=deps.tool_registry,
        event_bus=deps.event_bus,
    )
