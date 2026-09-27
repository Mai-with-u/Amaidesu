"""Vision Dashboard API：显示器枚举与预览端点。

端点：

- ``GET /api/v1/vision/monitors``  列出当前所有显示器（mss 枚举；含虚拟合屏 0）
- ``GET /api/v1/vision/preview``   抓一帧 + 在图上叠加区域框（可选）

行为约定：

- 不调 VLM、不加缓存 / 轮询 / 视频流 / 鉴权。
- 失败处理：参数非法 → 400 + 明确错误体；mss 不可用 / 抓取失败 / 无显示器 →
  503 + 明确错误体。**绝不 500 裸崩**。
- 区域叠加策略（保持简单，注释说明）：
  - 始终以全显示器抓取（``capture(... region=None ...)``，让叠加层画框），
    不在 ``capture`` 阶段按 region 裁剪——返回图就是显示器全图，区域框直接
    按用户传入坐标画在图上。
  - 显示器边界框 = 整图边框；因返回图已是显示器全图，故不再单独绘制边界框
    （图本身的边缘即是显示器边缘，标注价值低，避免叠加层干扰主信息）。
"""

from __future__ import annotations

import asyncio
import base64
import io
from typing import Annotated, List, Optional

from fastapi import APIRouter, HTTPException, Query
from PIL import Image, ImageDraw
from pydantic import BaseModel, Field

from src.modules.logging import get_logger
from src.modules.vision.mss_capture import MssScreenCapture

logger = get_logger("DashboardVisionAPI")

router = APIRouter()


class MonitorItem(BaseModel):
    """单个显示器信息（mss 枚举结果投影）。"""

    index: int = Field(..., description="mss 索引（0=虚拟合屏，1..N=物理显示器）")
    left: int = Field(..., description="显示器左上角在虚拟桌面坐标系中的 X（像素）")
    top: int = Field(..., description="显示器左上角在虚拟桌面坐标系中的 Y（像素）")
    width: int = Field(..., description="显示器宽度（像素）")
    height: int = Field(..., description="显示器高度（像素）")
    is_primary: bool = Field(..., description="是否包含虚拟桌面 (0, 0) 点——即系统主屏")


class MonitorsResponse(BaseModel):
    """显示器列表响应。"""

    count: int = Field(..., description="返回的显示器条目数")
    monitors: List[MonitorItem]


class PreviewResponse(BaseModel):
    """预览响应：单帧截图 + 元数据。"""

    image_b64: str = Field(..., description="PNG 图像的 base64 编码（叠加区域框后）")
    width: int = Field(..., description="图像宽度（像素）")
    height: int = Field(..., description="图像高度（像素）")
    monitor_index: int = Field(..., description="请求传入的显示器索引（回显）")
    region: Optional[List[int]] = Field(
        default=None,
        description="区域 [x1, y1, x2, y2]（相对显示器左上角，已规范化方向）；无区域则 None",
    )


def _parse_region(region_str: Optional[str]) -> Optional[List[int]]:
    """解析 ``region`` 查询字符串 ``"x1,y1,x2,y2"`` → ``[x1, y1, x2, y2]``。

    行为：

    - 空串 / None → 返回 ``None``（无区域，不画框）。
    - 非 4 个分量 / 任一分量非整数 → 抛 ``HTTPException(400)``，附明确错误体。
    - 方向写反（如 ``x2 < x1``）自动交换，不视为错误（前端拖框可能反向）。

    Raises:
        HTTPException: 400 + 明确错误体（参数非法）。
    """
    if region_str is None or region_str.strip() == "":
        return None
    parts = [p.strip() for p in region_str.split(",")]
    if len(parts) != 4:
        raise HTTPException(
            status_code=400,
            detail=(f"region 必须是 4 个逗号分隔整数（'x1,y1,x2,y2'），收到 {len(parts)} 个值: {region_str!r}"),
        )
    try:
        coords = [int(p) for p in parts]
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"region 各分量必须是整数，收到 {region_str!r}: {exc}",
        ) from exc
    if coords[2] < coords[0]:
        coords[0], coords[2] = coords[2], coords[0]
    if coords[3] < coords[1]:
        coords[1], coords[3] = coords[3], coords[1]
    return coords


@router.get(
    "/monitors",
    response_model=MonitorsResponse,
    summary="列出当前所有显示器（mss 枚举）",
)
async def list_monitors() -> MonitorsResponse:
    """mss 枚举显示器（含虚拟合屏 index=0 与各物理显示器 index=1..N）。

    mss 不可用 / 枚举失败 → 返回 ``count=0`` + ``monitors=[]`` + 200（枚举失败
    不视为端点错误——前端可由空列表判定无可用显示器；503 由 ``preview`` 端点
    表达"想抓但抓不到"）。
    """
    cap = MssScreenCapture()
    monitors = cap.list_monitors()
    return MonitorsResponse(
        count=len(monitors),
        monitors=[
            MonitorItem(
                index=m.index,
                left=m.left,
                top=m.top,
                width=m.width,
                height=m.height,
                is_primary=m.is_primary,
            )
            for m in monitors
        ],
    )


def _draw_region_overlay(img: Image.Image, region: List[int]) -> None:
    """在图上叠加区域框（红色矩形 + 黑色阴影偏移，提升暗底可见性）。

    区域超出图边界的部分自动截断到图内；区域完全在图外则不绘制（避免无意义
    噪声）。不在图上叠加"显示器边界框"——因返回图已是显示器全图，图像边缘
    即为显示器边缘（详见模块 docstring）。
    """
    x1, y1, x2, y2 = region
    x2 = min(x2, img.width)
    y2 = min(y2, img.height)
    if x1 >= img.width or y1 >= img.height or x2 <= x1 or y2 <= y1:
        return
    draw = ImageDraw.Draw(img)
    # 主色：红（3px）；偏移 1px 黑色描边，提升暗背景可见性
    for ox, oy, color in ((0, 0, (255, 0, 0)), (1, 1, (0, 0, 0))):
        draw.rectangle(
            [(x1 + ox, y1 + oy), (x2 + ox, y2 + oy)],
            outline=color,
            width=3,
        )


def _render_preview_frame(
    monitor_index: int,
    max_width: Optional[int],
    region_overlay: Optional[List[int]],
) -> tuple[str, int, int]:
    """同步执行抓屏与编码全流程（仅供 ``asyncio.to_thread`` 调用）。

    mss 抓屏 + PIL 解码/画框/编码是同步阻塞调用（实测 80–220ms/次），整体
    移入 worker 线程避免冻结事件循环；``MssScreenCapture`` 每次调用自建
    mss 实例，实例的创建与使用都留在本线程内。失败路径以 ``HTTPException``
    表达（503 语义），经 to_thread 传回后由端点原样抛出。

    Returns:
        ``(image_b64, width, height)``：叠加区域框后的 PNG base64 与尺寸。
    """
    cap = MssScreenCapture()

    monitors = cap.list_monitors()
    if not monitors:
        raise HTTPException(
            status_code=503,
            detail="显示器不可用（mss 枚举为空或后端未就绪）",
        )

    # 始终抓全显示器图（不在 capture 阶段按 region 裁剪）；overlay 在内存里画
    try:
        result = cap.capture(monitor_index=monitor_index, region=None, max_width=max_width)
    except Exception as exc:  # noqa: BLE001 - 抓取边界：异常映射 503
        logger.warning(f"preview capture 异常（monitor_index={monitor_index}）: {type(exc).__name__}: {exc}")
        raise HTTPException(
            status_code=503,
            detail=f"抓取显示器 index={monitor_index} 失败: {type(exc).__name__}: {exc}",
        ) from exc
    if result.image is None:
        raise HTTPException(
            status_code=503,
            detail=f"抓取显示器 index={monitor_index} 失败（mss 后端异常或显示器不存在）",
        )

    img = Image.open(io.BytesIO(result.image))
    if region_overlay is not None:
        _draw_region_overlay(img, region_overlay)

    buf = io.BytesIO()
    try:
        img.save(buf, format="PNG")
        img_bytes = buf.getvalue()
    except Exception as exc:  # noqa: BLE001 - 编码边界兜底
        logger.warning(f"预览 PNG 编码失败: {type(exc).__name__}: {exc}")
        raise HTTPException(status_code=503, detail=f"预览图像编码失败: {exc}") from exc

    return base64.b64encode(img_bytes).decode("ascii"), img.width, img.height


@router.get(
    "/preview",
    response_model=PreviewResponse,
    summary="抓取一帧屏幕截图并叠加区域框",
)
async def get_preview(
    monitor_index: Annotated[int, Query(ge=0, description="显示器索引（默认 1=首个物理显示器）")] = 1,
    region: Annotated[
        Optional[str],
        Query(description="区域 'x1,y1,x2,y2'（相对显示器左上角的坐标）"),
    ] = None,
    max_width: Annotated[
        Optional[int],
        Query(ge=1, le=7680, description="图像缩放最大宽度（等比缩放）"),
    ] = None,
) -> PreviewResponse:
    """抓取一帧屏幕截图，并在图上叠加用户传入的 region 矩形。

    失败路径（全部映射为 HTTPException，避免 500 裸崩）：

    - ``region`` 非法 → 400 + 明确错误体（由 ``_parse_region`` 抛）。
    - mss 不可用 / 枚举空 / 抓取失败 → 503 + 明确错误体。
    """
    parsed_region = _parse_region(region)

    image_b64, width, height = await asyncio.to_thread(_render_preview_frame, monitor_index, max_width, parsed_region)

    return PreviewResponse(
        image_b64=image_b64,
        width=width,
        height=height,
        monitor_index=monitor_index,
        region=parsed_region,
    )


__all__ = ["router"]
