"""限制同一失败机器设计的重复提交，保留模型修改方案的自主权。"""

from __future__ import annotations

import json
from typing import Any


class MachineDesignProgress:
    """只处理已明确拒绝的设计请求，不把异步受理或结果未知当作失败重试。"""

    def __init__(self) -> None:
        self._last_rejection = ""
        self._repeats = 0

    def reset(self) -> None:
        """新的玩家指令可以重新尝试；后台通知不重置同一设计的重复拒绝计数。"""
        self._last_rejection = ""
        self._repeats = 0

    def observe(self, name: str, arguments: dict[str, Any], observation: dict[str, Any]) -> str | None:
        """不同方案或不同诊断表示进展，同一方案反复被同一规则拒绝时让角色停下。"""
        goal = arguments.get("goal")
        if name not in {"maicraft_plan", "maicraft_execute"} or not isinstance(goal, dict):
            return None
        if goal.get("ability") != "maicraft:design_machine":
            return None
        error = observation.get("error")
        if (
            observation.get("accepted")
            or observation.get("ok") is not False
            and observation.get("success") is not False
        ):
            self.reset()
            return None
        if not isinstance(error, dict) or error.get("outcome_known") is False:
            return None
        if error.get("code") not in {"invalid_arguments", "invalid_semantic_goal"}:
            return None
        # 请求键和消息措辞不是设计变化；保留完整方案及规则诊断，允许模型真正修改后重新审阅。
        signature = json.dumps(
            {
                "target": goal.get("target"),
                "parameters": goal.get("parameters"),
                "code": error.get("code"),
                "message": error.get("message"),
                "diagnostics": error.get("design_diagnostics"),
            },
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
        self._repeats = self._repeats + 1 if signature == self._last_rejection else 1
        self._last_rejection = signature
        if self._repeats >= 3:
            return f"同一机器设计连续被相同规则拒绝三次：{error.get('message', error.get('code'))}。已保留原目标与待办，请修订设计或补足缺失能力后继续。"
        return None
