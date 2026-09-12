"""
Tools API（工具内省与提供者开关端点）

工具清单（只读）：
- GET /api/v1/tools  ->  已注册工具清单（含 provider / kind / category 元数据）

工具提供者面板（观测 + 控制）：
- GET  /api/v1/tools/categories                          -> 提供者分类目录（分类 → 提供者 → 开关 + 运行态计数）
- POST /api/v1/tools/categories/{category}/{key}/control -> 提供者开关写回 tools.toml（重启后生效）
- POST /api/v1/tools/{name}/control                      -> 单个工具停用/启用（写 [tools].disabled_tools，重启后生效）
- POST /api/v1/tools/providers/{provider_id}/reconnect   -> 手动重连指定 Provider（重连成功即探活 + 复位归属工具熔断）

数据源：``DashboardServer.tool_registry``（运行态）与
``ConfigService.main_config`` 的 ``[tools]`` 段（配置态）。

提供者开关语义：一个提供者 = 一个 enabled 开关，控制权归属人类（配置 +
Web UI），AI 主播不可决策；写回后需重启应用让组合根按新开关重新装配
（工具注册发生在启动期）。

可用性动作语义：手动重连用于连接类工具故障（VTS/Warudo/MCP 掉线）时，
从 Dashboard 触发通道级重连；重连成功后对归属该 Provider 的全部已熔断
工具做探活，通过者即刻复位熔断（联动 ``probe_tool`` + ``recover_tool``）。
无连接语义或未覆写 ``connect`` 的 Provider 不暴露按钮（按 ``supports_reconnect``
判定）。
"""

import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Literal, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from src.modules.config.errors import ConfigValidationError
from src.modules.config.multi_file_loader import update_config_values
from src.modules.dashboard.dependencies import get_dashboard_server
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

# 已知提供者成员表（(分类, 提供者配置键, 工具 provider 标识, 描述)）。
# 分类 = avatar(虚拟形象) / studio(演播室) / vision(视觉) / memory(记忆)；
# vision / memory 为分类级开关（键与分类名相同，配置在 [tools.<分类>]）。
# 工具 provider 标识 = ToolSpec.provider（provider 自声明 category 的归组键，
# 与配置键一致）。mcp / game / framework 三个分类单独处理：mcp 的提供者
# （各 server）来自配置；game / framework 随 Agent 启用，不可开关。
_STATIC_CATEGORIES: Tuple[str, ...] = ("avatar", "studio", "vision", "memory")

# 静态成员表（bootstrap 的装配成员 + 组合根直连成员的元数据；分类归属以
# Provider.category 自声明为准，本表供工具页展示"配置中未启用的提供者"）。
_PROVIDER_MEMBERS: Tuple[Tuple[str, str, str, str], ...] = (
    ("avatar", "vts", "vts", "VTubeStudio 控制"),
    ("avatar", "vrchat", "vrchat", "VRChat OSC 桥接"),
    ("avatar", "warudo", "warudo", "Warudo 控制"),
    ("studio", "obs", "obs", "OBS Studio 控制"),
    ("vision", "vision", "vision", "视觉感知（vision_look_at_screen）"),
    ("memory", "memory", "memory", "记忆检索（memory_query_memory）"),
)

# 随 Agent 启用的分类（不可开关；提供者来自 Agent 自声明）。
# game 分类成员随注册表动态（文字冒险游戏等游戏 Agent 自声明），不固定列举。
_AGENT_CATEGORIES: Tuple[Tuple[str, Tuple[Tuple[str, str], ...]], ...] = (
    ("game", (("text_adv", "文字冒险游戏（text_adv_*）"),)),
    ("framework", (("framework", "框架内置（framework_*，如 AgentControl）"),)),
)

# 工具分类 "game" 在 Agent 扁平化后对应任意游戏 Agent 启用
_GAME_AGENT_NAMES: Tuple[str, ...] = ("minecraft", "text_adv")

# 分类级开关的成员（键与分类名相同，配置段为 [tools.<分类>]）。
_CATEGORY_LEVEL_KEYS = {"vision", "memory"}

# 随 Agent 启用的分类键（见 _AGENT_CATEGORIES；不可开关）。

# MCP 分类（提供者 = 各 server，动态来自 [tools.mcp.config.servers]）。
_MCP_CATEGORY = "mcp"

# 关键内部件停用保护清单：这些工具是宿主 Agent 运行控制的唯一操纵面
# （AgentControl 框架工具——暂停/恢复/关闭/重启/状态内省）。误停用会让
# Agent 失去自我控制通道（含 Dashboard 之外无替代入口的 shutdown/restart），
# 因此停用走警示确认语义：请求必须显式携带 confirm=true。
_CRITICAL_TOOL_NAMES: Tuple[str, ...] = (
    "pause_agent",
    "resume_agent",
    "shutdown_agent",
    "restart_agent",
    "list_agents",
    "agent_state",
)


class ProviderControlRequest(BaseModel):
    """提供者/工具开关控制请求体。

    ``confirm``：停用关键内部件（``_CRITICAL_TOOL_NAMES``）时的警示确认
    标记；缺省 False，非关键工具不要求。
    """

    action: Literal["enable", "disable"]
    confirm: bool = False


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


@router.get("/tools/categories", summary="工具提供者分类目录（配置态 + 运行态计数）")
async def list_tool_categories(
    server: "DashboardServer" = Depends(get_dashboard_server),  # noqa: B008
) -> Dict[str, List[Dict[str, Any]]]:
    """工具页侧边栏与提供者分组的数据源。

    每个分类返回其提供者列表；每个提供者携带配置态
    （``[tools.<分类>.<键>].enabled``）与运行态（registry 中该提供者已注册
    的工具数）。配置开但运行时计数为 0，通常意味着改动后尚未重启。

    分类固定为：avatar（虚拟形象）/ studio（演播室）/ vision（视觉）/
    memory（记忆）/ mcp（外部 MCP 工具源）/ game（游戏 Agent 自声明）。
    mcp 的提供者 = 各 server，动态来自 ``[tools.mcp.config.servers]``；
    game 随 agents.toml 启用列表存在，不提供开关。
    """
    registry = _get_registry(server)
    tools_cfg = _get_tools_config(server)
    counts = _runtime_tool_counts(registry)

    def _member_entry(category: str, key: str, provider_name: str, description: str) -> Dict[str, Any]:
        if key in _CATEGORY_LEVEL_KEYS and category == key:
            section = tools_cfg.get(category)
            section = section if isinstance(section, dict) else {}
            enabled = bool(section.get("enabled", False))
            in_config = bool(section)
        else:
            category_cfg = tools_cfg.get(category)
            category_cfg = category_cfg if isinstance(category_cfg, dict) else {}
            member_cfg = category_cfg.get(key)
            member_cfg = member_cfg if isinstance(member_cfg, dict) else {}
            enabled = bool(member_cfg.get("enabled", False))
            in_config = bool(member_cfg)
        total, disabled = counts.get((category, provider_name), (0, 0))
        return {
            "key": key,
            "provider_name": provider_name,
            "description": description,
            "enabled": enabled,
            "in_config": in_config,
            "switchable": True,
            "tool_count": total,
            "disabled_count": disabled,
        }

    categories: List[Dict[str, Any]] = []

    # 静态成员按分类聚组（保持成员表顺序：avatar → studio → vision → memory）
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for category, key, provider_name, description in _PROVIDER_MEMBERS:
        grouped.setdefault(category, []).append(_member_entry(category, key, provider_name, description))
    for category in _STATIC_CATEGORIES:
        categories.append({"category": category, "providers": grouped.get(category, [])})

    # mcp：提供者 = 各 server（动态，来自配置）
    mcp_cfg = tools_cfg.get(_MCP_CATEGORY)
    mcp_cfg = mcp_cfg if isinstance(mcp_cfg, dict) else {}
    mcp_config = mcp_cfg.get("config")
    mcp_config = mcp_config if isinstance(mcp_config, dict) else {}
    servers_cfg = mcp_config.get("servers")
    servers_cfg = servers_cfg if isinstance(servers_cfg, dict) else {}
    mcp_providers = [
        {
            "key": name,
            "provider_name": name,
            "description": "MCP server",
            "enabled": bool((cfg or {}).get("enabled", True)) if isinstance(cfg, dict) else True,
            "in_config": isinstance(cfg, dict) and bool(cfg),
            "switchable": True,
            "tool_count": counts.get((_MCP_CATEGORY, name), (0, 0))[0],
            "disabled_count": counts.get((_MCP_CATEGORY, name), (0, 0))[1],
        }
        for name, cfg in servers_cfg.items()
    ]
    categories.append({"category": _MCP_CATEGORY, "providers": mcp_providers})

    # game / framework：随 Agent 启用，不可开关；提供者 = Agent 自声明清单
    main_config = server.config_service.main_config if server.config_service else {}
    agents_cfg = main_config.get("agents") if isinstance(main_config, dict) else {}
    agents_enabled = agents_cfg.get("enabled", []) if isinstance(agents_cfg, dict) else []
    agents_enabled = agents_enabled if isinstance(agents_enabled, list) else []
    for category, members in _AGENT_CATEGORIES:
        if category == "game":
            enabled = any(name in agents_enabled for name in _GAME_AGENT_NAMES)
        else:
            enabled = category in agents_enabled
        categories.append(
            {
                "category": category,
                "providers": [
                    {
                        "key": provider_name,
                        "provider_name": provider_name,
                        "description": description,
                        "enabled": enabled,
                        "in_config": True,
                        "switchable": False,
                        "tool_count": counts.get((category, provider_name), (0, 0))[0],
                        "disabled_count": counts.get((category, provider_name), (0, 0))[1],
                    }
                    for provider_name, description in members
                ],
            }
        )

    return {"categories": categories}


def _config_dir(server: "DashboardServer") -> Path:
    """从 config_service 推导 config/ 目录（不可用时抛 503）。"""
    svc = server.config_service
    if not (svc and hasattr(svc, "base_dir")):
        raise HTTPException(status_code=503, detail="配置服务不可用")
    return Path(svc.base_dir) / "config"


def _read_tools_doc(config_dir: Path) -> Dict[str, Any]:
    """读 tools.toml 原始文档（只读，缺失/解析失败时返回空 dict）。

    配置管线落盘带 BOM，以 utf-8-sig 剥除后再交给 tomllib（tomllib 拒绝 BOM）。
    """
    path = config_dir / "tools.toml"
    if not path.exists():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:  # noqa: BLE001 - 只读边界：坏文件按空文档处理
        logger.warning(f"读取 tools.toml 失败，按空文档处理: {exc}")
        return {}
    return data if isinstance(data, dict) else {}


def _write_tools_updates(config_dir: Path, updates: Dict[str, Any]) -> None:
    """经统一写回器把变更并入 tools.toml（Schema 校验 + 备份 + 注释重生成）。

    校验失败（ConfigValidationError）映射 400——属调用方提交的非法值；
    其余异常映射 500。校验失败时磁盘零写入。
    """
    try:
        update_config_values(config_dir, "tools.toml", updates)
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
    """把提供者开关写回 tools.toml（统一写回器 update_config_values）。

    写回位置按成员类型区分（点分键，不含 scope 前缀）：
    - avatar / studio 成员：``tools.<分类>.<键>.enabled``
    - vision / memory（键与分类名相同）：``tools.<分类>.enabled``
    - mcp server：``tools.mcp.config.servers.<键>.enabled``
    - game 等自声明分类不可开关（400）

    工具注册发生在组合根装配期（启动时），无动态启停语义——写盘后不触发热
    重载，重启后生效。
    """
    known = {(c, k) for c, k, _p, _d in _PROVIDER_MEMBERS}
    agent_categories = {c for c, _members in _AGENT_CATEGORIES}
    if category in agent_categories:
        raise HTTPException(status_code=400, detail="Agent 自声明工具分类不可开关（随 Agent 启用）")
    if category == _MCP_CATEGORY:
        dotted_key = f"tools.{_MCP_CATEGORY}.config.servers.{key}.enabled"
    elif (category, key) in known:
        dotted_key = f"tools.{category}.enabled" if key in _CATEGORY_LEVEL_KEYS else f"tools.{category}.{key}.enabled"
    else:
        raise HTTPException(status_code=400, detail=f"未知工具提供者: {category}/{key}")

    enable = request.action == "enable"
    config_dir = _config_dir(server)
    _write_tools_updates(config_dir, {dotted_key: enable})

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

    关键内部件（``_CRITICAL_TOOL_NAMES``）停用走警示确认：缺 ``confirm=true``
    时返回 400 并附中文风险说明；非关键工具不受影响。
    """
    enable = request.action == "enable"
    registry = _get_registry(server)
    try:
        known = {s.name for s in registry.list_tools(include_disabled=True, include_tripped=True)}
    except Exception:
        known = set()
    if not enable and name not in known:
        raise HTTPException(status_code=404, detail=f"运行时未注册工具: {name}")
    if not enable and name in _CRITICAL_TOOL_NAMES and not request.confirm:
        raise HTTPException(
            status_code=400,
            detail=(
                f"工具 {name} 是宿主 Agent 运行控制的关键内部件，停用会导致 Agent "
                "失去自我控制通道（暂停/恢复/关闭/重启不可用）。如确认停用，请携带 "
                "confirm: true 重新提交。"
            ),
        )

    config_dir = _config_dir(server)
    doc = _read_tools_doc(config_dir)
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

    _write_tools_updates(config_dir, {"tools.disabled_tools": disabled})

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
    具名列表）、``still_tripped``（探活未通过的熔断工具名列表）。详情日志
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
