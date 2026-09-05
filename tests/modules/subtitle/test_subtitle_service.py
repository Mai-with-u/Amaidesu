"""测试 SubtitleService - 多 Backend 并行广播 + 故障隔离 + 幂等生命周期

覆盖：

- ``show`` 广播到全部已注册 Backend
- ``clear`` 广播到全部已注册 Backend
- 单 Backend 抛异常不影响其他 Backend（其他后端仍被调用）
- 无 Backend 时 ``show`` / ``clear`` 不抛错
- ``start`` / ``stop`` 幂等（重复调用早退，不炸）
- ``is_running`` / ``backend_count`` 诊断属性
- ``register_backend`` 追加行为（多次注册累加）
"""

from __future__ import annotations

from typing import List, Optional

import pytest

from src.modules.subtitle import SubtitleBackend, SubtitleService


# ---------------------------------------------------------------------------
# 测试用假后端：记录所有调用 + 可配置抛错
# ---------------------------------------------------------------------------


class _FakeBackend:
    """满足 ``SubtitleBackend`` 协议的最小假后端。

    记录每次 ``show`` / ``clear`` 调用（文本 / utterance_id），并允许
    通过 ``raise_on_show`` / ``raise_on_clear`` 配置抛错。
    """

    def __init__(
        self,
        *,
        raise_on_show: Optional[BaseException] = None,
        raise_on_clear: Optional[BaseException] = None,
    ) -> None:
        self.show_calls: List[tuple[str, Optional[str]]] = []
        self.clear_calls: int = 0
        self._raise_on_show = raise_on_show
        self._raise_on_clear = raise_on_clear

    async def show(self, text: str, utterance_id: Optional[str] = None) -> None:
        self.show_calls.append((text, utterance_id))
        if self._raise_on_show is not None:
            raise self._raise_on_show

    async def clear(self) -> None:
        self.clear_calls += 1
        if self._raise_on_clear is not None:
            raise self._raise_on_clear


# ---------------------------------------------------------------------------
# 协议契约
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    def test_fake_backend_satisfies_protocol(self):
        """假后端满足 ``SubtitleBackend`` 协议（运行时校验通过）。"""
        backend = _FakeBackend()
        assert isinstance(backend, SubtitleBackend)

    def test_object_missing_methods_rejected(self):
        """缺 ``show`` / ``clear`` 的对象不满足契约。"""
        assert not isinstance(object(), SubtitleBackend)
        assert not isinstance({}, SubtitleBackend)
        assert not isinstance(None, SubtitleBackend)


# ---------------------------------------------------------------------------
# 生命周期
# ---------------------------------------------------------------------------


class TestLifecycle:
    async def test_start_stop_idempotent(self):
        """``start`` / ``stop`` 重复调用不抛错，标志位翻转正确。"""
        svc = SubtitleService()
        assert svc.is_running is False

        await svc.start()
        assert svc.is_running is True

        # 重复 start 不抛错，状态保持
        await svc.start()
        assert svc.is_running is True

        await svc.stop()
        assert svc.is_running is False

        # 重复 stop 不抛错
        await svc.stop()
        assert svc.is_running is False

    async def test_stop_without_start_is_noop(self):
        """未启动时调 ``stop`` 不抛错（早退）。"""
        svc = SubtitleService()
        await svc.stop()
        assert svc.is_running is False

    async def test_backend_count_after_register(self):
        """``backend_count`` 正确反映已注册数量。"""
        svc = SubtitleService()
        assert svc.backend_count == 0
        svc.register_backend(_FakeBackend())
        assert svc.backend_count == 1
        svc.register_backend(_FakeBackend())
        assert svc.backend_count == 2

    async def test_start_without_backends_does_not_raise(self):
        """无 Backend 注册时 ``start`` 不抛错（允许装配期留空后续追加）。"""
        svc = SubtitleService()
        await svc.start()
        assert svc.is_running is True


# ---------------------------------------------------------------------------
# 广播
# ---------------------------------------------------------------------------


class TestBroadcast:
    async def test_show_broadcasts_to_all_backends(self):
        """``show`` 广播到全部已注册 Backend，每个后端收到 (text, utterance_id)。"""
        svc = SubtitleService()
        b1 = _FakeBackend()
        b2 = _FakeBackend()
        svc.register_backend(b1)
        svc.register_backend(b2)

        await svc.show("hello", utterance_id="u-1")

        assert b1.show_calls == [("hello", "u-1")]
        assert b2.show_calls == [("hello", "u-1")]

    async def test_show_passes_none_utterance_id(self):
        """``utterance_id=None`` 时透传到后端。"""
        svc = SubtitleService()
        b = _FakeBackend()
        svc.register_backend(b)

        await svc.show("plain text")

        assert b.show_calls == [("plain text", None)]

    async def test_clear_broadcasts_to_all_backends(self):
        """``clear`` 广播到全部已注册 Backend，每个后端调用计数 +1。"""
        svc = SubtitleService()
        b1 = _FakeBackend()
        b2 = _FakeBackend()
        svc.register_backend(b1)
        svc.register_backend(b2)

        await svc.clear()
        await svc.clear()

        assert b1.clear_calls == 2
        assert b2.clear_calls == 2

    async def test_no_backends_show_is_noop(self):
        """无 Backend 时 ``show`` 不抛错。"""
        svc = SubtitleService()
        # 不抛错即可
        await svc.show("anything")

    async def test_no_backends_clear_is_noop(self):
        """无 Backend 时 ``clear`` 不抛错。"""
        svc = SubtitleService()
        await svc.clear()


# ---------------------------------------------------------------------------
# 故障隔离
# ---------------------------------------------------------------------------


class TestFaultIsolation:
    async def test_single_backend_show_failure_does_not_affect_others(self):
        """单 Backend ``show`` 抛异常 → 其他 Backend 仍被调用。"""
        svc = SubtitleService()
        boom = _FakeBackend(raise_on_show=RuntimeError("boom"))
        ok = _FakeBackend()
        svc.register_backend(boom)
        svc.register_backend(ok)

        # 不应向上传播异常
        await svc.show("hello")

        # 异常后端被调用了一次（抛错前），正常后端也调用了
        assert len(boom.show_calls) == 1
        assert ok.show_calls == [("hello", None)]

    async def test_single_backend_clear_failure_does_not_affect_others(self):
        """单 Backend ``clear`` 抛异常 → 其他 Backend 仍被调用。"""
        svc = SubtitleService()
        boom = _FakeBackend(raise_on_clear=RuntimeError("boom"))
        ok = _FakeBackend()
        svc.register_backend(boom)
        svc.register_backend(ok)

        await svc.clear()

        assert boom.clear_calls == 1
        assert ok.clear_calls == 1

    async def test_all_backends_failing_still_returns_cleanly(self):
        """所有 Backend 都抛异常 → ``show`` 仍正常返回（隔离保证）。"""
        svc = SubtitleService()
        svc.register_backend(_FakeBackend(raise_on_show=RuntimeError("a")))
        svc.register_backend(_FakeBackend(raise_on_show=ValueError("b")))

        # 不应抛错
        await svc.show("hello")
