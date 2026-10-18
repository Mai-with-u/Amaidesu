"""Avatar Dashboard API：表情差分图静态资源端点

端点：

- ``GET /api/v1/avatar/expression_sheet/assets/{filename}``  按文件名服务差分图

行为约定：

- 数据源是已装配的 ``ExpressionSheetProvider`` 解析出的差分图目录
  （``image_dir``）；Provider 未装配 / 未配置目录时一律 404（小部件页
  据此回退 neutral 差分图，neutral 也 404 时显示"未配置"占位）。
- 安全硬要求：``filename`` 只接受纯文件名——含路径分隔符（``/``、``\``）
  或 ``..`` 的一律 404，杜绝路径穿越读到目录外文件。
- 无鉴权、无缓存语义、绝不 500 裸崩（异常路径 404/日志）。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse

from src.modules.avatar.platform.expression_sheet.expression_sheet_provider import SUPPORTED_IMAGE_EXTS
from src.modules.dashboard.dependencies import get_dashboard_server
from src.modules.logging import get_logger

if TYPE_CHECKING:
    from src.modules.dashboard.server import DashboardServer

logger = get_logger("DashboardAvatarAPI")

router = APIRouter()


def _get_expression_sheet_provider(server: "DashboardServer") -> Optional[Any]:
    """从工具注册表定位表情差分 Provider 实例；未装配时返回 None。

    定位路径：注册表按工具名反查归属 Provider——未就绪的 Provider 降级
    登记 0 工具，此路自然返回 None（与"未配置 → 404"语义一致）。
    """
    registry = getattr(server, "tool_registry", None)
    if registry is None:
        return None
    try:
        return registry.provider_of_tool("expression_sheet_set_expression")
    except Exception:  # noqa: BLE001 - 兼容层兜底：查不到按未装配处理
        logger.warning("按工具名定位表情差分 Provider 失败，按未装配处理", exc=True)
        return None


def _validate_pure_filename(filename: str) -> str:
    """校验请求的文件名是纯文件名（防路径穿越）；非法一律 404。

    拒绝路径分隔符（``/``、``\``）、父目录引用（``..``）与其他任何
    与 basename 不一致的形态。
    """
    if (
        not filename
        or "/" in filename
        or "\\" in filename
        or ".." in filename
        or ":" in filename  # Windows 盘符前缀（如 "C:foo"）也是路径语义
        or Path(filename).name != filename
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="非法文件名",
        )
    return filename


@router.get("/expression_sheet/assets/{filename}", summary="获取表情差分图")
async def get_expression_sheet_asset(
    filename: str,
    server: "DashboardServer" = Depends(get_dashboard_server),  # noqa: B008
) -> FileResponse:
    """按文件名服务差分图（数据源 = Provider 已解析的差分图目录）。

    Provider 未装配 / 目录未配置 / 文件不存在 → 404（小部件页回退链依据）。
    """
    _validate_pure_filename(filename)
    provider = _get_expression_sheet_provider(server)
    image_dir = getattr(provider, "image_dir", None) if provider is not None else None
    if not isinstance(image_dir, Path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="表情差分形象未配置（请在 avatar.toml [avatar.platform.expression_sheet] 配置 image_dir）",
        )
    file_path = image_dir / filename
    if not file_path.is_file() or file_path.suffix.lower() not in SUPPORTED_IMAGE_EXTS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="差分图不存在")
    return FileResponse(file_path)


__all__ = ["router"]
