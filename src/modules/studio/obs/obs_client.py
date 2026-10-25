"""OBS WebSocket 客户端适配层：包装 MIT 许可的 obs-websocket-py（导入名 obswebsocket）。

obs_provider、直播画面采集器与诊断脚本共用。作用有两个：

- 软降级：obswebsocket 是可选依赖，没装时本项目其余功能照常——这里在模块顶部
  try/except 导入并暴露 ``AVAILABLE`` 标志，调用方据此判断是否可用；
- 统一错误面：连接失败还原底层 socket 异常，请求被 OBS 拒绝时抛携带协议错误码
  （如 600=资源不存在）的 :class:`OBSRequestError`，供 ``obs_errors`` 翻译层按码
  给出"找不到场景/来源"等可操作原因。
"""

from __future__ import annotations

import json
import threading
from typing import Any, Optional

# 可选重型依赖延迟加载的变体：obswebsocket 未安装时只让 OBS 相关功能降级，
# 其余模块导入本适配层不受影响
try:
    from obswebsocket import exceptions as obs_exceptions
    from obswebsocket import requests as obs_requests
    from obswebsocket.core import obsws
except ImportError:  # pragma: no cover - 取决于环境是否安装该可选依赖
    obs_exceptions = None  # type: ignore[assignment]
    obs_requests = None  # type: ignore[assignment]
    obsws = None  # type: ignore[assignment]

__all__ = ["AVAILABLE", "OBSRequestError", "ObsClient", "obs_requests"]

# obswebsocket 是否可用（未安装时 OBS 相关调用方应软降级）
AVAILABLE = obsws is not None


class OBSRequestError(Exception):
    """OBS 拒绝了请求（协议 requestStatus.result=false）。

    属性名与错误翻译层的读法对齐：``req_name`` 请求名、``code`` 协议错误码
    （如 600=资源不存在）、``comment`` OBS 给的原因。``str(error)`` 即 comment，
    与旧库请求异常的文本习惯一致。
    """

    def __init__(self, req_name: str, code: Optional[int], comment: str) -> None:
        super().__init__(comment)
        self.req_name = req_name
        self.code = code
        self.comment = comment


class ObsClient:
    """obsws 的 v5 组合包装：连接异常还原底层原因，请求往返自带错误码。

    只支持 obs-websocket 5 协议（OBS 28+），因此显式 ``legacy=False``——
    不依赖库对 4444 端口的猜测。
    """

    def __init__(self, host: str, port: int, password: str, timeout: int) -> None:
        if obsws is None:
            # 对齐 obs_errors 的 ImportError 分支：没装依赖也要能翻译成"照着装"的原因
            raise ImportError("obs-websocket-py 未安装：在项目目录执行 uv sync 安装依赖")
        self._client: Any = obsws(host, port, password, legacy=False, timeout=timeout)

    def connect(self) -> None:
        """建立连接。

        连接/身份验证阶段的失败一律以 ConnectionFailure 抛出：socket 层错误
        （OBS 没开、超时等）被库包装成 ``ConnectionFailure(str(e))``，这里还原成
        原始 OSError 抛出，翻译层好按 ConnectionRefusedError/TimeoutError 给出
        对应改法；身份验证被拒时库抛的是裸 WebSocketConnectionClosedException，
        不经本方法包装、直接透传。
        """
        try:
            self._client.connect()
        except obs_exceptions.ConnectionFailure as exc:
            cause = exc.__context__
            if isinstance(cause, OSError):
                raise cause from exc
            raise

    def call(self, request: Any) -> Any:
        """发送一条 v5 请求并返回填充了响应数据的请求对象。

        不复用基类的 ``call``：它对被 OBS 拒绝（requestStatus.result=false）的
        请求不抛异常，且丢弃回执里的 code 与 comment——错误码要给翻译层用
        （600 → "找不到场景/来源"），因此自行实现请求往返：失败结果抛
        :class:`OBSRequestError`，超时未回执抛内建 TimeoutError。
        """
        message_id = str(self._client.id)
        self._client.id += 1
        done = threading.Event()
        self._client.events[message_id] = done
        payload = {
            "op": 6,
            "d": {
                "requestId": message_id,
                "requestType": request.name,
                "requestData": request.data(),
            },
        }
        try:
            self._client.ws.send(json.dumps(payload))
            if not done.wait(self._client.timeout):
                raise TimeoutError(f"OBS 未在 {self._client.timeout} 秒内回执请求 {request.name}")
            answer = self._client.answers.pop(message_id)
        finally:
            self._client.events.pop(message_id, None)
            self._client.answers.pop(message_id, None)
        request_status = answer.get("requestStatus") or {}
        if not request_status.get("result"):
            raise OBSRequestError(request.name, request_status.get("code"), answer.get("comment") or "")
        request.input(answer.get("responseData") or {}, True)
        return request

    def disconnect(self) -> None:
        """断开连接（委托底层）。"""
        self._client.disconnect()
