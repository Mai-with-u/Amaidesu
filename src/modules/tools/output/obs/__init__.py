"""OBS 工具模块（Wave 4 拆分）

将原 ``ObsControlHandler`` 拆为三个独立工具：
- ``obs_send_text``          - 发送文本到 OBS 文本源（含逐字效果）
- ``obs_switch_scene``       - 切换 OBS 场景
- ``obs_set_source_visibility`` - 控制源可见性

迁移策略（与 .omo/drafts/amaidesu-v2-migration.md A 段对齐）:
- 三个命令 verbatim 保留
- ``OUTPUT_OBS_COMMAND`` 事件被删除（事件映射删除项）：Dashboard
  直接调用 ``obs_*`` 工具
- ``obsws-python`` 软降级不变
- ``ConfigSchema`` 字段 verbatim 保留
"""

from .obs_provider import OBSProvider, create_obs_provider, register_obs_tools

__all__ = [
    "OBSProvider",
    "create_obs_provider",
    "register_obs_tools",
]
