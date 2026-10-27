"""
Tools API（工具内省与提供者开关端点）

工具清单（只读）：
- GET /api/v1/tools  ->  已注册工具清单（含 provider / kind / category 元数据）

工具提供者面板（观测 + 控制）：
- GET  /api/v1/tools/categories                          -> 提供者分类目录（分类 → 提供者 → 开关 + 运行态计数）
- POST /api/v1/tools/categories/{category}/{key}/control -> 提供者开关写回声明配置（重启后生效）
- POST /api/v1/tools/{full_name}/control                 -> 单个工具停用/启用（写 [tools].disabled_tools，重启后生效）
- POST /api/v1/tools/{full_name}/invoke                  -> 调试调用工具（与 Agent 同路径经 registry 真实执行）
- POST /api/v1/tools/providers/{provider_id}/reconnect   -> 手动重连指定 Provider（重连 + 工具集刷新 + 探活复位熔断）

数据源：**运行时注册表为事实源**（``DashboardServer.tool_registry``，含连接
失败降级登记的 0 工具 Provider），∪ 配置声明态（``ConfigService.main_config``
的 ``[tools]`` / ``[agents]`` 段：enabled=false 未装配的提供者也展示）。
运营观测与重连动作（提供者目录 / 健康快照 / 标注投影 / 手动重连）经
``DashboardServer.tool_operations``（``ToolOperations``）触达，注册表窄接口
不承载运营视图。

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

import json
from typing import TYPE_CHECKING, Annotated, Any, Dict, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException

from src.modules.dashboard.dependencies import get_dashboard_server, server_component
from src.modules.dashboard.schemas.tool_catalog import ToolCatalogResponse
from src.modules.dashboard.schemas.tools import (
    ProviderControlRequest,
    ToolInvokeRequest,
    ToolInvokeResponse,
    ToolListResponse,
    ToolToggleResponse,
)
from src.modules.dashboard.services.component_runtime import ComponentRuntimeContext
from src.modules.dashboard.services.config_adapter import ConfigApplyOutcome, apply_config_updates
from src.modules.dashboard.services.tool_catalog import (
    AGENT_CATEGORIES,
    CATEGORY_LEVEL_KEYS,
    MCP_CATEGORY,
    PROVIDER_DESCRIPTIONS,
    build_action_entry,
    build_tool_catalog,
    format_tool_health,
    safe_list_providers,
    serialize_execution_result,
)
from src.modules.dashboard.utils.component_helper import config_dir, read_toml_dict
from src.modules.logging import get_logger
from src.modules.utils.time_utils import now_ms
from src.modules.tools.models import ToolInvocation

if TYPE_CHECKING:
    from src.modules.dashboard.server import DashboardServer

logger = get_logger("DashboardToolsAPI")

router = APIRouter()


# 目录装配常量（分类词表/提供者描述/开关分类）、卡片拼装与工具条目/健康
# 字段/调试调用响应的构造在 services/tool_catalog，本模块只保留路由与 HTTP 语义。

# 组件访问声明：ToolRegistry 未注入（极简启动/测试场景）时统一 503
RegistryDep = Annotated[Any, Depends(server_component("tool_registry", missing_detail="ToolRegistry 未注入"))]
# 工具运营面（提供者目录视图 / 健康快照 / 标注投影 / 重连编排）：与 registry
# 分离的只读伴生对象（ToolOperations），组合根随 registry 一并注入
ToolOpsDep = Annotated[Any, Depends(server_component("tool_operations", missing_detail="ToolOperations 未注入"))]


def _get_tools_config(server: "DashboardServer") -> Dict[str, Any]:
    """读主配置的 ``[tools]`` 段（缺失 / 非 dict 时返回空 dict）。"""
    main_config = server.config_service.main_config if server.config_service else {}
    tools_cfg = main_config.get("tools") if isinstance(main_config, dict) else {}
    return tools_cfg if isinstance(tools_cfg, dict) else {}


@router.get(
    "/tools",
    response_model=ToolListResponse,
    # exclude_unset：result_event / 参数可选键按"源声明才输出"的原样透出
    # （同步工具不出现 result_event 键、未声明 default 的参数不出现 default 键）；
    # health 恒有键（无历史为 null），入参显式携带不受 exclude_unset 影响
    response_model_exclude_unset=True,
    summary="列出所有已注册工具（含 provider/kind/category 元数据）",
)
async def list_tools(
    provider: Optional[str] = None,
    server: "DashboardServer" = Depends(get_dashboard_server),  # noqa: B008
    registry: RegistryDep = None,  # noqa: B008
    tool_ops: ToolOpsDep = None,  # noqa: B008
) -> ToolListResponse:
    """工具注册表只读内省（含停用工具，``disabled`` 字段标记状态）。

    Args:
        provider: 可选过滤（提供者标识，如 "vts" / "maicraft"），透传 registry。

    Returns:
        {"tools": [{"name", "description", "parameters", "provider", "kind",
                    "category", "disabled", "result_event"(仅异步工具),
                    "health": 可选熔断/历史健康对象；无历史则为 None}, ...]}
    """
    try:
        specs = registry.list_tools(
            provider=provider,
            include_disabled=True,
            include_tripped=True,
            include_scoped=True,  # Dashboard 工具页是运营面：可见一切
        )
    except Exception:
        logger.warning("list_tools 调用失败，按空工具清单处理", exc=True)
        specs = []

    health_snapshot = tool_ops.tool_health_snapshot()
    tools = [
        build_action_entry(
            spec,
            # category_of 按注册表索引键（全名）反查，传短名会落空返回空分类
            category=registry.category_of(spec.full_name),
            # 停用集合按注册表索引键（全名）存储，必须用全名判定
            disabled=registry.is_disabled(spec.full_name),
            owner_agent=tool_ops.scoped_owner_of(spec.full_name),
            supports_reconnect=tool_ops.provider_supports_reconnect(spec.name),
        )
        for spec in specs
    ]
    for entry in tools:
        entry["health"] = format_tool_health(health_snapshot.get(entry["name"]))
    return {"tools": tools}


@router.get(
    "/tools/categories", response_model=ToolCatalogResponse, summary="工具提供者分类目录（注册表运行态 ∪ 配置声明态）"
)
async def list_tool_categories(
    server: "DashboardServer" = Depends(get_dashboard_server),  # noqa: B008
    registry: RegistryDep = None,  # noqa: B008
    tool_ops: ToolOpsDep = None,  # noqa: B008
) -> ToolCatalogResponse:
    """工具页侧边栏与提供者分组的数据源。

    提供者卡片以**运行时注册表为事实源**，∪ 配置声明态；卡片字段与分类
    排序规则见 ``services.tool_catalog.build_tool_catalog``。
    """
    tools_cfg = _get_tools_config(server)
    main_config = server.config_service.main_config if server.config_service else {}
    return ToolCatalogResponse.model_validate(build_tool_catalog(tool_ops, registry, tools_cfg, main_config))


async def _apply_config_toggle(server: "DashboardServer", changes: list[tuple[str, Any]]) -> ConfigApplyOutcome:
    """经配置写路径统一编排落盘（与设置页保存同一管线：校验 → 写回 → 热重载 →
    agents / collectors 域组件重建热应用）。

    入参是带 scope 前缀的 API 键值对；前置校验或 Schema 校验失败按 422 拒绝，
    磁盘零写入。
    """
    if server.config_service is None:
        raise HTTPException(status_code=503, detail="Config service 不可用")
    outcome = await apply_config_updates(
        server.config_service,
        config_dir(server),
        changes,
        hot_context=ComponentRuntimeContext.from_server(server),
    )
    if not outcome.success:
        raise HTTPException(status_code=422, detail=outcome.message)
    return outcome


def _toggle_message(outcome: ConfigApplyOutcome, subject: str, *, enable: bool) -> str:
    """开关写回的响应话术：落盘位置与生效方式照实说（热域组件已重建即已生效）。"""
    action_text = "启用" if enable else "停用"
    where = f"（写入 {outcome.target_file}）" if outcome.target_file else ""
    effect = "已即时生效" if not outcome.requires_restart else "重启后生效"
    return f"{subject} 已{action_text}{where}，{effect}"


@router.post(
    "/tools/categories/{category}/{key}/control",
    response_model=ToolToggleResponse,
    summary="提供者开关写回（重启后生效）",
)
async def control_tool_provider(
    category: str,
    key: str,
    request: ProviderControlRequest,
    server: "DashboardServer" = Depends(get_dashboard_server),  # noqa: B008
    tool_ops: ToolOpsDep = None,  # noqa: B008
) -> ToolToggleResponse:
    """把提供者开关写回其声明配置（配置写路径统一编排）。

    写回位置按成员类型区分（点分键，API 键带 scope 前缀）：
    - 提供者自带开关地址（``switch_config``，如 Agent 私有 MCP）：写声明
      的文件与键（``agents.minecraft.mcp.enabled`` → agents.toml；agents 域
      在跑组件随写回重建，已即时生效）
    - avatar 平台成员：``avatar.platform.enabled`` 启用名单增删
    - studio 成员：``tools.<分类>.<键>.enabled``
    - vision（键与分类名相同）：``tools.<分类>.enabled``
    - mcp 全局 server：``tools.mcp.config.servers.<键>.enabled``
    - game / framework / memory 等随 Agent 分类不可开关（400）

    工具与虚拟形象域的注册发生在组合根装配期（启动时），无动态启停语义
    ——写盘后重启生效。
    """
    if category in AGENT_CATEGORIES:
        raise HTTPException(status_code=400, detail="随 Agent 启用的工具分类不可开关（随 agents.enabled）")
    enable = request.action == "enable"
    cfg_dir = config_dir(server)

    # 提供者自带开关地址优先（绑定处声明，注册表记录透出）
    switch = next(
        (
            record["switch"]
            for record in safe_list_providers(tool_ops)
            if record.get("name") == key and isinstance(record.get("switch"), dict)
        ),
        None,
    )
    if switch is not None:
        file_name = str(switch.get("file") or "")
        dotted_key = str(switch.get("key") or "")
        if not file_name or not dotted_key:
            raise HTTPException(status_code=500, detail=f"提供者 {category}/{key} 的开关地址声明不完整")
        scope = file_name.removesuffix(".toml")
        outcome = await _apply_config_toggle(server, [(f"{scope}.{dotted_key}", enable)])
        return {
            "success": True,
            "enabled": enable,
            "message": _toggle_message(outcome, f"工具提供者 {category}.{key}", enable=enable),
        }

    if category == MCP_CATEGORY:
        dotted_key = f"tools.{MCP_CATEGORY}.config.servers.{key}.enabled"
    elif category == "avatar":
        # avatar 平台开合 = avatar.toml 启用名单增删（文件根即 AvatarRootConfig，
        # 文件内路径为 platform.enabled，无 avatar 前缀）
        doc = read_toml_dict(cfg_dir / "avatar.toml")
        platform = doc.get("platform")
        platform = platform if isinstance(platform, dict) else {}
        raw = platform.get("enabled", [])
        names = [n for n in raw if isinstance(n, str)] if isinstance(raw, list) else []
        if enable:
            if key not in names:
                names.append(key)
        else:
            names = [n for n in names if n != key]
        outcome = await _apply_config_toggle(server, [("avatar.platform.enabled", names)])
        return {
            "success": True,
            "enabled": enable,
            "message": _toggle_message(outcome, f"虚拟形象平台 {category}.{key}", enable=enable),
        }
    elif (category, key) in PROVIDER_DESCRIPTIONS:
        dotted_key = f"tools.{category}.enabled" if key in CATEGORY_LEVEL_KEYS else f"tools.{category}.{key}.enabled"
    else:
        raise HTTPException(status_code=400, detail=f"未知工具提供者: {category}/{key}")

    outcome = await _apply_config_toggle(server, [(f"tools.{dotted_key}", enable)])
    return {
        "success": True,
        "enabled": enable,
        "message": _toggle_message(outcome, f"工具提供者 {category}.{key}", enable=enable),
    }


@router.post(
    "/tools/{full_name}/control",
    response_model=ToolToggleResponse,
    summary="单个工具停用/启用（写 [tools].disabled_tools，重启后生效）",
)
async def control_tool(
    full_name: str,
    request: ProviderControlRequest,
    server: "DashboardServer" = Depends(get_dashboard_server),  # noqa: B008
    registry: RegistryDep = None,  # noqa: B008
) -> ToolToggleResponse:
    """把工具加入/移出 ``tools.disabled_tools`` 停用列表（统一写回器落盘）。

    停用的工具仍保留在注册表中（工具页可见全集），但对 LLM 不可见且调用被
    拒绝；写盘后不触发热重载，重启后生效。``full_name`` 是注册表调用键
    （``<provider>_<工具名>``，工具清单接口的 ``full_name`` 字段），与
    ``registry.apply_disabled`` 的过滤口径一致；工具必须在运行时注册表中
    存在（防止拼写错误静默写入无效条目，404）。
    """
    enable = request.action == "enable"
    try:
        known = {s.full_name for s in registry.list_tools(include_disabled=True, include_tripped=True)}
    except Exception:
        logger.warning("读取注册表工具名失败，按空名册处理（停用校验将放行未知名）", exc=True)
        known = set()
    if not enable and full_name not in known:
        raise HTTPException(status_code=404, detail=f"运行时未注册工具: {full_name}")

    cfg_dir = config_dir(server)
    doc = read_toml_dict(cfg_dir / "tools.toml")
    tools_section = doc.get("tools")
    tools_section = tools_section if isinstance(tools_section, dict) else {}
    raw = tools_section.get("disabled_tools")
    disabled = [n for n in raw if isinstance(n, str)] if isinstance(raw, list) else []

    if enable:
        disabled = [n for n in disabled if n != full_name]
    elif full_name not in disabled:
        disabled.append(full_name)
    disabled = sorted(set(disabled))

    unknown = [n for n in disabled if n not in known]
    if unknown:
        logger.warning(f"disabled_tools 含运行时未注册的工具名（重启后若仍不存在则不生效）: {unknown}")

    outcome = await _apply_config_toggle(server, [("tools.tools.disabled_tools", disabled)])
    return {
        "success": True,
        "enabled": enable,
        "message": _toggle_message(outcome, f"工具 {full_name}", enable=enable),
    }


# 调试调用 arguments 的体积上限（字节）：registry 完成调用后会把 arguments
# 全文放进 tool.result 事件 payload（WS 广播 + event_recorder 落库），在调试
# 输入口掐掉大文本，防事件流被超大参数污染。LLM 路径天然受输出 token 约束，
# 无需此限制。
_MAX_ARGUMENTS_BYTES = 64 * 1024


@router.post(
    "/tools/{full_name}/invoke",
    response_model=ToolInvokeResponse,
    summary="调试调用工具（与 Agent 同路径经 registry 真实执行）",
)
async def invoke_tool(
    full_name: str,
    request: ToolInvokeRequest,
    server: "DashboardServer" = Depends(get_dashboard_server),  # noqa: B008
    registry: RegistryDep = None,  # noqa: B008
) -> ToolInvokeResponse:
    """人手调试调用：走 ``registry.invoke``，与 Planner/Agent 完全同路径。

    停用/熔断短路、熔断计数、``tool.result`` 事件广播全部真实生效——调试
    看到的就是 Agent 会经历的（含"调试失败推进熔断"这一真实行为）。
    ``source="dashboard-debug"`` 标记人手调用，事件流与日志可据此与 Agent
    调用区分。``full_name`` 是注册表调用键（工具清单接口的 ``full_name``
    字段）。

    HTTP 语义：200 = 调用已执行（工具级失败也是 200 + ``success=false``，
    失败结果是有效调试产出）；400 = arguments 超限/不可序列化；404 = 工具
    不存在。同步工具响应即最终结果；异步工具响应是受理回执，真实结果经
    ``tool.result`` 事件（WS 直通）异步回传。
    """
    if not registry.has(full_name):
        raise HTTPException(status_code=404, detail=f"运行时未注册工具: {full_name}")
    try:
        arguments_bytes = len(json.dumps(request.arguments, ensure_ascii=False, default=str).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"arguments 无法 JSON 序列化: {exc}") from exc
    if arguments_bytes > _MAX_ARGUMENTS_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"arguments 超限: {arguments_bytes} 字节 > 上限 {_MAX_ARGUMENTS_BYTES} 字节",
        )
    invocation = ToolInvocation(
        tool_name=full_name,
        arguments=request.arguments,
        call_id=uuid4().hex[:12],
        invoked_at_ms=now_ms(),
        source="dashboard-debug",
    )
    logger.info(f"调试调用工具 '{full_name}'（call_id={invocation.call_id}，arguments {arguments_bytes} 字节）")
    result = await registry.invoke(invocation)
    return serialize_execution_result(result)


@router.post(
    "/tools/providers/{provider_id}/reconnect",
    summary="手动重连指定 Provider（成功后联动探活 + 复位归属工具熔断）",
)
async def reconnect_provider_endpoint(
    provider_id: str,
    server: "DashboardServer" = Depends(get_dashboard_server),  # noqa: B008
    tool_ops: ToolOpsDep = None,  # noqa: B008
) -> Dict[str, Any]:
    """触发通道级手动重连：仅维护外部连接（VTS/Warudo/OBS/MCP）的 Provider
    可用。失败映射：

    - 404 → 运营面中无此 provider_id（未注册 / 拼写错误）
    - 409 → 已注册但 Provider 不支持重连（非 BaseToolProvider /
      ``supports_reconnect`` 为 False，例如无连接语 Provider 与内置 spec 工厂
      生成的 Provider）

    成功（200）返回报告 dict：``provider_id``、``recovered``（已复位熔断的工
    具名列表）、``still_tripped``（探活未通过的熔断工具名列表）、
    ``refreshed``（工具集刷新报告：``added`` / ``removed`` / ``count``，
    降级登记补注册或 server 清单换血的结果；刷新异常时为 null）。详情日志
    走 ``ToolOperations.reconnect_provider``。
    """
    report = await tool_ops.reconnect_provider(provider_id)
    if report.get("ok"):
        return report
    # reason 是机器可读的失败枚举（ToolOperations.reconnect_provider 报告契约），
    # 状态码映射按它判定；中文 error 文案只作展示，不做子串匹配
    status_by_reason = {"not_registered": 404, "not_supported": 409, "reconnect_failed": 500}
    err = report.get("error", "")
    raise HTTPException(status_code=status_by_reason.get(report.get("reason"), 500), detail=err)
