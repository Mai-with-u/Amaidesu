"""Simulator 阶段配置 Schema 定义

定义 ``config/simulator.toml`` 的 Pydantic 聚合模型。

模拟直播间是独立的「输入模拟服务」（SimulatorService），
不属于 Input/Decision/Output 三阶段，因此配置独立为一个 TOML 文件。
"""

from __future__ import annotations

from src.modules.config.schemas.base import BaseConfig
from src.modules.simulator.config_schema import SimulatorConfigSchema
from pydantic import Field


class SimulatorConfig(BaseConfig):
    """simulator.toml 配置根模型

    TOML 结构示例::

        [simulator]
        enabled = false
        base_rate_per_minute = 6.0
        ...
    """

    simulator: SimulatorConfigSchema = Field(
        default_factory=SimulatorConfigSchema,
        description="模拟直播间配置",
    )
