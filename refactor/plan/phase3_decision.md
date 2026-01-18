# Phase 3: 决策层+中间层 (10-14天)

> **目标**: 实现决策层可替换性和中间表示层,迁移AmaidesuCore中的核心功能
> **依赖**: Phase 1、2完成(Provider接口、EventBus、DataCache、InputLayer)
> **风险**: 高(核心功能迁移,容易破坏现有流程)

---

## 📋 阶段概述

本阶段实现决策层(Layer 3.5的中间层)和中间表示层,将MaiCore连接从AmaidesuCore中解耦,迁移到独立的DecisionProvider。这是整个重构的核心阶段,需要特别谨慎。

---

## 🎯 任务分解

### 任务3.1: Layer 3: CanonicalMessage设计 (1-2天)

**目标**: 定义中间表示数据结构,支持元数据传递

**范围**:
- [ ] `src/canonical/canonical_message.py` - CanonicalMessage类
- [ ] `src/canonical/message_builder.py` - MessageBuilder工具
- [ ] `src/canonical/__init__.py` - 模块导出

**核心特性**:
- 统一的中间表示格式
- 支持原始数据引用(data_ref → DataCache)
- 可序列化/反序列化
- 清晰的文档和示例

**接口设计**:
```python
@dataclass
class CanonicalMessage:
    text: str                          # 文本内容
    source: str                          # 数据来源(弹幕/控制台/等)
    metadata: Dict[str, Any]           # 元数据(用户ID、时间戳等)
    data_ref: Optional[str] = None   # DataCache引用
    original_message: Optional[MessageBase] = None  # 原始MessageBase(保留兼容)
    timestamp: float = time.time()
    
    def to_dict(self) -> dict:
        """序列化为字典"""
        ...
    
    @classmethod
    def from_dict(cls, data: dict) -> 'CanonicalMessage':
        """从字典反序列化"""
        ...
    
    def to_message_base(self) -> MessageBase:
        """转换为MessageBase(发送到旧系统)"""
        ...
```

**验收标准**:
- [ ] CanonicalMessage类完成,文档齐全
- [ ] MessageBuilder工具函数齐全(to_message_base, from_message_base)
- [ ] 支持data_ref指向DataCache
- [ ] 单元测试覆盖率>80%

---

### 任务3.2: DecisionProvider接口设计 (1天)

**目标**: 定义可替换的决策提供者接口

**范围**:
- [ ] `src/core/providers/decision_provider.py` - DecisionProvider接口
- [ ] `src/core/providers/__init__.py` - 模块导出

**接口规范**:
```python
class DecisionProvider(Protocol):
    async def setup(self, event_bus: EventBus, config: dict):
        """初始化Provider,订阅事件"""
        ...
    
    async def decide(self, canonical_message: CanonicalMessage) -> MessageBase:
        """
        根据CanonicalMessage做出决策,返回MessageBase
        
        Args:
            canonical_message: 标准化消息
            
        Returns:
            MessageBase: 决策结果
        """
        ...
    
    async def cleanup(self):
        """清理资源"""
        ...
    
    def get_info(self) -> Dict[str, Any]:
        """获取Provider信息"""
        return {
            "name": "DecisionProviderName",
            "version": "1.0.0",
            "description": "Provider description",
            "author": "Author",
            "api_version": "1.0"
        }
```

**验收标准**:
- [ ] DecisionProvider接口定义完成
- [ ] 文档清晰,示例代码齐全
- [ ] 类型注解完整
- [ ] 单元测试覆盖所有方法

---

### 任务3.3: DecisionManager实现 (1-2天)

**目标**: 实现决策层管理器,支持工厂模式和运行时切换

**范围**:
- [ ] `src/core/decision_manager.py` - DecisionManager类
- [ ] `src/core/providers/__init__.py` - 导出

**核心功能**:
- 工厂模式: 根据配置创建不同DecisionProvider
- 运行时切换: 支持动态切换Provider
- Provider生命周期管理: setup/cleanup
- 异常处理: Provider失败时优雅降级

**接口设计**:
```python
class DecisionManager:
    def __init__(self, event_bus: EventBus):
        ...
    
    async def setup(self, provider_name: str, config: dict):
        """设置决策Provider"""
        provider_class = self._factory.get(provider_name)
        self._current_provider = provider_class(config)
        await self._current_provider.setup(event_bus, config)
    
    async def decide(self, canonical_message: CanonicalMessage) -> MessageBase:
        """进行决策"""
        return await self._current_provider.decide(canonical_message)
    
    async def switch_provider(self, provider_name: str, config: dict):
        """切换决策Provider(运行时)"""
        await self.setup(provider_name, config)
    
    async def cleanup(self):
        """清理当前Provider"""
        if self._current_provider:
            await self._current_provider.cleanup()
    
    def get_current_provider(self) -> Optional[DecisionProvider]:
        """获取当前Provider实例"""
        return self._current_provider
```

**验收标准**:
- [ ] DecisionManager实现完整
- [ ] 工厂模式正常工作
- [ ] 运行时切换无中断
- [ ] 异常处理完善

---

### 任务3.4: MaiCoreDecisionProvider实现 (3-4天)

**目标**: 将AmaidesuCore中的WebSocket/HTTP/Router代码迁移为独立Provider

**范围**:
- [ ] `src/providers/maicore_decision_provider.py` - MaiCoreDecisionProvider实现
- [ ] WebSocket连接管理(从amaidesu_core.py迁移)
- [ ] HTTP回调管理(从amaidesu_core.py迁移)
- [ ] Router集成(保持maim_message兼容)

**迁移内容**:
- 从`amaidesu_core.py`提取WebSocket连接代码(~150行)
- 从`amaidesu_core.py`提取HTTP服务器代码(~100行)
- 从`amaidesu_core.py`提取Router代码(~150行)
- 集成为MaiCoreDecisionProvider

**关键技术点**:
1. WebSocket连接/断开管理
2. HTTP回调路由注册
3. maim_message Router使用
4. 与EventBus集成(订阅canonical.message_ready, 发布decision.response_generated)
5. 与DataCache集成(可选保留原始数据)
6. 错误处理和重连机制
7. AmaidesuCore实例获取(通过EventBus的core.ready事件)

**依赖关系**:
```
DecisionManager
  └─> MaiCoreDecisionProvider
       ├─> EventBus(订阅/发布事件)
       ├─> DataCache(可选访问原始数据)
       └─> AmaidesuCore(通过core.ready事件)
```

**验收标准**:
- [ ] WebSocket连接/断开正常
- [ ] HTTP回调接收正常
- [ ] Router与MaiCore通信正常
- [ ] EventBus事件正确发布/订阅
- [ ] 与Layer 2(正常ization)集成正常
- [ ] 错误处理完善,自动重连
- [ ] 所有原始功能保留

---

### 任务3.5: LocalLLMDecisionProvider实现 (2-3天)

**目标**: 实现本地LLM决策Provider,无需MaiCore

**范围**:
- [ ] `src/providers/local_llm_decision_provider.py` - LocalLLMDecisionProvider实现
- [ ] OpenAI API集成
- [ ] 提示词工程优化

**功能特性**:
- 使用OpenAI API(或其他兼容API)
- 支持自定义prompt模板
- 支持流式/非流式调用
- 错误处理和降级(回退到规则引擎)

**验收标准**:
- [ ] LLM API调用正常
- [ ] 响应解析为MessageBase
- [ ] 提示词模板支持
- [ ] 错误时优雅降级

---

### 任务3.6: RuleEngineDecisionProvider实现 (2-3天)

**目标**: 实现基于规则的决策Provider,提供简单快速决策

**范围**:
- [ ] `src/providers/rule_engine_decision_provider.py` - RuleEngineDecisionProvider实现
- [ ] 规则配置加载(JSON/YAML)
- [ ] 规则引擎核心

**功能特性**:
- 支持关键词匹配
- 支持正则表达式
- 支持优先级(多个规则匹配时选择最高优先级)
- 支持动作配置(返回特定的MessageBase)

**规则示例**:
```json
{
  "rules": [
    {
      "pattern": "你好|hello|hi",
      "action": {
        "type": "text_response",
        "text": "你好呀~",
        "metadata": {"user": "local"}
      },
      "priority": 10
    },
    {
      "pattern": "表情|表情|emotion",
      "action": {
        "type": "emoji",
        "emoji": "😊",
        "metadata": {}
      },
      "priority": 5
    }
  ]
}
```

**验收标准**:
- [ ] 规则引擎核心完成
- [ ] 规则配置可加载
- [ ] 匹配和优先级正确
- [ ] 动作执行正确

---

### 任务3.7: Layer 4: Understanding层实现 (3-4天)

**目标**: 实现表现理解层,解析MessageBase为Intent

**范围**:
- [ ] `src/understanding/intent.py` - Intent数据类
- [ ] `src/understanding/response_parser.py` - 响应解析器
- [ ] `src/understanding/__init__.py` - 模块导出

**数据流**:
```
DecisionProvider返回MessageBase
    ↓
Layer 4: Understanding层
    ↓
Intent对象
```

**Intent结构**:
```python
@dataclass
class Intent:
    original_text: str              # 原始文本
    emotion: EmotionType               # 情感类型(HAPPY, SAD, ANGRY, etc.)
    response_text: str              # 回复文本
    actions: List[Action]          # 动作列表(表情、热键等)
    metadata: Dict[str, Any]         # 扩展元数据
    
    @dataclass
    class Action:
        type: ActionType               # 动作类型(EMOJI, HOTKEY, TTS, SUBTITLE等)
        params: Dict[str, Any]         # 动作参数
        priority: int                  # 优先级
        timestamp: float = time.time()
```

**验收标准**:
- [ ] Intent数据类定义清晰
- [ ] 响应解析器支持多种MessageBase格式
- [ ] 情感判断功能正常(可选)
- [ ] 动作提取功能正常
- [ ] 单元测试覆盖率>80%

---

### 任务3.8: AmaidesuCore重构 (2-3天)

**目标**: 简化AmaidesuCore,移除WebSocket/HTTP/Router代码

**删除代码**:
- ❌ WebSocket连接管理(~150行)
- ❌ HTTP服务器管理(~100行)
- ❌ Router相关(~150行)
- ❌ send_to_maicore()方法(~50行)
- ❌ _handle_maicore_message()方法(~50行)
- ❌ _setup_maicore_connection()方法(~50行)
- ❌ _setup_http_server()方法(~30行)
- ❌ _start_http_server_internal()方法(~30行)
- ❌ _stop_http_server_internal()方法(~20行)

**保留代码**:
- ✅ EventBus管理(~100行)
- ✅ Pipeline管理(~100行)
- ✅ Context管理(~50行)
- ✅ Avatar管理器(~30行)
- ✅ LLM客户端管理(~20行)
- ✅ 决策层管理(新增~50行)

**新增代码**:
- ✅ DecisionManager集成(~50行)

**验收标准**:
- [ ] 代码量从642行减少到~350行
- [ ] 删除所有外部通信相关代码
- [ ] 所有原有内部协调功能保留
- [ ] DecisionManager正常集成
- [ ] 向后兼容保持(通过DecisionProvider间接连接MaiCore)

---

## 🔄 依赖关系

```
任务3.1: CanonicalMessage设计
└─ 无依赖

任务3.2: DecisionProvider接口
└─ 无依赖

任务3.3: DecisionManager
├─ 任务3.2: DecisionProvider接口
└─ Phase 1: EventBus

任务3.4: MaiCoreDecisionProvider
├─ 任务3.2: DecisionProvider接口
├─ Phase 1: EventBus
└─ Phase 2: DataCache(可选)

任务3.5: LocalLLMDecisionProvider
├─ 任务3.2: DecisionProvider接口
└─ Phase 1: EventBus

任务3.6: RuleEngineDecisionProvider
├─ 任务3.2: DecisionProvider接口
└─ Phase 1: EventBus

任务3.7: Layer 4: Understanding
├─ 任务3.1: CanonicalMessage设计
└─ Phase 1: EventBus

任务3.8: AmaidesuCore重构
├─ 任务3.3: DecisionManager
├─ Phase 1: EventBus
├─ Phase 2: DataCache(可选)
└─ Phase 1: PipelineManager
```

---

## 🚀 实施顺序

### 串行执行(关键路径)

1. **任务3.1**: CanonicalMessage设计(1天)
   - 创建数据结构
   - 编写MessageBuilder工具

2. **任务3.2**: DecisionProvider接口(1天)
   - 定义接口规范
   - 编写接口文档

3. **任务3.3**: DecisionManager(1天)
   - 实现工厂模式
   - 添加测试

4. **任务3.4**: MaiCoreDecisionProvider(2天)
   - Day 1: WebSocket迁移,基础连接
   - Day 2: HTTP回调迁移,完整测试

5. **任务3.5**: LocalLLMDecisionProvider(2天)
   - Day 1: API集成,基础决策
   - Day 2: 提示词优化,错误处理

6. **任务3.6**: RuleEngineDecisionProvider(2天)
   - Day 1: 规则引擎核心
   - Day 2: 规则匹配,动作配置

7. **任务3.7**: Layer 4: Understanding(3天)
   - Day 1: Intent数据类
   - Day 2: 响应解析器
   - Day 3: 集成测试

8. **任务3.8**: AmaidesuCore重构(1天)
   - 删除外部通信代码
   - 集成DecisionManager
   - 测试所有功能

### 并行执行(非关键路径)

- **任务3.5** 和 **任务3.6** 可并行(两个可选Provider)
- **文档编写** 在实现时同步进行

---

## ⚠️ 风险控制

### 风险1: WebSocket迁移破坏连接稳定性
- **概率**: 高
- **影响**: MaiCore连接中断
- **缓解**: 
  - 1. 详细记录现有WebSocket连接逻辑
  - 2. 逐步迁移,每步测试
  - 3. 保留旧代码作为回退
  - 4. 添加详细的连接/断开日志

### 风险2: HTTP回调遗漏
- **概率**: 中
- **影响**: MaiCore无法回调
- **缓解**:
  - 1. 梳理所有HTTP回调路径
  - 2. 添加路由注册日志
  - 3. 测试所有回调场景

### 风险3: DecisionProvider切换导致服务中断
- **概率**: 中
- **影响**: 运行时切换Provider可能失败
- **缓解**:
  - 1. 切换前验证新Provider配置
  - 2. 原子先启动新Provider再关闭旧Provider
  - 3. 提供回退机制(切换失败时自动回退)

### 风险4: MessageBase兼容性破坏
- **概率**: 中
- **影响**: 新格式可能导致旧插件无法工作
- **缓解**:
  - 1. CanonicalMessage保留original_message字段
  - 2. MessageBuilder提供双向转换
  - 3. 详细测试所有插件消息

### 风险5: AmaidesuCore简化后功能缺失
- **概率**: 低
- **影响**: 某些边缘功能可能遗漏
- **缓解**:
  - 1. 逐行检查所有方法调用
  - 2. 对每个方法标记保留/删除
  - 3. 编写完整的测试覆盖

---

## ✅ 验收标准

### 功能验收
- [ ] MaiCore连接正常(通过MaiCoreDecisionProvider)
- [ ] HTTP回调正常接收
- [ ] DecisionManager支持3种Provider(MaiCore/LocalLLM/RuleEngine)
- [ ] 运行时切换DecisionProvider无中断
- [ ] Layer 4正确解析MessageBase为Intent

### 性能验收
- [ ] 决策延迟无明显增加(<50ms)
- [ ] WebSocket连接稳定性不降低
- [ ] EventBus事件吞吐量正常

### 稳定性验收
- [ ] 单元测试覆盖率>80%
- [ ] 异常处理完善,无未捕获的异常
- [ ] 连接断开后自动重连机制正常

### 文档验收
- [ ] Provider接口文档清晰
- [ ] DecisionManager文档清晰
- [ ] CanonicalMessage文档清晰
- [ ] Layer 4文档清晰
- [ ] 迁移指南详细

### 向后兼容
- [ ] 所有现有插件无需修改即可工作(通过DecisionProvider)
- [ ] 配置格式保持兼容(DecisionProvider配置在[decision]下)
- [ ] EventBus事件名称稳定

---

## 🗺️ 相关文档

- [Phase 1: 某础设施](../phase1_infrastructure.md)
- [Phase 2: 输入层](../phase2_input.md)
- [6层架构设计](../../design/layer_refactoring.md)
- [决策层设计](../../design/decision_layer.md)
- [核心重构设计](../../design/core_refactoring.md)
