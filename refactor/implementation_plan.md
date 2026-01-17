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
git mv src/plugins/minecraft src/extensions/minecraft
git commit -m "refactor: migrate minecraft to extension"

# 查看完整历史（包括移动）
git log --follow src/extensions/minecraft/
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

#### 1.1 创建目录结构
```
src/
├── core/
│   ├── provider.py              # Provider接口（公共API）
│   ├── extension.py             # Extension接口
│   └── extension_loader.py      # 扩展加载器
│
├── perception/                  # Layer 1: 输入感知
├── normalization/               # Layer 2: 输入标准化
├── canonical/                   # Layer 3: 中间表示
├── understanding/               # Layer 4: 语言理解
├── expression/                  # Layer 5: 表现生成
├── rendering/                   # Layer 6: 渲染呈现
└── extensions/                  # Layer 8: 扩展系统
    ├── minecraft/               # 内置扩展
    ├── warudo/                  # 内置扩展
    ├── dg_lab/                  # 内置扩展
    └── user_extensions/         # 用户扩展
        └── installed/
```

#### 1.2 Provider接口（公共API）
```python
# 创建Provider接口
src/core/provider.py
```

#### 1.3 事件系统增强
```python
# 完善EventBus
src/core/event_bus.py  # 增强事件路由和错误处理
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

### Phase 5: 扩展系统实现

#### 5.1 Provider接口（公共API）

**目标**：定义Provider接口，社区开发者可以继承。

**创建Provider接口**：

```python
# src/core/provider.py
from typing import Protocol, AsyncIterator, Any, List
from src.core.event_bus import EventBus

class RawData:
    """原始数据基类"""
    content: Any
    timestamp: float
    source: str
    metadata: dict

class InputProvider(Protocol):
    """输入Provider接口 - 社区可继承"""
    
    async def start(self) -> AsyncIterator[RawData]:
        """
        启动输入流
        
        Yields:
            RawData: 原始数据
        """
        ...
    
    async def stop(self):
        """停止输入源"""
        ...
    
    async def cleanup(self):
        """清理资源"""
        ...

class OutputProvider(Protocol):
    """输出Provider接口 - 社区可继承"""
    
    async def setup(self, event_bus: EventBus):
        """设置Provider（订阅EventBus）"""
        ...
    
    async def render(self, parameters: Any):
        """
        渲染输出
        
        Args:
            parameters: 渲染参数（类型取决于具体Provider）
        """
        ...
    
    async def cleanup(self):
        """清理资源"""
        ...

# Provider类型
Provider = InputProvider | OutputProvider
```

#### 5.2 Extension接口

**目标**：定义Extension接口，社区开发者通过Extension聚合Provider。

```python
# src/core/extension.py
from typing import Protocol, List
from src.core.provider import Provider
from src.core.event_bus import EventBus

class Extension(Protocol):
    """扩展接口 - 聚合多个Provider的完整功能"""
    
    async def setup(self, event_bus: EventBus, config: dict) -> List[Provider]:
        """
        初始化扩展
        
        Args:
            event_bus: 事件总线
            config: 配置
        
        Returns:
            初始化好的Provider列表
        """
        ...
    
    async def cleanup(self):
        """清理资源"""
        ...
    
    def get_info(self) -> dict:
        """
        获取扩展信息
        
        Returns:
            扩展元数据
        """
        return {
            "name": "ExtensionName",
            "version": "1.0.0",
            "author": "Author",
            "description": "Extension description",
            "category": "game/hardware/software",
            "api_version": "1.0"
        }
```

#### 5.3 ExtensionLoader（扩展加载器）

**目标**：实现扩展加载器，管理内置扩展和用户扩展。

```python
# src/core/extension_loader.py
"""扩展加载器"""

class ExtensionLoader:
     """扩展加载器"""
     
     def __init__(self, event_bus: EventBus):
         self.event_bus = event_bus
         self.builtin_extensions: dict[str, Extension] = {}
         self.user_extensions: dict[str, Extension] = {}
         self.providers: List[Provider] = []
         
         # ⭐ 将根目录添加到sys.path（关键！）
         self._setup_sys_path()
     
     def _setup_sys_path(self):
         """设置Python路径（重要！）"""
         # 确保可以导入根目录extensions/下的用户扩展
         project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
         extensions_dir = os.path.join(project_root, "extensions")
         
         if extensions_dir not in sys.path:
             sys.path.insert(0, extensions_dir)
             logger.debug(f"添加到sys.path: {extensions_dir}")
    
    async def load_all(self, config: dict):
        """加载所有扩展"""
        # 1. 加载内置扩展（自动加载）
        await self._load_builtin_extensions(config)
        
        # 2. 加载用户扩展（根据配置加载）
        await self._load_user_extensions(config)
    
    async def _load_builtin_extensions(self, config: dict):
        """加载内置扩展（官方）"""
        builtin_extensions = [
            "minecraft",
            "warudo",
            "dg_lab"
        ]
        
        for ext_name in builtin_extensions:
            try:
                ext_config = config.get(f"extensions.{ext_name}", {})
                await self._load_extension(ext_name, "builtin", ext_config)
            except Exception as e:
                logger.error(f"加载内置扩展失败: {ext_name} - {e}")
    
    async def _load_user_extensions(self, config: dict):
        """加载用户扩展（自动扫描）"""
        # ⭐ 自动扫描根目录的extensions/文件夹
        if not os.path.exists("extensions"):
            logger.debug("用户扩展目录不存在: extensions/")
            return
        
        # 获取所有扩展目录
        ext_names = []
        for item in os.listdir("extensions"):
            ext_path = os.path.join("extensions", item)
            
            # 检查是否是目录
            if not os.path.isdir(ext_path):
                continue
            
            # 检查是否包含__init__.py
            init_file = os.path.join(ext_path, "__init__.py")
            if not os.path.exists(init_file):
                continue
            
            # 检查配置中是否显式禁用
            ext_config = config.get(f"extensions.{item}", {})
            if ext_config.get("enabled", True) == False:
                logger.info(f"扩展已禁用: {item}")
                continue
            
            ext_names.append((item, ext_config))
        
        # 加载所有找到的用户扩展
        for ext_name, ext_config in ext_names:
            try:
                await self._load_extension(ext_name, "user", ext_config)
            except Exception as e:
                logger.error(f"加载用户扩展失败: {ext_name} - {e}")
    
    async def _load_extension(self, extension_name: str,
                              extension_type: str, config: dict):
        """加载扩展（内置或用户）"""
        # 1. 根据类型选择导入路径
        if extension_type == "builtin":
            module_path = f"src.extensions.{extension_name}"
        else:  # user extension
            module_path = f"extensions.{extension_name}"
        
        try:
            # 2. 导入扩展
            module = __import__(
                module_path,
                fromlist=[f"{extension_name.title()}Extension"]
            )
            extension_class = getattr(module, f"{extension_name.title()}Extension")
            
            # 3. 检查API版本
            extension = extension_class()
            info = extension.get_info()
            self._check_api_version(info.get("api_version", "1.0"))
            
            # 4. 初始化扩展
            providers = await extension.setup(self.event_bus, config)
            
            # 5. 注册Provider
            self.providers.extend(providers)
            
            # 6. 注册扩展
            if extension_type == "builtin":
                self.builtin_extensions[extension_name] = extension
            else:
                self.user_extensions[extension_name] = extension
            
            logger.info(f"扩展加载成功: {extension_name} ({extension_type})")
            
        except ImportError as e:
            if extension_type == "user":
                # 用户扩展：提供友好提示
                raise ImportError(
                    f"用户扩展导入失败: {extension_name}\n"
                    f"错误: {e}\n"
                    f"请检查: \n"
                    f"  1. 扩展是否安装在根目录的extensions/文件夹中\n"
                    f"  2. 扩展目录结构是否正确\n"
                    f"  3. 扩展是否包含__init__.py文件\n"
                    f"  4. 扩展目录名是否为: {extension_name}"
                )
            else:
                # 内置扩展：直接抛出
                raise ImportError(
                    f"内置扩展导入失败: {extension_name} ({module_path})\n"
                    f"错误: {e}"
                )
    
    async def unload_extension(self, extension_name: str):
        """卸载扩展"""
        # 1. 查找扩展
        extension = self.builtin_extensions.get(extension_name) or \
                   self.user_extensions.get(extension_name)
        
        if not extension:
            raise ValueError(f"扩展不存在: {extension_name}")
        
        # 2. 清理资源
        await extension.cleanup()
        
        # 3. 移除扩展
        self.builtin_extensions.pop(extension_name, None)
        self.user_extensions.pop(extension_name, None)
        
        logger.info(f"扩展卸载成功: {extension_name}")
```

#### 5.4 示例：Minecraft扩展

**目标**：实现Minecraft扩展，作为内置扩展。

```python
# src/extensions/minecraft/__init__.py
"""Minecraft扩展"""
from typing import List
from .providers import MinecraftEventProvider, MinecraftCommandProvider
from src.core.extension import Extension
from src.core.provider import Provider
from src.core.event_bus import EventBus

class MinecraftExtension(Extension):
    """Minecraft扩展 - 聚合Minecraft的所有能力"""
    
    def __init__(self):
        self.providers: List[Provider] = []
        self.host = "localhost"
        self.port = 25565
    
    async def setup(self, event_bus: EventBus, config: dict) -> List[Provider]:
        """初始化Minecraft扩展"""
        # ✅ 一处配置
        self.host = config.get("host", "localhost")
        self.port = config.get("port", 25565)
        
        self.providers = []
        
        # 输入Provider（Layer 1）
        if config.get("events_enabled", True):
            event_provider = MinecraftEventProvider({
                "host": self.host,
                "port": self.port
            })
            await event_provider.setup(event_bus)
            self.providers.append(event_provider)
        
        # 输出Provider（Layer 6）
        if config.get("commands_enabled", True):
            command_provider = MinecraftCommandProvider({
                "host": self.host,
                "port": self.port
            })
            await command_provider.setup(event_bus)
            self.providers.append(command_provider)
        
        return self.providers
    
    async def cleanup(self):
        """清理资源"""
        await asyncio.gather(*[p.cleanup() for p in self.providers])
    
    def get_info(self) -> dict:
        return {
            "name": "Minecraft",
            "version": "1.0.0",
            "author": "Official",
            "description": "Minecraft游戏集成扩展",
            "category": "game",
            "api_version": "1.0"
        }

# 内部Provider（对开发者透明）
# src/extensions/minecraft/providers/event_provider.py
class MinecraftEventProvider(InputProvider):
    """Minecraft事件输入Provider"""
    
    def __init__(self, config: dict):
        self.host = config.get("host")
        self.port = config.get("port")
        self.game_client = GameClient(self.host, self.port)
    
    async def start(self):
        """启动输入流"""
        async for event in self.game_client.events():
            yield RawData(
                content=event,
                source="game.minecraft",
                timestamp=time.time()
            )
    
    async def stop(self):
        """停止输入源"""
        await self.game_client.close()
    
    async def cleanup(self):
        """清理资源"""
        await self.game_client.close()

# src/extensions/minecraft/providers/command_provider.py
class MinecraftCommandProvider(OutputProvider):
    """Minecraft命令输出Provider"""
    
    def __init__(self, config: dict):
        self.host = config.get("host")
        self.port = config.get("port")
        self.game_client = GameClient(self.host, self.port)
    
    async def setup(self, event_bus: EventBus):
        """订阅事件"""
        self.event_bus = event_bus
        event_bus.on("expression.parameters_generated", self.on_parameters)
    
    async def on_parameters(self, event):
        """处理渲染参数"""
        params = event.data
        if params.minecraft_commands:
            await self.game_client.send_commands(params.minecraft_commands)
    
    async def render(self, parameters):
        """渲染输出（备用接口）"""
        if parameters.minecraft_commands:
            await self.game_client.send_commands(parameters.minecraft_commands)
    
    async def cleanup(self):
        """清理资源"""
        await self.game_client.close()
```

#### 5.5 目录结构

```
src/
├── core/
│   ├── extension_loader.py      # ⭐ 扩展加载器
│   ├── extension.py             # 扩展接口
│   └── provider.py              # ⭐ Provider接口（公共API）
│
├── extensions/                  # ⭐ 内置扩展（官方）
│   ├── minecraft/               # Minecraft扩展
│   │   ├── __init__.py
│   │   │   └── MinecraftExtension
│   │   └── providers/           # 内部Provider
│   │       ├── event_provider.py
│   │       └── command_provider.py
│   ├── warudo/                  # Warudo扩展
│   └── dg_lab/                  # DG-Lab扩展
│
└── user_extensions/             # ⭐ 用户扩展（社区）
    └── installed/              # 用户安装的扩展
        ├── genshin/             # 原神扩展（用户安装）
        │   ├── __init__.py
        │   │   └── GenshinExtension
        │   └── providers/
        └── mygame/              # 其他扩展（用户安装）
```
```
src/
├── core/
│   ├── extension_loader.py      # ⭐ 扩展加载器
│   ├── extension.py             # 扩展接口
│   └── provider.py              # ⭐ Provider接口（公共API）
│
├── extensions/                  # ⭐ 内置扩展（官方）
│   ├── minecraft/               # Minecraft扩展
│   │   ├── __init__.py
│   │   │   └── MinecraftExtension
│   │   └── providers/           # 内部Provider
│   │       ├── event_provider.py
│   │       └── command_provider.py
│   ├── warudo/                  # Warudo扩展
│   └── dg_lab/                  # DG-Lab扩展
│
└── user_extensions/             # ⭐ 用户扩展（社区）
    └── installed/              # 用户安装的扩展
        ├── genshin/             # 原神扩展（用户安装）
        │   ├── __init__.py
        │   │   └── GenshinExtension
        │   └── providers/
        └── mygame/              # 其他扩展（用户安装）
```

**项目根目录**：
```
Amaidesu/                        # 项目根目录
├── src/                         # 源代码
│   ├── core/
│   └── extensions/              # 内置扩展
│
├── extensions/                   # ⭐ 用户扩展（根目录，.gitignore）
│   ├── genshin/                 # 用户扩展1
│   │   ├── __init__.py
│   │   │   └── GenshinExtension
│   │   └── providers/
│   └── mygame/                  # 用户扩展2
│       ├── __init__.py
│       │   └── MyGameExtension
│       └── providers/
│
├── config.toml
├── main.py
└── README.md
```

#### 5.6 Git迁移步骤（必须使用git mv）

**⚠️ 重要：所有文件移动必须使用git mv，禁止直接移动文件**

**迁移内置扩展**：

```bash
# 创建迁移分支
git checkout -b refactor/migrate-extensions

# 迁移Minecraft插件为扩展
git mv src/plugins/minecraft src/extensions/minecraft
git commit -m "refactor: migrate minecraft plugin to extension"

# 迁移Warudo插件
git mv src/plugins/warudo src/extensions/warudo
git commit -m "refactor: migrate warudo plugin to extension"

# 迁移DG-Lab插件
git mv src/plugins/dg_lab_service src/extensions/dg_lab
git commit -m "refactor: migrate dg_lab_service plugin to extension"

# 迁移其他内置扩展
git mv src/plugins/mainosaba src/extensions/mainosaba
git commit -m "refactor: migrate mainosaba plugin to extension"

git mv src/plugins/maicraft src/extensions/maicraft
git commit -m "refactor: migrate maicraft plugin to extension"

# 验证历史完整性
git log --follow src/extensions/minecraft/
# 应该可以看到完整的提交历史，包括原始插件的历史

# 合并到主分支
git checkout main
git merge refactor/migrate-extensions
```

**批量迁移脚本（可选）**：

```bash
# 创建迁移分支
git checkout -b refactor/migrate-extensions

# 迁移所有内置扩展
for plugin in minecraft warudo dg_lab mainosaba maicraft; do
    git mv src/plugins/$plugin src/extensions/$plugin
    git commit -m "refactor: migrate $plugin plugin to extension"
done

# 验证历史完整性
git log --follow --all --oneline -- src/extensions/

# 合并到主分支
git checkout main
git merge refactor/migrate-extensions
```

**验证历史完整性的命令**：

```bash
# 查看特定扩展的完整历史
git log --follow src/extensions/minecraft/

# 查看所有迁移的提交历史
git log --oneline --follow src/extensions/

# 验证历史完整性（应该看到原始插件的提交）
git log --follow --all --oneline -- src/extensions/minecraft/
```

#### 5.7 配置示例

```toml
# 内置扩展（官方）
[extensions.minecraft]
enabled = true
host = "localhost"
port = 25565
events_enabled = true
commands_enabled = true

[extensions.warudo]
enabled = true
host = "localhost"
port = 50051
events_enabled = true
commands_enabled = true
rendering_enabled = true

[extensions.dg_lab]
enabled = true
device_id = "DG-001"
sensor_enabled = true
actuator_enabled = true

# 用户扩展（社区）
# ✅ 自动扫描：所有extensions/目录下的扩展自动加载
# ✅ 无需配置：安装后直接可用
# ⚠️ 禁用扩展：设置enabled = false

[extensions.genshin]
enabled = false  # 显式禁用（可选）
api_url = "https://genshin-api.example.com"
events_enabled = true

[extensions.mygame]
# enabled = true  # 不设置或设置为true，默认启用
api_url = "https://mygame-api.example.com"
```

#### 5.8 .gitignore配置

**目的**：排除用户扩展目录，避免纳入版本控制。

```gitignore
# Amaidesu

# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python

# 用户扩展（不纳入版本控制）
extensions/
!extensions/.gitkeep  # 保留.gitkeep文件

# 配置
config.toml

# 日志
*.log

# 其他...
```

**.gitkeep文件**：

```bash
# 创建.gitkeep文件，保持extensions/目录
mkdir -p extensions
touch extensions/.gitkeep
git add extensions/.gitkeep
git commit -m "chore: add extensions/.gitkeep"
```

### 用户扩展安装指南

**方式1：从GitHub克隆**

```bash
# 1. 进入项目根目录
cd Amaidesu

# 2. 克隆扩展到extensions/目录
git clone https://github.com/xxx/genshin-extension.git extensions/genshin

# 3. 运行程序（自动识别）
python main.py
# 日志显示：✅ 扩展加载成功: genshin
```

**方式2：下载后复制**

```bash
# 1. 下载扩展ZIP包
# 2. 解压到extensions/目录
unzip genshin-extension.zip -d extensions/

# 3. 重命名目录（如果需要）
mv extensions/genshin-extension extensions/genshin

# 4. 运行程序（自动识别）
python main.py
```

**方式3：手动创建**

```bash
# 1. 创建扩展目录
mkdir -p extensions/my-custom-extension

# 2. 创建__init__.py
cat > extensions/my-custom-extension/__init__.py << 'EOF'
from .providers import MyCustomProvider

class MyCustomExtension(Extension):
    async def setup(self, event_bus, config):
        providers = [MyCustomProvider(config)]
        for provider in providers:
            await provider.setup(event_bus)
        return providers
    
    async def cleanup(self):
        pass
    
    def get_info(self):
        return {
            "name": "MyCustom",
            "version": "1.0.0",
            "author": "You",
            "description": "My custom extension",
            "category": "custom",
            "api_version": "1.0"
        }
EOF

# 3. 创建providers目录
mkdir -p extensions/my-custom-extension/providers

# 4. 创建Provider
cat > extensions/my-custom-extension/providers/my_provider.py << 'EOF'
from src.core.provider import InputProvider

class MyCustomProvider(InputProvider):
    async def start(self):
        yield RawData(content="hello", source="custom")
EOF

# 5. 运行程序（自动识别）
python main.py
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