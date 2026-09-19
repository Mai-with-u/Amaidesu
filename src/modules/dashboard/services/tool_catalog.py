"""工具提供者分类目录装配

工具页侧边栏与提供者分组的数据源装配：以运行时注册表为事实源
（``registry.list_providers()``，含连接失败降级登记的 0 工具 Provider），
∪ 配置声明态（``[tools.*]`` 段键：enabled=false 未装配的提供者也展示）。
HTTP 语义（503 / 400 / 写回映射）留在 api 层。
"""

from typing import Any, Dict, List, Tuple

from src.modules.logging import get_logger

logger = get_logger("ToolCatalog")

# 分类展示顺序（固定词表；注册表出现词表外分类时按名排序追加在后）。
# 提供者成员不再静态硬编码——注册表（运行态）∪ 配置段键（声明态）。
CATEGORY_ORDER: Tuple[str, ...] = ("avatar", "studio", "vision", "memory", "mcp", "game", "framework")

# 已知提供者的展示描述（(分类, 提供者键) → 文案）。仅为显示元数据，不构成
# 成员事实；查不到的提供者按分类给默认描述。
PROVIDER_DESCRIPTIONS: Dict[Tuple[str, str], str] = {
    ("avatar", "vts"): "VTubeStudio 控制",
    ("avatar", "vrchat"): "VRChat OSC 桥接",
    ("avatar", "warudo"): "Warudo 控制",
    ("studio", "obs"): "OBS Studio 控制",
    ("vision", "vision"): "视觉感知（vision_look_at_screen）",
    ("memory", "memory"): "记忆检索（memory_query_memory）",
    ("game", "text_adv"): "文字冒险游戏（text_adv_*）",
    ("framework", "framework"): "框架内置（framework_*，如 AgentControl）",
}

# 成员来自 [tools.<分类>] 直接子段键的分类（enabled / config 之外的子键 =
# 提供者声明；enabled=false 的声明提供者也展示——"配置已声明、重启后装配"）。
_CONFIG_MEMBER_CATEGORIES: Tuple[str, ...] = ("avatar", "studio")

# 工具分类 "game" 的判据：名册里除框架自己的主播 Agent 之外，剩下的都是游戏 Agent
# （主播 Agent 唯一且自我驱动，游戏 Agent 命令驱动）。这里不列举任何具体游戏名——
# 接哪款游戏由 agents.enabled 决定，后端分类不随游戏增减而改。
_NON_GAME_AGENT_NAMES: Tuple[str, ...] = ("streamer",)

# 分类级开关的成员（键与分类名相同，配置段为 [tools.<分类>]）。
CATEGORY_LEVEL_KEYS = {"vision", "memory"}

# 随 Agent 启用的分类（不可开关；提供者 = Agent 自声明，注册表动态发现）。
AGENT_CATEGORIES: Tuple[str, ...] = ("game", "framework")

# MCP 分类（提供者 = 注册表 MCP Provider ∪ [tools.mcp.config.servers] 配置键）。
MCP_CATEGORY = "mcp"


def runtime_tool_counts(registry: Any) -> Dict[Tuple[str, str], Tuple[int, int]]:
    """按 (分类, 工具 provider 标识) 统计工具数，返回 (全集数, 停用数)。

    统计包含停用工具——工具页展示提供者的工具全集，停用数供行级开关渲染。
    熔断（tripped）工具也纳入全集统计（与停用并列可见），便于面板观察
    运行态健康徽标。
    """
    counts: Dict[Tuple[str, str], Tuple[int, int]] = {}
    try:
        for spec in registry.list_tools(include_disabled=True, include_tripped=True):
            key = (registry.category_of(spec.name), getattr(spec, "provider", "") or "")
            total, disabled = counts.get(key, (0, 0))
            counts[key] = (total + 1, disabled + (1 if registry.is_disabled(spec.name) else 0))
    except Exception:
        logger.warning("统计运行态工具计数失败，提供者卡片按 0 工具展示", exc=True)
    return counts


def safe_list_providers(registry: Any) -> List[Dict[str, Any]]:
    """取 Provider 运营摘要清单（旧 registry / mock 未实现时返回空列表）。"""
    fn = getattr(registry, "list_providers", None)
    if fn is None:
        return []
    try:
        records = fn()
    except Exception:  # noqa: BLE001 - 兼容层兜底
        logger.warning("list_providers 调用失败，按空提供者列表处理", exc=True)
        return []
    return [r for r in records if isinstance(r, dict)]


def _default_description(category: str, key: str) -> str:
    known = PROVIDER_DESCRIPTIONS.get((category, key), "")
    if known:
        return known
    return "MCP server" if category == MCP_CATEGORY else ""


def _dotted_get(data: Dict[str, Any], dotted: str) -> Any:
    """按点分路径读嵌套 dict 值（任一层缺失返回 None）。"""
    current: Any = data
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _config_only_card(category: str, key: str, counts: Dict[Tuple[str, str], Tuple[int, int]]) -> Dict[str, Any]:
    total, disabled = counts.get((category, key), (0, 0))
    return {
        "key": key,
        "provider_name": key,
        "description": _default_description(category, key),
        "tool_count": total,
        "disabled_count": disabled,
        "registered": False,
        "degraded": False,
        "supports_reconnect": False,
        "last_error": "",
        "switch": None,
    }


def build_tool_catalog(
    registry: Any, tools_cfg: Dict[str, Any], main_config: Dict[str, Any]
) -> Dict[str, List[Dict[str, Any]]]:
    """装配工具提供者分类目录（注册表运行态 ∪ 配置声明态）。

    每个提供者卡片携带：配置态（``enabled`` / ``in_config`` / ``switchable``）、
    运行态（``tool_count`` / ``disabled_count`` / ``registered`` / ``degraded`` /
    ``last_error`` / ``supports_reconnect``）与 ``notice`` 管理提示。
    分类 = Provider 自声明 ``category``，固定词表外按名排序追加。
    """
    main_config = main_config if isinstance(main_config, dict) else {}
    agents_cfg = main_config.get("agents")
    agents_cfg = agents_cfg if isinstance(agents_cfg, dict) else {}
    agents_enabled = agents_cfg.get("enabled", [])
    agents_enabled = agents_enabled if isinstance(agents_enabled, list) else []
    counts = runtime_tool_counts(registry)

    # 卡片收集：注册表记录建卡（provider 自声明分类）∪ 配置段键补卡
    entries: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for record in safe_list_providers(registry):
        key = str(record.get("name") or "")
        category = str(record.get("category") or "")
        if not key or not category:
            continue
        tool_count = int(record.get("tool_count", 0) or 0)
        switch = record.get("switch") if isinstance(record.get("switch"), dict) else None
        entries[(category, key)] = {
            "key": key,
            "provider_name": key,
            "description": _default_description(category, key),
            "tool_count": tool_count,
            "disabled_count": int(record.get("disabled_count", 0) or 0),
            "registered": True,
            "degraded": tool_count == 0,
            "supports_reconnect": bool(record.get("supports_reconnect", False)),
            "last_error": str(record.get("last_error", "") or ""),
            "switch": switch,
        }

    for category in _CONFIG_MEMBER_CATEGORIES:
        section = tools_cfg.get(category)
        section = section if isinstance(section, dict) else {}
        for key, member_cfg in section.items():
            if key in ("enabled", "config") or not isinstance(member_cfg, dict):
                continue
            entries.setdefault((category, key), _config_only_card(category, key, counts))
    for category in sorted(CATEGORY_LEVEL_KEYS):
        section = tools_cfg.get(category)
        if isinstance(section, dict) and section:
            entries.setdefault((category, category), _config_only_card(category, category, counts))
    mcp_cfg = tools_cfg.get(MCP_CATEGORY)
    mcp_cfg = mcp_cfg if isinstance(mcp_cfg, dict) else {}
    mcp_config = mcp_cfg.get("config")
    mcp_config = mcp_config if isinstance(mcp_config, dict) else {}
    servers_cfg = mcp_config.get("servers")
    servers_cfg = servers_cfg if isinstance(servers_cfg, dict) else {}
    for key, cfg in servers_cfg.items():
        if isinstance(cfg, dict) and cfg:
            entries.setdefault((MCP_CATEGORY, key), _config_only_card(MCP_CATEGORY, key, counts))
    # 首次发现：已知提供者元数据表补卡（从未声明也未装配的提供者保持可见
    # 可开启）；随 Agent 分类（game/framework）不预设——由注册表动态发现
    for category, key in PROVIDER_DESCRIPTIONS:
        if category in AGENT_CATEGORIES:
            continue
        entries.setdefault((category, key), _config_only_card(category, key, counts))

    # 配置态叠加：enabled / in_config / switchable / notice
    for (category, key), entry in entries.items():
        switch = entry.pop("switch", None)
        if switch:
            # 提供者自带开关地址（Agent 私有 MCP 等）：按声明从对应文件读状态
            value = _dotted_get(main_config, str(switch.get("key") or ""))
            entry["enabled"] = bool(value) if value is not None else True
            entry["in_config"] = value is not None
            entry["notice"] = (
                "停用只卸载工具面；独立装配的身体事件采集器仍会连接此 server（见采集器页）"
                if switch.get("file") == "agents.toml"
                else ""
            )
        elif category in AGENT_CATEGORIES:
            # 随 Agent 启用：分类级 flag（有启用中的游戏 Agent / 主播 Agent 在册）
            entry["enabled"] = (
                any(name not in _NON_GAME_AGENT_NAMES for name in agents_enabled)
                if category == "game"
                else category in agents_enabled
            )
            entry["in_config"] = True
            entry["notice"] = ""
        elif key in CATEGORY_LEVEL_KEYS and category == key:
            section = tools_cfg.get(category)
            section = section if isinstance(section, dict) else {}
            entry["enabled"] = bool(section.get("enabled", False))
            entry["in_config"] = bool(section)
            entry["notice"] = ""
        elif category == MCP_CATEGORY:
            cfg = servers_cfg.get(key)
            entry["enabled"] = bool((cfg or {}).get("enabled", True)) if isinstance(cfg, dict) else True
            entry["in_config"] = isinstance(cfg, dict) and bool(cfg)
            entry["notice"] = ""
        else:
            category_cfg = tools_cfg.get(category)
            category_cfg = category_cfg if isinstance(category_cfg, dict) else {}
            member_cfg = category_cfg.get(key)
            member_cfg = member_cfg if isinstance(member_cfg, dict) else {}
            entry["enabled"] = bool(member_cfg.get("enabled", False))
            entry["in_config"] = bool(member_cfg)
            entry["notice"] = ""
        entry["switchable"] = category not in AGENT_CATEGORIES

    # 分类组装：固定词表全量输出（空分类保持侧边栏稳定），词表外按名追加
    seen = {category for category, _key in entries}
    ordered = list(CATEGORY_ORDER) + sorted(seen - set(CATEGORY_ORDER))
    categories: List[Dict[str, Any]] = []
    for category in ordered:
        providers = [entry for (cat, _key), entry in entries.items() if cat == category]
        providers.sort(key=lambda e: (e["description"] == "", e["key"]))
        categories.append({"category": category, "providers": providers})
    return {"categories": categories}
