"""热重启续播前置挂接与启动期早到事件场次归属回归测试。

覆盖两类场景：
1. 热重启交接（携带 RESTART_RESUME_SESSION_ENV）：场次恢复必须在流程单加载（发出
   rundown.changed）及采集器/Agent 启动拉取游戏事件之前完成挂接，确保启动期首条事件即
   归属恢复后的活跃场次，绝不错贴前一场已结束场次号；且订阅方就绪后收到 live.started(resumed=True)。
2. 非热重启冷启动（存在待决残留但无交接信号）：启动期早到事件不得回退错贴前一场已结束场次号，
   保持未归属（live_session_id=0），场次留在待决列表中等待人工拍板。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List

import pytest

import main as app_main
from src.modules.agents.base import BaseAgent
from src.modules.collectors.base import BaseCollector
from src.modules.dashboard.server import RESTART_RESUME_SESSION_ENV
from src.modules.events.names import CoreEvents
from src.modules.events.payloads.game import GamePayload
from src.modules.events.payloads.live import LiveStartedPayload
from src.modules.events.payloads.rundown import RundownChangedPayload
from src.modules.storage.database import SQLiteDatabase


class _StubConfigService:
    """只暴露组合根所需公开读取接口的配置服务替身。"""

    def __init__(self, config: Dict[str, Any], collectors_section: Dict[str, Any] | None = None) -> None:
        self.main_config = config
        self._collectors_section = collectors_section or {}

    def get_section(self, name: str, default: Any = None) -> Any:
        return self.main_config.get(name, default)

    def get_file_section(self, name: str) -> Dict[str, Any]:
        if name == "collectors":
            return dict(self._collectors_section)
        return {}


def _minimal_config(db_path: Path) -> Dict[str, Any]:
    return {
        "general": {"platform_id": "test"},
        "context": {},
        "events": {"history_size": 100},
        "dashboard": {"enabled": False},
        "logging": {"level": "WARNING"},
        "subtitle": {"enabled": False},
        "diagnostics": {"enabled": False},
        "interceptors": {},
        "simulator": {"enabled": False},
        "tools": {},
        "agents": {"enabled": ["streamer"]},
        "memory": {},
        "storage": {},
        "sqlite": {"db_path": str(db_path)},
        "background": {},
        "llm_providers": [],
        "llm": {"client_type": "", "model": ""},
        "llm_fast": {"client_type": "", "model": ""},
    }


async def _seed_ended_and_dangling_sessions(db_path: Path) -> tuple[int, int]:
    """在库中预置前一场已结束场次（如 79）与当前未结账残留场次（如 80）。"""
    db = SQLiteDatabase(db_path)
    await db.initialize()
    try:
        ended_pk = await db.sessions.insert_live_session(started_at_ms=1_000, source="manual", title="上一场")
        await db.chat.insert_live_chat(
            live_session_id=ended_pk,
            timestamp_ms=1_500,
            sender_role="viewer",
            content="旧弹幕",
            message_type="danmaku",
        )
        await db.sessions.close_live_session(live_session_id=ended_pk, ended_at_ms=2_000)

        dangling_pk = await db.sessions.insert_live_session(started_at_ms=3_000, source="manual", title="当前场")
        await db.chat.insert_live_chat(
            live_session_id=dangling_pk,
            timestamp_ms=3_500,
            sender_role="viewer",
            content="断前弹幕",
            message_type="danmaku",
        )
        return ended_pk, dangling_pk
    finally:
        await db.close()


async def test_hot_restart_preattaches_session_before_rundown_and_collector_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """热重启前置挂接：启动期流程单加载与采集器首条游戏事件直接归属续播场次，不贴旧场次号。"""
    db_path = tmp_path / "restart_resume.db"
    ended_pk, dangling_pk = await _seed_ended_and_dangling_sessions(db_path)

    captured_rundown: List[RundownChangedPayload] = []
    captured_game: List[GamePayload] = []
    captured_live_started: List[LiveStartedPayload] = []
    startup_order: List[str] = []

    class _ProbeAgent(BaseAgent):
        name = "streamer"
        description = "启动序探针 Agent"

        def __init__(self, *, event_bus: Any) -> None:
            super().__init__(event_bus=event_bus)
            self._bus = event_bus

        def list_tools(self) -> List[str]:
            return []

        async def _on_start(self) -> None:
            startup_order.append("agent.start")
            self._bus.on(CoreEvents.RUNDOWN_CHANGED, self._on_rundown, model_class=RundownChangedPayload)
            self._bus.on(CoreEvents.GAME_MILESTONE, self._on_game, model_class=GamePayload)
            self._bus.on(CoreEvents.LIVE_STARTED, self._on_live_started, model_class=LiveStartedPayload)
            # 模拟 StreamerAgent._on_start() 中流程单加载发出的首个 rundown.changed(load)
            await self._bus.emit(
                CoreEvents.RUNDOWN_CHANGED,
                RundownChangedPayload(action="load", rundown_id="default_plan", by="system"),
                source="ProbeAgent",
            )

        async def _on_stop(self) -> None:
            return None

        async def _on_rundown(self, _name: str, payload: RundownChangedPayload, _src: str) -> None:
            captured_rundown.append(payload)

        async def _on_game(self, _name: str, payload: GamePayload, _src: str) -> None:
            captured_game.append(payload)

        async def _on_live_started(self, _name: str, payload: LiveStartedPayload, _src: str) -> None:
            startup_order.append("agent.live_started")
            captured_live_started.append(payload)

    class _ProbeCollector(BaseCollector):
        name = "probe_collector"

        def __init__(self, *, event_bus: Any) -> None:
            super().__init__(event_bus=event_bus)
            self._bus = event_bus

        async def _on_start(self) -> None:
            startup_order.append("collector.start")
            # 模拟采集器启动后立即拉入的早到游戏事件
            await self._bus.emit(
                CoreEvents.GAME_MILESTONE,
                GamePayload(game="minecraft", event_type="milestone", message="进入存档"),
                source="ProbeCollector",
            )

        async def _on_stop(self) -> None:
            return None

    async def _noop(self: Any, *args: Any, **kwargs: Any) -> None:
        del self, args, kwargs

    monkeypatch.setattr("src.modules.llm.engine.LLMManager.setup", _noop)
    monkeypatch.setattr("src.modules.llm.engine.LLMManager.cleanup", _noop)
    monkeypatch.setattr(
        "main.instantiate_agent",
        lambda name, _cfg, deps=None: _ProbeAgent(event_bus=deps.event_bus) if name == "streamer" else None,
    )
    monkeypatch.setattr(
        "main.instantiate_collector",
        lambda name, _cfg, event_bus=None, llm_manager=None: (
            _ProbeCollector(event_bus=event_bus) if name == "probe_collector" else None
        ),
    )
    monkeypatch.setenv(RESTART_RESUME_SESSION_ENV, str(dangling_pk))

    cfg = _minimal_config(db_path)
    components = await app_main.create_app_components(
        config=cfg,
        config_service=_StubConfigService(cfg, collectors_section={"enabled": ["probe_collector"]}),  # type: ignore[arg-type]
        dev_webui=False,
        log_streamer_persist=False,
        startup_effects=False,
        acquire_instance_lock=False,
    )
    try:
        await components.event_bus.wait_until_drained()

        assert RESTART_RESUME_SESSION_ENV not in os.environ
        assert components.session_manager.active_pk == dangling_pk
        assert components.session_manager.pending_residual_pks == []

        assert len(captured_rundown) == 1
        assert captured_rundown[0].live_session_id == dangling_pk
        assert captured_rundown[0].live_session_id != ended_pk

        assert len(captured_game) == 1
        assert captured_game[0].live_session_id == dangling_pk
        assert captured_game[0].live_session_id != ended_pk

        assert len(captured_live_started) == 1
        assert captured_live_started[0].live_session_id == dangling_pk
        assert captured_live_started[0].resumed is True

        # Agent 订阅先于采集器启动就位
        assert startup_order.index("agent.start") < startup_order.index("collector.start")
    finally:
        await app_main.run_shutdown(components, settle_active_session=False)


async def test_cold_startup_with_pending_residual_never_stamps_previous_ended_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """崩溃后冷启动无热重启交接信号：早到事件保持 live_session_id=0，绝不错贴前一场已结束场次。"""
    db_path = tmp_path / "cold_start_residual.db"
    ended_pk, dangling_pk = await _seed_ended_and_dangling_sessions(db_path)

    captured_rundown: List[RundownChangedPayload] = []
    captured_game: List[GamePayload] = []

    class _ProbeAgent(BaseAgent):
        name = "streamer"
        description = "冷启动探针 Agent"

        def __init__(self, *, event_bus: Any) -> None:
            super().__init__(event_bus=event_bus)
            self._bus = event_bus

        def list_tools(self) -> List[str]:
            return []

        async def _on_start(self) -> None:
            self._bus.on(CoreEvents.RUNDOWN_CHANGED, self._on_rundown, model_class=RundownChangedPayload)
            self._bus.on(CoreEvents.GAME_MILESTONE, self._on_game, model_class=GamePayload)
            await self._bus.emit(
                CoreEvents.RUNDOWN_CHANGED,
                RundownChangedPayload(action="load", rundown_id="default_plan", by="system"),
                source="ProbeAgent",
            )

        async def _on_stop(self) -> None:
            return None

        async def _on_rundown(self, _name: str, payload: RundownChangedPayload, _src: str) -> None:
            captured_rundown.append(payload)

        async def _on_game(self, _name: str, payload: GamePayload, _src: str) -> None:
            captured_game.append(payload)

    class _ProbeCollector(BaseCollector):
        name = "probe_collector"

        def __init__(self, *, event_bus: Any) -> None:
            super().__init__(event_bus=event_bus)
            self._bus = event_bus

        async def _on_start(self) -> None:
            await self._bus.emit(
                CoreEvents.GAME_MILESTONE,
                GamePayload(game="minecraft", event_type="milestone", message="进入存档"),
                source="ProbeCollector",
            )

        async def _on_stop(self) -> None:
            return None

    async def _noop(self: Any, *args: Any, **kwargs: Any) -> None:
        del self, args, kwargs

    monkeypatch.setattr("src.modules.llm.engine.LLMManager.setup", _noop)
    monkeypatch.setattr("src.modules.llm.engine.LLMManager.cleanup", _noop)
    monkeypatch.setattr(
        "main.instantiate_agent",
        lambda name, _cfg, deps=None: _ProbeAgent(event_bus=deps.event_bus) if name == "streamer" else None,
    )
    monkeypatch.setattr(
        "main.instantiate_collector",
        lambda name, _cfg, event_bus=None, llm_manager=None: (
            _ProbeCollector(event_bus=event_bus) if name == "probe_collector" else None
        ),
    )
    monkeypatch.delenv(RESTART_RESUME_SESSION_ENV, raising=False)

    cfg = _minimal_config(db_path)
    components = await app_main.create_app_components(
        config=cfg,
        config_service=_StubConfigService(cfg, collectors_section={"enabled": ["probe_collector"]}),  # type: ignore[arg-type]
        dev_webui=False,
        log_streamer_persist=False,
        startup_effects=False,
        acquire_instance_lock=False,
    )
    try:
        await components.event_bus.wait_until_drained()

        assert components.session_manager.active_pk is None
        assert components.session_manager.pending_residual_pks == [dangling_pk]
        assert len(captured_rundown) == 1
        assert captured_rundown[0].live_session_id == 0
        assert captured_rundown[0].live_session_id != ended_pk
        assert len(captured_game) == 1
        assert captured_game[0].live_session_id == 0
        assert captured_game[0].live_session_id != ended_pk
    finally:
        await app_main.run_shutdown(components, settle_active_session=False)
