"""skill_support - 把 MCP 侧技能世界映射进 Agent 上下文的支撑件

职责边界（沿"技能世界映射"切，不沿行号切）：
- 技能目录与正文：本地直播策略技能 + MaiCraft 远端玩法技能（目录合并进
  ``SkillLibrary``，正文按 ``skill://`` 地址按需读）
- 系统提示词的技能目录段（含工具名对照说明与计量标注的数据源）
- 按连接缓存的装载资料：能力清单（lookup）与远端技能目录，每次 MCP 连接
  只读一次，连接恢复/断开时作废重读
- 新任务开局的"宿主代读"资料包：自己、周围、能力清单、手上的目标（有笔记
  时附笔记），以一组宿主代发的工具调用与回复进历史，与模型自己读取同形

依赖全部构造器注入，不持有宿主引用（与 ``GoalWatch`` 同款形态）：MCP 客户端
经取值回调注入（连接恢复会整体换实例），工具执行与回复吸收经回调注入——
开局代读与能力清单探测都是装载流程的实测调用，吸收动作让读取结果像模型
自己读过一样进目标跟踪状态。
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set

from src.agents.minecraft.maicraft import GOAL, LOOKUP, OBSERVE
from src.agents.minecraft.state import MinecraftAgentState
from src.modules.logging import ModuleLogger
from src.modules.skills import RemoteSkill, SkillEnvironment, SkillLibrary, render_catalog

__all__ = ["SkillSupport"]

# 宿主代读的开局资料：自己、周围、能力清单、手上的目标。
_OPENING_READS = (
    (OBSERVE, {"what": "self"}),
    (OBSERVE, {"what": "scene"}),
    (LOOKUP, {}),
    (GOAL, {"operation": "list"}),
)


def _skill_body(text: str) -> str:
    """去掉 SKILL.md 的 frontmatter：本地技能库读正文时也这样，模型看到的两边一致。"""
    normalized = text.replace("\r\n", "\n")
    if normalized.startswith("---\n"):
        end = normalized.find("\n---\n", 4)
        if end >= 0:
            return normalized[end + 5 :].strip()
    return normalized.strip()


class SkillSupport:
    """MaiCraft 技能世界 → Agent 上下文的映射支撑件。

    宿主消费的入口：``ensure_ability_listing`` / ``ensure_remote_skills`` /
    ``drop_remote_if_disconnected`` / ``reset_connection_cache``（任务批与
    连接恢复路径）、``append_opening_bundle``（开局代读）、
    ``catalog_section``（系统提示词）、``read_skill``（minecraft_skill
    工具的执行后端，经 ``MinecraftToolProvider.skill_reader`` 回调持有）。
    """

    def __init__(
        self,
        *,
        skills: Optional[SkillLibrary],
        agent_name: str,
        state: MinecraftAgentState,
        logger: ModuleLogger,
        maicraft_ready: Callable[[], bool],
        mcp_client: Callable[[], Optional[Any]],
        execute_tool: Callable[[str, Dict[str, Any]], Awaitable[Dict[str, Any]]],
        absorb_reply: Callable[[str, Dict[str, Any], Dict[str, Any]], None],
    ) -> None:
        """Args:
        skills: 可选技能库；``None`` 时技能目录段整段省略、读技能返回未装配
        agent_name: 宿主 Agent 注册名（技能目录按 agent 名筛）
        state: 宿主的内存状态（开局代读按有无笔记补一项）
        logger: 宿主持有的模块 logger（共享同一过滤名）
        maicraft_ready: 连接就绪判定回调（远端读取的前置条件）
        mcp_client: MCP 客户端取值回调（连接恢复会整体换实例，不能缓存）
        execute_tool: 工具执行回调（装载流程的实测调用走它）
        absorb_reply: 回复吸收回调（代读结果像模型自己读过一样进状态）
        """
        self._skills = skills
        self._agent_name = agent_name
        self._state = state
        self._logger = logger
        self._maicraft_ready = maicraft_ready
        self._mcp_client = mcp_client
        self._execute_tool = execute_tool
        self._absorb_reply = absorb_reply
        # 能力清单（lookup()）每次 MCP 连接只读一次；新任务开局直接附上。
        self._ability_listing: Optional[Dict[str, Any]] = None
        # 远端技能目录（MaiCraft 的 resources/list）每次连接只读一次；正文仍按需读。
        self._remote_skills_loaded = False

    # ==================================================================
    # 按连接缓存的装载资料
    # ==================================================================

    async def ensure_ability_listing(self) -> None:
        """连上 MaiCraft 后读一次能力清单（lookup()）：开局资料与本地策略的环境都用它，读失败下次再试。"""
        if self._ability_listing is not None or not self._maicraft_ready():
            return
        observation = await self._execute_tool(LOOKUP, {})
        if observation.get("ok") is True:
            self._ability_listing = observation

    async def ensure_remote_skills(self) -> None:
        """连上 MaiCraft 后读一次技能目录（resources/list）：玩法技能随 Mod 发布，正文仍按需读。

        只取 ``skill://`` 资源，分类取地址的第一段；名字以服务器给的为准，缺了才从地址末段取。
        读失败不标记已读，下次开批再试；读到空目录也标记已读，免得每一批都问一次。
        """
        if self._remote_skills_loaded or self._skills is None or not self._maicraft_ready():
            return
        client = self._mcp_client()
        if client is None or not hasattr(client, "list_resources"):
            return
        resources = await client.list_resources()
        entries: list[RemoteSkill] = []
        for resource in resources:
            uri = str(getattr(resource, "uri", "") or "")
            if not uri.startswith("skill://"):
                continue
            parts = [part for part in uri[len("skill://") :].split("/") if part]
            if len(parts) < 2 or parts[-1] != "SKILL.md":
                continue
            entries.append(
                RemoteSkill(
                    name=str(getattr(resource, "name", "") or parts[-2]),
                    description=str(getattr(resource, "description", "") or ""),
                    category=parts[0],
                    uri=uri,
                    agents=(self._agent_name,),
                )
            )
        self._skills.replace_remote("MaiCraft", entries)
        self._remote_skills_loaded = True
        if entries:
            self._logger.info(f"已登记 MaiCraft 的 {len(entries)} 项玩法技能")
        else:
            self._logger.info("MaiCraft 没有发布玩法技能（旧版 Mod 或没有 resources 接口）")

    def drop_remote_if_disconnected(self) -> None:
        """连接断了：目录里不能再摆着读不到的远端技能，撤掉，本地策略照常可用。"""
        if self._remote_skills_loaded and not self._maicraft_ready() and self._skills is not None:
            self._skills.clear_remote("MaiCraft")
            self._remote_skills_loaded = False

    def reset_connection_cache(self) -> None:
        """作废按连接缓存的资料：能力清单与远端技能目录下次开批重读。

        连接恢复（Mod 可能重启过，旧目录可能不适用）与 Agent 停止两个路径共用。
        """
        self._ability_listing = None
        self._remote_skills_loaded = False
        if self._skills is not None:
            self._skills.clear_remote("MaiCraft")

    def _ability_ids(self) -> Optional[Set[str]]:
        """能力清单里的能力 ID；还没读到返回 None（按未知处理，不当成"一个能力都没有"）。"""
        listing = self._ability_listing
        data = listing.get("data") if isinstance(listing, dict) else None
        abilities = data.get("abilities") if isinstance(data, dict) else None
        if not isinstance(abilities, list):
            return None
        return {str(entry["ability"]) for entry in abilities if isinstance(entry, dict) and entry.get("ability")}

    # ==================================================================
    # 技能目录与正文
    # ==================================================================

    def _skill_environment(self) -> SkillEnvironment:
        """本地技能的环境事实：接的是 v1 的 Mod；读到能力清单后才认定哪些能力有、哪些没有。

        远端玩法技能由 MaiCraft 按它登记的模组自己筛，宿主不再按能力筛它们；
        这里的 ``maicraft`` 版本与能力集合只服务本地直播策略。
        """
        environment: Dict[str, Set[str]] = {"maicraft": {"v1"}}
        abilities = self._ability_ids()
        if abilities is not None:
            environment["abilities"] = abilities
        return environment

    def catalog_section(self) -> str:
        """系统提示词里的技能目录段；没有技能库或没有可用技能时整段省略。"""
        if self._skills is None:
            return ""
        catalog = render_catalog(self._skills.catalog(self._agent_name, self._skill_environment()))
        if not catalog:
            return ""
        return (
            "\n\n## 技能\n"
            "技能是把一类目标做成的打法与经验：步骤、决策点、常见坑和该查的资料。"
            "开始一类不熟悉或容易出错的工作前，用 minecraft_skill 按名读取相关技能；"
            "技能是参考，不授予额外权限，现场和能力说明优先。"
            "标注“前提待确认”的技能只在现场确实具备该前提时使用。"
            "标着“来自 MaiCraft”的是随 Mod 发布的玩法技能，正文里的 lookup、execute、goal、events、observe "
            "对应本机的 maicraft_lookup、maicraft_execute、maicraft_goal、maicraft_events、maicraft_observe；"
            "MaiCraft 没连上时这些技能读不到，本地技能照常可用。\n" + catalog
        )

    async def read_skill(self, name: str) -> Dict[str, Any]:
        """minecraft_skill：本地技能直接读；远端玩法技能按地址去 MaiCraft 取正文。"""
        if self._skills is None:
            return {"success": False, "error": "技能库未装配"}
        remote = self._skills.remote(name, self._agent_name)
        if remote is not None:
            text = await self._read_remote_skill(remote.uri)
            if text is None:
                return {
                    "success": False,
                    "error": f"读取技能 {name!r} 失败：MaiCraft 没有返回正文",
                    "available": [
                        entry.name for entry in self._skills.catalog(self._agent_name, self._skill_environment())
                    ],
                }
            return {
                "success": True,
                "name": remote.name,
                "category": remote.category,
                "content": _skill_body(text),
                "source": remote.origin,
            }
        return self._skills.read(name, self._agent_name, self._skill_environment())

    async def _read_remote_skill(self, uri: str) -> Optional[str]:
        """按 skill:// 地址读一项技能的 SKILL.md；没连上、读失败、没有正文都返回 None。"""
        client = self._mcp_client()
        if client is None or not self._maicraft_ready():
            return None
        result = await client.read_resource(uri)
        if result is None:
            return None
        for item in getattr(result, "contents", None) or []:
            text = getattr(item, "text", None)
            if text:
                return str(text)
        return None

    # ==================================================================
    # 开局代读
    # ==================================================================

    async def append_opening_bundle(self, messages: List[Dict[str, Any]]) -> None:
        """新任务开局由宿主代读：自己、周围、能力清单、手上的目标，有笔记时附上笔记。

        以一组宿主代发的工具调用和回复放进历史，与模型自己读取时一样；读取失败的那一项照实留下错误。
        能力清单每次连接只读一次。
        """
        reads: List[tuple[str, Dict[str, Any]]] = list(_OPENING_READS) if self._maicraft_ready() else []
        if self._state.notebook:
            reads.append(("minecraft_notebook", {"action": "read"}))
        if not reads:
            return
        batch = uuid.uuid4().hex[:8]
        calls: List[Dict[str, Any]] = []
        results: List[Dict[str, Any]] = []
        for index, (name, arguments) in enumerate(reads):
            if name == LOOKUP and self._ability_listing is not None:
                observation = self._ability_listing
            else:
                observation = await self._execute_tool(name, arguments)
                if name == LOOKUP and observation.get("ok") is True:
                    self._ability_listing = observation
            self._absorb_reply(name, arguments, observation)
            call_id = f"opening-{batch}-{index}"
            calls.append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
                }
            )
            results.append(
                {"role": "tool", "tool_call_id": call_id, "content": json.dumps(observation, ensure_ascii=False)}
            )
        messages.append(
            {
                "role": "assistant",
                "content": "[开局资料] 宿主已代为读取自己、周围、能力清单和手上的目标；仍有效时直接使用，不重复读取。",
                "tool_calls": calls,
            }
        )
        messages.extend(results)
