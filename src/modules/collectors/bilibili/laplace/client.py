"""LAPLACE Event Bridge 客户端（client 角色）

LAPLACE Chat（https://laplace.live/chat）是浏览器中的 B 站直播聊天面板，
把弹幕、SC、礼物分栏显示。LAPLACE Event Bridge
（https://laplace.live/chat/event-bridge）是它的 WebSocket 事件桥：桥接
服务器把 LAPLACE Chat 收到的实时 B 站直播事件广播给本地客户端。

本模块以 client 角色连接桥接服务器（如官方 ``leb-server``），接收其广播
的 B 站直播事件。前置要求是已按 Event Bridge 文档启动桥接服务器。

协议要点（依据官方 SDK 与 Go bridge 源码）：

- 无 token 时 client 不需要发送 subprotocol——裸连接在 bridge 侧即视为 client。
- 有 token 时官方 SDK 同时发送 ``["laplace-event-bridge-role-client", token]``
  与 ``?token=``。但 ``websockets`` 对 subprotocol 做 HTTP token 字符集校验，
  ``=`` / ``/`` / ``:`` 等字符会直接抛 ``ValueError``；因此 token 始终走 query，
  只有满足 HTTP token 字符集时才额外放进 subprotocol。
- 连接建立后先收到 ``established``；服务端业务事件会被 bridge 加上 ``source``
  字段（由采集器忽略）。``ping`` 是 Event Fetcher 的应用层心跳，需要回 ``pong``。
- bridge 是纯广播、无历史补播；断线后本客户端按指数退避无限重连，直到取消。
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, AsyncIterator, Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import websockets
from websockets.exceptions import ConnectionClosed, InvalidStatus

from src.modules.logging import get_logger
from src.modules.time_utils import now_ms

# 非业务的控制消息（established 单独处理）
_CONTROL_TYPES = frozenset(
    {
        "pong",
        "broadcast-success",
        "client-message-received",
        "unknown-message",
        "error",
    }
)

# subprotocol 值必须满足 HTTP token 字符集；不合法时 token 只走 query
_HTTP_TOKEN_RE = re.compile(r"[-!#$%&'*+.^_`|~0-9A-Za-z]+")

_MAX_MESSAGE_BYTES = 4 * 1024 * 1024
_PING_INTERVAL_S = 20.0
_PING_TIMEOUT_S = 20.0
_CLOSE_TIMEOUT_S = 1.0


class LaplaceBridgeClient:
    """连接 ``leb-server`` 的 client 角色客户端（自动重连）。

    Args:
        url: bridge WebSocket 地址（如 ``ws://localhost:9696``）。
        token: 可选认证 token；非空时始终通过 query 传递。
        reconnect_base_ms: 首次重连等待毫秒数。
        reconnect_max_ms: 重连等待上限毫秒数。
    """

    def __init__(
        self,
        url: str,
        token: str,
        *,
        reconnect_base_ms: int,
        reconnect_max_ms: int,
    ) -> None:
        self.logger = get_logger(self.__class__.__name__)
        self._url = url
        self._token = token
        self._reconnect_base_ms = reconnect_base_ms
        self._reconnect_max_ms = reconnect_max_ms
        self._client_id: str = ""

    @property
    def client_id(self) -> str:
        """bridge 分配的客户端 ID（``established`` 到达后才有值）。"""
        return self._client_id

    async def events(self) -> AsyncIterator[Dict[str, Any]]:
        """无限重连并产出业务事件 dict；取消时直接退出。"""
        attempt = 0
        while True:
            try:
                async with websockets.connect(
                    self._connect_url(),
                    subprotocols=self._subprotocols(),
                    ping_interval=_PING_INTERVAL_S,
                    ping_timeout=_PING_TIMEOUT_S,
                    close_timeout=_CLOSE_TIMEOUT_S,
                    max_size=_MAX_MESSAGE_BYTES,
                    # 本机 bridge 不走系统代理（Windows 代理未绕过 localhost 时会误连）
                    proxy=None,
                ) as websocket:
                    self.logger.info(f"LAPLACE Event Bridge 已连接: {self._display_url()}")
                    async for raw in websocket:
                        event = self._decode(raw)
                        if event is None:
                            continue
                        event_type = event.get("type")
                        if event_type == "ping":
                            await self._send_pong(websocket, event)
                            continue
                        if event_type == "established":
                            self._client_id = str(event.get("clientId") or "")
                            self.logger.info(
                                f"LAPLACE Event Bridge 握手完成: clientId={self._client_id or '<unknown>'}"
                            )
                            attempt = 0
                            continue
                        if event_type in _CONTROL_TYPES:
                            continue
                        yield event
            except asyncio.CancelledError:
                raise
            except InvalidStatus as exc:
                status = exc.response.status_code
                if status == 401:
                    self.logger.error("LAPLACE bridge 认证失败（401）：token 与 leb-server --auth 不一致")
                elif status == 404:
                    self.logger.error(f"LAPLACE bridge 路径错误（404）：{self._display_url()}")
                else:
                    self.logger.error(f"LAPLACE bridge 握手失败（HTTP {status}）：{self._display_url()}")
            except (OSError, TimeoutError) as exc:
                self.logger.warning(
                    f"LAPLACE bridge 不可达（{self._display_url()}）：{type(exc).__name__}: {exc}；"
                    "请确认 leb-server 已启动",
                    exc=exc,
                )
            except ConnectionClosed as exc:
                self.logger.warning(f"LAPLACE bridge 连接断开: code={exc.code} reason={exc.reason or ''}")
            except Exception as exc:  # noqa: BLE001 - 重连边界兜底
                self.logger.warning(
                    f"LAPLACE bridge 连接异常: {type(exc).__name__}: {exc}",
                    exc=exc,
                )

            attempt += 1
            delay_ms = min(
                int(self._reconnect_base_ms * (1.5 ** (attempt - 1))),
                self._reconnect_max_ms,
            )
            self.logger.info(f"LAPLACE bridge {delay_ms / 1000:.1f}s 后重连（第 {attempt} 次）")
            await asyncio.sleep(delay_ms / 1000)

    def _connect_url(self) -> str:
        """返回带 query token 的连接地址（token 为空时原样返回）。"""
        if not self._token:
            return self._url
        parts = urlsplit(self._url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query["token"] = self._token
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))

    def _subprotocols(self) -> Optional[List[str]]:
        """返回 client 角色 subprotocol；token 不合法时只发角色。"""
        if not self._token:
            return None
        protocols = ["laplace-event-bridge-role-client"]
        if _HTTP_TOKEN_RE.fullmatch(self._token):
            protocols.append(self._token)
        else:
            self.logger.debug("token 含非法 subprotocol 字符，仅通过 query 传递")
        return protocols

    def _display_url(self) -> str:
        """日志用地址：token 一律替换为 ``***``。"""
        parts = urlsplit(self._url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        if self._token or "token" in query:
            query["token"] = "***"
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))

    def _decode(self, raw: str | bytes) -> Optional[Dict[str, Any]]:
        """解析单帧 JSON；失败只告警并返回 None（不中断连接）。"""
        try:
            event = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            self.logger.warning(f"LAPLACE bridge 收到无法解析的消息（已忽略）: {type(exc).__name__}: {exc}")
            return None
        if not isinstance(event, dict):
            self.logger.warning(f"LAPLACE bridge 收到非对象 JSON（已忽略）: {type(event).__name__}")
            return None
        return event

    @staticmethod
    async def _send_pong(websocket: Any, ping: Dict[str, Any]) -> None:
        """回复 Event Fetcher 的应用层心跳。"""
        await websocket.send(
            json.dumps(
                {
                    "type": "pong",
                    "timestamp": now_ms(),
                    "respondingTo": ping.get("timestamp"),
                }
            )
        )


__all__ = ["LaplaceBridgeClient"]
