# Amaidesu 重构实施计划

## 🎯 实施原则

### 核心目标
1. **全面重构**：1-2天内完成，不考虑向后兼容
2. **消灭插件化**：核心功能全部模块化
3. **EventBus优先**：用事件系统替代服务注册
4. **策略模式**：统一接口，工厂动态选择

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

#### 1.2 策略模式基础设施
```python
# 创建基础类
src/core/strategies/base_strategy.py
src/core/factories/strategy_factory.py
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
   # src/perception/base_input.py
   from typing import Protocol, AsyncIterator, TypedDict

   class RawData(TypedDict):
       """原始数据基类"""
       content: Any
       timestamp: float
       source: str
       metadata: dict

   class InputSource(Protocol):
       """输入源协议"""
       
       async def start(self) -> AsyncIterator[RawData]:
           """启动输入流,返回原始数据"""
           ...
       
       async def stop(self):
           """停止输入源"""
           ...
       
       def get_source_type(self) -> str:
           """获取输入源类型"""
           ...
   ```

2. 迁移现有输入源：
   - `console_input` → `src/perception/text/console_input.py`
   - `bili_danmaku` → `src/perception/text/danmaku/bilibili.py`
   - `mock_danmaku` → `src/perception/text/danmaku/mock.py`
   - `stt` → `src/perception/audio/stt.py`

3. 创建输入源工厂：
   ```python
   # src/perception/input_factory.py
   from typing import Dict, Any, Protocol
   from src.core.factories.strategy_factory import StrategyFactory

   class InputFactory(StrategyFactory):
       """输入源工厂"""
       
       def __init__(self):
           super().__init__()
           self._register_all_inputs()
       
       def _register_all_inputs(self):
           """注册所有输入源"""
           # 注册文本输入
           self.register_strategy("console", ConsoleInputStrategy, is_default=True)
           self.register_strategy("bilibili", BilibiliDanmakuStrategy)
           self.register_strategy("mock", MockDanmakuStrategy)
           
           # 注册音频输入
           self.register_strategy("stt", STTStrategy)
       
       def create_input_source(self, provider: str, config: Dict[str, Any]) -> InputSource:
           """创建输入源实例"""
           return self.create_strategy(provider, config)
   ```

#### 2.2 输入标准化层(Layer 2)
**目标**：所有输入统一转换为Text

**实施步骤**：
1. 创建标准化器接口：
   ```python
   # src/normalization/base_normalizer.py
   from typing import Protocol
   from src.perception.base_input import RawData

   class Normalizer(Protocol):
       """标准化器协议"""
       
       async def normalize(self, raw_data: RawData) -> str:
           """将原始数据转换为文本"""
           ...
   ```

2. 实现具体标准化器：
   ```python
   # src/normalization/text_normalizer.py
   from typing import Dict, Any
   from src.core.strategies.base_strategy import BaseStrategy

   class TextNormalizer(BaseStrategy):
       """文本标准化器"""
       
       async def initialize(self) -> bool:
           self.clean_rules = self.config.get("clean_rules", [])
           self.logger.info("文本标准化器初始化成功")
           return True
       
       async def normalize(self, raw_data: RawData) -> str:
           """文本标准化处理"""
           if raw_data["source"] != "text":
               return ""
           
           text = raw_data["content"]
           
           # 应用清理规则
           for rule in self.clean_rules:
               pattern = rule.get("pattern")
               replacement = rule.get("replacement", "")
               text = re.sub(pattern, replacement, text)
           
           return text.strip()

   # src/normalization/audio_to_text.py
   class AudioToTextNormalizer(BaseStrategy):
       """音频→文本转换器(STT)"""
       
       async def initialize(self) -> bool:
           try:
               from ...normalization.implementations.edge_stt import EdgeSTTEngine
               self.stt_engine = EdgeSTTEngine(self.config)
               await self.stt_engine.initialize()
               return True
           except Exception as e:
               self.logger.error(f"STT引擎初始化失败: {e}")
               return False
       
       async def normalize(self, raw_data: RawData) -> str:
           if raw_data["source"] != "audio":
               return ""
           
           # 调用STT识别
           text = await self.stt_engine.recognize(raw_data["content"])
           return text
   ```

3. 自动路由标准化：
   ```python
   # src/normalization/auto_normalizer.py
   from src.normalization.base_normalizer import Normalizer

   class AutoNormalizer:
       """自动标准化路由器"""
       
       def __init__(self, config: Dict[str, Any]):
           self.factory = NormalizationFactory()
           self.logger = self._get_logger()
       
       async def normalize(self, raw_data: RawData) -> str:
           """根据数据类型自动选择标准化器"""
           source_type = raw_data["source"]
           
           # 根据源类型选择标准化器
           if source_type == "text":
               normalizer = self.factory.create_normalizer("text", self.config)
           elif source_type == "audio":
               normalizer = self.factory.create_normalizer("audio", self.config)
           elif source_type == "image":
               normalizer = self.factory.create_normalizer("image", self.config)
           else:
               self.logger.warning(f"未知的数据类型: {source_type}")
               return ""
           
           return await normalizer.normalize(raw_data)
   ```

### Phase 3: Layer 3-4 实现

#### 3.1 中间表示层(Layer 3)
**目标**：统一内部消息格式

**实施步骤**：
1. 定义CanonicalMessage：
   ```python
   # src/canonical/canonical_message.py
   from typing import TypedDict, Optional, Protocol
   from dataclasses import dataclass

   @dataclass
   class MessageMetadata(TypedDict):
       """消息元数据"""
       source: str
       timestamp: float
       user_id: Optional[str] = None
       user_name: Optional[str] = None
       platform: str = "unknown"
       room_id: Optional[int] = None

   @dataclass
   class ConversationContext(TypedDict):
       """对话上下文"""
       history: list[dict]
       current_turn: int
       max_history: int

   class CanonicalMessage:
       """统一消息格式"""
       
       def __init__(self):
           self.text: str = ""              # 文本内容(Layer 2输出)
           self.metadata: MessageMetadata = MessageMetadata(
               source="",
               timestamp=0.0
           )  # 元数据
           self.context: Optional[ConversationContext] = None  # 对话上下文
       
       @classmethod
       def from_text(cls, text: str, source: str, **metadata) -> "CanonicalMessage":
           """从文本创建消息"""
           msg = cls()
           msg.text = text
           msg.metadata = MessageMetadata(
               source=source,
               timestamp=time.time(),
               **metadata
           )
           return msg
       
       @classmethod
       def from_dict(cls, data: dict) -> "CanonicalMessage":
           """从字典创建消息"""
           msg = cls()
           msg.text = data.get("text", "")
           msg.metadata = MessageMetadata(**data.get("metadata", {}))
           
           # 处理上下文
           if "context" in data:
               msg.context = ConversationContext(**data["context"])
           
           return msg
       
       def to_dict(self) -> dict:
           """转换为字典"""
           return {
               "text": self.text,
               "metadata": self.metadata,
               "context": self.context
           }
   ```

2. 创建消息构建器：
   ```python
   # src/canonical/message_builder.py
   from typing import Dict, Any, Optional
   from src.canonical.canonical_message import CanonicalMessage, MessageMetadata

   class MessageBuilder:
       """消息构建器"""
       
       @staticmethod
       def create_from_text(
           text: str,
           source: str,
           user_id: Optional[str] = None,
           user_name: Optional[str] = None,
           **kwargs
       ) -> CanonicalMessage:
           """从文本创建消息"""
           return CanonicalMessage.from_text(
               text=text,
               source=source,
               user_id=user_id,
               user_name=user_name,
               **kwargs
           )
       
       @staticmethod
       def create_from_raw(
           raw_data: Dict[str, Any],
           **metadata
       ) -> CanonicalMessage:
           """从原始数据创建消息"""
           return CanonicalMessage.from_dict({
               "text": raw_data.get("content", ""),
               "metadata": {
                   "source": raw_data.get("source", "unknown"),
                   "timestamp": raw_data.get("timestamp", time.time()),
                   **metadata
               }
           })
   ```

#### 3.2 语言理解层(Layer 4)
**目标**：语言理解与意图生成

**实施步骤**：
1. 合并语言理解功能：
   - `llm_text_processor` → 核心LLM处理
   - `emotion_judge` → 情感分析

2. 创建统一接口：
   ```python
   # src/understanding/language_understanding.py
   from typing import Protocol, Optional
   from src.canonical.canonical_message import CanonicalMessage
   from src.canonical.intent_object import Intent

   class LanguageUnderstanding(Protocol):
       """语言理解协议"""
       
       async def understand(self, message: CanonicalMessage) -> Intent:
           """理解消息并生成意图"""
           ...
       
       async def get_context(self, max_history: int = 10) -> dict:
           """获取上下文"""
           ...
   ```

3. 策略模式实现：
   ```python
   # src/understanding/strategies/openai_llm_strategy.py
   from typing import Dict, Any
   from src.core.strategies.base_strategy import BaseStrategy
   from src.canonical.intent_object import Intent, EmotionType

   class OpenAILLMStrategy(BaseStrategy):
       """OpenAI LLM策略"""
       
       async def initialize(self) -> bool:
           try:
               from openai import AsyncOpenAI
               self.client = AsyncOpenAI(
                   api_key=self.config.get("api_key"),
                   base_url=self.config.get("base_url", "https://api.openai.com/v1/")
               )
               self.model = self.config.get("model", "gpt-4")
               self.logger.info(f"OpenAI LLM 初始化成功，模型: {self.model}")
               return True
           except Exception as e:
               self.logger.error(f"OpenAI LLM 初始化失败: {e}")
               return False
       
       async def understand(self, message: CanonicalMessage) -> Intent:
           """理解消息并生成意图"""
           try:
               # 构建提示词
               prompt = self._build_prompt(message)
               
               # 调用LLM
               response = await self.client.chat.completions.create(
                   model=self.model,
                   messages=[
                       {"role": "system", "content": self.config.get("system_prompt", "")},
                       {"role": "user", "content": prompt}
                   ],
                   temperature=self.config.get("temperature", 0.7)
               )
               
               response_text = response.choices[0].message.content
               
               # 创建意图对象
               intent = Intent()
               intent.original_text = message.text
               intent.response_text = response_text
               intent.emotion = self._analyze_emotion(response_text)
               intent.metadata = {
                   "model": self.model,
                   "tokens_used": response.usage.total_tokens
               }
               
               return intent
           
           except Exception as e:
               self.logger.error(f"LLM理解失败: {e}")
               # 返回默认意图
               intent = Intent()
               intent.original_text = message.text
               intent.response_text = "抱歉，我无法理解。"
               intent.emotion = EmotionType.NEUTRAL
               return intent
       
       def _build_prompt(self, message: CanonicalMessage) -> str:
           """构建提示词"""
           prompt_parts = []
           
           # 添加上下文
           if message.context:
               for hist_msg in message.context.history[-5:]:
                   prompt_parts.append(f"{hist_msg.get('role', 'user')}: {hist_msg.get('text', '')}")
           
           # 添加当前消息
           prompt_parts.append(f"用户: {message.text}")
           
           return "\n".join(prompt_parts)
       
       def _analyze_emotion(self, text: str) -> EmotionType:
           """分析情感（简单版本）"""
           # 这里可以使用更复杂的情感分析
           positive_words = ["开心", "哈哈", "棒", "喜欢"]
           negative_words = ["难过", "伤心", "不喜欢", "讨厌"]
           
           if any(word in text for word in positive_words):
               return EmotionType.HAPPY
           elif any(word in text for word in negative_words):
               return EmotionType.SAD
           
           return EmotionType.NEUTRAL
   ```

### Phase 4: Layer 5-6 实现

#### 4.1 表现生成层(Layer 5)
**目标**：生成抽象表现参数

**实施步骤**：
1. **统一TTS模块**（重要）：
   ```python
   # src/expression/tts_module.py
   from typing import Optional, Dict, Any, List
   from src.core.strategies.base_strategy import BaseStrategy
   from src.core.factories.tts_factory import TTSFactory

   class UnifiedTTSModule:
       """统一TTS模块，替代原来的3个TTS插件"""
       
       def __init__(self, config: Dict[str, Any]):
           self.factory = TTSFactory()
           self.default_provider = config.get("default_provider", "edge")
           self.provider_configs = config.get("providers", {})
           
           # 当前活跃的TTS策略
           self.current_tts_strategy: Optional[BaseStrategy] = None
           
           self.logger = self._get_logger()
       
       async def initialize(self):
           """初始化默认TTS策略"""
           config = self.provider_configs.get(self.default_provider, {})
           self.current_tts_strategy = self.factory.create_strategy(self.default_provider, config)
           
           if await self.current_tts_strategy.initialize():
               self.logger.info(f"TTS策略初始化成功: {self.default_provider}")
           else:
               self.logger.error(f"TTS策略初始化失败: {self.default_provider}")
       
       async def synthesize(self, text: str) -> bytes:
           """合成语音"""
           if not self.current_tts_strategy:
               raise RuntimeError("没有可用的TTS策略")
           
           return await self.current_tts_strategy.synthesize_speech(text)
       
       async def switch_provider(self, new_provider: str):
           """动态切换TTS提供商"""
           if new_provider not in self.provider_configs:
               self.logger.error(f"未知的TTS提供商: {new_provider}")
               return False
           
           if new_provider == self.default_provider:
               self.logger.info("已经是当前提供商，无需切换")
               return True
           
           # 切换策略
           config = self.provider_configs.get(new_provider, {})
           new_strategy = self.factory.create_strategy(new_provider, config)
           
           if await new_strategy.initialize():
               # 清理旧策略
               if self.current_tts_strategy:
                   await self.current_tts_strategy.cleanup()
               
               self.current_tts_strategy = new_strategy
               self.default_provider = new_provider
               
               # 发送切换事件
               if hasattr(self, "event_bus"):
                   await self.event_bus.emit("tts.provider_switched", {
                       "old_provider": self.default_provider,
                       "new_provider": new_provider
                   })
               
               return True
           else:
               self.logger.error(f"切换TTS提供商失败: {new_provider}")
               return False
       
       def get_available_providers(self) -> List[Dict[str, Any]]:
           """获取可用提供商列表"""
           providers = []
           for provider_name in self.factory.get_available_strategies():
               providers.append({
                   "name": provider_name,
                   "description": f"TTS Provider: {provider_name}",
                   "is_current": provider_name == self.default_provider
               })
           return providers
       
       async def cleanup(self):
           """清理资源"""
           if self.current_tts_strategy:
               await self.current_tts_strategy.cleanup()
   ```

2. 创建表现参数对象：
   ```python
   # src/expression/render_parameters.py
   from typing import TypedDict, Optional
   from dataclasses import dataclass

   @dataclass
   class ExpressionParameters(TypedDict):
       """表情参数"""
       expression_name: str
       value: float

   @dataclass
   class AudioParameters(TypedDict):
       """音频参数"""
       text: str
       voice: Optional[str]
       sample_rate: int

   @dataclass
   class VisualParameters(TypedDict):
       """视觉参数"""
       subtitle_text: Optional[str]
       subtitle_duration: Optional[float]
       show_duration: float

   @dataclass
   class RenderParameters:
       """渲染参数"""
       
       def __init__(self):
           # 表情参数
           self.expressions: dict[str, float] = {}  # {"MouthSmile": 1.0}
           
           # 音频参数
           self.tts_text: Optional[str] = None
           self.tts_voice: Optional[str] = None
           
           # 视觉参数
           self.subtitle_text: Optional[str] = None
           self.subtitle_duration: Optional[float] = None
           
           # 热键触发
           self.hotkeys: List[str] = []
       
       def to_dict(self) -> dict:
           """转换为字典"""
           return {
               "expressions": self.expressions,
               "tts_text": self.tts_text,
               "tts_voice": self.tts_voice,
               "subtitle_text": self.subtitle_text,
               "subtitle_duration": self.subtitle_duration,
               "hotkeys": self.hotkeys
           }
   ```

3. 整合其他表现功能：
   ```python
   # src/expression/expression_generator.py
   from typing import Dict, Any
   from src.canonical.intent_object import Intent, EmotionType
   from src.expression.render_parameters import RenderParameters

   class ExpressionGenerator:
       """表现生成器"""
       
       def __init__(self, config: Dict[str, Any]):
           self.emotion_map = config.get("emotion_map", {})
           self.tts_enabled = config.get("tts_enabled", True)
           self.subtitle_enabled = config.get("subtitle_enabled", True)
           self.logger = self._get_logger()
       
       async def generate(self, intent: Intent) -> RenderParameters:
           """从意图生成渲染参数"""
           params = RenderParameters()
           
           # 生成表情参数
           params.expressions = self._map_emotion_to_expressions(intent.emotion)
           
           # TTS参数
           if self.tts_enabled:
               params.tts_text = intent.response_text
           
           # 字幕参数
           if self.subtitle_enabled:
               params.subtitle_text = intent.response_text
           
           # 热键
           params.hotkeys = self._map_emotion_to_hotkeys(intent.emotion)
           
           return params
       
       def _map_emotion_to_expressions(self, emotion: EmotionType) -> Dict[str, float]:
           """映射情感到表情参数"""
           return self.emotion_map.get(emotion.value, {})
       
       def _map_emotion_to_hotkeys(self, emotion: EmotionType) -> List[str]:
           """映射情感到热键"""
           hotkey_map = {
               EmotionType.HAPPY: ["HappyHotkey"],
               EmotionType.SAD: ["SadHotkey"],
               EmotionType.ANGRY: ["AngryHotkey"],
               EmotionType.SURPRISED: ["SurprisedHotkey"]
           }
           return hotkey_map.get(emotion, [])
   ```

#### 4.2 渲染呈现层(Layer 6)
**目标**：实际渲染输出

**实施步骤**：
1. 统一渲染器接口：
   ```python
   # src/rendering/base_renderer.py
   from typing import Protocol
   from src.expression.render_parameters import RenderParameters

   class Renderer(Protocol):
       """渲染器协议"""
       
       async def render(self, parameters: RenderParameters):
           """渲染输出"""
           ...
       
       async def cleanup(self):
           """清理资源"""
           ...
   ```

2. 实现具体渲染器：
   ```python
   # src/rendering/virtual_rendering/vts_renderer.py
   from typing import Dict, Any
   from src.core.strategies.base_strategy import BaseStrategy
   from src.expression.render_parameters import RenderParameters

   class VTSRenderer(BaseStrategy):
       """VTS渲染器"""
       
       async def initialize(self) -> bool:
           try:
               from vtube_studio import VTuberStudio
               self.vts_client = VTuberStudio()
               await self.vts_client.connect()
               self.logger.info("VTS渲染器初始化成功")
               return True
           except Exception as e:
               self.logger.error(f"VTS渲染器初始化失败: {e}")
               return False
       
       async def render(self, parameters: RenderParameters):
           """渲染到VTS"""
           # 设置表情参数
           for exp_name, value in parameters.expressions.items():
               await self.vts_client.set_parameter(exp_name, value)
           
           # 触发热键
           for hotkey in parameters.hotkeys:
               await self.vts_client.trigger_hotkey(hotkey)
           
           # 同步口型（如果有TTS）
           if parameters.tts_text and hasattr(self, "audio_duration"):
               await self.vts_client.set_parameter("MouthOpen", self.audio_duration)
       
       async def cleanup(self):
           """清理VTS连接"""
           if hasattr(self, "vts_client"):
               await self.vts_client.disconnect()
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

### Phase 6: 事件系统重构

#### 6.1 定义核心事件流
```python
# src/core/event_types.py
from typing import TypedDict, Protocol, Any

class EventData(TypedDict):
    """事件数据基类"""
    event: str
    timestamp: float
    source: str
    data: Any

class EventHandler(Protocol):
    """事件处理器协议"""
    
    async def __call__(self, event_data: EventData):
        """处理事件"""
        ...

# 核心数据流事件
EVENT_DEFINITIONS = {
    # Layer 1 → Layer 2
    "perception.raw_data": Any,              # RawData
    
    # Layer 2 → Layer 3  
    "normalization.text_ready": str,            # Text
    
    # Layer 3 → Layer 4
    "canonical.message_created": "CanonicalMessage",  # CanonicalMessage
    
    # Layer 4 → Layer 5 ⭐ 核心事件
    "understanding.intent_generated": "Intent",       # Intent
    
    # Layer 5 → Layer 6 ⭐ 核心事件
    "expression.parameters_generated": "RenderParameters",  # RenderParameters
    
    # Layer 6 输出
    "rendering.audio_played": dict,
    "rendering.expression_applied": dict,
    "rendering.subtitle_shown": dict,
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
# 新配置格式
[perception]
text_input_provider = "bilibili"
audio_input_enabled = true

[perception.text_inputs.bilibili]
room_id = 123456

[perception.text_inputs.mock]
enabled = true
messages_per_minute = 5

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
audio_renderer = "edge_tts"
subtitle_enabled = true
```

#### 7.2 配置迁移工具
```python
# src/utils/config_migrator.py
from typing import Dict, Any

class ConfigMigrator:
    """配置迁移器"""
    
    def __init__(self):
        self.logger = self._get_logger()
    
    def migrate_to_new_format(self, old_config: Dict[str, Any]) -> Dict[str, Any]:
        """自动迁移旧配置到新格式"""
        migrated = {}
        
        # 迁移插件配置
        plugins_section = old_config.get("plugins", {})
        
        # 处理TTS插件迁移
        if "tts" in plugins_section or "gptsovits_tts" in plugins_section:
            migrated["expression"] = self._migrate_tts_config(old_config)
        
        # 处理输入插件迁移
        if any(key in plugins_section for key in ["console_input", "bili_danmaku"]):
            migrated["perception"] = self._migrate_input_config(old_config)
        
        # 处理解析插件迁移
        if "llm_text_processor" in plugins_section:
            migrated["understanding"] = self._migrate_llm_config(old_config)
        
        return migrated
    
    def _migrate_tts_config(self, old_config: Dict[str, Any]) -> Dict[str, Any]:
        """迁移TTS配置"""
        return {
            "tts": {
                "default_provider": "edge",
                "providers": {
                    "edge": old_config.get("plugins", {}).get("tts", {}),
                    "gptsovits": old_config.get("plugins", {}).get("gptsovits_tts", {}),
                    "omni": old_config.get("plugins", {}).get("omni_tts", {})
                }
            }
        }
    
    def _migrate_input_config(self, old_config: Dict[str, Any]) -> Dict[str, Any]:
        """迁移输入配置"""
        return {
            "text_input_provider": "bilibili",
            "text_inputs": {
                "bilibili": old_config.get("plugins", {}).get("bili_danmaku", {}),
                "mock": old_config.get("plugins", {}).get("mock_danmaku", {})
            }
        }
    
    def _migrate_llm_config(self, old_config: Dict[str, Any]) -> Dict[str, Any]:
        """迁移LLM配置"""
        return {
            "llm_provider": "openai",
            "model": "gpt-4"
        }
```

## 🔄 实施步骤详细指南

### 每个Layer的标准实施步骤

#### Step 1: 定义接口
```python
# 创建抽象基类，定义统一接口
from typing import Protocol, runtime_checkable

@runtime_checkable
class BaseLayer(Protocol):
    """层级协议"""
    
    async def process(self, input_data: Any) -> Any:
        """处理数据"""
        ...
```

#### Step 2: 实现策略
```python
# 为每个实现创建策略类
class ConcreteStrategy(BaseStrategy):
    def __init__(self, config: Dict[str, Any]):
        self.config = config
    
    async def process(self, input_data: Any) -> Any:
        # 具体实现
        pass
```

#### Step 3: 创建工厂
```python
# 创建工厂类支持动态选择
class LayerFactory(StrategyFactory):
    def __init__(self):
        self._strategies = {
            "implementation1": ConcreteStrategy1,
            "implementation2": ConcreteStrategy2,
        }
    
    def create(self, provider: str, config: Dict[str, Any]) -> BaseStrategy:
        strategy_class = self._strategies.get(provider)
        if not strategy_class:
            raise ValueError(f"Unknown provider: {provider}")
        return strategy_class(config)
```

#### Step 4: 集成事件系统
```python
# 在Layer中使用EventBus
class LayerModule:
    def __init__(self, event_bus, config: Dict[str, Any]):
        self.event_bus = event_bus
        self.factory = LayerFactory()
        
        # 订阅输入事件
        self.event_bus.on(self.input_event, self.on_input)
        
        # 发布输出事件
        self.output_event = self.output_event_name
    
    async def on_input(self, event_data: EventData):
        # 处理输入
        result = await self.process(event_data.data)
        
        # 发布输出
        await self.event_bus.emit(self.output_event, {
            "timestamp": time.time(),
            "source": self.__class__.__name__,
            "data": result
        })
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

#### 2. 错误处理策略
```python
# 每个Layer的错误处理
class LayerModule:
    async def process_with_error_handling(self, data):
        try:
            result = await self.process(data)
            await self.event_bus.emit(self.success_event, result)
        except Exception as e:
            self.logger.error(f"Layer处理失败: {e}")
            await self.event_bus.emit(self.error_event, {
                "error": str(e),
                "timestamp": time.time()
            })
```

#### 3. 配置热重载
```python
# 支持运行时配置更新
class LayerModule:
    async def reload_config(self, new_config: Dict[str, Any]):
        self.config = new_config
        # 重新初始化策略
        await self.strategy.cleanup()
        self.strategy = self.factory.create(
            self.config.get("provider"), 
            self.config
        )
        await self.strategy.initialize()
```

## ✅ 验证标准

### 每个Layer完成标准
- [ ] 接口定义完成，所有必需方法都有文档
- [ ] 至少一个具体实现可以工作
- [ ] 工厂模式可以动态选择实现
- [ ] 事件订阅/发布正常工作
- [ ] 配置可以正确加载和使用

### 整体验证标准
- [ ] 所有现有功能都可以正常工作
- [ ] 配置简化后可以正常启动
- [ ] 事件系统替代了所有服务注册
- [ ] 策略模式支持运行时切换
- [ ] 无循环依赖，启动顺序无关

## 🚀 快速开始检查清单

### 实施前准备
- [ ] 备份现有代码
- [ ] 确认理解了所有现有功能
- [ ] 准备了测试数据

### 实施中检查
- [ ] 每完成一个Layer就进行功能测试
- [ ] 确保事件正确订阅和发布
- [ ] 验证配置格式正确

### 实施后验证
- [ ] 所有原有功能正常工作
- [ ] 新架构可以正常启动
- [ ] 性能没有明显下降
- [ ] 日志输出清晰可调试

## 📝 注意事项

### 开发原则
1. **先接口，后实现**：先定义清晰的接口，再写具体实现
2. **事件优先**：优先使用EventBus，避免直接依赖
3. **策略解耦**：用策略模式隔离不同实现
4. **工厂选择**：用工厂模式支持动态切换
5. **配置简化**：减少配置复杂度，提高可维护性

### 风险控制
1. **分步实施**：按Layer顺序，每步验证
2. **功能保持**：确保重构过程中功能不丢失
3. **错误隔离**：每层独立错误处理，不影响其他层
4. **配置兼容**：提供配置迁移工具
5. **日志完善**：详细日志便于问题定位

## 🎯 预期成果

### 架构收益
- **依赖地狱消除**：EventBus完全替代服务注册
- **代码重复减少**：统一接口替代重复插件
- **配置简化**：配置行数减少40%以上
- **扩展性提升**：新增实现只需实现策略接口

### 开发体验提升
- **启动顺序无关**：无依赖链，任意启动顺序
- **热切换支持**：运行时动态切换实现
- **调试友好**：清晰的事件流，易于定位问题
- **文档完善**：每层职责清晰，易于理解

这个实施计划提供了详细的分步重构指南，确保在1-2天内完成全面重构，同时保持功能完整性和架构清晰性。