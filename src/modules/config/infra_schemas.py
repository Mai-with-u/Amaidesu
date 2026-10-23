"""基础设施配置根模型（config/infra.toml）

聚合一切支撑设施段：TTS / 字幕 / 事件 / 拦截器 / 面板 / 日志 /
模拟直播间。段名与键名保持既有形态——文件重组不改键。
（口型分析共享件段已迁往 avatar.toml ``[avatar.lipsync]``。）
"""

from typing import Any

from pydantic import Field

from src.modules.config.core_schemas import (
    AgentSupervisorConfig,
    DashboardConfig,
    DiagnosticsConfig,
    EventHistoryConfig,
    SubtitleInfraConfig,
    TTSConfig,
)
from src.modules.config.file_meta import FileMetaConfig
from src.modules.config.schemas.base import BaseConfig
from src.modules.config.schemas.logging import LoggingConfig
from src.modules.maibot_sync.config_schema import MaiBotSyncConfigSchema
from src.modules.simulator.config_schema import SimulatorConfigSchema


class InfraRootConfig(BaseConfig):
    """基础设施配置根（对应 ``config/infra.toml`` 文件）

    段树：
    - ``[meta]``        — 文件元数据（结构版本，只读）
    - ``[tts]``         — TTS 基础设施（开关/目标引擎/队列/超时 + 引擎子段）
    - ``[subtitle]``    — 字幕基础设施
    - ``[events]``      — EventBus 事件历史
    - ``[interceptors]`` — 事件拦截器配置（动态键）
    - ``[dashboard]``   — Web Dashboard
    - ``[agent_supervisor]`` — Agent 心跳与自动重建（守护）
    - ``[logging]``     — 日志
    - ``[diagnostics]`` — 本地诊断记录落盘（LLM 正文/事件流/TTS 记录）
    - ``[simulator]``   — 模拟直播间
    - ``[maibot_sync]`` — 直播近况同步给 MaiBot 适配器插件
    """

    __file_name__ = "infra.toml"
    __section_label__ = "基础设施"

    meta: FileMetaConfig = Field(default_factory=FileMetaConfig, title="文件元数据", description="文件元数据")
    tts: TTSConfig = Field(
        default_factory=TTSConfig,
        title="TTS 配置",
        description="TTS 基础设施配置（开关/目标引擎/队列/超时 + 引擎子段）",
    )
    subtitle: SubtitleInfraConfig = Field(
        default_factory=SubtitleInfraConfig,
        title="字幕配置",
        description="字幕基础设施配置（开关/后端列表 + tk_gui 子段）",
    )
    events: EventHistoryConfig = Field(
        default_factory=EventHistoryConfig, title="事件历史", description="事件历史记录配置"
    )
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
        title="事件拦截器",
        description="事件拦截器配置（动态键，如 rate_limit / similar_filter）",
    )
    agent_supervisor: AgentSupervisorConfig = Field(
        default_factory=AgentSupervisorConfig,
        title="Agent 守护",
        description="Agent 心跳与自动重建（守护）配置（间隔/判死阈值/重建风暴保护）",
    )
    dashboard: DashboardConfig = Field(
        default_factory=DashboardConfig, title="Dashboard 配置", description="Dashboard 配置"
    )
    logging: LoggingConfig = Field(default_factory=LoggingConfig, title="日志配置", description="日志配置")
    diagnostics: DiagnosticsConfig = Field(
        default_factory=DiagnosticsConfig,
        title="诊断记录",
        description="本地诊断记录落盘（LLM 请求正文/事件流/TTS 合成记录，logs/ 平铺子目录）",
    )
    simulator: SimulatorConfigSchema = Field(
        default_factory=SimulatorConfigSchema,
        title="模拟直播间配置",
        description="模拟直播间配置（人设/礼物数据位于 SQLite 的 sim_personas/sim_gifts 表）",
    )
    maibot_sync: MaiBotSyncConfigSchema = Field(
        default_factory=MaiBotSyncConfigSchema,
        title="MaiBot 近况同步",
        description="把开播、换环节、收播与场次回顾推给 MaiBot 适配器插件（MaiBot-Amaidesu-Adapter）",
    )


__all__ = ["AgentSupervisorConfig", "InfraRootConfig"]
