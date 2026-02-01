# Provider迁移执行计划

**日期**：2025年2月1日
**状态**：执行中

---

## 🎯 迁移目标

将 `plugins_backup/` 中的Provider迁移到新的5层架构：

```
src/layers/
├── input/              # Layer 1: 输入层
│   └── providers/
├── decision/           # Layer 3: 决策层
│   └── providers/
└── output/             # Layer 5: 输出层
    └── providers/
```

---

## 📋 迁移优先级

### P1 - 必需（测试和基础功能）

| 插件 | 类型 | 源文件 | 目标位置 | 复杂度 |
|------|------|--------|---------|--------|
| **mock_danmaku** | Input | `plugins_backup/mock_danmaku/mock_danmaku_input_provider.py` | `src/layers/input/providers/` | 简单 |
| **console_input** | Input | `plugins_backup/console_input/plugin.py` | `src/layers/input/providers/` | 简单 |
| **subtitle** | Output | `plugins_backup/subtitle/subtitle_output_provider.py` | `src/layers/output/providers/` | 简单 |

### P2 - 重要（核心功能）

| 插件 | 类型 | 源文件 | 目标位置 | 复杂度 |
|------|------|--------|---------|--------|
| **bili_danmaku** | Input | `plugins_backup/bili_danmaku/providers/bili_danmaku_provider.py` | `src/layers/input/providers/` | 中等 |
| **tts** | Output | `plugins_backup/tts/` (多个实现) | `src/layers/output/providers/` | 中等 |
| **vtube_studio** | Output | `plugins_backup/vtube_studio/` | `src/layers/output/providers/` | 中等 |

### P3 - 可选（扩展功能）

| 插件 | 类型 | 源文件 | 目标位置 | 复杂度 |
|------|------|--------|---------|--------|
| **minecraft** | Input | `plugins_backup/minecraft/` | `src/layers/input/providers/` | 复杂 |
| **warudo** | Output | `plugins_backup/warudo/providers/warudo_output_provider.py` | `src/layers/output/providers/` | 复杂 |
| **obs_control** | Output | `plugins_backup/obs_control/providers/obs_control_output_provider.py` | `src/layers/output/providers/` | 复杂 |

---

## 🚀 执行步骤

### Phase 1: P1优先级（必需）

#### 1.1 创建providers目录（如果不存在）
```bash
mkdir -p src/layers/input/providers
mkdir -p src/layers/output/providers
```

#### 1.2 迁移mock_danmaku
- 复制：`plugins_backup/mock_danmaku/mock_danmaku_input_provider.py`
- 到：`src/layers/input/providers/mock_danmaku_provider.py`
- 更新导入路径
- 测试

#### 1.3 迁移console_input
- 分析：`plugins_backup/console_input/plugin.py`
- 提取Provider逻辑
- 创建：`src/layers/input/providers/console_input_provider.py`
- 更新导入路径
- 测试

#### 1.4 迁移subtitle
- 复制：`plugins_backup/subtitle/subtitle_output_provider.py`
- 到：`src/layers/output/providers/subtitle_provider.py`
- 更新导入路径
- 测试

### Phase 2: P2优先级（重要）

#### 2.1 迁移bili_danmaku
- 复制：`plugins_backup/bili_danmaku/providers/bili_danmaku_provider.py`
- 到：`src/layers/input/providers/bili_danmaku_provider.py`
- 更新导入路径
- 依赖检查（blivedm等）
- 测试

#### 2.2 迁移tts
- 分析：`plugins_backup/tts/` 目录结构
- 识别不同的TTS实现（edge_tts, gptsovits_tts, omni_tts等）
- 创建对应的Provider
- 测试

#### 2.3 迁移vtube_studio
- 分析：`plugins_backup/vtube_studio/` 目录结构
- 提取Provider逻辑
- 创建：`src/layers/output/providers/vts_provider.py`
- 更新导入路径
- 测试

### Phase 3: P3优先级（可选）

根据时间和需求决定是否迁移。

---

## 📝 迁移规范

### 文件命名规则

- 输入Provider：`{name}_input_provider.py`
- 输出Provider：`{name}_output_provider.py` 或 `{name}_provider.py`
- 决策Provider：`{name}_decision_provider.py`

### 导入路径更新

**旧导入**：
```python
from src.core.providers.input_provider import InputProvider
from src.core.providers.output_provider import OutputProvider
```

**新导入**：
```python
from src.layers.input.providers.base_input_provider import BaseInputProvider
from src.layers.output.providers.base_output_provider import BaseOutputProvider
```

### Provider基类

需要创建基类：
- `src/layers/input/providers/base_input_provider.py`
- `src/layers/output/providers/base_output_provider.py`

---

## ✅ 验证清单

每个Provider迁移后：
- [ ] 文件已复制到目标位置
- [ ] 导入路径已更新
- [ ] 继承自正确的基类
- [ ] 配置路径已更新
- [ ] 可以正常导入
- [ ] 基本功能测试通过

---

## 🔧 工具和命令

### 查找Provider文件
```bash
find plugins_backup/ -name "*provider*.py"
```

### 检查导入依赖
```bash
grep -r "from.*import" plugins_backup/{插件名}/
```

### 测试导入
```bash
python -c "from src.layers.input.providers.xxx_provider import XxxProvider"
```

---

## 📊 进度跟踪

- [ ] Phase 1.1: 创建providers目录
- [ ] Phase 1.2: 迁移mock_danmaku
- [ ] Phase 1.3: 迁移console_input
- [ ] Phase 1.4: 迁移subtitle
- [ ] Phase 2.1: 迁移bili_danmaku
- [ ] Phase 2.2: 迁移tts
- [ ] Phase 2.3: 迁移vtube_studio

---

**最后更新**：2025年2月1日
