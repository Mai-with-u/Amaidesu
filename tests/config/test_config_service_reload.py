"""ConfigService 配置重载：内存视图刷新、变更批次解析、FileWatcher 启停与端到端热更新"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from watchfiles import Change

from src.modules.config.file_watcher import FileChange
from src.modules.config.multi_file_loader import generate_default_configs, load_config_dir
from src.modules.config.self_write_guard import consume_self_write
from src.modules.config.service import SCOPE_RELOAD_POLICY, ConfigService
from tests.config.helpers import wait_until

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
async def watching(service: ConfigService, config_dir: Path):
    """启动 FileWatcher，用探针写确认监视已生效，结束时停止。

    watchfiles 建立监视句柄前发生的写入不会产生事件；反复写同一内容直到
    首批变更被观察到（内容不变，重载是无漂移的 no-op），替代固定 sleep 猜启动时长。
    """
    await service.start_file_watcher()
    watcher = service._file_watcher
    assert watcher is not None
    probe = config_dir / "infra.toml"
    original = probe.read_text(encoding="utf-8-sig")
    deadline = asyncio.get_running_loop().time() + 10.0
    while watcher.stats.batches_seen == 0:
        if asyncio.get_running_loop().time() >= deadline:
            pytest.fail("探针写未被 FileWatcher 观察：监视未建立")
        probe.write_text(original, encoding="utf-8-sig")
        await asyncio.sleep(0.1)
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


def test_no_scope_declares_hot_anymore() -> None:
    """任何 scope 都不得声明 hot：配置目录没有热重载回调通道，改动一律重启生效；
    声明 hot 的效果是吞掉"需重启"警告，让改动看似已生效实则未加载（静默假生效）。"""
    assert "hot" not in set(SCOPE_RELOAD_POLICY.values())


async def test_reload_rereads_files(service: ConfigService, config_dir: Path) -> None:
    assert _bot_name(service) in (None, "麦麦")
    _rename_bot(config_dir, "新名字")
    await service.reload_config()
    assert _bot_name(service) == "新名字"


async def test_handle_reload_rereads_changed_files(service: ConfigService, config_dir: Path, tmp_path: Path) -> None:
    """文件名→scope 映射：变更文件被重载进内存视图；未知文件与空批次是 no-op。"""
    _rename_bot(config_dir, "handle_reload 后的名字")
    restart_only = [FileChange(change_type=Change.modified, path=config_dir / n) for n in ("agents.toml", "model.toml")]
    await service._handle_reload(restart_only)
    assert _bot_name(service) == "handle_reload 后的名字"
    await service._handle_reload([FileChange(change_type=Change.modified, path=tmp_path / "random.txt")])
    await service._handle_reload([])


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


async def test_modifying_agents_toml_becomes_visible_after_reload(watching: ConfigService, config_dir: Path) -> None:
    _rename_bot(config_dir, "热重载后的名字")

    deadline = asyncio.get_running_loop().time() + 10.0  # watcher 防抖约 600ms + reload
    while asyncio.get_running_loop().time() < deadline:
        streamer = watching.get_section("agents", {}).get("streamer", {})
        if isinstance(streamer, dict) and streamer.get("persona", {}).get("bot_name") == "热重载后的名字":
            return
        await asyncio.sleep(0.2)
    pytest.fail("热重载后 service 读取的仍是旧值")


async def test_invalid_restart_scope_change_keeps_old_config(watching: ConfigService, config_dir: Path) -> None:
    """写入缺 [meta].version 的非法内容：校验硬错被 reload 捕获，内存配置保持旧值继续运行。"""
    old_agents = watching.get_section("agents", {})
    watcher = watching._file_watcher
    assert watcher is not None
    seen_before = watcher.stats.batches_seen

    (config_dir / "agents.toml").write_text("# broken\n", encoding="utf-8")
    # 轮询到该批次已被分发（校验失败不影响批次计数），再断言配置未变，
    # "保持旧值"从猜时长变成有确定性前提
    await wait_until(lambda: watcher.stats.batches_seen >= seen_before + 1, what="非法变更批次被分发", timeout_s=10.0)
    assert watching.get_section("agents", {}) == old_agents
