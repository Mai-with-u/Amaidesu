"""ConfigService 热重载：回调注册、reload_config 分发（仅 hot 段）、变更批次解析、FileWatcher 启停与端到端热更新"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from watchfiles import Change

from src.modules.config.config_proxy import ConfigProxy
from src.modules.config.file_watcher import FileChange
from src.modules.config.multi_file_loader import generate_default_configs, load_config_dir
from src.modules.config.self_write_guard import consume_self_write
from src.modules.config.service import ConfigService

_ALL_TOML = ("agents.toml", "collectors.toml", "tools.toml", "model.toml", "storage.toml", "infra.toml")


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    cfg = tmp_path / "config"
    generate_default_configs(cfg)
    # 预加载一次让升级钩子的迁移写回先行落定，并消费预热轮的自写标记，
    # 否则残留的抑制窗口会把测试的外部变更误判为管线自写
    load_config_dir(cfg)
    for toml_file in cfg.glob("*.toml"):
        consume_self_write(toml_file)
    return cfg


@pytest.fixture
def service(config_dir: Path) -> ConfigService:
    svc = ConfigService(base_dir=str(config_dir.parent))
    svc.initialize()
    return svc


@pytest.fixture
async def watching(service: ConfigService):
    """启动 FileWatcher（等一会让 watcher 稳定），结束时停止。"""
    await service.start_file_watcher()
    await asyncio.sleep(0.3)
    yield service
    await service.stop_file_watcher()


def _rename_bot(config_dir: Path, new_name: str) -> None:
    path = config_dir / "agents.toml"
    path.write_text(
        path.read_text(encoding="utf-8-sig").replace('bot_name = "麦麦"', f'bot_name = "{new_name}"', 1),
        encoding="utf-8",
    )


def _bot_name(service: ConfigService) -> str | None:
    return service.get_section("agents", {}).get("streamer", {}).get("persona", {}).get("bot_name")


def test_callback_registration_is_fifo_and_unregister_is_safe(service: ConfigService) -> None:
    assert service._reload_callbacks == []
    cb1, cb2 = (lambda scopes: None), (lambda scopes: None)
    service.register_reload_callback(cb1)
    service.register_reload_callback(cb2)
    assert service._reload_callbacks == [cb1, cb2]
    service.unregister_reload_callback(cb1)
    service.unregister_reload_callback(cb1)  # 注销未注册的回调静默
    assert service._reload_callbacks == [cb2]


async def test_reload_rereads_files(service: ConfigService, config_dir: Path) -> None:
    assert _bot_name(service) in (None, "麦麦")
    _rename_bot(config_dir, "新名字")
    await service.reload_config()
    assert _bot_name(service) == "新名字"


async def test_reload_dispatches_hot_scopes_to_every_callback_flavor(service: ConfigService) -> None:
    """回调只对 hot 段分发；同步/无参/异步回调都支持，一个回调抛异常不阻止其余。"""
    calls: list[tuple] = []

    async def async_cb(scopes) -> None:
        calls.append(("async", list(scopes)))
        await asyncio.sleep(0)

    def bad_cb(scopes) -> None:
        raise RuntimeError("intentional test failure")

    service.register_reload_callback(lambda scopes: calls.append(("sync", list(scopes))))
    service.register_reload_callback(lambda: calls.append(("no_arg",)))
    service.register_reload_callback(async_cb)
    service.register_reload_callback(bad_cb)
    service.register_reload_callback(lambda scopes: calls.append(("last", list(scopes))))

    assert await service.reload_config(changed_scopes=["infra"]) is True
    assert calls == [("sync", ["infra"]), ("no_arg",), ("async", ["infra"]), ("last", ["infra"])]


async def test_reload_without_scopes_uses_all_scopes(service: ConfigService) -> None:
    calls: list[list[str]] = []
    service.register_reload_callback(lambda scopes: calls.append(list(scopes)))
    await service.reload_config()
    assert len(calls) == 1 and len(calls[0]) > 0


async def test_handle_reload_maps_filenames_to_scopes(service: ConfigService, config_dir: Path, tmp_path: Path) -> None:
    """restart 段（agents/model）reload 成功但不分发回调；未知文件与空批次是 no-op。"""
    calls: list[list[str]] = []
    service.register_reload_callback(lambda scopes: calls.append(list(scopes)))

    restart_only = [FileChange(change_type=Change.modified, path=config_dir / n) for n in ("agents.toml", "model.toml")]
    await service._handle_reload(restart_only)
    await service._handle_reload([FileChange(change_type=Change.modified, path=tmp_path / "random.txt")])
    await service._handle_reload([])
    assert calls == []


async def test_file_watcher_lifecycle_and_monitored_files(service: ConfigService, config_dir: Path) -> None:
    assert service._file_watcher is None
    await service.stop_file_watcher()  # 未 start 时安全

    await service.start_file_watcher()
    watcher = service._file_watcher
    assert watcher is not None and watcher.running is True
    await service.start_file_watcher()  # 幂等
    assert service._file_watcher is watcher
    watched = {p.resolve() for p in watcher._paths}
    assert all((config_dir / name).resolve() in watched for name in _ALL_TOML)

    await service.stop_file_watcher()
    assert service._file_watcher is None


async def test_modifying_agents_toml_is_visible_through_config_proxy(watching: ConfigService, config_dir: Path) -> None:
    proxy = ConfigProxy(getter=lambda: watching._main_config)
    _rename_bot(config_dir, "热重载后的名字")

    deadline = asyncio.get_running_loop().time() + 10.0  # watcher 防抖约 600ms + reload
    while asyncio.get_running_loop().time() < deadline:
        streamer = proxy.get("agents", {}).get("streamer", {})
        if isinstance(streamer, dict) and streamer.get("persona", {}).get("bot_name") == "热重载后的名字":
            return
        await asyncio.sleep(0.2)
    pytest.fail("ConfigProxy 未反映热重载新值")


async def test_hot_scope_change_fires_callback(watching: ConfigService, config_dir: Path) -> None:
    """hot 段（infra）有效变更才回调；restart 段变更仅记"待重启"日志。"""
    received: list[list[str]] = []
    fired = asyncio.Event()

    def cb(scopes) -> None:
        received.append(list(scopes))
        fired.set()

    watching.register_reload_callback(cb)
    infra = config_dir / "infra.toml"
    infra.write_text(infra.read_text(encoding="utf-8-sig") + "\n# touched\n", encoding="utf-8-sig")
    await asyncio.wait_for(fired.wait(), timeout=10.0)
    assert received == [["infra"]]


async def test_invalid_restart_scope_change_keeps_old_config(watching: ConfigService, config_dir: Path) -> None:
    """写入缺 [meta].version 的非法内容：校验硬错被 reload 捕获，内存配置保持旧值继续运行，回调不触发。"""
    fired = asyncio.Event()
    watching.register_reload_callback(lambda scopes: fired.set())
    old_agents = watching.get_section("agents", {})

    (config_dir / "agents.toml").write_text("# broken\n", encoding="utf-8")
    await asyncio.sleep(1.5)
    assert not fired.is_set() and watching.get_section("agents", {}) == old_agents
