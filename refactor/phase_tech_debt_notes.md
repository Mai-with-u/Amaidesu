# Amaidesu 重构技术债务笔记

> **创建日期**: 2026-01-19
> **目的**: 避免压缩上下文时失去记忆，记录本轮需要处理的所有技术债务
> **状态**: 待处理

---

## 📊 重构整体进度（截至Phase 4完成）

| Phase | 状态 | 完成度 | 核心成果 |
|-------|------|--------|----------|
| **Phase 1: 基础设施** | ✅ 完成 | 100% | Provider接口、EventBus增强、DataCache、配置转换工具 |
| **Phase 2: 输入层** | ✅ 完成 | 90% | RawData/NormalizedText、InputProviderManager、2个Provider迁移 |
| **Phase 3: 决策层** | ✅ 完成 | 100% | CanonicalMessage、DecisionManager、3种Provider、AmaidesuCore重构 |
| **Phase 4: 输出层** | ✅ 完成 | 90% | Layer 5-6接口、5个OutputProvider实现 |
| **Phase 5: 扩展系统** | ⏸️ 未开始 | 0% | 插件系统重构、24个插件迁移 |
| **Phase 6: 清理和测试** | ⏸️ 未开始 | 0% | 最终清理、端到端测试、文档完善 |

**总体完成度：约 50%**

---

## 🔴 高优先级问题（必须解决）

### 问题1: EventBus版本不统一 ⚠️

**状态**: Phase 1-4 都存在此问题  
**影响**: 可能导致事件通信混乱、接口不兼容  
**优先级**: 🔴 P0 - 必须在Phase 5之前解决

**问题描述**:

存在两个版本：
- `src/core/event_bus.py` (基础版, 114行)
- `src/core/event_bus_new.py` (增强版, 272行)

增强版EventBus新增功能：
- 错误隔离机制(单个handler异常不影响其他)
- 优先级控制(handler可设置priority,数字越小越优先)
- 统计功能(emit/on调用计数、错误率、执行时间)
- 生命周期管理(cleanup方法)

**Phase使用情况**:
- Phase 1: 实现了event_bus_new（增强功能）
- Phase 2: 使用event_bus.py（基础版）
- Phase 3: 需要统一选择
- Phase 4: 不明确

**解决方案**:

将 `event_bus_new.py` 替换 `event_bus.py`：

1. 备份原event_bus.py
   ```bash
   git mv src/core/event_bus.py src/core/event_bus_old.py
   ```

2. 使用event_bus_new替换event_bus
   ```bash
   git mv src/core/event_bus_new.py src/core/event_bus.py
   ```

3. 更新所有导入语句（搜索并替换）
   ```python
   # 需要替换的导入
   from src.core.event_bus_new import EventBus  # 旧
   from src.core.event_bus import EventBus        # 新
   ```

4. 验证所有模块使用新的EventBus
   - Phase 2: `src/perception/`
   - Phase 3: `src/canonical/`, `src/understanding/`, `src/core/decision_manager.py`
   - Phase 4: `src/expression/`, `src/providers/`

**验收标准**:
- [ ] 只有`src/core/event_bus.py`存在，删除`event_bus_new.py`和`event_bus_old.py`
- [ ] 所有模块都导入`src/core.event_bus`
- [ ] 所有测试通过（Phase 1-4的测试）
- [ ] EventBus增强功能正常工作（优先级、错误隔离、统计）

**相关文件**:
- `src/core/event_bus.py`
- `src/core/event_bus_new.py`
- `src/perception/input_layer.py`
- `src/perception/input_provider_manager.py`
- `src/core/decision_manager.py`
- `src/providers/maicore_decision_provider.py`
- `src/expression/expression_generator.py`
- `src/core/output_provider_manager.py`

---

### 问题2: DataCache未实际集成 ⚠️

**状态**: 已实现但未使用  
**影响**: 无法发挥缓存优势，性能无法优化  
**优先级**: 🔴 P0 - 必须在Phase 4集成前解决

**问题描述**:

Phase 1已实现的DataCache功能：
- ✅ TTL过期机制（默认300秒）
- ✅ LRU淘汰策略（最近最少使用）
- ✅ 统计信息（命中率、未命中率、淘汰次数）
- ✅ 标签查询功能（find_by_tags）
- ✅ 线程安全（asyncio.Lock + threading.Lock双重保护）
- ✅ 后台清理任务（start_cleanup/stop_cleanup）
- ✅ 缓存大小限制（max_entries, max_size_mb）

CanonicalMessage支持：
- ✅ `data_ref: Optional[str]` 字段
- ✅ DataCache引用格式：`cache://hash_string`

**问题**:
- Phase 2-4中，没有实际使用DataCache存储/检索原始数据
- RawData的`preserve_original`功能未实现
- NormalizedText的`data_ref`字段始终为None

**解决方案**:

在Phase 3的DecisionManager或Phase 4的OutputLayer中集成DataCache：

1. 在AmaidesuCore中初始化DataCache
   ```python
   # src/core/amaidesu_core.py
   from src.core.data_cache import DataCache, CacheConfig, MemoryDataCache

   class AmaidesuCore:
       def __init__(self, ...):
           # ... 现有代码 ...

           # DataCache初始化
           cache_config = CacheConfig(
               ttl=300,
               max_entries=1000,
               max_size_mb=100,
               eviction_policy="ttl_or_lru"
           )
           self.data_cache = MemoryDataCache(cache_config)
   ```

2. 在CanonicalMessage中保留原始数据（可选）
   ```python
   # src/core/decision_manager.py
   async def decide(self, canonical_message: CanonicalMessage) -> MessageBase:
       """进行决策"""

       # 可选：如果原始数据需要保留，存储到DataCache
       if canonical_message.original_message:
           # 将原始MessageBase序列化并存储
           original_data = canonical_message.original_message.to_dict()
           data_ref = await self.core.data_cache.store(
               data=original_data,
               ttl=300,  # 5分钟
               tags=["message_base", canonical_message.source]
           )
           # 更新data_ref（虽然CanonicalMessage已有data_ref字段，但这里演示）
           canonical_message.data_ref = data_ref

       # ... 决策逻辑 ...
   ```

3. 在Layer 4（Understanding）中访问原始数据（可选）
   ```python
   # src/understanding/response_parser.py
   class ResponseParser:
       def __init__(self, event_bus: EventBus, data_cache: Optional[DataCache] = None):
           self.event_bus = event_bus
           self.data_cache = data_cache

       async def parse(self, message: MessageBase) -> Intent:
           """解析消息为Intent"""

           # 提取文本
           text = self._extract_text(message)

           # 可选：如果有data_ref，从DataCache获取原始数据
           # 注意：message是MessageBase，不包含data_ref
           # data_ref应该在NormalizedText或CanonicalMessage中
           # 这里只是示例，实际使用场景需要评估

           # ... 其他解析逻辑 ...
   ```

**使用场景建议**:

| 场景 | 是否需要DataCache | 理由 |
|------|------------------|------|
| 普通文本输入 | ❌ 否 | 文本本身就是轻量的，不需要缓存 |
| 图像输入 | ✅ 是 | 图像数据较大，应该缓存，后续可复用 |
| 音频输入 | ✅ 是 | 音频数据较大，应该缓存，后续可复用 |
| 礼物/醒目留言 | ⚠️ 可选 | 如果需要保留完整礼物信息，缓存 |
| 弹幕 | ❌ 否 | 弹幕本身就是轻量的，不需要缓存 |

**验收标准**:
- [ ] AmaidesuCore中初始化DataCache
- [ ] 至少有一个使用场景实际调用data_cache.store/retrieve
- [ ] 所有测试通过（包括DataCache相关测试）
- [ ] DataCache统计功能正常（hits/misses/evictions）

**相关文件**:
- `src/core/data_cache/__init__.py`
- `src/core/data_cache/base.py`
- `src/core/data_cache/memory_cache.py`
- `src/core/amaidesu_core.py`
- `src/canonical/canonical_message.py`
- `src/core/decision_manager.py`

---

### 问题3: Phase 3 代码质量问题 🟡

**状态**: 存在但可修复  
**影响**: 不影响功能，但影响代码质量  
**优先级**: 🟠 P1 - 应该尽快修复

**问题描述**:

1. **response_parser.py 未使用的导入**
   ```python
   # src/understanding/response_parser.py
   import asyncio  # 未使用
   import re  # 未使用
   from typing import Dict, List, Optional, Any
   ```

2. **response_parser.py 重复的字典键**
   ```python
   # emotion_keywords定义中有重复键（检查第60-80行）
   self.emotion_keywords = {
       "happy": ["开心", "快乐", "高兴", ...],
       "happy": ["哈", "哈哈", ...],  # 重复键，会被覆盖
   }
   ```

3. **类型注解不一致**
   - decision_provider.py使用TYPE_CHECKING避免循环导入
   - 事件处理器签名可能与event_bus_new.py不兼容

**解决方案**:

运行 `ruff check --fix` 修复大部分问题：

```bash
# 检查所有Phase 3相关文件
ruff check src/canonical/ src/understanding/ src/core/decision_manager.py src/providers/maicore_decision_provider.py src/providers/local_llm_decision_provider.py src/providers/rule_engine_decision_provider.py

# 自动修复
ruff check --fix src/canonical/ src/understanding/ src/core/decision_manager.py src/providers/
```

手动修复重复字典键：

```python
# src/understanding/response_parser.py
class ResponseParser:
    def __init__(self):
        # 修复重复键
        self.emotion_keywords = {
            "happy": ["开心", "快乐", "高兴", "哈哈", "嘻嘻", "嘿嘿"],
            "sad": ["难过", "悲伤", "伤心", "痛苦"],
            "angry": ["生气", "愤怒", "恼火"],
            "surprised": ["惊讶", "意外", "吃惊"],
            "excited": ["兴奋", "激动", "期待"],
            "confused": ["困惑", "疑惑", "不解"],
            "love": ["喜欢", "爱", "爱慕"],
            "neutral": ["好的", "嗯", "嗯嗯"]
        }
```

验证类型注解：

```python
# src/core/providers/decision_provider.py
from typing import TYPE_CHECKING, Any, Dict

if TYPE_CHECKING:
    from src.core.event_bus import EventBus
    from src.canonical.canonical_message import CanonicalMessage
    from maim_message import MessageBase

class DecisionProvider(ABC):
    """决策提供者抽象基类"""

    async def setup(self, event_bus: EventBus, config: Dict[str, Any]):
        """初始化Provider"""
        ...

    @abstractmethod
    async def decide(self, canonical_message: CanonicalMessage) -> MessageBase:
        """根据CanonicalMessage做出决策"""
        ...
```

**验收标准**:
- [ ] `ruff check` 无错误（可能保留设计性警告B027）
- [ ] 删除所有未使用的导入
- [ ] 修复所有重复的字典键
- [ ] 类型注解正确且一致

**相关文件**:
- `src/understanding/response_parser.py`
- `src/core/providers/decision_provider.py`
- `src/canonical/canonical_message.py`

---

### 问题4: Phase 4 集成未完成 ⚠️

**状态**: Provider实现完成但未集成  
**影响**: 无法验证完整数据流  
**优先级**: 🔴 P0 - 必须在Phase 4完成后解决

**问题描述**:

**已完成的组件**:
- ✅ Layer 5: ExpressionParameters、EmotionMapper、ActionMapper、ExpressionGenerator
- ✅ Layer 6: OutputProvider接口、OutputProviderManager
- ✅ 5个OutputProvider实现：
  - TTSProvider (390行)
  - SubtitleProvider (723行)
  - StickerProvider (265行)
  - VTSProvider (~700行，17/17测试通过)
  - OmniTTSProvider (360行)

**未完成的集成**:
- ❌ OutputProviderManager未集成到AmaidesuCore
- ❌ 从配置文件加载OutputProvider的逻辑未实现
- ❌ Layer 4→Layer 5→Layer 6的完整数据流未测试
- ❌ ExpressionGenerator未订阅Decision层的事件

**解决方案**:

1. 在AmaidesuCore中集成OutputProviderManager
   ```python
   # src/core/amaidesu_core.py
   from src.core.output_provider_manager import OutputProviderManager
   from src.expression.expression_generator import ExpressionGenerator

   class AmaidesuCore:
       def __init__(self, config: Dict[str, Any]):
           # ... 现有代码 ...

           # Phase 4新增
           self.output_provider_manager = None
           self.expression_generator = None

       async def setup(self):
           # ... 现有代码 ...

           # Phase 4：初始化OutputProviderManager
           output_config = config.get("rendering", {}).get("outputs", {})
           if output_config:
               await self._setup_output_layer(output_config)

       async def _setup_output_layer(self, output_config: Dict[str, Any]):
           """初始化输出层"""
           # 1. 创建OutputProviderManager
           self.output_provider_manager = OutputProviderManager(
               config=output_config,
               concurrent_rendering=True  # 并发渲染
           )

           # 2. 启动所有OutputProvider
           await self.output_provider_manager.setup_all_providers(self.event_bus)

           # 3. 创建ExpressionGenerator
           self.expression_generator = ExpressionGenerator()

           # 4. 订阅事件：Layer 4 → Layer 5 → Layer 6
           self.event_bus.on("understanding.intent.ready", self._on_intent_ready)

       async def _on_intent_ready(self, event: Dict[str, Any]):
           """处理Intent就绪事件"""
           intent = event.get("intent")
           if not intent:
               return

           # Layer 5: Intent → ExpressionParameters
           expression_params = self.expression_generator.generate(intent)

           # Layer 6: ExpressionParameters → 多Provider并发渲染
           await self.output_provider_manager.render_all(expression_params)

       async def cleanup(self):
           # ... 现有代码 ...

           # Phase 4：清理输出层
           if self.output_provider_manager:
               await self.output_provider_manager.cleanup_all_providers()
   ```

2. 实现配置加载逻辑
   ```python
   # src/core/output_provider_manager.py
   class OutputProviderManager:
       async def setup_all_providers(self, event_bus: EventBus, amaidesu_core: Optional['AmaidesuCore'] = None):
           """从配置加载并启动所有OutputProvider"""

           # 从配置获取Provider列表
           provider_names = self.config.get("outputs", [])
           if isinstance(provider_names, list):
               for name in provider_names:
                   # 加载Provider配置
                   provider_config = self.config.get(f"outputs.{name}", {})
                   if provider_config:
                       await self._setup_provider(name, provider_config, event_bus, amaidesu_core)

       async def _setup_provider(self, name: str, config: Dict[str, Any], event_bus: EventBus, amaidesu_core: Optional['AmaidesuCore']):
           """设置单个Provider"""

           # 创建Provider实例
           provider = self._create_provider(name, config)

           # 传入core引用（如果需要访问服务）
           if amaidesu_core and hasattr(provider, 'set_core'):
               provider.set_core(amaidesu_core)

           # 启动Provider
           await provider.setup(event_bus, config)

           # 添加到管理器
           self.providers.append(provider)
   ```

3. 更新配置文件
   ```toml
   # config.toml
   [rendering]
   # 启用的输出Provider列表
   outputs = ["subtitle", "tts", "sticker", "vts"]

   [rendering.outputs.subtitle]
   type = "subtitle"
   enabled = true
   window_width = 800
   window_height = 100
   font_size = 24
   background_color = "#000000"
   text_color = "#FFFFFF"

   [rendering.outputs.tts]
   type = "edge"  # 或 "omni"
   enabled = true
   voice = "zh-CN-XiaoxiaoNeural"
   output_device_name = ""

   [rendering.outputs.sticker]
   type = "sticker"
   enabled = true
   sticker_size = 0.33
   sticker_rotation = 90
   cooldown_seconds = 5.0

   [rendering.outputs.vts]
   type = "vts"
   enabled = true
   vts_host = "localhost"
   vts_port = 8001
   llm_matching_enabled = false
   lip_sync_enabled = true
   ```

**数据流验证**:

```
Phase 2: Layer 2 (NormalizedText)
    ↓
MessageBuilder.build_from_normalized_text()
    ↓
Phase 3: Layer 3 (CanonicalMessage)
    ↓
DecisionProvider.decide(CanonicalMessage)
    ↓
返回 MessageBase
    ↓
Phase 4: Layer 4 (Understanding)
    ↓
ResponseParser.parse(message) → Intent
    ↓
emit("understanding.intent.ready", {"intent": Intent})
    ↓
Phase 5: Layer 5 (Expression)
    ↓
ExpressionGenerator.generate(intent) → ExpressionParameters
    ↓
Phase 6: Layer 6 (Rendering)
    ↓
OutputProviderManager.render_all(expression_params)
    ↓
多个Provider并发渲染（TTS、Subtitle、Sticker、VTS）
```

**验收标准**:
- [ ] AmaidesuCore中集成OutputProviderManager
- [ ] ExpressionGenerator订阅`understanding.intent.ready`事件
- [ ] 完整数据流测试通过（需要外部服务）
- [ ] 所有Provider可以独立启停
- [ ] 错误隔离生效（单个Provider失败不影响其他）

**相关文件**:
- `src/core/amaidesuCore.py`
- `src/core/output_provider_manager.py`
- `src/expression/expression_generator.py`
- `src/providers/tts_provider.py`
- `src/providers/subtitle_provider.py`
- `src/providers/sticker_provider.py`
- `src/providers/vts_provider.py`
- `src/providers/omni_tts_provider.py`
- `config.toml` 或 `config-template.toml`

---

## 🟠 中优先级问题（建议解决）

### 问题5: 上下文标签功能未实现 🟡

**状态**: Phase 2有意跳过  
**影响**: ConsoleInputPlugin原有功能丢失  
**优先级**: 🟠 P1 - Phase 3或Phase 5中考虑

**问题描述**:

原有ConsoleInputPlugin功能：
- 通过`core.get_service("prompt_context")`服务获取上下文
- 添加上下文标签到消息中

新的ConsoleInputProvider：
- 通过EventBus发布RawData
- 上下文标签功能未迁移

**解决方案**:

在Phase 3的CanonicalMessage或Phase 4的Intent中添加上下文字段：

```python
# src/canonical/canonical_message.py
@dataclass
class CanonicalMessage:
    text: str
    source: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    data_ref: Optional[str] = None
    original_message: Optional[MessageBase] = None
    timestamp: float = field(default_factory=time.time)

    # 新增：上下文字段
    context: Optional[Dict[str, Any]] = None
```

然后在InputLayer中添加上下文管理：

```python
# src/perception/input_layer.py
class InputLayer:
    def __init__(self, event_bus: EventBus, context_manager=None):
        self.event_bus = event_bus
        self.context_manager = context_manager

    async def normalize(self, raw_data: RawData) -> NormalizedText:
        """标准化RawData为NormalizedText"""

        # ... 转换逻辑 ...

        # 添加上下文（如果有context_manager）
        context = None
        if self.context_manager:
            context = await self.context_manager.get_context(
                source=raw_data.source,
                user=raw_data.metadata.get("user")
            )

        return NormalizedText(
            text=text,
            metadata=metadata,
            context=context
        )
```

**验收标准**:
- [ ] CanonicalMessage包含context字段
- [ ] 上下文管理逻辑正确
- [ ] 上下文正确传递到后续层

---

### 问题6: AmaidesuCore代码未精简到350行 🟡

**状态**: 从641行→474行（减少26%）  
**影响**: 未达到设计目标（350行）  
**优先级**: 🟠 P2 - Phase 6处理

**问题描述**:

设计目标：减少到350行（54%代码减少）  
实际结果：474行（26%代码减少）

保留的代码：
- HTTP服务器代码（用于插件HTTP回调）
- 插件系统代码
- 向后兼容代码

**解决方案**:

Phase 6中进一步简化：
1. 评估HTTP服务器代码是否真的需要
2. 评估向后兼容代码是否可以删除
3. 合并重复的逻辑

**验收标准**:
- [ ] AmaidesuCore代码量降至350行或接近
- [ ] 所有核心功能正常

---

### 问题7: 配置系统未完善 🟡

**状态**: 部分完成  
**影响**: 无法使用新架构配置  
**优先级**: 🟠 P2 - Phase 4完成后处理

**问题描述**:

- `config-converter.py` 已实现
- 但**未创建新的配置模板文件**
- OutputProvider配置格式未定义
- Phase 4设计文档没有详细说明`[rendering.outputs.xxx]`配置结构

**解决方案**:

为每个Provider创建`config-template.toml`：

```bash
# src/providers/tts/config-template.toml
# src/providers/subtitle/config-template.toml
# src/providers/sticker/config-template.toml
# src/providers/vts/config-template.toml
# src/providers/omni_tts/config-template.toml
```

在根配置文件中添加示例：

```toml
# config-template.toml
[rendering]
outputs = ["subtitle", "tts", "sticker", "vts"]

[rendering.outputs.subtitle]
type = "subtitle"
enabled = true
# ... 其他配置 ...

[rendering.outputs.tts]
type = "edge"
enabled = true
# ... 其他配置 ...

# ... 其他Provider配置 ...
```

**验收标准**:
- [ ] 每个Provider都有config-template.toml
- [ ] 根配置文件包含rendering配置示例
- [ ] 配置加载逻辑正确

---

### 问题8: 测试覆盖率不足 🟡

**状态**: 部分模块未测试  
**影响**: 关键路径可能未验证  
**优先级**: 🟠 P2 - Phase 6处理

**问题描述**:

- Phase 1: 85%覆盖率（21/21测试通过）✅
- Phase 2: 60%覆盖率（24/24测试通过，但ConsoleInputProvider仅16%）
- Phase 3: 核心功能已测试，但覆盖不完整
- Phase 4: 仅VTSProvider有单元测试（17/17通过）

**解决方案**:

Phase 6中补充测试：
1. ConsoleInputProvider集成测试（需要stdin交互模拟）
2. InputProviderManager错误处理测试
3. 其他Provider单元测试（需要外部服务）
4. 完整数据流集成测试

**验收标准**:
- [ ] 总体测试覆盖率>80%
- [ ] 所有核心模块有单元测试
- [ ] 集成测试覆盖完整数据流

---

## 🟡 低优先级问题（可选优化）

### 问题9: LocalLLM和RuleEngineDecisionProvider文档不足

**状态**: 已实现但文档不完善  
**建议**: 补充使用示例和配置说明

---

### 问题10: Pipeline未重构

**状态**: Phase 1-4保留现有逻辑  
**建议**: Phase 6评估Pipeline的实际使用场景，决定是否重构

---

### 问题11: Performance testing未执行

**状态**: 未测试  
**建议**: Phase 6执行性能测试，确保响应时间无增加

---

## 📋 验收标准检查

| 验收标准 | 状态 | 备注 |
|---------|------|------|
| 所有现有功能正常运行 | ⚠️ 部分验证 | 需要集成测试验证 |
| 配置文件行数减少40%以上 | ⏸️ 未验证 | 需要创建新配置后统计 |
| 核心功能响应时间无增加 | ⏸️ 未测试 | Phase 6执行性能测试 |
| 代码重复率降低30%以上 | ✅ 完成 | Provider模式替代重复插件 |
| 服务注册调用减少80%以上 | ⏸️ 未验证 | 需要统计迁移前后调用次数 |
| EventBus事件调用覆盖率90%以上 | ⚠️ 部分完成 | 需要集成后统计 |
| 扩展系统正常加载内置扩展和用户扩展 | ⏸️ 未开始 | Phase 5实现 |
| 清晰的6层核心数据流架构 | ✅ 完成 | Layer 1-6全部实现 |
| 决策层可替换 | ✅ 完成 | 支持3种DecisionProvider |
| 多Provider并发支持 | ✅ 完成 | InputProviderManager和OutputProviderManager已实现 |
| AmaidesuCore代码量降至350行 | ⚠️ 未达标 | 当前474行 |

---

## 📝 下一步行动

### 立即行动（本轮处理）

1. **统一EventBus版本** - P0
   - 将event_bus_new.py替换event_bus.py
   - 更新所有导入语句
   - 验证所有测试通过

2. **修复代码质量问题** - P1
   - 运行ruff check --fix
   - 修复重复字典键
   - 验证类型注解

3. **集成OutputProvider到AmaidesuCore** - P0
   - 在AmaidesuCore中创建OutputProviderManager
   - 实现配置加载逻辑
   - 验证完整数据流

4. **集成DataCache（可选，评估必要性）** - P0/P1
   - 在AmaidesuCore中初始化DataCache
   - 在适当场景使用DataCache
   - 验证缓存功能正常

### Phase 5实施时

1. 迁移24个插件
2. 实现Plugin接口
3. 实现ExtensionLoader

### Phase 6实施时

1. 进一步简化AmaidesuCore
2. 移除旧代码
3. 执行端到端测试
4. 执行性能测试
5. 完善文档

---

**文档创建时间**: 2026-01-19  
**创建人**: AI Assistant (Sisyphus)
**状态**: 待处理
