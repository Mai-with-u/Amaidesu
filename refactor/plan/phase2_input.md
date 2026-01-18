# Phase 2: 输入层重构 (7-10天)

> **目标**: 实现Layer 1(输入感知)和Layer 2(输入标准化),迁移2个简单输入插件
> **依赖**: Phase 1完成(Provider接口、EventBus、DataCache、ConfigLoader)
> **风险**: 中(需要保证现有输入功能正常)

---

## 📋 阶段概述

本阶段实现输入层的前两层,建立标准化的数据输入流程。将2个简单插件(ConsoleInput、MockDanmaku)迁移到新的6层架构,作为后续迁移的示例。

---

## 🎯 任务分解

### 任务2.1: RawData和NormalizedText定义 (1-2天)

**目标**: 定义数据结构,支持Layer 1和Layer 2

**范围**:
- [ ] `src/core/raw_data.py` - RawData类定义
- [ ] `src/core/normalized_text.py` - NormalizedText类定义
- [ ] `src/core/data_types/__init__.py` - 数据类型导出

**数据结构**:
```python
# RawData: Layer 1输出
class RawData:
    def __init__(self, content: Any, source: str, metadata: dict = None, 
                 preserve_original: bool = False, original_data: Any = None):
        self.content = content
        self.source = source
        self.metadata = metadata or {}
        self.timestamp = time.time()
        self.preserve_original = preserve_original
        self.original_data = original_data
        self.data_ref = None  # DataCache引用

# NormalizedText: Layer 2输出
class NormalizedText:
    def __init__(self, text: str, metadata: dict, data_ref: Optional[str] = None):
        self.text = text
        self.metadata = metadata
        self.data_ref = data_ref  # DataCache引用,按需加载原始数据
```

**验收标准**:
- [ ] 所有数据类定义完成
- [ ] 类型注解完整
- [ ] 文档字符串清晰
- [ ] 通过Python类型检查

---

### 任务2.2: InputProviderManager实现 (2-3天)

**目标**: 实现多Provider并发管理,支持错误隔离和生命周期管理

**范围**:
- [ ] `src/core/input_provider_manager.py` - InputProviderManager类
- [ ] 错误隔离机制(单个Provider失败不影响其他)
- [ ] 生命周期管理(start/stop/cleanup)
- [ ] 统计功能(运行时长、消息计数)

**核心功能**:
```python
class InputProviderManager:
    async def start_all_providers(self, providers: List[InputProvider]):
        """并发启动所有InputProvider,错误隔离"""
    
    async def stop_all_providers(self):
        """优雅停止所有InputProvider"""
    
    async def get_stats(self) -> Dict[str, Any]:
        """获取所有Provider的统计信息"""
    
    def get_provider_by_source(self, source: str) -> Optional[InputProvider]:
        """根据source获取Provider实例"""
```

**验收标准**:
- [ ] 多Provider并发启动正常
- [ ] 错误隔离生效(单个异常不影响其他)
- [ ] 优雅停止功能正常
- [ ] 统计信息准确

---

### 任务2.3: ConsoleInputProvider迁移 (2-3天)

**目标**: 将ConsoleInputPlugin迁移为InputProvider

**范围**:
- [ ] `src/perception/text/console_input_provider.py` - InputProvider实现
- [ ] `src/perception/text/__init__.py` - 模块导出

**迁移内容**:
- 从现有插件提取输入逻辑
- 适配InputProvider接口
- 支持命令处理(exit(), /gift, /sc, /guard)
- 保留上下文标签功能

**接口适配**:
```python
class ConsoleInputProvider(InputProvider):
    async def start(self) -> AsyncIterator[RawData]:
        """启动控制台输入,返回RawData流"""
        while True:
            line = await self._read_input()
            if line == "exit()":
                break
            yield RawData(
                content=line,
                source="console",
                metadata={"user": "local"}
            )
    
    async def stop(self):
        """停止输入"""
        self._running = False
    
    async def cleanup(self):
        """清理资源"""
        pass
```

**验收标准**:
- [ ] 控制台输入正常工作
- [ ] 命令处理功能保留(exit, gift, sc, guard)
- [ ] RawData格式正确
- [ ] 上下文标签功能保留(通过EventBus)

---

### 任务2.4: MockDanmakuProvider迁移 (1-2天)

**目标**: 将MockDanmakuPlugin迁移为InputProvider

**范围**:
- [ ] `src/perception/text/mock_danmaku_provider.py` - InputProvider实现
- [ ] 随机弹幕生成逻辑

**接口适配**:
```python
class MockDanmakuProvider(InputProvider):
    async def start(self) -> AsyncIterator[RawData]:
        """生成模拟弹幕"""
        while self._running:
            await asyncio.sleep(random.uniform(1, 3))
            text = self._generate_random_danmaku()
            yield RawData(
                content=text,
                source="mock_danmaku",
                metadata={"user": f"user_{random.randint(1000, 9999)}"}
            )
```

**验收标准**:
- [ ] 随机弹幕生成正常
- [ ] RawData格式正确
- [ ] 可配置生成频率

---

### 任务2.5: InputLayer集成 (1-2天)

**目标**: 将Provider与EventBus集成,建立Layer 1→Layer 2数据流

**范围**:
- [ ] `src/perception/input_layer.py` - 输入层协调器
- [ ] 事件定义(perception.raw_data_generated)

**数据流**:
```
InputProvider.start() → emit("perception.raw_data.generated", {data: RawData, source: source})
                                        ↓
InputLayer.on_raw_data_generated()
                                        ↓
Normalizer.normalize(RawData) → NormalizedText
                                        ↓
emit("normalization.text.ready", {normalized: NormalizedText})
```

**验收标准**:
- [ ] 数据流正确: RawData → NormalizedText
- [ ] EventBus事件正确发布
- [ ] DataCache引用正确生成
- [ ] 多Provider并发数据正确汇聚

---

## 🔄 依赖关系

```
任务2.1: 数据类型定义
├─ 无依赖

任务2.2: InputProviderManager
├─ 任务2.1: 数据类型定义
└─ Phase 1: EventBus

任务2.3: ConsoleInputProvider迁移
├─ 任务2.1: 数据类型定义
└─ 任务2.2: InputProviderManager

任务2.4: MockDanmakuProvider迁移
├─ 任务2.1: 数据类型定义
└─ 任务2.2: InputProviderManager

任务2.5: InputLayer集成
├─ 任务2.1: 数据类型定义
├─ 任务2.2: InputProviderManager
├─ 任务2.3: ConsoleInputProvider
├─ 任务2.4: MockDanmakuProvider
├─ Phase 1: EventBus
└─ Phase 1: DataCache
```

---

## 🚀 实施顺序

### 串行部分(核心依赖)
1. 数据类型定义(2.1)
2. InputProviderManager实现(2.2)
3. InputLayer集成(2.5)

### 并行部分(可同时开始)
4. ConsoleInputProvider迁移(2.3)
5. MockDanmakuProvider迁移(2.4)

---

## ⚠️ 风险控制

### 风险1: InputProviderManager并发问题
- **概率**: 中
- **影响**: 多Provider启动顺序混乱
- **缓解**: 使用asyncio.gather包装每个Provider启动

### 风险2: 数据类型不完善
- **概率**: 低
- **影响**: 后续阶段需要修改
- **缓解**: 充分设计,预留扩展字段

### 风险3: 现有插件功能丢失
- **概率**: 中
- **影响**: 命令处理、上下文标签等
- **缓解**: 详细迁移测试,保留所有功能

### 风险4: EventBus事件命名冲突
- **概率**: 低
- **影响**: 后续阶段订阅混乱
- **缓解**: 使用事件命名规范(perception.*、normalization.*)

---

## ✅ 验收标准

### 功能验收
- [ ] ConsoleInput和MockDanmaku在新架构下正常工作
- [ ] RawData→NormalizedText数据流正确
- [ ] 多Provider并发正常,错误隔离生效
- [ ] DataCache引用正常工作(可选保留原始数据)
- [ ] 所有输入功能保留(命令、上下文标签)

### 性能验收
- [ ] 输入延迟无明显增加(<50ms)
- [ ] 多Provider并发不影响性能
- [ ] DataCache命中率合理

### 质量验收
- [ ] 代码符合项目规范(导入顺序、类型注解、日志)
- [ ] 单元测试覆盖率>80%
- [ ] 无LSP错误
- [ ] 文档清晰,示例代码完整

### 文档验收
- [ ] Provider接口文档完整
- [ ] 数据类型文档清晰
- [ ] 迁移指南详细
- [ ] 新旧架构对比说明

---

## 🗺️ 迁移指南

### 从旧插件到新Provider

**旧插件结构**:
```python
class BiliDanmakuPlugin(BasePlugin):
    async def setup(self):
        # 注册WebSocket处理器
        await self.core.register_websocket_handler("text", self.handle_message)
    
    async def handle_message(self, message: MessageBase):
        # 处理消息
        await self.core.send_to_maicore(message)
```

**新Provider结构**:
```python
class BilibiliDanmakuProvider(InputProvider):
    async def start(self) -> AsyncIterator[RawData]:
        """生成弹幕数据流"""
        while True:
            # ... 获取弹幕
            yield RawData(...)
    
    async def cleanup(self):
        """清理资源"""
        pass
```

### 事件映射

| 旧方式 | 新方式 |
|--------|--------|
| `core.register_websocket_handler()` | EventBus订阅事件 |
| `core.send_to_maicore()` | EventBus发布事件 |
| 服务注册调用 | EventBus发布/订阅 |
| WebSocket消息分发 | Layer间数据流 |

---

## 🔗 相关文档

- [Phase 1: 基础设施](./phase1_infrastructure.md)
- [6层架构设计](../design/layer_refactoring.md)
- [多Provider并发设计](../design/multi_provider.md)
- [DataCache设计](../design/data_cache.md)
