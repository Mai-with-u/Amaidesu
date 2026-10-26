"""
Dashboard 依赖注入

提供 FastAPI 依赖注入所需的服务器实例，以及 api 层统一的组件访问点
（"这个组件这次装没装、没装怎么办"的一处定义）。
"""

from typing import TYPE_CHECKING, Any, Callable, Optional

from fastapi import Depends, HTTPException, status

if TYPE_CHECKING:
    from src.modules.dashboard.server import DashboardServer

# 全局服务器实例
_dashboard_server: Optional["DashboardServer"] = None


def set_dashboard_server(server: "DashboardServer") -> None:
    """设置 Dashboard 服务器实例"""
    global _dashboard_server
    _dashboard_server = server


def get_dashboard_server() -> "DashboardServer":
    """获取 Dashboard 服务器实例（用于 FastAPI Depends）"""
    if _dashboard_server is None:
        raise NotImplementedError("Dashboard server not initialized")
    return _dashboard_server


def server_component(
    attr: str,
    *,
    missing_detail: str = "",
    required: bool = True,
) -> Callable[..., Any]:
    """组件访问点工厂：api 端点声明"要哪个组件、缺了怎么办"，缺失语义一处定义。

    生产装配下 ``DashboardServer.__init__`` 为全部组件属性赋值（装配缺席即
    None），因此缺失判定就是"属性值为 None"。两种缺失语义：

    - ``required=True``（默认）：缺失 → 503 + ``missing_detail``（装配缺失
      是环境问题不是业务失败，前端 ``getApiErrorMessage`` 直接可读）；
    - ``required=False``：缺失 → None，端点按自身响应模型降级（如
      ``is_available=false``、空数据）。

    用法：api 模块顶部为每个组件声明一个 ``Annotated[类型, Depends(...)]``
    别名，端点签名里出现该别名即"声明要这个组件"，端点体内不再写
    getattr 探测与 503 分支。

    Args:
        attr: ``DashboardServer`` 上的组件属性名。
        missing_detail: required 缺失时的 503 detail 文案。
        required: 是否必需组件（见上）。
    """

    def _resolve(server: "DashboardServer" = Depends(get_dashboard_server)) -> Any:  # noqa: B008 - FastAPI 依赖注入模式
        value = getattr(server, attr, None)
        if value is not None:
            return value
        if required:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=missing_detail)
        return None

    return _resolve
