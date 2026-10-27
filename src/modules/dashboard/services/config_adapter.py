"""配置写路径的组件重建编排层。

配置视图构建、脱敏契约、写前校验与"校验 → 写盘 → 触发热重载"统一管线
在 ``src.modules.config.webui``；本模块只做 dashboard 侧的接力编排：
写盘成功后对组件域（agents / collectors）执行"停 → 按新配置重新实例化 →
启动"，并把统一管线结果翻译为 API 层的回执结构。
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Tuple

from src.modules.config.service import COMPONENT_REBUILD_SCOPES
from src.modules.config.webui import resolve_scope, write_config_updates
from src.modules.dashboard.services.component_runtime import (
    ComponentRuntimeContext,
    HotApplyReport,
    hot_apply_components,
)
from src.modules.logging import get_logger

if TYPE_CHECKING:
    from src.modules.config.service import ConfigService

logger = get_logger("ConfigAdapter")


@dataclass
class ConfigApplyOutcome:
    """一次配置写请求的编排结果（api 层负责翻译为 HTTP 响应）。"""

    success: bool
    message: str = ""
    requires_restart: bool = False
    # 单文件批次时携带实际写入的 TOML 文件名，供响应透出
    target_file: Optional[str] = None
    # last-wins 去重后的实际写入条目（key, value），供响应逐条回执
    applied: list[tuple[str, Any]] = field(default_factory=list)
    errors: list[Tuple[str, str]] = field(default_factory=list)
    # agents / collectors 域变更的组件重建热应用回执（未注入热应用上下文时为 None）
    hot_apply: Optional[HotApplyReport] = None


async def apply_config_updates(
    config_service: "ConfigService",
    config_dir: Path,
    changes: Sequence[Tuple[str, Any]],
    hot_context: Optional[ComponentRuntimeContext] = None,
) -> ConfigApplyOutcome:
    """配置写路径统一业务入口（单条与批量共用）。

    先走 config 包的统一管线（前置校验 → 写盘 → 热重载）；写盘成功后，
    注入 ``hot_context`` 且批次触碰组件域（agents / collectors，见
    ``COMPONENT_REBUILD_SCOPES``）时执行组件重建热应用（保存即生效）。
    任一前置校验失败即整体拒绝，磁盘零写入。
    """
    result = await write_config_updates(config_service, config_dir, changes)
    if not result.success:
        return ConfigApplyOutcome(success=False, message=result.message, errors=result.errors)

    scopes = [resolve_scope(k) for k, _ in result.normalized]
    hot_report = None
    if hot_context is not None and any(s in COMPONENT_REBUILD_SCOPES for s in scopes):
        keys = [k for k, _ in result.normalized]
        hot_report = await hot_apply_components(hot_context, config_service, config_dir, keys)
    # 热生效判真：组件域（agents / collectors）必须组件重建真的跑过（hot_report
    # 在场）才算——没传热应用上下文时照实报"需重启"，不虚报已生效
    hot_applied = result.reloaded and all(s in COMPONENT_REBUILD_SCOPES and hot_report is not None for s in scopes)
    return ConfigApplyOutcome(
        success=True,
        requires_restart=not hot_applied,
        target_file=result.target_file,
        applied=result.normalized,
        hot_apply=hot_report,
    )


async def sync_enabled_lists(
    config_service: "ConfigService",
    config_dir: Path,
    desired_by_group: Dict[str, List[str]],
) -> Optional[str]:
    """把采集器与 Agent 两组 enabled 名单一次写为期望全集（组件意图对齐的收尾写盘）。

    写入的是意图名单而非实际达成名单：个别实例启动/停止失败时名单照写，
    重启后按名单对齐——与单组件启停按钮的失败回退语义一致。不注入
    hot_context：实例层由调用方先行对齐完毕，避免重复重建。
    返回错误消息（None=成功）。
    """
    changes = [
        ("collectors.enabled", list(desired_by_group.get("collectors", []))),
        ("agents.agents.enabled", list(desired_by_group.get("agents", []))),
    ]
    outcome = await apply_config_updates(config_service, config_dir, changes)
    if not outcome.success:
        return outcome.message
    return None
