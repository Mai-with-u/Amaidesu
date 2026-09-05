"""配置升级钩子系统（v2.0.0）

提供版本驱动的配置迁移机制。
当配置版本跨越特定版本号时，自动执行对应的迁移函数。

使用方式：
    1. 定义迁移函数：def _my_migrate(data: dict) -> list[str]
    2. 注册钩子：在 CONFIG_UPGRADE_HOOKS 中添加 ConfigUpgradeHook
    3. 启动时自动触发：apply_upgrade_hooks(data, file, old_ver, new_ver)

AGENTS.md 钩子契约：
- **原地修改**：hook 接收 dict 并直接修改，不返回新对象
- **幂等**：重复执行结果一致（首次执行后旧值已不存在，再次执行无事发生）
- **返回变更路径列表**：hook 返回本次修改的字段路径列表
- **每条 hook 配单元测试**：旧结构输入 → → 断言新结构输出
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

ConfigMigrateCallable = Callable[[dict[str, Any]], list[str]]


@dataclass(frozen=True)
class ConfigUpgradeHook:
    """配置升级钩子，在跨过指定版本时执行一次。

    Attributes:
        target_version: 触发此钩子的目标版本号（如 "2.0.0"）
        config_file: 作用的配置文件名（如 "core.toml"）
        migrate: 迁移函数，接收配置字典，原地修改，返回变更的字段路径列表
    """

    target_version: str
    config_file: str
    migrate: ConfigMigrateCallable


@dataclass
class UpgradeResult:
    """升级结果

    Attributes:
        data: 迁移后的配置数据
        migrated: 是否发生了迁移
        reasons: 迁移原因列表（格式："{version}:{field_path}"）
    """

    data: dict[str, Any]
    migrated: bool
    reasons: list[str]


# ---------------------------------------------------------------------------
# 0.5.4 → 1.0.0 阶段迁移 hooks（沿用历史 mainosaba 改名）
# ---------------------------------------------------------------------------


def _migrate_mainosaba_to_text_adv_game(data: dict[str, Any]) -> list[str]:
    """兼容期 hook（旧 input.toml 已废弃；v2.0.0 此函数不再被新文件触发，保留供回滚）。

    - ``[collectors]`` 段键 ``mainosaba`` → ``text_adv_game``（子配置整体保留）
    - ``[collectors].enabled`` 列表内 ``mainosaba`` → ``text_adv_game``

    原地修改、幂等，返回变更路径列表。
    """
    changed: list[str] = []
    collectors = data.get("collectors")
    if isinstance(collectors, dict):
        if "mainosaba" in collectors:
            collectors["text_adv_game"] = collectors.pop("mainosaba")
            changed.append("collectors.text_adv_game")
        enabled = collectors.get("enabled")
        if isinstance(enabled, list) and "mainosaba" in enabled:
            collectors["enabled"] = ["text_adv_game" if v == "mainosaba" else v for v in enabled]
            changed.append("collectors.enabled")
    return changed


# ---------------------------------------------------------------------------
# 2.0.1 增量修复 hooks（Wave 6 收尾）
# ---------------------------------------------------------------------------


def _migrate_core_2_0_1(data: dict[str, Any]) -> list[str]:
    """core.toml 2.0.1：清理 Wave 6 删除的 text_adv_game 段。

    Wave 6 删除了 src/stages/input/collectors/text_adv_game/，但旧用户
    config.toml 可能仍有 [input.text_adv_game] / [input.collectors.text_adv_game]
    残留字段。此 hook 主动剥离这些残留（避免 schema 校验失败）。

    原地修改、幂等，返回变更路径列表。
    """
    changed: list[str] = []
    for key in ("text_adv_game",):
        if key in data:
            data.pop(key)
            changed.append(key)
    return changed


def _migrate_agents_2_0_1(data: dict[str, Any]) -> list[str]:
    """agents.toml 2.0.1：移除旧 reply_probability 字段（Wave 6 重构后已不再使用）。

    原 StreamerAgentConfig 早期有 reply_probability 字段（被 Planner 决策代替），
    v6 重新设计后该字段已删除。旧配置文件若残留 reply_probability 字段，
    主动剥离（保留其他字段）。

    原地修改、幂等，返回变更路径列表。
    """
    changed: list[str] = []
    streamer = data.get("streamer")
    if isinstance(streamer, dict) and "reply_probability" in streamer:
        streamer.pop("reply_probability")
        changed.append("streamer.reply_probability")
    return changed


def _migrate_model_2_0_3(data: dict[str, Any]) -> list[str]:
    """model.toml 2.0.3：修复无效 provider 引用（自包含，不依赖 2.0.0 钩子）。

    版本门控下 2.0.0 的 ``llm_outline → llm_agenda`` 改名钩子不会对已
    越过 2.0.0 的文件再触发；本钩子自行处理改名 + 将无效/缺失的
    profile.provider（如残留默认值 "default"）重写为首个可用 provider。

    无效引用会导致 Schema 校验失败 → 写回跳过 → 漂移修复永远无法落盘。

    原地修改、幂等，返回变更路径列表。
    """
    changed: list[str] = []
    if "llm_outline" in data and "llm_agenda" not in data:
        data["llm_agenda"] = data.pop("llm_outline")
        changed.append("llm_agenda")
        changed.append("llm_outline")

    providers = data.get("llm_providers")
    if not isinstance(providers, list):
        return changed
    available = [p.get("name") for p in providers if isinstance(p, dict) and p.get("name")]
    if not available:
        return changed
    first = available[0]
    for profile in ("llm", "llm_fast", "vlm", "llm_local", "llm_summary", "llm_agenda"):
        cfg = data.get(profile)
        if isinstance(cfg, dict):
            prov = cfg.get("provider")
            if prov not in available:
                cfg["provider"] = first
                changed.append(f"{profile}.provider")
        elif profile == "llm_agenda":
            data["llm_agenda"] = {"provider": first}
            changed.append("llm_agenda")
    return changed


def _migrate_agents_2_0_3(data: dict[str, Any]) -> list[str]:
    """agents.toml 2.0.3：过滤失效 Agent 类型并剥离旧决策时代子节。

    旧配置含 "maibot" 等 enabled 值与 ``[agents.llm]/[agents.maibot]``
    等子节；前者不在 AgentType Literal 内，后者违反 extra="forbid"，
    均导致 Schema 校验失败并回退 raw dict 加载。过滤后列表为空时
    回退默认 ["streamer"]，保证应用开箱可用。

    原地修改、幂等，返回变更路径列表。
    """
    changed: list[str] = []
    agents = data.get("agents")
    if not isinstance(agents, dict):
        return changed
    enabled = agents.get("enabled")
    if isinstance(enabled, list):
        valid = [v for v in enabled if isinstance(v, str) and v in ("streamer", "game", "custom")]
        if len(valid) != len(enabled):
            agents["enabled"] = valid or ["streamer"]
            changed.append("agents.enabled")
    for key in ("llm", "maibot", "amaidesu", "replay", "command"):
        if key in agents:
            del agents[key]
            changed.append(f"agents.{key}")
    return changed


def _migrate_core_2_0_2(data: dict[str, Any]) -> list[str]:
    """core.toml 2.0.2：移除 ``[mcp]`` 段（MCP 桥接服务已随 v2 决策架构移除）。

    MCP 服务依赖的 /api/v1/maibot/action 端点与 MaiBot 桥接一并删除，
    配置段失去消费方，主动剥离避免死配置漂移告警。

    原地修改、幂等，返回变更路径列表。
    """
    changed: list[str] = []
    if "mcp" in data:
        data.pop("mcp")
        changed.append("mcp")
    return changed


def _migrate_core_2_0_4(data: dict[str, Any]) -> list[str]:
    """core.toml 2.0.4：``[pipelines]`` 正名为 ``[interceptors]``（§1.46.1 收官）。

    旧结构为阶段嵌套（input/output 子层），新结构拍平为拦截器名直挂：
    - 取 ``pipelines.input`` 子节作为拦截器集合（rate_limit / similar_filter）
    - 兼容历史上直接挂在 pipelines 根的扁平键（v1 遗留形态）
    - 丢弃 ``output`` 子节（OutputPipeline 已删；敏感词净化归 Replyer）
    - ``priority`` 字段随管道调度语义废弃，剥离
    - 已存在的 ``[interceptors]`` 键保留，迁移项以 setdefault 并入

    原地修改、幂等（重复执行时 ``pipelines`` 已不存在，无事发生），
    返回变更路径列表。
    """
    changed: list[str] = []
    pipelines = data.get("pipelines")
    if not isinstance(pipelines, dict):
        return changed

    data.pop("pipelines")

    raw_staged = pipelines.get("input")
    staged_input: dict[str, Any] = raw_staged if isinstance(raw_staged, dict) else {}
    existing = data.get("interceptors")
    merged: dict[str, Any] = dict(existing) if isinstance(existing, dict) else {}
    for source in (staged_input, pipelines):
        for name, cfg in source.items():
            if name in ("input", "output") or not isinstance(cfg, dict):
                continue
            if name in merged:
                continue
            merged[name] = {k: v for k, v in cfg.items() if k != "priority"}

    if not isinstance(existing, dict) or merged != existing:
        data["interceptors"] = merged
        changed.append("interceptors")
    else:
        changed.append("pipelines")
    return changed


def _strip_pipelines_2_0_4(data: dict[str, Any]) -> list[str]:
    """input.toml / output.toml 2.0.4：剥离遗留 ``[pipelines]`` 死段。

    管道配置已正名至 core.toml ``[interceptors]``；阶段文件中的残留段无
    Schema 字段承接（extra="forbid"），不剥离将导致永久验证失败循环。

    原地修改、幂等，返回变更路径列表。
    """
    changed: list[str] = []
    if "pipelines" in data:
        data.pop("pipelines")
        changed.append("pipelines")
    return changed


# ---------------------------------------------------------------------------
# 2.0.0 跨域迁移 hooks（核心改造）
# ---------------------------------------------------------------------------


def _migrate_core_2_0_0(data: dict[str, Any]) -> list[str]:
    """core.toml 2.0.0：删 ``[maicore]`` + 改造 ``[context]`` 为 ContextAssembler 配置。

    旧 [maicore] 段（host/port/token）→ 2.0.0 单进程不需要 MaiCore 连接，
    直接删除（字段已无人引用，旧客户端连接走 [mcp] 段）。

    旧 [context] 段（storage_type/max_messages_per_session/...）→ 新
    [context] 段（enabled/memory_recall_viewers/memory_recall_long_term/
    cache_ttl_ms），旧字段全部丢弃（语义已变：会话存储职责下放给 memory 后端）。

    原地修改、幂等，返回变更路径列表。
    """
    changed: list[str] = []
    if "maicore" in data:
        data.pop("maicore")
        changed.append("maicore")

    context = data.get("context")
    if context is None:
        data["context"] = {
            "enabled": True,
            "memory_recall_viewers": 3,
            "memory_recall_long_term": 3,
            "cache_ttl_ms": 1000,
        }
        changed.append("context")
        return changed

    if isinstance(context, dict):
        old_keys = set(context.keys())
        new_context = {
            "enabled": bool(context.get("enabled", True)),
            "memory_recall_viewers": 3,
            "memory_recall_long_term": 3,
            "cache_ttl_ms": 1000,
        }
        new_keys = set(new_context.keys())
        data["context"] = new_context
        if old_keys != new_keys:
            changed.append("context")
    return changed


def _migrate_model_2_0_0(data: dict[str, Any]) -> list[str]:
    """model.toml 2.0.0：``llm_outline`` → ``llm_agenda``（Outline→Agenda 命名统一）。

    原地修改、幂等；重复执行时 ``llm_outline`` 已不存在，无事发生。

    同时（可选）补齐缺失的 ``llm_agenda`` 默认占位，避免其他依赖该 profile 的
    模块读取时报 KeyError。
    """
    changed: list[str] = []
    if "llm_outline" in data:
        outline_cfg = data.pop("llm_outline")
        # 仅在用户没显式配置 llm_agenda 时迁移
        if "llm_agenda" not in data:
            data["llm_agenda"] = outline_cfg
            changed.append("llm_agenda")
            changed.append("llm_outline")
    return changed


def _migrate_tools_2_0_0(data: dict[str, Any]) -> list[str]:
    """tools.toml 2.0.0：补齐缺失的默认结构。

    旧 [collectors]/[handlers] 段通过 CrossFileMigration 合并到
    ``[tools.perception.config]`` / ``[tools.output.config]``，本 hook 仅负责
    兜底：用户全新升级时若 tools.toml 已生成但无任何工具包，补充 enabled 列表
    和默认结构。

    原地修改、幂等。
    """
    changed: list[str] = []
    tools = data.get("tools")
    if not isinstance(tools, dict):
        # tools 段不存在或类型错误 → 重建
        data["tools"] = {
            "enabled": ["perception", "output"],
            "perception": None,
            "output": None,
        }
        changed.append("tools")
        return changed

    # enabled 列表兜底
    if "enabled" not in tools or not isinstance(tools.get("enabled"), list):
        tools["enabled"] = ["perception", "output"]
        changed.append("tools.enabled")
    return changed


def _migrate_agents_2_0_0(data: dict[str, Any]) -> list[str]:
    """agents.toml 2.0.0：补齐缺失的默认结构。

    旧 [deciders] 段通过 CrossFileMigration 合并到 ``[agents]`` 段，本 hook 负责：
    1. ``enabled`` 列表兜底（含旧 enabled 列表的迁移项）
    2. 默认 streamer 子配置

    原地修改、幂等。
    """
    changed: list[str] = []
    agents = data.get("agents")
    if not isinstance(agents, dict):
        data["agents"] = {
            "enabled": ["streamer"],
            "streamer": {
                "planner_llm": "llm_fast",
                "replyer_llm": "llm",
                "reply_probability": 0.7,
                "budget": {
                    "max_rounds": 3,
                    "timeout_ms": 5000,
                    "finish_action": "say",
                },
                "room_state_enabled": True,
                "room_state_cold_timeout_ms": 60000,
                "room_state_llm_summary_interval_ms": 60000,
            },
            "game": None,
        }
        changed.append("agents")
        return changed

    if "enabled" not in agents or not isinstance(agents.get("enabled"), list):
        agents["enabled"] = ["streamer"]
        changed.append("agents.enabled")
    return changed


def _migrate_memory_2_0_0(data: dict[str, Any]) -> list[str]:
    """memory.toml 2.0.0：兜底 backend 默认值。

    新文件首次生成时 backend="simple" 已写入，但用户从旧 config.toml 迁移时
    可能没有 [memory] 段。这里补默认。

    原地修改、幂等。
    """
    changed: list[str] = []
    memory = data.get("memory")
    if not isinstance(memory, dict):
        data["memory"] = {"backend": "simple", "simple": None, "amemorix": None}
        changed.append("memory")
        return changed
    if "backend" not in memory:
        memory["backend"] = "simple"
        changed.append("memory.backend")
    return changed


def _migrate_storage_2_0_0(data: dict[str, Any]) -> list[str]:
    """storage.toml 2.0.0：兜底 [storage.sqlite] 默认值。

    原地修改、幂等。
    """
    changed: list[str] = []
    storage = data.get("storage")
    if not isinstance(storage, dict):
        data["storage"] = {
            "sqlite": {
                "db_path": "data/amaidesu.db",
                "wal": True,
                "busy_timeout_ms": 5000,
                "foreign_keys": True,
            }
        }
        changed.append("storage")
        return changed
    sqlite = storage.get("sqlite")
    if not isinstance(sqlite, dict):
        storage["sqlite"] = {
            "db_path": "data/amaidesu.db",
            "wal": True,
            "busy_timeout_ms": 5000,
            "foreign_keys": True,
        }
        changed.append("storage.sqlite")
    return changed


def _migrate_background_2_0_0(data: dict[str, Any]) -> list[str]:
    """background.toml 2.0.0：兜底默认配置。

    原地修改、幂等。
    """
    changed: list[str] = []
    bg = data.get("background")
    if not isinstance(bg, dict):
        data["background"] = {
            "light_tick_ms": 5000,
            "compressor": {"concurrency": 1, "queue_max": 100},
        }
        changed.append("background")
        return changed
    if "light_tick_ms" not in bg:
        bg["light_tick_ms"] = 5000
        changed.append("background.light_tick_ms")
    compressor = bg.get("compressor")
    if not isinstance(compressor, dict):
        bg["compressor"] = {"concurrency": 1, "queue_max": 100}
        changed.append("background.compressor")
    return changed


def _migrate_tools_2_0_9(data: dict[str, Any]) -> list[str]:
    """tools.toml 2.0.9：剥离 ``[tools.perception.config.read_pingmu]`` 死键。

    审计 D1：VLM 收编后，ScreenChangeCollector.ConfigSchema 移除 ``api_key`` /
    ``base_url`` / ``model_name`` 三字段（统一走 ``model.toml`` 的 ``[vlm]`` profile
    + ``[[llm_providers]]`` 池）。原配置残留会触发 Schema 漂移告警，且永远无人读取。

    路径钻取：``[tools] → [perception] → [config] → [read_pingmu]``；任一中间层
    类型错误（应 dict）则放弃该路径返回，保持其他字段完整。

    原地修改、幂等（重复执行时三键已不存在，无事发生），返回变更路径列表。
    """
    changed: list[str] = []

    tools = data.get("tools")
    if not isinstance(tools, dict):
        return changed

    perception = tools.get("perception")
    if not isinstance(perception, dict):
        return changed

    perception_cfg = perception.get("config")
    if not isinstance(perception_cfg, dict):
        return changed

    read_pingmu = perception_cfg.get("read_pingmu")
    if not isinstance(read_pingmu, dict):
        return changed

    dead_keys = ("api_key", "base_url", "model_name")
    for key in dead_keys:
        if key in read_pingmu:
            del read_pingmu[key]
            changed.append(f"tools.perception.config.read_pingmu.{key}")

    return changed


def _migrate_tools_2_0_10(data: dict[str, Any]) -> list[str]:
    """tools.toml 2.0.10：剥离 TTS 基础设施重塑遗留的 9 个死字段。

    TTS 基础设施从 [tools.output.config] 提到 core.toml [tts]，原属
    ``OutputHandlersConfig`` 的 3 个调度字段与 4 个 Provider 的 ``output_device_name``
    以及 OmniTTS 的 ``use_vts_lip_sync`` / ``use_subtitle`` 均已无人读取，统一切除。

    ``render_timeout_ms`` 由 CrossFileMigration 移至 core.toml ``[tts]``，不在本钩子
    处理范围内。

    任一中间层缺失或类型错误均安全返回，保持其他字段完整。

    原地修改、幂等（重复执行时死字段已不存在，无事发生），返回变更路径列表。
    """
    changed: list[str] = []

    tools = data.get("tools")
    if not isinstance(tools, dict):
        return changed

    output = tools.get("output")
    if not isinstance(output, dict):
        return changed

    output_cfg = output.get("config")
    if not isinstance(output_cfg, dict):
        return changed

    for dead_key in ("concurrent_rendering", "error_handling", "completion_timeout_ms"):
        if dead_key in output_cfg:
            del output_cfg[dead_key]
            changed.append(f"tools.output.config.{dead_key}")

    for provider_key in ("edge_tts", "gptsovits", "omni_tts", "voicebox"):
        provider_cfg = output_cfg.get(provider_key)
        if isinstance(provider_cfg, dict) and "output_device_name" in provider_cfg:
            del provider_cfg["output_device_name"]
            changed.append(f"tools.output.config.{provider_key}.output_device_name")

    omni_cfg = output_cfg.get("omni_tts")
    if isinstance(omni_cfg, dict):
        for dead_key in ("use_vts_lip_sync", "use_subtitle"):
            if dead_key in omni_cfg:
                del omni_cfg[dead_key]
                changed.append(f"tools.output.config.omni_tts.{dead_key}")

    return changed


# ---------------------------------------------------------------------------
# 升级钩子注册表
# ---------------------------------------------------------------------------


CONFIG_UPGRADE_HOOKS: tuple[ConfigUpgradeHook, ...] = (
    # 0.5.4 历史钩子（保留供回滚，新文件不再触发）
    ConfigUpgradeHook(
        target_version="0.5.4",
        config_file="input.toml",
        migrate=_migrate_mainosaba_to_text_adv_game,
    ),
    # 2.0.1 Wave 6 收尾清理（清理 text_adv_game 残留字段 + 移除 reply_probability）
    ConfigUpgradeHook(
        target_version="2.0.1",
        config_file="core.toml",
        migrate=_migrate_core_2_0_1,
    ),
    ConfigUpgradeHook(
        target_version="2.0.1",
        config_file="agents.toml",
        migrate=_migrate_agents_2_0_1,
    ),
    ConfigUpgradeHook(
        target_version="2.0.2",
        config_file="core.toml",
        migrate=_migrate_core_2_0_2,
    ),
    ConfigUpgradeHook(
        target_version="2.0.3",
        config_file="model.toml",
        migrate=_migrate_model_2_0_3,
    ),
    ConfigUpgradeHook(
        target_version="2.0.3",
        config_file="agents.toml",
        migrate=_migrate_agents_2_0_3,
    ),
    # 2.0.4 管道→事件拦截器正名收官（§1.46.1）
    ConfigUpgradeHook(
        target_version="2.0.4",
        config_file="core.toml",
        migrate=_migrate_core_2_0_4,
    ),
    ConfigUpgradeHook(
        target_version="2.0.4",
        config_file="input.toml",
        migrate=_strip_pipelines_2_0_4,
    ),
    ConfigUpgradeHook(
        target_version="2.0.4",
        config_file="output.toml",
        migrate=_strip_pipelines_2_0_4,
    ),
    # 2.0.0 七文件全量改造
    ConfigUpgradeHook(
        target_version="2.0.0",
        config_file="core.toml",
        migrate=_migrate_core_2_0_0,
    ),
    ConfigUpgradeHook(
        target_version="2.0.0",
        config_file="model.toml",
        migrate=_migrate_model_2_0_0,
    ),
    ConfigUpgradeHook(
        target_version="2.0.0",
        config_file="tools.toml",
        migrate=_migrate_tools_2_0_0,
    ),
    ConfigUpgradeHook(
        target_version="2.0.0",
        config_file="agents.toml",
        migrate=_migrate_agents_2_0_0,
    ),
    ConfigUpgradeHook(
        target_version="2.0.0",
        config_file="memory.toml",
        migrate=_migrate_memory_2_0_0,
    ),
    ConfigUpgradeHook(
        target_version="2.0.0",
        config_file="storage.toml",
        migrate=_migrate_storage_2_0_0,
    ),
    ConfigUpgradeHook(
        target_version="2.0.0",
        config_file="background.toml",
        migrate=_migrate_background_2_0_0,
    ),
    # 2.0.9 D1 VLM 收编：剥离 [tools.perception.config.read_pingmu] 的 VLM 自管字段
    ConfigUpgradeHook(
        target_version="2.0.9",
        config_file="tools.toml",
        migrate=_migrate_tools_2_0_9,
    ),
    # TTS 基础设施重塑收尾：剥离 9 个 OutputHandlersConfig / Provider 死字段
    ConfigUpgradeHook(
        target_version="2.0.10",
        config_file="tools.toml",
        migrate=_migrate_tools_2_0_10,
    ),
)


# ---------------------------------------------------------------------------
# 版本范围与 apply
# ---------------------------------------------------------------------------


def _parse_version(version: str) -> tuple[int, ...]:
    """解析版本字符串为可比较的元组。"""
    return tuple(int(part) for part in version.split("."))


def _version_in_range(old_ver: str, target_ver: str, new_ver: str) -> bool:
    """检查 target_ver 是否在 (old_ver, new_ver] 范围内。

    即：old < target <= new 时返回 True。
    确保每个钩子在版本跨越时只执行一次。
    """
    old_parts = _parse_version(old_ver)
    target_parts = _parse_version(target_ver)
    new_parts = _parse_version(new_ver)
    return old_parts < target_parts <= new_parts


def apply_upgrade_hooks(
    data: dict[str, Any],
    config_file: str,
    old_ver: str,
    new_ver: str,
) -> UpgradeResult:
    """应用版本范围内的升级钩子。

    遍历所有注册的钩子，对匹配 config_file 且版本在 (old_ver, new_ver] 范围内的钩子执行迁移。
    """
    migrated_reasons: list[str] = []

    for hook in CONFIG_UPGRADE_HOOKS:
        if hook.config_file != config_file:
            continue
        if not _version_in_range(old_ver, hook.target_version, new_ver):
            continue

        hook_reasons = hook.migrate(data)
        for reason in hook_reasons:
            migrated_reasons.append(f"{hook.target_version}:{reason}")

    return UpgradeResult(
        data=data,
        migrated=bool(migrated_reasons),
        reasons=migrated_reasons,
    )
