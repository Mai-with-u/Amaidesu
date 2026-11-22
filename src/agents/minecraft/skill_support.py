"""skill_support - 把 MCP 侧技能世界与 Agent 自建技能卡映射进上下文的支撑件

职责边界（沿"技能世界映射"切，不沿行号切）：
- 技能目录与正文：本地直播策略技能 + MaiCraft 远端玩法技能（目录合并进
  ``SkillLibrary``，正文按 ``skill://`` 地址按需读）+ Agent 自建可读写/可修订
  技能卡（分 ``world`` 本服设施流程与 ``global`` 通用模组玩法，持久化于存储层）
- 系统提示词的紧凑技能目录段（只列名称、来源/scope 与一句话触发条件，按需读正文）
- 短窗防重读：记录本会话近 10 轮刚读过的技能名，上下文未被折叠时避免重复灌入全文
- 按连接缓存的装载资料：能力清单（lookup）与远端技能目录，每次 MCP 连接
  只读一次，连接恢复/断开时作废重读
- 新任务开局的"宿主代读"资料包：自己、周围、能力清单、手上的目标（有笔记
  时附笔记），以一条紧跟任务指令的宿主消息进历史，写明不是模型发起的调用

依赖全部构造器注入，不持有宿主引用（与 ``GoalWatch`` 同款形态）：MCP 客户端
经取值回调注入（连接恢复会整体换实例），工具执行与回复吸收经回调注入——
开局代读与能力清单探测都是装载流程的实测调用，吸收动作让读取结果像模型
自己读过一样进目标跟踪状态。
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple

from src.agents.minecraft.context import opening_message
from src.agents.minecraft.maicraft import GOAL, LOOKUP, OBSERVE
from src.agents.minecraft.state import LAST_SESSION_NOTEBOOK_MAX_CHARS, MinecraftAgentState
from src.modules.logging import ModuleLogger
from src.modules.skills import RemoteSkill, SkillEnvironment, SkillLibrary, render_catalog
from src.modules.storage.models.learned_skills import LearnedSkillRecord, LearnedSkillScope
from src.modules.utils.time_utils import now_ms

__all__ = [
    "DEFAULT_REREAD_GUARD_WINDOW_STEPS",
    "LEARNED_SKILL_MAX_CONTENT_CHARS",
    "LEARNED_SKILL_MAX_NAME_CHARS",
    "LEARNED_SKILL_MAX_PER_SCOPE",
    "LEARNED_SKILL_MAX_TRIGGER_CHARS",
    "SkillSupport",
]

# 宿主代读的开局资料：自己、周围、能力清单、手上的目标。
_OPENING_READS = (
    (OBSERVE, {"what": "self"}),
    (OBSERVE, {"what": "scene"}),
    (LOOKUP, {}),
    (GOAL, {"operation": "list"}),
)

# 自建技能卡的体积与数量护栏：单篇上限 1200 字、单域（world/global）上限 20 条
LEARNED_SKILL_MAX_NAME_CHARS = 48
LEARNED_SKILL_MAX_TRIGGER_CHARS = 120
LEARNED_SKILL_MAX_CONTENT_CHARS = 1_200
LEARNED_SKILL_MAX_PER_SCOPE = 20
# 短窗防重读：近 10 轮内刚读过且上下文未折叠时，不再重复灌入数千字全文
DEFAULT_REREAD_GUARD_WINDOW_STEPS = 10

_VALID_SCOPES: Tuple[LearnedSkillScope, ...] = ("world", "global")
_SCOPE_LABELS: Dict[str, str] = {"world": "本服", "global": "通用"}


def _skill_body(text: str) -> str:
    """去掉 SKILL.md 的 frontmatter：本地技能库读正文时也这样，模型看到的两边一致。"""
    normalized = text.replace("\r\n", "\n")
    if normalized.startswith("---\n"):
        end = normalized.find("\n---\n", 4)
        if end >= 0:
            return normalized[end + 5 :].strip()
    return normalized.strip()


def _validate_skill_name(raw_name: Any) -> Tuple[str, str]:
    """校验自建技能名：去空白、限长、禁换行与 URI 前缀；返回 (合法名, 错误说明)。"""
    name = str(raw_name or "").strip()
    if not name:
        return "", "必须提供非空的技能名 name"
    if len(name) > LEARNED_SKILL_MAX_NAME_CHARS:
        return "", f"技能名超过 {LEARNED_SKILL_MAX_NAME_CHARS} 字上限，请使用更简短的名称"
    if any(ch.isspace() for ch in name) or name.startswith("skill://") or "/" in name or "\\" in name:
        return "", "技能名不能包含空白、换行或路径分隔符（请用简短词组或下划线/连字符命名）"
    return name, ""


def _parse_scope(
    raw_scope: Any, *, default: Optional[LearnedSkillScope] = None
) -> Tuple[Optional[LearnedSkillScope], str]:
    """解析自建技能作用域（world / global）；缺省时返回 default，非法值返回错误说明。"""
    text = str(raw_scope or "").strip().lower()
    if not text:
        return default, ""
    if text == "world":
        return "world", ""
    if text == "global":
        return "global", ""
    return None, f"scope 必须是 'world'（本服设施流程）或 'global'（通用模组玩法），收到 {raw_scope!r}"


class SkillSupport:
    """MaiCraft 技能世界与自建技能卡 → Agent 上下文的映射支撑件。

    宿主消费的入口：``ensure_ability_listing`` / ``ensure_remote_skills`` /
    ``refresh_learned_skills`` / ``drop_remote_if_disconnected`` /
    ``reset_connection_cache``（任务批与连接恢复路径）、``append_opening_bundle``（开局代读）、
    ``catalog_section``（系统提示词）、``read_skill``（只读预览）与
    ``handle_skill_tool``（``minecraft_skill`` 读写修订与短窗防重读后端）。
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
        learned_skill_repo: Optional[Any] = None,
        game_name: str = "minecraft",
        save_id_reader: Optional[Callable[[], str]] = None,
        reread_guard_steps: int = DEFAULT_REREAD_GUARD_WINDOW_STEPS,
        clock_ms: Optional[Callable[[], int]] = None,
    ) -> None:
        """Args:
        skills: 可选技能库；``None`` 时内置与远端技能省略（自建技能仍可独立运作）
        agent_name: 宿主 Agent 注册名（技能目录按 agent 名筛）
        state: 宿主的内存状态（开局代读按有无笔记补一项）
        logger: 宿主持有的模块 logger（共享同一过滤名）
        maicraft_ready: 连接就绪判定回调（远端读取的前置条件）
        mcp_client: MCP 客户端取值回调（连接恢复会整体换实例，不能缓存）
        execute_tool: 工具执行回调（装载流程的实测调用走它）
        absorb_reply: 回复吸收回调（代读结果像模型自己读过一样进状态）
        learned_skill_repo: 可选自建技能仓储（``LearnedSkillRepo``）；None 时退化为纯内存存储
        game_name: 持久化自建技能时写入的游戏标识
        save_id_reader: 读取当前存档唯一编号的回调（用于 world 作用域隔离）
        reread_guard_steps: 短窗防重读步数上限（默认 10 轮）
        clock_ms: 可选毫秒时钟（用于记录自建技能更新时间）
        """
        self._skills = skills
        self._agent_name = agent_name
        self._state = state
        self._logger = logger
        self._maicraft_ready = maicraft_ready
        self._mcp_client = mcp_client
        self._execute_tool = execute_tool
        self._absorb_reply = absorb_reply
        self._learned_repo = learned_skill_repo
        self._game_name = (game_name or agent_name).strip() or "minecraft"
        self._save_id_reader = save_id_reader
        self._reread_guard_steps = max(0, int(reread_guard_steps))
        self._clock_ms = clock_ms if clock_ms is not None else now_ms

        # 能力清单（lookup()）每次 MCP 连接只读一次；新任务开局直接附上。
        self._ability_listing: Optional[Dict[str, Any]] = None
        # 远端技能目录（MaiCraft 的 resources/list）每次连接只读一次；正文仍按需读。
        self._remote_skills_loaded = False

        # 自建技能内存缓存：(scope, save_id, name) -> LearnedSkillRecord
        # 有仓储时随 refresh_learned_skills 与读写操作保持同步；无仓储时直接作纯内存持久化
        self._learned_cache: Dict[Tuple[str, str, str], LearnedSkillRecord] = {}
        self._learned_loaded_save_id: Optional[str] = None

        # 短窗防重读：skill_name -> (最近成功读取时的步号, 当时的折叠纪元)
        self._recent_reads: Dict[str, Tuple[int, int]] = {}
        self._fold_epoch: int = 0

    # ==================================================================
    # 短窗防重读控制（由宿主在折叠、收起、集中整理或新任务时通知）
    # ==================================================================

    def note_context_folded(self) -> None:
        """历史发生折叠、收起或集中整理时递增纪元，允许模型按需重新读取已被折走的技能全文。"""
        self._fold_epoch += 1

    def reset_read_window(self) -> None:
        """新任务开局或重开对话时清空短窗防重读记录，保证新上下文里首次读取总能拿到全文。"""
        self._recent_reads.clear()
        self._fold_epoch = 0

    # ==================================================================
    # 按连接缓存的装载资料与自建技能同步
    # ==================================================================

    def _current_save_id(self) -> str:
        """读取当前已识别的存档编号（未注入回调或未认出存档时为空串）。"""
        if self._save_id_reader is None:
            return ""
        return (self._save_id_reader() or "").strip()

    async def refresh_learned_skills(self, save_id: Optional[str] = None) -> None:
        """从仓储加载当前存档可见的自建技能（global + 当前 save_id 的 world）进内存缓存。"""
        if self._learned_repo is None:
            return
        current_save = self._current_save_id() if save_id is None else (save_id or "").strip()
        try:
            records = await self._learned_repo.list_skills(game=self._game_name, save_id=current_save)
        except Exception as exc:  # noqa: BLE001 - 读库失败不阻断游戏循环，沿用现有缓存
            self._logger.warning(f"读取自建技能目录失败（save_id={current_save or '无'}）", exc=exc)
            return
        self._learned_cache = {(rec.scope, rec.save_id, rec.name): rec for rec in records}
        if self._learned_loaded_save_id is not None and self._learned_loaded_save_id != current_save:
            # 切换存档后清空旧存档同名技能的短窗防重读记录，避免新档同名 world 技能被旧档缓存误拦
            self._recent_reads.clear()
        self._learned_loaded_save_id = current_save

    async def _ensure_learned_loaded(self) -> None:
        """若装配了仓储且尚未加载或存档已切换，先同步一次最新自建技能列表。"""
        if self._learned_repo is None:
            return
        current_save = self._current_save_id()
        if self._learned_loaded_save_id != current_save:
            await self.refresh_learned_skills(current_save)

    def _visible_learned_skills(self, *, scope: Optional[LearnedSkillScope] = None) -> List[LearnedSkillRecord]:
        """返回当前存档下可见的自建技能列表（world 限当前 save_id，global 跨存档可见）。"""
        current_save = self._current_save_id()
        items: List[LearnedSkillRecord] = []
        for rec in self._learned_cache.values():
            if scope is not None and rec.scope != scope:
                continue
            if rec.scope == "global" and rec.save_id == "":
                items.append(rec)
            elif rec.scope == "world" and current_save and rec.save_id == current_save:
                items.append(rec)
        # 固定排序：本服（world）在前，通用（global）在后，同域按名排序，保证系统提示词前缀稳定
        items.sort(key=lambda r: (0 if r.scope == "world" else 1, r.name))
        return items

    def _find_learned_skill(
        self,
        name: str,
        *,
        scope: Optional[LearnedSkillScope] = None,
    ) -> Optional[LearnedSkillRecord]:
        """在当前可见缓存中查找自建技能；未指定 scope 时优先命中本服 world，再回退 global。"""
        clean_name = name.strip()
        current_save = self._current_save_id()
        if scope == "world":
            return self._learned_cache.get(("world", current_save, clean_name)) if current_save else None
        if scope == "global":
            return self._learned_cache.get(("global", "", clean_name))
        if current_save:
            world_hit = self._learned_cache.get(("world", current_save, clean_name))
            if world_hit is not None:
                return world_hit
        return self._learned_cache.get(("global", "", clean_name))

    def _is_readonly_builtin_or_remote(self, name: str) -> bool:
        """判断技能名是否属于只读内置技能或 MaiCraft 远端 skill:// 技能（严禁覆盖或删除）。"""
        if self._skills is None:
            return False
        clean_name = name.strip()
        if self._skills.get(clean_name) is not None:
            return True
        if self._skills.remote(clean_name, self._agent_name) is not None:
            return True
        return False

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

    @property
    def remote_skills_loaded(self) -> bool:
        """本轮连接是否已读过 MaiCraft 的技能目录（含读到空目录的情况）。"""
        return self._remote_skills_loaded

    def _ability_ids(self) -> Optional[Set[str]]:
        """能力清单里的能力 ID；还没读到返回 None（按未知处理，不当成"一个能力都没有"）。"""
        listing = self._ability_listing
        data = listing.get("data") if isinstance(listing, dict) else None
        abilities = data.get("abilities") if isinstance(data, dict) else None
        if not isinstance(abilities, list):
            return None
        return {str(entry["ability"]) for entry in abilities if isinstance(entry, dict) and entry.get("ability")}

    # ==================================================================
    # 技能目录与正文（内置 + 远端 + 自建）
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

    def _render_learned_catalog(self) -> str:
        """将当前可见的自建技能渲染为紧凑目录（仅含名称、scope 与一句话触发条件，不含正文）。"""
        learned = self._visible_learned_skills()
        if not learned:
            return ""
        lines: List[str] = ["- 自建技能（可用 minecraft_skill 读取、修订或删除）"]
        for rec in learned:
            scope_label = _SCOPE_LABELS.get(rec.scope, rec.scope)
            trigger = rec.trigger or "按需读取操作步骤"
            lines.append(f"  - `{rec.name}` [{scope_label}]：{trigger}")
        return "\n".join(lines)

    def catalog_section(self) -> str:
        """系统提示词里的紧凑技能目录段；内置、远端与自建技能全部为空时整段省略。"""
        base_catalog = (
            render_catalog(self._skills.catalog(self._agent_name, self._skill_environment()))
            if self._skills is not None
            else ""
        )
        learned_catalog = self._render_learned_catalog()
        combined = "\n".join(part for part in (base_catalog, learned_catalog) if part)
        if not combined:
            return ""
        return (
            "\n\n## 技能\n"
            "技能是把一类目标做成的打法与经验：步骤、决策点、常见坑和该查的资料。"
            "开始一类不熟悉或容易出错的工作前，用 minecraft_skill 按名读取相关技能；"
            "学到确定可复用的本服设施流程（scope=world）或通用模组玩法（scope=global）时，"
            "也可用 minecraft_skill 的 upsert/patch 沉淀或修订自建技能卡。"
            "技能是参考，不授予额外权限，现场和能力说明优先。"
            "标注“前提待确认”的技能只在现场确实具备该前提时使用。"
            "标着“来自 MaiCraft”的是随 Mod 发布的玩法技能，正文里的 lookup、execute、goal、events、observe "
            "对应本机的 maicraft_lookup、maicraft_execute、maicraft_goal、maicraft_events、maicraft_observe；"
            "MaiCraft 没连上时这些技能读不到，本地与自建技能照常可用。\n" + combined
        )

    def _available_skill_names(self) -> List[str]:
        """汇总当前可读的全部技能名（内置 + 远端 + 当前可见自建技能）。"""
        names: List[str] = []
        if self._skills is not None:
            names.extend(entry.name for entry in self._skills.catalog(self._agent_name, self._skill_environment()))
        seen = set(names)
        for rec in self._visible_learned_skills():
            if rec.name not in seen:
                seen.add(rec.name)
                names.append(rec.name)
        return names

    async def read_skill(self, name: str) -> Dict[str, Any]:
        """无副作用按名读取技能正文（供检视页预览与旧调用方复用）：自建 → 远端 → 本地内置。"""
        await self._ensure_learned_loaded()
        clean_name = (name or "").strip()
        learned = self._find_learned_skill(clean_name)
        if learned is not None:
            return {
                "success": True,
                "name": learned.name,
                "scope": learned.scope,
                "trigger": learned.trigger,
                "content": learned.content,
                "source": "learned",
            }
        if self._skills is None:
            return {
                "success": False,
                "error": f"没有名为 {name!r} 的技能" if clean_name else "技能库未装配",
                "available": self._available_skill_names(),
            }
        remote = self._skills.remote(clean_name, self._agent_name)
        if remote is not None:
            text, problem = await self._read_remote_skill(remote.uri)
            if text is None:
                # 模型点名读一项随 Mod 发布的玩法技能却拿不到正文时，先把原因记进日志供复盘，
                # 再照实告诉模型是哪种读不到，免得它把"没连上"当成"技能不存在"转去猜资料页
                self._logger.warning(f"读取 MaiCraft 技能 {name!r}（{remote.uri}）失败：{problem}")
                return {
                    "success": False,
                    "error": f"读取技能 {name!r} 失败：{problem}",
                    "available": self._available_skill_names(),
                }
            return {
                "success": True,
                "name": remote.name,
                "category": remote.category,
                "content": _skill_body(text),
                "source": remote.origin,
            }
        result = self._skills.read(clean_name, self._agent_name, self._skill_environment())
        if not result.get("success"):
            result["available"] = self._available_skill_names()
        return result

    async def handle_skill_tool(
        self,
        args: Dict[str, Any],
        *,
        current_step: Optional[int] = None,
    ) -> Dict[str, Any]:
        """``minecraft_skill`` 工具统一调度入口：支持 read / upsert / patch / remove / list 与短窗防重读。

        向后兼容：未传 ``action`` 时默认按 ``read`` 执行。
        """
        await self._ensure_learned_loaded()
        action = str(args.get("action") or "read").strip().lower()
        if action == "list":
            return self._handle_list(args)
        if action == "read":
            return await self._handle_read(args, current_step=current_step)
        if action == "upsert":
            return await self._handle_upsert(args)
        if action == "patch":
            return await self._handle_patch(args)
        if action == "remove":
            return await self._handle_remove(args)
        return {
            "success": False,
            "error": f"未知 action: {action!r}（仅支持 read / upsert / patch / remove / list）",
        }

    def _handle_list(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """列出当前可读技能紧凑目录（可按 scope 过滤自建技能）。"""
        raw_scope = str(args.get("scope") or "").strip().lower()
        if raw_scope in ("world", "global"):
            learned = self._visible_learned_skills(scope=raw_scope)  # type: ignore[arg-type]
            return {
                "success": True,
                "action": "list",
                "scope": raw_scope,
                "skills": [
                    {
                        "name": rec.name,
                        "scope": rec.scope,
                        "source": "learned",
                        "trigger": rec.trigger,
                        "chars": len(rec.content),
                    }
                    for rec in learned
                ],
                "count": len(learned),
                "limit": LEARNED_SKILL_MAX_PER_SCOPE,
            }
        if raw_scope and raw_scope != "all":
            return {
                "success": False,
                "error": f"不支持的 scope 过滤值: {raw_scope!r}（可用 world / global，留空列出全部）",
            }

        items: List[Dict[str, Any]] = []
        if self._skills is not None:
            for entry in self._skills.catalog(self._agent_name, self._skill_environment()):
                items.append(
                    {
                        "name": entry.name,
                        "category": entry.category,
                        "source": entry.origin,
                        "trigger": entry.description,
                        "readonly": True,
                    }
                )
        for rec in self._visible_learned_skills():
            items.append(
                {
                    "name": rec.name,
                    "scope": rec.scope,
                    "source": "learned",
                    "trigger": rec.trigger,
                    "chars": len(rec.content),
                    "readonly": False,
                }
            )
        return {"success": True, "action": "list", "skills": items, "count": len(items)}

    async def _handle_read(self, args: Dict[str, Any], *, current_step: Optional[int]) -> Dict[str, Any]:
        """按名读取技能正文，启用近 10 轮短窗防重读以避免上下文未折叠时反复灌入长正文。"""
        name = str(args.get("name") or "").strip()
        if not name:
            return {"success": False, "error": "read 必须提供技能名 name", "available": self._available_skill_names()}

        # 短窗防重读：近 N 轮内读过同一篇且期间未发生历史折叠/收起，直接提示复用上下文中的步骤
        if current_step is not None and self._reread_guard_steps > 0:
            record = self._recent_reads.get(name)
            if record is not None:
                last_step, last_epoch = record
                elapsed = current_step - last_step
                if last_epoch == self._fold_epoch and 0 <= elapsed <= self._reread_guard_steps:
                    return {
                        "success": True,
                        "name": name,
                        "cached_in_context": True,
                        "note": (
                            f"技能 {name!r} 最近刚读过（约 {elapsed} 步前），正文仍在当前上下文中；"
                            "请直接复用上下文中的步骤，或用 minecraft_context_recall 回查细节，无需重复读取全文。"
                        ),
                    }

        result = await self.read_skill(name)
        if result.get("success") is True and current_step is not None:
            self._recent_reads[name] = (current_step, self._fold_epoch)
        return result

    async def _handle_upsert(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """新建或整体覆盖一篇自建技能卡（禁止覆盖只读内置或 Mod 远端技能）。"""
        name, name_err = _validate_skill_name(args.get("name"))
        if name_err:
            return {"success": False, "error": name_err}
        if self._is_readonly_builtin_or_remote(name):
            return {
                "success": False,
                "error": f"技能 {name!r} 是只读的内置或 Mod 远端技能，不能覆盖；请换一个新名称保存自建技能",
            }
        scope, scope_err = _parse_scope(args.get("scope"), default="world")
        if scope_err or scope is None:
            return {"success": False, "error": scope_err}
        save_id = "" if scope == "global" else self._current_save_id()
        if scope == "world" and not save_id:
            return {
                "success": False,
                "error": "当前尚未识别所在存档，无法保存 world（本服）作用域技能；请先 observe(self) 认出存档，或改用 scope='global'",
            }

        trigger = " ".join(str(args.get("trigger") or "").split())
        if not trigger:
            return {"success": False, "error": "upsert 必须提供 trigger（一句话说明何时使用该技能）"}
        if len(trigger) > LEARNED_SKILL_MAX_TRIGGER_CHARS:
            return {
                "success": False,
                "error": f"trigger 超过 {LEARNED_SKILL_MAX_TRIGGER_CHARS} 字上限（当前 {len(trigger)} 字），请精简为一句话",
            }

        content = str(args.get("content") or "").strip()
        if not content:
            return {"success": False, "error": "upsert 必须提供非空的操作步骤 content"}
        if len(content) > LEARNED_SKILL_MAX_CONTENT_CHARS:
            return {
                "success": False,
                "error": (
                    f"自建技能正文超过 {LEARNED_SKILL_MAX_CONTENT_CHARS} 字上限（当前 {len(content)} 字）；"
                    "请精简为可执行的要点步骤后再保存"
                ),
            }

        key = (scope, save_id, name)
        exists = key in self._learned_cache
        if not exists:
            current_count = len(self._visible_learned_skills(scope=scope))
            if current_count >= LEARNED_SKILL_MAX_PER_SCOPE:
                return {
                    "success": False,
                    "error": (
                        f"{scope} 作用域的自建技能已达 {LEARNED_SKILL_MAX_PER_SCOPE} 条上限；"
                        "请先用 remove 删除失效技能，或用 patch 修订已有技能"
                    ),
                }

        ts = int(self._clock_ms())
        if self._learned_repo is not None:
            record = await self._learned_repo.upsert_skill(
                game=self._game_name,
                scope=scope,
                save_id=save_id,
                name=name,
                trigger=trigger,
                content=content,
                updated_at_ms=ts,
            )
        else:
            record = LearnedSkillRecord(
                id=len(self._learned_cache) + 1,
                game=self._game_name,
                save_id=save_id,
                scope=scope,
                name=name,
                trigger=trigger,
                content=content,
                updated_at_ms=ts,
            )
        self._learned_cache[key] = record
        # 内容已更新：清除该技能的短窗防重读记录，允许按需读取新版本
        self._recent_reads.pop(name, None)
        return {
            "success": True,
            "action": "upsert",
            "created": not exists,
            "name": record.name,
            "scope": record.scope,
            "trigger": record.trigger,
            "chars": len(record.content),
        }

    async def _handle_patch(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """修订已有自建技能卡：支持更新 trigger、替换全文 content、子串替换（old_text/new_text）或追加（append）。"""
        name, name_err = _validate_skill_name(args.get("name"))
        if name_err:
            return {"success": False, "error": name_err}
        if self._is_readonly_builtin_or_remote(name):
            return {
                "success": False,
                "error": f"技能 {name!r} 是只读的内置或 Mod 远端技能，不能修订；请改用 upsert 以新名称保存自建技能",
            }
        scope, scope_err = _parse_scope(args.get("scope"), default=None)
        if scope_err:
            return {"success": False, "error": scope_err}
        existing = self._find_learned_skill(name, scope=scope)
        if existing is None:
            return {
                "success": False,
                "error": f"未找到可修订的自建技能 {name!r}；如需新建请使用 action='upsert'",
            }

        new_trigger = existing.trigger
        if "trigger" in args and args.get("trigger") is not None:
            cleaned_trigger = " ".join(str(args.get("trigger") or "").split())
            if not cleaned_trigger:
                return {"success": False, "error": "修订后的 trigger 不能为空"}
            if len(cleaned_trigger) > LEARNED_SKILL_MAX_TRIGGER_CHARS:
                return {
                    "success": False,
                    "error": f"trigger 超过 {LEARNED_SKILL_MAX_TRIGGER_CHARS} 字上限（当前 {len(cleaned_trigger)} 字）",
                }
            new_trigger = cleaned_trigger

        new_content = existing.content
        content_touched = False
        if "content" in args and args.get("content") is not None:
            new_content = str(args.get("content") or "").strip()
            content_touched = True
        elif "old_text" in args and args.get("old_text") is not None:
            old_text = str(args.get("old_text") or "")
            replacement = str(args.get("new_text") or "")
            if not old_text or old_text not in new_content:
                return {"success": False, "error": "patch 指定的 old_text 未在原技能正文中找到，请先 read 核对原文"}
            new_content = new_content.replace(old_text, replacement, 1).strip()
            content_touched = True
        elif "append" in args and args.get("append") is not None:
            addition = str(args.get("append") or "").strip()
            if not addition:
                return {"success": False, "error": "patch 的 append 内容不能为空"}
            new_content = f"{new_content}\n{addition}".strip()
            content_touched = True

        if not content_touched and new_trigger == existing.trigger:
            return {
                "success": False,
                "error": "patch 至少需要提供 trigger、content、old_text+new_text 或 append 中的一项修改内容",
            }
        if not new_content:
            return {"success": False, "error": "修订后的技能正文不能为空"}
        if len(new_content) > LEARNED_SKILL_MAX_CONTENT_CHARS:
            return {
                "success": False,
                "error": (
                    f"修订后的技能正文超过 {LEARNED_SKILL_MAX_CONTENT_CHARS} 字上限（当前 {len(new_content)} 字）；"
                    "请精简旧步骤后再提交"
                ),
            }

        ts = int(self._clock_ms())
        if self._learned_repo is not None:
            updated = await self._learned_repo.upsert_skill(
                game=self._game_name,
                scope=existing.scope,
                save_id=existing.save_id,
                name=existing.name,
                trigger=new_trigger,
                content=new_content,
                updated_at_ms=ts,
            )
        else:
            updated = existing.model_copy(update={"trigger": new_trigger, "content": new_content, "updated_at_ms": ts})
        self._learned_cache[(updated.scope, updated.save_id, updated.name)] = updated
        self._recent_reads.pop(updated.name, None)
        return {
            "success": True,
            "action": "patch",
            "name": updated.name,
            "scope": updated.scope,
            "trigger": updated.trigger,
            "chars": len(updated.content),
        }

    async def _handle_remove(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """删除已有自建技能卡（严禁删除只读内置或 Mod 远端技能）。"""
        name, name_err = _validate_skill_name(args.get("name"))
        if name_err:
            return {"success": False, "error": name_err}
        if self._is_readonly_builtin_or_remote(name):
            return {
                "success": False,
                "error": f"技能 {name!r} 是只读的内置或 Mod 远端技能，不允许删除",
            }
        scope, scope_err = _parse_scope(args.get("scope"), default=None)
        if scope_err:
            return {"success": False, "error": scope_err}
        existing = self._find_learned_skill(name, scope=scope)
        if existing is None:
            return {"success": False, "error": f"未找到可删除的自建技能 {name!r}"}

        if self._learned_repo is not None:
            await self._learned_repo.delete_skill(
                game=self._game_name,
                name=existing.name,
                save_id=existing.save_id,
                scope=existing.scope,
            )
        self._learned_cache.pop((existing.scope, existing.save_id, existing.name), None)
        self._recent_reads.pop(existing.name, None)
        return {
            "success": True,
            "action": "remove",
            "removed": existing.name,
            "scope": existing.scope,
        }

    async def _read_remote_skill(self, uri: str) -> tuple[Optional[str], str]:
        """按 skill:// 地址读一项技能的 SKILL.md，返回 (正文, 读不到的原因)。

        MCP 客户端的 ``read_resource`` 回的是内容项列表（文本项带 ``text``，二进制项带 ``blob``），
        不是带 ``contents`` 的整包结果；取第一项有文本的内容当正文。读到正文时原因为空串，
        没连上、读取出错、空内容、只有非文本内容各给一句原因。
        """
        client = self._mcp_client()
        if client is None or not self._maicraft_ready():
            return None, "MaiCraft 没连上"
        contents = await client.read_resource(uri)
        if contents is None:
            # 通道层已把未连接、超时或服务端报错的具体原因记成 McpClient 的 warning，这里只说结论
            return None, "MaiCraft 读取出错（连接断开或服务端报错）"
        items = list(contents)
        for item in items:
            text = getattr(item, "text", None)
            if text:
                return str(text), ""
        if not items:
            return None, "MaiCraft 返回的内容是空的"
        shapes = "、".join(f"{type(item).__name__}（mimeType={getattr(item, 'mimeType', None)}）" for item in items)
        return None, f"MaiCraft 返回的 {len(items)} 项内容里没有文本正文：{shapes}"

    # ==================================================================
    # 开局代读
    # ==================================================================

    async def append_opening_bundle(self, messages: List[Dict[str, Any]]) -> None:
        """新任务开局由宿主代读：自己、周围、能力清单、手上的目标，有笔记时附上笔记。

        先执行现场四项代读（其中 ``observe(self)`` 会认出当前存档并把库里持久化的笔记加载到位），
        再按笔记来源分路：若是上一场留在这个存档里的笔记，限长截断并标明"记于上一场"供核对；
        若是本场刚写过的笔记，照旧经 ``minecraft_notebook(read)`` 附上。
        """
        reads: List[tuple[str, Dict[str, Any]]] = list(_OPENING_READS) if self._maicraft_ready() else []
        opened: List[tuple[str, Dict[str, Any], Dict[str, Any]]] = []
        for name, arguments in reads:
            if name == LOOKUP and self._ability_listing is not None:
                observation = self._ability_listing
            else:
                observation = await self._execute_tool(name, arguments)
                if name == LOOKUP and observation.get("ok") is True:
                    self._ability_listing = observation
            self._absorb_reply(name, arguments, observation)
            opened.append((name, arguments, observation))

        last_session_part: Optional[str] = None
        if self._state.notebook:
            if self._state.notebook_from_last_session:
                raw_note = self._state.notebook
                if len(raw_note) > LAST_SESSION_NOTEBOOK_MAX_CHARS:
                    clipped = (
                        raw_note[:LAST_SESSION_NOTEBOOK_MAX_CHARS] + "…（已截断，完整内容可用 minecraft_notebook 读取）"
                    )
                else:
                    clipped = raw_note
                last_session_part = (
                    "\n\n### 上次在这个存档里的笔记（记于上一场，仅供核对；与眼前现场不符的以现场观察为准）\n" + clipped
                )
            else:
                nb_args = {"action": "read"}
                observation = await self._execute_tool("minecraft_notebook", nb_args)
                self._absorb_reply("minecraft_notebook", nb_args, observation)
                opened.append(("minecraft_notebook", nb_args, observation))

        if not opened and last_session_part is None:
            return
        msg = opening_message(opened)
        if last_session_part is not None:
            msg["content"].append({"type": "text", "text": last_session_part})
        messages.append(msg)
