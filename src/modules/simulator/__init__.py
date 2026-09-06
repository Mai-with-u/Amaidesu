"""模拟直播间输入模拟服务（SimulatorService）

LLM 模拟器定位为**官方开发基础设施**（与 Dashboard / --dry / 日志系统同类）。
默认 ``enabled = false``（生产零沾染），由组合根在 ``[simulator].enabled = true`` 时
装配 ``SimulatorService`` 纳入生命周期；服务内部实例化本包的 8 个实现类
（persona_pool / cadence / gift_generator / llm_wrapper / session_selector /
token_budget / types / config_schema）构建 LLM 驱动的生成循环，
向 EventBus 推送带 ``simulated=True`` 溯源标记的 ``room.message.*`` 事件。
"""

from src.modules.simulator.service import SimulatorService  # noqa: F401

__all__ = ["SimulatorService"]
