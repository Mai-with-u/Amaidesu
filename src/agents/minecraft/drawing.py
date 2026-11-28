"""后台建筑图纸生成：minecraft_drawing 工具的执行体。

主模型调 minecraft_drawing 委派画图（purpose=要什么样的房子，target=参照/位置）；
本类在 agent 事件循环里挂一个生成协程：读一次现场 → 用 minecraft_builder 档生成
drawing JSON → 发 Mod design create 校验收档（报错拼回重画）→ 成功/失败都以
[系统] 通知注入唤醒主模型。

边界（设计定案见 .omo/drafts/minecraft-builder-tool.md）：
- 串行上限：同一时间最多一个生成任务，第二个调用当场拒绝；
- 委派收手（release epoch 变化）时取消生成，结果静默丢弃——没有消费者；
- 不进通用任务账本：可见性=工具回执+系统通知+日志；
- 交付门禁不管图纸：build 引用未就绪的图纸会得到 Mod 的明确错误观察，反馈回路足够。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional

from src.modules.logging import get_logger

from src.agents.minecraft.maicraft import EXECUTE, LOOKUP

logger = get_logger("DrawingBuilder")

# 图纸生成用的模型档：独立于游戏决策档（模型名单/温度/思考档在 model.toml 的 minecraft_builder）
BUILDER_PROFILE = "minecraft_builder"

# 现场读取与知识文档的数量上限：守住"有界的生成任务、不是第二个 agent"的边界
_MAX_KNOWLEDGE_DOCS = 6
# 单篇知识正文截取上限：足以容纳核心图纸格式说明，同时防止上下文膨胀
_MAX_KNOWLEDGE_BODY_CHARS = 6000
# 单次图纸生成超时上限与整单总耗时预算（毫秒）：防止校验重试阶段双模型各阻塞 120s 造成无界长等待
DEFAULT_ATTEMPT_TIMEOUT_MS = 75_000
DEFAULT_TOTAL_BUDGET_MS = 180_000

# 核心建筑知识文档优先级：确保格式规范与屋顶/范例排在前面，不被上限截断
_CORE_BUILDING_DOC_ORDER: tuple[str, ...] = (
    "building/design",
    "building/roofs",
    "building/example-cottage",
    "building/house",
    "building/styles",
)
# 合法建筑知识 ID 形态：允许完整 URI 或相对路径，严禁空白、括号、引号或逗号等 JSON 结构噪声
_BUILDING_KNOWLEDGE_ID_RE = re.compile(r"^(?:maicraft://knowledge/)?building/[A-Za-z0-9._/-]+$")

# 校准后的 design create 系统提示词（对齐 MaiCraft 现行 schema：materials 对象形态、MESH 必填 primitive、ROOF 用 footprint）
DRAWING_SYSTEM_PROMPT = (
    "你是 MaiCraft 的建筑图纸生成器：按需求产出一次 design create 用的 drawing JSON。"
    "只输出一个 JSON 对象——不要 markdown 代码围栏、不要解释文字。"
    '顶层键：name（图纸名）、coordinate_system（固定 "minecraft_y_up"）、'
    'materials（材质名 → 对象 {"block_id": "命名空间:方块id", "properties"?: {...}} '
    '或混色 {"mix": [{"block_id": "...", "weight"?: 1}]}，严禁直接写裸字符串方块 id）、'
    "objects（物件列表，可选 components）。"
    'MESH 物件必填：name、type: "MESH"、primitive（如 cube/panel/wedge/triangular_prism/pyramid/prism/cylinder/cone）、'
    "location（物件中心 [x, y, z]，奇数边长的轴写 .5，偶数边长的轴写整数）、dimensions（整数边长 [x, y, z]）、"
    "material（引用 materials 里的键名，纯切割体可省略）；"
    '门窗洞用 modifiers: [{"type": "BOOLEAN", "operation": "DIFFERENCE", "object": "同组切割体名"}]，'
    '重复元素用 array: {"count": [cx, cy, cz], "step": [sx, sy, sz]}；房间内部留空需放置 minecraft:air 材质物件。'
    'ROOF 物件必填：name、type: "ROOF"、location、footprint（[宽, 深]，不用 dimensions）、material，'
    "可配 shape、overhang、gable_material 等。"
    "严格遵守随附图纸格式资料中的字段与取值约束；若收到上一次校验报错，按报错里的 objects[下标] 定位逐项修正。"
)


def _normalize_building_doc_key(doc_id: str) -> str:
    """去掉可选 ``maicraft://knowledge/`` 前缀，用于排序和优先级比对。"""
    prefix = "maicraft://knowledge/"
    return doc_id[len(prefix) :] if doc_id.startswith(prefix) else doc_id


def extract_building_knowledge_ids(catalog: Any, *, limit: int = _MAX_KNOWLEDGE_DOCS) -> List[str]:
    """从 ``maicraft_lookup(topic='knowledge')`` 返回的目录结构中安全提取干净的 ``building/`` 知识 ID。

    支持整包回复（含 ``data`` / ``notes``）、嵌套 ``knowledge`` 字典列表、纯字符串列表等形态；
    递归结构化遍历并按合法 ID 模式校验，彻底杜绝把 JSON 序列化字符串按逗号硬切拼出畸形路径。
    核心建筑格式文档优先排前，保序去重后截断至 ``limit`` 条。
    """
    if limit <= 0 or catalog is None:
        return []

    collected: List[str] = []
    seen: set[str] = set()

    def _add_if_valid(raw: Any) -> None:
        if not isinstance(raw, str):
            return
        candidate = raw.strip()
        if not candidate or candidate in seen:
            return
        if _BUILDING_KNOWLEDGE_ID_RE.fullmatch(candidate):
            seen.add(candidate)
            collected.append(candidate)

    def _walk(node: Any, depth: int = 0) -> None:
        # 限制递归深度防止畸形嵌套结构导致栈溢出
        if depth > 12 or node is None:
            return
        if isinstance(node, str):
            _add_if_valid(node)
            return
        if isinstance(node, Mapping):
            # 优先读取目录条目的标准 id / uri 字段，再继续遍历嵌套子容器
            for key in ("id", "uri"):
                _add_if_valid(node.get(key))
            for key, value in node.items():
                if key in ("id", "uri"):
                    continue
                if isinstance(value, (Mapping, list, tuple)):
                    _walk(value, depth + 1)
            return
        if isinstance(node, (list, tuple)):
            for item in node:
                _walk(item, depth + 1)

    if isinstance(catalog, Mapping):
        # 传入整包回执时优先扫 data / notes，无这两个键则直接扫整个 mapping
        if "data" in catalog or "notes" in catalog:
            _walk(catalog.get("data"))
            _walk(catalog.get("notes"))
        else:
            _walk(catalog)
    else:
        _walk(catalog)

    priority_index = {key: idx for idx, key in enumerate(_CORE_BUILDING_DOC_ORDER)}
    # 核心格式文档优先加载，同级保持目录原始顺序
    ordered = sorted(
        enumerate(collected),
        key=lambda pair: (
            priority_index.get(_normalize_building_doc_key(pair[1]), len(_CORE_BUILDING_DOC_ORDER)),
            pair[0],
        ),
    )
    return [doc_id for _, doc_id in ordered[:limit]]


def _extract_knowledge_body(reply: Mapping[str, Any]) -> str:
    """从单篇 ``maicraft_lookup(topic='knowledge', id=...)`` 回复提取可读正文。"""
    payload = reply.get("data")
    if isinstance(payload, Mapping):
        text = payload.get("text") or payload.get("doc") or payload.get("content")
        if isinstance(text, str) and text.strip():
            title = str(payload.get("title") or "").strip()
            body = f"{title}\n{text.strip()}" if title and not text.lstrip().startswith("#") else text.strip()
            return body[:_MAX_KNOWLEDGE_BODY_CHARS]
        return json.dumps(payload, ensure_ascii=False)[:_MAX_KNOWLEDGE_BODY_CHARS]
    fallback = reply.get("notes") or {}
    return json.dumps(fallback, ensure_ascii=False)[:_MAX_KNOWLEDGE_BODY_CHARS]


class DrawingValidationError(Exception):
    """图纸被 Mod 校验拒绝且重画次数用尽，或生成调用超时/失败。"""


class DrawingBuilder:
    """后台图纸生成器（minecraft_drawing 工具的执行体）。

    Args:
        llm: LLMManager（生成调用走 minecraft_builder 档）。
        maicraft_call: 直调 Mod 工具的回调 ``(tool_name, arguments) -> 原样 JSON 回执``。
        notify: 注入通知的回调（写 [系统] 消息进对话历史并置唤醒事件）。
        release_epoch_reader: 读当前收手纪元；生成完成时纪元变了说明委派已收手，结果静默丢弃。
        max_validation_retries: Mod 校验拒绝后的最大重试次数。
        attempt_timeout_ms: 单次生成调用超时上限（毫秒）；<=0 表示不设单次上限。
        total_budget_ms: 整次画图任务（含重试）的总耗时预算（毫秒）；<=0 表示不设总预算。
        clock: 单调时钟回调（秒），供测试注入假时钟验证总预算收敛。
    """

    def __init__(
        self,
        *,
        llm: Any,
        maicraft_call: Callable[[str, Dict[str, Any]], Awaitable[Dict[str, Any]]],
        notify: Callable[[str], None],
        release_epoch_reader: Callable[[], int],
        max_validation_retries: int,
        attempt_timeout_ms: int = DEFAULT_ATTEMPT_TIMEOUT_MS,
        total_budget_ms: int = DEFAULT_TOTAL_BUDGET_MS,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self._llm = llm
        self._maicraft_call = maicraft_call
        self._notify = notify
        self._release_epoch = release_epoch_reader
        self._max_retries = max(0, max_validation_retries)
        self._attempt_timeout_ms = int(attempt_timeout_ms)
        self._total_budget_ms = int(total_budget_ms)
        self._clock: Callable[[], float] = clock or time.monotonic
        self._task: Optional[asyncio.Task[None]] = None
        self._task_seq = 0
        self._started_epoch = 0
        # 生成完成但通知还没进对话：通知可能在模型推理期间入队（in_progress 已翻 False），
        # 让出判定把这段窗口也按"在跑"对待，防通知先到却被判"停了没交付"
        self._notice_pending = False

    def in_progress(self) -> bool:
        """是否有图纸正在后台生成（串行拒绝用）。"""
        return self._task is not None and not self._task.done()

    def has_unconsumed_notice(self) -> bool:
        """生成已完成但完成通知还没进对话（挂起判定用）。"""
        return self._notice_pending

    def mark_notice_consumed(self) -> None:
        """通知已进入对话（每轮推理开始前调用）：此后让出判定回到正常语义。"""
        self._notice_pending = False

    def start(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """受理画图委派：串行检查 → 挂生成协程 → 当场回执任务编号。"""
        if self.in_progress():
            return {
                "ok": False,
                "error": {"code": "drawing_busy", "message": "上一张图纸还在画，等 [系统] 通知到了再要新的"},
            }
        purpose = str(arguments.get("purpose") or "").strip()
        if not purpose:
            return {
                "ok": False,
                "error": {
                    "code": "missing_purpose",
                    "message": "purpose 必填：要什么样的房子，写清楚位置/参照、大小、材料偏好",
                },
            }
        target = arguments.get("target")
        target = target if isinstance(target, dict) else None
        self._task_seq += 1
        task_id = f"drawing-{self._task_seq}"
        self._started_epoch = self._release_epoch()
        self._task = asyncio.create_task(self._generate(task_id, purpose, target), name=task_id)
        logger.info(f"图纸生成任务已受理（{task_id}）：{purpose[:60]}")
        return {
            "ok": True,
            "data": {
                "task_id": task_id,
                "status": "generating",
                "note": "图纸画好后会以 [系统] 通知你（含 design_id），期间可以先备常用的料或干别的活",
            },
        }

    def cancel(self) -> None:
        """委派收手时取消进行中的生成（结果静默丢弃，不注入通知）。"""
        if self._task is not None and not self._task.done():
            self._task.cancel()
            logger.info("图纸生成已随委派收手取消")

    async def _generate(self, task_id: str, purpose: str, target: Optional[Dict[str, Any]]) -> None:
        started_epoch = self._started_epoch
        try:
            design_id, name = await self._produce(purpose, target)
        except asyncio.CancelledError:
            raise
        except DrawingValidationError as exc:
            self._finish(
                started_epoch,
                f"[系统] 图纸生成失败（{task_id}，试了 {self._max_retries + 1} 次仍过不了校验）："
                f"{str(exc)[:300]}。可自己调 design 直接画或上报主播。",
            )
            return
        except Exception as exc:
            logger.error(f"图纸生成异常（{task_id}）：{exc}", exc=True)
            self._finish(
                started_epoch,
                f"[系统] 图纸生成失败（{task_id}）：出了意外，可自己调 design 直接画或上报主播。",
            )
            return
        self._finish(
            started_epoch,
            f"[系统] 图纸已画好：design_id={design_id}，名称={name}，可直接 build(design_id={design_id})。",
        )

    def _finish(self, started_epoch: int, notice: str) -> None:
        self._task = None
        if self._release_epoch() != started_epoch:
            # 委派已收手：通知没有消费者，静默丢弃
            logger.info("图纸生成结束时委派已收手，结果静默丢弃")
            return
        self._notice_pending = True
        self._notify(notice)

    async def _produce(self, purpose: str, target: Optional[Dict[str, Any]]) -> tuple[str, str]:
        started_at = self._clock()
        site = await self._read_site(target)
        knowledge = await self._load_knowledge()
        drawing: Optional[Dict[str, Any]] = None
        last_error = ""
        for attempt in range(self._max_retries + 1):
            call_timeout_ms = self._next_attempt_timeout_ms(started_at)
            if call_timeout_ms is not None and call_timeout_ms <= 0:
                detail = f"；上一次校验错误：{last_error}" if last_error else ""
                raise DrawingValidationError(
                    f"图纸生成累计耗时已达预算上限（{self._total_budget_ms // 1000} 秒），停止继续重试{detail}"
                )
            drawing = await self._compose(
                purpose,
                site,
                knowledge,
                last_error,
                attempt=attempt,
                timeout_ms=call_timeout_ms,
            )
            reply = await self._maicraft_call(
                EXECUTE,
                {"goal": {"ability": "design", "parameters": {"operation": "create", "drawing": drawing}}},
            )
            if reply.get("ok"):
                data = reply.get("data") or {}
                design_id = str(data.get("design_id") or data.get("id") or "")
                name = str(data.get("name") or drawing.get("name") or "")
                if design_id:
                    logger.info(f"图纸已存档：design_id={design_id}（第 {attempt + 1} 次尝试）")
                    return design_id, name
                last_error = f"存档成功但回执里没有 design_id：{json.dumps(data, ensure_ascii=False)[:300]}"
                continue
            error = reply.get("error") or {}
            last_error = json.dumps(error, ensure_ascii=False)[:600]
            logger.warning(f"图纸第 {attempt + 1} 次尝试被 Mod 校验拒绝：{last_error}")
        raise DrawingValidationError(last_error or "校验重试次数用尽")

    def _next_attempt_timeout_ms(self, started_at: float) -> Optional[int]:
        """结合单次超时与整单总预算计算本次生成可用超时（毫秒）；总预算耗尽时返回 0。"""
        remaining_budget_ms: Optional[int] = None
        if self._total_budget_ms > 0:
            elapsed_ms = max(0, int(round((self._clock() - started_at) * 1000)))
            remaining_budget_ms = max(0, self._total_budget_ms - elapsed_ms)
            if remaining_budget_ms <= 0:
                return 0
        if self._attempt_timeout_ms > 0 and remaining_budget_ms is not None:
            return min(self._attempt_timeout_ms, remaining_budget_ms)
        if self._attempt_timeout_ms > 0:
            return self._attempt_timeout_ms
        return remaining_budget_ms

    async def _read_site(self, target: Optional[Dict[str, Any]]) -> str:
        """target 指了参照建筑/地点时读一次现场；读不到就空着继续（图纸质量靠校验回执兜）。"""
        if not target:
            return ""
        try:
            reply = await self._maicraft_call(
                EXECUTE,
                {"goal": {"ability": "design", "parameters": {"operation": "read_site"}, "target": target}},
            )
            if reply.get("ok"):
                return json.dumps(reply.get("data") or reply.get("notes") or {}, ensure_ascii=False)[:4000]
            logger.warning(f"现场读取未成功，图纸不带现场信息：{reply.get('error')}")
        except Exception as exc:
            logger.warning(f"现场读取异常，图纸不带现场信息：{exc}", exc=True)
        return ""

    async def _load_knowledge(self) -> str:
        """拉 Mod 随包的图纸格式资料：结构化提取 building/ 知识 ID 并逐篇读取正文（有界、降级安全）。"""
        try:
            catalog = await self._maicraft_call(LOOKUP, {"topic": "knowledge"})
            if not isinstance(catalog, Mapping) or catalog.get("ok") is False:
                logger.warning(f"图纸格式资料目录读取未成功，靠校验回执兜格式：{catalog}")
                return ""
            doc_ids = extract_building_knowledge_ids(catalog, limit=_MAX_KNOWLEDGE_DOCS)
            if not doc_ids:
                logger.debug("知识目录中未发现 building/ 建筑资料条目，跳过逐篇拉取")
                return ""
            sections: List[str] = [f"已加载建筑资料条目：{', '.join(doc_ids)}"]
            for doc_id in doc_ids:
                reply = await self._maicraft_call(LOOKUP, {"topic": "knowledge", "id": doc_id})
                if not isinstance(reply, Mapping) or reply.get("ok") is False:
                    logger.warning(f"图纸资料 {doc_id} 读取未成功，已跳过：{reply}")
                    continue
                body = _extract_knowledge_body(reply)
                if body:
                    sections.append(f"### 资料 {doc_id}\n{body}")
            return "\n\n".join(sections) if len(sections) > 1 else ""
        except Exception as exc:
            logger.warning(f"图纸格式资料读取失败，靠校验回执兜格式：{exc}", exc=True)
            return ""

    async def _compose(
        self,
        purpose: str,
        site: str,
        knowledge: str,
        last_error: str,
        *,
        attempt: int = 0,
        timeout_ms: Optional[int] = None,
    ) -> Dict[str, Any]:
        """用 minecraft_builder 档生成一次 drawing JSON（无工具，纯生成；重试轮收敛思考预算并受超时约束）。"""
        user_lines = [f"需求：{purpose}"]
        if site:
            user_lines.append(f"现场读取结果：{site}")
        if knowledge:
            user_lines.append(f"图纸格式资料：\n{knowledge}")
        if last_error:
            user_lines.append(f"上一次的图纸被 Mod 校验拒绝，原因如下；改掉这些问题重新输出完整 JSON：\n{last_error}")
        user_lines.append("只输出一个 drawing JSON 对象。")
        generate_kwargs: Dict[str, Any] = {"profile": BUILDER_PROFILE}
        if attempt > 0:
            # 校验重试轮已有明确错误定位，压低思考档位收敛重试耗时，避免重复慢思考拉满阻塞
            generate_kwargs["reasoning_effort"] = "low"
        coro = self._llm.generate(
            [
                {"role": "system", "content": DRAWING_SYSTEM_PROMPT},
                {"role": "user", "content": "\n".join(user_lines)},
            ],
            **generate_kwargs,
        )
        try:
            if timeout_ms is not None and timeout_ms > 0:
                response = await asyncio.wait_for(coro, timeout=timeout_ms / 1000.0)
            else:
                response = await coro
        except TimeoutError as exc:
            limit_s = max(1, (timeout_ms or 0) // 1000)
            logger.warning(f"图纸第 {attempt + 1} 次生成调用超时（超过 {limit_s} 秒），已提前终止")
            raise DrawingValidationError(f"第 {attempt + 1} 次生成超时（超过 {limit_s} 秒）") from exc
        if not response.success:
            raise DrawingValidationError(response.error or "生成调用失败")
        return _parse_drawing_json(response.content or "")


def _parse_drawing_json(content: str) -> Dict[str, Any]:
    """从生成回复里抠出 drawing JSON：剥掉可能的代码围栏后解析。"""
    text = content.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1 :]
        text = text.rsplit("```", 1)[0].strip()
    try:
        drawing = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DrawingValidationError(f"生成回复不是合法 JSON：{exc}") from exc
    if not isinstance(drawing, dict):
        raise DrawingValidationError("生成回复不是 JSON 对象")
    return drawing
