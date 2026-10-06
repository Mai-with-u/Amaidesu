"""Skill 数据模型：元数据、加载后的技能与目录条目

Skill 是"怎么把一类事做成"的经验文档（打法、步骤、决策点、常见坑），
与能力契约（某个能力怎么调）和外部知识库（世界里的事实是什么）分工：
契约与事实由能力提供方给出，打法由使用能力的 Agent 一侧沉淀。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Set
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# 技能名同时是 LLM 读取正文时填写的参数，只用小写字母、数字与下划线，避免大小写与空白歧义。
_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")

# 已知的环境事实：类别 → 当前环境里存在的项（如 {"mods": {"create", "mekanism"}}）。
# 类别不在映射里表示该类事实未知，而不是"一项都没有"。
SkillEnvironment = Mapping[str, Set[str]]

# 适用性三值：满足 / 已知不满足 / 有前提尚未得到确认
Applicability = Literal["applicable", "unmet", "unverified"]


class SkillMetadata(BaseModel):
    """技能元数据（YAML frontmatter）

    ``agents`` 是生产侧受众声明：技能作者写明给哪些 Agent 用，消费方只按自己的
    注册名取用；``"*"`` 单独出现表示所有接入技能的 Agent。
    ``requires`` 是环境前提：类别由消费方的环境事实定义（如 ``mods``），
    已知不满足的技能不进目录。
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="全局唯一技能名（LLM 读取正文时填写）")
    description: str = Field(min_length=1, description="一句话说明什么时候用（进入目录）")
    agents: list[str] = Field(min_length=1, description="受众 Agent 注册名列表，或单独的 '*'")
    category: str = Field(min_length=1, description="技能分类（目录按此分组）")
    requires: dict[str, list[str]] = Field(default_factory=dict, description="环境前提：事实类别 → 必须全部存在的项")
    tags: list[str] = Field(default_factory=list, description="标签")

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        if not _NAME_PATTERN.match(value):
            raise ValueError(f"技能名只能由小写字母、数字、下划线组成且以字母开头: {value!r}")
        return value

    @field_validator("description", "category")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("不能为空白")
        return stripped

    @field_validator("agents")
    @classmethod
    def _check_agents(cls, value: list[str]) -> list[str]:
        if "*" in value and len(value) > 1:
            raise ValueError("'*' 表示全部 Agent，必须单独出现")
        if any(not agent.strip() for agent in value):
            raise ValueError("受众不能含空白名")
        return value

    @field_validator("requires")
    @classmethod
    def _check_requires(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        for kind, items in value.items():
            if not items:
                raise ValueError(f"前提类别 {kind!r} 至少列出一项")
        return value

    def visible_to(self, agent: str) -> bool:
        """该技能是否给指定 Agent 用。"""
        return self.agents == ["*"] or agent in self.agents

    def applicability(self, environment: SkillEnvironment) -> tuple[Applicability, dict[str, list[str]]]:
        """按已知环境事实判断适用性。

        Returns:
            (适用性, 相关前提)。已知不满足时返回缺失项；尚未确认时返回未知类别下要求的项；
            满足时第二项为空。
        """
        unmet: dict[str, list[str]] = {}
        unverified: dict[str, list[str]] = {}
        for kind, items in self.requires.items():
            present = environment.get(kind)
            if present is None:
                unverified[kind] = list(items)
                continue
            missing = [item for item in items if item not in present]
            if missing:
                unmet[kind] = missing
        if unmet:
            return "unmet", unmet
        if unverified:
            return "unverified", unverified
        return "applicable", {}


class Skill(BaseModel):
    """加载后的技能：元数据 + 正文"""

    metadata: SkillMetadata
    content: str = Field(description="正文（不含 frontmatter）")
    path: Path = Field(description="来源文件")

    @property
    def name(self) -> str:
        return self.metadata.name


class SkillEntry(BaseModel):
    """目录条目：某个 Agent 在当前环境下看到的一项技能"""

    name: str
    description: str
    category: str
    # 有前提但当前环境尚未确认时，列出待确认的项，由模型结合现场自行判断
    unverified: dict[str, list[str]] = Field(default_factory=dict)


__all__ = [
    "Applicability",
    "Skill",
    "SkillEntry",
    "SkillEnvironment",
    "SkillMetadata",
]
