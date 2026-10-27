"""跨测试域共享的替身构造器；只服务单个子域的放该子域的 helpers.py。"""

from __future__ import annotations

from typing import Any

from src.modules.llm.payload import Response


def llm_response(**fields: Any) -> Response:
    """构造真实 :class:`~src.modules.llm.payload.Response` 作为 LLM 响应替身。

    生产代码按契约直取响应属性（含 ``ok``），SimpleNamespace 手搓替身缺字段
    即 AttributeError；替身一律经这里构造真 Response，字段形状与生产契约同源。
    """
    return Response(**fields)
