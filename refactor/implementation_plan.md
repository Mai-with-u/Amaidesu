# Amaidesu 重构实施计划

## 🎯 实施原则

### 核心目标
1. **全面重构**：1-2天内完成，不考虑向后兼容
2. **消灭插件化**：核心功能全部模块化
3. **EventBus优先**：用事件系统替代服务注册
4. **Provider模式**：统一接口，工厂动态选择（使用Provider命名，比"策略"更直观）
5. **保留Git历史**：使用`git mv`迁移文件，避免丢失提交历史

### ⚠️ 重要：Git历史保留

**强制要求**：所有文件迁移必须使用`git mv`命令，**禁止使用文件系统直接移动文件**

**原因**：
- `git mv`会记录文件移动，Git可以追溯完整历史
- 直接移动文件会导致Git丢失该文件的提交历史
- 重构后的代码应该可以追溯到原始实现

**正确做法**：
```bash
# ✅ 正确：使用git mv
git mv src/plugins/mainosaba src/extensions/mainosaba
git commit -m "refactor: migrate mainosaba to extensions layer"

# 查看完整历史（包括移动）
git log --follow src/extensions/mainosaba/
```

**错误做法**：
```bash
# ❌ 错误：直接在文件系统移动文件
mv src/plugins/mainosaba src/extensions/mainosaba
git add src/extensions/mainosaba
git commit -m "refactor: move mainosaba"
# 结果：Git历史丢失！
```

**迁移策略**：
- 推荐渐进式迁移（Phase 1-4），每阶段独立提交
- 每次移动后立即提交，确保历史可追溯
- 使用`git log --follow`验证历史完整性

### 实施顺序
按照数据流顺序，从输入到输出逐步重构：
```
Layer 1 → Layer 2 → Layer 3 → Layer 4 → Layer 5 → Layer 6 → Layer 7
```

## 📋 分层实施计划

### Phase 1: 基础设施搭建

#### 1.1 创建7层目录结构
```
src/
├── perception/
├── normalization/  
├── canonical/
├── understanding/
├── expression/
├── rendering/
└── integration/
```

#### 1.2 Provider模式基础设施
```python
# 创建基础类
src/core/providers/base_provider.py
src/core/factories/provider_factory.py
src/core/module_loader.py
```

#### 1.3 事件系统增强
```python
# 完善EventBus
src/core/event_bus.py  # 增强事件路由和错误处理
```

### Phase 2: Layer 1-2 实现

#### 2.1 输入感知层(Layer 1)
**目标**：统一所有输入源接口

**实施步骤**：
1. 创建输入源基类
    ```python
    # src/perception/base_input.py（概念代码）
    class RawData:
        """原始数据基类"""
        # 包含：content、timestamp、source、metadata

    class InputSource:
        """输入源协议 - 所有输入源必须实现"""
        async def start(self) -> AsyncIterator[RawData]:
            """启动输入流，返回原始数据"""

        async def stop(self):
            """停止输入源"""
    ```

2. 迁移现有输入源：
    - `console_input` → `src/perception/text/console_input.py`
    - `bili_danmaku` → `src/perception/text/danmaku/bilibili.py`
    - `mock_danmaku` → `src/perception/text/danmaku/mock.py`
    - `stt` → `src/perception/audio/stt.py`

3. 创建输入源工厂（概念代码，完整实现见后续）
    ```python
    # src/perception/input_factory.py（概念代码）
    class InputFactory:
        """输入源工厂 - 动态选择输入源实现"""
        def create_input_source(self, provider: str, config: dict) -> InputSource:
            """创建输入源实例"""
    ```

#### 2.2 输入标准化层(Layer 2)
**目标**：所有输入统一转换为Text

**实施步骤**：
1. 创建标准化器接口（概念代码）
    ```python
    # src/normalization/base_normalizer.py（概念代码）
    class Normalizer:
        """标准化器协议 - 将原始数据转换为文本"""
        async def normalize(self, raw_data: RawData) -> str:
            """将原始数据转换为文本"""
    ```

2. 实现具体标准化器：
    - `TextNormalizer` - 文本标准化（清理、格式化）
    - `AudioToTextNormalizer` - 音频→文本（STT）
    - `ImageToTextNormalizer` - 图像→文本（VL模型）

3. 创建自动路由器（概念代码）
    ```python
    # src/normalization/auto_normalizer.py（概念代码）
    class AutoNormalizer:
        """自动标准化路由器 - 根据数据类型选择标准化器"""
        async def normalize(self, raw_data: RawData) -> str:
            """自动选择合适的标准化器"""
    ```

### Phase 3: Layer 3-4 实现

#### 3.1 中间表示层(Layer 3)
**目标**：统一内部消息格式

**实施步骤**：
1. 定义CanonicalMessage（概念代码）
    ```python
    # src/canonical/canonical_message.py（核心数据结构）
    class CanonicalMessage:
        """统一消息格式 - Layer 3的核心数据结构"""
        # 包含：text(文本)、metadata(来源/时间戳/用户)、context(对话上下文)

        @classmethod
        def from_text(cls, text: str, source: str, **metadata):
            """从文本创建消息"""

        def to_dict(self) -> dict:
            """转换为字典"""
    ```

2. 创建消息构建器（概念代码）
    ```python
    # src/canonical/message_builder.py
    class MessageBuilder:
        """消息构建器 - 便捷创建CanonicalMessage"""
        @staticmethod
        def create_from_text(text: str, source: str, **metadata) -> CanonicalMessage:
            """从文本创建消息"""

        @staticmethod
        def create_from_raw(raw_data: dict, **metadata) -> CanonicalMessage:
            """从原始数据创建消息"""
    ```

#### 3.2 语言理解层(Layer 4)
**目标**：语言理解与意图生成

**实施步骤**：
1. 合并语言理解功能：
    - `llm_text_processor` → 核心LLM处理
    - `emotion_judge` → 情感分析

2. 创建统一接口（概念代码）
    ```python
    # src/understanding/language_understanding.py（概念代码）
    class LanguageUnderstanding:
        """语言理解协议 - 理解消息并生成意图"""
        async def understand(self, message: CanonicalMessage) -> Intent:
            """理解消息并生成意图"""

        async def get_context(self, max_history: int = 10) -> dict:
            """获取上下文"""
    ```

3. Provider模式实现（概念代码）
    ```python
    # src/understanding/strategies/openai_llm_strategy.py（概念代码）
    class OpenAILLMStrategy:
        """OpenAI LLM实现"""
        async def initialize(self) -> bool:
            """初始化LLM客户端"""

        async def understand(self, message: CanonicalMessage) -> Intent:
            """调用LLM生成意图"""
    ```

### Phase 4: Layer 5-6 实现

#### 4.1 表现生成层(Layer 5)
**目标**：生成抽象表现参数

**实施步骤**：
1. **统一TTS模块**（重要）：
    ```python
    # src/expression/tts_module.py（概念代码）
    class UnifiedTTSModule:
        """统一TTS模块 - 替代3个插件"""
        async def initialize(self):
            """初始化默认TTS提供者"""

        async def synthesize(self, text: str) -> bytes:
            """合成语音"""

        async def switch_provider(self, new_provider: str):
            """动态切换TTS提供者"""
    ```

2. 创建表现参数对象（概念代码）
    ```python
    # src/expression/render_parameters.py（概念代码）
    class RenderParameters:
        """渲染参数 - Layer 5的输出格式"""
        # 包含：expressions(表情)、tts_text(语音)、subtitle_text(字幕)、hotkeys
    ```

3. 整合其他表现功能（概念代码）
    ```python
    # src/expression/expression_generator.py（概念代码）
    class ExpressionGenerator:
        """表现生成器 - 从意图生成渲染参数"""
        async def generate(self, intent: Intent) -> RenderParameters:
            """从意图生成渲染参数"""
    ```

#### 4.2 渲染呈现层(Layer 6)
**目标**：实际渲染输出

**实施步骤**：
1. 统一渲染器接口（概念代码）
    ```python
    # src/rendering/base_renderer.py（概念代码）
    class Renderer:
        """渲染器协议 - 所有渲染器必须实现"""
        async def render(self, parameters: RenderParameters):
            """渲染输出"""

        async def cleanup(self):
            """清理资源"""
    ```

2. 实现具体渲染器（概念代码）
    ```python
    # src/rendering/virtual_rendering/vts_renderer.py（概念代码）
    class VTSRenderer:
        """VTS渲染器 - 渲染到VTubeStudio"""
        async def initialize(self) -> bool:
            """连接VTS"""

        async def render(self, parameters: RenderParameters):
            """渲染表情、热键等"""
    ```

### Phase 5: Layer 7 实现

#### 5.1 外部集成层
**目标**：保留插件系统用于真正扩展

**保留插件**：
- 游戏集成：mainosaba, arknights, minecraft, maicraft
- 工具集成：screen_monitor, remote_stream, read_pingmu
- 硬件集成：dg_lab_service

**迁移到新位置**：
```
src/integration/game_integration/
src/integration/tools/
src/integration/hardware/
```

#### 5.2 Git迁移步骤（必须使用git mv）

**⚠️ 重要：所有文件移动必须使用git mv，禁止直接移动文件**

**示例：迁移游戏集成插件**
```bash
# 创建迁移分支
git checkout -b refactor/migrate-plugins

# 逐个迁移插件（使用git mv）
git mv src/plugins/mainosaba src/integration/game_integration/
git commit -m "refactor: migrate mainosaba to integration layer"

git mv src/plugins/minecraft src/integration/game_integration/
git commit -m "refactor: migrate minecraft to integration layer"

git mv src/plugins/obs_control src/integration/tools/
git commit -m "refactor: migrate obs_control to integration layer"

# 验证历史完整性
git log --follow src/integration/game_integration/mainosaba/
# 应该可以看到完整的提交历史，包括原始插件的历史

# 合并到主分支
git checkout main
git merge refactor/migrate-plugins
```

**批量迁移脚本（可选）**
```bash
# 创建迁移分支
git checkout -b refactor/migrate-plugins

# 迁移游戏集成插件
for plugin in mainosaba arknights minecraft maicraft; do
    git mv src/plugins/$plugin src/integration/game_integration/
    git commit -m "refactor: migrate $plugin to integration layer"
done

# 迁移工具集成插件
for plugin in screen_monitor remote_stream read_pingmu obs_control warudo vrchat; do
    git mv src/plugins/$plugin src/integration/tools/
    git commit -m "refactor: migrate $plugin to integration layer"
done

# 迁移硬件集成插件
git mv src/plugins/dg_lab_service src/integration/hardware/
git commit -m "refactor: migrate dg_lab_service to integration layer"
```

**验证历史完整性的命令**
```bash
# 查看特定文件的完整历史
git log --follow src/integration/game_integration/mainosaba/

# 查看所有迁移的提交历史
git log --oneline --follow src/integration/

# 验证历史完整性（应该看到原始插件的提交）
git log --follow --all --oneline -- src/integration/game_integration/
```

### Phase 6: 事件系统重构

#### 6.1 定义核心事件流
```python
# src/core/event_types.py（概念代码）
class EventData:
    """事件数据基类"""
    # 包含：event、timestamp、source、data

# 核心数据流事件
EVENT_DEFINITIONS = {
    "perception.raw_data": "RawData",                        # Layer 1 → Layer 2
    "normalization.text_ready": "Text",                      # Layer 2 → Layer 3
    "canonical.message_created": "CanonicalMessage",        # Layer 3 → Layer 4
    "understanding.intent_generated": "Intent",             # Layer 4 → Layer 5 ⭐
    "expression.parameters_generated": "RenderParameters",  # Layer 5 → Layer 6 ⭐
    "rendering.audio_played": "dict",
    "rendering.expression_applied": "dict",
    "rendering.subtitle_shown": "dict",
}
```

#### 6.2 迁移服务注册到EventBus
**重点迁移**：
| 原服务注册 | 新事件订阅/发布 |
|------------|-----------------|
| `get_service("vts_control")` | 订阅 `"expression.parameters_generated"` 事件 |
| `get_service("subtitle_service")` | 发布 `"rendering.subtitle_shown"` 事件 |
| `get_service("text_cleanup")` | 订阅 `"normalization.text_ready"` 事件 |
| `get_service("tts_service")` | 订阅 `"expression.parameters_generated"` 事件 |

### Phase 7: 配置系统重构

#### 7.1 简化配置结构
```toml
# 新配置格式示例
[perception]
text_input_provider = "bilibili"
audio_input_enabled = true

[perception.text_inputs.bilibili]
room_id = 123456

[understanding]
llm_provider = "openai"
model = "gpt-4"

[expression.tts]
default_provider = "edge"

[expression.tts.providers.edge]
voice = "zh-CN-XiaoxiaoNeural"

[expression.tts.providers.gptsovits]
host = "127.0.0.1"
port = 9880

[rendering]
virtual_renderer = "vts"
subtitle_enabled = true
```

#### 7.2 配置迁移工具（概念代码）
```python
# src/utils/config_migrator.py（概念代码）
class ConfigMigrator:
    """配置迁移器 - 自动迁移旧配置到新格式"""
    def migrate_to_new_format(self, old_config: dict) -> dict:
        """自动迁移旧配置到新格式"""

    def _migrate_tts_config(self, old_config: dict) -> dict:
        """迁移TTS配置"""
```

## 🔄 实施步骤详细指南

### 每个Layer的标准实施步骤

#### Step 1: 定义接口
```python
# 创建抽象基类，定义统一接口（概念代码）
class BaseLayer:
    """层级协议"""
    async def process(self, input_data: Any) -> Any:
        """处理数据"""
```

#### Step 2: 实现Provider
```python
# 为每个实现创建Provider类（概念代码）
class ConcreteProvider(BaseProvider):
    def __init__(self, config: dict):
        self.config = config

    async def process(self, input_data: Any) -> Any:
        # 具体实现
```

#### Step 3: 创建工厂
```python
# 创建工厂类支持动态选择（概念代码）
class LayerFactory:
    """工厂类 - 动态选择实现"""
    def create(self, provider: str, config: dict) -> BaseProvider:
        """创建Provider实例"""
```

#### Step 4: 集成事件系统
```python
# 在Layer中使用EventBus（概念代码）
class LayerModule:
    def __init__(self, event_bus, config: dict):
        self.event_bus = event_bus
        self.factory = LayerFactory()

        # 订阅输入事件
        self.event_bus.on(self.input_event, self.on_input)

    async def on_input(self, event_data: EventData):
        # 处理输入并发布输出事件
        result = await self.process(event_data.data)
        await self.event_bus.emit(self.output_event, result)
```

### 关键实施要点

#### 1. 事件命名规范
```python
# 事件命名：{layer}.{action}.{status}
"perception.raw_data"
"normalization.text_ready"
"understanding.intent_generated"
"expression.parameters_generated"
"rendering.audio_played"
```

#### 2. 错误处理方式（概念代码）
```python
# 每个Layer的错误处理
class LayerModule:
    async def process_with_error_handling(self, data):
        try:
            result = await self.process(data)
            await self.event_bus.emit(self.success_event, result)
        except Exception as e:
            self.logger.error(f"Layer处理失败: {e}")
            await self.event_bus.emit(self.error_event, {"error": str(e)})
```

#### 3. 配置热重载（概念代码）
```python
# 支持运行时配置更新
class LayerModule:
    async def reload_config(self, new_config: dict):
        self.config = new_config
        # 重新初始化Provider
        await self.provider.cleanup()
        self.provider = self.factory.create(self.config.get("provider"), self.config)
        await self.provider.initialize()
```

## ✅ 验证标准

### 每个Layer完成标准
- [ ] 接口定义完成，所有必需方法都有文档
- [ ] 至少一个具体实现可以工作
- [ ] 工厂模式可以动态选择实现
- [ ] 事件订阅/发布正常工作
- [ ] 配置可以正确加载和使用

### 整体验证标准
- [ ] 所有原有功能正常工作
- [ ] 新架构可以正常启动
- [ ] 性能没有明显下降
- [ ] 日志输出清晰可调试
- [ ] Provider模式支持运行时切换

## 📝 注意事项

### 开发原则
1. **先接口，后实现**：先定义清晰的接口，再写具体实现
2. **事件优先**：优先使用EventBus，避免直接依赖
3. **Provider解耦**：用Provider模式隔离不同实现
4. **工厂选择**：用工厂模式支持动态切换
5. **配置简化**：减少配置复杂度，提高可维护性

### 风险控制
1. **分步实施**：按Layer顺序，每步验证
2. **功能保持**：确保重构过程中功能不丢失
3. **错误隔离**：每层独立错误处理，不影响其他层
4. **配置兼容**：提供配置迁移工具
5. **日志完善**：详细日志便于问题定位
6. **Git历史保留**：⚠️ 所有文件移动必须使用`git mv`，禁止直接移动文件

## 🎯 预期成果

### 架构收益
- **依赖地狱消除**：EventBus完全替代服务注册
- **代码重复减少**：统一接口替代重复插件
- **配置简化**：配置行数减少40%以上
- **扩展性提升**：新增实现只需实现Provider接口

### 开发体验提升
- **启动顺序无关**：无依赖链，任意启动顺序
- **热切换支持**：运行时动态切换实现
- **调试友好**：清晰的事件流，易于定位问题
- **文档完善**：每层职责清晰，易于理解

这个实施计划提供了详细的分步重构指南，确保在1-2天内完成全面重构，同时保持功能完整性和架构清晰性。