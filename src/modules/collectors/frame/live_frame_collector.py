"""直播画面采集器：低频采样观众此刻看到的画面，写入进程内图像暂存。

画面是持续流（不是被调才看的快照），按"持续流做采集器"的判据放在采集器层：
每隔 ``interval_ms`` 取一帧，缩成小灰度图与上一次的画面比较，差异超过
``change_threshold``、或距上次超过 ``refresh_ms`` 时刷新比对基线并记日志。
每次采样都覆盖暂存（``VisionImageStore``）的"最新帧"槽位，决策窗按需查询取帧，
不经事件流。

画面来源：

- ``obs``（默认）：经 obs-websocket 截 OBS 当前节目场景（或指定来源）——主播看到的
  就是观众看到的，不受桌面上开着什么窗口影响；
- ``screen``：OBS 不可用时截指定显示器（mss）。

取帧失败只记日志并按间隔重试，不影响其他采集器与决策。
"""

from __future__ import annotations

import asyncio
import base64
import io
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, List, Literal, Optional, Protocol

from PIL import Image
from pydantic import Field

from src.modules.config.schemas.base import BaseConfig
from src.modules.collectors.base import BaseCollector
from src.modules.logging import get_logger, silence_third_party_logger
from src.modules.studio.obs.obs_errors import describe_obs_error
from src.modules.time_utils import now_ms
from src.modules.vision.image_store import get_image_store
from src.modules.vision.mss_capture import MssScreenCapture

__all__ = [
    "FrameGrabber",
    "GrabbedFrame",
    "LiveFrameCollector",
    "ObsFrameGrabber",
    "describe_live_frame_obs_error",
    "frame_change",
]

# 变化比较用的缩略图尺寸：足够分辨场景切换、大块移动，又不被细碎动画干扰
_THUMB_SIZE = (32, 18)

# 连续失败时每隔多少次才再记一条告警，避免 OBS 没开时日志刷屏
_FAILURE_LOG_EVERY = 12


@dataclass(frozen=True, slots=True)
class GrabbedFrame:
    """一帧画面：data URL（存暂存用）+ 原始字节（算差异用）+ 尺寸。"""

    data_url: str
    image: bytes
    width: int
    height: int


class FrameGrabber(Protocol):
    """取帧后端：返回一帧画面，取不到返回 None；失败抛异常，由 ``describe`` 翻译成可操作的原因。"""

    async def grab(self) -> Optional[GrabbedFrame]: ...

    async def close(self) -> None: ...

    def describe(self, error: BaseException) -> str: ...


def _thumbnail(image: bytes) -> List[int]:
    """图像 → 小灰度图像素列表（变化比较用）。"""
    with Image.open(io.BytesIO(image)) as picture:
        return list(picture.convert("L").resize(_THUMB_SIZE).tobytes())


def frame_change(previous: Optional[List[int]], current: List[int]) -> float:
    """两张缩略图的平均灰度差（0–1）；没有上一帧时视为完全变化。"""
    if not previous or len(previous) != len(current):
        return 1.0
    return sum(abs(a - b) for a, b in zip(previous, current, strict=True)) / (255 * len(current))


def describe_live_frame_obs_error(error: BaseException, *, host: str, port: int, source: str) -> str:
    """把 OBS 取帧失败翻译成能照着改的原因（采集器告警与诊断脚本共用）。

    配置项一律指向 ``collectors.toml`` 的 ``[live_frame]``；采集器取帧时不区分连接与请求阶段
    （断了就丢掉客户端、下次重连），连接被关闭按握手阶段理解为身份验证被拒。
    """
    return describe_obs_error(
        error,
        host=host,
        port=port,
        password_setting="collectors.toml 的 [live_frame].obs_password",
        not_found=(
            f"OBS 里找不到要截的场景或来源「{source or '当前节目场景'}」："
            "检查 [live_frame].obs_source 的拼写（区分大小写），留空则截当前节目场景"
        ),
    )


class ObsFrameGrabber:
    """经 obs-websocket 截 OBS 画面（同步客户端放线程里跑，不阻塞事件循环）。

    首次取帧时连接：读 OBS 支持的图片格式（优先 JPEG）与输出分辨率（按宽度等比
    算高度）。连接中途断开就丢掉客户端，下次取帧重连。obsws-python 自带的日志会以
    INFO 级明文打印连接密码、连不上时每次都打整段堆栈，一律压住，失败原因由
    ``describe`` 翻译后经项目日志按抽样记录。
    """

    def __init__(self, *, host: str, port: int, password: str, source: str, max_width: int) -> None:
        self._host = host
        self._port = port
        self._password = password
        self._source = source
        self._max_width = max_width
        self._client: Any = None
        self._height = 0
        self._format = "jpg"
        self._logger = get_logger("LiveFrameCollector")
        silence_third_party_logger("obsws_python")

    async def grab(self) -> Optional[GrabbedFrame]:
        return await asyncio.to_thread(self._grab_sync)

    async def close(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            await asyncio.to_thread(self._disconnect, client)

    def describe(self, error: BaseException) -> str:
        return describe_live_frame_obs_error(error, host=self._host, port=self._port, source=self._source)

    def _disconnect(self, client: Any) -> None:
        try:
            client.disconnect()
        except Exception as exc:  # noqa: BLE001 - 断开阶段的失败只记录，不影响下次重连
            self._logger.debug(f"断开 OBS 画面连接时出错：{exc}")

    def _connect(self) -> Any:
        """连接 OBS，读支持的图片格式与输出分辨率；中途失败先断开再抛出。"""
        # 可选重型依赖延迟加载：没装 obsws-python 时只有 obs 来源不可用，桌面截屏照常
        import obsws_python as obs

        client = obs.ReqClient(host=self._host, port=self._port, password=self._password or None, timeout=3)
        try:
            formats = [
                str(item).lower() for item in getattr(client.get_version(), "supported_image_formats", None) or []
            ]
            self._format = next(
                (item for item in ("jpg", "jpeg", "png") if item in formats), formats[0] if formats else "jpg"
            )
            video = client.get_video_settings()
        except Exception:
            self._disconnect(client)
            raise
        width = int(getattr(video, "output_width", 0) or getattr(video, "base_width", 0) or 16)
        height = int(getattr(video, "output_height", 0) or getattr(video, "base_height", 0) or 9)
        self._height = max(8, round(self._max_width * height / width))
        return client

    def _grab_sync(self) -> Optional[GrabbedFrame]:
        if self._client is None:
            self._client = self._connect()
        try:
            source = self._source or self._client.get_current_program_scene().current_program_scene_name
            response = self._client.get_source_screenshot(source, self._format, self._max_width, self._height, 70)
        except Exception:
            # 连接断了（OBS 重启等）或来源名不对：丢掉客户端，下次取帧重连
            client, self._client = self._client, None
            self._disconnect(client)
            raise
        data_url = str(getattr(response, "image_data", "") or "")
        head, _, encoded = data_url.partition(",")
        if not encoded:
            return None
        image = base64.b64decode(encoded)
        # OBS 给 JPEG 标的是 image/jpg，不是标准 MIME，部分模型端点会拒收
        head = head.replace("image/jpg;", "image/jpeg;")
        with Image.open(io.BytesIO(image)) as picture:
            width, height = picture.size
        return GrabbedFrame(f"{head},{encoded}", image, width, height)


class ScreenFrameGrabber:
    """截指定显示器（mss），OBS 不可用时的回退来源。"""

    def __init__(self, *, monitor_index: int, max_width: int) -> None:
        self._capture = MssScreenCapture()
        self._monitor_index = monitor_index
        self._max_width = max_width

    async def grab(self) -> Optional[GrabbedFrame]:
        result = await asyncio.to_thread(self._capture.capture, self._monitor_index, None, self._max_width)
        if not result.image:
            return None
        mime = result.mime_type or "image/png"
        data_url = f"data:{mime};base64,{base64.b64encode(result.image).decode('ascii')}"
        return GrabbedFrame(data_url, result.image, result.width, result.height)

    async def close(self) -> None:
        return None

    def describe(self, error: BaseException) -> str:
        return f"截取显示器 {self._monitor_index} 失败：{type(error).__name__}: {error}"


class LiveFrameCollector(BaseCollector):
    """直播画面采集器（通用采集器，collectors.toml 开关）。"""

    name = "live_frame"
    description = "低频采样直播画面（OBS 节目画面或桌面截屏），画面明显变化时发布画面快照"

    class ConfigSchema(BaseConfig):
        """直播画面采集器配置"""

        source: Literal["obs", "screen"] = Field(
            default="obs", description="画面来源：obs=OBS 节目画面（观众看到的）/ screen=桌面截屏"
        )
        obs_host: str = Field(default="localhost", description="OBS WebSocket 主机（与 OBS 的 WebSocket 设置一致）")
        obs_port: int = Field(default=4455, ge=1, le=65535, description="OBS WebSocket 端口")
        obs_password: str = Field(default="", description="OBS WebSocket 密码（空串=无密码）")
        obs_source: str = Field(default="", description="要截的 OBS 场景或来源名；空串=当前节目场景")
        monitor_index: int = Field(default=1, ge=0, description="screen 来源截哪个显示器（1 起；0=全部显示器合屏）")
        interval_ms: int = Field(default=5000, ge=1000, description="采样间隔（毫秒）")
        max_width: int = Field(default=768, ge=160, le=1920, description="画面缩放到的宽度（像素），决定每张图的成本")
        change_threshold: float = Field(
            default=0.06, ge=0.0, le=1.0, description="与上次发布的画面平均灰度差超过它才发布（0–1）"
        )
        refresh_ms: int = Field(
            default=60_000, ge=0, description="画面一直没明显变化时，隔多久仍发布一次（毫秒；0=只在变化时发布）"
        )

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        event_bus: Any = None,
        grabber: Optional[FrameGrabber] = None,
    ) -> None:
        super().__init__(event_bus=event_bus)
        self.typed_config = self.ConfigSchema.from_dict(config or {})
        self.logger = get_logger("LiveFrameCollector")
        self._grabber: FrameGrabber = grabber or self._build_grabber()
        self._last_thumb: Optional[List[int]] = None
        self._last_published_ms = 0
        self._failures = 0

    def _build_grabber(self) -> FrameGrabber:
        cfg = self.typed_config
        if cfg.source == "screen":
            return ScreenFrameGrabber(monitor_index=cfg.monitor_index, max_width=cfg.max_width)
        return ObsFrameGrabber(
            host=cfg.obs_host,
            port=cfg.obs_port,
            password=cfg.obs_password,
            source=cfg.obs_source,
            max_width=cfg.max_width,
        )

    async def _on_start(self) -> None:
        await self._start_collect_task()

    async def _on_stop(self) -> None:
        await self._stop_collect_task()
        await self._grabber.close()

    async def collect(self) -> AsyncIterator[None]:
        """按间隔采样画面（仅驱动采样循环，无事件产出）。"""
        while True:
            await self.sample_once()
            await asyncio.sleep(self.typed_config.interval_ms / 1000)
            yield

    async def sample_once(self) -> bool:
        """取一帧并覆盖暂存最新帧槽位；画面明显变化或隔太久时刷新比对基线并记日志。

        Returns:
            本次采样是否触达变化闸（明显变化或补刷周期到）。
        """
        frame = await self._grab()
        if frame is None:
            return False
        try:
            thumb = await asyncio.to_thread(_thumbnail, frame.image)
        except Exception as exc:  # noqa: BLE001 - 坏帧跳过，下一轮重取
            self.logger.warning(f"画面解码失败，跳过这一帧：{exc}")
            return False
        change = frame_change(self._last_thumb, thumb)
        now = now_ms()
        refresh = self.typed_config.refresh_ms
        overdue = refresh > 0 and now - self._last_published_ms >= refresh
        publish = change >= self.typed_config.change_threshold or overdue
        # 每次成功采样都覆盖暂存里的"最新帧"槽位：决策窗挂帧是查询式的，
        # 看的必须是刚截的画面。基线只在触达变化闸时重置，让"明显变化"
        # 的判定始终对着上一张稳定画面
        store = get_image_store()
        store.put_latest(
            frame.data_url,
            width=frame.width,
            height=frame.height,
            change=round(change, 4),
            timestamp_ms=now,
        )
        if not publish:
            return False
        self._last_thumb = thumb
        self._last_published_ms = now
        self.logger.debug(f"画面{'明显变化' if not overdue else '距上次采样已久'}，刷新比对基线（差异 {change:.2f}）")
        return True

    async def _grab(self) -> Optional[GrabbedFrame]:
        """取帧；失败只记日志（连续失败按间隔抽样记录），不中断采样循环。"""
        try:
            frame = await self._grabber.grab()
        except Exception as exc:  # noqa: BLE001 - 来源不可用（OBS 未开等）按间隔重试
            if self._failures % _FAILURE_LOG_EVERY == 0:
                self.logger.warning(
                    f"直播画面取帧失败（来源 {self.typed_config.source}，已连续 {self._failures + 1} 次）："
                    f"{self._grabber.describe(exc)}"
                )
            self._failures += 1
            return None
        if self._failures:
            self.logger.info(f"直播画面取帧恢复（此前连续失败 {self._failures} 次）")
            self._failures = 0
        return frame
