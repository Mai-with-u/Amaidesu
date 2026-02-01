# Amaidesu 架构设计总览

## 📋 文档结构

本文档是Amaidesu架构重构的设计总览，**以 5 层架构为基准**，下述子文档的层号与本文一致。详细设计请查看：

 - [5层架构设计](./layer_refactoring.md) - 核心数据流的5层架构
 - [决策层设计](./decision_layer.md) - 可替换的决策Provider系统（含LLM意图解析）
 - [多Provider并发设计](./multi_provider.md) - 输入/输出层并发处理
 - [核心重构设计](./core_refactoring.md) - AmaidesuCore的彻底解耦
 - [事件数据契约设计](./event_data_contract.md) - 类型安全的事件数据契约系统
 - [LLM服务设计](./llm_service.md) - 统一的LLM调用服务
 - [Pipeline重新设计](./pipeline_refactoring.md) - 3类Pipeline系统（Pre/Post/Render）
 - [HTTP服务器设计](./http_server.md) - HTTP服务器管理

---

## 🎯 重构目标

### 核心问题

1. **过度插件化**：24个插件中，核心功能也作为插件
2. **依赖地狱**：18个插件使用服务注册，形成复杂依赖链
3. **模块定位模糊**：核心功能、可选插件、测试工具都作为插件
4. **职责边界不清**：Plugin在创建和管理Provider，导致管理分散

### 重构目标

1. **消灭插件化**：核心功能全部模块化，移除插件系统
2. **统一接口**：同一功能收敛到统一接口，用Provider模式动态选择实现
3. **消除依赖**：推广EventBus通信，替代服务注册模式
4. **按数据流组织**：按AI VTuber数据处理流程组织层级
5. **职责分离**：驱动与渲染分离，决策层可替换
6. **Provider直接管理**：Provider由Manager统一管理，不再通过Plugin

---

## 🏗️ 架构概览

### 核心理念

**按AI VTuber数据处理的完整流程组织层级，每层有明确的输入和输出格式。**

 - **核心数据流**（Layer 1-5）：按AI VTuber数据处理流程组织
 - **Provider直接管理**：Provider由Manager直接管理，配置驱动启用
 - **EventBus**：唯一的跨层通信机制，实现松耦合
 - **事件数据契约**（Event Contract）：类型安全的事件数据格式，支持社区扩展
 - **LLM服务**（LLM Service）：统一的LLM调用基础设施，与EventBus同级

### 为什么移除插件系统？

**设计文档的初衷**（已废弃）：
```
Provider = 原子能力（单一职责、可复用、统一管理）
Plugin = 能力组合（整合 Provider、提供业务场景、不创建 Provider）
```

**实际实现的问题**：
- ❌ Plugin在创建和管理Provider，违背了"不创建Provider"的设计原则
- ❌ Provider生命周期由Plugin管理，而不是Manager，导致管理分散
- ❌ 与"消灭插件化"的重构目标直接矛盾
- ❌ 增加了一层不必要的抽象，反而使架构更复杂

**新架构的优势**：
- ✅ Provider由Manager统一管理，生命周期清晰
- ✅ 配置驱动启用/禁用，无需修改代码
- ✅ 职责边界明确：Provider = 原子能力
- ✅ 代码组织更清晰：按数据流层级组织

### 5层架构（2025年最新版本）

```
外部输入（弹幕、游戏、语音）
  ↓
【Layer 1-2: Input】RawData → NormalizedMessage
  ├─ InputProvider: 并发采集 RawData
  ├─ TextPipeline: 限流、过滤、相似文本检测（可选）
  └─ InputLayer: 标准化为 NormalizedMessage
  ↓ normalization.message_ready
【Layer 3: Decision】NormalizedMessage → Intent
  ├─ MaiCoreDecisionProvider (默认，WebSocket+LLM意图解析)
  ├─ LocalLLMDecisionProvider (可选，直接LLM)
  └─ RuleEngineDecisionProvider (可选，规则引擎)
  ↓ decision.intent_generated
【Layer 4-5: Parameters+Rendering】Intent → RenderParameters → 输出
  ├─ ExpressionGenerator: Intent → RenderParameters
  └─ OutputProvider: 并发渲染（TTS、字幕、VTS等）
  ↓
【配置驱动】Provider管理 + Pipeline系统
```

**架构变化说明：**

1. **合并 Layer 1-2**: Input 和 Normalization 合并为 InputLayer
   - 减少数据转换开销
   - NormalizedMessage 直接包含 StructuredContent

2. **移除 UnderstandingLayer**: Intent 解析由 DecisionProvider 负责
   - MaiCoreDecisionProvider 使用 IntentParser (LLM)
   - LocalLLMDecisionProvider 直接生成响应
   - RuleEngineDecisionProvider 使用规则匹配

3. **简化 Pipeline**: TextPipeline 集成到 InputLayer
   - 在 RawData → NormalizedMessage 转换中处理文本
   - 不再单独的 Pre-Pipeline 层

4. **移除插件系统**: Provider由Manager直接管理
   - InputProviderManager 管理输入Provider
   - OutputProviderManager 管理输出Provider
   - DecisionManager 管理决策Provider
   - 配置驱动启用/禁用

---

## 📊 Provider管理架构

### Provider目录组织

```
src/
├── layers/
│   ├── input/                   # Layer 1-2: 输入层
│   │   ├── input_layer.py       # InputLayer（统一管理）
│   │   ├── input_provider_manager.py  # Provider管理器
│   │   └── providers/           # ✅ 所有输入Provider
│   │       ├── console_input_provider.py
│   │       ├── bili_danmaku_provider.py
│   │       ├── minecraft_event_provider.py
│   │       └── mock_danmaku_provider.py
│   │
│   ├── decision/                # Layer 3: 决策层
│   │   ├── decision_manager.py  # DecisionManager（统一管理）
│   │   ├── intent_parser.py     # LLM意图解析器
│   │   └── providers/           # ✅ 所有决策Provider
│   │       ├── maicore_decision_provider.py
│   │       ├── local_llm_decision_provider.py
│   │       ├── rule_engine_decision_provider.py
│   │       └── mock_decision_provider.py
│   │
│   ├── parameters/              # Layer 4: 参数生成
│   │   ├── parameters_layer.py
│   │   ├── emotion_mapper.py
│   │   ├── action_mapper.py
│   │   └── expression_mapper.py
│   │
│   └── output/                  # Layer 5: 渲染层
│       ├── rendering_manager.py # OutputProviderManager（统一管理）
│       └── providers/           # ✅ 所有输出Provider
│           ├── tts_provider.py
│           ├── subtitle_provider.py
│           ├── vts_provider.py
│           └── mock_tts_provider.py
│
└── core/
    └── pipelines/               # 3类Pipeline系统
        ├── pre/                 # Pre-Pipeline（处理NormalizedMessage）
        │   ├── rate_limit_pipeline.py
        │   ├── filter_pipeline.py
        │   └── similar_text_pipeline.py
        ├── post/                # Post-Pipeline（处理Intent，可选）
        │   └── format_cleanup_pipeline.py
        └── render/              # Render-Pipeline（处理Intent，可选）
            └── emotion_smoothing_pipeline.py
```

### 配置驱动启用

```toml
# 输入Provider配置
[input]
enabled = ["console", "bili_danmaku", "minecraft"]  # 启用的输入Provider

[input.providers.console]
source = "stdin"  # 控制台输入

[input.providers.bili_danmaku]
room_id = "123456"  # B站直播间ID

[input.providers.minecraft]
host = "localhost"
port = 25565

# 决策Provider配置
[decision]
default_provider = "maicore"  # 默认决策Provider

[decision.providers.maicore]
host = "localhost"
port = 8000

[decision.providers.local_llm]
model = "gpt-4"
api_key = "your_key"

# 输出Provider配置
[output]
enabled = ["tts", "subtitle", "vts"]  # 启用的输出Provider

[output.providers.tts]
engine = "gptsovits"
api_url = "http://localhost:5000"

[output.providers.subtitle]
font_size = 24
window_position = "bottom"

[output.providers.vts]
host = "localhost"
port = 8001
```

### 社区扩展

社区开发者如何添加新的Provider？

```python
# 1. 在对应层创建Provider文件
# src/layers/input/providers/my_input_provider.py

from src.core.providers.input_provider import InputProvider
from src.core.data_types.raw_data import RawData
from typing import AsyncIterator

class MyInputProvider(InputProvider):
    """自定义输入Provider"""

    def __init__(self, config: dict):
        super().__init__(config)
        self.logger = get_logger("MyInputProvider")

    async def _collect_data(self) -> AsyncIterator[RawData]:
        """采集数据"""
        while self.is_running:
            # 采集数据逻辑
            data = await self._fetch_data()
            if data:
                yield RawData(
                    content={"data": data},
                    source="my_provider",
                    data_type="text",
                )

# 2. 在配置中启用
# config.toml
[input]
enabled = ["console", "my_provider"]  # 添加到enabled列表

[input.providers.my_provider]
api_url = "https://my-api.example.com"
```

---

## ✅ 成功标准

### 技术指标
- ✅ 所有现有功能正常运行
- ✅ 配置文件行数减少40%以上
- ✅ 核心功能响应时间无增加
- ✅ 代码重复率降低30%以上
- ✅ 服务注册调用减少80%以上
- ✅ EventBus事件调用覆盖率90%以上
- ✅ 插件系统已移除，Provider由Manager统一管理

### 架构指标
- ✅ 清晰的5层核心数据流架构
- ✅ 决策层可替换（支持多种DecisionProvider）
- ✅ 多Provider并发支持（输入层和输出层）
- ✅ 层级间依赖关系清晰（单向依赖）
- ✅ EventBus为内部主要通信模式
- ✅ Provider模式替代重复插件
- ✅ 工厂模式支持动态切换
- ✅ 配置驱动，无需修改代码即可启用/禁用Provider
- ✅ 事件数据契约类型安全（Pydantic Model + 开放式注册表）
- ✅ 结构化消息保留原始数据（不丢失信息）
- ✅ LLM意图解析（比规则更智能）
- ✅ 插件系统已完全移除

---

## 🚀 快速导航

### 我想知道...

**整体架构是什么？**
→ 阅读[5层架构设计](./layer_refactoring.md)

**决策层如何工作？**
→ 阅读[决策层设计](./decision_layer.md)（含LLM意图解析）

**多个Provider如何并发？**
→ 阅读[多Provider并发设计](./multi_provider.md)

**如何配置Provider？**
→ 阅读[多Provider并发设计 - 配置示例](./multi_provider.md#配置示例)

**Pipeline如何工作？**
→ 阅读[Pipeline重新设计](./pipeline_refactoring.md)

**LLM意图解析如何实现？**
→ 阅读[决策层设计 - LLM意图解析](./decision_layer.md#llm意图解析)

**如何定义事件数据格式？**
→ 阅读[事件数据契约设计](./event_data_contract.md)

**AmaidesuCore如何重构？**
→ 阅读[核心重构设计](./core_refactoring.md)

**LLM调用如何统一管理？**
→ 阅读[LLM服务设计](./llm_service.md)

**HTTP服务器如何管理？**
→ 阅读[HTTP服务器设计](./http_server.md)

**为什么移除插件系统？**
→ 查看本文档的[为什么移除插件系统？](#为什么移除插件系统)章节

**如何添加新Provider？**
→ 查看本文档的[社区扩展](#社区扩展)章节

---

## 🔄 架构演进历史

### 2024年初始设计（已废弃）

- 插件系统 + Provider系统双轨并行
- Plugin创建和管理Provider
- 24个插件，18个服务注册

### 2025年重构（当前架构）

- 移除插件系统
- Provider由Manager统一管理
- 配置驱动启用/禁用
- 5层架构，职责清晰

---

## 🔗 相关文档

### 设计文档

 - [5层架构设计](./layer_refactoring.md) - 详细描述5层核心数据流
 - [决策层设计](./decision_layer.md) - 可替换的决策Provider系统（含LLM意图解析）
 - [多Provider并发设计](./multi_provider.md) - 输入/输出层并发处理
 - [核心重构设计](./core_refactoring.md) - AmaidesuCore的彻底解耦
 - [事件数据契约设计](./event_data_contract.md) - 类型安全的事件数据契约系统
 - [LLM服务设计](./llm_service.md) - 统一的LLM调用服务
 - [Pipeline重新设计](./pipeline_refactoring.md) - 3类Pipeline系统
 - [HTTP服务器设计](./http_server.md) - HTTP服务器管理

### 已移除的文档

 - [插件系统设计](./plugin_system.md) - 已完全移除，插件系统不再存在
 - [架构设计审查](./architecture_review.md) - 历史文档，仅供参考
