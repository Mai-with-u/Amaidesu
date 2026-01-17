# Amaidesu 架构重构计划：基于数据流的7层架构设计

## 📋 核心问题

通过深入分析当前架构，发现以下关键问题：

### 1. 过度插件化导致"自我折磨"

**现状举例**：TTS功能有3个独立插件，通过配置切换

````
src/plugins/tts/           # Edge TTS
src/plugins/gptsovits_tts/ # GPT-SoVITS  
src/plugins/omni_tts/      # Omni TTS
````

**问题**：

- 同一功能重复实现，代码冗余
- 切换实现需要修改 `[plugins] enabled = [...]` 列表
- 配置分散在多个地方

### 2. 依赖地狱问题

**现状**：24个插件中有18个使用服务注册，形成复杂依赖链

```python
# 典型依赖链示例
vts_control_service = self.core.get_service("vts_control")
subtitle_service = self.core.get_service("subtitle_service")  
text_cleanup = self.core.get_service("text_cleanup")
```

**问题**：

- 启动顺序依赖（必须先启动被依赖的服务）
- "插件排列组合"调试困难
- 配置错误导致启动失败
- 难以单独测试插件

### 3. 模块定位模糊

**现状**：核心功能、可选扩展、测试工具都作为插件

**问题**：

- 插件系统承载了过多职责
- "伪插件"问题：console_input、keyword_action 实际无法禁用
- 不符合"插件=可拔插"的语义

## 🎯 重构目标

### 核心设计原则（源自设计讨论）

1. **消灭过度插件化**：核心功能不应是插件，而是模块
2. **统一接口收敛功能**：同一功能收敛到统一接口，用策略模式/工厂动态切换实现
3. **消除依赖地狱**：推广EventBus通信，替代服务注册模式
4. **按数据流组织架构**：音输入→语言推理→表情动作→虚拟渲染→直播推流
5. **驱动与渲染分离**：驱动层输出参数，渲染层只管渲染（换引擎不用重写）

## 🏗️ 7层架构设计

### 核心理念

**按AI VTuber数据处理的完整流程组织层级，每层有明确的输入和输出格式。**

- **不按技术模式("策略"、"工厂")组织目录**
- **每层输出格式统一且明确**
- **层级间单向依赖，消除循环耦合**

### 架构概览

```mermaid
graph TB
    subgraph "Layer 1: 输入感知层"
        Perception[获取外部原始数据<br/>麦克风/弹幕/控制台/屏幕]
    end

    subgraph "Layer 2: 输入标准化层"
        Normalization[格式转换,统一转换为Text<br/>音频→文本/图像→文本描述/文本→文本]
    end

    subgraph "Layer 3: 中间表示层"
        Canonical[统一消息格式<br/>创建CanonicalMessage对象]
    end

    subgraph "Layer 4: 语言理解层"
        Understanding[理解意图+生成回复<br/>LLM/NLP处理]
    end

    subgraph "Layer 5: 表现生成层"
        Expression[生成各种表现参数<br/>表情/语音/字幕]
    end

    subgraph "Layer 6: 渲染呈现层"
        Rendering[最终渲染输出<br/>虚拟形象/音频播放/视觉渲染]
    end

    subgraph "Layer 7: 外部集成层"
        Integration[第三方服务集成<br/>游戏/硬件/平台]
    end

    Perception -->|"Raw Data"| Normalization
    Normalization -->|"Text"| Canonical
    Canonical -->|"CanonicalMessage"| Understanding
    Understanding -->|"Intent"| Expression
    Expression -->|"Parameters"| Rendering
    Rendering -.事件通知.-> Integration

    style Perception fill:#e1f5ff
    style Normalization fill:#fff4e1
    style Canonical fill:#f3e5f5
    style Understanding fill:#ffe1f5
    style Expression fill:#e1ffe1
    style Rendering fill:#e1f5ff
    style Integration fill:#f5e1ff
```

### 7层架构详细设计

| 层级                | 英文名        | 输入格式         | 输出格式             | 核心职责          | 设计理由                                       |
| ------------------- | ------------- | ---------------- | -------------------- | ----------------- | ---------------------------------------------- |
| **1. 输入感知层**   | Perception    | -                | Raw Data             | 获取外部原始数据  | 按数据源(音频/文本/图像)分离输入源             |
| **2. 输入标准化层** | Normalization | Raw Data         | **Text**             | 统一转换为文本    | LLM只能处理文本，简化后续处理流程              |
| **3. 中间表示层**   | Canonical     | Text             | **CanonicalMessage** | 统一消息格式      | 标准化数据结构，易于扩展和传输                 |
| **4. 语言理解层**   | Understanding | CanonicalMessage | **Intent**           | 理解意图+生成回复 | AI VTuber的"大脑"，负责语言理解与生成          |
| **5. 表现生成层**   | Expression    | Intent           | **Parameters**       | 生成各种表现参数  | **驱动层只输出参数**，符合设计讨论中的分离原则 |
| **6. 渲染呈现层**   | Rendering     | Parameters       | **Frame/Stream**     | 最终渲染输出      | **渲染层只管渲染**，换引擎不用重写             |
| **7. 外部集成层**   | Integration   | -                | -                    | 第三方服务集成    | 保留插件系统，仅用于真正的扩展                 |

### 关键设计决策

#### 1. 统一转换为文本(Layer 2)

**决策**:所有输入统一转换为Text格式

**理由**:

- 简化后续处理流程
- LLM只能处理文本
- 图像/音频通过VL模型转换为文本描述
- 降低系统复杂度

#### 2. 驱动与渲染分离(Layer 5 & 6)

**设计初衷**："虽然都是虚拟形象，但**驱动层只输出参数，渲染层只管画图**。这都不分开，以后换个模型或者引擎难道要重写一遍？"

- **Layer 5 (Expression)**: 生成抽象的表现参数（表情参数、热键、TTS文本）
- **Layer 6 (Rendering)**: 接收参数进行实际渲染（VTS调用、音频播放、字幕显示）

#### 3. CanonicalMessage统一格式(Layer 3)

```python
from typing import TypedDict, Optional
from dataclasses import dataclass

@dataclass
class MessageMetadata(TypedDict):
    """消息元数据"""
    source: str
    timestamp: float
    user_id: Optional[str]
    user_name: Optional[str]

@dataclass
class ConversationContext:
    """对话上下文"""
    history: list[dict]
    current_turn: int

class CanonicalMessage:
    """统一消息格式"""
    def __init__(self):
        self.text: str = ""              # 文本内容(Layer 2输出)
        self.metadata: MessageMetadata = {}  # 元数据(来源、时间戳、用户等)
        self.context: Optional[ConversationContext] = None  # 对话上下文

    @classmethod
    def from_text(cls, text: str, source: str) -> "CanonicalMessage":
        """从文本创建消息"""
        msg = cls()
        msg.text = text
        msg.metadata = MessageMetadata(
            source=source,
            timestamp=time.time(),
            user_id=None,
            user_name=None
        )
        return msg
```

#### 4. Intent意图对象(Layer 4输出)

```python
from enum import Enum
from typing import TypedDict, List
from dataclasses import dataclass

class EmotionType(Enum):
    NEUTRAL = "neutral"
    HAPPY = "happy"
    SAD = "sad"
    ANGRY = "angry"
    SURPRISED = "surprised"

class Action(TypedDict):
    """动作"""
    action_type: str
    parameters: dict

@dataclass
class IntentMetadata(TypedDict):
    """意图元数据"""
    confidence: float
    processing_time: float

class Intent:
    """意图对象"""
    def __init__(self):
        self.original_text: str = ""        # 原始文本
        self.emotion: EmotionType = EmotionType.NEUTRAL  # 情感类型
        self.response_text: str = ""         # 回复文本
        self.actions: List[Action] = []      # 触发的动作
        self.metadata: IntentMetadata = {}     # 其他元数据
```

#### 5. RenderParameters参数对象(Layer 5输出)

```python
from typing import TypedDict, Optional
from dataclasses import dataclass

class ExpressionParameters(TypedDict):
    """表情参数"""
    expression_name: str
    value: float

class AudioParameters(TypedDict):
    """音频参数"""
    text: str
    voice: Optional[str]
    sample_rate: int

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
```

## 🔄 模块化策略：消灭插件化

### 策略模式+工厂模式设计

基于设计讨论中的要求："同一功能收敛到一个统一接口里，用策略模式或者工厂动态选实现不就行了"

#### 1. 统一接口定义

```python
from typing import Protocol, runtime_checkable, Any, Dict
from abc import ABC, abstractmethod

class Strategy(Protocol):
    """策略协议"""
    
    async def initialize(self) -> bool:
        """初始化策略"""
        ...
    
    async def process(self, input_data: Any) -> Any:
        """处理数据"""
        ...
    
    async def cleanup(self):
        """清理资源"""
        ...

@runtime_checkable
class BaseStrategy(ABC):
    """策略模式基类"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = self._get_logger()
    
    def _get_logger(self):
        """获取日志记录器"""
        import logging
        return logging.getLogger(f"Strategy.{self.__class__.__name__}")
    
    @abstractmethod
    async def initialize(self) -> bool:
        """初始化策略"""
        pass
    
    @abstractmethod
    async def process(self, input_data: Any) -> Any:
        """处理数据"""
        pass
    
    @abstractmethod
    async def cleanup(self):
        """清理资源"""
        pass
```

#### 2. 具体策略示例：TTS统一接口

```python
from typing import Protocol, runtime_checkable, List, Dict, Any
from dataclasses import dataclass

class TTSStrategy(Protocol):
    """TTS策略协议"""
    
    async def synthesize_speech(self, text: str, **kwargs) -> bytes:
        """合成语音，返回音频数据"""
        ...
    
    async def get_available_voices(self) -> List[Dict[str, Any]]:
        """获取可用语音列表"""
        ...

@runtime_checkable
class BaseTTSStrategy(BaseStrategy):
    """TTS策略抽象基类"""
    
    @abstractmethod
    async def synthesize_speech(self, text: str, **kwargs) -> bytes:
        """合成语音，返回音频数据"""
        pass
    
    @abstractmethod
    async def get_available_voices(self) -> List[Dict[str, Any]]:
        """获取可用语音列表"""
        pass
    
    def get_default_config(self) -> Dict[str, Any]:
        """获取默认配置"""
        return {}

# 具体实现
class EdgeTTSStrategy(BaseTTSStrategy):
    """Edge TTS策略实现"""
    
    async def initialize(self) -> bool:
        try:
            import edge_tts
            self.voice = self.config.get("voice", "zh-CN-XiaoxiaoNeural")
            self.logger.info(f"Edge TTS 初始化成功，语音: {self.voice}")
            return True
        except ImportError:
            self.logger.error("Edge TTS 依赖缺失")
            return False
    
    async def synthesize_speech(self, text: str, **kwargs) -> bytes:
        import edge_tts
        communicate = edge_tts.Communicate(text, self.voice)
        return await communicate.stream_to_buffer()
    
    async def get_available_voices(self) -> List[Dict[str, Any]]:
        # 实现获取Edge TTS可用语音
        pass
    
    def get_default_config(self) -> Dict[str, Any]:
        return {
            "voice": "zh-CN-XiaoxiaoNeural",
            "output_device_name": ""
        }

class GPTSoVITSStrategy(BaseTTSStrategy):
    """GPTSoVITS策略实现"""
    
    async def initialize(self) -> bool:
        try:
            from ...plugins.gptsovits_tts.plugin import TTSModel
            self.tts_model = TTSModel(self.config)
            self.tts_model.load_preset(self.config.get("pipeline", {}).get("default_preset", "default"))
            self.logger.info("GPTSoVITS 初始化成功")
            return True
        except Exception as e:
            self.logger.error(f"GPTSoVITS 初始化失败: {e}")
            return False
    
    async def synthesize_speech(self, text: str, **kwargs) -> bytes:
        return self.tts_model.tts(text, **kwargs)
    
    async def get_available_voices(self) -> List[Dict[str, Any]]:
        # 返回预设的语音配置
        presets = self.config.get("models", {}).get("presets", {})
        return [{"name": name, "description": preset.get("name", name)} 
                for name, preset in presets.items()]
    
    def get_default_config(self) -> Dict[str, Any]:
        return {
            "host": "127.0.0.1",
            "port": 9880,
            "sample_rate": 22050,
            "pipeline": {"default_preset": "default"}
        }
```

#### 3. 工厂模式动态选择

```python
from typing import Dict, Type, Optional, List
from abc import ABC, abstractmethod

class StrategyFactory(ABC):
    """策略工厂抽象基类"""
    
    @abstractmethod
    def create_strategy(self, name: str, config: Dict[str, Any]) -> Any:
        """创建策略实例"""
        pass
    
    @abstractmethod
    def get_available_strategies(self) -> List[str]:
        """获取可用策略列表"""
        pass
    
    @abstractmethod
    def get_default_strategy(self) -> Optional[str]:
        """获取默认策略"""
        pass

class TTSFactory(StrategyFactory):
    """TTS策略工厂"""
    
    def __init__(self):
        self._strategies: Dict[str, Type[BaseTTSStrategy]] = {
            "edge": EdgeTTSStrategy,
            "gptsovits": GPTSoVITSStrategy,
            "omni": OmniTTSStrategy  # 假设存在
        }
        self._default_strategy: Optional[str] = "edge"
    
    def create_strategy(self, name: str, config: Dict[str, Any]) -> BaseTTSStrategy:
        if name not in self._strategies:
            raise ValueError(f"未知TTS提供商: {name}")
        
        strategy_class = self._strategies[name]
        return strategy_class(config)
    
    def get_available_strategies(self) -> List[str]:
        return list(self._strategies.keys())
    
    def get_default_strategy(self) -> Optional[str]:
        return self._default_strategy
    
    def get_strategy_configs(self) -> Dict[str, Dict[str, Any]]:
        """获取所有策略的默认配置"""
        configs = {}
        for name in self.get_available_strategies():
            strategy = self.create_strategy(name, {})
            configs[name] = strategy.get_default_config()
        return configs
```

#### 4. 统一模块替代插件

```python
from typing import Optional, Dict, Any

class UnifiedTTSModule:
    """统一TTS模块，替代原来的3个TTS插件"""
    
    def __init__(self, config: Dict[str, Any]):
        self.factory = TTSFactory()
        self.default_tts_engine = config.get("default_engine", "edge")
        self.tts_engines = config.get("engines", {})
        
        # 当前活跃的TTS策略
        self.current_tts_strategy: Optional[BaseTTSStrategy] = None
    
    async def initialize(self):
        """初始化默认TTS策略"""
        engine_config = self.tts_engines.get(self.default_tts_engine, {})
        
        # 合并全局配置和引擎特定配置
        final_config = {
            **engine_config,
            "plugin_dir": getattr(self, "plugin_dir", ""),
            "core": getattr(self, "core", None)
        }
        
        strategy = await self._initialize_tts_strategy(self.default_tts_engine, final_config)
        if strategy:
            self.current_tts_strategy = strategy
            self.logger.info(f"TTS策略初始化成功: {self.default_tts_engine}")
        else:
            self.logger.error(f"TTS策略初始化失败: {self.default_tts_engine}")
    
    async def _initialize_tts_strategy(self, engine_name: str, config: Dict[str, Any]) -> Optional[BaseTTSStrategy]:
        """初始化指定TTS策略"""
        try:
            strategy = self.factory.create_strategy(engine_name, config)
            if await strategy.initialize():
                return strategy
            else:
                self.logger.error(f"策略初始化失败: {engine_name}")
                return None
        except Exception as e:
            self.logger.error(f"创建策略失败: {engine_name} - {e}")
            return None
    
    async def synthesize(self, text: str) -> bytes:
        """合成语音"""
        if not self.current_tts_strategy:
            raise RuntimeError("没有可用的TTS策略")
        return await self.current_tts_strategy.synthesize_speech(text)
    
    async def switch_engine(self, engine_name: str):
        """动态切换TTS引擎"""
        if engine_name not in self.tts_engines:
            self.logger.error(f"未知的TTS引擎: {engine_name}")
            return False
        
        if engine_name == self.default_tts_engine:
            self.logger.info("已经是当前引擎，无需切换")
            return True
        
        # 切换策略
        engine_config = self.tts_engines.get(engine_name, {})
        final_config = {
            **engine_config,
            "plugin_dir": getattr(self, "plugin_dir", ""),
            "core": getattr(self, "core", None)
        }
        
        new_strategy = await self._initialize_tts_strategy(engine_name, final_config)
        
        if new_strategy:
            # 清理旧策略
            if self.current_tts_strategy:
                await self.current_tts_strategy.cleanup()
            
            self.current_tts_strategy = new_strategy
            self.default_tts_engine = engine_name
            
            # 发送切换事件
            if hasattr(self, "event_bus"):
                await self.event_bus.emit("tts.engine_switched", {
                    "old_engine": self.default_tts_engine,
                    "new_engine": engine_name
                })
            
            return True
        else:
            self.logger.error(f"切换TTS引擎失败: {engine_name}")
            return False
    
    def get_available_engines(self) -> List[Dict[str, Any]]:
        """获取可用引擎列表"""
        engines = []
        for engine_name in self.factory.get_available_strategies():
            engines.append({
                "name": engine_name,
                "description": f"TTS Engine: {engine_name}",
                "is_current": engine_name == self.default_tts_engine
            })
        return engines
    
    async def cleanup(self):
        """清理资源"""
        if self.current_tts_strategy:
            await self.current_tts_strategy.cleanup()
```

### 配置简化

```toml
# 当前：分散在多个插件配置
[plugins]
enabled = ["tts"]  # 只能启用一个
[tts]
voice = "zh-CN-XiaoxiaoNeural"

# 重构后：统一配置，支持多实现
[expression.tts]
default_provider = "edge"

[expression.tts.providers.edge]
voice = "zh-CN-XiaoxiaoNeural"

[expression.tts.providers.gptsovits]  
host = "127.0.0.1"
port = 9880

[expression.tts.providers.omni]
api_key = "your_key"
```

## 🔄 事件驱动的并行架构

### EventBus完全替代服务注册

**核心目标**：消灭依赖地狱，所有模块间通信通过EventBus

#### 关键事件流定义

```python
from typing import TypedDict, Any

class EventData(TypedDict):
    """事件数据基类"""
    event: str
    timestamp: float
    source: str
    data: Dict[str, Any]

# 核心数据流事件
EVENT_DEFINITIONS = {
    # Layer 1 → Layer 2
    "perception.raw_data": Any,              # RawData
    
    # Layer 2 → Layer 3  
    "normalization.text_ready": str,            # Text
    
    # Layer 3 → Layer 4
    "canonical.message_created": "CanonicalMessage",  # CanonicalMessage
    
    # Layer 4 → Layer 5 ⭐ 核心事件
    "understanding.intent_generated": Intent,       # Intent
    
    # Layer 5 → Layer 6 ⭐ 核心事件
    "expression.parameters_generated": RenderParameters,  # RenderParameters
    
    # Layer 6 输出
    "rendering.audio_played": Dict[str, Any],    # 播放信息
    "rendering.expression_applied": Dict[str, Any], # 表情应用
    "rendering.subtitle_shown": Dict[str, Any],     # 字幕显示
}
```

#### EventBus通信模式

```python
from typing import Callable, Any, Dict

class EventBus:
    """事件总线"""
    
    def __init__(self):
        self._listeners: Dict[str, List[Callable]] = {}
        self._event_history: List[EventData] = []
    
    async def emit(self, event_name: str, data: Dict[str, Any]):
        """发布事件"""
        event_data = EventData(
            event=event_name,
            timestamp=time.time(),
            source=self._get_caller_source(),
            data=data
        )
        
        self._event_history.append(event_data)
        
        # 通知所有监听器
        listeners = self._listeners.get(event_name, [])
        for listener in listeners:
            try:
                await listener(event_data)
            except Exception as e:
                self.logger.error(f"事件监听器出错: {event_name} - {e}")
    
    def on(self, event_name: str, handler: Callable):
        """订阅事件"""
        if event_name not in self._listeners:
            self._listeners[event_name] = []
        self._listeners[event_name].append(handler)
    
    def _get_caller_source(self) -> str:
        """获取调用者来源"""
        import inspect
        frame = inspect.currentframe()
        if frame and frame.f_back:
            return frame.f_back.f_code.co_filename
        return "unknown"

# 发布者不关心谁在监听
class ExpressionModule:
    async def process_intent(self, intent: Intent):
        params = self.generate_parameters(intent)
        # ✅ 发布事件，不关心谁在监听
        await self.event_bus.emit("expression.parameters_generated", {
            "parameters": params,
            "source": "expression"
        })

# 订阅者不关心谁是发布者
class RenderingModule:
    def setup(self):
        # ✅ 订阅事件，不关心谁是发布者
        self.event_bus.on("expression.parameters_generated", self.on_parameters)
    
    async def on_parameters(self, event_data: EventData):
        params: RenderParameters = event_data.data["parameters"]
        await self.render(params)
```

### 消除服务注册的迁移

| 原服务注册                        | EventBus替代方案                              |
| --------------------------------- | --------------------------------------------- |
| `get_service("vts_control")`      | 监听 `"expression.parameters_generated"` 事件 |
| `get_service("subtitle_service")` | 发布 `"rendering.subtitle_shown"` 事件        |
| `get_service("text_cleanup")`     | 监听 `"normalization.text_ready"` 事件        |
| `get_service("tts_service")`      | 监听 `"expression.parameters_generated"` 事件 |

## 📁 新目录结构

````
amaidesu/
├── src/
│   ├── core/                              # 核心基础设施(保持不变)
│   │   ├── amaidesu_core.py               # 中央枢纽
│   │   ├── event_bus.py                   # 事件系统(主要通信方式)
│   │   ├── pipeline_manager.py            # 管道系统
│   │   ├── context_manager.py             # 上下文管理
│   │   ├── strategies/                    # 策略模式基类
│   │   ├── factories/                     # 工厂模式实现
│   │   └── module_loader.py              # 模块加载器
│   │
│   ├── perception/                         # 【Layer 1】输入感知层
│   │   ├── audio/
│   │   │   ├── microphone.py              # 麦克风输入
│   │   │   └── stream_audio.py            # 流音频输入
│   │   └── text/
│   │       ├── console_input.py           # 控制台输入
│   │       └── danmaku/                    # 弹幕输入
│   │           ├── base_danmaku.py         # 弹幕基类
│   │           ├── bilibili_danmaku.py     # B站弹幕
│   │           └── mock_danmaku.py         # 模拟弹幕
│   │
│   ├── normalization/                      # 【Layer 2】输入标准化层
│   │   ├── text_normalizer.py             # 文本标准化
│   │   ├── audio_to_text.py               # 音频→文本(STT)
│   │   ├── image_to_text.py               # 图像→文本(VL)
│   │   └── implementations/
│   │       ├── edge_stt.py
│   │       └── openai_vl.py
│   │
│   ├── canonical/                          # 【Layer 3】中间表示层
│   │   ├── canonical_message.py           # CanonicalMessage定义
│   │   ├── message_builder.py             # 消息构建器
│   │   └── context_attacher.py            # 上下文附加器
│   │
│   ├── understanding/                       # 【Layer 4】语言理解层
│   │   ├── base_llm.py                    # LLM接口
│   │   ├── intent_analyzer.py             # 意图分析
│   │   ├── emotion_detector.py            # 情感检测
│   │   └── implementations/
│   │       └── openai_llm.py
│   │
│   ├── expression/                          # 【Layer 5】表现生成层
│   │   ├── tts_module.py                  # 统一TTS模块(替代3个插件)
│   │   ├── expression_generator.py         # 表情生成器
│   │   ├── action_mapper.py               # 动作映射器
│   │   └── subtitle_planner.py            # 字幕规划器
│   │
│   ├── rendering/                           # 【Layer 6】渲染呈现层
│   │   ├── virtual_rendering/             # 虚拟渲染
│   │   │   ├── base_renderer.py
│   │   │   └── implementations/
│   │   │       ├── vts_renderer.py
│   │   │       └── obs_renderer.py
│   │   ├── audio_rendering/               # 音频渲染
│   │   │   ├── playback_manager.py
│   │   │   └── implementations/
│   │   │       ├── edge_tts.py
│   │   │       ├── gptsovits_tts.py
│   │   │       └── omni_tts.py
│   │   └── visual_rendering/              # 视觉渲染
│   │       ├── subtitle_renderer.py
│   │       └── sticker_renderer.py
│   │
│   └── integration/                         # 【Layer 7】外部集成层(保留插件系统)
│       ├── game_integration/               # 游戏集成
│       ├── tools/                          # 工具插件
│       └── hardware/                       # 硬件集成
│
├── config/
├── config-template.toml
└── main.py
````

## 🔌 插件系统重新定位

### 保留为插件的功能(8个)

| 插件类型      | 数量 | 保留理由                   |
| ------------- | ---- | -------------------------- |
| **游戏集成**  | 4个  | 真正的外部集成，需要插件化 |
| **工具/硬件** | 4个  | 边缘功能，可选扩展         |

### 迁移到7层架构的插件(16个)

| 原插件                | 迁移到层级 | 迁移方式                    |
| --------------------- | ---------- | --------------------------- |
| **TTS系列(3个)**      | Layer 5+6  | 统一为TTS模块，策略模式实现 |
| **弹幕输入系列(4个)** | Layer 1    | 统一接口，工厂模式选择      |
| **虚拟渲染系列(3个)** | Layer 6    | 统一渲染器接口              |
| **理解处理系列(2个)** | Layer 4    | 合并为语言理解模块          |

## ✅ 成功标准

### 技术指标

- ✅ 所有现有功能正常运行
- ✅ 配置文件行数减少40%以上
- ✅ 核心功能响应时间无增加
- ✅ 代码重复率降低30%以上
- ✅ **服务注册调用减少80%以上**
- ✅ **EventBus事件调用覆盖率90%以上**

### 架构指标

- ✅ 清晰的7层数据流架构
- ✅ 层级间依赖关系清晰(单向依赖)
- ✅ **EventBus为主要通信模式**
- ✅ **策略模式替代重复插件**
- ✅ **工厂模式支持动态切换**

## 📚 设计优势

### 1. 解决核心问题

| 问题         | 解决方案          | 效果                           |
| ------------ | ----------------- | ------------------------------ |
| 过度插件化   | 策略模式+工厂模式 | 同一功能统一接口，动态切换实现 |
| 依赖地狱     | EventBus通信      | 模块间松耦合，无启动顺序依赖   |
| 配置分散     | 统一配置结构      | 集中管理，配置复杂度降低       |
| 模块定位模糊 | 按数据流分层      | 职责清晰，易于理解和维护       |

### 2. 符合设计初衷

- ✅ **"同一功能收敛到一个统一接口"**：策略模式实现
- ✅ **"用策略模式或者工厂动态选实现"**：工厂模式支持
- ✅ **"驱动层只输出参数，渲染层只管渲染"**：Layer 5&6分离
- ✅ **"以后换个模型或者引擎难道要重写一遍"**：通过策略切换解决

### 3. 架构优势

1. **数据流清晰**: 7层架构，每层职责明确
2. **消除重复**: 统一接口替代重复插件实现
3. **松耦合**: EventBus通信，模块间无直接依赖
4. **易扩展**: 新实现只需实现策略接口并注册
5. **易维护**: 分层清晰，问题定位准确

**本文档为Amaidesu项目的完整架构重构计划，聚焦于消灭过度插件化和依赖地狱，建立清晰的数据流架构。**