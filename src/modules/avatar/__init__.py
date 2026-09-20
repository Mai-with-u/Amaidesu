"""虚拟形象提供者包（avatar）。

收录各虚拟形象后端的 Provider 实现：
- vts/ — VTubeStudio 控制
- vrchat/ — VRChat OSC 桥接（独立后端，与 VTS 无关）
- warudo/ — Warudo 控制

一个形象后端 = 一个 Provider 实例 = 一个启用单元。
``[tools.avatar.<name>]`` 配置控制其工具可见性：开 = 全部可见，关 = 全部消失。

跨后端的 LLM 工具契约（同语义同名同参数形状）由 ``protocol.AvatarProvider``
承载（结构类型规范 + 契约测试锚点，非运行时接缝）。
"""

from .protocol import AvatarProvider

__all__ = ["AvatarProvider"]
