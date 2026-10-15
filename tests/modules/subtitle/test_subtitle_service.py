"""SubtitleService（多后端并行广播、故障隔离、幂等生命周期、诊断清单）与 build_subtitle_infrastructure 装配入口"""

from __future__ import annotations

from typing import Optional
from unittest.mock import patch

import pytest

from src.modules.subtitle import SubtitleBackend, SubtitleService, TkGuiBackend, build_subtitle_infrastructure
from src.modules.subtitle.backends.tk_gui_service import SubtitleGuiService


class _Backend:
    """满足 SubtitleBackend 协议的假后端：记录调用，可配置抛错；enabled 为 None 时不带该成员。"""

    def __init__(self, *, show_error: Optional[BaseException] = None, clear_error: Optional[BaseException] = None):
        self.show_calls: list[tuple[str, Optional[str]]] = []
        self.clear_calls = 0
        self._show_error = show_error
        self._clear_error = clear_error

    async def show(self, text: str, utterance_id: Optional[str] = None) -> None:
        self.show_calls.append((text, utterance_id))
        if self._show_error is not None:
            raise self._show_error

    async def clear(self) -> None:
        self.clear_calls += 1
        if self._clear_error is not None:
            raise self._clear_error


class _EnabledBackend(_Backend):
    def __init__(self, enabled: bool) -> None:
        super().__init__()
        self.enabled = enabled


def test_protocol_conformance_and_diagnostics() -> None:
    assert isinstance(_Backend(), SubtitleBackend)
    assert not any(isinstance(obj, SubtitleBackend) for obj in (object(), {}, None))

    svc = SubtitleService()
    assert svc.backend_count == 0 and svc.backend_diagnostics == []
    svc.register_backend(_EnabledBackend(True))
    svc.register_backend(_EnabledBackend(False))
    svc.register_backend(_Backend())  # 协议最小契约不含 enabled，缺失按 True 兜底
    assert svc.backend_count == 3
    assert [(d["name"], d["enabled"]) for d in svc.backend_diagnostics] == [
        ("_EnabledBackend", True),
        ("_EnabledBackend", False),
        ("_Backend", True),
    ]


async def test_start_and_stop_are_idempotent_even_without_backends() -> None:
    svc = SubtitleService()
    await svc.stop()  # 未启动先停：早退
    assert svc.is_running is False
    await svc.start()
    await svc.start()
    assert svc.is_running is True
    await svc.stop()
    await svc.stop()
    assert svc.is_running is False
    await svc.show("anything")  # 无后端时广播不抛错
    await svc.clear()


async def test_show_and_clear_broadcast_to_every_backend() -> None:
    svc = SubtitleService()
    first, second = _Backend(), _Backend()
    svc.register_backend(first)
    svc.register_backend(second)

    await svc.show("hello", utterance_id="u-1")
    await svc.show("plain text")  # utterance_id 缺省时透传 None
    await svc.clear()
    await svc.clear()

    for backend in (first, second):
        assert backend.show_calls == [("hello", "u-1"), ("plain text", None)] and backend.clear_calls == 2


async def test_failing_backend_never_blocks_the_others() -> None:
    svc = SubtitleService()
    boom = _Backend(show_error=RuntimeError("boom"), clear_error=RuntimeError("boom"))
    ok = _Backend()
    svc.register_backend(boom)
    svc.register_backend(ok)
    await svc.show("hello")  # 异常不向上传播，其他后端照常被调用
    await svc.clear()
    assert len(boom.show_calls) == 1 and ok.show_calls == [("hello", None)]
    assert boom.clear_calls == 1 and ok.clear_calls == 1

    everyone_fails = SubtitleService()
    everyone_fails.register_backend(_Backend(show_error=RuntimeError("a")))
    everyone_fails.register_backend(_Backend(show_error=ValueError("b")))
    await everyone_fails.show("hello")  # 全部失败也正常返回


# 装配入口


@pytest.fixture
def no_real_tk_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    """装配入口会启动 Tk 守护线程且无人停止；多个用例累积出并发 Tk 解释器，会让同进程后续测试随机段错误。"""
    monkeypatch.setattr(SubtitleGuiService, "start", lambda self: None)


@pytest.mark.usefixtures("no_real_tk_thread")
def test_assembly_registers_one_tk_backend_without_starting_the_service() -> None:
    service = build_subtitle_infrastructure({"window_title": "test-title", "font_size": 32})
    assert isinstance(service, SubtitleService) and service.backend_count == 1
    assert service.is_running is False  # 装配不触发 start，生命周期由组合根管理，与 TTS 引擎构造时不 setup 对齐

    gui = SubtitleGuiService(config={})
    backend = TkGuiBackend(service=gui)
    assert isinstance(backend, SubtitleBackend) and backend.enabled == gui.enabled  # CustomTkinter 不可用时联动为 False


@pytest.mark.usefixtures("no_real_tk_thread")
@pytest.mark.parametrize("bad_config", [None, "not-a-dict", 123, ["a", "b"], object()])
def test_assembly_with_non_dict_config_returns_empty_service(bad_config) -> None:
    service = build_subtitle_infrastructure(bad_config)
    assert isinstance(service, SubtitleService) and service.backend_count == 0  # fail-soft，不阻断冷启动


@pytest.mark.parametrize("error", [RuntimeError("simulated validation failure"), ValueError("bad config")])
def test_assembly_swallows_gui_service_construction_errors(error: Exception) -> None:
    with patch("src.modules.subtitle.assembly.SubtitleGuiService", side_effect=error):
        service = build_subtitle_infrastructure({})
    assert isinstance(service, SubtitleService) and service.backend_count == 0
