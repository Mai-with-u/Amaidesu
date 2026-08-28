"""模拟直播间输入模拟服务（SimulatorService）

ADR-006：LLM 模拟器归位为**官方开发基础设施**（与 Dashboard / --dry / 日志系统同类）。
默认 ``enabled = false``（生产零沾染），由组合根在 ``[simulator].enabled = true`` 时
装配 ``SimulatorService`` 纳入生命周期；服务内部实例化本包的 8 个实现类
（persona_pool / cadence / gift_generator / llm_wrapper / session_selector /
token_budget / types / config_schema）构建 LLM 驱动的生成循环，
向 EventBus 推送带 ``simulated=True`` 溯源标记的 ``room.message.*`` 事件。

历史包袱说明：本包早期版本以 ``LiveStreamSimulator`` 类为本体、``SimulatorService``
为 Manager of one；Wave 6 stub 退化期曾以 56 行 stub 占位。ADR-006 翻转该拍板，
stub 及其装饰器注册已删除，本模块仅保留 SimulatorService 作为组合根装配入口。
"""

from src.modules.simulator.service import SimulatorService  # noqa: F401

__all__ = ["SimulatorService"]
