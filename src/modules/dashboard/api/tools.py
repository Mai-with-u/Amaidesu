"""
Tools API（工具内省与提供者开关端点）

工具清单（只读）：
- GET /api/v1/tools  ->  已注册工具清单（含 provider / kind / category 元数据）

工具提供者面板（观测 + 控制）：
- GET  /api/v1/tools/categories                          -> 提供者分类目录（分类 → 提供者 → 开关 + 运行态计数）
- POST /api/v1/tools/categories/{category}/{key}/control -> 提供者开关写回声明配置（重启后生效）
- POST /api/v1/tools/{name}/control                      -> 单个工具停用/启用（写 [tools].disabled_tools，重启后生效）
- POST /api/v1/tools/providers/{provider_id}/reconnect   -> 手动重连指定 Provider（重连 + 工具集刷新 + 探活复位熔断）

数据源：**运行时注册表为事实源**（``DashboardServer.tool_registry``，含连接
失败降级登记的 0 工具 Provider），∪ 配置声明态（``ConfigService.main_config``
的 ``[tools]`` / ``[agents]`` 段：enabled=false 未装配的提供者也展示）。

提供者开关语义：一个提供者 = 一个 enabled 开关，控制权归属人类（配置 +
Web UI），AI 主播不可决策；写回后需重启应用让组合根按新开关重新装配
（工具注册发生在启动期）。写回位置由提供者声明（``switch_config``，如
Agent 私有 MCP 写 agents.toml）或按分类静态路由（默认 tools.toml）。

可用性动作语义：手动重连用于连接类工具故障（VTS/Warudo/MCP 掉线）时，
从 Dashboard 触发通道级重连；重连成功后先刷新工具集（降级登记补注册 /
server 清单变化换血），再对归属该 Provider 的已熔断工具做探活，通过者
即刻复位熔断。无连接语义或未覆写 ``connect`` 的 Provider 不暴露按钮
（按 ``supports_reconnect`` 判定）。
"""

from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Literal, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from src.modules.config.errors import ConfigValidationError
from src.modules.config.multi_file_loader import update_config_values
from src.modules.dashboard.dependencies import get_dashboard_server
from src.modules.dashboard.utils.component_helper import config_dir, read_toml_dict
from src.modules.logging import get_logger

if TYPE_CHECKING:
    from src.modules.dashboard.server import DashboardServer

logger = get_logger("DashboardToolsAPI")

router = APIRouter()


# JSON Schema 类型 → 前端 ParameterType 的映射。
# 仅支持 spec 实际使用的标量类型（string/integer/number/boolean）。
_TYPE_MAP: Dict[str, str] = {
    "string": "string",
    "integer": "integer",
    "number": "number",
    "boolean": "boolean",
}

# 分类展示顺序（固定词表；注册表出现词表外分类时按名排序追加在后）。
# 提供者成员不再静态硬编码——注册表（运行态）∪ 配置段键（声明态）。
_CATEGORY_ORDER: Tuple[str, ...] = ("avatar", "studio", "vision", "memory", "mcp", "game", "framework")

# 已知提供者的展示描述（(分类, 提供者键) → 文案）。仅为显示元数据，不构成
# 成员事实；查不到的提供者按分类给默认描述。
_PROVIDER_DESCRIPTIONS: Dict[Tuple[str, str], str] = {
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
_CATEGORY_LEVEL_KEYS = {"vision", "memory"}

# 随 Agent 启用的分类（不可开关；提供者 = Agent 自声明，注册表动态发现）。
_AGENT_CATEGORIES: Tuple[str, ...] = ("game", "framework")

# MCP 分类（提供者 = 注册表 MCP Provider ∪ [tools.mcp.config.servers] 配置键）。
_MCP_CATEGORY = "mcp"


class ProviderControlRequest(BaseModel):
    """提供者/工具开关控制请求体。"""

    action: Literal["enable", "disable"]


def _convert_parameters_schema(schema: Any) -> Dict[str, Dict[str, Any]]:
    """把 JSON Schema 形态的 parameters_schema 转成前端 ParameterSpec 字典。

    输入形态（ToolSpec.parameters_schema 约定的 JSON Schema 形状）：
        {"type": "object", "properties": {"k": {"type": "string", "description": "..."}},
         "required": ["k"]}

    输出形态（前端工具页消费的 ``Record<key, ParameterSpec>``）：
        {"k": {"type": "string", "required": True, "description": "...",
               "default": ..., "minimum": ..., "maximum": ...}}

    非 dict 输入返回空 dict；缺 properties 时返回空 dict。
    """
    if not isinstance(schema, dict):
        return {}
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return {}
    required = schema.get("required")
    required_set: set[str] = set(required) if isinstance(required, list) else set()

    result: Dict[str, Dict[str, Any]] = {}
    for key, prop in properties.items():
        if not isinstance(key, str) or not isinstance(prop, dict):
            continue
        json_type = prop.get("type")
        param_type = _TYPE_MAP.get(json_type) if isinstance(json_type, str) else None
        if param_type is None:
            # 未知/缺失类型 — 跳过（前端只接受四种标量类型）
            continue
        entry: Dict[str, Any] = {"type": param_type, "required": key in required_set}
        for src_key, dst_key in (
            ("description", "description"),
            ("default", "default"),
            ("minimum", "minimum"),
            ("maximum", "maximum"),
        ):
            if src_key in prop:
                entry[dst_key] = prop[src_key]
        result[key] = entry
    return result


def _build_action_entry(
    spec: Any,
    category: str,
    disabled: bool = False,
    owner_agent: str = "",
    supports_reconnect: bool = False,
) -> Dict[str, Any]:
    """构造单个工具条目（工具清单视图，供前端展示）。

    ``owner_agent`` 由调用方从 registry 传入（scoped_owner_of）；空串表示无
    归属限定（通用工具）。前端"归属列展示"留待后续——目前默认返回全部已含。

    ``supports_reconnect`` 由调用方按归属 Provider 判定（registry 提
    供 ``provider_supports_reconnect(name)``）；用于工具行渲染"手动重连"
    按钮的可视条件。无连接语 Provider 一律 False。
    """
    entry: Dict[str, Any] = {
        "name": spec.name,
        "description": getattr(spec, "description", "") or "",
        "parameters": _convert_parameters_schema(getattr(spec, "parameters_schema", None)),
        "provider": getattr(spec, "provider", "") or "",
        "kind": getattr(spec, "kind", "") or "sync",
        "category": category,
        "disabled": disabled,
        "owner_agent": owner_agent,
        "supports_reconnect": supports_reconnect,
    }
    if entry["kind"] == "async":
        entry["result_event"] = spec.resolve_result_event()
    return entry


def _get_registry(server: "DashboardServer") -> Any:
    """取 ToolRegistry；未注入（极简启动/测试场景）时抛 503。"""
    registry = getattr(server, "tool_registry", None)
    if registry is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ToolRegistry 未注入",
        )
    return registry


def _runtime_tool_counts(registry: Any) -> Dict[Tuple[str, str], Tuple[int, int]]:
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
        pass
    return counts


def _safe_tool_health_snapshot(registry: Any) -> Dict[str, Dict[str, Any]]:
    """取工具健康快照（旧 registry / mock 未实现时返回空 dict）。"""
    snapshot = getattr(registry, "tool_health_snapshot", None)
    if snapshot is None:
        return {}
    try:
        result = snapshot()
    except Exception:
        return {}
    return result if isinstance(result, dict) else {}


def _format_tool_health(entry: Any) -> Optional[Dict[str, Any]]:
    """从快照单项构建响应 health 字段；absent / 无效输入返回 None。

    触发条件：工具有过失败历史（即出现在快照里）；不存在则视为"无历史"，
    响应字段不出现 health。前端按"无徽标"渲染。
    """
    if not isinstance(entry, dict):
        return None
    state = entry.get("state")
    if state not in ("tripped", "healthy"):
        return None
    return {
        "state": state,
        "failure_count": int(entry.get("failure_count", 0) or 0),
        "last_error": str(entry.get("last_error", "") or ""),
        "tripped_at_ms": int(entry.get("tripped_at_ms", 0) or 0),
    }


def _supports_reconnect(registry: Any, tool_name: str) -> bool:
    """按工具名查归属 Provider 是否支持手动重连（不可用/未实现方法时返回 False）。

    registry 兼容旧版/missing：未实现 ``provider_supports_reconnect`` 时回
    退为 False（前端不渲染手动重连按钮）。FastAPI 响应构造里调一次/工具，
    量级 O(工具数)，单次调用经 ``_tool_owner`` 直查不开销。
    """
    fn = getattr(registry, "provider_supports_reconnect", None)
    if fn is None:
        return False
    try:
        return bool(fn(tool_name))
    except Exception:  # noqa: BLE001 - 兼容层兜底
        return False


def _get_tools_config(server: "DashboardServer") -> Dict[str, Any]:
    """读主配置的 ``[tools]`` 段（缺失 / 非 dict 时返回空 dict）。"""
    main_config = server.config_service.main_config if server.config_service else {}
    tools_cfg = main_config.get("tools") if isinstance(main_config, dict) else {}
    return tools_cfg if isinstance(tools_cfg, dict) else {}


def _safe_list_providers(registry: Any) -> List[Dict[str, Any]]:
    """取 Provider 运营摘要清单（旧 registry / mock 未实现时返回空列表）。"""
    fn = getattr(registry, "list_providers", None)
    if fn is None:
        return []
    try:
        records = fn()
    except Exception:  # noqa: BLE001 - 兼容层兜底
        return []
    return [r for r in records if isinstance(r, dict)]


def _dotted_get(data: Dict[str, Any], dotted: str) -> Any:
    """按点分路径读嵌套 dict 值（任一层缺失返回 None）。"""
    current: Any = data
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


@router.get("/tools", summary="列出所有已注册工具（含 provider/kind/category 元数据）")
async def list_tools(
    provider: Optional[str] = None,
    server: "DashboardServer" = Depends(get_dashboard_server),  # noqa: B008
) -> Dict[str, List[Dict[str, Any]]]:
    """工具注册表只读内省（含停用工具，``disabled`` 字段标记状态）。

    Args:
        provider: 可选过滤（提供者标识，如 "vts" / "maicraft"），透传 registry。

    Returns:
        {"tools": [{"name", "description", "parameters", "provider", "kind",
                    "category", "disabled", "result_event"(仅异步工具),
                    "health": 可选熔断/历史健康对象；无历史则为 None}, ...]}
    """
    registry = _get_registry(server)

    try:
        specs = registry.list_tools(
            provider=provider,
            include_disabled=True,
            include_tripped=True,
            include_scoped=True,  # Dashboard 工具页是运营面：可见一切
        )
    except Exception:
        specs = []

    health_snapshot = _safe_tool_health_snapshot(registry)
    tools = [
        _build_action_entry(
            spec,
            category=registry.category_of(spec.name),
            disabled=registry.is_disabled(spec.name),
            owner_agent=getattr(registry, "scoped_owner_of", lambda _n: "")(spec.name),
            supports_reconnect=_supports_reconnect(registry, spec.name),
        )
        for spec in specs
    ]
    for entry in tools:
        entry["health"] = _format_tool_health(health_snapshot.get(entry["name"]))
    return {"tools": tools}


@router.get("/tools/categories", summary="工具提供者分类目录（注册表运行态 ∪ 配置声明态）")
async def list_tool_categories(
    server: "DashboardServer" = Depends(get_dashboard_server),  # noqa: B008
) -> Dict[str, List[Dict[str, Any]]]:
    """工具页侧边栏与提供者分组的数据源。

    提供者卡片以**运行时注册表为事实源**（``registry.list_providers()``，
    含连接失败降级登记的 0 工具 Provider），∪ 配置声明态（``[tools.*]``
    段键：enabled=false 未装配的提供者也展示——"配置已声明、重启后装配"）。
    分类 = Provider 自声明 ``category``（固定词表见 ``_CATEGORY_ORDER``，
    词表外分类动态追加）。每个提供者卡片携带：

    - 配置态：``enabled`` / ``in_config`` / ``switchable``
    - 运行态：``tool_count`` / ``disabled_count`` / ``registered``（registry
      有登记）/ ``degraded``（已登记但 0 工具，通常连接失败）/
      ``last_error``（降级原因）/ ``supports_reconnect``（提供者级手动重连）
    - ``notice``：随卡片展示的管理提示（如 Agent 私有 MCP 停用后，独立
      装配的身体事件采集器仍会连接此 server）
    """
    registry = _get_registry(server)
    tools_cfg = _get_tools_config(server)
    main_config = server.config_service.main_config if server.config_service else {}
    main_config = main_config if isinstance(main_config, dict) else {}
    agents_cfg = main_config.get("agents")
    agents_cfg = agents_cfg if isinstance(agents_cfg, dict) else {}
    agents_enabled = agents_cfg.get("enabled", [])
    agents_enabled = agents_enabled if isinstance(agents_enabled, list) else []
    counts = _runtime_tool_counts(registry)

    # ---- 卡片收集：注册表记录建卡（provider 自声明分类）∪ 配置段键补卡 ----
    entries: Dict[Tuple[str, str], Dict[str, Any]] = {}

    def _default_description(category: str, key: str) -> str:
        known = _PROVIDER_DESCRIPTIONS.get((category, key), "")
        if known:
            return known
        return "MCP server" if category == _MCP_CATEGORY else ""

    for record in _safe_list_providers(registry):
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

    def _config_only_card(category: str, key: str) -> Dict[str, Any]:
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

    for category in _CONFIG_MEMBER_CATEGORIES:
        section = tools_cfg.get(category)
        section = section if isinstance(section, dict) else {}
        for key, member_cfg in section.items():
            if key in ("enabled", "config") or not isinstance(member_cfg, dict):
                continue
            entries.setdefault((category, key), _config_only_card(category, key))
    for category in sorted(_CATEGORY_LEVEL_KEYS):
        section = tools_cfg.get(category)
        if isinstance(section, dict) and section:
            entries.setdefault((category, category), _config_only_card(category, category))
    mcp_cfg = tools_cfg.get(_MCP_CATEGORY)
    mcp_cfg = mcp_cfg if isinstance(mcp_cfg, dict) else {}
    mcp_config = mcp_cfg.get("config")
    mcp_config = mcp_config if isinstance(mcp_config, dict) else {}
    servers_cfg = mcp_config.get("servers")
    servers_cfg = servers_cfg if isinstance(servers_cfg, dict) else {}
    for key, cfg in servers_cfg.items():
        if isinstance(cfg, dict) and cfg:
            entries.setdefault((_MCP_CATEGORY, key), _config_only_card(_MCP_CATEGORY, key))
    # 首次发现：已知提供者元数据表补卡（从未声明也未装配的提供者保持可见
    # 可开启）；随 Agent 分类（game/framework）不预设——由注册表动态发现
    for category, key in _PROVIDER_DESCRIPTIONS:
        if category in _AGENT_CATEGORIES:
            continue
        entries.setdefault((category, key), _config_only_card(category, key))

    # ---- 配置态叠加：enabled / in_config / switchable / notice ----
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
        elif category in _AGENT_CATEGORIES:
            # 随 Agent 启用：分类级 flag（有启用中的游戏 Agent / 主播 Agent 在册）
            entry["enabled"] = (
                any(name not in _NON_GAME_AGENT_NAMES for name in agents_enabled)
                if category == "game"
                else category in agents_enabled
            )
            entry["in_config"] = True
            entry["notice"] = ""
        elif key in _CATEGORY_LEVEL_KEYS and category == key:
            section = tools_cfg.get(category)
            section = section if isinstance(section, dict) else {}
            entry["enabled"] = bool(section.get("enabled", False))
            entry["in_config"] = bool(section)
            entry["notice"] = ""
        elif category == _MCP_CATEGORY:
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
        entry["switchable"] = category not in _AGENT_CATEGORIES

    # ---- 分类组装：固定词表全量输出（空分类保持侧边栏稳定），词表外按名追加 ----
    seen = {category for category, _key in entries}
    ordered = list(_CATEGORY_ORDER) + sorted(seen - set(_CATEGORY_ORDER))
    categories: List[Dict[str, Any]] = []
    for category in ordered:
        providers = [entry for (cat, _key), entry in entries.items() if cat == category]
        providers.sort(key=lambda e: (e["description"] == "", e["key"]))
        categories.append({"category": category, "providers": providers})
    return {"categories": categories}


def _write_config_updates(config_dir: Path, file_name: str, updates: Dict[str, Any]) -> None:
    """经统一写回器把变更并入指定配置文件（Schema 校验 + 备份 + 注释重生成）。

    校验失败（ConfigValidationError）映射 400——属调用方提交的非法值；
    其余异常映射 500。校验失败时磁盘零写入。
    """
    try:
        update_config_values(config_dir, file_name, updates)
    except ConfigValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - 配置写回边界
        raise HTTPException(status_code=500, detail=f"配置写回失败: {exc}") from exc


@router.post("/tools/categories/{category}/{key}/control", summary="提供者开关写回（重启后生效）")
async def control_tool_provider(
    category: str,
    key: str,
    request: ProviderControlRequest,
    server: "DashboardServer" = Depends(get_dashboard_server),  # noqa: B008
) -> Dict[str, Any]:
    """把提供者开关写回其声明配置（统一写回器 update_config_values）。

    写回位置按成员类型区分（点分键，不含 scope 前缀）：
    - 提供者自带开关地址（``switch_config``，如 Agent 私有 MCP）：写声明
      的文件与键（``agents.minecraft.mcp.enabled`` → agents.toml）
    - avatar / studio 成员：``tools.<分类>.<键>.enabled``
    - vision / memory（键与分类名相同）：``tools.<分类>.enabled``
    - mcp 全局 server：``tools.mcp.config.servers.<键>.enabled``
    - game / framework 等随 Agent 分类不可开关（400）

    工具注册发生在组合根装配期（启动时），无动态启停语义——写盘后不触发热
    重载，重启后生效。
    """
    if category in _AGENT_CATEGORIES:
        raise HTTPException(status_code=400, detail="随 Agent 启用的工具分类不可开关（随 agents.enabled）")
    registry = _get_registry(server)
    enable = request.action == "enable"
    cfg_dir = config_dir(server)

    # 提供者自带开关地址优先（绑定处声明，注册表记录透出）
    switch = next(
        (
            record["switch"]
            for record in _safe_list_providers(registry)
            if record.get("name") == key and isinstance(record.get("switch"), dict)
        ),
        None,
    )
    if switch is not None:
        file_name = str(switch.get("file") or "")
        dotted_key = str(switch.get("key") or "")
        if not file_name or not dotted_key:
            raise HTTPException(status_code=500, detail=f"提供者 {category}/{key} 的开关地址声明不完整")
        _write_config_updates(cfg_dir, file_name, {dotted_key: enable})
        action_text = "启用" if enable else "停用"
        return {
            "success": True,
            "enabled": enable,
            "message": f"工具提供者 {category}.{key} 已{action_text}（写入 {file_name}），重启后生效",
        }

    if category == _MCP_CATEGORY:
        dotted_key = f"tools.{_MCP_CATEGORY}.config.servers.{key}.enabled"
    elif (category, key) in _PROVIDER_DESCRIPTIONS:
        dotted_key = f"tools.{category}.enabled" if key in _CATEGORY_LEVEL_KEYS else f"tools.{category}.{key}.enabled"
    else:
        raise HTTPException(status_code=400, detail=f"未知工具提供者: {category}/{key}")

    _write_config_updates(cfg_dir, "tools.toml", {dotted_key: enable})

    action_text = "启用" if enable else "停用"
    return {
        "success": True,
        "enabled": enable,
        "message": f"工具提供者 {category}.{key} 已{action_text}（写入 tools.toml），重启后生效",
    }


@router.post("/tools/{name}/control", summary="单个工具停用/启用（写 [tools].disabled_tools，重启后生效）")
async def control_tool(
    name: str,
    request: ProviderControlRequest,
    server: "DashboardServer" = Depends(get_dashboard_server),  # noqa: B008
) -> Dict[str, Any]:
    """把工具名加入/移出 ``tools.disabled_tools`` 停用列表（统一写回器落盘）。

    停用的工具仍保留在注册表中（工具页可见全集），但对 LLM 不可见且调用被
    拒绝；写盘后不触发热重载，重启后生效。工具名必须在运行时注册表中存在
    （防止拼写错误静默写入无效条目，404）。
    """
    enable = request.action == "enable"
    registry = _get_registry(server)
    try:
        known = {s.name for s in registry.list_tools(include_disabled=True, include_tripped=True)}
    except Exception:
        known = set()
    if not enable and name not in known:
        raise HTTPException(status_code=404, detail=f"运行时未注册工具: {name}")

    cfg_dir = config_dir(server)
    doc = read_toml_dict(cfg_dir / "tools.toml")
    tools_section = doc.get("tools")
    tools_section = tools_section if isinstance(tools_section, dict) else {}
    raw = tools_section.get("disabled_tools")
    disabled = [n for n in raw if isinstance(n, str)] if isinstance(raw, list) else []

    if enable:
        disabled = [n for n in disabled if n != name]
    elif name not in disabled:
        disabled.append(name)
    disabled = sorted(set(disabled))

    unknown = [n for n in disabled if n not in known]
    if unknown:
        logger.warning(f"disabled_tools 含运行时未注册的工具名（重启后若仍不存在则不生效）: {unknown}")

    _write_config_updates(cfg_dir, "tools.toml", {"tools.disabled_tools": disabled})

    action_text = "启用" if enable else "停用"
    return {
        "success": True,
        "enabled": enable,
        "message": f"工具 {name} 已{action_text}（写入 tools.toml），重启后生效",
    }


@router.post(
    "/tools/providers/{provider_id}/reconnect",
    summary="手动重连指定 Provider（成功后联动探活 + 复位归属工具熔断）",
)
async def reconnect_provider_endpoint(
    provider_id: str,
    server: "DashboardServer" = Depends(get_dashboard_server),  # noqa: B008
) -> Dict[str, Any]:
    """触发通道级手动重连：仅维护外部连接（VTS/Warudo/OBS/MCP）的 Provider
    可用。失败映射：

    - 404 → registry 中无此 provider_id（未注册 / 拼写错误）
    - 409 → registry 已注册但 Provider 不支持重连（非 BaseToolProvider /
      ``supports_reconnect`` 为 False，例如无连接语 Provider 与内置 spec 工厂
      生成的 Provider）

    成功（200）返回报告 dict：``provider_id``、``recovered``（已复位熔断的工
    具名列表）、``still_tripped``（探活未通过的熔断工具名列表）、
    ``refreshed``（工具集刷新报告：``added`` / ``removed`` / ``count``，
    降级登记补注册或 server 清单换血的结果；刷新异常时为 null）。详情日志
    走 ``ToolRegistry.reconnect_provider``。
    """
    registry = _get_registry(server)
    fn = getattr(registry, "reconnect_provider", None)
    if fn is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ToolRegistry 不支持 reconnect_provider（旧版无此能力）",
        )
    report = await fn(provider_id)
    if report.get("ok"):
        return report
    err = report.get("error", "")
    if "未注册 Provider" in err:
        raise HTTPException(status_code=404, detail=err)
    if "不支持手动重连" in err:
        raise HTTPException(status_code=409, detail=err)
    raise HTTPException(status_code=500, detail=err)
