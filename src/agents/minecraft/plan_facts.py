"""把 Mod 计划的生命周期与普通查询回执分开，长期保留下一步可执行的事实。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


class MinecraftPlanFacts:
    """只记录实际规划和执行回执，不替模型提交、改写或重试游戏动作。"""

    def __init__(self) -> None:
        self._plans: dict[str, dict[str, Any]] = {}

    def clear(self) -> None:
        """新逻辑任务开始时释放旧计划；后台等待和历史整理继续沿用已有计划。"""
        self._plans.clear()

    def observe(self, tool: str, arguments: dict[str, Any], result: dict[str, Any], ref: str | None) -> None:
        """同一目标的新规划替代旧版本，执行回执则把计划标为已提交或需要核查。"""
        goal = arguments.get("goal")
        if tool.endswith("_plan") and isinstance(goal, dict):
            identity = {key: deepcopy(goal[key]) for key in ("ability", "outcome", "target") if key in goal}
            # 修订失败也不能让旧版本继续显示为可直接开工；修改后的要求必须以新的规划回执为准。
            for prior in self._plans.values():
                if prior["goal"] == identity and prior["state"] == "ready":
                    prior["state"] = "superseded"
            plan_id = result.get("plan_id")
            if isinstance(plan_id, str) and result.get("ready_to_execute") is True:
                self._plans[plan_id] = {
                    "plan_id": plan_id,
                    "state": "ready",
                    "goal": identity,
                    "result_ref": ref,
                }
        if tool.endswith("_execute") and isinstance(arguments.get("plan_id"), str):
            plan = self._plans.get(arguments["plan_id"])
            if plan is None:
                return
            error = result.get("error")
            if result.get("accepted") is True and result.get("task_id"):
                plan.update(state="submitted", task_id=str(result["task_id"]), execution_ref=ref)
            else:
                # 没有受理回执时保留结果未知与明确拒绝的区别，不能再把它提示为尚未尝试的计划。
                known = (
                    result.get("outcome_known") is True
                    or isinstance(error, dict)
                    and error.get("outcome_known") is True
                )
                plan.update(state="execution_rejected" if known else "execution_unknown", execution_ref=ref)

    def snapshot(self) -> list[dict[str, Any]]:
        """整理历史只携带计划身份和阶段，完整蓝图仍从原始引用读取。"""
        return deepcopy(list(self._plans.values()))

    def pending(self) -> list[dict[str, Any]]:
        """普通观察后仍提示待执行的计划，查询结果不能把已通过的阶段挤出当前视野。"""
        return [
            {"plan_id": plan["plan_id"], "state": "ready", "result_ref": plan["result_ref"]}
            for plan in self._plans.values()
            if plan["state"] == "ready"
        ]
