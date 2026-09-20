"""基础设施配置根模型（config/infra.toml）

聚合一切支撑设施段：TTS / 字幕 / 事件 / 拦截器 / 面板 / 日志 /
模拟直播间 / 口型分析共享件。段名与键名保持既有形态——文件重组不改键。
"""

from typing import Any

from pydantic import Field

from src.modules.avatar.lipsync import LipSyncConfig
from src.modules.config.core_schemas import (
    AgentSupervisorConfig,
    DashboardConfig,
    EventHistoryConfig,
    SubtitleInfraConfig,
    TTSConfig,
)
from src.modules.config.file_meta import FileMetaConfig
from src.modules.config.schemas.base import BaseConfig
from src.modules.config.schemas.logging import LoggingConfig
from src.modules.simulator.config_schema import SimulatorConfigSchema


class AvatarInfraConfig(BaseConfig):
    """avatar 基础设施共享件配置（``[avatar]`` 段）

    口型分析器是共享基础设施、非 provider（一个实例扇出多具皮套），
    调参不进 provider 命名空间——依据「组件配置随其架构类别走」。
    """

    lipsync: LipSyncConfig = Field(default_factory=LipSyncConfig, description="口型分析调参（共享件）")


class InfraRootConfig(BaseConfig):
    """基础设施配置根（对应 ``config/infra.toml`` 文件）

    段树：
    - ``[meta]``        — 文件元数据（结构版本，只读）
    - ``[tts]``         — TTS 基础设施（开关/目标引擎/队列/超时 + 引擎子段）
    - ``[subtitle]``    — 字幕基础设施
    - ``[avatar]``      — avatar 共享件（口型分析 [avatar.lipsync]）
    - ``[events]``      — EventBus 事件历史
    - ``[interceptors]`` — 事件拦截器配置（动态键）
    - ``[dashboard]``   — Web Dashboard
    - ``[agent_supervisor]`` — Agent 心跳与自动重建（守护）
    - ``[logging]``     — 日志
    - ``[simulator]``   — 模拟直播间
    """

    __file_name__ = "infra.toml"
    __section_label__ = "基础设施"

    meta: FileMetaConfig = Field(default_factory=FileMetaConfig, description="文件元数据")
    tts: TTSConfig = Field(
        default_factory=TTSConfig,
        description="TTS 基础设施配置（开关/目标引擎/队列/超时 + 引擎子段）",
    )
    subtitle: SubtitleInfraConfig = Field(
        default_factory=SubtitleInfraConfig,
        description="字幕基础设施配置（开关/后端列表 + tk_gui 子段）",
    )
    avatar: AvatarInfraConfig = Field(
        default_factory=AvatarInfraConfig,
        description="avatar 共享件配置（口型分析 [avatar.lipsync]）",
    )
    events: EventHistoryConfig = Field(default_factory=EventHistoryConfig, description="事件历史记录配置")
    interceptors: dict[str, Any] = Field(
        default_factory=lambda: {
            "rate_limit": {
                "enabled": True,
                "global_rate_limit": 100,
                "user_rate_limit": 10,
                "window_size": 60,
            },
            "similar_filter": {
                "enabled": True,
                "similarity_threshold": 0.85,
                "time_window": 5.0,
                "min_text_length": 3,
                "cross_user_filter": True,
            },
        },
        description="事件拦截器配置（动态键，如 rate_limit / similar_filter）",
    )
    agent_supervisor: AgentSupervisorConfig = Field(
        default_factory=AgentSupervisorConfig,
        description="Agent 心跳与自动重建（守护）配置（间隔/判死阈值/重建风暴保护）",
    )
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig, description="Dashboard 配置")
    logging: LoggingConfig = Field(default_factory=LoggingConfig, description="日志配置")
    simulator: SimulatorConfigSchema = Field(
        default_factory=SimulatorConfigSchema,
        description="模拟直播间配置（人设/礼物数据位于 SQLite 的 sim_personas/sim_gifts 表）",
    )


__all__ = ["AgentSupervisorConfig", "AvatarInfraConfig", "InfraRootConfig"]
