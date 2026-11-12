"""D-13 / D-14 回归测试：营地常识、取材留坑回填（details.pits）与实例能力/连锁挖矿事实（mod_features / chain_mining）。"""

from __future__ import annotations

from typing import Any, Dict, List

from src.agents.minecraft.glance import glance_brief, glance_self
from src.agents.minecraft.goal_ledger import GoalLedger, _dry_goal_summary
from src.agents.minecraft.maicraft import MaicraftReply, goal_run_of
from src.agents.minecraft.mod_facts import (
    WorldMiningFacts,
    chain_mining_text,
    mod_features_text,
    pits_text,
)
from src.agents.minecraft.tools import STREAMER_GUIDE
from src.agents.minecraft.work_log_snapshot import build_snapshot
from src.modules.prompts import get_prompt_manager, reset_prompt_manager
from src.modules.llm.payload import Response

from .minecraft_v1_fakes import FakeMaicraft, _call, _resp, _wait_until, make_minecraft_agent


def test_mod_facts_gracefully_absent_when_mod_not_reporting() -> None:
    """旧版 Mod 或尚未挖过方块时缺省返回 None，速览与状态快照不报错、不平白多出空键。"""
    assert mod_features_text({}) is None
    assert chain_mining_text(None) is None
    assert pits_text(None) is None
    assert pits_text([]) is None

    reply = MaicraftReply(
        ok=True,
        data={
            "position": {"x": 10.0, "y": 64.0, "z": -5.0, "dimension": "minecraft:overworld"},
            "health": 20,
            "food": 20,
        },
    )
    self_view = glance_self(reply)
    assert "mod_features" not in self_view
    assert "last_chain_mining" not in self_view

    snapshot = build_snapshot(reply)
    assert "实例能力" not in snapshot
    assert "最近挖掘" not in snapshot
    assert "近期留坑" not in snapshot


def test_mod_features_and_chain_mining_compact_formatting_and_do_not_crowd_brief() -> None:
    """实例能力与连锁挖矿统计转成单行短句挂入结构化速览和快照，绝不挤占 glance_brief 核心空间。"""
    assert mod_features_text({"mod_features": []}) == "无额外联动"
    features = mod_features_text(
        {
            "mod_features": [
                "ae2",
                "chain_mining",
                "create",
                "ftb_quests",
                "lootr",
                "mekanism",
                "sable",
                "sophisticated_backpacks",
                "touhou_little_maid",
                "custom_future_mod",
            ]
        }
    )
    assert features == (
        "应用能源2、连锁破坏、机械动力、FTB任务书、独立战利品箱、"
        "通用机械、Sable载具、精妙背包、车万女仆、custom_future_mod"
    )

    assert (
        chain_mining_text({"chained_batches": 5, "chained_blocks": 42, "single_blocks": 2, "not_chained_reason": None})
        == "连锁 5 批共 42 格、单挖 2 格"
    )
    assert (
        chain_mining_text(
            {
                "chained_batches": 2,
                "chained_blocks": 14,
                "single_blocks": 10,
                "not_chained_reason": "选区伸进未加载区块",
            }
        )
        == "连锁 2 批共 14 格、单挖 10 格（部分未连：选区伸进未加载区块）"
    )
    assert (
        chain_mining_text(
            {
                "chained_batches": 0,
                "chained_blocks": 0,
                "single_blocks": 28,
                "not_chained_reason": "工具剩余耐久不够一整批",
            }
        )
        == "单挖 28 格，未触发连锁（工具剩余耐久不够一整批）"
    )

    reply = MaicraftReply(
        ok=True,
        data={
            "position": {"x": 10.0, "y": 64.0, "z": -5.0, "dimension": "minecraft:overworld"},
            "health": 20,
            "food": 20,
            "held": "minecraft:iron_pickaxe",
            "mod_features": ["chain_mining", "lootr", "create"],
            "last_chain_mining": {
                "chained_batches": 0,
                "chained_blocks": 0,
                "single_blocks": 16,
                "not_chained_reason": "选区紧挨水或岩浆",
            },
        },
    )
    self_view = glance_self(reply)
    assert self_view["mod_features"] == "连锁破坏、独立战利品箱、机械动力"
    assert self_view["last_chain_mining"] == "单挖 16 格，未触发连锁（选区紧挨水或岩浆）"

    brief = glance_brief({**self_view, "biome": "minecraft:plains", "time": "白天"})
    assert "连锁" not in brief
    assert "机械动力" not in brief
    assert "选区紧挨水或岩浆" not in brief

    snapshot = build_snapshot(reply)
    assert "实例能力：连锁破坏、独立战利品箱、机械动力" in snapshot
    assert "最近挖掘：单挖 16 格，未触发连锁（选区紧挨水或岩浆）" in snapshot


def test_pits_and_chain_mining_in_goal_ledger_strip_coordinates() -> None:
    """取材目标结果里的 details.pits 与 details.chain_mining 记入委派摘要与最近结束簿，并剥除坐标。"""
    filled_pit = [
        {
            "at": {"x": -361, "y": 63, "z": 39, "dimension": "minecraft:overworld"},
            "depth": 3,
            "filled": True,
            "note": "收工前在（-361, 63, 39）用圆石把台阶入口封平了",
        }
    ]
    unfilled_pit = [
        {
            "at": {"x": -350, "y": 64, "z": 40, "dimension": "minecraft:overworld"},
            "depth": 4,
            "filled": False,
            "note": "身上没有多余的泥土或圆石，留着可走上来的台阶口",
        }
    ]
    filled_line = pits_text(filled_pit)
    assert filled_line is not None and "顺手回填留坑 1 处" in filled_line and "-361" not in filled_line
    unfilled_line = pits_text(unfilled_pit)
    assert unfilled_line is not None and "留坑 1 处未填平" in unfilled_line and "深 4 格" in unfilled_line

    run = goal_run_of(
        {
            "goal_id": 21,
            "ability": "maicraft:obtain",
            "purpose": "去营地外挖圆石",
            "state": "finished",
            "result": {
                "status": "done",
                "summary": "挖到 32 个圆石",
                "details": {
                    "chain_mining": {
                        "chained_batches": 4,
                        "chained_blocks": 30,
                        "single_blocks": 2,
                        "not_chained_reason": None,
                    },
                    "pits": filled_pit,
                },
            },
        }
    )
    assert run is not None
    dry = _dry_goal_summary(run)
    assert "连锁 4 批共 30 格、单挖 2 格" in dry
    assert "顺手回填留坑 1 处" in dry
    assert "-361" not in dry

    ledger = GoalLedger()
    ledger.remember_finished(run)
    [entry] = ledger.recent_finished()
    assert entry["chain_mining"] == "连锁 4 批共 30 格、单挖 2 格"
    assert "顺手回填留坑 1 处" in entry["pits"]


async def test_agent_absorbs_goal_mining_and_pits_per_world_without_polluting_brief() -> None:
    """采掘目标结束后，Agent 按存档记住连锁统计与留坑情况，结构化速览/快照/任务事实可读，换存档隔离。"""
    server = FakeMaicraft()
    server.reports_world = True
    server.world = {"id": "world-alpha", "name": "一周目"}
    server.self_view["mod_features"] = ["chain_mining", "touhou_little_maid"]

    step = 0

    def script(_messages: List[Dict[str, Any]]) -> Response:
        nonlocal step
        step += 1
        if step == 1:
            return _resp(
                calls=[
                    _call(
                        "maicraft_execute",
                        {"ability": "obtain", "purpose": "营地外采石头", "parameters": {"item": "cobblestone"}},
                    )
                ]
            )
        if step == 2:
            return _resp("")
        return _resp(calls=[_call("minecraft_report", {"kind": "delivery", "content": "石头采回来了，坑口也封平了"})])

    agent, _, _ = await make_minecraft_agent(server, script)
    agent.receive_delegation(instruction="去营地外挖点圆石", task_id="d-mine")
    await _wait_until(lambda: bool(server.goals))
    [goal_id] = list(server.goals.keys())
    server.finish(
        goal_id,
        status="done",
        summary="挖到 24 个圆石",
        details={
            "chain_mining": {
                "chained_batches": 3,
                "chained_blocks": 22,
                "single_blocks": 2,
                "not_chained_reason": None,
            },
            "pits": [
                {
                    "at": {"x": 40, "y": 63, "z": -12, "dimension": "minecraft:overworld"},
                    "depth": 3,
                    "filled": True,
                    "note": "收工前用泥土封平了坑口",
                }
            ],
        },
    )
    await _wait_until(lambda: not agent.batch_active and step >= 3)

    glance = await agent._glance()
    assert glance["mod_features"] == "连锁破坏、车万女仆"
    assert glance["last_chain_mining"] == "连锁 3 批共 22 格、单挖 2 格"
    assert "顺手回填留坑 1 处" in glance["recent_pits"]
    assert "连锁" not in glance["brief"] and "留坑" not in glance["brief"]

    ctx = agent._current_task_context()
    assert ctx["mod_features"] == "连锁破坏、车万女仆"
    assert ctx["last_chain_mining"] == "连锁 3 批共 22 格、单挖 2 格"
    assert "顺手回填留坑 1 处" in ctx["recent_pits"]

    snap = await agent._state_snapshot()
    assert "实例能力：连锁破坏、车万女仆" in snap
    assert "最近挖掘：连锁 3 批共 22 格、单挖 2 格" in snap
    assert "近期留坑：顺手回填留坑 1 处" in snap

    # 切到尚未挖过方块、也没开连锁的新存档：旧存档的连锁批次与留坑记录不串过去
    server.world = {"id": "world-beta", "name": "二周目"}
    server.self_view = {k: v for k, v in server.self_view.items() if k != "last_chain_mining"}
    server.self_view["mod_features"] = []
    glance_beta = await agent._glance()
    assert glance_beta["mod_features"] == "无额外联动"
    assert "last_chain_mining" not in glance_beta
    assert "recent_pits" not in glance_beta
    await agent.stop()


def test_world_mining_facts_unit_isolation() -> None:
    store = WorldMiningFacts()
    store.absorb_self("w1", {"mod_features": ["chain_mining"]})
    store.absorb_goal_result(
        "w1",
        {
            "details": {
                "chain_mining": {"chained_batches": 1, "chained_blocks": 8, "single_blocks": 0},
                "pits": [{"depth": 2, "filled": False, "note": "缺方块未填"}],
            }
        },
    )
    assert store.view("w1") == {
        "mod_features": "连锁破坏",
        "last_chain_mining": "连锁 1 批共 8 格",
        "recent_pits": "留坑 1 处未填平（深 2 格：缺方块未填）",
    }
    assert store.view("w2") == {}


def test_prompts_and_streamer_guide_cover_d13_camp_rules_and_d14_chain_mining() -> None:
    """提示词覆盖营地不往下挖、顺手填坑常识，以及实例能力与连锁挖矿如实答口径。"""
    reset_prompt_manager()
    try:
        prompt = get_prompt_manager().render("amaidesu_minecraft_agent")
    finally:
        reset_prompt_manager()
    assert "营地范围内" in prompt and "走出营地" in prompt
    assert "营地地面有坑顺手填平" in prompt
    assert "details.pits" in prompt
    assert "mod_features" in prompt and "last_chain_mining" in prompt and "not_chained_reason" in prompt

    assert "mod_features" in STREAMER_GUIDE
    assert "last_chain_mining" in STREAMER_GUIDE
    assert "recent_pits" in STREAMER_GUIDE
    assert "登记" not in STREAMER_GUIDE
