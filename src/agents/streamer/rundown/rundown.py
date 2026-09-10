"""流程单（Rundown）数据契约

设计要点
--------

- 与 ``src/agents/streamer/agenda/agenda.py`` 同类：**主播 Agent 内部契约**，
  不跨 Agent 共享，内聚于 ``src/agents/streamer/`` 下。
- 描述一场直播的**预定义流程单**：通过 WebUI 手工建立环节清单，自驱动
  主播 Agent 拿到的是"备忘录 + 闹钟"——参考材料与超时提醒，不替 Agent
  按推进按钮。Agent 自行决定何时通过工具切换环节（切片 3 交付）。
- ``extra="forbid"``：与代码库其他 Pydantic 模型保持一致，严格拒绝未知字段。

字段说明
--------

``RundownSegment`` —— 流程单中单个环节

- ``id``：环节唯一标识（流程单内唯一，goto 定位用）
- ``title``：环节标题，面向人展示
- ``task_description``：给 AI 的目标指引，允许自由发挥
- ``key_points``：要点列表（可空），给 AI 提供本环节需要覆盖的要点
- ``expected_ms``：预期停留时长（毫秒，``>= 1000``）——只用于进度显示与
  超时提醒，**不是切换器**
- ``min_duration_ms``：最少停留时长（可选）——工具校验下界，防御抢跑
- ``notes``：备注（可选）——导演直录内容（如参考开场白），直接注入上下文

``Rundown`` —— 整场流程单

- ``rundown_id``：业务唯一标识（存储主键、配置引用同一 id）
- ``title``：标题
- ``segments``：环节列表，非空

校验仅两条：环节 ``id`` 在流程单内唯一；任一环节 ``min_duration_ms`` 不大于
其 ``expected_ms``。无分支、无回退字段——"分支"是 Agent 看到情境中的后续
环节列表后自行 ``goto`` 的决定，不是数据结构。

``DEFAULT_RUNDOWN``
------------------

内置默认流程单（包内 Python 常量），主题"初次直播·自我介绍"。当配置未选
单、所选 id 不存在或库为空时启用；**虚拟存在，不写库**——用户在 WebUI 建立
第一份流程单后自然替代。编辑器"新建"可从它预填。

时间字段约定
------------

时长用 ``*_ms`` int（Unix epoch 毫秒）；与仓库 ``src/modules/time_utils.py``
``now_ms()`` 输出的时刻字段单位一致。时刻字段与时长字段统一毫秒，零转换。
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


__all__ = ["RundownSegment", "Rundown", "DEFAULT_RUNDOWN"]


# ---------------------------------------------------------------------------
# 环节
# ---------------------------------------------------------------------------


class RundownSegment(BaseModel):
    """流程单中单个环节——一段有目标 / 时长 / 要点的直播片段。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="环节唯一标识（用于 goto 定位，流程单内唯一）")
    title: str = Field(..., description="环节标题，面向人展示")
    task_description: str = Field(
        ...,
        description="给 AI 的目标指引（开场/话题引导/收尾等），允许 AI 自由发挥",
    )
    key_points: List[str] = Field(
        default_factory=list,
        description="要点列表（可空），给 AI 提供本环节需要覆盖的要点",
    )
    expected_ms: int = Field(
        ...,
        ge=1000,
        description="预期停留时长（毫秒，至少 1 秒）；只用于进度显示与超时提醒，不是切换器",
    )
    min_duration_ms: Optional[int] = Field(
        default=None,
        ge=1000,
        description="最少停留时长（毫秒，可选）；工具校验下界，防御 Agent 抢跑",
    )
    notes: Optional[str] = Field(
        default=None,
        description="备注（可选）；导演直录内容（如参考开场白），直接注入上下文",
    )


# ---------------------------------------------------------------------------
# 整场流程单
# ---------------------------------------------------------------------------


class Rundown(BaseModel):
    """整场流程单——若干环节的集合。"""

    model_config = ConfigDict(extra="forbid")

    rundown_id: str = Field(..., description="流程单唯一标识（业务可读，存储主键、配置引用同一 id）")
    title: str = Field(..., description="流程单标题，面向人展示")
    segments: List[RundownSegment] = Field(
        ...,
        min_length=1,
        description="环节列表，非空；每个环节 id 在流程单内唯一",
    )

    @model_validator(mode="after")
    def _validate_integrity(self) -> "Rundown":
        """跨字段完整性校验：环节 id 唯一；任一环节 ``min_duration_ms`` 不大于 ``expected_ms``。

        Raises:
            ValueError: 违反以下任一规则时
                - ``segments`` 中存在重复的 ``id``
                - 任一环节 ``min_duration_ms`` 大于其 ``expected_ms``（语义矛盾）
        """
        seen: set[str] = set()
        dupes: List[str] = []
        for seg in self.segments:
            if seg.id in seen and seg.id not in dupes:
                dupes.append(seg.id)
            seen.add(seg.id)
        if dupes:
            raise ValueError(f"Rundown.segments 中存在重复 id: {dupes}")

        for seg in self.segments:
            if seg.min_duration_ms is not None and seg.min_duration_ms > seg.expected_ms:
                raise ValueError(
                    f"segment '{seg.id}' 的 min_duration_ms({seg.min_duration_ms}) "
                    f"大于 expected_ms({seg.expected_ms})，语义矛盾"
                )

        return self


# ---------------------------------------------------------------------------
# 内置默认流程单
# ---------------------------------------------------------------------------
# 切片 1 拍板：流程单一律 WebUI 手工建立；未配置 / 指向不存在 / 库为空时启用
# 此常量。虚拟存在，不写库——用户在 WebUI 建立第一份流程单后自然替代。


DEFAULT_RUNDOWN: Rundown = Rundown(
    rundown_id="default_first_stream",
    title="初次直播",
    segments=[
        RundownSegment(
            id="opening",
            title="开场问候",
            task_description=(
                "用友善、轻松的语气向首次进入直播间的观众打招呼，"
                "说明这是首播，预告接下来会自我介绍，欢迎大家在弹幕互动。"
            ),
            key_points=["问好", "说明首播", "欢迎弹幕"],
            expected_ms=180_000,  # 约 3 分钟
        ),
        RundownSegment(
            id="self_intro",
            title="自我介绍",
            task_description=(
                "围绕自己的角色定位、开播初衷、未来内容规划展开，让观众快速了解主播是谁、为什么开播、接下来打算做什么。"
            ),
            key_points=["角色身份", "开播初衷", "未来内容方向"],
            expected_ms=600_000,  # 约 10 分钟
        ),
        RundownSegment(
            id="chat",
            title="互动闲聊",
            task_description=("欢迎观众弹幕提问与闲聊，主动抛出话题制造氛围；保持对话流动，不主导话题而是呼应观众。"),
            key_points=["回应弹幕", "抛出互动话题", "维持氛围"],
            expected_ms=1_200_000,  # 约 20 分钟
        ),
        RundownSegment(
            id="closing",
            title="收尾预告",
            task_description=("感谢今晚陪伴的观众，简短预告下次直播内容与下播时间，自然结束本次首播。"),
            key_points=["感谢观众", "预告下次直播", "道别"],
            expected_ms=300_000,  # 约 5 分钟
        ),
    ],
)
