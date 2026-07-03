"""ConfigService 热重载测试套件 (Task 7)

覆盖 ConfigService 的热重载 API 契约：

1. **register_reload_callback()** — 回调注册
2. **unregister_reload_callback()** — 回调注销
3. **_handle_reload()** — 内部处理 FileChange 批次
4. **reload_config()** — 重载配置并触发回调
5. **start_file_watcher()** — 启动 FileWatcher
6. **stop_file_watcher()** — 停止 FileWatcher
7. **ConfigProxy 集成** — 通过代理在热重载后看到新值

参考实现：MaiBot-v1.0.0/src/config/config.py (ConfigManager 类)
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Sequence

import pytest

from src.modules.config.file_watcher import FileChange
from watchfiles import Change


# ===========================================================================
# Fixtures
# ===========================================================================


def _build_core_toml() -> str:
    """生成最小可加载的 core.toml 内容"""
    return (
        "[meta]\n"
        'type = "meta"\n'
        'version = "0.4.0"\n'
        "\n"
        "[persona]\n"
        'type = "persona"\n'
        'bot_name = "麦麦"\n'
    )


def _build_model_toml() -> str:
    """生成最小可加载的 model.toml 内容"""
    return (
        "[llm]\n"
        'client = "openai"\n'
        'model = "gpt-4"\n'
        'api_key = ""\n'
    )


@pytest.fixture
def config_dir_with_toml(tmp_path: Path) -> Path:
    """创建一个带有 5 个默认 TOML 配置文件的临时目录。

    配置文件名与 Amaidesu 多文件加载约定一致：
    core.toml / model.toml / input.toml / decision.toml / output.toml
    """
    cfg = tmp_path / "config"
    cfg.mkdir()

    # 最小可加载的 core.toml / model.toml
    (cfg / "core.toml").write_text(_build_core_toml(), encoding="utf-8")
    (cfg / "model.toml").write_text(_build_model_toml(), encoding="utf-8")

    # 其余 3 个 phase 文件空字典即可
    for name in ("input", "decision", "output"):
        (cfg / f"{name}.toml").write_text("", encoding="utf-8")

    return cfg


@pytest.fixture
def initialized_service(config_dir_with_toml: Path):
    """返回一个已 initialize() 的 ConfigService 实例。

    base_dir 是 config_dir_with_toml 的父目录（Amaidesu 约定）。
    """
    from src.modules.config.service import ConfigService

    service = ConfigService(base_dir=str(config_dir_with_toml.parent))
    service.initialize()
    return service


# ===========================================================================
# 1. register_reload_callback / unregister_reload_callback
# ===========================================================================


class TestReloadCallbackRegistration:
    """回调注册/注销的存储契约"""

    def test_register_stores_callback(self, initialized_service):
        """register_reload_callback 必须把回调存到内部列表"""
        from src.modules.config.service import ConfigService

        # 内部状态: 应该是空列表（或包含 _reload_callbacks 字段的某个容器）
        assert hasattr(initialized_service, "_reload_callbacks")
        assert isinstance(initialized_service._reload_callbacks, list)
        assert len(initialized_service._reload_callbacks) == 0

        def cb(scopes: Sequence[str]) -> None:
            pass

        initialized_service.register_reload_callback(cb)
        assert cb in initialized_service._reload_callbacks
        assert len(initialized_service._reload_callbacks) == 1

    def test_register_multiple_callbacks(self, initialized_service):
        """多次注册回调按顺序保留 (FIFO)"""
        calls = []

        def cb1(scopes):
            calls.append(("cb1", scopes))

        def cb2(scopes):
            calls.append(("cb2", scopes))

        initialized_service.register_reload_callback(cb1)
        initialized_service.register_reload_callback(cb2)

        assert initialized_service._reload_callbacks == [cb1, cb2]

    def test_unregister_removes_callback(self, initialized_service):
        """unregister 必须把回调从列表里移除"""
        cb1 = lambda scopes: None
        cb2 = lambda scopes: None

        initialized_service.register_reload_callback(cb1)
        initialized_service.register_reload_callback(cb2)
        assert len(initialized_service._reload_callbacks) == 2

        initialized_service.unregister_reload_callback(cb1)
        assert initialized_service._reload_callbacks == [cb2]

    def test_unregister_missing_callback_is_noop(self, initialized_service):
        """注销未注册的回调必须静默 (不抛异常)"""
        cb = lambda scopes: None
        # 不应抛 ValueError
        initialized_service.unregister_reload_callback(cb)
        assert initialized_service._reload_callbacks == []


# ===========================================================================
# 2. reload_config + callback dispatch
# ===========================================================================


class TestReloadConfig:
    """reload_config 必须重载配置并调用所有回调"""

    @pytest.mark.asyncio
    async def test_reload_re_reads_files(self, initialized_service, config_dir_with_toml):
        """reload_config 后 _main_config 必须反映磁盘上最新的 TOML 内容"""
        new_core_toml = (
            "[persona]\n"
            'type = "persona"\n'
            'bot_name = "新名字"\n'
        )
        (config_dir_with_toml / "core.toml").write_text(new_core_toml, encoding="utf-8")

        # 旧值应仍是默认值
        assert initialized_service.get_section("persona", {}).get("bot_name") in (None, "麦麦")

        await initialized_service.reload_config()

        # reload 后必须看到新值
        assert initialized_service.get_section("persona", {}).get("bot_name") == "新名字"

    @pytest.mark.asyncio
    async def test_reload_invokes_callbacks_with_scopes(self, initialized_service):
        """reload_config 必须用 changed_scopes 调用所有已注册回调"""
        calls = []

        def cb1(scopes):
            calls.append(("cb1", list(scopes)))

        def cb2(scopes):
            calls.append(("cb2", list(scopes)))

        initialized_service.register_reload_callback(cb1)
        initialized_service.register_reload_callback(cb2)

        await initialized_service.reload_config(changed_scopes=["core", "model"])

        assert ("cb1", ["core", "model"]) in calls
        assert ("cb2", ["core", "model"]) in calls

    @pytest.mark.asyncio
    async def test_reload_supports_callback_without_scopes(self, initialized_service):
        """reload_config 也必须支持无参回调（不接收 scopes）"""
        calls = []

        def no_arg_cb():
            calls.append("no_arg")

        initialized_service.register_reload_callback(no_arg_cb)

        await initialized_service.reload_config(changed_scopes=["core"])

        assert "no_arg" in calls

    @pytest.mark.asyncio
    async def test_reload_supports_async_callbacks(self, initialized_service):
        """reload_config 必须支持 async 回调"""
        calls = []

        async def async_cb(scopes):
            calls.append(("async", list(scopes)))
            await asyncio.sleep(0)

        initialized_service.register_reload_callback(async_cb)

        await initialized_service.reload_config(changed_scopes=["model"])

        assert ("async", ["model"]) in calls

    @pytest.mark.asyncio
    async def test_reload_callback_exception_does_not_break_others(self, initialized_service):
        """一个回调抛异常不能阻止其他回调执行"""
        calls = []

        def good_cb_1(scopes):
            calls.append("good1")

        def bad_cb(scopes):
            raise RuntimeError("intentional test failure")

        def good_cb_2(scopes):
            calls.append("good2")

        initialized_service.register_reload_callback(good_cb_1)
        initialized_service.register_reload_callback(bad_cb)
        initialized_service.register_reload_callback(good_cb_2)

        # bad_cb 抛异常不应让 reload_config 失败
        result = await initialized_service.reload_config(changed_scopes=["core"])
        assert result is True

        # good1 和 good2 必须都被调用
        assert "good1" in calls
        assert "good2" in calls

    @pytest.mark.asyncio
    async def test_reload_default_scopes_when_none(self, initialized_service):
        """reload_config() 不传 scopes 时应使用全部 scope"""
        calls = []

        def cb(scopes):
            calls.append(list(scopes))

        initialized_service.register_reload_callback(cb)

        await initialized_service.reload_config()

        assert len(calls) == 1
        assert len(calls[0]) > 0  # 应至少有一个 scope


# ===========================================================================
# 3. _handle_reload — FileChange 批次处理
# ===========================================================================


class TestHandleReload:
    """_handle_reload 必须把 FileChange 列表转换为 scopes 并触发 reload"""

    @pytest.mark.asyncio
    async def test_handle_reload_resolves_scopes_from_filenames(
        self, initialized_service, config_dir_with_toml
    ):
        """FileChange 列表的 path.name 应映射到 scope 名 (core/model/...)"""
        calls = []

        def cb(scopes):
            calls.append(list(scopes))

        initialized_service.register_reload_callback(cb)

        # 模拟 core.toml 和 model.toml 一起被修改
        changes = [
            FileChange(change_type=Change.modified, path=config_dir_with_toml / "core.toml"),
            FileChange(change_type=Change.modified, path=config_dir_with_toml / "model.toml"),
        ]

        await initialized_service._handle_reload(changes)

        assert len(calls) == 1
        scopes_received = calls[0]
        assert "core" in scopes_received
        assert "model" in scopes_received

    @pytest.mark.asyncio
    async def test_handle_reload_ignores_unknown_files(self, initialized_service, tmp_path):
        """未知的文件名不应触发 reload (scope 解析失败)"""
        calls = []

        def cb(scopes):
            calls.append(list(scopes))

        initialized_service.register_reload_callback(cb)

        changes = [
            FileChange(change_type=Change.modified, path=tmp_path / "random.txt"),
        ]

        await initialized_service._handle_reload(changes)

        # 没有有效 scope → 不应调用回调
        assert len(calls) == 0

    @pytest.mark.asyncio
    async def test_handle_reload_empty_changes_is_noop(self, initialized_service):
        """空变更列表必须直接返回，不调用任何回调"""
        calls = []

        def cb(scopes):
            calls.append("called")

        initialized_service.register_reload_callback(cb)

        await initialized_service._handle_reload([])

        assert calls == []


# ===========================================================================
# 4. start_file_watcher / stop_file_watcher
# ===========================================================================


class TestFileWatcherLifecycle:
    """start/stop_file_watcher 必须正确管理 FileWatcher"""

    @pytest.mark.asyncio
    async def test_start_creates_file_watcher(self, initialized_service):
        """start_file_watcher 必须创建 FileWatcher 实例"""
        # 初始: _file_watcher 为 None
        assert initialized_service._file_watcher is None

        await initialized_service.start_file_watcher()

        assert initialized_service._file_watcher is not None
        assert initialized_service._file_watcher.running is True

        # 清理
        await initialized_service.stop_file_watcher()

    @pytest.mark.asyncio
    async def test_start_is_idempotent(self, initialized_service):
        """重复 start_file_watcher 不应重启 watcher"""
        await initialized_service.start_file_watcher()
        watcher_ref = initialized_service._file_watcher

        await initialized_service.start_file_watcher()  # 第二次
        assert initialized_service._file_watcher is watcher_ref

        await initialized_service.stop_file_watcher()

    @pytest.mark.asyncio
    async def test_stop_cleans_up_watcher(self, initialized_service):
        """stop_file_watcher 必须把 _file_watcher 置 None 并停止 watcher"""
        await initialized_service.start_file_watcher()
        assert initialized_service._file_watcher is not None

        await initialized_service.stop_file_watcher()

        assert initialized_service._file_watcher is None

    @pytest.mark.asyncio
    async def test_stop_without_start_is_noop(self, initialized_service):
        """未 start 时 stop 必须不抛异常"""
        # 不应抛异常
        await initialized_service.stop_file_watcher()

    @pytest.mark.asyncio
    async def test_watcher_monitors_all_config_files(
        self, initialized_service, config_dir_with_toml
    ):
        """watcher 必须监听 config/ 下所有 5 个 TOML 文件"""
        await initialized_service.start_file_watcher()
        try:
            watched_paths = [p.resolve() for p in initialized_service._file_watcher._paths]
            for name in ("core.toml", "model.toml", "input.toml", "decision.toml", "output.toml"):
                expected = (config_dir_with_toml / name).resolve()
                assert expected in watched_paths, f"watcher 应监听 {name}"
        finally:
            await initialized_service.stop_file_watcher()


# ===========================================================================
# 5. 端到端: 文件变更触发 reload + ConfigProxy 反映新值
# ===========================================================================


class TestHotReloadEndToEnd:
    """修改 TOML 文件 → FileWatcher 检测 → reload → ConfigProxy 看到新值"""

    @pytest.mark.asyncio
    async def test_modify_core_toml_triggers_reload_and_proxy_reflects_new_value(
        self, initialized_service, config_dir_with_toml
    ):
        """修改 core.toml 后, ConfigProxy 必须能看到新值"""
        from src.modules.config.config_proxy import ConfigProxy
        import tomlkit

        # 建立 proxy: 始终返回最新配置
        proxy = ConfigProxy(getter=lambda: initialized_service._main_config)

        # 修改前的值
        old_persona = proxy.get("persona", {})
        old_bot_name = old_persona.get("bot_name") if isinstance(old_persona, dict) else None

        # 启动 watcher
        await initialized_service.start_file_watcher()

        # 给 watcher 一点时间稳定
        await asyncio.sleep(0.3)

        try:
            new_core_toml = (
                "[persona]\n"
                'type = "persona"\n'
                'bot_name = "热重载后的名字"\n'
            )
            (config_dir_with_toml / "core.toml").write_text(new_core_toml, encoding="utf-8")

            # 等待 FileWatcher 检测 (debounce ~600ms) + reload
            # 多次轮询直到值改变或超时
            deadline = asyncio.get_running_loop().time() + 10.0
            new_value = None
            while asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.2)
                new_persona = proxy.get("persona", {})
                if isinstance(new_persona, dict) and new_persona.get("bot_name") == "热重载后的名字":
                    new_value = new_persona["bot_name"]
                    break

            assert new_value == "热重载后的名字", (
                f"ConfigProxy 未反映热重载新值 (旧值={old_bot_name})"
            )
        finally:
            await initialized_service.stop_file_watcher()

    @pytest.mark.asyncio
    async def test_reload_callback_fires_on_file_change(
        self, initialized_service, config_dir_with_toml
    ):
        """FileWatcher 检测到变更后, 已注册的回调必须被调用"""
        callback_called = asyncio.Event()
        received_scopes: list[Sequence[str]] = []

        def cb(scopes):
            received_scopes.append(list(scopes))
            callback_called.set()

        initialized_service.register_reload_callback(cb)

        await initialized_service.start_file_watcher()
        await asyncio.sleep(0.3)

        try:
            # 触发文件变更
            (config_dir_with_toml / "core.toml").write_text(
                "# touched\n", encoding="utf-8"
            )

            # 等待回调触发 (最多 10s)
            await asyncio.wait_for(callback_called.wait(), timeout=10.0)
            assert len(received_scopes) >= 1
        finally:
            await initialized_service.stop_file_watcher()

    @pytest.mark.asyncio
    async def test_multiple_callbacks_all_fire_on_reload(self, initialized_service):
        """reload_config 时所有注册的回调都必须被调用"""
        calls = []

        def cb1(scopes):
            calls.append("cb1")

        def cb2(scopes):
            calls.append("cb2")

        def cb3(scopes):
            calls.append("cb3")

        initialized_service.register_reload_callback(cb1)
        initialized_service.register_reload_callback(cb2)
        initialized_service.register_reload_callback(cb3)

        await initialized_service.reload_config(changed_scopes=["core"])

        assert "cb1" in calls
        assert "cb2" in calls
        assert "cb3" in calls
        assert len(calls) == 3