"""Background 配置 Schema 定义（v2.0.0）

定义 ``config/background.toml`` 的 Pydantic 聚合模型。

段树结构（TOML 视角）::

    [background]
    light_tick_ms = 5000
    compressor_concurrency = 1
    queue_max = 100

设计原则：
- 后台维护任务（§1.7）的轻量循环 + 压缩 worker 参数
- ``compressor_concurrency=1`` 是默认值（保顺序，§1.7 定案）
- 后续若增加其他后台 worker（如清理任务），可扩展为子段
"""

from __future__ import annotations

from pydantic import ConfigDict, Field

from src.modules.config.schemas.base import BaseConfig


# ---------------------------------------------------------------------------
# Compressor 配置（压缩 worker）
# ---------------------------------------------------------------------------


class CompressorConfig(BaseConfig):
    """压缩 worker 配置

    负责房间状态摘要的压缩（§1.7）。
    """

    concurrency: int = Field(
        default=1,
        ge=1,
        le=8,
        description="压缩 worker 并发数（默认 1 保顺序，避免乱序覆盖）",
    )
    queue_max: int = Field(
        default=100,
        ge=1,
        description="压缩队列上限（防止积压耗尽内存）",
    )


# ---------------------------------------------------------------------------
# [background] 段聚合
# ---------------------------------------------------------------------------


class BackgroundConfig(BaseConfig):
    """[background] 段聚合

    包含后台维护任务的参数（轻量循环、压缩 worker 等）。
    """

    model_config = ConfigDict(extra="forbid")

    light_tick_ms: int = Field(
        default=5000,
        ge=100,
        le=60000,
        description="轻循环 tick 间隔（毫秒，§1.7 周期）",
    )
    compressor: CompressorConfig = Field(
        default_factory=CompressorConfig,
        description="压缩 worker 配置",
    )


# ---------------------------------------------------------------------------
# 顶层根模型（对应 config/background.toml）
# ---------------------------------------------------------------------------


class BackgroundRootConfig(BaseConfig):
    """Background 配置根类

    对应 ``config/background.toml`` 文件。
    """

    background: BackgroundConfig = Field(
        default_factory=BackgroundConfig,
        description="[background] 段聚合（轻循环 + 压缩 worker）",
    )


__all__ = [
    # 压缩 worker
    "CompressorConfig",
    # 聚合
    "BackgroundConfig",
    # 顶层根模型
    "BackgroundRootConfig",
]
