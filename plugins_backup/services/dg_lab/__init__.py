"""
DGLab 服务模块

提供 DG-LAB 硬件控制服务，作为共享基础设施供其他组件调用。

这是一个服务（Service），不是 Provider：
- 不产生数据流
- 不订阅事件
- 提供共享 API 供其他组件通过依赖注入调用

使用示例：
    ```python
    from src.services.dg_lab import DGLabService, DGLabConfig

    # 创建服务
    config = DGLabConfig(
        api_base_url="http://127.0.0.1:8081",
        default_strength=10,
        default_waveform="big",
        shock_duration_seconds=2,
    )
    service = DGLabService(config)

    # 初始化
    await service.setup()

    # 触发电击
    await service.trigger_shock(strength=15, waveform="big", duration=3.0)

    # 清理
    await service.cleanup()
    ```
"""

from src.services.dg_lab.service import DGLabService
from src.services.dg_lab.config import DGLabConfig, WaveformPreset

__all__ = [
    "DGLabService",
    "DGLabConfig",
    "WaveformPreset",
]
