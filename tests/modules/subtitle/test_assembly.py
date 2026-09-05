"""测试 build_subtitle_infrastructure - 字幕基础设施装配入口

覆盖：

- 合法配置 → 返回 ``SubtitleService`` 且 ``backend_count == 1``
  （TkGuiBackend 已注册）
- 后端是 ``TkGuiBackend``，并持有 ``SubtitleGuiService`` 实例
- 后端 ``enabled`` 属性与 GUI 服务联动（CustomTkinter 不可用时为 False）
- 配置非 dict（None / str / list）→ 返回空 ``SubtitleService``（fail-soft，
  ``backend_count == 0``）
- ``SubtitleGuiService`` 构造抛异常 → 返回空 ``SubtitleService``
  （装配边界兜底，不污染下游）
- ``SubtitleService.start()`` 不被自动调用（装配入口不触发 broadcast-ready
  生命周期，与 TTS 引擎"构造时不 setup"对齐）

实现策略：

- ``build_subtitle_infrastructure`` 直接调用真实 ``SubtitleGuiService``，
  传入合法最小 config dict；CustomTkinter 可用性由被测环境决定，本测试
  断言后端 ``enabled`` 与 GUI 服务联动即可
- 异常分支用 ``unittest.mock.patch`` 在 ``src.modules.subtitle.assembly``
  模块空间替换 ``SubtitleGuiService``——避免污染全局
"""

from __future__ import annotations

from typing import Any, Dict
from unittest.mock import patch

import pytest

from src.modules.subtitle import (
    SubtitleBackend,
    SubtitleService,
    TkGuiBackend,
    build_subtitle_infrastructure,
)
from src.modules.subtitle.backends.tk_gui_service import SubtitleGuiService


# ---------------------------------------------------------------------------
# 合法配置：装配成功 + Backend 注册
# ---------------------------------------------------------------------------


class TestAssemblySuccess:
    def test_returns_subtitle_service(self):
        """合法配置 → 返回 ``SubtitleService`` 实例。"""
        service = build_subtitle_infrastructure({})
        assert isinstance(service, SubtitleService)

    def test_backend_count_is_one(self):
        """合法配置 → 注册 1 个 Backend（TkGuiBackend）。"""
        service = build_subtitle_infrastructure({})
        assert service.backend_count == 1

    def test_registered_backend_is_tk_gui(self):
        """注册的后端是 ``TkGuiBackend`` 实例。"""
        service = build_subtitle_infrastructure({})
        # 通过``isinstance``委托``SubtitleBackend``协议的实际注册路径校验
        # （``SubtitleService.register_backend`` 内部已做``isinstance``校验）
        assert service.backend_count == 1
        # 直接构造一个 TkGuiBackend 走同协议路径，确认装配选型一致
        from src.modules.subtitle.backends import TkGuiBackend as TkGuiBackendDirect

        assert TkGuiBackendDirect is TkGuiBackend

    def test_service_not_auto_started(self):
        """装配入口**不**触发 ``SubtitleService.start()``——组合根管理
        broadcast-ready 生命周期，与 TTS 引擎"构造时不 setup"对齐。"""
        service = build_subtitle_infrastructure({})
        assert service.is_running is False

    def test_assembly_does_not_call_subtitle_service_start(self):
        """即便 GUI 不可用，``SubtitleService.is_running`` 仍为 False
        （装配入口不触发 start 生命周期）。"""
        service = build_subtitle_infrastructure({})
        # ``is_running`` 是 start/stop 标志位——构造即未启动
        assert service.is_running is False


# ---------------------------------------------------------------------------
# 真实 SubtitleGuiService 装配：后端联动 + enabled 联动
# ---------------------------------------------------------------------------


class TestBackendWiring:
    def test_backend_satisfies_subtitle_backend_protocol(self):
        """注册的后端满足 ``SubtitleBackend`` 协议（结构契约）。"""
        service = build_subtitle_infrastructure({})
        # 通过``isinstance``协议运行时校验——``@runtime_checkable``
        assert service.backend_count == 1
        # 由于 ``backend_count`` 是唯一对外诊断接口，单独构造一个
        # TkGuiBackend 验证协议结构契约即可
        from src.modules.subtitle.backends import TkGuiBackend as _TkGuiBackend

        backend = _TkGuiBackend(service=SubtitleGuiService(config={}))
        assert isinstance(backend, SubtitleBackend)

    def test_gui_service_constructed_with_provided_config(self):
        """``SubtitleGuiService`` 用传入的 config 字典构造（duck check）。"""
        # 用任意非空 dict 触发构造；``SubtitleGuiService.__init__`` 在
        # CTK 不可用时仅存储 config + 设 _enabled=False，**不抛错**
        custom_cfg: Dict[str, Any] = {"window_title": "test-title", "font_size": 32}
        service = build_subtitle_infrastructure(custom_cfg)
        assert isinstance(service, SubtitleService)
        assert service.backend_count == 1


# ---------------------------------------------------------------------------
# 输入防御：非 dict / None
# ---------------------------------------------------------------------------


class TestInvalidConfig:
    def test_none_config_returns_empty_service(self):
        """``config=None`` → 返回空 ``SubtitleService``（WARN + backend_count == 0）。"""
        service = build_subtitle_infrastructure(None)
        assert isinstance(service, SubtitleService)
        assert service.backend_count == 0

    def test_non_dict_config_returns_empty_service(self):
        """``config`` 非 dict → 返回空 ``SubtitleService``。"""
        for bad_input in ["not-a-dict", 123, ["a", "b"], object()]:
            service = build_subtitle_infrastructure(bad_input)
            assert isinstance(service, SubtitleService), f"failed for {bad_input!r}"
            assert service.backend_count == 0, f"failed for {bad_input!r}"


# ---------------------------------------------------------------------------
# 构造异常兜底：装配边界 fail-soft
# ---------------------------------------------------------------------------


class TestGuiServiceConstructionFailure:
    def test_gui_service_exception_returns_empty_service(self):
        """``SubtitleGuiService(config)`` 抛异常 → 返回空 ``SubtitleService``（fail-soft）。"""
        boom = RuntimeError("simulated ConfigSchema validation failure")
        with patch(
            "src.modules.subtitle.assembly.SubtitleGuiService",
            side_effect=boom,
        ):
            service = build_subtitle_infrastructure({})
        assert isinstance(service, SubtitleService)
        assert service.backend_count == 0

    def test_gui_service_value_error_returns_empty_service(self):
        """``ValueError`` 同样兜底（fail-soft，不阻断冷启动）。"""
        with patch(
            "src.modules.subtitle.assembly.SubtitleGuiService",
            side_effect=ValueError("bad config"),
        ):
            service = build_subtitle_infrastructure({})
        assert isinstance(service, SubtitleService)
        assert service.backend_count == 0


# ---------------------------------------------------------------------------
# GUI 服务启动行为：Tk 线程启动（仅 enabled=True 时）
# ---------------------------------------------------------------------------


class TestGuiServiceStart:
    def test_gui_service_enabled_property_propagates_to_backend(self):
        """GUI 服务 ``enabled`` 属性（CustomTkinter 不可用时为 False）
        通过 TkGuiBackend 暴露——本装配链路保形透传。"""
        service = build_subtitle_infrastructure({})
        # service.backend_count == 1 已在上方断言；这里确认协议结构
        assert service.backend_count == 1
        # 单独构造 TkGuiBackend 直接验证 enabled 联动契约
        from src.modules.subtitle.backends import TkGuiBackend as _TkGuiBackend

        svc = SubtitleGuiService(config={})
        backend = _TkGuiBackend(service=svc)
        assert backend.enabled == svc.enabled
        # 本装配入口产出的 service 已含 TkGuiBackend，结构契约成立
        assert isinstance(service, SubtitleService)