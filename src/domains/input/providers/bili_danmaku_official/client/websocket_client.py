# src/domains/input/providers/bili_danmaku_official/client/websocket_client.py

import asyncio
import contextlib
import json
import time
import hashlib
import hmac
import random
import urllib3
from hashlib import sha256
from typing import Dict, Optional, Callable
import websockets
import requests
from src.core.utils.logger import get_logger

from .proto import Proto

# 禁用HTTPS证书警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class BiliWebSocketClient:
    """Bilibili官方WebSocket客户端"""

    def __init__(self, id_code: str, app_id: int, access_key: str, access_key_secret: str, api_host: str):
        self.id_code = id_code
        self.app_id = app_id
        self.access_key = access_key
        self.access_key_secret = access_key_secret
        self.api_host = api_host
        self.logger = get_logger("BiliWebSocketClient")

        self.game_id = ""
        self.websocket = None
        self.is_running = False
        self.heartbeat_task = None
        self.app_heartbeat_task = None
        self.recv_task = None

    async def run(self, message_handler: Callable, queue: asyncio.Queue = None):
        """运行WebSocket客户端

        Args:
            message_handler: 消息处理回调函数
            queue: 可选的消息队列，如果提供则传递给handler
        """
        if self.is_running:
            return

        self.is_running = True
        try:
            # 建立连接
            websocket = await self._connect()
            if not websocket:
                return

            self.websocket = websocket

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
                self.logger.error(f"WebSocket任务组异常: {e}")
                # 取消所有任务
                for task in tasks:
                    if not task.done():
                        task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await task
        except Exception as e:
            self.logger.error(f"WebSocket客户端运行异常: {e}", exc_info=True)
        finally:
            self.is_running = False
            await self._cleanup()

    async def close(self):
        """关闭WebSocket连接"""
        self.logger.info("正在关闭WebSocket连接...")
        self.is_running = False

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

    def _sign(self, params: str) -> Dict[str, str]:
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

    async def _get_websocket_info(self) -> tuple[Optional[str], Optional[str]]:
        """获取WebSocket连接信息"""
        try:
            # 开启应用
            post_url = f"{self.api_host}/v2/app/start"
            params = json.dumps({"code": self.id_code, "app_id": self.app_id})
            header_map = self._sign(params)

            self.logger.debug(f"请求应用启动: {post_url}")
            response = requests.post(url=post_url, headers=header_map, data=params, verify=False, timeout=30)
            response.raise_for_status()

            data = response.json()
            self.logger.debug(f"应用启动响应: {data}")

            if data.get("code") != 0:
                self.logger.error(f"应用启动失败: {data}")
                return None, None

            self.game_id = str(data["data"]["game_info"]["game_id"])
            wss_link = data["data"]["websocket_info"]["wss_link"][0]
            auth_body = data["data"]["websocket_info"]["auth_body"]

            self.logger.info(f"应用启动成功，游戏ID: {self.game_id}")
            return wss_link, auth_body

        except Exception as e:
            self.logger.error(f"获取WebSocket信息失败: {e}", exc_info=True)
            return None, None

    async def _send_app_heartbeat(self):
        """发送应用心跳"""
        if not self.game_id:
            return

        try:
            post_url = f"{self.api_host}/v2/app/heartbeat"
            params = json.dumps({"game_id": self.game_id})
            header_map = self._sign(params)

            response = requests.post(url=post_url, headers=header_map, data=params, verify=False, timeout=10)
            response.raise_for_status()

            data = response.json()
            if data.get("code") == 0:
                self.logger.debug("应用心跳发送成功")
            else:
                self.logger.warning(f"应用心跳失败: {data}")

        except Exception as e:
            self.logger.warning(f"发送应用心跳时出错: {e}")

    async def _end_app(self):
        """结束应用"""
        if not self.game_id:
            return

        try:
            post_url = f"{self.api_host}/v2/app/end"
            params = json.dumps({"game_id": self.game_id, "app_id": self.app_id})
            header_map = self._sign(params)

            response = requests.post(url=post_url, headers=header_map, data=params, verify=False, timeout=10)
            response.raise_for_status()

            data = response.json()
            if data.get("code") == 0:
                self.logger.info("应用结束成功")
            else:
                self.logger.warning(f"应用结束失败: {data}")

        except Exception as e:
            self.logger.warning(f"结束应用时出错: {e}")

    async def _auth(self, websocket, auth_body: str):
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
            websocket = await websockets.connect(wss_link, ping_interval=30, ping_timeout=10)

            # 进行认证
            if not await self._auth(websocket, auth_body):
                await websocket.close()
                return None

            self.logger.info("WebSocket连接建立成功")
            return websocket

        except Exception as e:
            self.logger.error(f"建立WebSocket连接时出错: {e}", exc_info=True)
            return None

    async def _heartbeat_loop(self):
        """WebSocket心跳循环"""
        while self.is_running and self.websocket:
            try:
                proto = Proto()
                proto.op = 2
                proto.body = ""
                data = proto.pack()

                await self.websocket.send(data)
                self.logger.debug("WebSocket心跳已发送")

                await asyncio.sleep(30)  # 每30秒发送一次心跳

            except Exception as e:
                self.logger.warning(f"发送WebSocket心跳时出错: {e}")
                break

    async def _app_heartbeat_loop(self):
        """应用心跳循环"""
        while self.is_running:
            try:
                await self._send_app_heartbeat()
                await asyncio.sleep(20)  # 每20秒发送一次应用心跳

            except Exception as e:
                self.logger.warning(f"应用心跳循环出错: {e}")
                break

    async def _recv_loop(self, message_handler: Callable, queue: asyncio.Queue = None):
        """接收消息循环"""
        while self.is_running and self.websocket:
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
                        self.logger.error(f"处理消息时出错: {e}", exc_info=True)
                elif proto.op == 3:  # 心跳回复
                    self.logger.debug("收到WebSocket心跳回复")
                else:
                    self.logger.debug(f"收到其他类型消息，op: {proto.op}")

            except websockets.exceptions.ConnectionClosed:
                self.logger.warning("WebSocket连接已关闭")
                break
            except Exception as e:
                self.logger.error(f"接收消息时出错: {e}", exc_info=True)
                break

    async def _cleanup(self):
        """清理资源"""
        if self.websocket:
            try:
                await self.websocket.close()
            except Exception as e:
                self.logger.warning(f"清理WebSocket时出错: {e}")
            finally:
                self.websocket = None
