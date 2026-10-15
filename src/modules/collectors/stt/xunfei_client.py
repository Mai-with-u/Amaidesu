"""
讯飞听写（IAT）流式识别 WebSocket 客户端

从 STTCollector 内嵌的连接/收发逻辑拆出，专职讯飞侧协议：
认证 URL 签名、帧构造、连接维护（建连/收尾）、后台接收循环。

- ``ensure_connection`` / ``close``：连接生命周期（collector 在话语起止时驱动）
- ``send_frame``：按讯飞状态码发送音频帧（首帧/中间帧/末帧由调用方定序）
- ``_receive_loop``：后台任务，解析识别结果，整句文本装入 ``result_queue``
  （payload 形态与 ``room.message.partner_speech`` 事件契约一致）

识别结果经 ``result_queue`` 交还采集器——客户端不触碰事件总线，保持
采集器作为唯一的事件发布点。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import ssl
from datetime import datetime
from typing import Any, Callable, Dict, Optional

from src.modules.events.payloads.room import RoomMessagePayload, RoomMessageUser
from src.modules.logging import get_logger
from src.modules.time_utils import now_ms

# 讯飞听写协议帧状态码：首帧 / 中间帧 / 末帧
STATUS_FIRST_FRAME = 0
STATUS_CONTINUE_FRAME = 1
STATUS_LAST_FRAME = 2


class XunfeiIatClient:
    """讯飞 WebSocket 流式识别客户端（单连接；一次话语一条连接）。

    Attributes:
        result_queue: 识别完成的整句 payload 队列（采集器排空后发布事件）
    """

    def __init__(
        self,
        *,
        config: Dict[str, Any],
        sample_rate: int,
        vad_eos_ms: int,
        message_config: Dict[str, Any],
        should_run: Optional[Callable[[], bool]] = None,
    ) -> None:
        self._logger = get_logger(self.__class__.__name__)
        self._config = config
        self._sample_rate = sample_rate
        self._vad_eos_ms = vad_eos_ms
        self._message_config = message_config
        # 接收循环的继续条件（采集器传入 ``lambda: self.is_started``），
        # 停机时接收器随采集器状态退出，与拆分前行为一致
        self._should_run = should_run

        # aiohttp 为可选重型依赖：延迟导入，缺失在采集器依赖检查期已拦截
        try:
            import aiohttp

            self.aiohttp = aiohttp
        except ImportError:
            self._logger.error("缺少依赖: aiohttp。请运行 'pip install aiohttp'")
            self.aiohttp = None

        self.result_queue: asyncio.Queue = asyncio.Queue()
        self._session = None
        self._active_ws = None
        self._active_receiver_task = None
        self.full_text = ""
        self._last_audio_chunk: Optional[bytes] = None

    @property
    def has_ws(self) -> bool:
        """是否持有 WebSocket 连接（含已关闭未清理的残留）。"""
        return self._active_ws is not None

    @property
    def is_open(self) -> bool:
        """WebSocket 连接是否存在且未关闭。"""
        return self._active_ws is not None and not self._active_ws.closed

    async def ensure_connection(self) -> bool:
        """确保讯飞 WebSocket 连接存在"""
        if self._active_ws and not self._active_ws.closed:
            if self._active_receiver_task and not self._active_receiver_task.done():
                return True
            else:
                self._logger.warning("WebSocket 存在但接收器任务未运行，重新连接")
                await self.close(send_last_frame=False)

        if not self._session or self._session.closed:
            try:
                self._session = self.aiohttp.ClientSession()
                self._logger.info("已创建新的 aiohttp session")
            except Exception as e:
                self._logger.exception(f"创建 aiohttp session 失败: {e}")
                return False

        try:
            auth_url = self._build_auth_url()
            self._logger.debug("连接到讯飞 WebSocket...")

            ssl_context = ssl.create_default_context()
            self._active_ws = await self._session.ws_connect(
                auth_url,
                autoping=True,
                heartbeat=30,
                ssl=ssl_context,
            )
            self._logger.info("成功连接到讯飞 WebSocket")

            if self._active_receiver_task and not self._active_receiver_task.done():
                self._active_receiver_task.cancel()
                try:
                    await asyncio.wait_for(self._active_receiver_task, timeout=1.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass

            self._active_receiver_task = asyncio.create_task(self._receive_loop(self._active_ws))
            self._logger.info("已启动讯飞接收器任务")
            return True

        except Exception as e:
            self._logger.exception(f"建立讯飞连接失败: {e}")
            if self._active_ws and not self._active_ws.closed:
                await self._active_ws.close()
            self._active_ws = None
            if self._active_receiver_task and not self._active_receiver_task.done():
                self._active_receiver_task.cancel()
            self._active_receiver_task = None
            return False

    async def close(self, send_last_frame: bool = True, last_audio_chunk: Optional[bytes] = None) -> None:
        """关闭讯飞 WebSocket 连接"""
        ws_to_close = self._active_ws
        receiver_task_to_await = self._active_receiver_task

        self._active_ws = None
        self._active_receiver_task = None
        self._last_audio_chunk = None

        if not ws_to_close:
            if receiver_task_to_await and not receiver_task_to_await.done():
                receiver_task_to_await.cancel()
            return

        if ws_to_close.closed:
            if receiver_task_to_await and not receiver_task_to_await.done():
                receiver_task_to_await.cancel()
            return

        self._logger.debug(f"关闭讯飞连接 (发送结束帧: {send_last_frame})...")

        try:
            if send_last_frame:
                try:
                    audio_for_end = last_audio_chunk or self._last_audio_chunk
                    last_frame = self._build_frame(STATUS_LAST_FRAME, audio_for_end or b"")
                    await asyncio.wait_for(ws_to_close.send_bytes(last_frame), timeout=2.0)
                    self._logger.debug("已发送结束帧")
                except Exception as e:
                    self._logger.warning(f"发送结束帧失败: {e}")

            if receiver_task_to_await and not receiver_task_to_await.done():
                try:
                    await asyncio.wait_for(receiver_task_to_await, timeout=1.0)
                except asyncio.TimeoutError:
                    receiver_task_to_await.cancel()
                except Exception as e:
                    self._logger.exception(f"等待接收器任务出错: {e}")

            await ws_to_close.close()
            self._logger.debug(f"讯飞连接已关闭 (代码: {ws_to_close.close_code})")

        except Exception as e:
            self._logger.exception(f"关闭连接出错: {e}")
        finally:
            if receiver_task_to_await and not receiver_task_to_await.done():
                receiver_task_to_await.cancel()

    async def send_frame(self, status: int, audio_chunk_bytes: bytes, timeout: float) -> None:
        """按讯飞状态码发送一帧音频；失败抛出原异常由调用方处置。

        中间帧发送成功后记录末帧音频（``close`` 生成结束帧时的兜底素材），
        与原采集器内联发送路径的行为一致。
        """
        frame = self._build_frame(status, audio_chunk_bytes)
        ws = self._active_ws
        await asyncio.wait_for(ws.send_bytes(frame), timeout=timeout)
        if status == STATUS_CONTINUE_FRAME:
            self._last_audio_chunk = audio_chunk_bytes

    async def dispose(self) -> None:
        """清理底层资源（采集器停机路径调用）：关闭 aiohttp session 并复位连接状态。"""
        if self._session and not self._session.closed:
            await self._session.close()
            self._logger.info("aiohttp session 已关闭")
        self._session = None
        self._active_ws = None
        self._active_receiver_task = None

    async def _receive_loop(self, ws: Any) -> None:
        """接收讯飞 WebSocket 消息并处理识别结果"""
        self._logger.debug("讯飞接收器任务启动")
        self.full_text = ""
        utterance_failed = False

        try:
            async for msg in ws:
                if self._should_run is not None and not self._should_run():
                    break

                if msg.type == self.aiohttp.WSMsgType.TEXT:
                    try:
                        resp = json.loads(msg.data)
                        self._logger.debug(f"讯飞原始响应: {json.dumps(resp, ensure_ascii=False)[:500]}")

                        if resp.get("code", -1) != 0:
                            msg_content = resp.get("message", "")
                            err_msg = f"讯飞 API 错误: Code={resp.get('code')}, Message={msg_content}"
                            self._logger.error(err_msg)
                            utterance_failed = True
                            break

                        data = resp.get("data", {})
                        result_data = data.get("result", {})
                        status = data.get("status", -1)

                        if "ws" in result_data:
                            try:
                                for w in result_data["ws"]:
                                    for cw in w.get("cw", []):
                                        self.full_text += cw.get("w", "")
                            except Exception as e:
                                self._logger.warning(f"解析识别结果文本失败: {e}")

                        self._logger.debug(
                            f"检查最后一帧: status={status}, STATUS_LAST_FRAME={STATUS_LAST_FRAME}, full_text='{self.full_text}'"
                        )
                        if status == STATUS_LAST_FRAME:
                            full_text = self.full_text.strip()
                            self._logger.debug(f"讯飞识别结果: '{full_text}'")

                            if full_text and not utterance_failed:
                                await self.result_queue.put(
                                    RoomMessagePayload(
                                        message_type="partner_speech",
                                        user=RoomMessageUser(
                                            id=str(self._message_config.get("user_id") or "partner"),
                                            name=str(self._message_config.get("user_nickname") or "联动对象"),
                                        ),
                                        content=full_text,
                                        timestamp_ms=now_ms(),
                                    )
                                )
                                self.full_text = ""
                                break

                    except json.JSONDecodeError:
                        self._logger.error(f"无法解码讯飞 JSON: {msg.data}")
                        utterance_failed = True
                        break
                    except Exception as e:
                        self._logger.exception(f"处理讯飞消息出错: {e}")
                        utterance_failed = True
                        break

                elif msg.type == self.aiohttp.WSMsgType.ERROR:
                    err = ws.exception() or RuntimeError("WebSocket 错误")
                    self._logger.error(f"讯飞 WebSocket 错误: {err}")
                    utterance_failed = True
                    break

                elif msg.type == self.aiohttp.WSMsgType.CLOSED:
                    self._logger.warning(f"讯飞连接已关闭: Code={ws.close_code}")
                    break

        except asyncio.CancelledError:
            self._logger.info("讯飞接收器任务被取消")
        except Exception as e:
            self._logger.exception(f"讯飞接收器任务异常: {e}")
        finally:
            self._logger.debug("讯飞接收器任务结束")

    def _build_auth_url(self) -> str:
        """构建讯飞认证 URL"""
        cfg = self._config

        host = cfg.get("host", "iat-api.xfyun.com")
        path = cfg.get("path", "/v2/iat")

        if not all([host, path, cfg.get("api_secret"), cfg.get("api_key")]):
            raise ValueError("讯飞配置缺少必要字段")

        url = f"wss://{host}{path}"

        date = datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT")
        signature_origin = f"host: {host}\ndate: {date}\nGET {path} HTTP/1.1"

        self._logger.debug(f"signature_origin: {repr(signature_origin)}")

        signature_sha = hmac.new(
            cfg["api_secret"].encode("utf-8"), signature_origin.encode("utf-8"), digestmod=hashlib.sha256
        ).digest()
        signature_sha_base64 = base64.b64encode(signature_sha).decode(encoding="utf-8")

        self._logger.debug(f"signature_sha_base64: {signature_sha_base64}")

        authorization_origin = f'api_key="{cfg["api_key"]}", algorithm="hmac-sha256", headers="host date request-line", signature="{signature_sha_base64}"'

        self._logger.debug(f"authorization_origin: {authorization_origin[:50]}...")

        authorization = base64.b64encode(authorization_origin.encode("utf-8")).decode(encoding="utf-8")

        self._logger.debug(f"讯飞认证 - host: {host}, path: {path}, date: {date}")

        params = f"authorization={authorization}&date={date}&host={host}"
        signed_url = url + "?" + params

        self._logger.debug(f"讯飞认证 URL: {signed_url[:150]}...")
        return signed_url

    def _build_frame(self, status: int, audio_chunk_bytes: bytes = b"") -> bytes:
        """构建讯飞帧（语音听写流式版 API 格式）"""
        is_first = status == STATUS_FIRST_FRAME

        business: Dict[str, Any] = {}
        if is_first:
            business = {
                "language": self._config.get("language", "zh_cn"),
                "domain": self._config.get("domain", "iat"),
                "accent": self._config.get("accent", "mandarin"),
                "vad_eos": self._vad_eos_ms,
            }

        format_str = f"audio/L16;rate={self._sample_rate}"
        data = {
            "status": status,
            "format": format_str,
            "encoding": "raw",
            "audio": base64.b64encode(audio_chunk_bytes).decode("utf-8") if audio_chunk_bytes else "",
        }

        if is_first:
            frame = {
                "common": {"app_id": str(self._config["appid"])},
                "business": business,
                "data": data,
            }
            self._logger.debug(
                f"发送第一帧: common={frame['common']}, business={frame['business']}, data.status={data['status']}, format={format_str}"
            )
        else:
            frame = {"data": data}

        return json.dumps(frame).encode("utf-8")
