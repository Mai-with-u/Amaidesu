# 事件数据契约 - 当前方式 vs 更好的方式

> **目的**: 探索并评估当前 EventBus 数据契约方式，提出可能的改进方案
> **创建日期**: 2026-01-31

---

## 📊 当前方式：@dataclass + 文档约定

### 1. 当前实现总结

**数据类型定义**：
```python
# src/core/data_types/raw_data.py
@dataclass
class RawData:
    content: Any
    source: str
    data_type: str
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)
    preserve_original: bool = False
    original_data: Optional[Any] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    data_ref: Optional[str] = None

# src/core/data_types/normalized_text.py
@dataclass
class NormalizedText:
    text: str
    metadata: Dict[str, Any]
    data_ref: Optional[str] = None

# src/expression/render_parameters.py
@dataclass
class ExpressionParameters:
    tts_text: str = ""
    tts_enabled: bool = True
    subtitle_text: str = ""
    subtitle_enabled: bool = True
    expressions: Dict[str, float] = field(default_factory=dict)
    expressions_enabled: bool = True
    hotkeys: List[str] = field(default_factory=list)
    hotkeys_enabled: bool = True
    actions: List[Dict[str, Any]] = field(default_factory=list)
    actions_enabled: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)
    priority: int = 100
    timestamp: float = field(default_factory=time.time)

# src/understanding/intent.py
@dataclass
class Intent:
    original_text: str
    emotion: EmotionType
    response_text: str
    actions: List[IntentAction]
    metadata: Dict[str, Any]
    timestamp: float = field(default_factory=time.time)
```

**事件发布方式**：
```python
# 发布事件（当前方式）
await event_bus.emit(
    "perception.raw_data.generated",
    {"data": raw_data, "source": raw_data.source},
    source="InputLayer"
)

# 事件处理器（当前方式）
async def handler(event_name: str, event_data: Dict[str, Any], source: str):
    data = event_data.get("data")
    source = event_data.get("source")
    
    # 使用类型注解和 IDE 智能提示
    if isinstance(data, RawData):
        text = data.content
        source = data.source
```

**文档约定（在类文档字符串中）**：
```python
class InputProvider(ABC):
    """
    输入Provider抽象基类
    
    事件约定：
        - 发布事件: event_bus.emit("perception.raw_data.generated", {...})
        - 数据格式: {"data": RawData, "source": str}
        
    RawData 结构：
        content: Any              # 数据内容
        source: str               # 数据源（如 "console_input"）
        data_type: str           # 数据类型（如 "text"）
        timestamp: float           # Unix时间戳（秒）
        metadata: Dict[str, Any]      # 元数据
        preserve_original: bool     # 是否保留原始数据
        original_data: Optional[Any] = None  # 原始数据
        metadata: Dict[str, Any]      # 元数据
        data_ref: Optional[str] = None    # 数据引用
    """
```

### 2. 当前方式的优缺点

| 方面 | 优势 | 缺点 |
|------|------|------|
| **@dataclass** | ✅ 类型注解完整<br>✅ IDE 自动补全<br>✅ 易于序列化(to_dict()<br>✅ Python 3.7+ 内置 | ❌ 无验证功能<br>❌ 无法定义必需字段<br>❌ 默认值复杂（field_factory） |
| **文档约定** | ✅ 清晰的说明<br>✅ 与代码在一起 | ❌ 容易过时<br>❌ 代码和文档可能不同步<br>❌ IDE 不自动检查文档 |
| **字典传递** | ✅ 灵活简单<br>✅ 无额外依赖 | ❌ 魔魔法字符串<br>❌ 无类型检查<br>❌ 容易传错字段名 |

---

## 🚀 改进方案对比

### 方案 1：Pydantic Model（强烈推荐）⭐⭐⭐⭐⭐

**核心思想**：使用 Pydantic 进行数据验证，结合类型注解和自动文档生成。

**实现方式**：
```python
from pydantic import BaseModel, Field, validator, field_validator
from typing import Dict, Any, Optional
from datetime import datetime

class RawDataModel(BaseModel):
    """
    原始数据模型 - Layer 1 输出
    
    验证规则：
    - source: 必须是非空字符串
    - data_type: 必须是支持的类型
    - timestamp: 必须是未来的时间戳
    """
    
    source: str = Field(..., description="数据源标识符")
    data_type: str = Field(..., description="数据类型")
    content: Any = Field(..., description="数据内容")
    timestamp: float = Field(..., ge=time.time(), description="Unix 时间戳（秒）")
    
    preserve_original: bool = False
    original_data: Optional[Any] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    data_ref: Optional[str] = None
    
    class Config:
        """配置类"""
        supported_data_types: List[str] = ["text", "audio", "image", "json", "event"]
    
    supported_data_types = ["text", "audio", "image", "json", "event"]
    
    @field_validator('data_type')
    @classmethod
    def validate_data_type(cls, v):
        if v not in cls.Config.supported_data_types:
            raise ValueError(f"不支持的 data_type: {v}")
        return v

    @model_validator(mode='after')
    def validate_timestamp(cls, v):
        if v < time.time() - 86400:  # 1 天前
            raise ValueError("时间戳不能是过去的时间")
        return v
```

**发布事件**：
```python
# 发布事件
raw_data = RawDataModel(
    source="console_input",
    data_type="text",
    content="Hello",
    timestamp=time.time()
)

await event_bus.emit(
    "perception.raw_data.generated",
    raw_data.model_dump(mode='json'),  # 序列化为 JSON
    raw_data.dict(),               # 或转换为字典
    "InputLayer"
)
```

**订阅事件**：
```python
async def handler(event_name: str, event_data: dict, source: str):
    try:
        # 方式1: 从 JSON 反序列化
        raw_data = RawDataModel.model_validate_json(event_data.get("data"))
    except ValidationError as e:
        # 方式2: 从字典创建（不会触发验证）
        raw_data = RawDataModel(**event_data.get("data"))
    
    text = raw_data.content
    source = raw_data.source
```

**测试验证**：
```python
# 测试正常情况
raw_data = RawDataModel(source="test", data_type="text", content="Hello")
assert raw_data.source == "test"
raw_data.validate_model({"data": raw_data.model_dump()})

# 测试验证（应该抛出异常）
try:
    RawDataModel(source="", data_type="invalid_type", content="Hello")
    except ValueError as e:
    print(f"验证失败: {e}")

# 测试时间戳验证
try:
    RawDataModel(
        source="test",
        data_type="text",
        content="Hello",
        timestamp=time.time() - 100000  # 24 小时前
    )
except ValueError as e:
    print(f"时间戳验证失败: {e}")
```

**优势**：
- ✅ **自动验证**：发布时验证数据，防止错误数据传播
- ✅ **类型安全**：编译时和运行时都检查
- ✅ **IDE 友好**：自动补全、类型提示、文档生成
- ✅ **文档自动生成**：`model.schema()` 生成 JSON Schema
- ✅ **默认值清晰**：Field() 语法比 field_factory 更简洁
- ✅ **测试友好**：Pytest-Pydantic 插件提供专门支持
- ✅ **错误信息详细**：Pydantic 提供清晰的验证错误

**劣势**：
- ❌ 增加依赖：需要安装 Pydantic
- ❌ 学习曲线：需要学习 Pydantic 语法
- ❌ 运行时开销：验证有轻微性能影响

---

### 方案 2：TypedDict（类型化字典）⭐⭐⭐

**核心思想**：Python 3.9+ 内置的 TypedDict，无需额外依赖。

**实现方式**：
```python
from typing import TypedDict, Required

# 定义事件数据类型
RawDataEvent = TypedDict("RawDataEvent", {
    "data": Required[object],
    "source": Required[str],
    "timestamp": Required[float],
})

NormalizedTextEvent = TypedDict("NormalizedTextEvent", {
    "normalized": Required[object],
    "source": Required[str],
})

# 定义数据结构
class RawDataStructure:
    source: str
    data_type: str
    timestamp: float

# 类型工厂函数
def create_raw_data_event(source: str, data_type: str, content: Any) -> RawDataEvent:
    return RawDataEvent(
        data={"content": content, "data_type": data_type, "timestamp": time.time()},
        source=source
    )

# 发布事件
raw_data_event = create_raw_data_event("console_input", "text", "Hello")
await event_bus.emit(
    "perception.raw_data.generated",
    raw_data_event,
    "InputLayer"
)
```

**订阅事件**：
```python
async def handler(event_name: str, event_data: RawDataEvent, source: str):
    # 类型注解让 IDE 提供智能提示
    data = event_data["data"]
    source = event_data["source"]
    
    # 访问嵌套字段
    content = data["content"]
    timestamp = data["timestamp"]
    
    # 如果需要结构化访问
    raw_data_structure = RawDataStructure(**data)
```

**优势**：
- ✅ 无额外依赖
- ✅ 类型安全：IDE 提供智能提示
- ✅ 可序列化为 JSON：`json.dumps(event_data)`
- ✅ 可用于 IDE 自动补全
- ✅ 测试友好：可以创建类型化的测试数据

**劣势**：
- ❌ 验证能力有限：只能做类型检查，无业务规则验证
- ❌ 必需字段用 Required，可选字段用 Optional
- ❌ 无法定义字段约束（如枚举值范围）

---

### 方案 3：事件 Schema 注册表⭐⭐⭐⭐

**核心思想**：提前在启动时注册所有事件的数据 Schema，EventBus 验证发布的数据是否符合 Schema。

**实现方式**：
```python
from dataclasses import dataclass

class EventSchema:
    """事件 Schema 注册表"""
    
    _schemas: Dict[str, type] = {}
    
    @classmethod
    def register(cls, event_name: str, schema: type):
        """注册事件的数据类型"""
        cls._schemas[event_name] = schema
        cls.logger.info(f"注册事件 Schema: {event_name} -> {schema}")
    
    @classmethod
    def validate(cls, event_name: str, data: dict) -> bool:
        """验证事件数据是否符合 Schema"""
        schema = cls._schemas.get(event_name)
        if not schema:
            cls.logger.warning(f"事件 {event_name} 没有 Schema，跳过验证")
            return True
        
        # 使用 dataclass 创建实例进行验证
        try:
            schema(**data)
            return True
        except (TypeError, ValueError) as e:
            cls.logger.error(f"事件数据验证失败 ({event_name}): {e}")
            return False
    
    @classmethod
    def get_schema(cls, event_name: str) -> Optional[type]:
        """获取事件的 Schema 类型"""
        return cls._schemas.get(event_name)

# 在启动时注册所有事件
EventSchema.register("perception.raw_data.generated", RawData)
EventSchema.register("normalization.text.ready", NormalizedText)
EventSchema.register("understanding.intent_generated", Intent)
EventSchema.register("expression.parameters_generated", ExpressionParameters)

# EventBus 中的验证
async def emit(self, event_name: str, data: Any, ...):
    if not EventSchema.validate(event_name, data):
        self.logger.warning(f"事件数据验证失败，仍然发布: {event_name}")
    
    await self._actual_emit(...)
```

**优势**：
- ✅ 集中管理所有事件契约
- ✅ 启动时验证（不是运行时发现）
- ✅ 清晰的事件总览
- ✅ 可以生成文档（从 _schemas 生成事件列表）

**劣势**：
- ❌ 需要维护注册表
- ❌ 增加启动时检查
- ❌ 不够灵活（难以支持动态事件）

---

### 方案 4：事件类型注册（Pydantic + 注册表）⭐⭐⭐⭐⭐

**核心思想**：结合 Pydantic Model 的优势 + 注册表的集中管理。

**实现方式**：
```python
from pydantic import BaseModel, Field
from typing import Dict, Type, Optional

class EventTypeRegistry:
    """事件类型注册表"""
    
    # 事件类型定义
    PERCEPTION_RAW_DATA_GENERATED = "perception.raw_data.generated"
    NORMALIZATION_TEXT_READY = "normalization.text.ready"
    UNDERSTANDING_INTENT_GENERATED = "understanding.intent_generated"
    EXPRESSION_PARAMETERS_GENERATED = "expression.parameters_generated"
    
    # 事件类型到 Model 类型的映射
    _event_models: Dict[str, Type[BaseModel]] = {}
    
    @classmethod
    def register_event(cls, event_name: str, model_type: Type[BaseModel]):
        """注册事件类型"""
        cls._event_models[event_name] = model_type
        cls.logger.info(f"注册事件类型: {event_name} -> {model_type.__name__}")
    
    @classmethod
    def get_event_model(cls, event_name: str) -> Optional[Type[BaseModel]]:
        """获取事件的 Model 类型"""
        return cls._event_models.get(event_name)
    
    @classmethod
    def validate_and_parse(cls, event_name: str, data: dict) -> BaseModel:
        """验证并解析事件数据"""
        model_type = cls.get_event_model(event_name)
        if not model_type:
            cls.logger.warning(f"事件 {event_name} 未注册，使用通用解析")
            # 通用解析为字典
            return data
        
        try:
            return model_type.model_validate_json(data)
        except ValidationError as e:
            cls.logger.error(f"事件数据验证失败 ({event_name}): {e}")
            raise
    
    @classmethod
    def list_all_events(cls) -> Dict[str, Type[BaseModel]]:
        """列出所有注册的事件"""
        return cls._event_models.copy()

# 定义事件模型
class RawDataModel(BaseModel):
    """原始数据模型"""
    source: str = Field(..., description="数据源")
    data_type: str = Field(..., description="数据类型")
    content: Any = Field(..., description="数据内容")
    timestamp: float = Field(..., description="时间戳")

# 注册所有事件
EventTypeRegistry.register_event(
    EventTypeRegistry.PERCEPTION_RAW_DATA_GENERATED,
    RawDataModel
)
EventTypeRegistry.register_event(
    EventTypeRegistry.NORMALIZATION_TEXT_READY,
    NormalizedTextModel
)
EventTypeRegistry.register_event(
    EventTypeRegistry.UNDERSTANDING_INTENT_GENERATED,
    IntentModel
)
EventTypeRegistry.register_event(
    EventTypeRegistry.EXPRESSION_PARAMETERS_GENERATED,
    ExpressionParametersModel
)
```

**发布事件**：
```python
# 发布事件
raw_data = RawDataModel(source="test", data_type="text", content="Hello", timestamp=time.time())
await event_bus.emit(
    EventTypeRegistry.PERCEPTION_RAW_DATA_GENERATED,
    raw_data.model_dump_json(),
    "ProviderName"
)
```

**订阅事件**：
```python
async def handler(event_name: str, event_data: BaseModel, source: str):
    # 根据 event_name 获取对应的 Model 类型
    model_type = EventTypeRegistry.get_event_model(event_name)
    if not model_type:
        self.logger.error(f"未知的事件类型: {event_name}")
        return
    
    # 使用对应的 Model 类型接收
    if event_name == EventTypeRegistry.PERCEPTION_RAW_DATA_GENERATED:
        raw_data = RawDataModel(**event_data)
        text = raw_data.content
        source = raw_data.source
    # ... 处理逻辑
```

**优势**：
- ✅ **类型安全**：自动检查事件数据类型
- ✅ **自动验证**：Pydantic 自动验证所有字段
- ✅ **集中管理**：所有事件类型在一个地方
- ✅ IDE 友好：根据 event_name 自动补全
- ✅ 可以自动生成文档：从注册表生成 API 文档

**劣势**：
- ❌ 增加复杂度：需要注册表
- ❌ 启动时依赖检查
- ❌ 学习曲线：需要理解 Pydantic 和注册表

---

## 📊 方案对比总结

| 维度 | 当前方式 (@dataclass) | Pydantic Model | TypedDict | Schema 注册表 | 事件类型注册 |
|------|----------------|---------------|----------|------------|------------|--------------|---------------|
| **额外依赖** | ❌ 无 | ⚠️ Pydantic | ✅ 无 | ✅ 无 | ✅ Pydantic |
| **类型安全** | ⚠️ 部分（@dataclass） | ✅ 完整 | ✅ 部分 | ✅ 无 | ✅ 完整 |
| **数据验证** | ❌ 无 | ✅ 强验证 | ❌ 类型检查 | ✅ 可选 | ✅ 可选 |
| **IDE 友好** | ✅ 基本 | ✅ 优秀 | ✅ 良好 | ✅ 优秀 | ✅ 优秀 |
| **自动文档** | ⚠️ 需要手动写 | ✅ 自动生成 | ⚠️ 部分手动 | ✅ 手动写 | ✅ 自动生成 |
| **测试支持** | ✅ 基本 | ✅ Pytest-Pydantic | ✅ 基本 | ✅ 手动写 | ✅ 基本 |
| **复杂度** | ✅ 低 | ⚠️ 中等 | ✅ 低 | ⚠️ 高 | ⚠️ 中等 |
| **灵活性** | ✅ 高 | ⚠️ 中等 | ✅ 高 | ❌ 低 | ✅ 中等 |
| **学习曲线** | ✅ 低 | ⚠️ 中等 | ✅ 低 | ⚠️ 低 | ⚠️ 高 | ⚠️ 中等 |

---

## 🎯 针对不同场景推荐方案

### 1. 小型项目 / 原型

**当前方式足够**：
- 如果事件类型很少（< 10 个）
- 团队已经熟悉 @dataclass
- 不需要数据验证

**建议**：**保持当前方式**，添加文档生成工具

**改进建议**：
```python
# 添加 @dataclass_serializer 裋饰器，自动生成 to_dict()
from dataclasses import dataclass, asdict
import json

def dataclass_serializer(cls):
    """自动为 @dataclass 添加 to_dict 方法"""
    def to_dict(self) -> dict:
        """序列化为字典"""
        return {
            k: getattr(self, k)
            for k in self.__dataclass_fields__
        }

# 使用方式
@dataclass_serializer
class RawData:
    content: Any
    source: str
    
# 现在可以
raw_data.to_dict()  # 自动生成字典
```

---

### 2. 中型项目 / 需要验证

**推荐方案：Pydantic Model ⭐⭐⭐⭐**

**适用场景**：
- 事件类型较多（10-30 个）
- 需要数据验证（格式、范围、业务规则）
- 需要清晰的类型提示
- 团队需要测试友好

**为什么选择 Pydantic**：
- ✅ 验证能力强大（类型检查 + 业务规则）
- ✅ 错误信息清晰，易于调试
- ✅ 与测试框架完美集成
- ✅ 自动文档生成（可生成 OpenAPI 文档）

**实施路径**：
1. 安装依赖：`pip install pydantic pydantic-settings`
2. 定义所有事件 Model
3. 更新 EventBus 集成 Pydantic
4. 更新所有插件使用新 Model
5. 编写测试用例

---

### 3. 大型项目 / 需要文档自动化

**推荐方案：事件类型注册表 ⭐⭐⭐⭐⭐**

**适用场景**：
- 事件类型很多（30+ 个）
- 需要集中管理事件契约
- 需要自动生成 API 文档
- 需要类型系统保证事件一致性

**为什么选择事件类型注册**：
- ✅ 集中管理，清晰的事件总览
- ✅ 可以自动生成事件列表文档
- ✅ 类型系统（不同事件有不同 Model 类型）
- ✅ 自动验证和数据转换

**实施路径**：
1. 创建 EventTypeRegistry
2. 注册所有事件类型和 Model
3. 更新 EventBus 支持 Schema 验证
4. 生成事件契约文档
5. 在启动时自动验证

---

### 4. 无依赖 / 快速原型

**推荐方案：TypedDict ⭐⭐⭐**

**适用场景**：
- 快速原型开发
- 不想引入额外依赖
- 只需要基本类型检查

**为什么选择 TypedDict**：
- ✅ Python 3.9+ 内置，无依赖
- ✅ 提供 IDE 智能提示
- ✅ 比字典更明确
- ✅ 易于使用和测试

**实施路径**：
1. 定义 TypedDict 事件类型
2. 更新 EventBus 使用 TypedDict
3. 更新插件
4. 编写基本测试

---

## 🔧 实践建议

### 建议 1：改进当前方式（低风险）

如果项目已经在使用 @dataclass，但想要改进，可以：

```python
# 1. 添加 @dataclass_serializer 棋饰器
from dataclasses import dataclass
import json

def dataclass_serializer(cls):
    """自动为 @dataclass 添加 to_dict 方法"""
    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}

# 2. 添加类型检查包装器
def emit_with_validation(event_bus, event_name: str, data: Any, source: str):
    """发布事件并验证数据结构"""
    # 检查必需字段
    required_fields = {"data", "source"}
    if not all(field in data for field in required_fields):
        event_bus.logger.error(f"事件 {event_name} 缺少必需字段: {missing}")
        return
    
    # 验证数据类型
    if event_name == "perception.raw_data.generated":
        if not isinstance(data, dict):
            event_bus.logger.error(f"事件 {event_name} 数据格式错误: {type(data)}")
            return
    
    # 发布事件
    await event_bus.emit(event_name, data, source)

# 3. 使用类型工厂函数
def create_raw_data(source: str, data_type: str, content: Any) -> RawData:
    """创建 RawData 的工厂函数"""
    return RawData(
        source=source,
        data_type=data_type,
        content=content,
        timestamp=time.time(),
        metadata={}
    )

# 4. 在文档字符串中添加 JSON Schema 示例
class InputProvider(ABC):
    """
    事件数据示例：
    ```json
    {
        "data": {
            "content": "hello",
            "source": "console_input",
            "data_type": "text",
            "timestamp": 1700000000
        },
        "source": "InputLayer"
    }
    ```
    """
```

---

### 建议 2：渐进式迁移（推荐）

**Phase 1：添加类型检查工具**
```python
# src/core/utils/event_validation.py

def validate_event_data(event_name: str, data: dict) -> bool:
    """验证事件数据结构"""
    
    # 检查必需字段
    if event_name == "perception.raw_data.generated":
        if not isinstance(data, dict):
            return False
        if "data" not in data:
            return False
    
    # 检查数据类型
    data = data["data"]
    if not isinstance(data, dict):
        return False
    if "source" not in data:
            return False
    
    return True
```

**Phase 2：在 EventBus 中集成验证**
```python
async def emit(self, event_name: str, data: Any, ...):
    # 验证数据
    if not validate_event_data(event_name, data):
        self.logger.error(f"事件 {event_name} 数据验证失败，仍然发布: {event_name}")
    
    # 原有发布逻辑
    await self._actual_emit(...)
```

**Phase 3：添加单元测试**
```python
# tests/test_event_validation.py
import pytest

def test_valid_raw_data_event():
    data = {
        "data": {
            "content": "hello",
            "source": "test",
            "data_type": "text",
            "timestamp": time.time()
        },
        "source": "test"
    }
    assert validate_event_data("perception.raw_data.generated", data)

def test_invalid_raw_data_event_missing_field():
    data = {
        "data": {
            "content": "hello",
            # 缺少 source
            "data_type": "text",
            "timestamp": time.time()
        },
        "source": "test"
    }
    assert not validate_event_data("perception.raw_data.generated", data)
```

**Phase 4：生成文档**
```python
# scripts/generate_event_docs.py

def generate_event_documentation():
    """从 EventTypeRegistry 生成 Markdown 文档"""
    events = EventTypeRegistry.list_all_events()
    
    for event_name, model_type in events.items():
        # 生成 Schema
        schema = model_type.schema()
        
        # 生成示例
        example = create_example_data(event_name, model_type)
        
        # 写入文档
        with open(f"docs/events/{event_name}.md", "w") as f:
            f.write(f"# {event_name} 事件文档\n\n")
            f.write(f"## 数据结构\n\n```json\n")
            f.write(schema.model_dump_json(indent=2))
            f.write("```\n\n")
            f.write("## 数据示例\n\n```json\n")
            f.write(json.dumps(example, indent=2))
            f.write("```\n\n")
```

**Phase 5：更新插件**
```python
# 更新事件发布
raw_data = create_raw_data("console_input", "text", "Hello")
await self.event_bus.emit(
    "perception.raw_data.generated",
    raw_data.to_dict(),  # 使用改进的 to_dict() 方法
    "InputLayer"
)
```

---

### 建议 3：如果要大规模改进

**目标项目**：
1. 引入 Pydantic 作为依赖
2. 重构所有事件数据为 Pydantic Model
3. 更新 EventBus 集成 Pydantic
4. 添加单元测试覆盖所有事件
5. 生成 API 文档

**时间估算**：
- Phase 1（添加验证工具）：1-2 天
- Phase 2（EventBus 集成）：2-3 天
- Phase 3（重构数据类）：5-7 天
- Phase 4（更新插件）：7-10 天
- Phase 5（测试和文档）：3-5 天
- **总计**:18-27 天（约 1 个月）

---

## 📊 决策树

### 问题：我该选择哪种方式？

#### 回答几个问题：

1. **项目规模**：
   - 小型（< 10 个事件类型）
   - 中型（10-30 个事件类型）
   - 大型（30+ 个事件类型）

2. **是否需要数据验证**：
   - 不需要（完全信任发布者）
   - 基本验证（类型检查）
   - 强验证（业务规则验证）

3. **优先级排序**（从高到低）：
   - 类型安全
   - IDE 友好
   - 数据验证
   - 自动文档
   - 测试友好
   - 无依赖
   - 易于理解

4. **团队技能**：
   - 熟悉 Pydantic
   - 只熟悉基本 Python
   - 有测试经验

#### 推荐：

| 场景 | 推荐方案 | 理由 |
|------|---------|------|
| **小型项目，无验证需求** | 保持 @dataclass + 添加序列化工具 | 低风险，足够使用 |
| **小型项目，需要基本验证** | TypedDict + 类型检查工具 | 无依赖，类型安全 |
| **中型项目，需要验证** | Pydantic Model | 验证能力强 |
| **大型项目，需要文档自动化** | 事件类型注册表 | 集中管理 |

---

## 🎯 实施建议

### 如果选择改进当前方式（推荐给新同事）：

**步骤 1：理解当前架构**
- 阅读 `refactor/docs/NEW_COLLEAGUE_ONBOARDING.md`
- 查看 `src/core/event_bus.py` 了解 EventBus
- 查看 `src/core/data_types/` 了解数据结构

**步骤 2：编写代码前先验证**
- 在文档字符串中明确事件数据结构
- 在发布时做基本验证（必需字段检查）
- 编写单元测试验证事件流

**步骤 3：使用类型工厂函数**
- 创建 `create_raw_data()` 等工厂函数
- 避免每次手动构造字典

**步骤 4：添加辅助工具**
- 添加 `emit_with_validation()` 包装器
- 添加类型检查工具函数

**步骤 5：生成文档**
- 为每个事件生成 Markdown 文档
- 包含 JSON Schema 和数据示例

---

## 📋 总结

### 当前方式适用场景
✅ 小型项目（< 10 个事件）
✅ 不需要数据验证
✅ 团队熟悉 @dataclass
✅ 避免额外依赖

### Pydantic Model 适用场景
✅ 中大型项目（10+ 个事件类型）
✅ 需要数据验证
✅ 需要类型安全
✅ 需要测试友好
✅ 需要自动文档

### 事件类型注册表适用场景
✅ 大型项目（30+ 个事件类型）
✅ 需要集中管理事件契约
✅ 需要自动生成 API 文档
✅ 需要类型系统保证一致性

### TypedDict 适用场景
✅ 快速原型开发
✅ 不想引入额外依赖
✅ 只需要基本类型检查

### 我的建议

**对于 Amaidesu 项目**：
1. **短期**：改进当前方式，添加序列化工具和基本验证
2. **中期**：评估是否需要引入 Pydantic
3. **长期**：如果事件类型增长到 20+ 个，考虑事件类型注册表

---

**文档创建时间**: 2026-01-31
**版本**: 1.0
**状态**: 可用的改进方案分析
