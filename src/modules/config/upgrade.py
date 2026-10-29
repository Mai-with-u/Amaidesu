"""每文件版本推进 + 升级钩子注册表

版本流按文件独立递进：读 ``[meta].version`` → 跑该文件所有 ``old < target``
的钩子 → 版本戳推进到最后一个已执行钩子的 target。没有适用钩子的文件版本
保持原值——一个文件升版本绝不带动其他文件的版本戳。钩子分两类：

- **FileUpgradeHook**：单文件钩子，原地改 dict、返回变更路径列表、幂等。
- **CrossFileHook**：跨文件钩子——声明 ``target_file``；运行时同时拿到宿主
  与目标两个文件的 dict，变更由调度器双写，目标文件版本推进到该钩子的
  target（不回退已高于 target 的目标文件）。

调度不存在全局版本上界：文件版本超前于全部钩子时自然零变更。新钩子随
结构变更在此登记。钩子的 ``target_version`` 是该文件自身版本流内的坐标，
新钩子取该文件链上界的下一位即可，与主程序发布版本号无关——配置版本
流与发布版本同自 2.0.0 起步、此后各自独立递进，下方历史钩子的 target
数字与发布版本号相近只是同起点独立演进的自然结果，两套序列无关。

**钩子的定义面分两档**：

- 大多数钩子是"导航到嵌套段 → 删键/整段删除/按旧默认改写/键改名 → 返回
  变更路径"的同构体，用本模块的声明式词汇（``drop_section_keys`` /
  ``drop_section`` / ``rename_key`` / ``swap_default`` / ``chain``）按数据表
  形态构造，登记处的名字即测试与 grep 的锚点；
- 真正定制的钩子（跨文件搬迁、带警告的值换算、归一化枚举等）保留为函数。
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
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


# ---------------------------------------------------------------------------
# 声明式钩子词汇：同构迁移体的解释器。每个工厂返回一个满足 FileUpgradeHook
# 语义的闭包（原地修改、返回变更路径、幂等），供登记处按数据表形态组合。
# ---------------------------------------------------------------------------


def _navigate(data: Dict[str, Any], dotted_path: str) -> Dict[str, Any] | None:
    """沿点分路径逐层取嵌套表；任一层缺失或不是 dict 时返回 None"""
    section: Any = data
    for part in dotted_path.split("."):
        section = section.get(part) if isinstance(section, dict) else None
        if section is None:
            return None
    return section if isinstance(section, dict) else None


def drop_section_keys(section_path: str, keys: tuple[str, ...]) -> Callable[[Dict[str, Any]], List[str]]:
    """删除指定段内的若干键；段缺失时零变更。

    变更路径记作 ``<section_path>.<key>``。与"按旧默认改写"钩子的区别：
    这里不看值——退役键无论用户改没改过都失去消费者，一律删除。
    """

    def run(data: Dict[str, Any]) -> List[str]:
        section = _navigate(data, section_path)
        if section is None:
            return []
        changed = [f"{section_path}.{key}" for key in keys if key in section]
        for key in keys:
            section.pop(key, None)
        return changed

    return run


def drop_section(section_path: str) -> Callable[[Dict[str, Any]], List[str]]:
    """整段删除（退役段留在文件里即属未知段硬错，原地移除）；段缺失时零变更"""

    def run(data: Dict[str, Any]) -> List[str]:
        parent = _navigate(data, section_path.rsplit(".", 1)[0])
        key = section_path.rsplit(".", 1)[1]
        if parent is None or key not in parent:
            return []
        del parent[key]
        return [section_path]

    return run


def rename_key(section_path: str, old_key: str, new_key: str) -> Callable[[Dict[str, Any]], List[str]]:
    """段内键改名（值原样搬迁）；变更路径记作 ``<section_path>.<old> -> <new>``"""

    def run(data: Dict[str, Any]) -> List[str]:
        section = _navigate(data, section_path)
        if section is None or old_key not in section:
            return []
        section[new_key] = section.pop(old_key)
        return [f"{section_path}.{old_key} -> {new_key}"]

    return run


def swap_default(
    path: str, old_default: Any, new_default: Any, *, annotate: bool = True
) -> Callable[[Dict[str, Any]], List[str]]:
    """仅当落盘值等于旧默认（用户未显式改过）时改写为新默认，用户显式值不动。

    变更路径默认带值注记（``<path>: <old> -> <new>``）；``annotate=False``
    时只记路径——旧/新值本身冗长（URL、整句人设指令）时用后者。
    """
    parent_path, _, key = path.rpartition(".")

    def run(data: Dict[str, Any]) -> List[str]:
        parent = _navigate(data, parent_path)
        if parent is None or parent.get(key) != old_default:
            return []
        parent[key] = new_default
        if annotate:
            return [f"{path}: {old_default} -> {new_default}"]
        return [path]

    return run


def chain(*runs: Callable[..., List[str]]) -> Callable[..., List[str]]:
    """按序执行多个钩子体，变更路径列表首尾相接（组合声明式词汇用）"""

    def run(*args: Any) -> List[str]:
        changed: List[str] = []
        for one in runs:
            changed.extend(one(*args))
        return changed

    return run


# ---------------------------------------------------------------------------
# 生产钩子登记（声明式数据表 + 定制函数）
# ---------------------------------------------------------------------------

# agents.toml v2.0.32：force 段字段正名 + 死字段清理。
# ``force_data_types`` 重命名为 ``force_message_types``（与 Agent 侧读的强制
# 名单字段名对齐）；``force_importance`` 经核实为零消费者死字段（强制触发
# 只看消息类型集合归属，无 importance 数值路径），一并删除。
_upgrade_agents_force_rename = chain(
    rename_key("agents.streamer.force", "force_data_types", "force_message_types"),
    drop_section_keys("agents.streamer.force", ("force_importance",)),
)

# 生产钩子登记：agents.toml v2.0.32（force 段字段正名 + 死字段清理）
register_file_hook("agents.toml", "agents_force_field_rename", "2.0.32", _upgrade_agents_force_rename)

# infra.toml v2.0.33：删 [events].persist 键。event_history 表持久化路径整体
# 移除（表 DROP + 服务纯内存化），persist 开关失去消费者。
_upgrade_infra_drop_events_persist = drop_section_keys("events", ("persist",))

# 生产钩子登记：infra.toml v2.0.33（[events].persist 删键，表持久化路径移除）
register_file_hook("infra.toml", "infra_drop_events_persist", "2.0.33", _upgrade_infra_drop_events_persist)

# 删除 ``[simulator].llm_profile``（profile 绑定改由代码显式常量声明）
_drop_simulator_llm_profile = drop_section_keys("simulator", ("llm_profile",))

register_file_hook("infra.toml", "drop_simulator_llm_profile", "2.0.32", _drop_simulator_llm_profile)

# 删除 ``[agents.streamer.background].window_event_threshold``（窗口压缩未实现，字段无消费者）
_drop_window_event_threshold = drop_section_keys("agents.streamer.background", ("window_event_threshold",))

register_file_hook("agents.toml", "drop_window_event_threshold", "2.0.33", _drop_window_event_threshold)

# agents.toml v2.0.34：删 [agents.text_adv] 三个无消费者假旋钮。
# ``engine_kind`` / ``decision_strategy`` / ``enable_event_emission`` 经核实
# 均无有效消费路径（Agent 行为不再由配置切换），一并删除。
_drop_text_adv_fake_knobs = drop_section_keys(
    "agents.text_adv", ("engine_kind", "decision_strategy", "enable_event_emission")
)

# 生产钩子登记：agents.toml v2.0.34（text_adv 假旋钮删字段）
register_file_hook("agents.toml", "drop_text_adv_fake_knobs", "2.0.34", _drop_text_adv_fake_knobs)

# infra.toml v2.0.41：删 ``[simulator].sc_probability`` 死键。SC 触发实际走
# 付费分支的 ``sc_pay_probability``，该键自付费分支引入后再无业务消费（唯一
# 消费方是面板展示），删除。
_drop_simulator_sc_probability = drop_section_keys("simulator", ("sc_probability",))

register_file_hook("infra.toml", "drop_simulator_sc_probability", "2.0.41", _drop_simulator_sc_probability)

# infra.toml v2.0.42：运行日志收进 ``logs/app/`` 子目录。``logs/`` 根改为只放
# 平铺子目录（app / llm_requests / llm_context / events / tts / webui），运行
# 日志落盘目录相应内收。仅改写值等于旧默认 ``"logs"`` 的落盘值，用户显式
# 自设的其他目录原样保留。
_upgrade_logging_directory_subdir = swap_default("logging.directory", "logs", "logs/app")

# 生产钩子登记：infra.toml v2.0.42（[logging].directory 收进子目录）
register_file_hook("infra.toml", "logging_directory_subdir", "2.0.42", _upgrade_logging_directory_subdir)


def _upgrade_builder_scene_transport(data: Dict[str, Any]) -> List[str]:
    """设计与施工复用 Mod 受力通道，独立校验入口不再有消费者。"""
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

# 游戏主任务持续推进到交付或实际阻塞，清理会使长任务中途挂起的旧步数配置
# （父玩家的累计步数不再决定任务终止；建筑设计子任务的独立配置仍归子 Agent 使用）
_drop_minecraft_max_steps = drop_section_keys("agents.minecraft", ("max_steps",))

register_file_hook("agents.toml", "drop_minecraft_max_steps", "2.0.39", _drop_minecraft_max_steps)


def _drop_config_paths(data: Dict[str, Any], paths: tuple[str, ...]) -> List[str]:
    """清理已移除的输入裁剪选项，保留同段的业务配置和摘要参数。"""
    changed: List[str] = []
    for path in paths:
        parts = path.split(".")
        section = data
        for part in parts[:-1]:
            section = section.get(part) if isinstance(section, dict) else None
        if isinstance(section, dict) and parts[-1] in section:
            del section[parts[-1]]
            changed.append(path)
    return changed


def _drop_model_generation_limits(data: Dict[str, Any]) -> List[str]:
    """模型生成不再受宿主额度或时钟终止，旧自定义上限一并退役。"""
    changed: List[str] = []
    profiles = data.get("llm_profiles")
    if isinstance(profiles, dict):
        for name, profile in profiles.items():
            if isinstance(profile, dict):
                changed.extend(
                    f"llm_profiles.{name}.{path}"
                    for path in _drop_config_paths(profile, ("max_tokens", "hard_timeout_ms"))
                )
    for index, provider in enumerate(data.get("llm_providers") or []):
        if isinstance(provider, dict):
            changed.extend(
                f"llm_providers[{index}].{path}" for path in _drop_config_paths(provider, ("max_tokens", "timeout"))
            )
    return changed


# 直播历史与建造资料完整读取，任务历史的自动摘要配置继续保留
_drop_agent_input_limits = partial(
    _drop_config_paths,
    paths=(
        "agents.streamer.history_limit",
        "agents.minecraft.builder.max_resource_chars",
        "agents.minecraft.context.observation_inline_chars",
        "agents.minecraft.context.archive_max_chars",
    ),
)

# 模拟观众使用完整场次上下文，发言不再按字符数裁短
_drop_simulator_text_limits = partial(
    _drop_config_paths, paths=("simulator.context_window_size", "simulator.max_message_chars")
)

# 网页返回完整正文，视觉识别默认使用原图并等待模型完成
_drop_tool_input_limits = partial(
    _drop_config_paths,
    paths=(
        "tools.web.search.config.max_fetch_chars",
        "tools.vision.config.vlm_timeout_ms",
        "tools.vision.config.default_max_width",
    ),
)

# agents.toml v2.0.40：删 [agents.minecraft.builder] 残段。独立校验/预览入口
# 已随场景施工迁移删除（builder_scene_transport 只处理三个协议键），残余键
# （max_steps 等）亦无消费者，整段退役。
_drop_minecraft_builder_section = drop_section("agents.minecraft.builder")

register_file_hook("agents.toml", "drop_minecraft_builder_section", "2.0.40", _drop_minecraft_builder_section)


def _normalize_llm_model_cache_mode(data: Dict[str, Any]) -> List[str]:
    """model.toml v2.0.43：``[[llm_models]].cache`` 开关串归一为显式枚举。

    旧形态是空串/任意非空串当布尔开关用（仅 truthy 判断，无类型分支消费）；
    新值域封闭为 none（全价）/ hit_discount（命中按缓存价分段）。空串按
    cache_price_in 是否已填分流归一——填了价说明启用分段的意图明确，归
    hit_discount，否则归 none；非空旧值（"anthropic" 等）统一归 hit_discount。
    对已归一数据零变更（幂等）。
    """
    models = data.get("llm_models")
    if not isinstance(models, list):
        return []
    changed: List[str] = []
    for index, model in enumerate(models):
        if not isinstance(model, dict) or "cache" not in model:
            continue
        old = model["cache"]
        if old in ("none", "hit_discount"):
            continue
        if old == "" or old is None:
            new = "hit_discount" if float(model.get("cache_price_in", 0.0) or 0.0) > 0 else "none"
        else:
            new = "hit_discount"
        model["cache"] = new
        changed.append(f"llm_models[{index}].cache: {old!r} -> {new}")
    return changed


# 生产钩子登记：model.toml v2.0.43（llm_models[].cache 开关串归一为显式枚举）
register_file_hook("model.toml", "normalize_llm_model_cache_mode", "2.0.43", _normalize_llm_model_cache_mode)


def _drop_memory_injection_limit(data: Dict[str, Any]) -> List[str]:
    """storage.toml v2.0.40：删 ``[memory].profile_injection_max`` 并清理空段。

    本批观众的已有画像全部可见，注入上限失去消费者；``[memory]`` 段
    亦无其他存活键，删键后的空段残留在 forbid 根下属冗余硬错，一并
    移除。对已迁移数据零变更（幂等）。
    """
    memory = data.get("memory")
    if not isinstance(memory, dict) or "profile_injection_max" not in memory:
        return []
    changed: List[str] = ["memory.profile_injection_max"]
    del memory["profile_injection_max"]
    if not memory:
        del data["memory"]
        changed.append("memory")
    return changed


register_file_hook("model.toml", "drop_generation_limits", "2.0.40", _drop_model_generation_limits)
register_file_hook("agents.toml", "drop_input_limits", "2.0.40", _drop_agent_input_limits)
register_file_hook("infra.toml", "drop_simulator_text_limits", "2.0.40", _drop_simulator_text_limits)
register_file_hook("tools.toml", "drop_tool_input_limits", "2.0.40", _drop_tool_input_limits)
register_file_hook("storage.toml", "drop_memory_injection_limit", "2.0.40", _drop_memory_injection_limit)


# 口型调参键：tools.toml [tools.avatar.vts].config → avatar.toml [avatar.lipsync]
# （口型分析器升为共享基础设施时调参先落 infra [avatar.lipsync]，avatar 域
# avatar 独立为第七配置文件后，本钩子的迁移目标改为 avatar.toml 同名段；
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


def _upgrade_vts_lipsync_to_avatar(host_data: Dict[str, Any], target_data: Dict[str, Any]) -> List[str]:
    """tools.toml v2.0.36：VTS lip-sync 调参键跨文件搬至 [avatar.lipsync]。

    用户显式设置值原样搬迁（不改名、不改值）；仅值等于旧默认的键同样搬迁
    （口径统一：VTS provider 不再消费这些键，留在原处即成死键）。开关键
    ``lip_sync_enabled`` 正名为 ``enabled``。对已迁移数据零变更（幂等）。

    目标文件为 avatar.toml（段 ``[avatar.lipsync]`` 即根字段 ``lipsync``）：
    infra 的 avatar 段已随域迁出从 Schema 移除，写入 infra 的键会被校验
    剥离成数据丢失，目标必须与配置实际所在文件一致。
    """
    tools = host_data.get("tools")
    avatar = tools.get("avatar") if isinstance(tools, dict) else None
    vts = avatar.get("vts") if isinstance(avatar, dict) else None
    vts_config = vts.get("config") if isinstance(vts, dict) else None
    if not isinstance(vts_config, dict):
        return []

    changed: List[str] = []
    lipsync = target_data.setdefault("lipsync", {})
    if not isinstance(lipsync, dict):
        lipsync = {}
        target_data["lipsync"] = lipsync

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


# 生产钩子登记：tools.toml v2.0.36（VTS lip-sync 键跨文件迁 [avatar.lipsync]，
# 目标文件随域迁出改为 avatar.toml）
register_cross_file_hook("tools.toml", "avatar.toml", "vts_lipsync_to_avatar", "2.0.36", _upgrade_vts_lipsync_to_avatar)

# tools.toml v2.0.37：删 [tools.avatar.warudo].config 三个字幕键。Warudo 内置
# 的 8766 端口字幕推送服务整体删除（与 Dashboard /subtitle 页重叠），字幕收敛
# 为单一广播源（SubtitleService）分发给各展示页；三键失去消费者。
_drop_warudo_subtitle_keys = drop_section_keys(
    "tools.avatar.warudo.config", ("subtitle_enabled", "subtitle_port", "subtitle_show_status")
)

# 生产钩子登记：tools.toml v2.0.37（Warudo 字幕三键删除，8766 端口的内置字幕推送随之退役）
register_file_hook("tools.toml", "drop_warudo_subtitle_keys", "2.0.37", _drop_warudo_subtitle_keys)


def _upgrade_avatar_platform_to_avatar_file(host_data: Dict[str, Any], target_data: Dict[str, Any]) -> List[str]:
    """tools.toml v2.0.38：``[tools.avatar.*]`` 整体迁至 avatar.toml ``[avatar.platform.*]``。

    avatar 从 tools 域独立为第七配置文件：成员段去掉 tools 动态键机制的
    ``.config`` 中间层直接铺参数键；``enabled`` 布尔换算为
    ``[avatar.platform].enabled`` 启用名单的成员（对齐 agents/collectors
    域根名单约定；段缺 enabled 键按装配侧"缺省不装配"口径处理）。
    VTS 成员段的 idle 六轴绑定旧默认随迁移同步改写。其余用户显式值原样搬迁不改名。宿主侧旧段整体删除。对已迁移数据零变更（幂等）。
    """
    tools = host_data.get("tools")
    avatar = tools.get("avatar") if isinstance(tools, dict) else None
    if not isinstance(avatar, dict) or not avatar:
        return []

    changed: List[str] = []
    # avatar.toml 根即 AvatarRootConfig：段直接挂在文件根（[platform]），无 [avatar] 前缀
    platform = target_data.setdefault("platform", {})
    if not isinstance(platform, dict):
        platform = {}
        target_data["platform"] = platform

    enabled_names = [n for n in platform.get("enabled", []) if isinstance(n, str)]
    for name, member in avatar.items():
        member_cfg = member if isinstance(member, dict) else {}
        config = member_cfg.get("config")
        member_section = dict(config) if isinstance(config, dict) else {}
        if name == "vts":
            # idle 六轴绑定旧默认随迁移改写（数据变换随搬家一次完成；avatar.toml
            # 是新生成文件、版本直接落基线，挂在其自身链上的改写钩子永远够不着
            # 迁移数据）。仅改写旧默认落盘值，用户显式配置原样保留。
            for key, old_default, new_default in (
                ("idle_param_head_x", "HeadAngleX", "FaceAngleX"),
                ("idle_param_head_y", "HeadAngleY", "FaceAngleY"),
                ("idle_param_head_z", "HeadAngleZ", "FaceAngleZ"),
                ("idle_param_body_x", "BodyX", ""),
                ("idle_param_body_y", "BodyY", ""),
                ("idle_param_body_z", "BodyZ", ""),
            ):
                if member_section.get(key) == old_default:
                    member_section[key] = new_default
                    changed.append(f"platform.vts.{key}: {old_default} -> {new_default!r}")
        platform[name] = member_section
        if member_cfg.get("enabled", False) is True and name not in enabled_names:
            enabled_names.append(name)
        changed.append(f"tools.avatar.{name} -> avatar.platform.{name}")
    if enabled_names:
        platform["enabled"] = enabled_names
    if isinstance(tools, dict):
        del tools["avatar"]
        changed.append("tools.avatar")
    return changed


def _upgrade_avatar_lipsync_to_avatar_file(host_data: Dict[str, Any], target_data: Dict[str, Any]) -> List[str]:
    """infra.toml v2.0.34：``[avatar.lipsync]`` 段跨文件迁至 avatar.toml 同名段。

    口型分析共享件的配置从 infra 迁入 avatar 域文件（组件代码住哪、
    配置段跟哪）；键名原样搬迁不改名，用户显式值即唯一事实。宿主侧
    ``[avatar]`` 段删除。对已迁移数据零变更（幂等）。
    """
    avatar_infra = host_data.get("avatar")
    if not isinstance(avatar_infra, dict) or not avatar_infra:
        return []
    changed: List[str] = []
    # avatar.toml 根即 AvatarRootConfig：口型段直接挂文件根（[lipsync]）
    lipsync = avatar_infra.get("lipsync")
    if isinstance(lipsync, dict):
        # 直接赋值：目标段只可能带新字段的基线默认落盘，同一名下用户的唯一
        # 配置事实在源键（infra 侧旧段），搬迁即覆盖
        target_data["lipsync"] = dict(lipsync)
        changed.append("avatar.lipsync（infra.toml -> avatar.toml）")
    del host_data["avatar"]
    return changed


# 生产钩子登记：tools.toml v2.0.38（[tools.avatar.*] 迁至 avatar.toml [avatar.platform.*]）
register_cross_file_hook(
    "tools.toml", "avatar.toml", "avatar_platform_to_avatar_file", "2.0.38", _upgrade_avatar_platform_to_avatar_file
)


# 生产钩子登记：infra.toml v2.0.38（[avatar.lipsync] 迁往 avatar.toml）。
# target 取 2.0.38：存量 infra.toml 版本曾被 tools→infra 跨文件钩子推到
# 2.0.36、新生成文件基线种子 2.0.37，低于两者的钩子 target 永不执行
register_cross_file_hook(
    "infra.toml", "avatar.toml", "avatar_lipsync_to_avatar_file", "2.0.38", _upgrade_avatar_lipsync_to_avatar_file
)


# 麦麦表情档案读取器（组合根注入点）：avatar.toml v2.0.41 的迁移钩子需要读
# avatar 包的表情档案，而配置层不得反向 import 具体组件——档案数据的获取
# 由组合根（main.py）在配置加载前经 ``set_expression_profile_reader`` 注入。
# 未注入时迁移真要写档案即硬错，同样在启动期暴露，不留运行期隐患。
_expression_profile_reader: Callable[[], Dict[str, Any]] | None = None


def set_expression_profile_reader(reader: Callable[[], Dict[str, Any]]) -> None:
    """注入表情档案读取器（零参调用返回档案 dict，如麦麦档案的 toml dict）"""
    global _expression_profile_reader
    _expression_profile_reader = reader


def _upgrade_warudo_maimai_expression(data: Dict[str, Any]) -> List[str]:
    """avatar.toml v2.0.41：已启用 Warudo 的配置写入麦麦表情档案。

    情绪 → 表情键映射改由 ``[platform.warudo.expression]`` 配置、默认取 VRM
    标准表情名；此前适配器内置的是麦麦模型的自定义形状名。启用名单里已有
    Warudo 的部署一直按麦麦映射运行，写入麦麦档案保持表现不变；未启用的
    部署拿默认档案。已有档案段视为用户显式配置，不动。对已迁移数据零变更（幂等）。
    """
    platform = data.get("platform")
    if not isinstance(platform, dict):
        return []
    enabled = platform.get("enabled")
    if not isinstance(enabled, list) or "warudo" not in enabled:
        return []
    warudo = platform.setdefault("warudo", {})
    if not isinstance(warudo, dict) or "expression" in warudo:
        return []
    if _expression_profile_reader is None:
        raise ConfigValidationError(
            "avatar.toml",
            "platform.warudo.expression",
            "表情档案读取器未注入（组合根未调用 set_expression_profile_reader），无法写入麦麦表情档案",
        )
    warudo["expression"] = _expression_profile_reader()
    return ["platform.warudo.expression（写入麦麦表情档案）"]


# 生产钩子登记：avatar.toml v2.0.41（已启用 Warudo 的配置写入麦麦表情档案）。
# target 取 2.0.41：存量 avatar.toml 停在 2.0.38（迁出钩子）或新生成基线
# 2.0.40，两者都要跑到；新生成文件未启用 Warudo，钩子零变更只推进版本
register_file_hook("avatar.toml", "warudo_maimai_expression", "2.0.41", _upgrade_warudo_maimai_expression)


# VTS 段退役的键：待机微动与常驻微笑基线改由面捕合成器（[tracking]）统一负责
_VTS_RETIRED_KEYS = (
    "base_smile",
    "idle_enabled",
    "idle_param_head_x",
    "idle_param_head_y",
    "idle_param_head_z",
    "idle_param_body_x",
    "idle_param_body_y",
    "idle_param_body_z",
    "idle_head_amplitude",
    "idle_body_amplitude",
    "idle_speed",
    "idle_update_interval_ms",
    "idle_fade_speed",
    "idle_head_enabled",
    "idle_body_enabled",
    "idle_pause_while_speaking",
    "idle_extra_params",
    "idle_extra_speed",
)


def _upgrade_vts_idle_to_tracking(data: Dict[str, Any]) -> List[str]:
    """avatar.toml v2.0.42：VTS 的待机微动与常驻微笑基线退役，由面捕合成器接管。

    旧键全部删除（新的头部微动、静止嘴角在 ``[tracking]``，量纲与语义都变了，不做数值换算）；
    唯一保留的用户意图是"关掉了待机微动"：``idle_enabled = false`` 转成
    ``[tracking].head_wander_enabled = false``（``[tracking]`` 里已显式写了该键则不动）。
    对已迁移数据零变更（幂等）。
    """
    platform = data.get("platform")
    vts = platform.get("vts") if isinstance(platform, dict) else None
    if not isinstance(vts, dict):
        return []
    changed: List[str] = []
    if vts.get("idle_enabled") is False:
        tracking = data.setdefault("tracking", {})
        if isinstance(tracking, dict) and "head_wander_enabled" not in tracking:
            tracking["head_wander_enabled"] = False
            changed.append("platform.vts.idle_enabled=false -> tracking.head_wander_enabled=false")
    for key in _VTS_RETIRED_KEYS:
        if key in vts:
            del vts[key]
            changed.append(f"platform.vts.{key}（退役）")
    return changed


# 生产钩子登记：avatar.toml v2.0.42（VTS 待机微动与基线微笑退役，由面捕合成器接管）
register_file_hook("avatar.toml", "vts_idle_to_tracking", "2.0.42", _upgrade_vts_idle_to_tracking)


# Minecraft 的两个事件流采集器改为随 Minecraft Agent 开关的附属采集器（见 src.modules.agents.companions）
_MINECRAFT_COMPANION_SECTIONS = ("maicraft_attention", "maicraft_chat")
_COMPANION_OLD_DEFAULTS = {"url": "http://127.0.0.1:8766/mcp", "retry_interval_ms": 5000}
_COMPANION_NEW_HOMES = {
    "url": "agents.minecraft.mcp.url",
    "retry_interval_ms": "agents.minecraft.events_retry_interval_ms",
}


def _drop_minecraft_companion_collectors(data: Dict[str, Any]) -> List[str]:
    """collectors.toml v2.0.43：Minecraft 的身体事件流、聊天流两个采集器搬出 collectors.toml。

    它们只对 Minecraft Agent 有意义，改为随 Agent 一同开关、配置由 ``[agents.minecraft]`` 换算：
    从 ``enabled`` 名单去掉这两个名字，删掉两个段。只动本文件（各文件版本流独立，不去改 agents.toml）；
    段里改过的地址或重试间隔（不等于旧默认）记一条警告，写明今后在 agents.toml 哪一项设置。
    对已迁移数据零变更（幂等）。
    """
    changed: List[str] = []
    enabled = data.get("enabled")
    if isinstance(enabled, list):
        kept = [name for name in enabled if name not in _MINECRAFT_COMPANION_SECTIONS]
        if len(kept) != len(enabled):
            data["enabled"] = kept
            changed.append("enabled")
    for name in _MINECRAFT_COMPANION_SECTIONS:
        if name not in data:
            continue
        section = data.pop(name)
        changed.append(name)
        if not isinstance(section, dict):
            continue
        for key, old_default in _COMPANION_OLD_DEFAULTS.items():
            value = section.get(key)
            if value is not None and value != old_default:
                logger.warning(
                    f"collectors.toml [{name}].{key} = {value!r} 已随该采集器搬出：它改为随 Minecraft Agent 开关，"
                    f"今后在 agents.toml 的 {_COMPANION_NEW_HOMES[key]} 设置"
                )
    return changed


# 生产钩子登记：collectors.toml v2.0.43（Minecraft 两个采集器改为随 Agent 开关，搬出 collectors.toml）
register_file_hook(
    "collectors.toml", "drop_minecraft_companion_collectors", "2.0.43", _drop_minecraft_companion_collectors
)


# 旧版 B 站弹幕采集器 message_config 两个键的旧默认值：不等于它说明用户改过，迁移时记警告
_BILI_DANMAKU_MESSAGE_OLD_DEFAULTS = {"default_user_id": "", "platform": "bilibili"}


def _drop_bili_danmaku_message_config(data: Dict[str, Any]) -> List[str]:
    """collectors.toml v2.0.44：删 ``[bili_danmaku.message_config]`` 段。

    旧版 B 站弹幕采集器改为缺 uid 即丢弃：``default_user_id`` 是"缺 uid 时的占位身份"，
    会让所有缺 uid 的观众并成一个人，随兜底一起退役；``platform`` 是身份键的组成部分，
    改由采集器写死为 ``bilibili``，不再开放配置。段里改过的值（不等于旧默认）记一条
    警告说明它已失效。对已迁移数据零变更（幂等）。
    """
    section = data.get("bili_danmaku")
    if not isinstance(section, dict) or "message_config" not in section:
        return []
    message_config = section.pop("message_config")
    if isinstance(message_config, dict):
        for key, old_default in _BILI_DANMAKU_MESSAGE_OLD_DEFAULTS.items():
            value = message_config.get(key)
            if value is not None and value != old_default:
                logger.warning(
                    f"collectors.toml [bili_danmaku.message_config].{key} = {value!r} 已失效并删除："
                    "缺 uid 的弹幕改为丢弃，平台标识固定为 bilibili"
                )
    return ["bili_danmaku.message_config"]


# 生产钩子登记：collectors.toml v2.0.44（旧版 B 站弹幕采集器退役 message_config）
register_file_hook("collectors.toml", "drop_bili_danmaku_message_config", "2.0.44", _drop_bili_danmaku_message_config)


# Minecraft 字符预算的历届默认值：等于其中之一说明用户没改过，换用新的 token 预算默认值
_MINECRAFT_CHAR_BUDGET_DEFAULTS = (120_000, 160_000)
# 实测 Minecraft 历史约 2.9 字符折 1 token（16 万字符 ≈ 55k token），换算取整按 3
_MINECRAFT_CHARS_PER_TOKEN = 3


def _upgrade_minecraft_context_tokens(data: Dict[str, Any]) -> List[str]:
    """agents.toml v2.0.44：Minecraft 上下文预算由字符改为 token。

    ``max_context_chars`` 退役；用户显式改过的值按约 3 字符/token 换算成 ``max_tokens``
    并记警告，历届默认值直接删除、由新默认（窗口一半、上限 20 万 token）接管。
    已有 ``max_tokens`` 时不覆盖（幂等）。
    """
    agents = data.get("agents")
    minecraft = agents.get("minecraft") if isinstance(agents, dict) else None
    context = minecraft.get("context") if isinstance(minecraft, dict) else None
    if not isinstance(context, dict) or "max_context_chars" not in context:
        return []
    chars = context.pop("max_context_chars")
    changed = ["agents.minecraft.context.max_context_chars"]
    if isinstance(chars, int) and chars not in _MINECRAFT_CHAR_BUDGET_DEFAULTS and "max_tokens" not in context:
        tokens = max(4_000, chars // _MINECRAFT_CHARS_PER_TOKEN)
        context["max_tokens"] = tokens
        changed.append("agents.minecraft.context.max_tokens")
        logger.warning(
            f"agents.toml [agents.minecraft.context].max_context_chars = {chars} 已改为 token 预算："
            f"换算为 max_tokens = {tokens}；实际预算还受 window_ratio × 模型上下文窗口约束"
        )
    return changed


register_file_hook("agents.toml", "minecraft_context_tokens", "2.0.44", _upgrade_minecraft_context_tokens)


def _drop_profile_history_budget(data: Dict[str, Any]) -> List[str]:
    """model.toml v2.0.44：删 ``[llm_profiles.*].history_token_budget``。

    对话历史预算是消费方 Agent 的策略、按模型窗口比例换算，改由各 Agent 的上下文段
    承载（主播 ``[agents.streamer.context]``、Minecraft ``[agents.minecraft.context]``）；
    profile 上的这个键失去全部消费者。旧值不搬迁：它是固定 token 数，与新的
    "窗口比例 + 上限"语义不对应，且主播侧的默认值 8000 正是要淘汰的配置。
    """
    changed: List[str] = []
    profiles = data.get("llm_profiles")
    if isinstance(profiles, dict):
        for name, profile in profiles.items():
            if isinstance(profile, dict) and "history_token_budget" in profile:
                del profile["history_token_budget"]
                changed.append(f"llm_profiles.{name}.history_token_budget")
    return changed


register_file_hook("model.toml", "drop_profile_history_budget", "2.0.44", _drop_profile_history_budget)


def _drop_tools_memory_section(data: Dict[str, Any]) -> List[str]:
    """tools.toml v2.0.45：删 ``[tools.memory]`` 段。

    通用记忆检索工具（query_memory / query_viewer_profile）退役，主播记忆改为
    随主播 Agent 注册的 ``memory_recall_session`` / ``memory_recall_viewer``，
    不再有独立开关；段留在 forbid 根下属未知段硬错，整段删除。对已迁移数据
    零变更（幂等）。
    """
    tools = data.get("tools")
    if not isinstance(tools, dict) or "memory" not in tools:
        return []
    del tools["memory"]
    return ["tools.memory"]


register_file_hook("tools.toml", "drop_tools_memory_section", "2.0.45", _drop_tools_memory_section)


# web 搜索 bing 引擎入口的历届默认值：等于旧值说明用户没显式改过，才随默认值更换改写
_WEB_SEARCH_BING_ENTRY_OLD_DEFAULT = "https://cn.bing.com/search"
_WEB_SEARCH_BING_ENTRY_NEW_DEFAULT = "https://www.bing.com/search"

# tools.toml v2.0.46：web 搜索 bing 引擎默认入口更换。cn.bing.com 对无 cookie
# 直连请求改为落地必应首页（不再返回结果页），默认入口换为 www.bing.com/search。
# 仅当落盘值等于旧默认（用户未显式改过）时才改写，自定义入口保持原值。
_upgrade_web_search_bing_entry = swap_default(
    "tools.web.search.config.base_url",
    _WEB_SEARCH_BING_ENTRY_OLD_DEFAULT,
    _WEB_SEARCH_BING_ENTRY_NEW_DEFAULT,
    annotate=False,
)

# 生产钩子登记：tools.toml v2.0.46（web 搜索 bing 入口默认值 cn→www）
register_file_hook("tools.toml", "web_search_bing_entry_default_swap", "2.0.46", _upgrade_web_search_bing_entry)


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


# agents.toml v2.0.45：style_constraints 默认值去掉"适当使用emoji"尾缀（与表达侧
# 统一禁 emoji 的模板指令矛盾）。仅值等于旧默认时改写为新默认，用户显式
# 设置的其他值不动。
_upgrade_replyer_style_drop_emoji = swap_default(
    "agents.streamer.persona.style_constraints",
    "口语化，使用网络流行语，避免机械式回复，适当使用emoji",
    "口语化，使用网络流行语，避免机械式回复",
    annotate=False,
)

register_file_hook("agents.toml", "replyer_style_drop_emoji", "2.0.45", _upgrade_replyer_style_drop_emoji)

# agents.toml v2.0.46：``[agents.streamer.live_frame]`` 的两道挂帧节流键退役。
# 决策窗改为每窗必挂暂存里的最新帧，``max_age_ms`` / ``min_interval_ms`` 失去
# 消费者。只处理本段内的这两个键——其他段的 ``min_interval_ms``（如 proactive）
# 是活跃配置，不许误伤。
_retire_live_frame_gates = drop_section_keys("agents.streamer.live_frame", ("max_age_ms", "min_interval_ms"))

register_file_hook("agents.toml", "retire_live_frame_gates", "2.0.46", _retire_live_frame_gates)

# agents.toml v2.0.47：已聊话题回看窗默认 6 → 60 轮。6 轮（约 2 分钟）只够看
# 穿两三句内的连读，十几分钟前的同一话题照样被当新角度讲。仅改写等于旧默认
# 6 的落盘值，用户显式设置的其他值不动。
_widen_recent_intents_window = swap_default("agents.streamer.planner_recent_intents_max", 6, 60)

register_file_hook("agents.toml", "widen_recent_intents_window", "2.0.47", _widen_recent_intents_window)

# agents.toml v2.0.48：删 ``[agents.streamer.narrative]`` 段。游戏事件素材改为
# 统一追加式历史：事件在决策窗边界批量写进对话时间线，不再逐窗重放，三个
# 叙事缓冲及其保留期/条数上限/折叠窗配置失去消费者。段留在文件里即属未知
# 段硬错，整段原地删除。
_retire_streamer_narrative = drop_section("agents.streamer.narrative")

register_file_hook("agents.toml", "retire_streamer_narrative", "2.0.48", _retire_streamer_narrative)

# agents.toml v2.0.49：删 ``[agents.streamer.background].cold_timeout_ms``。该字段
# 读进后台维护器后无任何消费点；冷场判定只认 ``proactive`` 段的 ``cold_timeout_ms``
# ——同一概念两处定义且默认值还不一致（60s vs 45s），调参只会被误导。字段退役，
# 存量键原地删除。
_drop_background_cold_timeout = drop_section_keys("agents.streamer.background", ("cold_timeout_ms",))

# 生产钩子登记：agents.toml v2.0.49（background.cold_timeout_ms 死配置清除）
register_file_hook("agents.toml", "drop_background_cold_timeout", "2.0.49", _drop_background_cold_timeout)

# agents.toml v2.0.50：删 ``[agents.streamer.persona].audience_salutation``。
# Replyer 的称呼模板变量已退役（模板正文硬编码统一称呼），该键读入后无任何
# 消费点；留在文件里即属未知字段硬错，原地删除。
_drop_persona_audience_salutation = drop_section_keys("agents.streamer.persona", ("audience_salutation",))

register_file_hook("agents.toml", "drop_persona_audience_salutation", "2.0.50", _drop_persona_audience_salutation)

# infra.toml v2.0.43：删三个小部件段的 ``enable_html_page`` 键。旧版后端直出
# 透明页（/widget、/widget/2048）整体退役，嵌入页统一为前端 SPA 的
# /widget/<name> 路由，该开关失去消费者。
_upgrade_widget_drop_html_page = chain(
    drop_section_keys("dashboard.danmaku_widget", ("enable_html_page",)),
    drop_section_keys("dashboard.subtitle_widget", ("enable_html_page",)),
    drop_section_keys("dashboard.game2048_widget", ("enable_html_page",)),
)

# 生产钩子登记：infra.toml v2.0.43（小部件段 enable_html_page 删键，旧透明页退役）
register_file_hook("infra.toml", "widget_drop_html_page", "2.0.43", _upgrade_widget_drop_html_page)


__all__ = [
    "FileUpgradeHook",
    "CrossFileHook",
    "register_file_hook",
    "register_cross_file_hook",
    "advance_file_versions",
    "set_expression_profile_reader",
]
