"""直播自动切片的配置 Schema（infra.toml ``[clipping]`` 段）。"""

from pydantic import Field

from src.modules.config.schemas.base import BaseConfig


class ClippingConfigSchema(BaseConfig):
    """直播切片配置 Schema

    切片流程：下播后 AI 基于本场事件时间线提议值得切片的时段，运营在 WebUI
    微调后调用本机 ffmpeg 把片段从录播文件中切出。ffmpeg 以独立进程调用、
    由使用者在本地安装配置路径，不随本项目分发。
    """

    enabled: bool = Field(
        default=False,
        title="是否启用",
        description="启用切片功能（false 时不订阅下播事件、切片页不可用）",
    )
    recording_dir: str = Field(
        default="",
        title="录播目录",
        description="录播文件所在目录（任意录制软件：文件名带录制开始时刻即可自动匹配场次，如录播姬的 录制-房间号-日期-时间-标题.flv 或 OBS 的 2026-11-08 20-00-00.mkv）；空串 = 未配置",
    )
    ffmpeg_path: str = Field(
        default="",
        title="ffmpeg 路径",
        description="ffmpeg 可执行文件路径；空串 = 使用 PATH 上能找到的 ffmpeg",
    )
    output_dir: str = Field(
        default="",
        title="切片输出目录",
        description="切片产物存放目录；空串 = 录播目录下的 切片 子目录",
    )
    llm_profile: str = Field(
        default="settle",
        title="LLM 档位",
        description="AI 选段用的 LLM 档位名（model.toml profiles 的引用名）",
    )
    reencode: bool = Field(
        default=False,
        title="重编码切片",
        description="false = 流复制（-c copy，秒级出片，边界按关键帧对齐可能有几秒偏差）；true = 重编码（边界精确到帧，速度接近实时）",
    )
    min_segment_seconds: int = Field(
        default=30,
        ge=5,
        title="最短片段秒数",
        description="AI 提议片段的最短时长（秒），过短的提议会被拒绝",
    )
    max_segment_seconds: int = Field(
        default=300,
        ge=10,
        title="最长片段秒数",
        description="AI 提议片段的最长时长（秒），超长的提议会被截断",
    )


__all__ = ["ClippingConfigSchema"]
