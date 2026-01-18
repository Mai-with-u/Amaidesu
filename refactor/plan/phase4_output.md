# Phase 4: 输出层重构 (10-14天)

> **目标**: 实现表现生成层和渲染呈现层,迁移所有输出型插件
> **依赖**: Phase 1,2, 3完成(Provider接口、EventBus、DataCache、InputLayer、DecisionLayer)
> **风险**: 中(输出功能多样化,需要保证兼容性)

---

## 📋 阶段概述

本阶段实现表现生成层(Layer 5)和渲染呈现层(layer 6),将5个输出型插件(TTS、Subtitle、Sticker、VTS、OmniTTS)迁移到新的Provider架构。这是用户体验的关键层,需要保证高质量和稳定性。

---

## 🎯 任务分解

### 任务4.1: Layer 5: Expression生成层设计 (1-2天)

**目标**: 设计Intent到RenderParameters的映射逻辑

**范围**:
- [ ] `src/expression/render_parameters.py` - RenderParameters类定义
- [ ] `src/expression/expression_generator.py` - Expression生成器
- [ ] `src/expression/emotion_mapper.py` - 情感映射
- [ ] `src/expression/action_mapper.py` - 动作映射

**核心逻辑**:
```
Intent {
    original_text: str           # 原始文本
    emotion: EmotionType        # 情感类型
    response_text: str        # 回复文本
    actions: List[Action]    # 动作列表
    metadata: Dict              # 扩展数据
}

ExpressionGenerator {
    generate(intent: Intent) -> RenderParameters:
        expressions = EmotionMapper.map(emotion)  # 情感映射到表情参数
        tts_text = intent.response_text           # TTS文本
        subtitle_text = intent.response_text       # 字幕文本
        hotkeys = ActionMapper.map(actions)   # 动作映射到热键
        ...
}
```

**验收标准**:
- [ ] RenderParameters结构清晰,支持所有表情和动作类型
- [ ] 情感映射覆盖常见情感(HAPPY, SAD, ANGRY, SURPRISED, SHY)
- [ ] 动作映射支持TTS热键、表情贴纸、道具加载等
- [ ] 单元测试覆盖率>80%

---

### 任务4.2: Layer 6: Rendering层接口 (1-2天)

**目标**: 定义OutputProvider接口,统一渲染行为

**范围**:
- [ ] `src/core/providers/output_provider.py` - OutputProvider接口
- [ ] `src/core/providers/__init__.py` - 接口导出

**接口规范**:
```python
class OutputProvider(Protocol):
    async def setup(self, event_bus: EventBus, config: dict):
        """
        设置Provider,订阅Expression层事件
        
        Args:
            event_bus: 事件总线
            config: Provider配置
        """
        ...
    
    async def render(self, parameters: RenderParameters):
        """
        渲染输出
        
        Args:
            parameters: 渲染参数(包含expressions/tts_text/subtitle_text/hotkeys)
        """
        ...
    
    async def cleanup(self):
        """清理资源"""
        ...
    
    def get_info(self) -> Dict[str, Any]:
        """获取Provider信息"""
        ...
```

**验收标准**:
- [ ] OutputProvider接口定义清晰
- [ ] 类型注解完整
- [ ] 示例代码齐全

---

### 任务4.3: OutputProviderManager实现 (2天)

**目标**: 管理多个OutputProvider,支持并发渲染

**范围**:
- [ ] `src/core/output_provider_manager.py` - OutputProviderManager类
- [ ] 并发启动所有Provider
- [ ] 错误隔离(单个Provider失败不影响其他)
- [ ] 生命周期管理

**核心功能**:
```python
class OutputProviderManager:
    async def start_all_providers(self, providers: List[OutputProvider]):
        """并发启动所有OutputProvider"""
        ...
    
    async def render_all(self, parameters: RenderParameters):
        """并发渲染到所有OutputProvider"""
        ...
    
    async def stop_all_providers(self):
        """优雅停止所有Provider"""
        ...
    
    def get_stats(self) -> Dict[str, Any]:
        """获取所有Provider的统计信息"""
        ...
```

**验收标准**:
- [ ] 所有Provider并发启动正常
- [ ] 并发渲染无冲突(如多个Provider同时操作同一资源)
- [ ] 错误隔离生效
- [ ] 统计信息准确

---

### 任务4.4: TTSProvider实现 (2-3天)

**目标**: 将TTS插件迁移为OutputProvider

**范围**:
- [ ] `src/providers/tts_provider.py` - TTSProvider实现
- [ ] 支持Edge TTS和Omni TTS引擎
- [ ] 集成text_cleanup服务
- [ ] 集成vts_lip_sync服务

**接口适配**:
```python
class TTSProvider(OutputProvider):
    async def setup(self, event_bus: EventBus, config: dict):
        # 订阅expression.parameters_generated事件
        event_bus.on("expression.parameters_generated", self.on_parameters)
        
        # 订阅audio.playing事件(可选)
        # event_bus.on("audio.playing", self.on_audio_playing)
    
    async def render(self, parameters: RenderParameters):
        # 合成语音
        # 播放语音
        # 通知vts_lip_sync服务进行口型同步
```

**验收标准**:
- [ ] TTS语音正常合成和播放
- [ ] 口型同步正常(如果启用)
- [ ] 音频播放完成通知subtitle_service
- [ ] 错误处理完善(降级到备用播放方案)

---

### 任务4.5: SubtitleProvider实现 (2-3天)

**目标**: 将Subtitle插件迁移为OutputProvider

**范围**:
- [ ] `src/providers/subtitle_provider.py` - SubtitleProvider实现
- [ ] 窗口管理
- [ ] 文本样式配置
- [ ] 支持多语言

**接口适配**:
```python
class SubtitleProvider(OutputProvider):
    async def setup(self, event_bus: EventBus, config: dict):
        # 订阅expression.parameters_generated事件
        event_bus.on("expression.parameters_generated", self.on_parameters)
    
    async def render(self, parameters: RenderParameters):
        if parameters.subtitle_text:
            self.display(parameters.subtitle_text)
```

**验收标准**:
- [ ] 字幕显示正常
- [ ] 支持配置的字体、大小、位置
- [ ] 支持多种语言(如需要)
- [ ] 错误处理完善(窗口创建失败时降级)

---

### 任务4.6: StickerProvider实现 (2-3天)

**目标**: 将Sticker插件迁移为OutputProvider

**范围**:
- [ ] `src/providers/sticker_provider.py` - StickerProvider实现
- [ ] VTS道具加载API调用
- [ ] 贴纸/道具配置管理

**接口适配**:
```python
class StickerProvider(OutputProvider):
    async def setup(self, event_bus: EventBus, config: dict):
        # 订阅expression.parameters_generated事件
        event_bus.on("expression.parameters_generated", self.on_parameters)
    
    async def render(self, parameters: RenderParameters):
        # 加载道具
        # 显示道具
        # 卸载道具
        # 更新道具
```

**验收标准**:
- [ ] 道具加载和卸载正常
- [ ] 道具显示正常
- [ ] 道具卸载完成
- [ ] 错误处理完善(道具加载失败时跳过)

---

### 任务4.7: VTSProvider实现 (2-3天)

**目标**: 将VTubeStudio插件迁移为OutputProvider,保留所有功能

**范围**:
- [ ] `src/providers/vts_provider.py` - VTSProvider实现
- [ ] 热键触发
- [ ] 表情控制
- [ LIP同步(保留功能)
- ] 道具管理
- [ ] LLM智能热键匹配(保留功能)

**接口适配**:
```python
class VTSProvider(OutputProvider):
    async def setup(self, event_bus: EventBus, config: dict):
        # 订阅expression.parameters_generated事件
        event_bus.on("expression.parameters_generated", self.on_parameters)
        
        # 订阅audio.playing事件(用于LIP同步,可选)
        # event_bus.on("audio.playing", self.on_audio_playing)
    
    async def render(self, parameters: RenderParameters):
        # 应用表情参数
        # 触发热键
        # 更新道具
        # ...
    
    # 保留现有方法
    async def smile(self, value: float) -> bool:
        ...
    async def close_eyes(self) -> bool:
        ...
    async def open_eyes(self) -> bool:
        ...
```

**验收标准**:
- [ ] 所有现有功能正常工作(表情、热键、道具管理、LIP同步)
- [ ] Expression事件正确解析和处理
- [ ] 错误处理完善
- [ ] 单元测试覆盖率>70%

---

### 任务4.8: OmniTTSProvider实现 (2天)

**目标**: 将OmniTTS插件迁移为OutputProvider

**范围**:
- [ ] `src/providers/omni_tts_provider.py` - OmniTTSProvider实现
- [ ] OmniTTS引擎集成
- [ ] 合成优化和缓存

**接口适配**:
```python
class OmniTTSProvider(OutputProvider):
    async def setup(self, event_bus: EventBus, config: dict):
        # 订阅expression.parameters_generated事件
        event_bus.on("expression.parameters_generated", self.on_parameters)
    
    async def render(self, parameters: RenderParameters):
        # 合成语音
        # 播放语音
```

**验收标准**:
- [ ] OmniTTS引擎集成正常
- [ ] 语音合成质量良好
- [ ] 性能达标(平均延迟<2s)

---

## 🔄 依赖关系

```
任务4.1: Expression生成层
├─ 任务4.2: Rendering层接口
└─ 任务4.3: OutputProviderManager

任务4.4: TTSProvider
├─ Phase 1: Provider接口
├─ Phase 2: EventBus(增强)
├─ Phase 2: DataCache(可选,用于存储缓存音频)
├─ 任务4.3: OutputProviderManager

任务4.5: SubtitleProvider
├─ Phase 1: Provider接口
├─ Phase 2: EventBus
├─ 任务4.3: OutputProviderManager

任务4.6: StickerProvider
├─ Phase 1: Provider接口
├─ Phase 2: EventBus
├─ 任务4.3: OutputProviderManager
├─ 任务4.7: VTSProvider(间接依赖vts_control服务)

任务4.7: VTSProvider
├─ Phase 1: Provider接口
├─ Phase 2: EventBus
├─ 任务4.3: OutputProviderManager
├─ Phase 2: AvatarManager(通过vts_control服务访问)

任务4.8: OmniTTSProvider
├─ Phase 1: Provider接口
├─ Phase 2: EventBus
├─ 任务4.3: OutputProviderManager
└─ Phase 2: AvatarManager(可选,获取配置)
```

---

## 🚀 实施顺序

### 串行执行(必须遵守)

1. Layer 5: Expression生成层
   - 先定义数据结构,再实现生成逻辑
   - 越后迁移时才有Expression生成

2. Layer 6: Rendering层接口
   - 所有Provider依赖Rendering层接口

3. OutputProviderManager
   - 所有Provider管理器依赖此Manager

4. 各个Provider实现(可部分并行)
   - 由于各Provider独立,可以并行开发
   - 但测试时需要一个一个一个验证

### 关键路径

**Phase 3的DecisionProvider → Layer 4: Understanding**:
```
DecisionProvider.decide(canonical_message)
    ↓
Layer 4: Understanding.on_decision_generated(event)
    ↓
emit("understanding.intent.generated")
```

**Layer 4: Understanding → Layer 5: Expression**:
```
emit("understanding.intent.generated")
    ↓
Layer 5: ExpressionGenerator.generate(intent)
    ↓
emit("expression.parameters_generated")
```

**Layer 5: Expression → Layer 6: Rendering**:
```
emit("expression.parameters_generated")
    ↓
OutputProviderManager.render_all(parameters)
    ↓
TTSProvider.render()
SubtitleProvider.render()
StickerProvider.render()
```

---

## ⚠️ 风险控制

### 风险1: Expression生成逻辑不完善
- **概率**: 高
- **影响**: 情感和动作可能不准确
- **缓解**: 
  - 1. 详细设计各种情感和动作的映射规则
  - 2. 添加规则引擎支持动态调整
  - 3. 提供A/B测试案例

### 风险2: 多Provider并发冲突
- **概率**: 中
- **影响**: 同时操作同一资源(如VTS参数、窗口)
- **缓解**:
  - 1. 使用asyncio.Lock保护共享资源访问
  - 2. OutputProviderManager协调资源访问顺序
  - 3. 添加优先级控制(如TTS在窗口显示后更新)

### 风险3: LIP同步时机不准确
- **概率**: 低
- **影响**: 口型同步可能提前或延迟
- **缓解**:
  - 1. 添加播放事件监听(audio.playing)
  - 2. 使用累积策略平滑口型参数
  - 3. 提供手动控制接口

### 风险4: VTSProvider保留旧代码导致代码臃肿
- **概率**: 中
- **影响**: 代码维护困难,不符合Provider单一职责
- **缓解**:
  - 1. 拆分VTSProvider为子类(继承OutputProvider + 扩展功能)
  - 2. 使用组合模式而非继承
  - 3. 将扩展功能迁移到独立组件

### 风险5: 各Provider迁移可能遗漏边缘情况
- **概率**: 中
- **影响**: 某些边缘情况可能未覆盖
- **缓解**:
  - 1. 详细对比旧插件的所有功能
  - 2. 编写迁移对比测试
  - 3. 保留降级方案

---

## ✅ 验收标准

### 功能验收
- [ ] 所有输出功能正常工作(TTS语音、字幕、贴纸、VTS控制等)
- [ ] Expression生成逻辑正确处理各种Intent和情感
- [ ] 多Provider并发无冲突
- [ ] 错误隔离生效,单个失败不影响其他

### 性能验收
- [ ] 音频播放延迟<3s(可配置)
- [ ] 表情更新延迟<100ms
- [ ] 多Provider并发不影响系统整体性能

### 兼容性验收
- [ ] 现有插件功能完整保留
- [ ] 新架构下系统响应时间不增加
- [ ] 配置简化(移除输出插件的独立配置,统一在[rendering.outputs]配置)

### 稳定性验收
- [ ] 长时间运行无内存泄漏
- [ ] 所有Provider可独立启停
- [ ] 异常处理完善,无未捕获的异常

### 文档验收
- [ ] Provider接口文档清晰
- [ ] Expression生成文档完整
- [ ] 各个Provider迁移指南详细
- [ ] 性能优化建议文档

---

## 🗺️ 迁移指南

### 从插件到Provider的步骤

**1. 分析旧插件**:
   - 列出所有功能点
   - 识别独立功能(如TTS引擎、窗口管理、LIP同步等)
   - 识别服务依赖(text_cleanup, vts_lip_sync, subtitle_service等)

**2. 设计Provider结构**:
   - 保留核心功能,移除冗余代码
   - 将独立功能拆分为独立方法
   - 添加清晰的错误处理

**3. 适配接口**:
   - 构造RenderParameters对象
   - 实现setup()方法(订阅EventBus)
   - 实现render()方法(处理渲染逻辑)
   - 实现cleanup()方法(清理资源)

**4. 处理服务依赖**:
   - 获取服务的正确方式(get_service或通过EventBus)
   - 处理服务不存在的情况(提供默认行为)
   - 将get_service调用替换为EventBus订阅

**5. 迁移测试**:
   - 对比旧插件和新Provider的功能
   - 确保没有遗漏
   - 测试边缘情况

---

## 🔗 相关文档

- [Phase 1: 基础设施](./phase1_infrastructure.md)
- [Phase 2: 输入层](./phase2_input.md)
- [Phase 3: 决策层+中间层](./phase3_decision.md)
- [6层架构设计](../design/layer_refactoring.md)
- [多Provider并发设计](../design/multi_provider.md)
- [DataCache设计](../design/data_cache.md)
- [Expression生成层设计](../design/expression_layer.md)
- [Rendering层设计](../design/rendering_layer.md)
