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

import frontmatter
import re

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
    - 从多个扫描根发现并加载所有 .md 模板文件（多根发现）
    - 解析 YAML frontmatter 元数据
    - 提供 template 语法 ($variable) 的渲染功能
    - 支持严格模式和安全模式渲染

    模板键规则（声明式键，与文件位置解耦）：
    - 优先使用 frontmatter 的 ``name`` 字段作为全局唯一键
    - 未声明 ``name`` 时兜底使用相对扫描根的路径（去扩展名、``/`` 分隔）
    - 键重复注册立即抛出 :class:`ValueError`（fail-fast，防止静默覆盖）

    扫描根：
    - 中央目录 ``src/modules/prompts/templates``（兼容保留，可放跨组件共享提示词）
    - 约定内聚目录：``src/**/prompts/``——组件把提示词放在自己包内的
      ``prompts/`` 子目录即可被自动发现。该约定扫描仅在 ``auto_scan_src=True``
      时启用（全局单例 ``get_prompt_manager`` 默认开启；裸构造默认关闭，
      保证单元测试的隔离性）

    使用示例：
        ```python
        # 初始化管理器
        prompt_manager = PromptManager()
        prompt_manager.load_all()

        # 渲染模板（键来自模板 frontmatter 的 name 字段）
        result = prompt_manager.render("amaidesu_replyer", text="你好")

        # 安全模式渲染（缺失变量保留原样）
        result = prompt_manager.render_safe("amaidesu_replyer", text="你好")

        # 获取元数据
        metadata = prompt_manager.get_metadata("amaidesu_replyer")
        ```
    """

    def __init__(
        self,
        templates_dir: Optional[str] = None,
        auto_scan_src: bool = False,
    ):
        """
        初始化 Prompt 管理器

        Args:
            templates_dir: 中央模板目录路径，默认为 src/modules/prompts/templates。
                该目录与约定扫描根共同构成发现范围。
            auto_scan_src: 是否按 ``src/**/prompts/`` 约定扫描内聚提示词目录。
                生产单例应开启；测试裸构造保持关闭以隔离真实仓库模板。
        """
        if templates_dir is None:
            # 默认中央模板目录
            templates_dir = str(Path(__file__).parent / "templates")

        self.logger = get_logger("PromptManager")
        self.templates_dir = Path(templates_dir)
        self.auto_scan_src = auto_scan_src
        self._extra_roots: list[Path] = []
        self._templates: Dict[str, PromptTemplate] = {}

    def register_scan_root(self, path: "Path | str") -> None:
        """注册额外的模板扫描根（在 load_all 之前调用）

        用于测试注入或非常规布局；生产环境通常依赖 src/**/prompts/ 约定扫描。

        Args:
            path: 要追加扫描的目录
        """
        self._extra_roots.append(Path(path))

    def _discover_scan_roots(self) -> list[Path]:
        """发现全部模板扫描根

        组成（按优先序）：
        1. 显式注册的额外根（``register_scan_root``）
        2. 中央目录 ``templates_dir``
        3. 约定内聚目录：``<src/>**/prompts/``（排除 prompts 包自身，
           仅当 ``auto_scan_src=True``）

        Returns:
            去重后的扫描根列表（排序保证确定性）
        """
        seen: set[Path] = set()
        roots: list[Path] = []

        def _add(candidate: Path) -> None:
            try:
                resolved = candidate.resolve()
            except OSError:
                return
            if resolved not in seen:
                seen.add(resolved)
                roots.append(candidate)

        for extra in self._extra_roots:
            _add(extra)
        _add(self.templates_dir)

        if self.auto_scan_src:
            # 约定扫描：<src/>**/prompts/
            package_dir = Path(__file__).resolve().parent  # src/modules/prompts
            src_root = package_dir.parents[1]  # src/
            for prompts_dir in sorted(src_root.rglob("prompts")):
                if not prompts_dir.is_dir():
                    continue
                if prompts_dir == package_dir:
                    # prompts 包自身不作为扫描根（其 templates 子目录已是中央根）
                    continue
                _add(prompts_dir)

        return roots

    def load_all(self) -> None:
        """加载所有扫描根下的 .md 模板文件"""
        self._templates.clear()

        for root in self._discover_scan_roots():
            if not root.exists():
                if root == self.templates_dir:
                    self.logger.debug(f"中央模板目录不存在，跳过: {root}")
                continue

            # 递归查找该根下所有 .md 文件
            for md_file in sorted(root.rglob("*.md")):
                # 兜底键：相对扫描根的路径（去扩展名、统一 / 分隔）
                rel_path = md_file.relative_to(root)
                fallback_name = str(rel_path.with_suffix("")).replace("\\", "/")

                try:
                    self._load_template(fallback_name, md_file)
                except ValueError:
                    # 键冲突必须 fail-fast，禁止静默吞掉导致行为漂移
                    raise
                except Exception as e:
                    self.logger.error(f"加载模板失败 {fallback_name}: {e}", exc_info=True)

        self.logger.info(f"已加载 {len(self._templates)} 个模板")

    def _load_template(self, fallback_name: str, path: Path) -> None:
        """
        加载单个模板文件

        Args:
            fallback_name: 兜底键（相对扫描根的路径名），仅在 frontmatter
                未声明 ``name`` 时使用
            path: 模板文件路径

        Raises:
            ValueError: 模板键与已注册模板冲突（fail-fast）
        """
        # 读取文件内容
        raw_content = path.read_text(encoding="utf-8")

        # 解析 frontmatter
        frontmatter_data, content = self._parse_frontmatter(raw_content)

        # 声明式键优先：frontmatter name > 路径兜底键
        declared = frontmatter_data.get("name")
        template_name = str(declared).strip() if declared else fallback_name

        existing = self._templates.get(template_name)
        if existing is not None:
            raise ValueError(f"模板键冲突: '{template_name}' 已由 {existing.path} 注册，拒绝加载重复文件 {path}")

        # 构建元数据（metadata.name 与注册键保持一致）
        metadata = TemplateMetadata(
            name=template_name,
            description=frontmatter_data.get("description"),
            version=frontmatter_data.get("version"),
            variables=frontmatter_data.get("variables") or [],
            author=frontmatter_data.get("author"),
            tags=frontmatter_data.get("tags") or [],
        )

        # 存储模板
        self._templates[template_name] = PromptTemplate(
            name=template_name,
            content=content,
            raw=raw_content,
            metadata=metadata,
            path=path,
        )

        self.logger.debug(f"已加载模板: {template_name}")

    def _parse_frontmatter(self, content: str) -> tuple[Dict[str, Any], str]:
        """
        解析 YAML frontmatter

        使用 python-frontmatter 库解析，替代自定义正则解析。

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
        try:
            post = frontmatter.loads(content)
            metadata = dict(post.metadata)

            # 处理类型转换（frontmatter 可能将 "1.0" 解析为 float）
            if "version" in metadata and isinstance(metadata["version"], float):
                metadata["version"] = str(metadata["version"])

            return metadata, post.content
        except Exception as e:
            self.logger.warning(f"frontmatter 解析失败，使用原始内容: {e}")
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

    单例启用 ``auto_scan_src``：按 ``src/**/prompts/`` 约定发现各组件
    内聚的提示词目录。

    Returns:
        PromptManager 实例（惰性初始化）
    """
    global _prompt_manager_singleton
    if _prompt_manager_singleton is None:
        _prompt_manager_singleton = PromptManager(auto_scan_src=True)
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
