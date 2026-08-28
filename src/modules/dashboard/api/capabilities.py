"""
Capabilities API（v2 适配 Wave U1 / B3）

暴露已注册工具的能力查询端点（只读）：
- GET /api/v1/capabilities  ->  工具清单（按 provider 限定名）

数据源：``DashboardServer.tool_registry.list_tools()``，与 OutputHandlerManager
彻底解耦（v2 中已不存在 output_manager）。

⚠️ 协调点：本端点提供**只读**注册表内省。工具体系团队若后续提供正式的
``/api/v1/tools`` 端点（含调用能力），本端点可并存或由其替代。

删除（Wave U1 / B4）：
- ~~GET /api/v1/handlers~~ — v1 遗物，零消费
"""

from typing import TYPE_CHECKING, Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, status

from src.modules.dashboard.dependencies import get_dashboard_server

if TYPE_CHECKING:
    from src.modules.dashboard.server import DashboardServer

router = APIRouter()


# JSON Schema 类型 → 前端 ParameterType 的映射。
# 仅支持 spec 实际使用的标量类型（string/integer/number/boolean）。
_TYPE_MAP: Dict[str, str] = {
    "string": "string",
    "integer": "integer",
    "number": "number",
    "boolean": "boolean",
}


def _convert_parameters_schema(schema: Any) -> Dict[str, Dict[str, Any]]:
    """把 JSON Schema 形态的 parameters_schema 转成前端 ParameterSpec 字典。

    输入形态（ToolSpec.parameters_schema 约定的 JSON Schema 形状）：
        {"type": "object", "properties": {"k": {"type": "string", "description": "..."}},
         "required": ["k"]}

    输出形态（CapabilitiesPanel.vue 消费的 ``Record<key, ParameterSpec>``）：
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


def _build_action_entry(provider: str, tool_name: str, description: str, parameters_schema: Any) -> Dict[str, Any]:
    """构造单个 action 条目（与 UnifiedActionEntry 对齐）。"""
    entry: Dict[str, Any] = {
        "name": f"{provider}.{tool_name}",
        "description": description or "",
        "parameters": _convert_parameters_schema(parameters_schema),
    }
    return entry


@router.get("/capabilities", summary="列出所有已注册工具（按 provider 限定名）")
async def list_capabilities(
    server: "DashboardServer" = Depends(get_dashboard_server),  # noqa: B008
) -> Dict[str, List[Dict[str, Any]]]:
    """工具注册表只读内省（v2 替代 v1 OutputHandlerManager 假数据）。

    Returns:
        {"actions": [{"name": "<provider>.<tool>", "description": ...,
                       "parameters": {...}}, ...]}

    Raises:
        HTTPException 503: tool_registry 未注入（通常发生在极简启动/测试场景）。
    """
    registry = getattr(server, "tool_registry", None)
    if registry is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ToolRegistry 未注入",
        )

    try:
        specs = registry.list_tools()
    except Exception:
        specs = []

    actions = [
        _build_action_entry(
            provider=getattr(spec, "provider", "builtin") or "builtin",
            tool_name=spec.name,
            description=getattr(spec, "description", "") or "",
            parameters_schema=getattr(spec, "parameters_schema", None),
        )
        for spec in specs
    ]
    return {"actions": actions}
