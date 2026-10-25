"""Avatar Dashboard API：表情差分图静态资源端点

端点：

- ``GET /api/v1/avatar/expression_sheet/assets/{filename}``  按文件名服务差分图

行为约定：

- 数据源是已装配的 ``ExpressionSheetProvider`` 解析出的差分图目录
  （``image_dir``）；Provider 未装配 / 未配置目录时一律 404（小部件页
  据此回退 neutral 差分图，neutral 也 404 时显示"未配置"占位）。
- 安全硬要求：``filename`` 只接受纯文件名——含路径分隔符（``/``、``\\``）
  或 ``..`` 的一律 404，杜绝路径穿越读到目录外文件。
- 无鉴权、无缓存语义、绝不 500 裸崩（异常路径 404/日志）。

调试台端点（走三后端同语义跨平台同名的契约工具面，不给单一平台开后门）：

- ``GET /api/v1/avatar/debug/status``  各平台的连接态 / 当前表情 / 目录等
  摘要。数据全部经 ``AvatarProvider`` 契约方法与 ``get_stats`` 公共面取；
  平台未暴露的信息（如"当前表情"各平台均无读取通道）返回 null 并在
  detail 说明，绝不伪造。
- ``POST /api/v1/avatar/debug/trigger``  按 contract 方法转发执行：
  kind=expression 走 ``set_expression``（17 情绪 + 强度），kind=parameter
  仅对保留了参数写入方法的平台（VTS）转发，其余平台明确报不支持。
  未装配 / 未连接 / 平台不支持分别返回 404 / 409 / 400，业务拒绝以
  success=false 的 200 响应返回（调用方需看到失败原因）。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from src.modules.avatar.platform.expression_sheet.expression_sheet_provider import SUPPORTED_IMAGE_EXTS
from src.modules.dashboard.dependencies import get_dashboard_server
from src.modules.logging import get_logger
from src.modules.types.emotion_vocab import Emotion

if TYPE_CHECKING:
    from src.modules.dashboard.server import DashboardServer

logger = get_logger("DashboardAvatarAPI")

router = APIRouter()

# 调试台覆盖的平台名单（与 avatar.toml [avatar.platform] 成员一致）
DEBUG_PLATFORMS: Tuple[str, ...] = ("vts", "vrchat", "warudo", "expression_sheet")

# 平台参数直调支持面：设备微旋钮已从 LLM 工具面撤出，仅 VTS 保留了参数写入
# 方法实体，其余平台无安全的参数写入口
PARAMETER_CAPABLE_PLATFORMS: Tuple[str, ...] = ("vts",)


# ===== Pydantic Schema =====


class AvatarDebugPlatformStatus(BaseModel):
    """单平台调试状态：能取多少取多少，取不到的字段 null 并说明。"""

    platform: str
    connected: Optional[bool] = None
    current_expression: Optional[str] = None
    detail: str = ""
    actions: List[Dict[str, Any]] = Field(default_factory=list)
    emotions: List[str] = Field(default_factory=list)
    stats: Dict[str, Any] = Field(default_factory=dict)


class AvatarDebugStatusResponse(BaseModel):
    """调试台状态聚合响应。"""

    platforms: List[AvatarDebugPlatformStatus]


class AvatarDebugTriggerRequest(BaseModel):
    """调试触发请求：expression（情绪+强度）或 parameter（参数名+数值）。"""

    platform: str
    kind: str = Field(description="expression（情绪+强度）| parameter（参数名+数值，仅 VTS）| action（预设动作名）")
    key: str = Field(description="expression=情绪名（17 枚举值）；parameter=参数名；action=预设动作名")
    value: float = Field(default=0.5, description="expression=强度 0.0-1.0；parameter=目标数值")


class AvatarDebugTriggerResponse(BaseModel):
    """调试触发结果：HTTP 200 下 success=false 表示业务拒绝（原因在 detail）。"""

    platform: str
    kind: str
    key: str
    value: float
    success: bool
    detail: str = ""
    structured_content: Optional[Dict[str, Any]] = None


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

    拒绝路径分隔符（``/``、``\\``）、父目录引用（``..``）与其他任何
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


def _validate_platform(platform: str) -> str:
    """校验平台名在调试台名单内；未知平台 404（合法名单随错误返回）。"""
    if platform not in DEBUG_PLATFORMS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"未知平台 '{platform}'（合法名单：{', '.join(DEBUG_PLATFORMS)}）",
        )
    return platform


def _get_platform_provider(server: "DashboardServer", platform: str) -> Optional[Any]:
    """经注册表按契约工具全名反查平台 Provider；未装配返回 None。

    与表情差分静态资源端点同一套定位路径（provider_of_tool）；未连接的
    ConnectionToolProvider 降级登记 0 工具，但 Provider 本体仍在册可反查。
    """
    registry = getattr(server, "tool_registry", None)
    if registry is None:
        return None
    try:
        return registry.provider_of_tool(f"{platform}_set_expression")
    except Exception:  # noqa: BLE001 - 兼容层兜底：查不到按未装配处理
        logger.warning(f"按工具名定位平台 Provider 失败（{platform}），按未装配处理", exc=True)
        return None


def _connection_state(provider: Any) -> Tuple[Optional[bool], str]:
    """读平台连接态，返回 (连接态或 None, 说明文案)。

    三种公共面按可用性取：``get_stats()["is_connected"]``（VTS/VRChat/Warudo）
    → ``is_ready`` 属性（表情差分）→ ``_connected`` 标志；都没有则返回
    None（"平台未暴露连接态"）。
    """
    get_stats = getattr(provider, "get_stats", None)
    if callable(get_stats):
        try:
            stats = get_stats()
            value = stats.get("is_connected") if isinstance(stats, dict) else None
            if isinstance(value, bool):
                return value, ""
        except Exception:  # noqa: BLE001 - 统计面读取失败按未暴露处理
            logger.warning("get_stats 读取失败，按平台未暴露连接态处理", exc=True)
    if isinstance(getattr(provider, "is_ready", None), bool):
        return bool(provider.is_ready), ""
    connected = getattr(provider, "_connected", None)
    if isinstance(connected, bool):
        return connected, ""
    return None, "平台未暴露连接态"


async def _fetch_actions(provider: Any) -> List[Dict[str, Any]]:
    """经契约方法 list_preset_actions 取动作目录；失败按空目录处理（记日志）。"""
    try:
        result = await provider.list_preset_actions()
        content = getattr(result, "structured_content", None)
        if isinstance(content, dict) and isinstance(content.get("actions"), list):
            return [a for a in content["actions"] if isinstance(a, dict)]
    except Exception:  # noqa: BLE001 - 目录读取失败不阻断状态聚合
        logger.warning("list_preset_actions 读取失败，按空目录处理", exc=True)
    return []


def _read_stats(provider: Any) -> Dict[str, Any]:
    """读平台 get_stats 公共面摘要；不可用返回空 dict。"""
    get_stats = getattr(provider, "get_stats", None)
    if not callable(get_stats):
        return {}
    try:
        stats = get_stats()
        return dict(stats) if isinstance(stats, dict) else {}
    except Exception:  # noqa: BLE001 - 统计面读取失败按空处理
        logger.warning("get_stats 读取失败，按空摘要处理", exc=True)
        return {}


@router.get("/debug/status", response_model=AvatarDebugStatusResponse, summary="各平台调试状态")
async def get_avatar_debug_status(
    server: "DashboardServer" = Depends(get_dashboard_server),  # noqa: B008
) -> AvatarDebugStatusResponse:
    """聚合四平台调试状态：连接态 / 当前表情 / 动作目录 / 统计摘要。

    全部走契约方法与 get_stats 公共面；Provider 未装配的平台按 connected=false +
    说明降级列出（前端可见"未装配"而非报错）。
    """
    emotion_values = [e.value for e in Emotion]
    platforms: List[AvatarDebugPlatformStatus] = []
    for platform in DEBUG_PLATFORMS:
        provider = _get_platform_provider(server, platform)
        if provider is None:
            platforms.append(
                AvatarDebugPlatformStatus(
                    platform=platform,
                    connected=False,
                    detail="平台未装配（未启用或未注册工具）",
                    emotions=emotion_values,
                )
            )
            continue
        connected, conn_detail = _connection_state(provider)
        # 当前表情：各平台均未暴露"上次设置的情绪"读取通道，诚实返回 null
        if conn_detail:
            detail = conn_detail
        elif not connected:
            detail = "平台未连接"
        else:
            detail = "平台未暴露当前表情状态"
        platforms.append(
            AvatarDebugPlatformStatus(
                platform=platform,
                connected=connected,
                current_expression=None,
                detail=detail,
                actions=await _fetch_actions(provider),
                emotions=emotion_values,
                stats=_read_stats(provider),
            )
        )
    return AvatarDebugStatusResponse(platforms=platforms)


@router.post("/debug/trigger", response_model=AvatarDebugTriggerResponse, summary="调试触发表情/参数")
async def trigger_avatar_debug(
    request: AvatarDebugTriggerRequest,
    server: "DashboardServer" = Depends(get_dashboard_server),  # noqa: B008
) -> AvatarDebugTriggerResponse:
    """转发触发到平台契约方法；前置不满足时以明确 HTTP 错误返回。

    前置校验链：平台名 → kind → Provider 已装配 → 平台已连接 → 平台支持该
    kind；通过后调用契约方法，业务拒绝（如未知情绪名）以 success=false 的
    200 响应回传，不静默吞。
    """
    _validate_platform(request.platform)
    if request.kind not in ("expression", "parameter", "action"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"未知 kind '{request.kind}'（合法值：expression / parameter / action）",
        )
    if request.kind == "parameter" and request.platform not in PARAMETER_CAPABLE_PLATFORMS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"平台 '{request.platform}' 不支持参数直调"
                f"（参数微旋钮已撤出工具面，支持平台：{', '.join(PARAMETER_CAPABLE_PLATFORMS)}）"
            ),
        )

    provider = _get_platform_provider(server, request.platform)
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"平台 '{request.platform}' 未装配（未启用或未注册工具）",
        )
    connected, _ = _connection_state(provider)
    if connected is not True:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"平台 '{request.platform}' 未连接，无法触发",
        )

    try:
        if request.kind == "expression":
            result = await provider.set_expression(request.key, request.value)
        elif request.kind == "action":
            result = await provider.trigger_preset_action(request.key)
        else:
            result = await provider.set_parameter_value(request.key, request.value)
    except Exception as exc:  # noqa: BLE001 - 转发边界兜底：失败原因如实回传
        logger.error(f"调试触发转发失败（{request.platform}/{request.kind}）: {exc}", exc=True)
        return AvatarDebugTriggerResponse(
            platform=request.platform,
            kind=request.kind,
            key=request.key,
            value=request.value,
            success=False,
            detail=f"转发执行异常：{type(exc).__name__}: {exc}",
        )

    structured = getattr(result, "structured_content", None)
    if isinstance(result, bool):
        # 保留的旧式方法（如 VTS set_parameter_value）返回裸 bool
        success = result
        detail = "" if result else "平台返回失败"
    else:
        success = bool(getattr(result, "success", False))
        detail = "" if success else str(getattr(result, "error_message", "") or "平台返回失败")
    return AvatarDebugTriggerResponse(
        platform=request.platform,
        kind=request.kind,
        key=request.key,
        value=request.value,
        success=success,
        detail=detail,
        structured_content=structured if isinstance(structured, dict) else None,
    )


__all__ = ["router"]
