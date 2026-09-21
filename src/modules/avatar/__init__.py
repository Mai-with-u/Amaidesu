"""虚拟形象域包（avatar）。

平台适配器收录在 platform/ 子包（目录镜像 avatar.toml 的 [avatar.platform]）：
- platform/vts/ — VTubeStudio 控制
- platform/vrchat/ — VRChat OSC 桥接（独立平台，与 VTS 无关）
- platform/warudo/ — Warudo 控制

一个皮套平台 = 一个 Provider 实例 = 一个启用单元，
``avatar.toml`` 的 ``[avatar.platform].enabled`` 名单控制装配：名单内 = 全部
工具可见，名单外 = 全部消失。共享件（口型分析 lipsync/）与平台平级。

跨平台的 LLM 工具契约（同语义同名同参数形状）由 ``protocol.AvatarProvider``
承载（结构类型规范 + 契约测试锚点，非运行时接缝）。
"""

from .protocol import AvatarProvider

__all__ = ["AvatarProvider"]
