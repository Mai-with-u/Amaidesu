# 提示词管理系统设计

**创建时间**: 2026-02-09
**版本**: 1.0
**状态**: 设计阶段

---

## 核心理念

将分散、硬编码的提示词统一为「**Markdown 模板文件 + PromptManager 集中管理**」的方式。

### 设计目标

| 目标 | 说明 |
|------|------|
| **集中管理** | 所有提示词集中在 `src/modules/prompts/templates/` 目录 |
| **易于维护** | Markdown 格式，支持 YAML frontmatter 元数据 |
| **零冲突语法** | `$variable` 语法与 JSON 零冲突，LLM 提示词无需转义 |
| **配置覆盖** | 支持配置文件覆盖模板，保持向后兼容 |
| **全局访问** | 全局单例模式，无需依赖注入 |

---

## 当前问题

### 提示词分散硬编码

| 文件 | 提示词用途 | 行数 | 是否可配置 |
|------|-----------|------|-----------|
| `src/domains/decision/intent_parser.py` | Intent 解析系统提示词 | ~36 行 | 否（类常量） |
| `src/domains/decision/providers/local_llm/local_llm_decision_provider.py` | 本地 LLM 决策模板 | ~3 行 | 是（config） |
| `src/domains/output/providers/vts/vts_provider.py` | VTS 热键匹配提示词 | ~5 行 | 是（config） |
| `src/domains/input/providers/mainosaba/mainosaba_provider.py` | 游戏截图 OCR 提示词 | ~12 行 | 否（硬编码） |

### 核心问题

1. **难以维护**：修改提示词需要在代码中找到具体位置
2. **版本控制不友好**：提示词变更淹没在代码提交中
3. **无法复用**：相同提示词在不同模块中重复定义
4. **JSON 转义痛苦**：LLM 提示词含大量 JSON，使用 `{}` 语法需要大量转义

---

## 方案选择：全局单例（方案 B）

### 为什么选择全局单例

| 对比项 | 方案 A（依赖注入） | 方案 B（全局单例） |
|--------|-------------------|-------------------|
| InputProvider 改动 | 需新增 setup() 阶段 | **无需改动** |
| ProviderRegistry 改动 | 需支持 dependencies 参数 | **无需改动** |
| Manager 层改动 | 需传递 prompt_manager | **无需改动** |
| 调用方式 | `self.prompt_manager.render(...)` | `get_prompt_manager().render(...)` |
| 测试友好度 | 更好（可 mock 注入） | 需 monkeypatch |
| 未来可迁移 | — | 可随时迁移到方案 A |

**核心优势**：零架构改动，仅需创建新模块 + 修改调用点。

---

## 目录结构

```
src/prompts/
├── __init__.py                     # 导出 get_prompt_manager()
├── manager.py                      # PromptManager 实现
└── templates/                      # 提示词模板
    ├── decision/
    │   ├── intent_parser.md         # Intent 解析系统提示词
    │   └── local_llm.md            # 本地 LLM 决策模板
    ├── output/
    │   └── vts_hotkey.md            # VTS 热键匹配提示词
    └── input/
        └── mainosaba_ocr.md         # 游戏截图文本识别提示词
```

---

## PromptManager 实现

### 核心类设计

```python
# src/prompts/manager.py
import os
from pathlib import Path
from string import Template
from typing import Dict, Any, Optional

from src.core.utils.logger import get_logger


class PromptManager:
    """
    提示词管理器

    从 Markdown 模板文件加载提示词，支持 $variable 变量替换（string.Template）。
    使用 $ 语法而非 {} 语法，避免与 LLM 提示词中的 JSON 示例冲突。
    模板文件使用 YAML frontmatter 存储元数据。
    """

    def __init__(self, templates_dir: Optional[str] = None):
        self.logger = get_logger("PromptManager")
        self._templates_dir = Path(templates_dir or os.path.join(
            os.path.dirname(__file__), "templates"
        ))
        self._cache: Dict[str, str] = {}
        self._metadata: Dict[str, Dict[str, Any]] = {}
        self._loaded = False

    def load_all(self) -> None:
        """加载所有模板文件"""
        if not self._templates_dir.exists():
            self.logger.warning(f"模板目录不存在: {self._templates_dir}")
            return

        for md_file in self._templates_dir.rglob("*.md"):
            rel_path = md_file.relative_to(self._templates_dir)
            # 模板名称：去掉 .md 后缀，路径分隔符改为 /
            template_name = str(rel_path.with_suffix("")).replace(os.sep, "/")
            self._load_template(template_name, md_file)

        self._loaded = True
        self.logger.info(f"已加载 {len(self._cache)} 个提示词模板")

    def _load_template(self, name: str, path: Path) -> None:
        """加载单个模板文件，解析 YAML frontmatter"""
        content = path.read_text(encoding="utf-8")
        metadata, body = self._parse_frontmatter(content)
        self._cache[name] = body.strip()
        self._metadata[name] = metadata

    def _parse_frontmatter(self, content: str) -> tuple[Dict[str, Any], str]:
        """解析 YAML frontmatter"""
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                try:
                    import yaml
                    metadata = yaml.safe_load(parts[1]) or {}
                    return metadata, parts[2]
                except Exception:
                    pass
        return {}, content

    def render(self, template_name: str, **kwargs) -> str:
        """
        渲染模板（严格模式：缺少变量时抛出异常）

        使用 string.Template.substitute()，$variable 占位符。

        Args:
            template_name: 模板名称（如 "decision/intent_parser"）
            **kwargs: 变量替换参数

        Returns:
            渲染后的提示词文本

        Raises:
            KeyError: 模板不存在或缺少必需变量
        """
        if not self._loaded:
            self.load_all()

        if template_name not in self._cache:
            raise KeyError(f"提示词模板不存在: {template_name}")

        raw = self._cache[template_name]
        if kwargs:
            return Template(raw).substitute(**kwargs)
        return raw

    def render_safe(self, template_name: str, **kwargs) -> str:
        """
        渲染模板（安全模式：缺少变量时保留原始占位符）

        使用 string.Template.safe_substitute()。

        Args:
            template_name: 模板名称
            **kwargs: 变量替换参数

        Returns:
            渲染后的提示词文本（未替换的变量保留为 $variable）
        """
        if not self._loaded:
            self.load_all()

        if template_name not in self._cache:
            raise KeyError(f"提示词模板不存在: {template_name}")

        raw = self._cache[template_name]
        if kwargs:
            return Template(raw).safe_substitute(**kwargs)
        return raw

    def get_raw(self, template_name: str) -> str:
        """获取原始模板（不做变量替换）"""
        if not self._loaded:
            self.load_all()
        if template_name not in self._cache:
            raise KeyError(f"提示词模板不存在: {template_name}")
        return self._cache[template_name]

    def get_metadata(self, template_name: str) -> Dict[str, Any]:
        """获取模板元数据"""
        if not self._loaded:
            self.load_all()
        return self._metadata.get(template_name, {})

    def list_templates(self) -> list[str]:
        """列出所有已加载的模板名称"""
        if not self._loaded:
            self.load_all()
        return list(self._cache.keys())
```

### 全局单例

```python
# src/prompts/__init__.py
from .manager import PromptManager

_instance: PromptManager | None = None


def get_prompt_manager() -> PromptManager:
    """获取 PromptManager 全局单例（惰性初始化）"""
    global _instance
    if _instance is None:
        _instance = PromptManager()
        _instance.load_all()
    return _instance


def reset_prompt_manager() -> None:
    """重置单例（用于测试）"""
    global _instance
    _instance = None


__all__ = ["PromptManager", "get_prompt_manager", "reset_prompt_manager"]
```

---

## 模板文件格式

### 基本结构

模板文件使用 **YAML frontmatter + Markdown** 格式：

```markdown
---
name: template_name
version: "1.0"
description: "模板描述"
---

# 提示词内容

这里放置实际的 LLM 提示词文本。

可以使用 Markdown 格式。
```

### 占位符语法

使用 `string.Template` 语法（`$variable`）：

```markdown
用户文本: "$text"

可用列表:
$items_str

---
```

**为什么不用 `{}` 语法**：
- LLM 提示词含大量 JSON 示例
- `{}` 与 JSON 冲突，需要大量转义
- `$` 在提示词中几乎不出现，零冲突

### 转义规则

| 需要输出 | 转义方式 |
|---------|---------|
| `$` 字面量 | `$$` |
| `${variable}` 大括号形式 | 无需特殊处理 |

### 示例：无变量模板

```markdown
---
name: intent_parser
version: "1.0"
description: "将 AI 回复消息解析为结构化意图"
---

你是一个AI VTuber的意图分析助手。你的任务是将AI的回复消息解析为结构化的意图(Intent)。

分析消息内容并提取：
1. **情感(EmotionType)**: neutral/happy/sad/angry/surprised/love
2. **回复文本**: 提取主要回复内容
3. **动作(IntentAction)**: 识别应该执行的表现动作

动作类型说明：
- expression: 表情（params: {"name": "表情名称"}）
- hotkey: 热键（params: {"key": "按键名称"}）
- emoji: emoji表情（params: {"emoji": "实际emoji"}）
- blink: 眨眼
- nod: 点头
- shake: 摇头
- wave: 挥手
- clap: 鼓掌
- none: 无动作

输出格式（严格JSON）：
{
  "emotion": "happy",
  "response_text": "回复内容",
  "actions": [
    {"type": "expression", "params": {"name": "smile"}, "priority": 50}
  ]
}

注意：
- emotion: 必须是预定义的6种之一
- response_text: 提取消息的主要文本内容
- actions: 数组，每个action包含type、params、priority(0-100)
- 如果无法确定情感，默认使用"neutral"
- 如果没有明显动作，返回空数组
- 严格按照JSON格式输出，不要添加其他内容
```

### 示例：含变量模板

```markdown
---
name: vts_hotkey
version: "1.0"
description: "VTS 热键匹配提示词"
---

你是一个VTube Studio热键匹配助手。根据用户的文本内容，从提供的热键列表中选择最合适的热键。

用户文本: "$text"

可用的热键列表:
$hotkey_list_str

规则:
1. 仔细分析用户文本的情感和动作意图
2. 从热键列表中选择最匹配的一个热键名称
3. 如果没有合适的匹配，返回 "NONE"
4. 只返回热键名称或"NONE"，不要其他解释

你的选择:
```

---

## 调用点改造

### 改造原则

| 场景 | 使用方法 | 说明 |
|------|---------|------|
| 无变量提示词 | `get_raw()` | 获取原始模板 |
| 有变量提示词 | `render()` | 渲染变量（严格模式） |
| 允许缺失变量 | `render_safe()` | 安全渲染（保留未替换的占位符） |

### IntentParser 改造

```python
# === 改造前 ===
class IntentParser:
    SYSTEM_PROMPT = """你是一个AI VTuber的意图分析助手..."""  # 36行硬编码

    async def _parse_with_llm(self, text, message):
        response = await self.llm_service.chat(
            system_message=self.SYSTEM_PROMPT, ...
        )

# === 改造后 ===
from src.prompts import get_prompt_manager

class IntentParser:
    async def _parse_with_llm(self, text, message):
        system_prompt = get_prompt_manager().get_raw("decision/intent_parser")
        response = await self.llm_service.chat(
            system_message=system_prompt, ...
        )
```

### VTSProvider 改造（含变量）

```python
# === 改造前 ===
prompt = self.llm_prompt_prefix.format(text=text)

# === 改造后 ===
from src.prompts import get_prompt_manager

prompt = get_prompt_manager().render(
    "output/vts_hotkey",
    text=text,
    hotkey_list_str=hotkey_str,
)
```

### LocalLLMDecisionProvider 改造（配置覆盖优先）

```python
# === 改造后 ===
from src.prompts import get_prompt_manager

class LocalLLMDecisionProvider(DecisionProvider):
    async def _get_prompt_template(self) -> str:
        # 优先级 1：配置覆盖
        if self.config.get("prompt_template"):
            return self.config["prompt_template"]

        # 优先级 2：模板文件
        try:
            return get_prompt_manager().get_raw("decision/local_llm")
        except KeyError:
            # 优先级 3：硬编码兜底
            return "你是一个AI VTuber助手..."
```

---

## 配置优先级

### 三级优先级

```
优先级 1（最高）: Provider 配置字段覆盖
优先级 2: 模板文件（src/prompts/templates/*.md）
优先级 3（最低）: 硬编码兜底
```

### 实现逻辑

```python
# 通用实现模式
def get_prompt_with_fallback(self, template_name: str, config_key: str) -> str:
    # 优先级 1：配置覆盖
    config_prompt = self.config.get(config_key)
    if config_prompt:
        return config_prompt

    # 优先级 2：模板文件
    try:
        return get_prompt_manager().get_raw(template_name)
    except KeyError:
        # 优先级 3：硬编码兜底
        return self._get_default_prompt()
```

### 向后兼容性

- 现有配置文件中的 `prompt_template`、`llm_prompt_prefix` 等字段优先级最高
- 不会破坏现有部署
- 新部署可依赖模板文件，简化配置

---

## 测试策略

### PromptManager 单元测试

```python
import pytest
from src.prompts import get_prompt_manager, reset_prompt_manager

def test_load_templates():
    reset_prompt_manager()
    pm = get_prompt_manager()
    templates = pm.list_templates()
    assert "decision/intent_parser" in templates
    assert "output/vts_hotkey" in templates

def test_render_no_variables():
    pm = get_prompt_manager()
    prompt = pm.get_raw("decision/intent_parser")
    assert "AI VTuber" in prompt

def test_render_with_variables():
    pm = get_prompt_manager()
    prompt = pm.render("output/vts_hotkey", text="你好", hotkey_list_str="smile, wave")
    assert "你好" in prompt
    assert "smile" in prompt

def test_missing_template():
    pm = get_prompt_manager()
    with pytest.raises(KeyError):
        pm.get_raw("nonexistent")

def test_render_missing_variable():
    pm = get_prompt_manager()
    with pytest.raises(KeyError):
        pm.render("output/vts_hotkey", text="你好")  # 缺少 hotkey_list_str

def test_render_safe_missing_variable():
    pm = get_prompt_manager()
    # render_safe 保留未替换的变量
    prompt = pm.render_safe("output/vts_hotkey", text="你好")
    assert "$hotkey_list_str" in prompt
```

### 集成测试

```python
import pytest
from src.prompts import get_prompt_manager

@pytest.mark.asyncio
async def test_intent_parser_integration():
    """验证 IntentParser 的提示词内容不变"""
    from src.domains.decision.intent_parser import IntentParser

    old_prompt = IntentParser.SYSTEM_PROMPT  # 改造前的硬编码
    new_prompt = get_prompt_manager().get_raw("decision/intent_parser")

    # 验证关键内容一致
    assert "AI VTuber" in new_prompt
    assert "intent" in new_prompt.lower()
    assert "emotion" in new_prompt.lower()
```

---

## 常见问题

### Q: 为什么不使用 Jinja2 等高级模板引擎？

**A**: `string.Template` 足够简单且满足需求：
- LLM 提示词只需要简单的变量替换
- 零依赖（标准库）
- `$` 语法与 JSON 零冲突
- 调试简单（模板即最终输出）

### Q: 支持热重载吗？

**A**: 首版不支持。未来可在 `PromptManager.load_all()` 中添加文件监听。

### Q: 如何迁移已有的配置提示词？

**A**:
1. 提取配置中的提示词内容
2. 创建对应的 `.md` 模板文件
3. 配置保留作为覆盖（优先级 1）
4. 测试验证一致性
5. （可选）删除配置中的提示词，依赖模板

### Q: 测试时如何 mock PromptManager？

**A**: 使用 `monkeypatch`：

```python
def test_something(monkeypatch):
    def mock_get_manager():
        return MockPromptManager()

    monkeypatch.setattr("src.prompts.get_prompt_manager", mock_get_manager)
```

或使用 `reset_prompt_manager()` 重新加载测试模板。

---

## 附带发现的问题

以下问题在验证过程中发现，与提示词管理重构不直接相关，但值得记录。

### MainosabaProvider 的 vlm_client 依赖注入已坏

**现象**：
- `MainosabaProvider.__init__(config, vlm_client=None, event_bus=None)`
- `ProviderRegistry.create_input("mainosaba", config)` 仅传入 config
- 导致 `vlm_client` 始终为 None，`recognize_game_text()` 永远返回 None

**影响**：MainosabaProvider 通过标准配置路径创建后，**完全无法工作**。

**建议修复方向**：
- 方案 a：在 InputProvider 基类增加 `setup(dependencies)` 阶段（与 OutputProvider 对齐）
- 方案 b：改用 LLMManager 全局服务获取 VLM 能力，去掉 `vlm_client` 构造参数
- 方案 c：在 InputProviderManager.load_from_config() 中特殊处理额外依赖

### VTSProvider 绕过 LLMManager 自建 OpenAI 客户端

**现象**：
- VTSProvider 在 `__init__` 中直接创建 `openai.AsyncOpenAI` 客户端
- 使用独立的 `llm_api_key`、`llm_base_url`、`llm_model` 配置
- 完全绕过了项目的 `LLMManager` 统一管理

**影响**：
- Token 使用量无法统计
- LLM 配置分散，不便维护
- 与项目架构不一致

**建议**：后续单独处理，不在本方案范围内。迁移到 LLMManager 需要：
1. 在 OutputProvider.setup() 的 dependencies 中传入 llm_manager
2. 修改 VTSProvider._find_best_matching_hotkey_with_llm() 改用 LLMManager.chat()
3. 调整配置结构（复用 llm_fast 而非独立配置）

---

## 验收标准

- [ ] `src/prompts/` 模块创建并可工作
- [ ] 所有 4 处硬编码提示词改为从 PromptManager 获取
- [ ] 现有 config 配置覆盖机制保持兼容
- [ ] PromptManager 单元测试覆盖核心功能
- [ ] 各调用点集成测试验证提示词内容无变化
- [ ] `uv run ruff check .` 和 `uv run pytest tests/` 通过

---

## 参考资料

- [OpenCode 提示词架构](https://github.com/opencode-ai/opencode/tree/main/internal/llm/prompt) — 三层组装模式
- [Claude Code Memory](https://code.claude.com/docs/en/memory) — 层级化管理
- [Python string.Template 文档](https://docs.python.org/3/library/string.html#template-strings)
