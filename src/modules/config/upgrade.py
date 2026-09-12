"""每文件版本推进 + 升级钩子注册表

版本流按文件独立递进：读 ``[meta].version`` → 低于基线时跑该文件的钩子链
→ 写回推进。钩子分两类：

- **FileUpgradeHook**：单文件钩子，原地改 dict、返回变更路径列表、幂等。
- **CrossFileHook**：跨文件钩子——声明 ``target_file``；运行时同时拿到宿主
  与目标两个文件的 dict，变更由调度器双写并双版本同升（无独立预通道）。

调度采用**区间语义**：``old < hook.target <= baseline`` 的钩子执行，链跑完
后版本戳推进到基线。注册表当前为空表——历史钩子已随旧体系全量清除，新钩
子随未来的结构变更在此登记。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Protocol

from src.modules.config.errors import ConfigValidationError
from src.modules.config.file_meta import CONFIG_BASELINE_VERSION
from src.modules.logging import get_logger

logger = get_logger("ConfigUpgrade")


class FileUpgradeHook(Protocol):
    """单文件升级钩子协议

    实现须满足：
    - **原地修改**传入的配置 dict（返回值不是数据载体）
    - 返回本次实际变更的键路径列表（点分，如 ``"agents.streamer.x"``）
    - **幂等**：对已迁移的数据重复执行不再产生变更
    """

    name: str
    target_version: str

    def __call__(self, data: Dict[str, Any]) -> List[str]: ...


class CrossFileHook(Protocol):
    """跨文件升级钩子协议（声明 target_file；双写 + 双版本同升）

    在宿主文件的钩子链中登记；运行时同时拿到宿主与目标文件的原始 dict，
    可两端改写。目标文件的版本由调度器同步推进到基线。
    """

    name: str
    target_version: str
    target_file: str

    def __call__(self, host_data: Dict[str, Any], target_data: Dict[str, Any]) -> List[str]: ...


@dataclass(frozen=True)
class _VersionedHook:
    """调度视角的钩子条目（宿主文件 + 运行体 + 目标文件可空）"""

    host_file: str
    name: str
    target_version: str
    run: Callable[..., List[str]]
    target_file: str | None  # None = 单文件钩子；否则为跨文件钩子


# 升级钩子注册表（当前为空表：历史钩子已清除，登记处从零开始）
_FILE_HOOKS: Dict[str, List[_VersionedHook]] = {}
# 演示样例钩子只存在于测试中（见 tests/config/test_upgrade.py），不注册生效


def register_file_hook(
    file_name: str, name: str, target_version: str, run: Callable[[Dict[str, Any]], List[str]]
) -> None:
    """登记单文件升级钩子（调度按 target_version 升序执行）"""
    hook = _VersionedHook(host_file=file_name, name=name, target_version=target_version, run=run, target_file=None)
    _FILE_HOOKS.setdefault(file_name, []).append(hook)


def register_cross_file_hook(
    host_file: str,
    target_file: str,
    name: str,
    target_version: str,
    run: Callable[[Dict[str, Any], Dict[str, Any]], List[str]],
) -> None:
    """登记跨文件升级钩子：宿主链上执行，可同时改写目标文件"""
    hook = _VersionedHook(
        host_file=host_file, name=name, target_version=target_version, run=run, target_file=target_file
    )
    _FILE_HOOKS.setdefault(host_file, []).append(hook)


def _version_tuple(version: str) -> tuple[int, ...]:
    """版本号 → 可比较元组（"2.0.31" → (2, 0, 31)）；解析失败按 0 处理"""
    try:
        return tuple(int(part) for part in version.split("."))
    except (ValueError, AttributeError):
        return (0, 0, 0)


def advance_file_versions(raw_docs: Dict[str, Dict[str, Any]]) -> Dict[str, List[str]]:
    """版本推进调度（加载管线阶段②③）。

    逐文件：版本缺失硬错；区间 ``old < target <= baseline`` 内的钩子依次
    执行；链毕把版本戳推进到基线。跨文件钩子执行时目标文件版本同步推进
    （双版本同升），即使目标文件自身无待跑钩子。

    Args:
        raw_docs: 文件名 → 原始配置 dict（阶段①产物；原地修改）

    Returns:
        文件名 → 本次推进产生的变更路径列表（仅含发生推进的文件）

    Raises:
        ValueError: 任一存在的文件缺 ``[meta].version`` 字段
    """
    baseline = _version_tuple(CONFIG_BASELINE_VERSION)
    changed: Dict[str, List[str]] = {}

    for file_name, data in raw_docs.items():
        meta = data.get("meta")
        if not isinstance(meta, dict) or not meta.get("version"):
            raise ConfigValidationError(file_name, "meta.version", "缺少版本字段（每文件版本为硬性要求）")
        old_version = str(meta["version"])
        old = _version_tuple(old_version)
        if old >= baseline:
            continue

        file_changed: List[str] = []
        hooks = sorted(_FILE_HOOKS.get(file_name, []), key=lambda h: _version_tuple(h.target_version))
        for hook in hooks:
            if not (old < _version_tuple(hook.target_version) <= baseline):
                continue
            if hook.target_file is None:
                file_changed.extend(hook.run(data))
            else:
                target_data = raw_docs.get(hook.target_file)
                if target_data is None:
                    logger.warning(f"跨文件钩子 {hook.name} 的目标文件 {hook.target_file} 不存在，跳过")
                    continue
                file_changed.extend(hook.run(data, target_data))
                # 双版本同升：目标文件版本同步推进到基线
                target_meta = target_data.get("meta")
                if isinstance(target_meta, dict):
                    target_meta["version"] = CONFIG_BASELINE_VERSION
                changed.setdefault(hook.target_file, []).append("meta.version")

        # 宿主文件版本推进到基线
        data["meta"]["version"] = CONFIG_BASELINE_VERSION
        file_changed.append("meta.version")
        changed[file_name] = file_changed
        logger.info(
            f"{file_name} 版本推进: {old_version} → {CONFIG_BASELINE_VERSION}"
            + (f"（钩子变更 {len(file_changed) - 1} 处）" if len(file_changed) > 1 else "（无钩子，仅版本戳）")
        )

    return changed


__all__ = [
    "CONFIG_BASELINE_VERSION",
    "FileUpgradeHook",
    "CrossFileHook",
    "register_file_hook",
    "register_cross_file_hook",
    "advance_file_versions",
]
