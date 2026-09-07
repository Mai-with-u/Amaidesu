"""演播室提供者包（studio）。

收录演播室控制后端的 Provider 实现：
- obs/ — OBS Studio 控制（send_text / switch_scene / set_source_visibility）

一个演播室后端 = 一个 Provider 实例 = 一个启用单元。
``[tools.studio.<name>]`` 配置控制其工具可见性：开 = 全部可见，关 = 全部消失。
"""
