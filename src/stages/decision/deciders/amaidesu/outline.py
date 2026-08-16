"""StreamOutline - 直播大纲数据契约(Decision 阶段内部使用)。

设计要点
--------
- 与 :mod:`plan.py` 的 ``DecisionPlan`` 同类:**决策阶段内部契约**,不跨阶段共享,
  因此放在 ``src/stages/decision/deciders/amaidesu/`` 下,而不是 ``src/modules/types/``。
- 描述一场直播的**预定义战略层**:主播通过 TOML 文件预定义环节流程,系统在时间驱动
  + AI 顺带评估下自动推进;零观众也能按计划直播,弹幕可打断但保持大纲对齐。
- ``extra="forbid"``:与代码库其他 Pydantic 模型保持一致,严格拒绝未知字段。

字段说明
--------
``OutlineSegment`` —— 大纲中单个环节
- ``id``:环节唯一标识,用于分支跳转定位
- ``title``:环节标题,面向人(主播/观众)展示
- ``task_description``:给 AI 的任务指引,允许自由发挥(开场/话题引导/收尾等)
- ``duration_ms``:默认停留时长(毫秒),``>= 1000`` 即至少 1 秒(防止 0 时长立刻跳过)
- ``min_duration_ms``:最少停留时长(毫秒,可选),防御 AI 过早推进——
  即使 AI 觉得"讲完了",也要撑够这个时长再允许切换到下一环节
- ``key_points``:关键节点列表(可空),给 AI 提供本环节需要覆盖的要点
- ``branches``:可选分支列表,AI 在环节末尾根据直播情况选择跳转目标

``OutlineBranch`` —— 环节内分支
- ``branch_id``:分支唯一标识(在同一环节内唯一)
- ``description``:给 LLM 的分支触发条件描述(如"观众强烈反馈想看某个游戏就跳过去")
- ``target_segment_id``:跳转目标环节的 ``id``,必须指向存在的 ``segment_id``

``StreamOutline`` —— 整场直播大纲
- ``outline_id``:大纲唯一标识
- ``title``:大纲标题(面向人)
- ``segments``:环节列表,非空;每个环节 ``id`` 唯一
- ``fallback_segment_id``:分支未命中时的回退目标环节 ``id``(可选)

校验规则
--------
1. ``segments`` 非空;
2. 所有 ``branches[].target_segment_id`` 必须指向 ``segments`` 中存在的 ``id``;
3. 所有 ``segments[].id`` 在大纲内唯一;
4. ``fallback_segment_id`` 若设置,必须指向存在的 ``id``;
5. ``min_duration_ms`` 若设置,不得大于 ``duration_ms``(否则"最少停留"比"默认停留"还长,语义矛盾)。

便捷方法
--------
- ``StreamOutline.get_total_planned_ms()``:累加各环节 ``duration_ms``,
  供 ``OutlineState`` 计算整场进度百分比使用。
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


__all__ = ["OutlineBranch", "OutlineSegment", "StreamOutline", "parse_outline_toml"]


# ---------------------------------------------------------------------------
# 分支
# ---------------------------------------------------------------------------


class OutlineBranch(BaseModel):
    """大纲环节内的可选分支,描述 AI 触发的跳转条件与目标。"""

    model_config = ConfigDict(extra="forbid")

    branch_id: str = Field(..., description="分支唯一标识(在同一 OutlineSegment 内唯一)")
    description: str = Field(
        ...,
        description="给 LLM 的分支触发条件描述,例如 '观众强烈反馈想看某游戏就跳过去'",
    )
    target_segment_id: str = Field(..., description="跳转目标环节的 id,必须指向 segments 中存在的 id")


# ---------------------------------------------------------------------------
# 环节
# ---------------------------------------------------------------------------


class OutlineSegment(BaseModel):
    """直播大纲中单个环节——一段有目标 / 时长 / 任务描述的直播片段。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="环节唯一标识(用于分支跳转定位,大纲内唯一)")
    title: str = Field(..., description="环节标题,面向人展示")
    task_description: str = Field(
        ...,
        description="给 AI 的任务指引(开场/话题引导/收尾等),允许 AI 自由发挥",
    )
    duration_ms: int = Field(
        ...,
        ge=1000,
        description="默认停留时长(毫秒),至少 1 秒,防止配置错误导致立刻跳过",
    )
    min_duration_ms: Optional[int] = Field(
        default=None,
        ge=1000,
        description="最少停留时长(毫秒,可选);防御 AI 过早推进,撑够这个时长才允许切换",
    )
    key_points: List[str] = Field(
        default_factory=list,
        description="关键节点列表(可空),给 AI 提供本环节需要覆盖的要点",
    )
    branches: List[OutlineBranch] = Field(
        default_factory=list,
        description="可选分支列表,AI 在环节末尾根据直播情况选择跳转目标",
    )


# ---------------------------------------------------------------------------
# 整场大纲
# ---------------------------------------------------------------------------


class StreamOutline(BaseModel):
    """整场直播大纲——若干环节的集合,带可选回退目标。"""

    model_config = ConfigDict(extra="forbid")

    outline_id: str = Field(..., description="大纲唯一标识")
    title: str = Field(..., description="大纲标题,面向人展示")
    segments: List[OutlineSegment] = Field(
        ...,
        min_length=1,
        description="环节列表,非空;每个环节 id 在大纲内唯一",
    )
    fallback_segment_id: Optional[str] = Field(
        default=None,
        description="分支未命中时的回退目标环节 id(可选),必须指向 segments 中存在的 id",
    )

    @model_validator(mode="after")
    def _validate_integrity(self) -> "StreamOutline":
        """跨字段完整性校验:segments 非空、id 唯一、branch / fallback 目标指向存在 id。

        Raises:
            ValueError: 违反以下任一规则时
                - ``segments`` 为空(虽然 ``min_length=1`` 已覆盖,此处双保险)
                - ``segments`` 中存在重复的 ``id``
                - 任一 ``branch.target_segment_id`` 不在 ``segments.id`` 集合内
                - ``fallback_segment_id`` 已设置但不在 ``segments.id`` 集合内
                - 任一 ``segment.min_duration_ms`` 大于其 ``duration_ms``
        """
        # segments 非空(Pydantic min_length=1 已保证,此处显式再检一次以便错误信息更清晰)
        if not self.segments:
            raise ValueError("StreamOutline.segments 不能为空,至少需要一个环节")

        # 收集存在的 segment_id 集合
        seg_ids = {seg.id for seg in self.segments}

        # id 唯一性
        if len(seg_ids) != len(self.segments):
            seen: set[str] = set()
            dupes: List[str] = []
            for seg in self.segments:
                if seg.id in seen and seg.id not in dupes:
                    dupes.append(seg.id)
                seen.add(seg.id)
            raise ValueError(f"StreamOutline.segments 中存在重复 id: {dupes}")

        # branch target / fallback target 必须指向存在的 id
        for seg in self.segments:
            for branch in seg.branches:
                if branch.target_segment_id not in seg_ids:
                    raise ValueError(
                        f"segment '{seg.id}' 的 branch '{branch.branch_id}' 指向 "
                        f"不存在的 target_segment_id='{branch.target_segment_id}'"
                    )

        if self.fallback_segment_id is not None and self.fallback_segment_id not in seg_ids:
            raise ValueError(
                f"StreamOutline.fallback_segment_id='{self.fallback_segment_id}' "
                f"不在 segments.id 中(可用: {sorted(seg_ids)})"
            )

        # min_duration_ms 不应大于 duration_ms(语义矛盾)
        for seg in self.segments:
            if seg.min_duration_ms is not None and seg.min_duration_ms > seg.duration_ms:
                raise ValueError(
                    f"segment '{seg.id}' 的 min_duration_ms({seg.min_duration_ms}) "
                    f"大于 duration_ms({seg.duration_ms}),语义矛盾"
                )

        return self

    def get_total_planned_ms(self) -> int:
        """累加各环节 ``duration_ms``,返回整场计划总时长(毫秒)。

        用于 ``OutlineState`` 计算整场进度百分比:
        ``progress = elapsed_live_ms / total_planned_ms``。
        不包含 ``min_duration_ms``,因为 ``min_duration_ms`` 只是"最少停留"
        的下界,计划时长相加仍按 ``duration_ms`` 计。

        Returns:
            整场计划总时长(毫秒)
        """
        return sum(seg.duration_ms for seg in self.segments)


# ---------------------------------------------------------------------------
# TOML 解析
# ---------------------------------------------------------------------------


def parse_outline_toml(path: Path) -> StreamOutline:
    """从 TOML 文件加载并校验为 :class:`StreamOutline`。

    使用 Python 3.12 内置 :mod:`tomllib`(只读、无依赖)。

    Args:
        path: TOML 文件路径

    Returns:
        校验通过的 :class:`StreamOutline` 实例

    Raises:
        FileNotFoundError: 文件不存在
        PermissionError: 无读取权限
        tomllib.TOMLDecodeError: TOML 语法错误(自带行号信息,透传)
        pydantic.ValidationError: 结构 / 字段 / 跨字段校验失败
            (错误信息含字段名,如 ``duration_ms``)
    """
    path = Path(path)
    with path.open("rb") as f:
        data = tomllib.load(f)
    # Pydantic v2:校验错误自带字段路径(可定位到 duration_ms 这类嵌套字段),
    # 顶层错误包含原始 TOML 行号信息通过嵌套字段的 input 关联。
    return StreamOutline.model_validate(data)
