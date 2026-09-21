"""Avatar 配置 Schema 定义

定义 ``config/avatar.toml`` 的 Pydantic 聚合模型——avatar 相关配置统一放在这一个文件。

段树结构（TOML 视角）::

    [avatar.platform]
    enabled = ["vts"]

    [avatar.platform.vts]
    vts_host = "localhost"
    vts_port = 8001
    # ...（VTSProvider.ConfigSchema 字段直接铺键）

    [avatar.lipsync]
    enabled = true
    # ...（口型分析共享件调参）

结构规则：
- ``platform`` 组段放 ``enabled`` 启用名单（对齐 agents/collectors 域根
  名单约定）；成员段直接铺参数键——没有 tools.toml 动态键机制的
  ``.config`` 中间层，段位即归属；
- 成员段 typed 引用各平台包内 ConfigSchema（静态命名段，平台名封闭
  三值：vts / warudo / vrchat，与代码目录 ``src/modules/avatar/platform/``
  镜像）；包内 Schema 是字段与默认值的唯一权威；
- ``lipsync`` 与 ``platform`` 平级（口型分析共享件，非平台成员）；
  将来 avatar 新部件 = 新的第二段，以部件本名命名。
"""

from __future__ import annotations

from typing import List

from pydantic import ConfigDict, Field

from src.modules.avatar.lipsync import LipSyncConfig
from src.modules.avatar.platform.vrchat.vrchat_provider import VRChatProvider
from src.modules.avatar.platform.vts.vts_provider import VTSProvider
from src.modules.avatar.platform.warudo.warudo_provider import WarudoProvider
from src.modules.config.file_meta import FileMetaConfig
from src.modules.config.schemas.base import BaseConfig

# 平台成员名合法清单（与注册表键 ("avatar", <名>) 的提供者位、bootstrap
# 成员表、代码目录三方同源；名单出现表外名字属拼写错误，加载期硬错）
PLATFORM_NAMES: tuple[str, ...] = ("vts", "warudo", "vrchat")


class AvatarPlatformConfig(BaseConfig):
    """皮套平台组段（``[avatar.platform]``）

    ``enabled`` 名单管哪些平台参与装配；成员段缺省 = 该平台以全默认
    配置待命（写回补出），段存在但不在名单 = 声明但不装配。
    ``extra="allow"``：注册表外名字的残留段原样保留（加载分支 warning
    提示人工复核），不静默丢弃用户数据。
    """

    model_config = ConfigDict(extra="allow")

    enabled: List[str] = Field(
        default_factory=lambda: ["vts"],
        description="启用的皮套平台名单（合法名：vts / warudo / vrchat）",
    )
    vts: VTSProvider.ConfigSchema = Field(
        default_factory=VTSProvider.ConfigSchema,
        description="VTubeStudio 平台配置（连接 / 表情基线 / idle 绑定）",
    )
    warudo: WarudoProvider.ConfigSchema = Field(
        default_factory=WarudoProvider.ConfigSchema,
        description="Warudo 平台配置（WebSocket 连接 / TalkingHead / 动作目录）",
    )
    vrchat: VRChatProvider.ConfigSchema = Field(
        default_factory=VRChatProvider.ConfigSchema,
        description="VRChat 平台配置（OSC 主机与输出端口）",
    )


class AvatarRootConfig(BaseConfig):
    """Avatar 配置根类（对应 ``config/avatar.toml`` 文件）"""

    __file_name__ = "avatar.toml"
    __section_label__ = "皮套"

    meta: FileMetaConfig = Field(default_factory=FileMetaConfig, description="文件元数据")
    platform: AvatarPlatformConfig = Field(
        default_factory=AvatarPlatformConfig,
        description="皮套平台（启用名单 + 各平台成员段）",
    )
    lipsync: LipSyncConfig = Field(
        default_factory=LipSyncConfig,
        description="口型分析共享件调参（平台无关，分接 TTS 播放音频）",
    )


__all__ = [
    "PLATFORM_NAMES",
    "AvatarPlatformConfig",
    "AvatarRootConfig",
]
