"""Prompt 管理器 - 模板加载与渲染

提供统一的 Prompt 模板管理功能，支持：
- 从文件系统加载 .md 模板文件
- 解析 YAML frontmatter 元数据
- 使用 string.Template 进行变量替换
- 严格模式和安全模式渲染

设计文档: refactor/design/prompt_manager.md
"""

from pathlib import Path
from string import Template
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from src.modules.logging import get_logger

# === 数据类定义 ===


class TemplateMetadata(BaseModel):
    """模板元数据（从 YAML frontmatter 解析）"""

    name: str = Field(description="模板名称")
    description: Optional[str] = Field(default=None, description="模板描述")
    version: Optional[str] = Field(default=None, description="模板版本")
    variables: list[str] = Field(default_factory=list, description="模板中使用的变量列表")
    author: Optional[str] = Field(default=None, description="作者")
    tags: list[str] = Field(default_factory=list, description="标签")


class PromptTemplate(BaseModel):
    """加载后的模板"""

    name: str = Field(description="模板名称")
    content: str = Field(description="模板内容（不含 frontmatter）")
    raw: str = Field(description="原始模板内容（含 frontmatter）")
    metadata: TemplateMetadata = Field(description="模板元数据")
    path: Path = Field(description="模板文件路径")

    def render(self, **kwargs: Any) -> str:
        """渲染模板（严格模式）

        Args:
            **kwargs: 模板变量

        Returns:
            渲染后的字符串

        Raises:
            KeyError: 如果缺少必需的变量
        """
        template = Template(self.content)
        return template.substitute(**kwargs)

    def render_safe(self, **kwargs: Any) -> str:
        """渲染模板（安全模式）

        缺失的变量会被保留为原样，不会抛出异常。

        Args:
            **kwargs: 模板变量

        Returns:
            渲染后的字符串
        """
        template = Template(self.content)
        return template.safe_substitute(**kwargs)

    def extract_section(self, section_name: str, **kwargs: Any) -> str:
        """提取并渲染模板中的特定section

        Args:
            section_name: section名称（如 "User Message"）
            **kwargs: 模板变量

        Returns:
            提取并渲染后的section内容，如果section不存在则返回空字符串

        Example:
            提取 "## User Message" section
        """
        import re

        # 先渲染整个模板
        rendered = self.render(**kwargs)

        # 提取指定section
        pattern = rf"## {re.escape(section_name)}\s*\n(.*?)(?=\n## |\Z)"
        match = re.search(pattern, rendered, re.DOTALL)

        if match:
            return match.group(1).strip()
        return ""

    def extract_content_without_section(self, section_name: str, **kwargs: Any) -> str:
        """提取模板内容，排除指定的section

        Args:
            section_name: 要排除的section名称（如 "User Message"）
            **kwargs: 模板变量

        Returns:
            渲染后的模板内容（排除指定section）

        Example:
            获取系统消息（排除 "User Message" section）
        """
        import re

        # 先渲染整个模板
        rendered = self.render(**kwargs)

        # 移除指定section
        pattern = rf"## {re.escape(section_name)}\s*\n.*?(?=\n## |\Z)"
        result = re.sub(pattern, "", rendered, flags=re.DOTALL)

        return result.strip()


# === Prompt 管理器 ===


class PromptManager:
    """
    Prompt 模板管理器

    职责：
    - 从指定目录加载所有 .md 模板文件
    - 解析 YAML frontmatter 元数据
    - 提供 template 语法 ($variable) 的渲染功能
    - 支持严格模式和安全模式渲染

    使用示例：
        ```python
        # 初始化管理器
        prompt_manager = PromptManager()
        prompt_manager.load_all()

        # 渲染模板
        result = prompt_manager.render("decision/intent", user_name="Alice", message="你好")

        # 安全模式渲染（缺失变量保留原样）
        result = prompt_manager.render_safe("decision/intent", user_name="Alice")

        # 获取元数据
        metadata = prompt_manager.get_metadata("decision/intent")
        ```
    """

    def __init__(self, templates_dir: Optional[str] = None):
        """
        初始化 Prompt 管理器

        Args:
            templates_dir: 模板目录路径，默认为 src/prompts/templates
        """
        if templates_dir is None:
            # 默认模板目录
            templates_dir = str(Path(__file__).parent / "templates")

        self.logger = get_logger("PromptManager")
        self.templates_dir = Path(templates_dir)
        self._templates: Dict[str, PromptTemplate] = {}

    def load_all(self) -> None:
        """加载所有 .md 模板文件"""
        if not self.templates_dir.exists():
            self.logger.warning(f"模板目录不存在: {self.templates_dir}")
            return

        # 递归查找所有 .md 文件
        for md_file in self.templates_dir.rglob("*.md"):
            # 计算相对于 templates_dir 的路径作为模板名称
            rel_path = md_file.relative_to(self.templates_dir)
            # 移除 .md 扩展名，并将路径分隔符统一为 /
            template_name = str(rel_path.with_suffix("")).replace("\\", "/")

            try:
                self._load_template(template_name, md_file)
            except Exception as e:
                self.logger.error(f"加载模板失败 {template_name}: {e}", exc_info=True)

        self.logger.info(f"已加载 {len(self._templates)} 个模板")

    def _load_template(self, name: str, path: Path) -> None:
        """
        加载单个模板文件

        Args:
            name: 模板名称
            path: 模板文件路径

        Raises:
            ValueError: 如果文件不存在或解析失败
        """
        # 读取文件内容
        raw_content = path.read_text(encoding="utf-8")

        # 解析 frontmatter
        frontmatter, content = self._parse_frontmatter(raw_content)

        # 构建元数据
        metadata = TemplateMetadata(
            name=name,
            description=frontmatter.get("description"),
            version=frontmatter.get("version"),
            variables=frontmatter.get("variables") or [],
            author=frontmatter.get("author"),
            tags=frontmatter.get("tags") or [],
        )

        # 存储模板
        self._templates[name] = PromptTemplate(
            name=name,
            content=content,
            raw=raw_content,
            metadata=metadata,
            path=path,
        )

        self.logger.debug(f"已加载模板: {name}")

    def _parse_frontmatter(self, content: str) -> tuple[Dict[str, Any], str]:
        """
        解析 YAML frontmatter

        Args:
            content: 原始文件内容

        Returns:
            (frontmatter_dict, content_without_frontmatter)

        Frontmatter 格式：
            ---
            key: value
            ---
            实际内容
        """
        import re

        # 匹配 frontmatter
        pattern = r"^---\s*\n(.*?)\n---\s*\n(.*)$"
        match = re.match(pattern, content, re.DOTALL)

        if match:
            yaml_str = match.group(1)
            body = match.group(2).strip()

            # 解析 YAML（支持多行列表）
            frontmatter: Dict[str, Any] = {}
            current_key: Optional[str] = None
            in_list = False
            list_values: list[str] = []

            for line in yaml_str.split("\n"):
                line = line.rstrip()

                # 空行：结束当前列表
                if not line.strip():
                    if in_list and current_key:
                        frontmatter[current_key] = list_values
                        list_values = []
                        in_list = False
                        current_key = None
                    continue

                # 列表项（以 - 开头）
                if line.strip().startswith("- "):
                    list_value = line.strip()[2:].strip().strip("\"'")
                    list_values.append(list_value)
                    in_list = True
                    continue

                # 键值对（包含 :）
                if ":" in line:
                    # 先保存之前的列表
                    if in_list and current_key:
                        frontmatter[current_key] = list_values
                        list_values = []
                        in_list = False

                    key, value = line.split(":", 1)
                    key = key.strip()
                    value = value.strip()

                    # 处理行内列表格式 [item1, item2]
                    if value.startswith("[") and value.endswith("]"):
                        list_content = value[1:-1].strip()
                        if list_content:
                            frontmatter[key] = [v.strip().strip("\"'") for v in list_content.split(",")]
                        else:
                            frontmatter[key] = []
                        current_key = None
                        in_list = False
                    # 处理字符串值
                    elif value:
                        frontmatter[key] = value.strip("\"'")
                        current_key = None
                        in_list = False
                    # 空值，可能是多行列表的开始
                    else:
                        current_key = key
                        in_list = True
                        list_values = []

            # 处理最后的列表
            if in_list and current_key:
                frontmatter[current_key] = list_values

            return frontmatter, body

        # 没有 frontmatter
        return {}, content

    # === 渲染接口 ===

    def render(self, template_name: str, **kwargs: Any) -> str:
        """
        渲染模板（严格模式）

        Args:
            template_name: 模板名称
            **kwargs: 模板变量

        Returns:
            渲染后的字符串

        Raises:
            KeyError: 如果模板不存在或缺少必需的变量
        """
        template = self._get_template(template_name)
        return template.render(**kwargs)

    def render_safe(self, template_name: str, **kwargs: Any) -> str:
        """
        渲染模板（安全模式）

        Args:
            template_name: 模板名称
            **kwargs: 模板变量

        Returns:
            渲染后的字符串（缺失变量保留原样）

        Raises:
            KeyError: 如果模板不存在
        """
        template = self._get_template(template_name)
        return template.render_safe(**kwargs)

    # === 查询接口 ===

    def get_raw(self, template_name: str) -> str:
        """
        获取原始模板内容

        Args:
            template_name: 模板名称

        Returns:
            原始模板内容（含 frontmatter）

        Raises:
            KeyError: 如果模板不存在
        """
        template = self._get_template(template_name)
        return template.raw

    def get_metadata(self, template_name: str) -> TemplateMetadata:
        """
        获取模板元数据

        Args:
            template_name: 模板名称

        Returns:
            模板元数据

        Raises:
            KeyError: 如果模板不存在
        """
        template = self._get_template(template_name)
        return template.metadata

    def extract_section(self, template_name: str, section_name: str, **kwargs: Any) -> str:
        """
        提取并渲染模板中的特定section

        Args:
            template_name: 模板名称
            section_name: section名称（如 "User Message"）
            **kwargs: 模板变量

        Returns:
            提取并渲染后的section内容，如果section不存在则返回空字符串

        Raises:
            KeyError: 如果模板不存在

        Example:
            提取 "## User Message" section
        """
        template = self._get_template(template_name)
        return template.extract_section(section_name, **kwargs)

    def extract_content_without_section(self, template_name: str, section_name: str, **kwargs: Any) -> str:
        """
        提取模板内容，排除指定的section

        Args:
            template_name: 模板名称
            section_name: 要排除的section名称（如 "User Message"）
            **kwargs: 模板变量

        Returns:
            渲染后的模板内容（排除指定section）

        Raises:
            KeyError: 如果模板不存在

        Example:
            获取系统消息（排除 "User Message" section）
        """
        template = self._get_template(template_name)
        return template.extract_content_without_section(section_name, **kwargs)

    def list_templates(self) -> list[str]:
        """
        列出所有已加载的模板名称

        Returns:
            模板名称列表
        """
        return sorted(self._templates.keys())

    # === 内部方法 ===

    def _get_template(self, name: str) -> PromptTemplate:
        """
        获取模板

        Args:
            name: 模板名称

        Returns:
            PromptTemplate 实例

        Raises:
            KeyError: 如果模板不存在
        """
        if name not in self._templates:
            available = ", ".join(self.list_templates())
            raise KeyError(f"模板 '{name}' 不存在。可用模板: {available}")
        return self._templates[name]


# === 全局单例 ===

_prompt_manager_singleton: Optional[PromptManager] = None


def get_prompt_manager() -> PromptManager:
    """
    获取 PromptManager 全局单例

    Returns:
        PromptManager 实例（惰性初始化）
    """
    global _prompt_manager_singleton
    if _prompt_manager_singleton is None:
        _prompt_manager_singleton = PromptManager()
        _prompt_manager_singleton.load_all()
    return _prompt_manager_singleton


def reset_prompt_manager() -> None:
    """重置全局单例（用于测试）"""
    global _prompt_manager_singleton
    _prompt_manager_singleton = None


__all__ = [
    "PromptManager",
    "PromptTemplate",
    "TemplateMetadata",
    "get_prompt_manager",
    "reset_prompt_manager",
]
