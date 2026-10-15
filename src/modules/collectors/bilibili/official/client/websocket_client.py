# Bilibili官方WebSocket客户端

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import random
import time
from hashlib import sha256
from typing import Any, Awaitable, Callable, Optional

import requests
import urllib3
import websockets

from src.modules.logging import get_logger

from .game_checkpoint import GameIdCheckpoint
from .proto import Proto

# 禁用HTTPS证书警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# B站官方 WebSocket 心跳间隔（秒）：平台要求每 30s 心跳保活；
# 服务器约 10s 无活动即断连，故设 5s 留足余量
_WS_HEARTBEAT_INTERVAL_S = 5
_APP_HEARTBEAT_INTERVAL_S = 20

# 重连配置
_RECONNECT_BASE_DELAY_S = 2.0  # 首次重连等待秒数
_RECONNECT_MAX_DELAY_S = 60.0  # 最大重连等待秒数（指数退避上限）
_RECONNECT_BACKOFF_FACTOR = 2.0  # 退避倍率
_RECONNECT_MAX_ATTEMPTS = 0  # 最大重连次数，0 = 无限


class BiliWebSocketClient:
    """Bilibili官方WebSocket客户端"""

    def __init__(
        self,
        id_code: str,
        app_id: int,
        access_key: str,
        access_key_secret: str,
        api_host: str,
        game_id_checkpoint: Optional[GameIdCheckpoint] = None,
        start_error_callback: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> None:
        self.id_code = id_code
        self.app_id = app_id
        self.access_key = access_key
        self.access_key_secret = access_key_secret
        self.api_host = api_host
        # 检查点：game_id 落盘，进程异常退出后下次启动仍能 end 掉服务端残留会话
        self.game_id_checkpoint = game_id_checkpoint or GameIdCheckpoint()
        # app/start 失败的外显回调（如采集器经事件总线向 WebUI 发告警），可为空
        self.start_error_callback = start_error_callback
        self.logger = get_logger("BiliWebSocketClient")

        self.game_id = ""
        self.websocket = None
        self.is_started = False
        self.heartbeat_task = None
        self.app_heartbeat_task = None
        self.recv_task = None

    async def run(self, message_handler: Callable[..., Any], queue: Optional[asyncio.Queue] = None) -> None:
        """运行WebSocket客户端（带自动重连）

        Args:
            message_handler: 消息处理回调函数
            queue: 可选的消息队列，如果提供则传递给handler
        """
        if self.is_started:
            return

        self.is_started = True

        # B 站开放平台一个 app_id 同时只允许一场进行中的游戏：
        # 上次进程异常退出未 end 的残留会话会在 app/start 撞"房间重复游戏"，
        # 启动前先按检查点幂等清理一次（end 失败一律忽略，不阻塞正常 start）
        await self._clear_stale_game()

        attempt = 0

        while self.is_started:
            attempt += 1
            try:
                # 建立连接
                websocket = await self._connect()
                if not websocket:
                    self.logger.warning(f"连接失败（第 {attempt} 次），准备重连...")
                    await self._reconnect_delay(attempt)
                    continue

                self.websocket = websocket
                self.logger.info(f"WebSocket 已连接（第 {attempt} 次尝试）")
                attempt = 0  # 连接成功后重置计数

                # 启动后台任务
                tasks = [
                    asyncio.create_task(self._recv_loop(message_handler, queue), name="WebSocket接收循环"),
                    asyncio.create_task(self._heartbeat_loop(), name="WebSocket心跳"),
                    asyncio.create_task(self._app_heartbeat_loop(), name="应用心跳"),
                ]

                # 等待任务完成或异常
                try:
                    await asyncio.gather(*tasks)
                except Exception as e:
                    self.logger.warning(f"WebSocket任务组异常: {e}")
                finally:
                    # 取消所有任务
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                            with contextlib.suppress(asyncio.CancelledError):
                                await task

            except Exception as e:
                self.logger.warning(f"WebSocket客户端运行异常: {e}")

            await self._end_app()
            await self._cleanup()

            if not self.is_started:
                break

            self.logger.info("WebSocket 连接断开，准备重连...")
            await self._reconnect_delay(attempt if attempt > 0 else 1)

    async def close(self) -> None:
        """关闭WebSocket连接"""
        self.logger.debug("正在关闭WebSocket连接...")
        self.is_started = False

        # 取消后台任务
        tasks_to_cancel = [self.heartbeat_task, self.app_heartbeat_task, self.recv_task]
        for task in tasks_to_cancel:
            if task and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError):
                    await asyncio.wait_for(task, timeout=5)
        # 关闭应用
        await self._end_app()

        # 关闭WebSocket连接
        if self.websocket:
            try:
                await self.websocket.close()
            except Exception as e:
                self.logger.warning(f"关闭WebSocket时出错: {e}")

        await self._cleanup()

    def _sign(self, params: str) -> dict:
        """HTTP请求签名"""
        md5 = hashlib.md5()
        md5.update(params.encode())
        ts = time.time()
        nonce = random.randint(1, 100000) + time.time()
        md5data = md5.hexdigest()

        header_map = {
            "x-bili-timestamp": str(int(ts)),
            "x-bili-signature-method": "HMAC-SHA256",
            "x-bili-signature-nonce": str(nonce),
            "x-bili-accesskeyid": self.access_key,
            "x-bili-signature-version": "1.0",
            "x-bili-content-md5": md5data,
        }

        header_list = sorted(header_map)
        header_str = ""

        for key in header_list:
            header_str = header_str + key + ":" + str(header_map[key]) + "\n"
        header_str = header_str.rstrip("\n")

        app_secret = self.access_key_secret.encode()
        data = header_str.encode()
        signature = hmac.new(app_secret, data, digestmod=sha256).hexdigest()

        header_map["Authorization"] = signature
        header_map["Content-Type"] = "application/json"
        header_map["Accept"] = "application/json"
        return header_map

    def _post_openapi(self, *, url: str, headers: dict[str, str], body: str, timeout: int) -> requests.Response:
        """同步 POST B 站开放平台接口；调用方须经 asyncio.to_thread 进入，避免阻塞事件循环。"""
        # verify=False 为既有取舍：显式跳过证书校验（历史行为，恢复需单独评估）
        return requests.post(url=url, headers=headers, data=body, verify=False, timeout=timeout)

    async def _clear_stale_game(self) -> None:
        """按检查点清理上次异常退出遗留的服务端游戏会话（幂等，可安全重复）"""
        stale_game_id = self.game_id_checkpoint.load()
        if not stale_game_id:
            self.logger.debug("无残留检查点，跳过清残留")
            return
        # 残留可能已失效：end 返回任何错误都不阻塞启动
        await self._end_app(game_id=stale_game_id)
        self.game_id_checkpoint.clear()
        self.logger.info(f"检测到残留 game_id={stale_game_id}，已清理")

    async def _get_websocket_info(self) -> tuple[Optional[str], Optional[str]]:
        """获取WebSocket连接信息"""
        try:
            # 开启应用
            post_url = f"{self.api_host}/v2/app/start"
            params = json.dumps({"code": self.id_code, "app_id": self.app_id})
            header_map = self._sign(params)

            self.logger.debug(f"请求应用启动: {post_url}")
            response = await asyncio.to_thread(
                self._post_openapi, url=post_url, headers=header_map, body=params, timeout=30
            )
            response.raise_for_status()

            data = response.json()
            self.logger.debug(f"应用启动响应: {data}")

            if data.get("code") != 0:
                self.logger.error(f"应用启动失败: {data}")
                await self._notify_start_error(
                    f"B 站应用启动失败（code={data.get('code')}）: {data.get('message', data)}"
                )
                return None, None

            self.game_id = str(data["data"]["game_info"]["game_id"])
            wss_link = data["data"]["websocket_info"]["wss_link"][0]
            auth_body = data["data"]["websocket_info"]["auth_body"]

            # 检查点落盘：进程异常退出后下次启动仍能 end 掉本场会话
            self.game_id_checkpoint.save(self.game_id)

            self.logger.info(f"应用启动成功，游戏ID: {self.game_id}")
            return wss_link, auth_body

        except Exception as e:
            self.logger.exception(f"获取WebSocket信息失败: {e}")
            await self._notify_start_error(f"B 站应用启动异常: {e}")
            return None, None

    async def _send_app_heartbeat(self) -> None:
        """发送应用心跳"""
        if not self.game_id:
            return

        try:
            post_url = f"{self.api_host}/v2/app/heartbeat"
            params = json.dumps({"game_id": self.game_id})
            header_map = self._sign(params)

            response = await asyncio.to_thread(
                self._post_openapi, url=post_url, headers=header_map, body=params, timeout=10
            )
            response.raise_for_status()

            data = response.json()
            if data.get("code") == 0:
                self.logger.debug("应用心跳发送成功")
            else:
                self.logger.warning(f"应用心跳失败: {data}")

        except Exception as e:
            self.logger.warning(f"发送应用心跳时出错: {e}")

    async def _end_app(self, game_id: Optional[str] = None) -> None:
        """结束应用

        Args:
            game_id: 要结束的会话 ID；缺省用当前实例的 game_id（清理
                历史残留时传入外部 game_id，不依赖实例状态）
        """
        target_game_id = game_id if game_id is not None else self.game_id
        if not target_game_id:
            return

        try:
            post_url = f"{self.api_host}/v2/app/end"
            params = json.dumps({"game_id": target_game_id, "app_id": self.app_id})
            header_map = self._sign(params)

            response = await asyncio.to_thread(
                self._post_openapi, url=post_url, headers=header_map, body=params, timeout=10
            )
            response.raise_for_status()

            data = response.json()
            if data.get("code") == 0:
                self.logger.info("应用结束成功")
                # 会话已正常终结：检查点使命完成（end 失败则保留，供下次启动再清）
                self.game_id_checkpoint.clear()
            else:
                self.logger.warning(f"应用结束失败: {data}")

        except Exception as e:
            self.logger.warning(f"结束应用时出错: {e}")

    async def _notify_start_error(self, message: str) -> None:
        """app/start 失败外显：经回调上抛（采集器转事件总线告警），回调自身异常不外溢"""
        if self.start_error_callback is None:
            return
        try:
            await self.start_error_callback(message)
        except Exception as e:
            self.logger.warning(f"启动失败回调执行出错: {e}")

    async def _auth(self, websocket: Any, auth_body: str) -> None:
        """WebSocket认证"""
        try:
            proto = Proto()
            proto.op = 7
            proto.body = auth_body
            data = proto.pack()

            await websocket.send(data)
            self.logger.debug("WebSocket认证消息已发送")

            # 等待认证响应
            response = await websocket.recv()
            proto.unpack(response)

            if proto.op == 8:
                self.logger.info("WebSocket认证成功")
                return True
            else:
                self.logger.error(f"WebSocket认证失败，op: {proto.op}")
                return False

        except Exception as e:
            self.logger.error(f"WebSocket认证时出错: {e}")
            return False

    async def _connect(self) -> Optional[object]:
        """建立WebSocket连接"""
        try:
            wss_link, auth_body = await self._get_websocket_info()
            if not wss_link or not auth_body:
                return None

            self.logger.info(f"正在连接WebSocket: {wss_link}")
            websocket = await websockets.connect(wss_link, ping_interval=None)

            # 进行认证
            if not await self._auth(websocket, auth_body):
                await websocket.close()
                return None

            self.logger.info("WebSocket连接建立成功")
            return websocket

        except Exception as e:
            self.logger.exception(f"建立WebSocket连接时出错: {e}")
            return None

    async def _heartbeat_loop(self) -> None:
        """WebSocket心跳循环"""
        while self.is_started and self.websocket:
            try:
                proto = Proto()
                proto.op = 2
                proto.body = ""
                data = proto.pack()

                await self.websocket.send(data)
                self.logger.debug("WebSocket心跳已发送")

                await asyncio.sleep(_WS_HEARTBEAT_INTERVAL_S)

            except Exception as e:
                self.logger.warning(f"发送WebSocket心跳时出错: {e}")
                break

    async def _app_heartbeat_loop(self) -> None:
        """应用心跳循环"""
        while self.is_started:
            try:
                await self._send_app_heartbeat()
                await asyncio.sleep(_APP_HEARTBEAT_INTERVAL_S)

            except Exception as e:
                self.logger.warning(f"应用心跳循环出错: {e}")
                break

    async def _recv_loop(self, message_handler: Callable, queue: asyncio.Queue = None) -> None:
        """接收消息循环"""
        while self.is_started and self.websocket:
            try:
                data = await self.websocket.recv()
                proto = Proto()
                proto.unpack(data)

                if proto.op == 5:  # 消息通知
                    try:
                        message_data = json.loads(proto.body)
                        # 调用handler，传入queue（如果提供）
                        if queue is not None:
                            await message_handler(message_data, queue)
                        else:
                            await message_handler(message_data)
                    except json.JSONDecodeError as e:
                        self.logger.warning(f"解析消息JSON失败: {e}, 原始数据: {proto.body}")
                    except Exception as e:
                        self.logger.exception(f"处理消息时出错: {e}")
                elif proto.op == 3:  # 心跳回复
                    self.logger.debug("收到WebSocket心跳回复")
                else:
                    self.logger.debug(f"收到其他类型消息，op: {proto.op}")

            except websockets.exceptions.ConnectionClosed:
                self.logger.warning("WebSocket连接已关闭")
                break
            except Exception as e:
                self.logger.exception(f"接收消息时出错: {e}")
                break

    async def _cleanup(self) -> None:
        """清理资源"""
        if self.websocket:
            try:
                await self.websocket.close()
            except Exception as e:
                self.logger.warning(f"清理WebSocket时出错: {e}")
            finally:
                self.websocket = None

    async def _reconnect_delay(self, attempt: int) -> None:
        """重连等待（指数退避），连接断开后根据尝试次数递增等待时间。

        Args:
            attempt: 当前重连尝试次数（1-based）
        """
        if _RECONNECT_MAX_ATTEMPTS > 0 and attempt > _RECONNECT_MAX_ATTEMPTS:
            self.logger.error(f"已达最大重连次数 ({_RECONNECT_MAX_ATTEMPTS})，停止重连")
            self.is_started = False
            return

        delay = min(_RECONNECT_BASE_DELAY_S * (_RECONNECT_BACKOFF_FACTOR ** (attempt - 1)), _RECONNECT_MAX_DELAY_S)
        self.logger.info(f"等待 {delay:.1f}s 后重连（第 {attempt} 次）")
        await asyncio.sleep(delay)
