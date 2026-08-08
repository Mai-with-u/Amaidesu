"""模拟直播间输入模拟服务（SimulatorService）

独立的「输入模拟服务」一等公民，与 InputCollector 并列。
在无真实直播流时用 LLM 伪造多样化的观众消息，驱动下游 Decision / Output
阶段的联调与回归测试。

导入此包会触发 @simulator 装饰器注册 ConfigSchema/UI 元数据。
SimulatorService 是生命周期管理器，LiveStreamSimulator 是模拟器本体。
"""

from src.modules.simulator.service import SimulatorService  # noqa: F401
from src.modules.simulator.simulator import LiveStreamSimulator  # noqa: F401

__all__ = [
    "LiveStreamSimulator",
    "SimulatorService",
]
