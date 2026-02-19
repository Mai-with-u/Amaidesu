"""
TemplateOverrideService - MaiBot 提示词覆盖服务

职责:
- 从 PromptManager 加载覆盖模板
- 构建 TemplateInfo 对象
- 提供原始模板字符串（变量由 MaiBot 端填充）
- 剥离 YAML frontmatter 元数据
"""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from maim_message import TemplateInfo

from . import get_prompt_manager
from src.modules.logging import get_logger


@dataclass
class OverrideConfig:
    """覆盖配置"""

    enabled: bool = False
    template_name: str = "amaidesu_override"
    templates: List[str] = field(default_factory=lambda: ["replyer_prompt"])

    def __post_init__(self):
        if self.templates is None:
            self.templates = ["replyer_prompt"]


class TemplateOverrideService:
    """
    提示词覆盖服务

    负责构建发送给 MaiBot 的 TemplateInfo 对象，
    实现提示词覆盖功能。

    重要说明：
    - 模板变量由 MaiBot 端填充，本服务只提供原始模板字符串
    - 使用 get_raw() 获取原始模板内容，不进行变量渲染
    - 自动剥离 YAML frontmatter（---之间的元数据）
    """

    OVERRIDE_TEMPLATE_DIR = "maibot_override"

    def __init__(self, config: OverrideConfig):
        self.config = config
        self.logger = get_logger("TemplateOverrideService")
        self._prompt_manager = get_prompt_manager()
        # 初始化时打印配置状态
        self.logger.info(
            f"TemplateOverrideService 初始化: enabled={config.enabled}, template_name={config.template_name}, templates={config.templates}"
        )

    @staticmethod
    def _strip_yaml_frontmatter(content: str) -> str:
        """
        剥离 YAML frontmatter

        YAML frontmatter 格式：
        ---
        name: template_name
        version: "1.0"
        ---
        实际内容...

        Args:
            content: 原始模板内容

        Returns:
            剥离 frontmatter 后的内容
        """
        # 匹配开头的 YAML frontmatter（--- ... ---）
        pattern = r"^---\s*\n.*?\n---\s*\n"
        stripped = re.sub(pattern, "", content, flags=re.DOTALL)
        return stripped.strip()

    def build_template_info(self) -> Optional[TemplateInfo]:
        """
        构建 TemplateInfo 对象

        Returns:
            TemplateInfo 对象，如果覆盖功能未启用则返回 None
        """
        self.logger.info(f"build_template_info 被调用: enabled={self.config.enabled}")
        if not self.config.enabled:
            self.logger.info("提示词覆盖功能未启用，返回 None")
            return None

        template_items: Dict[str, str] = {}

        for template_name in self.config.templates:
            try:
                template_key = f"{self.OVERRIDE_TEMPLATE_DIR}/{template_name}"
                self.logger.info(f"尝试加载模板: {template_key}")
                # 获取原始模板内容（不渲染变量，由 MaiBot 端填充）
                raw_content = self._prompt_manager.get_raw(template_key)
                if raw_content:
                    # 剥离 YAML frontmatter，只发送实际模板内容
                    clean_content = self._strip_yaml_frontmatter(raw_content)
                    self.logger.info(
                        f"模板 {template_key} 加载成功，原始长度: {len(raw_content)}，清理后长度: {len(clean_content)}"
                    )
                    template_items[template_name] = clean_content
            except KeyError:
                self.logger.warning(f"模板 {template_key} 不存在，跳过")
            except Exception as e:
                self.logger.warning(f"加载模板 {template_name} 失败: {e}")

        if not template_items:
            self.logger.warning("没有成功加载任何覆盖模板，跳过 template_info 注入")
            return None

        self.logger.info(f"成功加载 {len(template_items)} 个覆盖模板: {list(template_items.keys())}")
        return TemplateInfo(
            template_items=template_items,
            template_name=self.config.template_name,
            template_default=False,  # 关键：False 表示启用覆盖
        )
