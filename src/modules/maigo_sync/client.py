"""MaiGO HTTP 协议客户端：封装签出、心跳、签入与话头消费接口。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol

import aiohttp

__all__ = ["HttpMaiGOClient", "MaiGOClient", "MaiGORejected"]


class MaiGORejected(RuntimeError):
    """服务端明确拒收（HTTP 4xx：鉴权或请求结构不合法），原样重试不会成功。"""


class MaiGOClient(Protocol):
    """与 MaiGO 跨端记忆中心通信的异步协议端口。"""

    async def checkout(self, payload: Dict[str, Any]) -> Dict[str, Any]: ...

    async def heartbeat(self, checkout_id: str, payload: Dict[str, Any]) -> Dict[str, Any]: ...

    async def checkin(self, checkout_id: str, payload: Dict[str, Any]) -> Dict[str, Any]: ...

    async def consume_topic(self, topic_id: int, *, branch_id: str) -> Dict[str, Any]: ...

    async def post_events(self, events: List[Dict[str, Any]]) -> Dict[str, Any]: ...

    async def close(self) -> None: ...


class HttpMaiGOClient:
    """基于 aiohttp 的 MaiGO REST JSON 客户端。

    触发条件：由 MaiGOBridge 在开播、场中心跳与下播收尾节点调用；
    执行顺序：按需懒建 ClientSession（带超时与可选 Bearer 鉴权头） -> 发起 HTTP 请求
    -> 4xx 抛 MaiGORejected 停止无意义重试，5xx 或网络错误抛异常交由上层退避或降级。
    """

    def __init__(self, *, base_url: str, token: str = "", timeout_ms: int = 5_000) -> None:
        self._base_url = base_url.strip().rstrip("/")
        cleaned_token = token.strip()
        self._headers: Dict[str, str] = {"Authorization": f"Bearer {cleaned_token}"} if cleaned_token else {}
        self._timeout = aiohttp.ClientTimeout(total=max(0.5, timeout_ms / 1000))
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """懒加载复用的异步 HTTP 会话，避免在无事件循环的同步装配阶段提前创建连接池。"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    async def _post_json(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """统一执行 POST JSON 调用与状态码分级处理。"""
        session = await self._get_session()
        target = f"{self._base_url}{path}"
        async with session.post(target, json=payload, headers=self._headers) as response:
            if response.status >= 300:
                detail = (await response.text())[:300]
                if 400 <= response.status < 500:
                    raise MaiGORejected(f"HTTP {response.status}: {detail}")
                raise RuntimeError(f"HTTP {response.status}: {detail}")
            data = await response.json()
            return dict(data) if isinstance(data, dict) else {}

    async def checkout(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """调用开播签出接口一次性获取简报、承诺、话头卡与熟人目录。"""
        return await self._post_json("/api/v1/sessions/checkout", payload)

    async def heartbeat(self, checkout_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """上报直播中状态心跳与已选用的话头卡编号。"""
        return await self._post_json(f"/api/v1/sessions/{checkout_id}/heartbeat", payload)

    async def checkin(self, checkout_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """下播整理完成后回推本场档案、承诺变更与熟人互动。"""
        return await self._post_json(f"/api/v1/sessions/{checkout_id}/checkin", payload)

    async def consume_topic(self, topic_id: int, *, branch_id: str) -> Dict[str, Any]:
        """标记某条跨端话头卡已在当前分支消费。"""
        return await self._post_json(f"/api/v1/topics/{int(topic_id)}/consume", {"branch_id": branch_id})

    async def post_events(self, events: List[Dict[str, Any]]) -> Dict[str, Any]:
        """批量上报历史或增量经历事件（依赖服务端 idempotency_key 去重）。"""
        return await self._post_json("/api/v1/events", {"events": list(events)})

    async def close(self) -> None:
        """关闭底层 HTTP 会话连接池（幂等）。"""
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None
