# 提示词管理指南

项目使用 **PromptManager** 统一管理所有 LLM 提示词，采用「Markdown 模板文件 + 全局单例」的方式，避免提示词分散硬编码。

## 概述

### 核心原则

- **集中管理**：所有提示词在 `src/prompts/templates/` 目录
- **易于维护**：Markdown 格式，支持 YAML frontmatter 元数据
- **零冲突语法**：`$variable` 语法与 JSON 零冲突
- **配置覆盖**：支持配置文件覆盖模板，保持向后兼容

### 为什么需要 PromptManager

**问题**：
- 提示词分散在各个 Provider 代码中
- 修改提示词需要改代码，难以维护
- 硬编码字符串难以版本控制

**解决方案**：
- 所有提示词存储为独立的 Markdown 文件
- 代码通过 `get_prompt_manager()` 获取提示词
- 支持配置覆盖，保持向后兼容

## PromptManager 使用

### 基本用法

```python
from src.prompts import get_prompt_manager

# 获取无变量提示词（原始模板）
system_prompt = get_prompt_manager().get_raw("decision/intent_parser")

# 渲染有变量提示词（严格模式：缺少变量抛出异常）
prompt = get_prompt_manager().render(
    "output/vts_hotkey",
    text="用户消息",
    hotkey_list_str="smile, wave",
)

# 渲染有变量提示词（安全模式：缺少变量保留占位符）
prompt = get_prompt_manager().render_safe(
    "output/vts_hotkey",
    text="用户消息",
    # hotkey_list_str 未提供，保留 $hotkey_list_str
)
```

### API 方法

| 方法 | 说明 | 使用场景 |
|------|------|----------|
| `get_raw(name)` | 获取原始模板（不渲染变量） | 无变量提示词 |
| `render(name, **kwargs)` | 渲染模板（严格模式） | 有变量提示词，缺少变量抛异常 |
| `render_safe(name, **kwargs)` | 渲染模板（安全模式） | 有变量提示词，缺少变量保留占位符 |

### 模板命名规则

模板名称与文件路径对应：

```
src/prompts/templates/decision/intent_parser.md  →  "decision/intent_parser"
src/prompts/templates/output/vts_hotkey.md       →  "output/vts_hotkey"
src/prompts/templates/input/mainosaba_ocr.md     →  "input/mainosaba_ocr"
```

## 创建新提示词模板

### 步骤 1：创建模板文件

在 `src/prompts/templates/` 下创建 `.md` 文件：

```markdown
---
name: my_prompt
version: "1.0"
description: "提示词描述"
tags: ["tag1", "tag2"]
---

你是一个AI助手。你的任务是处理用户输入。

用户输入: "$user_input"

规则:
1. 分析用户意图
2. 生成适当的回复
```

### 步骤 2：使用模板

```python
from src.prompts import get_prompt_manager

class MyProvider:
    async def process(self, user_input: str) -> str:
        # 渲染提示词
        prompt = get_prompt_manager().render(
            "my_prompt",  # 对应 templates/my_prompt.md
            user_input=user_input,
        )

        # 调用 LLM
        response = await self.llm_manager.chat(prompt)
        return response
```

## 模板变量语法

### 占位符语法

使用 `string.Template` 语法（`$variable`）：

| 占位符 | 说明 | 示例 |
|--------|------|------|
| `$variable` | 简单变量 | `$text` → 替换为 `text` 参数值 |
| `${variable}` | 大括号形式（避免歧义） | `${user_name}` |
| `$$` | 字面量 `$` | `Price: $$100` → `Price: $100` |

### 为什么不用 `{}` 语法

**问题**：
- LLM 提示词含大量 JSON 示例
- `{}` 与 JSON 冲突，需要大量转义
- 例如：`{"key": "value"}` 中的 `{}` 会被误认为模板变量

**解决方案**：
- 使用 `$` 语法（Python `string.Template`）
- `$` 在提示词中几乎不出现，零冲突
- 无需转义 JSON 示例

### 模板文件格式

```markdown
---
# YAML frontmatter（元数据）
name: template_name
version: "1.0"
description: "模板描述"
tags: ["tag1", "tag2"]
---

# 提示词内容（Markdown）

可以使用 Markdown 格式。

变量占位符：$variable

可以包含 JSON 示例：
{
  "key": "value",
  "nested": {
    "array": [1, 2, 3]
  }
}
```

## 配置优先级

### 三级优先级

```
优先级 1（最高）: Provider 配置字段覆盖
优先级 2: 模板文件（src/prompts/templates/*.md）
优先级 3（最低）: 硬编码兜底
```

### 实现配置覆盖

```python
from src.prompts import get_prompt_manager

class MyProvider:
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.config = config

    async def _get_prompt(self) -> str:
        # 优先级 1：配置覆盖
        config_prompt = self.config.get("prompt_template")
        if config_prompt:
            return config_prompt

        # 优先级 2：模板文件
        try:
            return get_prompt_manager().get_raw("my_prompt")
        except KeyError:
            # 优先级 3：硬编码兜底
            return "默认提示词..."
```

### 配置示例

```toml
# Provider 配置中覆盖提示词（优先级 1）
[providers.output.outputs.my_provider]
type = "my_provider"
# 可选：覆盖模板提示词
prompt_template = "自定义提示词..."
```

## 测试提示词

### 单元测试

```python
import pytest
from src.prompts import get_prompt_manager, reset_prompt_manager

def test_prompt_manager():
    # 重置单例，加载测试模板
    reset_prompt_manager()
    pm = get_prompt_manager()

    # 测试无变量模板
    prompt = pm.get_raw("decision/intent_parser")
    assert "AI VTuber" in prompt

    # 测试有变量模板
    prompt = pm.render("output/vts_hotkey", text="测试", hotkey_list_str="smile")
    assert "测试" in prompt
    assert "smile" in prompt

    # 测试缺失模板
    with pytest.raises(KeyError):
        pm.get_raw("nonexistent")

    # 测试缺失变量（严格模式）
    with pytest.raises(KeyError):
        pm.render("output/vts_hotkey", text="测试")  # 缺少 hotkey_list_str
```

### Mock 提示词（测试中）

```python
def test_something(monkeypatch):
    # Mock get_prompt_manager
    class MockPromptManager:
        def get_raw(self, name):
            return "测试提示词"

    monkeypatch.setattr(
        "src.prompts.get_prompt_manager",
        lambda: MockPromptManager()
    )

    # 测试代码使用 mock 的提示词
    ...
```

## 目录结构

```
src/prompts/
├── __init__.py                     # 导出 get_prompt_manager()
├── manager.py                      # PromptManager 实现
└── templates/                      # 提示词模板
    ├── decision/                   # 决策层提示词
    │   ├── intent_parser.md
    │   └── local_llm.md
    ├── output/                     # 输出层提示词
    │   └── vts_hotkey.md
    └── input/                      # 输入层提示词
        └── mainosaba_ocr.md
```

## 最佳实践

### 1. 模板组织

```python
# ✅ 正确：按域组织模板
src/prompts/templates/
├── decision/
│   ├── intent_parser.md
│   └── local_llm.md
├── output/
│   ├── vts_hotkey.md
│   └── emotion_mapping.md
└── input/
    └── ocr.md

# ❌ 错误：所有模板平铺
src/prompts/templates/
├── intent_parser.md
├── local_llm.md
├── vts_hotkey.md
└── ocr.md
```

### 2. 变量命名

```python
# ✅ 正确：使用有意义的变量名
prompt = get_prompt_manager().render(
    "output/vts_hotkey",
    user_input="用户消息",
    hotkey_list="smile,wave",
)

# ❌ 错误：使用模糊的变量名
prompt = get_prompt_manager().render(
    "output/vts_hotkey",
    arg1="用户消息",
    arg2="smile,wave",
)
```

### 3. 错误处理

```python
# ✅ 正确：处理缺失模板
try:
    prompt = get_prompt_manager().get_raw("my_prompt")
except KeyError:
    self.logger.warning(f"提示词模板不存在: my_prompt，使用默认提示词")
    prompt = "默认提示词..."

# ❌ 错误：未处理异常
prompt = get_prompt_manager().get_raw("my_prompt")  # 可能抛出 KeyError
```

### 4. 向后兼容

```python
# ✅ 正确：支持配置覆盖
async def _get_prompt(self) -> str:
    # 优先级 1：配置覆盖
    if "prompt_template" in self.config:
        return self.config["prompt_template"]

    # 优先级 2：模板文件
    try:
        return get_prompt_manager().get_raw("my_prompt")
    except KeyError:
        # 优先级 3：硬编码兜底
        return "默认提示词..."

# ❌ 错误：不支持配置覆盖
async def _get_prompt(self) -> str:
    return get_prompt_manager().get_raw("my_prompt")
```

## 常见问题

### Q: 为什么不使用 Jinja2？

A: `string.Template` 足够简单且满足需求：
- LLM 提示词只需要简单变量替换
- 零依赖（Python 标准库）
- `$` 语法与 JSON 零冲突
- 调试简单（模板即最终输出）

### Q: 如何迁移现有硬编码提示词？

A:
1. 在 `src/prompts/templates/` 创建对应 `.md` 文件
2. 将硬编码提示词复制到文件
3. 使用 `get_prompt_manager().get_raw()` 替换硬编码
4. （可选）保留配置覆盖，确保向后兼容

### Q: 支持模板热重载吗？

A: 首版不支持。未来可在 `PromptManager.load_all()` 中添加文件监听。

### Q: 如何调试提示词渲染？

A: 使用日志记录渲染后的提示词：

```python
prompt = get_prompt_manager().render("my_prompt", var=value)
self.logger.debug(f"渲染后的提示词:\n{prompt}")
```

## 相关文档

- [Provider 开发](provider-guide.md) - Provider 中使用 PromptManager
- [开发规范](development-guide.md) - 代码风格和约定
- [测试规范](testing-guide.md) - 测试提示词

---

*最后更新：2026-02-09*
