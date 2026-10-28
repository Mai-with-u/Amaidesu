"""跨测试域共享的替身构造器；只服务单个子域的放该子域的 helpers.py。"""

from __future__ import annotations

from typing import Any

from src.modules.llm.payload import Response


class FakeClock:
    """可手动推进的毫秒假时钟，替换全库时间入口 ``now_ms``（src/modules/utils/time_utils）。

    用法：生产组件若支持注入时间源（构造参数/可替换的 ``now_ms``），测试把
    ``fake_clock.now_ms`` 传入即得确定性时间；用 ``tick(ms)`` 显式推进，攒批窗口、
    重试退避、TTL 过期等时长判定不再依赖真实等待。

    适用场景与限制：
    - 只影响经 ``now_ms``（或同形毫秒时刻入口）取时刻的代码路径；直接读
      ``time.time()`` / ``time.monotonic()`` / ``asyncio.sleep`` 的代码不受控，
      那些路径要么开注入口，要么用条件轮询（见 tests/config/helpers.py 的 wait_until）。
    - 各模块 ``from ... import now_ms`` 绑定的是各自命名空间里的名字，替换时
      要 patch 到 import 处（如 ``monkeypatch.setattr("某模块.now_ms", fake_clock.now_ms)``）。

    起始时刻取一个远离当前日期的固定值，断言可与真实时间戳区分。
    """

    def __init__(self, start_ms: int = 1_791_000_000_000) -> None:
        self._now_ms = start_ms

    def now_ms(self) -> int:
        """当前假时刻（Unix epoch 毫秒）。"""
        return self._now_ms

    def tick(self, ms: int) -> None:
        """把假时钟向前推进 ms 毫秒。"""
        self._now_ms += ms


def llm_response(**fields: Any) -> Response:
    """构造真实 :class:`~src.modules.llm.payload.Response` 作为 LLM 响应替身。

    生产代码按契约直取响应属性（含 ``ok``），SimpleNamespace 手搓替身缺字段
    即 AttributeError；替身一律经这里构造真 Response，字段形状与生产契约同源。
    """
    return Response(**fields)
