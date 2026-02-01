"""
Bilibili 弹幕 InputProvider

从 Bilibili 直播间采集弹幕数据。
"""

import asyncio
import time
from typing import AsyncIterator, Dict, Any, Optional

try:
    import aiohttp
except ImportError:
    aiohttp = None

from src.core.providers.input_provider import InputProvider
from src.core.data_types.raw_data import RawData
from src.utils.logger import get_logger


class BiliDanmakuInputProvider(InputProvider):
    """
    Bilibili 直播弹幕 InputProvider

    通过轮询 Bilibili API 获取直播间的弹幕信息。
    """

    def __init__(self, config: dict):
        super().__init__(config)

        self.logger = get_logger(self.__class__.__name__)

        # 依赖检查
        if aiohttp is None:
            self.logger.error("aiohttp library not found. Please install it (`pip install aiohttp`).")
            raise ImportError("aiohttp is required for BiliDanmakuInputProvider")

        # 配置
        self.room_id = self.config.get("room_id")
        if not self.room_id or not isinstance(self.room_id, int) or self.room_id <= 0:
            raise ValueError(f"Invalid or missing 'room_id' in config: {self.room_id}")

        self.poll_interval = max(1, self.config.get("poll_interval", 3))
        self.api_url = f"https://api.live.bilibili.com/xlive/web-room/v1/dM/gethistory?roomid={self.room_id}"

        # 消息配置
        self.message_config = self.config.get("message_config", {})

        # 状态变量
        self._latest_timestamp: float = time.time()
        self._session: Optional[aiohttp.ClientSession] = None
        self._stop_event = asyncio.Event()

    async def _collect_data(self) -> AsyncIterator[RawData]:
        """
        采集弹幕数据

        Yields:
            RawData: 弹幕原始数据
        """
        self.logger.info("开始采集 Bilibili 弹幕数据...")

        # 创建 aiohttp session
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Referer": f"https://live.bilibili.com/{self.room_id}",
            "Accept": "application/json",
        }

        async with aiohttp.ClientSession(headers=headers) as session:
            self._session = session

            while not self._stop_event.is_set():
                try:
                    await self._fetch_and_process()
                except Exception as e:
                    self.logger.error(f"采集弹幕时出错: {e}", exc_info=True)

                # 等待下次轮询
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=self.poll_interval)
                    break
                except asyncio.TimeoutError:
                    pass  # 正常超时，继续循环

        self.logger.info("Bilibili 弹幕采集已停止")

    async def _fetch_and_process(self):
        """
        获取并处理弹幕

        从 Bilibili API 获取弹幕，过滤新弹幕并转换为 RawData。
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
                        self.logger.info(f"收到 {len(new_danmakus)} 条新弹幕")

                        for item in new_danmakus:
                            raw_data = await self._create_danmaku_raw_data(item)
                            if raw_data:
                                yield raw_data
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

    async def _create_danmaku_raw_data(self, item: Dict[str, Any]) -> Optional[RawData]:
        """
        创建弹幕 RawData

        Args:
            item: Bilibili API 返回的弹幕项

        Returns:
            RawData: 弹幕原始数据
        """
        text = item.get("text", "")
        nickname = item.get("nickname", "未知用户")
        timestamp = item.get("check_info", {}).get("ts", time.time())

        # 默认 user_id
        user_id = item.get("uid") or self.message_config.get("default_user_id", f"bili_{nickname}")

        if not text:
            return None

        # 创建 RawData
        raw_data = RawData(
            content={
                "text": text,
                "nickname": nickname,
                "user_id": str(user_id),
                "uid": item.get("uid"),
                "timestamp": timestamp,
                # 包含完整消息配置信息
                "message_config": {
                    "user_info": {
                        "platform": self.message_config.get("platform", "bilibili"),
                        "user_id": str(user_id),
                        "user_nickname": nickname,
                        "user_cardname": self.message_config.get("user_cardname", ""),
                    },
                    "group_info": self.message_config.get("enable_group_info", False)
                    and {
                        "platform": self.message_config.get("platform", "bilibili"),
                        "group_id": self.message_config.get("group_id", self.room_id),
                        "group_name": self.message_config.get("group_name", f"bili_{self.room_id}"),
                    },
                    "format_info": {
                        "content_format": self.message_config.get("content_format", ["text"]),
                        "accept_format": self.message_config.get("accept_format", ["text"]),
                    },
                    "additional_config": {
                        "source": "bili_danmaku_plugin",
                        "sender_name": nickname,
                        "bili_uid": str(user_id) if item.get("uid") else None,
                        "maimcore_reply_probability_gain": 1,
                    },
                },
            },
            source="bili_danmaku",
            data_type="text",
            timestamp=timestamp,
            metadata={
                "nickname": nickname,
                "user_id": str(user_id),
                "uid": item.get("uid"),
                "room_id": self.room_id,
            },
        )

        return raw_data

    async def _cleanup(self):
        """清理资源"""
        if self._session and not self._session.closed:
            await self._session.close()
            self.logger.debug("关闭了 aiohttp Session")

        self.logger.info("BiliDanmakuInputProvider 已清理")
