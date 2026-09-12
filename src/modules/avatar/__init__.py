"""虚拟形象提供者包（avatar）。

收录各虚拟形象后端的 Provider 实现：
- vts/ — VTubeStudio 控制
- vrchat/ — VRChat OSC 桥接（独立后端，与 VTS 无关）
- warudo/ — Warudo 控制

一个形象后端 = 一个 Provider 实例 = 一个启用单元。
``[tools.avatar.<name>]`` 配置控制其工具可见性：开 = 全部可见，关 = 全部消失。
"""
