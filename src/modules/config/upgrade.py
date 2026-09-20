"""每文件版本推进 + 升级钩子注册表

版本流按文件独立递进：读 ``[meta].version`` → 跑该文件所有 ``old < target``
的钩子 → 版本戳推进到最后一个已执行钩子的 target。没有适用钩子的文件版本
保持原值——一个文件升版本绝不带动其他文件的版本戳。钩子分两类：

- **FileUpgradeHook**：单文件钩子，原地改 dict、返回变更路径列表、幂等。
- **CrossFileHook**：跨文件钩子——声明 ``target_file``；运行时同时拿到宿主
  与目标两个文件的 dict，变更由调度器双写，目标文件版本推进到该钩子的
  target（不回退已高于 target 的目标文件）。

调度不存在全局版本上界：文件版本超前于全部钩子时自然零变更。新钩子随
结构变更在此登记。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Protocol

from src.modules.config.errors import ConfigValidationError
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


# 升级钩子注册表
_FILE_HOOKS: Dict[str, List[_VersionedHook]] = {}
# 演示样例钩子只存在于测试中（见 tests/config/test_upgrade.py），不注册生效


def _upgrade_agents_force_rename(data: Dict[str, Any]) -> List[str]:
    """agents.toml v2.0.32：force 段字段正名 + 死字段清理。

    ``force_data_types`` 重命名为 ``force_message_types``（与 TimingGate 构造参数
    名对齐）；``force_importance`` 经核实为零消费者死字段（TimingGate 只收类型
    列表，无 importance 数值路径），一并删除。对已迁移数据零变更（幂等）。
    """
    streamer = (data.get("agents") or {}).get("streamer")
    force = streamer.get("force") if isinstance(streamer, dict) else None
    if not isinstance(force, dict):
        return []
    changed: List[str] = []
    if "force_data_types" in force:
        force["force_message_types"] = force.pop("force_data_types")
        changed.append("agents.streamer.force.force_data_types -> force_message_types")
    if "force_importance" in force:
        del force["force_importance"]
        changed.append("agents.streamer.force.force_importance")
    return changed


def _upgrade_infra_drop_events_persist(data: Dict[str, Any]) -> List[str]:
    """infra.toml v2.0.33：删 [events].persist 键。

    event_history 表持久化路径整体移除（表 DROP + 服务纯内存化），
    persist 开关失去消费者。对已迁移数据零变更（幂等）。
    """
    events = data.get("events")
    if not isinstance(events, dict) or "persist" not in events:
        return []
    del events["persist"]
    return ["events.persist"]


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


# 生产钩子登记：agents.toml v2.0.32（force 段字段正名 + 死字段清理）
register_file_hook("agents.toml", "agents_force_field_rename", "2.0.32", _upgrade_agents_force_rename)

# 生产钩子登记：infra.toml v2.0.33（[events].persist 删键，表持久化路径移除）
register_file_hook("infra.toml", "infra_drop_events_persist", "2.0.33", _upgrade_infra_drop_events_persist)


def _drop_simulator_llm_profile(data: Dict[str, Any]) -> List[str]:
    """删除 ``[simulator].llm_profile``（profile 绑定改由代码显式常量声明）"""
    simulator = data.get("simulator")
    if isinstance(simulator, dict) and "llm_profile" in simulator:
        del simulator["llm_profile"]
        return ["simulator.llm_profile"]
    return []


register_file_hook("infra.toml", "drop_simulator_llm_profile", "2.0.32", _drop_simulator_llm_profile)


def _drop_window_event_threshold(data: Dict[str, Any]) -> List[str]:
    """删除 ``[agents.streamer.background].window_event_threshold``（窗口压缩未实现，字段无消费者）"""
    streamer = (data.get("agents") or {}).get("streamer")
    background = streamer.get("background") if isinstance(streamer, dict) else None
    if isinstance(background, dict) and "window_event_threshold" in background:
        del background["window_event_threshold"]
        return ["agents.streamer.background.window_event_threshold"]
    return []


register_file_hook("agents.toml", "drop_window_event_threshold", "2.0.33", _drop_window_event_threshold)


def _drop_text_adv_fake_knobs(data: Dict[str, Any]) -> List[str]:
    """agents.toml v2.0.34：删 [agents.text_adv] 三个无消费者假旋钮。

    ``engine_kind`` / ``decision_strategy`` / ``enable_event_emission`` 经核实
    均无有效消费路径（Agent 行为不再由配置切换），一并删除。对已迁移数据
    零变更（幂等）。
    """
    agents = data.get("agents")
    text_adv = agents.get("text_adv") if isinstance(agents, dict) else None
    if not isinstance(text_adv, dict):
        return []
    changed: List[str] = []
    for key in ("engine_kind", "decision_strategy", "enable_event_emission"):
        if key in text_adv:
            del text_adv[key]
            changed.append(f"agents.text_adv.{key}")
    return changed


# 生产钩子登记：agents.toml v2.0.34（text_adv 假旋钮删字段）
register_file_hook("agents.toml", "drop_text_adv_fake_knobs", "2.0.34", _drop_text_adv_fake_knobs)


def _upgrade_builder_scene_transport(data: Dict[str, Any]) -> List[str]:
    """设计与施工复用 Mod 受理通道，独立校验入口不再有消费者。"""
    builder = ((data.get("agents") or {}).get("minecraft") or {}).get("builder")
    if not isinstance(builder, dict):
        return []
    # 自定义旧协议不能被猜测映射，保留原文件并提示显式对齐，而不是悄悄丢掉绑定。
    for name, default in (("validate_tool", "builder_validate"), ("preview_tool", "")):
        if name in builder and builder[name] != default:
            raise ValueError(f"agents.minecraft.builder.{name} 使用自定义旧协议，请先对齐场景操作接口")
    changed: List[str] = []
    for name in ("validate_tool", "preview_tool"):
        if name in builder:
            del builder[name]
            changed.append(f"agents.minecraft.builder.{name}")
    if builder.get("execute_tool") == "builder_execute":
        builder["execute_tool"] = "maicraft_execute"
        changed.append("agents.minecraft.builder.execute_tool")
    return changed


register_file_hook("agents.toml", "builder_scene_transport", "2.0.35", _upgrade_builder_scene_transport)


# 口型调参键：tools.toml [tools.avatar.vts].config → infra.toml [avatar.lipsync]
# （口型分析器升为共享基础设施，调参随其架构类别离开 provider 命名空间；
# 开关键 lip_sync_enabled 正名为 enabled；其余键原样搬迁不改名）
_LIPSYNC_KEYS = (
    "sample_rate",
    "volume_threshold",
    "smoothing_factor",
    "vowel_detection_sensitivity",
    "volume_gain",
    "max_mouth_open",
    "silence_threshold",
    "close_mouth_threshold",
    "power_curve",
    "vowel_open_weight",
    "update_interval_ms",
    "mouth_open_lerp_speed",
    "vowel_decay",
    "min_mouth_delta",
)


def _upgrade_vts_lipsync_to_infra(host_data: Dict[str, Any], target_data: Dict[str, Any]) -> List[str]:
    """tools.toml v2.0.36：VTS lip-sync 调参键跨文件搬至 infra [avatar.lipsync]。

    用户显式设置值原样搬迁（不改名、不改值）；仅值等于旧默认的键同样搬迁
    （口径统一：VTS provider 不再消费这些键，留在原处即成死键）。开关键
    ``lip_sync_enabled`` 正名为 ``enabled``。对已迁移数据零变更（幂等）。
    """
    tools = host_data.get("tools")
    avatar = tools.get("avatar") if isinstance(tools, dict) else None
    vts = avatar.get("vts") if isinstance(avatar, dict) else None
    vts_config = vts.get("config") if isinstance(vts, dict) else None
    if not isinstance(vts_config, dict):
        return []

    changed: List[str] = []
    target_avatar = target_data.setdefault("avatar", {})
    if not isinstance(target_avatar, dict):
        target_avatar = {}
        target_data["avatar"] = target_avatar
    lipsync = target_avatar.setdefault("lipsync", {})
    if not isinstance(lipsync, dict):
        lipsync = {}
        target_avatar["lipsync"] = lipsync

    # 直接赋值：目标段只可能带新字段的基线默认落盘，同一名下用户的唯一
    # 配置事实在源键（tools 侧旧字段），搬迁即覆盖
    if "lip_sync_enabled" in vts_config:
        lipsync["enabled"] = vts_config.pop("lip_sync_enabled")
        changed.append("tools.avatar.vts.config.lip_sync_enabled -> avatar.lipsync.enabled")
    for key in _LIPSYNC_KEYS:
        if key in vts_config:
            lipsync[key] = vts_config.pop(key)
            changed.append(f"tools.avatar.vts.config.{key} -> avatar.lipsync.{key}")
    return changed


# 生产钩子登记：tools.toml v2.0.36（VTS lip-sync 键跨文件迁 infra [avatar.lipsync]）
register_cross_file_hook("tools.toml", "infra.toml", "vts_lipsync_to_infra", "2.0.36", _upgrade_vts_lipsync_to_infra)


def _drop_warudo_subtitle_keys(data: Dict[str, Any]) -> List[str]:
    """tools.toml v2.0.37：删 [tools.avatar.warudo].config 三个字幕键。

    Warudo 8766 字幕面整体删除（与 Dashboard /subtitle 页重叠），字幕收敛
    为"一源 → 三面"；三键失去消费者。对已迁移数据零变更（幂等）。
    """
    tools = data.get("tools")
    avatar = tools.get("avatar") if isinstance(tools, dict) else None
    warudo = avatar.get("warudo") if isinstance(avatar, dict) else None
    warudo_config = warudo.get("config") if isinstance(warudo, dict) else None
    if not isinstance(warudo_config, dict):
        return []
    changed: List[str] = []
    for key in ("subtitle_enabled", "subtitle_port", "subtitle_show_status"):
        if key in warudo_config:
            del warudo_config[key]
            changed.append(f"tools.avatar.warudo.config.{key}")
    return changed


# 生产钩子登记：tools.toml v2.0.37（Warudo 字幕三键删除，8766 面退役）
register_file_hook("tools.toml", "drop_warudo_subtitle_keys", "2.0.37", _drop_warudo_subtitle_keys)


def _version_tuple(version: str) -> tuple[int, ...]:
    """版本号 → 可比较元组（"2.0.31" → (2, 0, 31)）；解析失败按 0 处理"""
    try:
        return tuple(int(part) for part in version.split("."))
    except (ValueError, AttributeError):
        return (0, 0, 0)


def advance_file_versions(raw_docs: Dict[str, Dict[str, Any]]) -> Dict[str, List[str]]:
    """版本推进调度（加载管线阶段②③）。

    逐文件：版本缺失硬错；所有 ``old < target`` 的钩子依次执行；链毕把版本
    戳推进到最后一个已执行钩子的 target。没有适用钩子的文件版本保持原值，
    不产生变更记录。跨文件钩子执行时目标文件版本推进到该钩子的 target
    （已高于 target 的目标文件不回退）。

    Args:
        raw_docs: 文件名 → 原始配置 dict（阶段①产物；原地修改）

    Returns:
        文件名 → 本次推进产生的变更路径列表（仅含真发生变更的文件）

    Raises:
        ValueError: 任一存在的文件缺 ``[meta].version`` 字段
    """
    changed: Dict[str, List[str]] = {}

    for file_name, data in raw_docs.items():
        meta = data.get("meta")
        if not isinstance(meta, dict) or not meta.get("version"):
            raise ConfigValidationError(file_name, "meta.version", "缺少版本字段（每文件版本为硬性要求）")
        old_version = str(meta["version"])
        old = _version_tuple(old_version)

        file_changed: List[str] = []
        last_target: str | None = None
        hooks = sorted(_FILE_HOOKS.get(file_name, []), key=lambda h: _version_tuple(h.target_version))
        for hook in hooks:
            if not (old < _version_tuple(hook.target_version)):
                continue
            last_target = hook.target_version
            if hook.target_file is None:
                file_changed.extend(hook.run(data))
            else:
                target_data = raw_docs.get(hook.target_file)
                if target_data is None:
                    logger.warning(f"跨文件钩子 {hook.name} 的目标文件 {hook.target_file} 不存在，跳过")
                    continue
                target_changed = hook.run(data, target_data)
                file_changed.extend(target_changed)
                # 双版本同升：目标文件推进到该钩子的 target；已超前的目标文件不回退
                target_meta = target_data.get("meta")
                if isinstance(target_meta, dict):
                    target_old = str(target_meta.get("version", ""))
                    if _version_tuple(target_old) < _version_tuple(hook.target_version):
                        target_meta["version"] = hook.target_version
                        changed.setdefault(hook.target_file, []).append("meta.version")
                        logger.info(
                            f"{hook.target_file} 版本推进（跨文件钩子 {hook.name}）: {target_old} → {hook.target_version}"
                        )
                    elif target_changed:
                        # 版本已在基线（不推进）但钩子改写了数据 → 仍标记目标文件需写回，
                        # 否则搬迁值停留在内存、磁盘保留基线默认
                        changed.setdefault(hook.target_file, [])
                        logger.info(f"{hook.target_file} 数据变更（跨文件钩子 {hook.name}，版本已在基线不推进）")

        if last_target is None:
            continue

        # 宿主文件推进到最后一个已执行钩子的 target
        data["meta"]["version"] = last_target
        file_changed.append("meta.version")
        changed[file_name] = file_changed
        logger.info(
            f"{file_name} 版本推进: {old_version} → {last_target}"
            + (f"（钩子变更 {len(file_changed) - 1} 处）" if len(file_changed) > 1 else "（钩子无数据变更，仅版本戳）")
        )

    return changed


__all__ = [
    "FileUpgradeHook",
    "CrossFileHook",
    "register_file_hook",
    "register_cross_file_hook",
    "advance_file_versions",
]
