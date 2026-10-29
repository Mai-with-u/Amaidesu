"""VTS 插件接口客户端：一条 WebSocket，请求按编号对回复，逐帧注入发完就走

为什么不用 pyvts：pyvts 的请求是"发一条、等下一条消息当回复"，没有常驻接收循环，
所以只能一把锁串行。实测 VTS 每渲染一帧才处理一次排队的请求，
锁步发送时每条请求都要白等一帧；连发不等回复，10 条的回复在 37 毫秒内全部回来、
顺序还可能乱。这里改成：一个后台接收循环按 ``requestID`` 把回复交给等待者；逐帧
注入走 :meth:`send`，发出去就返回，回复到了只在报错时记日志。

授权流程（VTS 官方插件接口）：有令牌先用令牌登录；没有或失效就申请令牌——VTS 弹窗
请用户点"允许"，拿到后写入令牌文件，下次不用再点。
"""

from __future__ import annotations

import asyncio
import itertools
import json
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional

from websockets.asyncio.client import connect as ws_connect

from src.modules.logging import get_logger

API_NAME = "VTubeStudioPublicAPI"
API_VERSION = "1.0"

Connector = Callable[[str], Awaitable[Any]]


class VtsApiError(RuntimeError):
    """VTS 返回了 APIError（带 VTS 的错误编号与说明）。"""

    def __init__(self, message_type: str, error_id: int, message: str) -> None:
        super().__init__(f"{message_type} 失败（VTS 错误 {error_id}）: {message}")
        self.error_id = error_id


class VtsClient:
    """VTS 插件接口客户端（单连接；断开后由使用方重新 ``connect``）。"""

    # 申请令牌要等用户在 VTS 弹窗里点"允许"，给足时间
    TOKEN_TIMEOUT_S = 600.0
    REQUEST_TIMEOUT_S = 5.0

    def __init__(
        self,
        *,
        host: str,
        port: int,
        plugin_name: str,
        plugin_developer: str,
        token_path: Path,
        connector: Connector = ws_connect,
        logger_name: str = "VTSClient",
    ) -> None:
        self.logger = get_logger(logger_name)
        self._url = f"ws://{host}:{port}"
        self._plugin_name = plugin_name
        self._plugin_developer = plugin_developer
        self._token_path = token_path
        self._connector = connector
        self._socket: Any = None
        self._reader: Optional[asyncio.Task] = None
        self._waiters: Dict[str, asyncio.Future] = {}
        self._ids = itertools.count(1)
        self._send_errors = 0
        self.authenticated = False

    @property
    def is_open(self) -> bool:
        return self._socket is not None and self._reader is not None and not self._reader.done()

    # ===== 连接与授权 =====

    async def connect(self) -> None:
        """建立连接并完成授权；失败时关闭连接并抛出原因。"""
        await self.close()
        self._socket = await self._connector(self._url)
        self._reader = asyncio.create_task(self._read_loop(), name="VTSClient.reader")
        try:
            await self._authenticate()
        except BaseException:
            await self.close()
            raise

    async def _authenticate(self) -> None:
        token = self._read_token()
        if token and await self._login(token):
            return
        # 没有令牌或令牌失效（比如在 VTS 里撤销过授权）：申请新令牌，VTS 会弹窗
        self.logger.info("向 VTS 申请插件授权，请在 VTS 弹窗里点「允许」")
        response = await self.request(
            "AuthenticationTokenRequest",
            {"pluginName": self._plugin_name, "pluginDeveloper": self._plugin_developer},
            timeout_s=self.TOKEN_TIMEOUT_S,
        )
        token = str(response.get("authenticationToken") or "")
        if not token:
            raise ConnectionError("VTS 没有返回授权令牌")
        self._write_token(token)
        if not await self._login(token):
            raise ConnectionError("VTS 拒绝了新令牌的登录")

    async def _login(self, token: str) -> bool:
        response = await self.request(
            "AuthenticationRequest",
            {"pluginName": self._plugin_name, "pluginDeveloper": self._plugin_developer, "authenticationToken": token},
        )
        self.authenticated = bool(response.get("authenticated"))
        if not self.authenticated:
            self.logger.warning(f"VTS 令牌登录未通过: {response.get('reason', '')}")
        return self.authenticated

    def _read_token(self) -> str:
        try:
            return self._token_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return ""

    def _write_token(self, token: str) -> None:
        self._token_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._token_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(token)

    async def close(self) -> None:
        """关闭连接（幂等）；还在等回复的请求一律以连接断开结束。"""
        socket, reader = self._socket, self._reader
        self._socket = None
        self._reader = None
        self.authenticated = False
        if socket is not None:
            try:
                await socket.close()
            except Exception as exc:  # noqa: BLE001 - 关闭失败不影响后续重连
                self.logger.debug(f"关闭 VTS 连接异常（已忽略）: {exc}")
        if reader is not None and not reader.done():
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
        self._fail_waiters(ConnectionError("VTS 连接已关闭"))

    # ===== 收发 =====

    def _envelope(self, message_type: str, data: Optional[Dict[str, Any]], request_id: str) -> str:
        return json.dumps(
            {
                "apiName": API_NAME,
                "apiVersion": API_VERSION,
                "requestID": request_id,
                "messageType": message_type,
                "data": data or {},
            },
            ensure_ascii=False,
        )

    async def request(
        self, message_type: str, data: Optional[Dict[str, Any]] = None, *, timeout_s: Optional[float] = None
    ) -> Dict[str, Any]:
        """发一个请求并等它的回复，返回回复的 ``data``；VTS 报错时抛 :class:`VtsApiError`。"""
        if self._socket is None:
            raise ConnectionError("VTS 未连接")
        request_id = f"amaidesu-{next(self._ids)}"
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._waiters[request_id] = future
        try:
            await self._socket.send(self._envelope(message_type, data, request_id))
            message = await asyncio.wait_for(future, timeout=timeout_s or self.REQUEST_TIMEOUT_S)
        finally:
            self._waiters.pop(request_id, None)
        if message.get("messageType") == "APIError":
            error = message.get("data") or {}
            raise VtsApiError(message_type, int(error.get("errorID", -1)), str(error.get("message", "")))
        return message.get("data") or {}

    async def send(self, message_type: str, data: Optional[Dict[str, Any]] = None) -> None:
        """发出去就返回、不等回复（逐帧注入用）；回复由接收循环丢弃，报错只记日志。"""
        if self._socket is None:
            raise ConnectionError("VTS 未连接")
        await self._socket.send(self._envelope(message_type, data, f"amaidesu-send-{next(self._ids)}"))

    async def _read_loop(self) -> None:
        try:
            async for raw in self._socket:
                self._dispatch(raw)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 连接断开属预期，等待者统一以断开结束
            self.logger.info(f"VTS 连接已断开: {type(exc).__name__}: {exc}")
        finally:
            self._fail_waiters(ConnectionError("VTS 连接已断开"))

    def _dispatch(self, raw: Any) -> None:
        try:
            message = json.loads(raw)
        except (TypeError, ValueError):
            self.logger.debug(f"收到无法解析的 VTS 消息（已忽略）: {str(raw)[:120]}")
            return
        future = self._waiters.get(str(message.get("requestID", "")))
        if future is not None and not future.done():
            future.set_result(message)
            return
        if message.get("messageType") == "APIError":
            # 发完就走的注入报错：首次与每 300 次记一条，避免每帧刷屏
            self._send_errors += 1
            if self._send_errors == 1 or self._send_errors % 300 == 0:
                self.logger.warning(f"VTS 拒绝了注入（累计 {self._send_errors} 次）: {message.get('data')}")

    def _fail_waiters(self, error: BaseException) -> None:
        for future in self._waiters.values():
            if not future.done():
                future.set_exception(error)
        self._waiters.clear()


__all__ = ["Connector", "VtsApiError", "VtsClient"]
