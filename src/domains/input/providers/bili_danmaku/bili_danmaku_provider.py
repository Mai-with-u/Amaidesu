"""
Bilibili 弹幕 InputProvider

从 Bilibili 直播间采集弹幕数据。
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any, AsyncIterator, Dict, Literal, Optional

if TYPE_CHECKING:
    from src.modules.di.context import ProviderContext

try:
    import aiohttp
except ImportError:
    aiohttp = None

from pydantic import Field

from src.modules.config.schemas.base import BaseProviderConfig
from src.modules.logging import get_logger
from src.modules.types.base.input_provider import InputProvider
from src.modules.types.base.normalized_message import NormalizedMessage


class BiliDanmakuInputProvider(InputProvider):
    """
    Bilibili 直播弹幕 InputProvider

    通过轮询 Bilibili API 获取直播间的弹幕信息。
    """

    @classmethod
    def get_registration_info(cls) -> Dict[str, Any]:
        """获取 Provider 注册信息"""
        return {"layer": "input", "name": "bili_danmaku", "class": cls, "source": "builtin:bili_danmaku"}

    class ConfigSchema(BaseProviderConfig):
        """Bilibili弹幕输入Provider配置"""

        type: Literal["bili_danmaku"] = "bili_danmaku"
        room_id: int = Field(..., description="直播间ID", gt=0)
        poll_interval: int = Field(default=3, description="轮询间隔（秒）", ge=1)
        message_config: dict = Field(default_factory=dict, description="消息配置")

    def __init__(self, config: dict, context: "ProviderContext"):
        super().__init__(config, context)

        self.logger = get_logger(self.__class__.__name__)

        # 依赖检查
        if aiohttp is None:
            self.logger.error("aiohttp library not found. Please install it (`pip install aiohttp`).")
            raise ImportError("aiohttp is required for BiliDanmakuInputProvider")

        # 配置
        self.typed_config = self.ConfigSchema(**config)
        self.room_id = self.typed_config.room_id
        self.poll_interval = self.typed_config.poll_interval
        self.message_config = self.typed_config.message_config
        self.api_url = f"https://api.live.bilibili.com/xlive/web-room/v1/dM/gethistory?roomid={self.room_id}"

        # 状态变量
        self._latest_timestamp: float = time.time()
        self._session: Optional[aiohttp.ClientSession] = None

    async def generate(self) -> AsyncIterator[NormalizedMessage]:
        """
        采集弹幕数据

        Yields:
            NormalizedMessage: 弹幕标准化消息
        """
        self.is_started = True

        try:
            self.logger.info("开始采集 Bilibili 弹幕数据...")

            # 创建 aiohttp session
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
                "Referer": f"https://live.bilibili.com/{self.room_id}",
                "Accept": "application/json",
            }

            async with aiohttp.ClientSession(headers=headers) as session:
                self._session = session

                while self.is_started:
                    try:
                        await self._fetch_and_process()
                    except Exception as e:
                        self.logger.error(f"采集弹幕时出错: {e}", exc_info=True)

                    # 等待下次轮询
                    try:
                        await asyncio.wait_for(asyncio.sleep(self.poll_interval), timeout=None)
                    except asyncio.CancelledError:
                        break

        except asyncio.CancelledError:
            self.logger.info("采集被取消")
        except Exception as e:
            self.logger.error(f"数据采集出错: {e}", exc_info=True)
        finally:
            self.is_started = False
            self.logger.info("Bilibili 弹幕采集已停止")

    async def _fetch_and_process(self):
        """
        获取并处理弹幕

        从 Bilibili API 获取弹幕，过滤新弹幕并转换为 NormalizedMessage。
        """
        if not self._session or self._session.closed:
            self.logger.warning("aiohttp session 未初始化或已关闭，跳过本次轮询。")
            return

        new_max_timestamp = self._latest_timestamp

        try:
            self.logger.debug(f"轮询 Bilibili API: {self.api_url}")
            async with self._session.get(self.api_url, timeout=10) as response:
                if response.status != 200:
                    self.logger.warning(f"Bilibili API 请求失败，状态码: {response.status}")
                    await asyncio.sleep(self.poll_interval * 2)
                    return

                data = await response.json()
                self.logger.debug(f"收到 API 响应: code={data.get('code')}")

                if data.get("code") == 0:
                    room_data = data.get("data", {}).get("room", [])
                    if not room_data:
                        self.logger.debug("API 返回的弹幕列表为空")
                        return

                    new_danmakus = []
                    for item in room_data:
                        timestamp = item.get("check_info", {}).get("ts")
                        item.get("uid")

                        if timestamp and timestamp > self._latest_timestamp:
                            new_danmakus.append(item)
                            new_max_timestamp = max(new_max_timestamp, timestamp)

                    if new_danmakus:
                        new_danmakus.sort(key=lambda x: x.get("check_info", {}).get("ts", 0))
                        self.logger.debug(f"收到 {len(new_danmakus)} 条新弹幕")

                        for item in new_danmakus:
                            normalized_msg = await self._create_danmaku_message(item)
                            if normalized_msg:
                                yield normalized_msg
                    else:
                        self.logger.debug("没有新的弹幕")

                    self._latest_timestamp = new_max_timestamp
                else:
                    self.logger.warning(
                        f"Bilibili API 返回错误: code={data.get('code')}, message={data.get('message')}"
                    )

        except aiohttp.ClientError as e:
            self.logger.warning(f"轮询 Bilibili API 时发生网络错误: {e}")
        except asyncio.TimeoutError:
            self.logger.warning("轮询 Bilibili API 超时")
        except Exception as e:
            self.logger.exception(f"处理 Bilibili 弹幕时发生未知错误: {e}")

    async def _create_danmaku_message(self, item: Dict[str, Any]) -> Optional[NormalizedMessage]:
        """
        创建弹幕 NormalizedMessage

        Args:
            item: Bilibili API 返回的弹幕项

        Returns:
            NormalizedMessage: 弹幕标准化消息
        """
        text = item.get("text", "")
        nickname = item.get("nickname", "未知用户")

        # 默认 user_id
        user_id = item.get("uid") or self.message_config.get("default_user_id", f"bili_{nickname}")

        if not text:
            return None

        # 直接创建 NormalizedMessage
        return NormalizedMessage(
            text=text,
            source="bili_danmaku",
            data_type="text",
            importance=0.5,
            user_id=str(user_id),
            user_nickname=nickname,
            platform=self.message_config.get("platform", "bilibili"),
            room_id=str(self.room_id),
        )

    async def cleanup(self):
        """清理资源"""
        if self._session and not self._session.closed:
            await self._session.close()
            self.logger.debug("关闭了 aiohttp Session")

        self.logger.info("BiliDanmakuInputProvider 已清理")
