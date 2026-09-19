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
from typing import TYPE_CHECKING, Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from src.modules.config.errors import ConfigValidationError
from src.modules.config.multi_file_loader import update_config_values
from src.modules.dashboard.dependencies import get_dashboard_server
from src.modules.dashboard.services.tool_catalog import (
    AGENT_CATEGORIES,
    CATEGORY_LEVEL_KEYS,
    MCP_CATEGORY,
    PROVIDER_DESCRIPTIONS,
    build_tool_catalog,
    safe_list_providers,
)
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

# 目录装配常量（分类词表/提供者描述/开关分类）与卡片拼装在 services/tool_catalog，
# 本模块只保留路由与 HTTP 语义。


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


def _safe_tool_health_snapshot(registry: Any) -> Dict[str, Dict[str, Any]]:
    """取工具健康快照（旧 registry / mock 未实现时返回空 dict）。"""
    snapshot = getattr(registry, "tool_health_snapshot", None)
    if snapshot is None:
        return {}
    try:
        result = snapshot()
    except Exception:
        logger.warning("tool_health_snapshot 调用失败，按无健康数据处理", exc_info=True)
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
        logger.warning("list_tools 调用失败，按空工具清单处理", exc_info=True)
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

    提供者卡片以**运行时注册表为事实源**，∪ 配置声明态；卡片字段与分类
    排序规则见 ``services.tool_catalog.build_tool_catalog``。
    """
    registry = _get_registry(server)
    tools_cfg = _get_tools_config(server)
    main_config = server.config_service.main_config if server.config_service else {}
    return build_tool_catalog(registry, tools_cfg, main_config)


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
    if category in AGENT_CATEGORIES:
        raise HTTPException(status_code=400, detail="随 Agent 启用的工具分类不可开关（随 agents.enabled）")
    registry = _get_registry(server)
    enable = request.action == "enable"
    cfg_dir = config_dir(server)

    # 提供者自带开关地址优先（绑定处声明，注册表记录透出）
    switch = next(
        (
            record["switch"]
            for record in safe_list_providers(registry)
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

    if category == MCP_CATEGORY:
        dotted_key = f"tools.{MCP_CATEGORY}.config.servers.{key}.enabled"
    elif (category, key) in PROVIDER_DESCRIPTIONS:
        dotted_key = f"tools.{category}.enabled" if key in CATEGORY_LEVEL_KEYS else f"tools.{category}.{key}.enabled"
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
        logger.warning("读取注册表工具名失败，按空名册处理（停用校验将放行未知名）", exc_info=True)
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
