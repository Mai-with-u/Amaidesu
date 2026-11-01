"""Simulator API 测试：状态 / 运行期装配 / 回放日期 / 人设与礼物 CRUD。

注：使用真实 SimulatorService（tmp SQLiteDatabase + setup），不触发真实 LLM
（mode=generate 且无 LLMManager 注入时 setup 只落数据平面，CRUD 均可用）。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

import pytest
from fastapi.testclient import TestClient

from src.modules.dashboard.dependencies import get_dashboard_server
from src.modules.events.event_bus import EventBus
from src.modules.simulator import SimulatorService
from src.modules.simulator.seed_data import seed_simulator_data
from src.modules.storage.database import SQLiteDatabase
from src.modules.types.session_kind import SESSION_KIND_REHEARSAL
from tests.modules.simulator.helpers import FakeLLMService


class _FakeConfigService:
    """仅暴露 main_config 的最小 ConfigService 替身；get_section 供 dashboard API 读 [simulator] 开关。"""

    def __init__(self, main_config: Dict[str, Any] | None = None) -> None:
        self.main_config: Dict[str, Any] = main_config or {"simulator": {"enabled": False}}

    def get_section(self, section: str, default: Any = None) -> Any:
        return self.main_config.get(section, default)


@pytest.fixture
async def client(mount, store: SQLiteDatabase) -> TestClient:
    await seed_simulator_data(store.sim)
    service = SimulatorService(event_bus=EventBus(), sim_repo=store.sim, chat_repo=store.chat)
    await service.setup(_FakeConfigService(), auto_start=False)  # type: ignore[arg-type]
    client = mount()
    set_dashboard_server_simulator(service)
    return client


def set_dashboard_server_simulator(service: SimulatorService) -> None:
    """simulator_service 是装配后挂到 server 上的属性，不走构造器。"""
    get_dashboard_server().simulator_service = service  # type: ignore[attr-defined]


# status


def test_status_reports_mode_and_availability(client: TestClient) -> None:
    body = client.get("/api/v1/simulator/status").json()
    assert body["is_available"] is True
    assert body["is_running"] is False
    assert body["mode"] == "off"
    assert body["replay_progress"] is None


def test_status_without_service_returns_disabled_empty_state(mount) -> None:
    """未启用零装配（simulator_service=None，生产默认形态）返回 200 空态而非 500。"""
    client = mount(config_service=_FakeConfigService())
    body = client.get("/api/v1/simulator/status").json()
    assert body["enabled"] is False
    assert body["is_available"] is False
    assert body["is_running"] is False
    assert body["mode"] == "off"
    assert body["replay_progress"] is None
    assert body["last_error"] is None
    assert "未启用" in body["message"]


def test_status_enabled_but_service_not_injected(mount) -> None:
    """配置启用但服务未注入（存储仓储/LLMManager 缺失或 --dry 模式）→ 200 且消息点名装配缺失。"""
    client = mount(config_service=_FakeConfigService({"simulator": {"enabled": True}}))
    body = client.get("/api/v1/simulator/status").json()
    assert body["enabled"] is True
    assert body["is_available"] is False
    assert body["is_running"] is False
    assert "未注入" in body["message"]


def _make_factory(
    store: SQLiteDatabase, config_service: Any, llm: Optional[FakeLLMService] = None, session_manager: Any = None
) -> Callable[[], Any]:
    """造启用端点用的运行期装配工厂（形状镜像 main.py 的组合根闭包）。"""

    async def factory() -> SimulatorService:
        service = SimulatorService(
            event_bus=EventBus(),
            sim_repo=store.sim,
            chat_repo=store.chat,
            services_by_type={type(llm): llm} if llm is not None else {},
            session_manager=session_manager,
        )
        await service.setup(config_service, auto_start=False)
        return service

    return factory


async def test_enable_assembles_service_and_persists_config(mount, initialized_config, store, session_manager) -> None:
    """启用 = 运行期装配 + enabled 写盘（不自动生成）；生成由 /start 显式发起；重复启用幂等。"""
    factory = _make_factory(store, initialized_config, llm=FakeLLMService(), session_manager=session_manager)
    client = mount(config_service=initialized_config, simulator_factory=factory)
    await session_manager.open_session(kind=SESSION_KIND_REHEARSAL, title="彩排")
    try:
        body = client.post("/api/v1/simulator/enable").json()
        assert body["success"] is True
        assert body["status"]["enabled"] is True
        assert body["status"]["is_available"] is True
        assert body["status"]["is_running"] is False  # 启用与生成分离：装配待命
        assert initialized_config.get_section("simulator")["enabled"] is True
        # 配置摘要的 enabled 以配置文件现值为准（服务 setup 期快照仍是装配前的旧值）
        assert body["status"]["config"]["enabled"] is True

        # 生成循环由启动按钮显式发起
        started = client.post("/api/v1/simulator/start").json()
        assert started["success"] is True and started["is_running"] is True

        # 幂等：已装配时再次启用仍成功，且不打断运行
        again = client.post("/api/v1/simulator/enable").json()
        assert again["success"] is True
        assert again["status"]["is_running"] is True
    finally:
        client.post("/api/v1/simulator/disable")


async def test_enable_without_session_then_start_runs_debug(mount, initialized_config, store, session_manager) -> None:
    """采集器形态：无场次时启用后照样能启动（调试注入，不落库），不要求先开场次。"""
    factory = _make_factory(store, initialized_config, llm=FakeLLMService(), session_manager=session_manager)
    client = mount(config_service=initialized_config, simulator_factory=factory)
    try:
        assert client.post("/api/v1/simulator/enable").json()["success"] is True
        assert session_manager.active_pk is None
        started = client.post("/api/v1/simulator/start").json()
        assert started["success"] is True and started["is_running"] is True
        assert session_manager.active_pk is None  # 模拟器不代开场次
    finally:
        client.post("/api/v1/simulator/disable")


async def test_enable_without_llm_assembles_but_start_rejected(mount, initialized_config, store) -> None:
    """无 LLM 时启用装配成功；生成循环被拒，拒绝原因由 /start 返回。"""
    factory = _make_factory(store, initialized_config)
    client = mount(config_service=initialized_config, simulator_factory=factory)
    body = client.post("/api/v1/simulator/enable").json()
    assert body["success"] is True
    assert body["status"]["is_available"] is True
    assert body["status"]["is_running"] is False

    started = client.post("/api/v1/simulator/start").json()
    assert started["success"] is False
    assert "LLM" in started["message"]


def test_enable_without_factory_fails_with_guidance(mount) -> None:
    """工厂未注入（极简启动/测试装配）→ success=false 带重启指引，状态不变。"""
    client = mount(config_service=_FakeConfigService())
    body = client.post("/api/v1/simulator/enable").json()
    assert body["success"] is False
    assert "工厂未注入" in body["message"]
    assert body["status"]["is_available"] is False


async def test_disable_stops_service_and_persists_config(mount, initialized_config, store, session_manager) -> None:
    """停用 = 停循环 + 拆卸服务 + enabled 写回 false；人手开的场次不替人收口；未装配时幂等。"""
    factory = _make_factory(store, initialized_config, llm=FakeLLMService(), session_manager=session_manager)
    client = mount(config_service=initialized_config, simulator_factory=factory)
    rehearsal_pk = await session_manager.open_session(kind=SESSION_KIND_REHEARSAL, title="彩排")
    assert client.post("/api/v1/simulator/enable").json()["success"] is True
    assert client.post("/api/v1/simulator/start").json()["is_running"] is True
    body = client.post("/api/v1/simulator/disable").json()
    assert body["success"] is True
    assert body["status"]["is_available"] is False
    assert body["status"]["is_running"] is False
    assert body["status"]["enabled"] is False
    assert initialized_config.get_section("simulator")["enabled"] is False
    assert session_manager.active_pk == rehearsal_pk  # 场次是用户开的，停用不代关

    # 幂等：未装配时再停用仍成功
    again = client.post("/api/v1/simulator/disable").json()
    assert again["success"] is True


# replay dates


async def test_replay_dates_from_live_chat_only(client, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """空表 → 空列表；有弹幕时按时间正序返回日期并带各日条数，主播发言行不计入。

    端点经默认 store 工厂取连接，打桩到临时库（默认库在本机可能存有历史录制）。
    """
    replay_store = SQLiteDatabase(tmp_path / "replay-dates.db")
    await replay_store.initialize()
    monkeypatch.setattr("src.modules.dashboard.api.simulator.get_default_db", lambda: replay_store)
    try:
        assert client.get("/api/v1/simulator/replay/dates").json() == {"dates": []}

        for ts, role, mtype in (
            (1_790_000_000_000, "viewer", "danmaku"),
            (1_750_000_000_000, "viewer", "danmaku"),
            (1_760_000_000_000, "assistant", "speech"),
        ):
            await replay_store.chat.insert_live_chat(
                live_session_id=1, timestamp_ms=ts, sender_role=role, content="x", message_type=mtype
            )
        dates = client.get("/api/v1/simulator/replay/dates").json()["dates"]
        assert [d["date"] for d in dates] == sorted(d["date"] for d in dates)
        assert [d["danmaku_count"] for d in dates] == [1, 1]
    finally:
        await replay_store.close()


# personas CRUD


def test_persona_create_list_update_delete(client: TestClient) -> None:
    # 列表：内置种子
    body = client.get("/api/v1/simulator/personas").json()
    assert body["is_available"] is True
    seeded_ids = {p["user_id"] for p in body["personas"]}
    assert "sim_veteran_01" in seeded_ids

    # 新增
    resp = client.post(
        "/api/v1/simulator/personas",
        json={
            "user_nickname": "API新建观众",
            "role": "fan",
            "personality": "热情",
            "speaking_style": "活泼",
        },
    )
    assert resp.status_code == 200
    created = resp.json()
    assert created["success"] is True
    user_id = created["persona"]["user_id"]

    # 更新
    resp = client.patch(
        f"/api/v1/simulator/personas/{user_id}",
        json={"user_nickname": "API改名观众", "speaking_style": "自然"},
    )
    assert resp.status_code == 200
    assert resp.json()["success"] is True

    listed = client.get("/api/v1/simulator/personas").json()["personas"]
    target = next(p for p in listed if p["user_id"] == user_id)
    assert target["user_nickname"] == "API改名观众"
    assert target["speaking_style"] == "自然"

    # 删除
    resp = client.delete(f"/api/v1/simulator/personas/{user_id}")
    assert resp.json()["success"] is True
    listed = client.get("/api/v1/simulator/personas").json()["personas"]
    assert all(p["user_id"] != user_id for p in listed)


def test_persona_create_duplicate_nickname_rejected(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/simulator/personas",
        json={
            "user_nickname": "三楼老王",
            "role": "fan",
            "personality": "重复昵称",
            "speaking_style": "随意",
        },
    )
    body = resp.json()
    assert body["success"] is False
    assert "已存在" in body["message"]


def test_persona_update_nonexistent(client: TestClient) -> None:
    resp = client.patch("/api/v1/simulator/personas/ghost", json={"personality": "x"})
    assert resp.json()["success"] is False


# gifts CRUD


def test_gift_create_list_update_delete(client: TestClient) -> None:
    body = client.get("/api/v1/simulator/gifts").json()
    assert body["is_available"] is True
    assert any(g["gift_id"] == "small_heart" for g in body["gifts"])

    resp = client.post(
        "/api/v1/simulator/gifts",
        json={"gift_id": "api_gift", "gift_name": "API礼物", "category": "normal", "weight": 3},
    )
    assert resp.json()["success"] is True

    resp = client.patch("/api/v1/simulator/gifts/api_gift", json={"weight": 9})
    assert resp.json()["success"] is True
    listed = client.get("/api/v1/simulator/gifts").json()["gifts"]
    assert next(g for g in listed if g["gift_id"] == "api_gift")["weight"] == 9

    resp = client.delete("/api/v1/simulator/gifts/api_gift")
    assert resp.json()["success"] is True
    assert all(g["gift_id"] != "api_gift" for g in client.get("/api/v1/simulator/gifts").json()["gifts"])


def test_gift_create_duplicate_rejected(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/simulator/gifts",
        json={"gift_id": "small_heart", "gift_name": "重复", "category": "normal"},
    )
    body = resp.json()
    assert body["success"] is False
    assert "已存在" in body["message"]


def test_persona_partial_update_preserves_other_fields(client: TestClient) -> None:
    """修改发言风格只改变对应字段，人设 API 不再暴露上下文窗口。"""
    response = client.post(
        "/api/v1/simulator/personas",
        json={
            "user_nickname": "编辑观众",
            "role": "fan",
            "personality": "平静",
            "speaking_style": "简洁",
        },
    )
    assert response.json()["success"] is True
    user_id = response.json()["persona"]["user_id"]
    updated = client.patch(f"/api/v1/simulator/personas/{user_id}", json={"speaking_style": "自然"})
    assert updated.json()["success"] is True
    target = next(p for p in client.get("/api/v1/simulator/personas").json()["personas"] if p["user_id"] == user_id)
    assert target["user_nickname"] == "编辑观众" and target["speaking_style"] == "自然"
    assert "context_window_size" not in target
