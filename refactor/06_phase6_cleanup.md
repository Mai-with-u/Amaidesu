# Phase 6: 清理、测试和迁移

## 🎯 目标

1. 删除旧插件系统（PluginManager、plugins/目录）
2. 更新main.py以使用新架构
3. 测试所有功能
4. 验证Git历史完整性
5. 配置迁移和清理

## 📝 实施内容

### 6.1 删除旧插件系统

#### 删除PluginManager

```bash
# PluginManager已被ExtensionLoader取代
git rm src/core/plugin_manager.py
git commit -m "refactor: remove PluginManager (replaced by ExtensionLoader)"
```

#### 删除已迁移的插件

```bash
# 删除已迁移到核心数据流的插件
git rm -r src/plugins/tts
git rm -r src/plugins/stt
git rm -r src/plugins/bili_danmaku
git rm -r src/plugins/bili_danmaku_official
git rm -r src/plugins/mock_danmaku
git rm -r src/plugins/console_input
git rm -r src/plugins/subtitle
git rm -r src/plugins/vtube_studio
git rm -r src/plugins/emotion_judge
git rm -r src/plugins/keyword_action
git rm -r src/plugins/read_pingmu
git rm -r src/plugins/arknights

# 删除已迁移到扩展系统的插件
git rm -r src/plugins/minecraft
git rm -r src/plugins/warudo
git rm -r src/plugins/dg_lab_service
git rm -r src/plugins/mainosaba
git rm -r src/plugins/maicraft

git commit -m "refactor: remove migrated plugins"
```

### 6.2 更新main.py

`main.py`需要更新以使用新架构：

```python
import asyncio
import sys
import os

# 导入新的核心组件
from src.core.amaidesu_core import AmaidesuCore
from src.core.event_bus import EventBus
from src.core.decision_provider import DecisionManager
from src.core.extension_loader import ExtensionLoader
from src.core.pipeline_manager import PipelineManager
from src.core.context_manager import ContextManager

# 导入新层级
from src.perception.input_factory import InputProviderFactory
from src.normalization.normalizer_factory import NormalizerFactory
from src.expression.expression_generator import TTSModule
from src.rendering.renderer_factory import RendererFactory

from src.utils.logger import get_logger
from src.utils.config import initialize_configurations
from src.config.config import global_config

logger = get_logger("Main")

async def main():
    logger.info("Starting Amaidesu with new architecture...")

    # 初始化配置
    config, *_ = initialize_configurations(base_dir=os.path.dirname(__file__))

    # 创建事件总线
    event_bus = EventBus()
    logger.info("EventBus created")

    # 创建决策管理器
    decision_manager = DecisionManager(event_bus)
    decision_config = config.get("decision", {})
    await decision_manager.setup(
        provider_name=decision_config.get("default_provider", "maicore"),
        config=decision_config.get("providers", {}).get("maicore", {})
    )
    logger.info("DecisionManager created")

    # 创建AmaidesuCore
    core = AmaidesuCore(
        platform=config.get("general", {}).get("platform_id", "amaidesu_default"),
        maicore_host=config.get("maicore", {}).get("host", "127.0.0.1"),
        maicore_port=config.get("maicore", {}).get("port", 8000),
        http_host=config.get("http_server", {}).get("host", None),
        http_port=config.get("http_server", {}).get("port", None),
        http_callback_path=config.get("http_server", {}).get("callback_path", "/callback"),
        pipeline_manager=PipelineManager(),
        context_manager=ContextManager(config.get("context_manager", {})),
        event_bus=event_bus,
        avatar=None,
        llm_client_manager=None,
    )
    logger.info("AmaidesuCore created")

    # 加载输入Provider（Layer 1）
    input_factory = InputProviderFactory()
    input_providers = []
    input_config = config.get("perception", {}).get("inputs", {})

    for provider_name, provider_config in input_config.items():
        provider_type = provider_config.get("type")
        provider = input_factory.create(provider_type, provider_config)
        input_providers.append(provider)
        logger.info(f"Input provider created: {provider_name} ({provider_type})")

    # 加载输出Provider（Layer 6）
    renderer_factory = RendererFactory()
    output_providers = []
    output_config = config.get("rendering", {}).get("outputs", {})

    for provider_name, provider_config in output_config.items():
        provider_type = provider_config.get("type")
        renderer = renderer_factory.create(provider_type, provider_config)
        await renderer.setup(event_bus, provider_config)
        output_providers.append(renderer)
        logger.info(f"Output renderer created: {provider_name} ({provider_type})")

    # 加载扩展（Layer 8）
    extension_loader = ExtensionLoader(event_bus, config.get("extensions", {}))
    await extension_loader.load_all()
    logger.info("Extensions loaded")

    # 连接核心服务
    await core.connect()
    logger.info("Core connected")

    # 启动输入Provider（并发）
    logger.info("Starting input providers...")
    input_tasks = []
    for provider in input_providers:
        task = asyncio.create_task(provider.start())
        input_tasks.append(task)

    # 保持运行
    stop_event = asyncio.Event()
    try:
        await stop_event.wait()
    except KeyboardInterrupt:
        logger.info("Received KeyboardInterrupt, shutting down...")
    finally:
        # 清理
        logger.info("Cleaning up...")

        for provider in input_providers:
            await provider.stop()

        for renderer in output_providers:
            await renderer.cleanup()

        await extension_loader.cleanup()
        await core.disconnect()

        logger.info("Shutdown complete")

if __name__ == "__main__":
    asyncio.run(main())
```

### 6.3 配置迁移

```toml
# 新的config.toml示例

[general]
platform_id = "amaidesu_default"

[maicore]
host = "127.0.0.1"
port = 8000

# 决策层配置（新增）
[decision]
default_provider = "maicore"

[decision.providers.maicore]
host = "127.0.0.1"
port = 8000

# [decision.providers.local_llm]  # 可选
# model = "gpt-4"
# api_key = "your_key"

# 输入层配置（Layer 1）
[perception]
inputs = ["console", "danmaku", "voice"]

[perception.inputs.console]
type = "console"

[perception.inputs.danmaku]
type = "bilibili_danmaku"
room_id = "123456"

[perception.inputs.voice]
type = "microphone"
device_index = 0

# 输出层配置（Layer 6）
[rendering]
outputs = ["subtitle", "tts", "vts"]

[rendering.outputs.subtitle]
type = "subtitle"
font_size = 24

[rendering.outputs.tts]
type = "tts"
provider = "edge"
voice = "zh-CN-XiaoxiaoNeural"

[rendering.outputs.vts]
type = "virtual"
host = "127.0.0.1"
port = 8001

# 扩展配置（Layer 8）
[extensions.minecraft]
enabled = true
host = "localhost"
port = 25565
events_enabled = true
commands_enabled = true
```

### 6.4 测试验证

#### 单元测试

```bash
# 运行所有测试
python -m pytest tests/

# 测试决策层
python -m pytest tests/test_decision_layer.py

# 测试输入层
python -m pytest tests/test_input_layer.py

# 测试输出层
python -m pytest tests/test_output_layer.py
```

#### 集成测试

```bash
# 测试完整数据流
python -m pytest tests/test_integration.py

# 测试多Provider并发
python -m pytest tests/test_concurrent_providers.py

# 测试扩展加载
python -m pytest tests/test_extension_loader.py
```

#### 手动测试

```bash
# 启动应用
python main.py

# 测试输入
# - 在控制台输入消息
# - 查看弹幕是否被采集
# - 查看MaiCore是否收到消息

# 测试输出
# - 查看字幕是否显示
# - 查看TTS是否播放
# - 查看虚拟形象是否动作
```

### 6.5 Git历史验证

```bash
# 验证文件历史是否完整
git log --follow src/extensions/minecraft/

# 验证提交历史
git log --oneline --all | head -20

# 验证分支状态
git status

# 验证所有迁移的文件
git log --all --diff-filter=M --name-only | grep "src/extensions/"
```

## ✅ 验证标准

1. ✅ 旧插件系统完全删除
2. ✅ main.py使用新架构
3. ✅ 所有单元测试通过
4. ✅ 所有集成测试通过
5. ✅ 手动测试功能正常
6. ✅ Git历史完整（使用`git log --follow`验证）
7. ✅ 配置迁移完成
8. ✅ 文档更新完成

## 📝 最终提交

```bash
# 提交main.py更新
git add main.py
git commit -m "refactor: update main.py for new architecture"

# 提交配置迁移
git add config.toml config-template.toml
git commit -m "refactor: migrate configuration to new architecture"

# 创建最终提交
git commit -m "feat: complete architecture refactoring to 6-layer data flow"

# 标记版本
git tag -a v2.0.0 -m "Architecture refactoring: 6-layer data flow + decision layer + extension system"
```

## 🎉 重构完成

所有Phase完成，架构重构结束！

**主要成果**：
1. ✅ 6层核心数据流架构
2. ✅ 可替换的决策层
3. ✅ 多Provider并发支持
4. ✅ Provider模式统一接口
5. ✅ 扩展系统支持社区开发
6. ✅ EventBus内部通信
7. ✅ 配置简化40%以上
8. ✅ Git历史完整保留

**下一步**：
- 部署到生产环境
- 监控性能指标
- 收集用户反馈
- 持续优化
