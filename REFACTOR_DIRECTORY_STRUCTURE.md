# 目录结构重构总结

## 📅 日期
2026-02-01

## 🎯 重构目标

重新组织项目目录结构，使其更加清晰、符合7层架构设计，并解决命名混乱的问题。

## 📁 新目录结构

```
src/
├── core/                        # 核心基础设施
│   ├── amaidesu_core.py
│   ├── event_bus.py
│   ├── plugin_manager.py
│   └── base/                    # ✅ Provider 抽象基类
│       ├── __init__.py
│       ├── input_provider.py    # InputProvider ABC
│       ├── decision_provider.py # DecisionProvider ABC
│       ├── output_provider.py   # OutputProvider ABC
│       └── base.py              # 公共基类和数据类型导入
│
├── layers/                      # ✅ 7层架构（纯语义命名）
│   ├── __init__.py
│   │
│   ├── input/                   # Layer 1: 输入感知层
│   │   ├── __init__.py
│   │   ├── input_layer.py
│   │   ├── input_provider_manager.py
│   │   └── text/                # 内置 InputProvider
│   │       ├── console_input_provider.py
│   │       └── mock_danmaku_provider.py
│   │
│   ├── normalization/           # Layer 2: 输入标准化层
│   │   └── (待实现)
│   │
│   ├── canonical/               # Layer 3: 中间表示层
│   │   ├── __init__.py
│   │   ├── canonical_layer.py
│   │   └── canonical_message.py
│   │
│   ├── decision/                # Layer 4: 决策层
│   │   ├── __init__.py
│   │   ├── decision_layer.py
│   │   ├── decision_manager.py
│   │   └── providers/           # 内置 DecisionProvider
│   │       ├── maicore_decision_provider.py
│   │       ├── local_llm_decision_provider.py
│   │       └── rule_engine_decision_provider.py
│   │
│   ├── understanding/           # Layer 5: 表现理解层
│   │   ├── __init__.py
│   │   ├── understanding_layer.py
│   │   ├── emotion_analyzer.py
│   │   ├── intent.py
│   │   └── response_parser.py
│   │
│   ├── expression/              # Layer 6: 表现生成层
│   │   ├── __init__.py
│   │   ├── expression_layer.py
│   │   ├── expression_generator.py
│   │   ├── action_mapper.py
│   │   ├── emotion_mapper.py
│   │   ├── expression_mapper.py
│   │   └── render_parameters.py
│   │
│   └── rendering/               # Layer 7: 渲染呈现层
│       ├── __init__.py
│       ├── rendering_layer.py
│       ├── output_provider_manager.py
│       ├── provider_registry.py
│       └── providers/           # 内置 OutputProvider
│           ├── tts_provider.py
│           ├── subtitle_provider.py
│           ├── vts_provider.py
│           ├── sticker_provider.py
│           ├── avatar_output_provider.py
│           └── omni_tts_provider.py
│
├── plugins/                     # 官方 Plugin（场景整合，不创建Provider）
├── pipelines/                   # 管道系统
├── services/                    # 业务服务
├── data_types/                  # ✅ 数据类型定义
│   └── data_types/
│       ├── raw_data.py
│       └── normalized_text.py
├── config/                      # 配置管理
├── utils/                       # 工具函数
├── tools/                       # 工具脚本
├── platform/                    # 平台相关
└── usage/                       # 用法相关
```

## 🔄 迁移映射表

| 旧路径 | 新路径 |
|--------|--------|
| `src/core/providers/` (接口) | `src/core/base/` |
| `src/perception/` | `src/layers/input/` |
| `src/canonical/` | `src/layers/canonical/` |
| `src/core/decision_manager.py` | `src/layers/decision/decision_manager.py` |
| `src/core/providers/*_decision_provider.py` | `src/layers/decision/providers/` |
| `src/understanding/` | `src/layers/understanding/` |
| `src/expression/` | `src/layers/expression/` |
| `src/rendering/` | `src/layers/rendering/` |
| `src/core/data_types/` | `src/data_types/data_types/` |

## 📝 导入路径更新

所有受影响的导入路径已更新：

```python
# 旧导入
from src.core.providers import InputProvider
from src.perception.input_layer import InputLayer
from src.canonical.canonical_layer import CanonicalLayer
from src.core.decision_manager import DecisionManager
from src.understanding.understanding_layer import UnderstandingLayer
from src.expression.expression_generator import ExpressionGenerator
from src.rendering.provider_registry import ProviderRegistry
from src.core.data_types.raw_data import RawData

# 新导入
from src.core.base import InputProvider
from src.layers.input.input_layer import InputLayer
from src.layers.canonical.canonical_layer import CanonicalLayer
from src.layers.decision.decision_manager import DecisionManager
from src.layers.understanding.understanding_layer import UnderstandingLayer
from src.layers.expression.expression_generator import ExpressionGenerator
from src.layers.rendering.provider_registry import ProviderRegistry
from src.data_types.data_types.raw_data import RawData
```

## ✅ 重构优势

1. **清晰对应7层架构**：`layers/` 目录直接对应7层架构
2. **语义化命名**：使用语义化的层名（input, decision, rendering等），而非数字
3. **职责明确**：
   - `core/base/` 存放抽象基类（ABC）
   - `layers/*/providers/` 存放具体实现
   - `data_types/` 集中管理数据类型
4. **易于扩展**：插入新层无需重命名现有层
5. **符合设计文档**：按照 `refactor/design/plugin_system.md` 的建议组织结构

## 🔧 技术细节

### 使用 git mv 保留历史
所有文件移动使用 `git mv` 命令，保留了完整的 git 历史。

### 代码检查通过
- ✅ `uv run ruff check` - 所有检查通过
- ✅ `python -m py_compile main.py` - 语法检查通过

### 更新的文件数量
- **重命名文件**: 45个
- **修改文件**: 80+个（更新导入路径）
- **新建文件**: 3个（`__init__.py` 文件）

## 📋 后续工作

### 可选优化
1. 考虑是否将 `src/data_types/data_types/` 简化为 `src/data_types/`
2. 实现 Layer 2 (normalization) 的具体功能
3. 更新文档和注释以反映新目录结构

### 验证清单
- [x] 代码语法检查通过
- [x] Ruff 代码检查通过
- [ ] 运行 `main.py` 验证功能正常
- [ ] 运行测试套件（如果有）
- [ ] 更新 CLAUDE.md 和其他文档

## 🚀 提交信息

```
refactor: 重构目录结构以符合7层架构设计

- 创建 src/layers/ 目录组织7层架构
- 重命名层目录为语义化名称（input, decision, rendering等）
- 将抽象基类移至 src/core/base/
- 将数据类型集中到 src/data_types/
- 使用 git mv 保留完整历史
- 更新所有导入路径

新目录结构更加清晰，易于扩展和维护。

详见: REFACTOR_DIRECTORY_STRUCTURE.md
```
