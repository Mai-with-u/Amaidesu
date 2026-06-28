"""
Capabilities API

暴露 Output 阶段所有 handler 的能力查询端点:
- GET /api/v1/capabilities  ->  全限定 action 列表(带 handler 前缀)
- GET /api/v1/handlers      ->  合法 handler 名列表(供 Plugin 预验证)

`output_manager` 通过 `ManagerStatusProvider` 协议注入(避免 Dashboard 反向依赖
OutputHandlerManager 具体类)。如果 manager 没有 `get_all_capabilities` /
`get_handler_names` 方法,返回空集合(而不是 500),让 endpoints 在任意 Manager
实现下都安全。
"""

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, status

from src.modules.dashboard.dependencies import get_dashboard_server

if TYPE_CHECKING:
    from src.modules.dashboard.server import DashboardServer

router = APIRouter()


def _get_output_manager(server: "DashboardServer") -> Any:
    output_mgr = getattr(server, "output_manager", None)
    if output_mgr is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OutputHandlerManager 未注入",
        )
    return output_mgr


@router.get("/capabilities", summary="列出所有可用的 Output handler action(全限定名)")
async def list_capabilities(
    server: "DashboardServer" = Depends(get_dashboard_server),  # noqa: B008
) -> dict[str, Any]:
    mgr = _get_output_manager(server)
    getter = getattr(mgr, "get_all_capabilities", None)
    if getter is None:
        return {"actions": []}
    view = getter()
    return view.model_dump()


@router.get("/handlers", summary="列出所有已注册的 Output handler 名称")
async def list_handlers(
    server: "DashboardServer" = Depends(get_dashboard_server),  # noqa: B008
) -> dict[str, list[str]]:
    mgr = _get_output_manager(server)
    getter = getattr(mgr, "get_handler_names", None)
    if getter is None:
        return {"handlers": []}
    return {"handlers": getter()}
