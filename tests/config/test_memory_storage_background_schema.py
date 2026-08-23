"""Memory/Storage/Background 配置 Schema 测试（v2.0.0）"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.modules.config.background_schemas import (
    BackgroundConfig,
    BackgroundRootConfig,
    CompressorConfig,
)
from src.modules.config.memory_schemas import (
    AmemorixConfig,
    MemoryBackend,
    MemoryConfig,
    MemoryRootConfig,
    SimpleMemoryConfig,
)
from src.modules.config.storage_schemas import (
    SqliteStorageConfig,
    StorageConfig,
    StorageRootConfig,
)


class TestMemoryRootConfig:
    def test_default_construction(self):
        cfg = MemoryRootConfig()
        assert isinstance(cfg.memory, MemoryConfig)


class TestMemoryConfig:
    def test_default_backend_simple(self):
        cfg = MemoryConfig()
        assert cfg.backend == "simple"

    def test_backend_literal_validation(self):
        MemoryConfig(backend="simple")
        MemoryConfig(backend="amemorix")
        with pytest.raises(ValidationError):
            MemoryConfig(backend="unknown")

    def test_default_simple_subconfig_none(self):
        cfg = MemoryConfig()
        assert cfg.simple is None

    def test_simple_subconfig(self):
        cfg = MemoryConfig(simple={"recall_top_k": 10})
        assert cfg.simple.recall_top_k == 10

    def test_amemorix_subconfig(self):
        cfg = MemoryConfig(amemorix={"endpoint": "http://x:8100", "api_key": "k", "timeout": 60})
        assert cfg.amemorix.endpoint == "http://x:8100"
        assert cfg.amemorix.api_key == "k"
        assert cfg.amemorix.timeout == 60


class TestSimpleMemoryConfig:
    def test_defaults(self):
        cfg = SimpleMemoryConfig()
        assert cfg.recall_top_k == 5
        assert cfg.viewer_profile_max == 20

    def test_recall_top_k_min_max(self):
        with pytest.raises(ValidationError):
            SimpleMemoryConfig(recall_top_k=0)


class TestStorageRootConfig:
    def test_default_construction(self):
        cfg = StorageRootConfig()
        assert isinstance(cfg.storage, StorageConfig)


class TestStorageConfig:
    def test_default_sqlite_db_path(self):
        cfg = StorageConfig()
        assert cfg.sqlite.db_path == "data/amaidesu.db"

    def test_default_wal_enabled(self):
        cfg = StorageConfig()
        assert cfg.sqlite.wal is True

    def test_sqlite_override(self):
        cfg = StorageConfig(sqlite={"db_path": "/tmp/test.db", "wal": False})
        assert cfg.sqlite.db_path == "/tmp/test.db"
        assert cfg.sqlite.wal is False


class TestSqliteStorageConfig:
    def test_defaults(self):
        cfg = SqliteStorageConfig()
        assert cfg.db_path == "data/amaidesu.db"
        assert cfg.busy_timeout_ms == 5000
        assert cfg.foreign_keys is True


class TestBackgroundRootConfig:
    def test_default_construction(self):
        cfg = BackgroundRootConfig()
        assert isinstance(cfg.background, BackgroundConfig)


class TestBackgroundConfig:
    def test_defaults(self):
        cfg = BackgroundConfig()
        assert cfg.light_tick_ms == 5000
        assert cfg.compressor.concurrency == 1
        assert cfg.compressor.queue_max == 100

    def test_compressor_validation(self):
        with pytest.raises(ValidationError):
            BackgroundConfig(compressor={"concurrency": 0})

    def test_light_tick_validation(self):
        with pytest.raises(ValidationError):
            BackgroundConfig(light_tick_ms=50)


class TestCompressorConfig:
    def test_defaults(self):
        cfg = CompressorConfig()
        assert cfg.concurrency == 1
        assert cfg.queue_max == 100


class TestMemoryBackendLiteral:
    def test_backend_values(self):
        from typing import get_args
        values = get_args(MemoryBackend)
        assert "simple" in values
        assert "amemorix" in values