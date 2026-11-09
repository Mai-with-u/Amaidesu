"""换存档端到端：MaiCraft 报的存档从老家 → 主菜单 → 新的世界 → 回老家，主播的常驻简报、游戏承诺卡、
MC 的工作日志与生物名册都跟着眼下的存档走。

MC Agent 真启动，经目标跟踪的长轮询读替身的事件页认存档；主播与 MC 共用一个工具注册表，开窗前从
工具面拉存档。库是临时 SQLite（档案、承诺、名册都是真表）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from src.agents.streamer.planner_render import Delegation
from src.agents.streamer.streamer_agent import StreamerAgent
from src.modules.storage.database import SQLiteDatabase
from src.modules.storage.models.game_creatures import GameCreatureRecord
from src.modules.tools.models import ToolInvocation
from src.modules.tools.registry import ToolRegistry
from src.modules.tools.tasks import TaskLedger
from src.modules.types.game_save import GameSave
from tests.agents.streamer.helpers import ActiveSessionStub, make_streamer_agent
from tests.config.helpers import wait_until

from .minecraft_v1_fakes import FakeMaicraft, make_minecraft_agent, scene_with_maid

_SESSION_MS = 30 * 60_000


def _save_of(registry: ToolRegistry) -> Optional[GameSave]:
    return registry.current_saves(for_agent="streamer").get("minecraft")


async def _enter(server: FakeMaicraft, registry: ToolRegistry, save: GameSave) -> None:
    """Mod 认出进了某个存档（或回了主菜单），等 MC 的目标跟踪读到那一页。"""
    if save.known:
        server.enter_world(save.id, save.name)
        # 新版 Mod 的旧编号与世界编号相同：名册没有要从旧编号搬的
        server.self_view["world_key"] = save.id
    else:
        server.leave_world()
    # 替身的长轮询最多等 1.5 秒、空读后宿主再停 1 秒，正常 2.5 秒内读到；上限按满载留宽
    await wait_until(lambda: _save_of(registry) == save, what=f"MC 认出眼下的存档 {save}", timeout_s=15.0)


async def _streamer_view(streamer: StreamerAgent) -> Tuple[str, Tuple[int, ...]]:
    """主播这一窗看到的：常驻简报，与正在玩 minecraft 时推的承诺卡编号。"""
    streamer._sync_current_saves()
    await streamer._refresh_memory_brief()
    cues = await streamer._collect_memory_cues([], ["minecraft"])
    return streamer.memory_brief_text, tuple(cues.commitment_ids)


async def _work_log(registry: ToolRegistry) -> Dict[str, Any]:
    result = await registry.invoke(ToolInvocation(tool_name="minecraft_get_work_log", arguments={}, source="test"))
    return dict(result.structured_content or {})


async def test_memories_follow_the_save_through_a_round_trip(tmp_path: Path) -> None:
    db = SQLiteDatabase(tmp_path / "saves.db")
    await db.initialize()
    mc = None
    try:
        # 上一场：老家里屋顶封好了、答应修北边的桥；新的世界里找到了村子、答应去换绿宝石
        previous = await db.sessions.insert_live_session(stream_id="room", platform="bilibili", started_at_ms=1_000)
        await db.sessions.close_live_session(live_session_id=previous, ended_at_ms=1_000 + _SESSION_MS)
        for chat_id, text, quote, save_id, save_name in (
            (1, "Minecraft：屋顶封好了", "屋顶终于封好了", "home", "老家"),
            (2, "Minecraft：出生点旁边有村子", "出生点旁边就有村子", "new", "新的世界"),
        ):
            await db.archive.insert_line(
                live_session_id=previous,
                text=text,
                quote=quote,
                evidence_chat_id=chat_id,
                game="minecraft",
                save_id=save_id,
                save_name=save_name,
            )
        bridge = await db.commitments.insert_commitment(
            what="修北边的桥", quote="桥下次修完", game="minecraft", save_id="home", save_name="老家"
        )
        emerald = await db.commitments.insert_commitment(
            what="去村里换绿宝石", quote="下回去村里换绿宝石", game="minecraft", save_id="new", save_name="新的世界"
        )
        current = await db.sessions.insert_live_session(
            stream_id="room", platform="bilibili", started_at_ms=1_000 + 2 * _SESSION_MS
        )

        live = ActiveSessionStub(active_pk=current)
        server = FakeMaicraft()
        server.scene_view = scene_with_maid()
        registry = ToolRegistry()
        mc, _, _ = await make_minecraft_agent(server, registry=registry, creature_repo=db.game_creatures)
        ledger = TaskLedger()
        streamer = make_streamer_agent(
            tool_registry=registry,
            task_ledger=ledger,
            session_manager=live,
            chat_repo=db.chat,
            sessions_repo=db.sessions,
            event_repo=db.events,
            commitments_repo=db.commitments,
            archive_repo=db.archive,
        )
        home, new = GameSave(id="home", name="老家"), GameSave(id="new", name="新的世界")

        # 老家：老家的事照列、新的世界的折成一行；只推老家里答应的卡；记笔记、给女仆起名
        await _enter(server, registry, home)
        brief, cards = await _streamer_view(streamer)
        assert "眼下 minecraft 开着存档「老家」。" in brief and "屋顶终于封好了" in brief
        assert "还在 minecraft「新的世界」里做过 1 件事" in brief and "出生点旁边就有村子" not in brief
        assert cards == (bridge,)
        mc._mc_state.set_notebook("北边的桥还差三格")
        # 名册里那只叫阿缕的女仆经仓储预置（起名登记入口已退役），速览加载名册时读回
        await db.game_creatures.save_creatures(
            game="minecraft",
            world_key="home",
            creatures=[
                GameCreatureRecord(
                    creature_key="look:touhou_little_maid:maid|touhou_little_maid:winefox_momo|",
                    type="touhou_little_maid:maid",
                    skin="莫莫酒狐",
                    owner="你的",
                    nickname="阿缕",
                )
            ],
        )
        # 主播在老家交代给 MC 的一件事（手头的事里的一条），任务账本上在跑
        ledger.register(
            task_id="d1",
            provider="minecraft",
            tool="delegate",
            initiator="streamer",
            executor="minecraft",
            source="agent",
        )
        ledger.update("d1", "running")
        streamer._planner.workbench.delegations["minecraft"] = Delegation(
            agent="minecraft", instruction="把桥修完", task_id="d1", at_ms=1
        )

        # 回主菜单：认不出世界，记着存档的都当别的存档——不推卡，简报提醒别当成眼前的事；不算换存档
        await _enter(server, registry, GameSave())
        brief, cards = await _streamer_view(streamer)
        assert "minecraft 眼下还没进存档" in brief and "屋顶终于封好了" not in brief
        assert cards == ()
        assert "minecraft" in streamer._planner.workbench.delegations

        # 新的世界：新世界的事照列、老家的折起来；只推新世界的卡；MC 笔记是空的、名册里没有阿缕；
        # 老家交代的那件事账面还在跑，先留着——MC 会看眼前再决定接着做还是上报
        await _enter(server, registry, new)
        brief, cards = await _streamer_view(streamer)
        assert "眼下 minecraft 开着存档「新的世界」。" in brief and "出生点旁边就有村子" in brief
        assert "还在 minecraft「老家」里做过 1 件事" in brief
        assert cards == (emerald,)
        # 工作文档里的笔记正文后面接着一段现查的快照：新世界的笔记是空的，只剩快照
        assert (await _work_log(registry))["notebook"].startswith("【当前状态快照】")
        assert "阿缕" not in (await mc._glance())["brief"]
        assert "minecraft" in streamer._planner.workbench.delegations
        # 主播在新世界里叫停了那件事，账面结算
        ledger.update("d1", "cancelled")

        # 下一场回老家：笔记、名册、简报、卡都回到老家的（换一场是因为同一张卡十分钟内不重推）；
        # 已结算的那件委派随这次换存档清掉
        live.active_pk = await db.sessions.insert_live_session(
            stream_id="room", platform="bilibili", started_at_ms=1_000 + 4 * _SESSION_MS
        )
        await _enter(server, registry, home)
        brief, cards = await _streamer_view(streamer)
        assert "屋顶终于封好了" in brief and "还在 minecraft「新的世界」里做过 1 件事" in brief
        assert cards == (bridge,)
        assert (await _work_log(registry))["notebook"].startswith("北边的桥还差三格")
        assert "阿缕（你的女仆，莫莫酒狐皮肤）在身边" in (await mc._glance())["brief"]
        assert "minecraft" not in streamer._planner.workbench.delegations
    finally:
        if mc is not None:
            await mc.stop()
        await db.close()
