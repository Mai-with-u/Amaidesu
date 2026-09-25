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
        # 机器可以直接经 build_machine 编译；修改既有工地也必须受同一重复拒绝判断保护。
        if goal.get("ability") not in {"maicraft:design_machine", "maicraft:build_machine", "maicraft:modify_machine"}:
            return None
        error = observation.get("error")
        validation = observation.get("validation")
        if isinstance(validation, dict) and validation.get("valid") is False:
            # 编译器的 needs_revision 回执没有传输错误，仍明确表示这份蓝图尚未通过校验。
            error = {
                "code": "invalid_semantic_goal",
                "message": "machine_blueprint_requires_revision",
                "design_diagnostics": validation,
                "outcome_known": True,
            }
        if (
            observation.get("accepted")
            or not isinstance(error, dict)
            and observation.get("ok") is not False
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
                "ability": goal.get("ability"),
                "target": goal.get("target"),
                # 换现场观察编号并没有修改被拒绝的蓝图；实际选址或参数变化仍保留在指纹里。
                "parameters": {
                    key: value for key, value in (goal.get("parameters") or {}).items() if key != "snapshot_id"
                },
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
