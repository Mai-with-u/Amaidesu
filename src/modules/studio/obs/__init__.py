"""OBS 工具模块

提供三个 OBS 控制工具：
- ``obs_send_text``          - 发送文本到 OBS 文本源（含逐字效果）
- ``obs_switch_scene``       - 切换 OBS 场景
- ``obs_set_source_visibility`` - 控制源可见性

通过 ``obsws-python`` 控制 OBS Studio；依赖缺失时软降级。
"""

from .obs_provider import OBSProvider, create_obs_provider, register_obs_tools

__all__ = [
    "OBSProvider",
    "create_obs_provider",
    "register_obs_tools",
]
