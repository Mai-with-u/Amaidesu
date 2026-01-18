# Phase 1: 基础设施重构 (5-7天)

> **目标**: 建立新架构的基础设施,为后续重构提供支撑
> **依赖**: 无(独立阶段)
> **风险**: 低

---

## 📋 阶段概述

本阶段建立新架构的核心基础设施,包括Provider接口定义、EventBus增强、DataCache实现和配置系统迁移。这些基础设施不依赖现有代码,可以安全地并行开发。

---

## 🎯 任务分解

### 任务1.1: Provider接口定义 (1-2天)

**目标**: 定义清晰的Provider接口规范

**范围**:
- [ ] `src/core/providers/input_provider.py` - InputProvider接口
- [ ] `src/core/providers/output_provider.py` - OutputProvider接口  
- [ ] `src/core/providers/decision_provider.py` - DecisionProvider接口
- [ ] `src/core/providers/__init__.py` - 接口导出

**接口规范**:

```python
# InputProvider接口
class InputProvider:
    async def start(self) -> AsyncIterator[RawData]: ...
    async def stop(self): ...
    async def cleanup(self): ...

# OutputProvider接口
class OutputProvider:
    async def setup(self, event_bus: EventBus): ...
    async def render(self, parameters: RenderParameters): ...
    async def cleanup(self): ...

# DecisionProvider接口
class DecisionProvider:
    async def setup(self, event_bus: EventBus, config: dict): ...
    async def decide(self, canonical_message: CanonicalMessage) -> MessageBase: ...
    async def cleanup(self): ...
```

**验收标准**:
- [ ] 所有Provider接口定义完成
- [ ] 类型注解完整
- [ ] 文档字符串齐全
- [ ] 通过Python类型检查

---

### 任务1.2: EventBus增强 (2天)

**目标**: 增强EventBus,支持错误隔离、优先级、统计

**范围**:
- [ ] 添加错误隔离机制(try-catch包裹handler)
- [ ] 添加优先级控制(handler可设置priority)
- [ ] 添加统计功能(emit/on调用计数、错误率)
- [ ] 添加生命周期管理(cleanup事件)

**新功能**:
```python
class EventBus:
    # 新增: 错误隔离
    emit(self, event_name: str, data: Any, source: str = "unknown", error_isolate: bool = False): ...
    
    # 新增: 优先级
    on(self, event_name: str, handler: Callable, priority: int = 100): ...
    
    # 新增: 统计
    get_stats(self, event_name: str) -> Dict[str, int]: ...
    
    # 新增: 生命周期
    cleanup(self): ...
```

**验收标准**:
- [ ] 错误隔离正常工作(单个handler异常不影响其他)
- [ ] 优先级控制生效
- [ ] 统计功能正常
- [ ] 生命周期清理正常

---

### 任务1.3: DataCache实现 (2天)

**目标**: 实现原始数据缓存服务,支持EventBus传递大对象

**范围**:
- [ ] `src/core/data_cache.py` - DataCache接口
- [ ] `src/core/data_cache/memory_cache.py` - MemoryDataCache实现
- [ ] `src/core/data_cache/__init__.py` - 模块导出

**功能**:
- TTL过期机制
- LRU淘汰机制
- 混合淘汰策略(TTL_OR_LRU)
- 线程安全(asyncio.Lock + threading.Lock双重保护)
- 统计信息(hits, misses, evictions, size)

**接口规范**:
```python
class DataCache:
    async def store(self, data: Any, ttl: int = None, tags: Dict = None) -> str: ...
    async def retrieve(self, data_ref: str) -> Any: ...
    async def delete(self, data_ref: str) -> bool: ...
    async def clear(self): ...
    def get_stats(self) -> CacheStats: ...
    async def find_by_tags(self, tags: Dict) -> List[str]: ...
```

**验收标准**:
- [ ] TTL过期机制正常
- [ ] LRU淘汰机制正常
- [ ] 线程安全保证
- [ ] 统计功能准确
- [ ] 支持多种数据类型

---

### 任务1.4: 配置系统迁移 (2-3天)

**目标**: 支持新配置格式,向后兼容旧格式

**范围**:
- [ ] `src/config/config_loader.py` - 新配置加载器
- [ ] `src/config/config.py` - 配置类定义
- [ ] 配置转换工具(旧→新格式)

**新配置格式**:
```toml
[perception]
inputs = ["danmaku", "console", "mock"]

[perception.inputs.danmaku]
type = "bilibili_danmaku"
room_id = "123456"
enabled = true

[rendering]
outputs = ["tts", "subtitle", "vts"]

[decision]
default_provider = "maicore"

[plugins]
enabled = ["console_input", "tts"]
```

**向后兼容策略**:
- 保留`enable_xxx = true`格式
- 优先级: `enabled`列表 > 单独配置
- 自动转换旧配置到新格式

**验收标准**:
- [ ] 新配置格式可正常加载
- [ ] 旧配置格式可正常转换
- [ ] 配置合并逻辑正确(全局>插件独立)
- [ ] 错误提示清晰

---

## 🔄 依赖关系

```
任务1.1 Provider接口
  └─ 无依赖

任务1.2 EventBus增强
  └─ 无依赖

任务1.3 DataCache实现
  └─ 无依赖

任务1.4 配置系统迁移
  └─ 无依赖
```

**所有任务可并行执行**

---

## 🚀 实施顺序

### 并行执行(第1-5天)
- Day 1: Provider接口定义
- Day 1-2: EventBus增强
- Day 3-4: DataCache实现
- Day 5-6: 配置系统迁移

### 集成测试(第7天)
- 确保所有基础设施协同工作
- 编写单元测试

---

## ⚠️ 风险控制

### 风险1: 接口定义不完善
- **概率**: 中
- **影响**: 后续阶段需要修改接口
- **缓解**: 充分设计,团队评审

### 风险2: EventBus增强破坏现有功能
- **概率**: 低
- **影响**: 现有插件可能受影响
- **缓解**: 保持向后兼容,渐进式增强

### 风险3: DataCache性能问题
- **概率**: 低
- **影响**: 系统整体性能
- **缓解**: 充分测试,性能基准测试

### 风险4: 配置转换错误
- **概率**: 中
- **影响**: 配置加载失败
- **缓解**: 详细日志,验证工具

---

## ✅ 验收标准

### 功能验收
- [ ] 所有Provider接口定义清晰、文档齐全
- [ ] EventBus增强功能正常,向后兼容
- [ ] DataCache功能正常,性能达标
- [ ] 配置系统支持新旧格式,自动转换

### 质量验收
- [ ] 代码符合项目规范(导入顺序、类型注解、日志)
- [ ] 单元测试覆盖率>80%
- [ ] 无LSP错误
- [ ] 文档完整

### 文档验收
- [ ] Provider接口文档清晰,示例代码齐全
- [ ] EventBus增强文档完整
- [ ] DataCache API文档齐全
- [ ] 配置格式说明清晰

---

## 🔗 相关文档

- [设计总览](../design/overview.md)
- [6层架构设计](../design/layer_refactoring.md)
- [多Provider并发设计](../design/multi_provider.md)
- [DataCache设计](../design/data_cache.md)
