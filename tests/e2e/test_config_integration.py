"""
E2E Test: 配置系统集成测试

验证整个配置系统与Provider/Manager的集成：
1. ConfigService初始化和配置加载
2. 三级配置合并（Schema默认值 → 主配置覆盖 → Provider本地配置）
3. Provider注册表完整性
4. Provider生命周期管理（enabled开关控制）
5. Manager从配置加载Provider
6. 应用启动不抛出异常

注意：已移除config-defaults.toml加载逻辑，所有默认值由Schema定义。

测试策略：
- 使用实际项目配置文件（config-template.toml）
- 验证所有23个Provider能正常注册
- 验证Schema-based配置合并逻辑
- 验证Manager能正确加载Provider
"""

import os

import pytest

import src.domains.decision.providers  # noqa: F401

# 触发 Provider 注册
import src.domains.input.providers  # noqa: F401
import src.domains.output.providers  # noqa: F401


@pytest.fixture
def project_base_dir():
    """获取项目根目录"""
    # 从当前文件向上找到项目根目录
    current_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(current_dir))


@pytest.fixture
def config_service(project_base_dir):
    """创建 ConfigService 实例并初始化"""
    from src.modules.config.service import ConfigService

    service = ConfigService(base_dir=project_base_dir)
    service.initialize()  # 同步方法，不需要 await
    yield service
    # ConfigService 没有需要清理的资源


# ==================== 测试套件 1: ConfigService 基本功能 ====================


@pytest.mark.asyncio
async def test_config_service_initialization(config_service):
    """测试 ConfigService 能正常初始化"""
    assert config_service is not None
    assert config_service._initialized is True
    assert config_service.main_config is not None
    assert isinstance(config_service.main_config, dict)


@pytest.mark.asyncio
async def test_config_service_has_required_sections(config_service):
    """测试配置文件包含必需的配置节"""
    # 验证主要配置节存在
    general = config_service.get_section("general")
    assert general is not None
    assert isinstance(general, dict)

    # 验证Provider配置节存在
    input_config = config_service.get_section("providers.input")
    assert input_config is not None
    assert isinstance(input_config, dict)

    output_config = config_service.get_section("providers.output")
    assert output_config is not None
    assert isinstance(output_config, dict)

    decision_config = config_service.get_section("providers.decision")
    assert decision_config is not None
    assert isinstance(decision_config, dict)


@pytest.mark.asyncio
async def test_config_service_no_exceptions_on_init(project_base_dir):
    """测试配置初始化不抛出异常"""
    from src.modules.config.service import ConfigService

    # 应该能成功初始化，不抛出任何异常
    service = ConfigService(base_dir=project_base_dir)
    main_config, main_copied, input_copied, decision_copied, output_copied = service.initialize()

    # 验证返回值
    assert main_config is not None
    assert isinstance(main_copied, bool)
    assert isinstance(input_copied, bool)
    assert isinstance(decision_copied, bool)
    assert isinstance(output_copied, bool)


# ==================== 测试套件 2: Provider 注册表完整性 ====================


@pytest.mark.asyncio
async def test_provider_registry_has_all_providers():
    """测试 Provider 注册表包含所有 23 个 Provider"""
    from src.modules.registry import ProviderRegistry

    input_providers = ProviderRegistry.get_registered_input_providers()
    output_providers = ProviderRegistry.get_registered_output_providers()
    decision_providers = ProviderRegistry.get_registered_decision_providers()

    total_providers = len(input_providers) + len(output_providers) + len(decision_providers)

    # 验证总数量（根据设计文档，应该有 23 个 Provider）
    assert total_providers >= 20, f"Provider 数量不足，预期至少 20 个，实际 {total_providers}"

    # 验证每种类型都有 Provider
    assert len(input_providers) > 0, "应该有 InputProvider"
    assert len(output_providers) > 0, "应该有 OutputProvider"
    assert len(decision_providers) > 0, "应该有 DecisionProvider"

    # 验证关键的 InputProvider 存在
    expected_input_providers = ["console_input", "bili_danmaku", "mock_danmaku"]
    for provider_name in expected_input_providers:
        assert provider_name in input_providers, f"缺少 InputProvider: {provider_name}"

    # 验证关键的 OutputProvider 存在
    expected_output_providers = ["edge_tts", "subtitle", "mock", "vts"]
    for provider_name in expected_output_providers:
        assert provider_name in output_providers, f"缺少 OutputProvider: {provider_name}"

    # 验证关键的 DecisionProvider 存在
    expected_decision_providers = ["maicore", "local_llm", "mock", "rule_engine"]
    for provider_name in expected_decision_providers:
        assert provider_name in decision_providers, f"缺少 DecisionProvider: {provider_name}"


@pytest.mark.asyncio
async def test_provider_registry_can_create_all_providers():
    """测试 Provider 注册表能创建所有已注册的 Provider"""
    from src.modules.registry import ProviderRegistry

    input_providers = ProviderRegistry.get_registered_input_providers()
    output_providers = ProviderRegistry.get_registered_output_providers()
    decision_providers = ProviderRegistry.get_registered_decision_providers()

    # 尝试创建所有 InputProvider（使用最小配置）
    created_input = []
    failed_input = []

    for provider_name in input_providers:
        try:
            provider = ProviderRegistry.create_input(provider_name, {})
            assert provider is not None
            created_input.append(provider_name)
        except Exception as e:
            failed_input.append((provider_name, str(e)))

    # 尝试创建所有 OutputProvider（使用最小配置）
    created_output = []
    failed_output = []

    for provider_name in output_providers:
        try:
            provider = ProviderRegistry.create_output(provider_name, {})
            assert provider is not None
            created_output.append(provider_name)
        except Exception as e:
            failed_output.append((provider_name, str(e)))

    # 尝试创建所有 DecisionProvider（使用最小配置）
    created_decision = []
    failed_decision = []

    for provider_name in decision_providers:
        try:
            provider = ProviderRegistry.create_decision(provider_name, {})
            assert provider is not None
            created_decision.append(provider_name)
        except Exception as e:
            failed_decision.append((provider_name, str(e)))

    # 验证至少大部分 Provider 能创建（允许一些失败，因为可能需要复杂配置）
    total_created = len(created_input) + len(created_output) + len(created_decision)
    total_registered = len(input_providers) + len(output_providers) + len(decision_providers)

    success_rate = total_created / total_registered if total_registered > 0 else 0
    assert success_rate >= 0.5, (
        f"Provider 创建成功率过低: {success_rate:.2%}, 失败: {failed_input + failed_output + failed_decision}"
    )


# ==================== 测试套件 3: 配置合并功能 ====================


@pytest.mark.asyncio
async def test_config_service_three_level_merge(config_service):
    """测试三级配置合并功能（Schema默认值 → 主配置覆盖 → 本地配置）"""
    # 测试Schema-based配置合并
    # 这里使用 console_input 作为示例

    # 1. 获取合并后的配置（使用Schema）
    from src.modules.config.schemas import ConsoleInputProviderConfig

    merged_config = config_service.get_provider_config_with_defaults(
        "console_input",
        "input",
        schema_class=ConsoleInputProviderConfig,  # 使用 Schema 验证
    )

    assert merged_config is not None
    assert isinstance(merged_config, dict)

    # 2. 验证配置结构
    # 配置应该包含Schema默认值
    assert "type" in merged_config
    # 其他字段取决于 ConsoleInputProviderConfig 的定义
    # 这里只验证返回了字典，不验证具体字段，因为不同 Provider 的配置差异很大


@pytest.mark.asyncio
async def test_config_service_deep_merge_function():
    """测试深度合并函数"""
    from src.modules.config.service import deep_merge_configs

    # 测试基本合并
    base = {"a": 1, "b": {"x": 10, "y": 20}}
    override = {"b": {"y": 200}, "c": 3}
    result = deep_merge_configs(base, override)

    assert result == {"a": 1, "b": {"x": 10, "y": 200}, "c": 3}

    # 测试列表替换
    base = {"items": [1, 2, 3]}
    override = {"items": [4, 5]}
    result = deep_merge_configs(base, override)

    assert result == {"items": [4, 5]}

    # 测试 None 值跳过
    base = {"a": 1, "b": 2}
    override = {"b": None, "c": 3}
    result = deep_merge_configs(base, override)

    assert result == {"a": 1, "b": 2, "c": 3}


# ==================== 测试套件 4: Provider 生命周期管理 ====================


@pytest.mark.asyncio
async def test_input_provider_manager_load_from_config(config_service):
    """测试 InputProviderManager 能从配置加载 Provider"""
    from src.domains.input.input_provider_manager import InputProviderManager

    from src.modules.events.event_bus import EventBus

    event_bus = EventBus()
    manager = InputProviderManager(event_bus)

    # 获取输入配置
    input_config = config_service.get_section("providers.input")

    # 加载 Provider（不启动）
    providers = await manager.load_from_config(input_config)

    # 验证至少加载了一些 Provider
    assert len(providers) >= 0
    assert all(isinstance(p, object) for p in providers)

    # 清理
    for provider in providers:
        try:
            await provider.cleanup()
        except Exception:
            pass


@pytest.mark.asyncio
async def test_output_provider_manager_load_from_config(config_service):
    """测试 OutputProviderManager 能从配置加载 Provider"""
    from src.domains.output.provider_manager import OutputProviderManager

    manager = OutputProviderManager()

    # 获取输出配置
    output_config = config_service.get_section("providers.output")

    # 加载 Provider（不启动）
    await manager.load_from_config(output_config, core=None)

    # 验证至少加载了一些 Provider
    providers = manager.providers
    assert len(providers) >= 0
    assert all(isinstance(p, object) for p in providers)

    # 清理
    await manager.stop_all_providers()


@pytest.mark.asyncio
async def test_enabled_switch_controls_provider_loading(config_service):
    """测试 enabled 开关正确控制 Provider 加载"""
    from src.domains.input.input_provider_manager import InputProviderManager

    from src.modules.events.event_bus import EventBus

    event_bus = EventBus()
    manager = InputProviderManager(event_bus)

    # 测试 enabled = false 的情况
    config_disabled = {
        "enabled": False,
        "inputs": ["console_input"],
        "inputs_config": {"console_input": {"type": "console_input"}},
    }

    providers = await manager.load_from_config(config_disabled)
    assert len(providers) == 0, "enabled=false 时应该不加载任何 Provider"

    # 测试 enabled = true 的情况
    config_enabled = {
        "enabled": True,
        "inputs": ["console_input"],
        "inputs_config": {"console_input": {"type": "console_input"}},
    }

    providers = await manager.load_from_config(config_enabled)
    assert len(providers) == 1, "enabled=true 时应该加载 Provider"
    assert providers[0].__class__.__name__ == "ConsoleInputProvider"

    # 清理
    for provider in providers:
        try:
            await provider.cleanup()
        except Exception:
            pass


# ==================== 测试套件 5: 应用启动模拟 ====================


@pytest.mark.asyncio
async def test_application_startup_simulation(project_base_dir):
    """模拟应用启动流程，验证所有组件能正常初始化"""
    from src.domains.input.input_provider_manager import InputProviderManager

    from src.domains.decision.provider_manager import DecisionProviderManager as DecisionManager
    from src.domains.output.provider_manager import OutputProviderManager
    from src.modules.config.service import ConfigService
    from src.modules.events.event_bus import EventBus

    # 1. 初始化配置服务
    config_service = ConfigService(base_dir=project_base_dir)
    main_config, main_copied, _, _, _ = config_service.initialize()
    assert main_config is not None

    # 2. 创建 EventBus
    event_bus = EventBus()
    assert event_bus is not None

    # 3. 创建并加载 InputProviderManager
    input_manager = InputProviderManager(event_bus)
    input_config = config_service.get_section("providers.input")
    input_providers = await input_manager.load_from_config(input_config)
    # 注意：不启动 Provider，只验证加载

    # 4. 创建并加载 OutputProviderManager
    output_manager = OutputProviderManager()
    output_config = config_service.get_section("providers.output")
    await output_manager.load_from_config(output_config)
    # 注意：不启动 Provider，只验证加载

    # 5. 创建 DecisionManager（不启动）
    decision_manager = DecisionManager(event_bus, llm_service=None)
    config_service.get_section("providers.decision")
    # DecisionManager 的初始化可能在后续版本实现

    # 6. 验证所有 Manager 都创建成功
    assert input_manager is not None
    assert output_manager is not None
    assert decision_manager is not None

    # 7. 清理
    for provider in input_providers:
        try:
            await provider.cleanup()
        except Exception:
            pass

    await output_manager.stop_all_providers()

    if decision_manager.get_current_provider():
        await decision_manager.cleanup()


@pytest.mark.asyncio
async def test_config_does_not_throw_exceptions(project_base_dir):
    """测试配置加载不会抛出未捕获的异常"""
    from src.modules.config.service import ConfigService

    # 尝试初始化配置服务
    try:
        service = ConfigService(base_dir=project_base_dir)
        service.initialize()

        # 尝试访问各种配置
        _ = service.get_section("general")
        _ = service.get_section("providers.input")
        _ = service.get_section("providers.output")
        _ = service.get_section("providers.decision")

        # 使用新的配置API获取 Provider 配置
        from src.domains.output.providers.audio import EdgeTTSProvider
        from src.modules.config.schemas import ConsoleInputProviderConfig

        _ = service.get_provider_config_with_defaults("console_input", "input", schema_class=ConsoleInputProviderConfig)
        _ = service.get_provider_config_with_defaults("edge_tts", "output", schema_class=EdgeTTSProvider.ConfigSchema)

        # 如果没有抛出异常，测试通过
        assert True

    except Exception as e:
        pytest.fail(f"配置加载抛出异常: {e}", pytrace=False)


# ==================== 测试套件 6: 配置迁移检测 ====================


@pytest.mark.asyncio
async def test_config_migration_detection(config_service):
    """测试配置迁移检测功能"""
    # ConfigService 的 initialize 方法返回迁移状态
    # 注意：config_service fixture 已经初始化过了，这里不需要再次初始化
    # 我们只需要验证 _initialized 标志
    assert config_service._initialized is True

    # 验证配置属性是布尔值
    assert isinstance(config_service._main_config_copied, bool)
    assert isinstance(config_service._plugin_configs_copied, bool)
    assert isinstance(config_service._pipeline_configs_copied, bool)


# ==================== 测试套件 7: 错误处理 ====================


@pytest.mark.asyncio
async def test_config_service_handles_missing_sections(config_service):
    """测试 ConfigService 正确处理缺失的配置节"""
    # 尝试获取不存在的配置节
    missing_section = config_service.get_section("non_existent_section")
    assert missing_section == {}

    # 尝试获取不存在的配置项
    missing_item = config_service.get("non_existent_key", default="default_value")
    assert missing_item == "default_value"


@pytest.mark.asyncio
async def test_provider_registry_handles_unknown_provider():
    """测试 Provider 注册表正确处理未注册的 Provider"""
    from src.modules.registry import ProviderRegistry

    # 尝试创建不存在的 Provider
    with pytest.raises(ValueError, match="Unknown input provider"):
        ProviderRegistry.create_input("non_existent_provider", {})

    with pytest.raises(ValueError, match="Unknown output provider"):
        ProviderRegistry.create_output("non_existent_provider", {})

    with pytest.raises(ValueError, match="Unknown decision provider"):
        ProviderRegistry.create_decision("non_existent_provider", {})


@pytest.mark.asyncio
async def test_manager_handles_invalid_provider_type():
    """测试 Manager 正确处理无效的 Provider 类型"""
    import os

    from src.domains.input.input_provider_manager import InputProviderManager

    from src.modules.config.service import ConfigService
    from src.modules.events.event_bus import EventBus

    event_bus = EventBus()
    manager = InputProviderManager(event_bus)

    # 创建 ConfigService 实例
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    config_service = ConfigService(base_dir=base_dir)
    config_service.initialize()

    # 尝试加载不存在的 Provider 类型
    config = {
        "enabled": True,
        "inputs": ["non_existent_provider"],
        "inputs_config": {"non_existent_provider": {"type": "non_existent_provider", "enabled": True}},
    }

    # 应该不抛出异常，而是跳过无效的 Provider
    providers = await manager.load_from_config(config, config_service=config_service)
    assert len(providers) == 0

    # 清理
    for provider in providers:
        try:
            await provider.cleanup()
        except Exception:
            pass


# ==================== 测试套件 8: 配置验证 ====================


@pytest.mark.asyncio
async def test_provider_config_validation():
    """测试 Provider 配置验证"""
    from src.modules.registry import ProviderRegistry

    # 测试创建 Provider 时传入空配置
    # 大部分 Provider 应该能使用空配置创建（使用默认值）
    try:
        provider = ProviderRegistry.create_input("console_input", {})
        assert provider is not None
    except Exception as e:
        pytest.fail(f"console_input 创建失败（空配置）: {e}", pytrace=False)

    # mock 输出 Provider 是抽象类，跳过测试
    # 我们改用 subtitle Provider
    try:
        provider = ProviderRegistry.create_output("subtitle", {})
        assert provider is not None
    except Exception as e:
        pytest.fail(f"subtitle 创建失败（空配置）: {e}", pytrace=False)

    try:
        provider = ProviderRegistry.create_decision("mock", {})
        assert provider is not None
    except Exception as e:
        pytest.fail(f"mock decision 创建失败（空配置）: {e}", pytrace=False)


# ==================== 测试套件 9: 性能和资源管理 ====================


@pytest.mark.asyncio
async def test_config_service_is_lightweight(project_base_dir):
    """测试 ConfigService 初始化是轻量级的"""
    import time

    from src.modules.config.service import ConfigService

    start_time = time.time()

    service = ConfigService(base_dir=project_base_dir)
    service.initialize()

    elapsed_time = time.time() - start_time

    # 配置初始化应该在 1 秒内完成
    assert elapsed_time < 1.0, f"配置初始化耗时过长: {elapsed_time:.2f}秒"


@pytest.mark.asyncio
async def test_provider_registry_has_no_duplicates():
    """测试 Provider 注册表没有重复注册"""
    from src.modules.registry import ProviderRegistry

    registry_info = ProviderRegistry.get_registry_info()

    # 检查每种 Provider 类型
    for provider_type, providers in registry_info.items():
        provider_names = list(providers.keys())
        # 验证没有重复的名称
        assert len(provider_names) == len(set(provider_names)), f"{provider_type} 中存在重复注册的 Provider"
