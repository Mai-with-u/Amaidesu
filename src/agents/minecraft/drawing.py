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
from typing import Any, Awaitable, Callable, Dict, Optional

from src.modules.logging import get_logger

from src.agents.minecraft.maicraft import EXECUTE, LOOKUP

logger = get_logger("DrawingBuilder")

# 图纸生成用的模型档：独立于游戏决策档（模型名单/温度/思考档在 model.toml 的 minecraft_builder）
BUILDER_PROFILE = "minecraft_builder"

# 现场读取与知识文档的数量上限：守住"有界的生成任务、不是第二个 agent"的边界
_MAX_KNOWLEDGE_DOCS = 6


class DrawingValidationError(Exception):
    """图纸被 Mod 校验拒绝且重画次数用尽。"""


class DrawingBuilder:
    """后台图纸生成器（minecraft_drawing 工具的执行体）。

    Args:
        llm: LLMManager（生成调用走 minecraft_builder 档）。
        maicraft_call: 直调 Mod 工具的回调 ``(tool_name, arguments) -> 原样 JSON 回执``。
        notify: 注入通知的回调（写 [系统] 消息进对话历史并置唤醒事件）。
        release_epoch_reader: 读当前收手纪元；生成完成时纪元变了说明委派已收手，结果静默丢弃。
        config: 图纸生成配置（enabled 在装配层判，这里只用重试上限）。
    """

    def __init__(
        self,
        *,
        llm: Any,
        maicraft_call: Callable[[str, Dict[str, Any]], Awaitable[Dict[str, Any]]],
        notify: Callable[[str], None],
        release_epoch_reader: Callable[[], int],
        max_validation_retries: int,
    ) -> None:
        self._llm = llm
        self._maicraft_call = maicraft_call
        self._notify = notify
        self._release_epoch = release_epoch_reader
        self._max_retries = max(0, max_validation_retries)
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
        site = await self._read_site(target)
        knowledge = await self._load_knowledge()
        drawing: Optional[Dict[str, Any]] = None
        last_error = ""
        for attempt in range(self._max_retries + 1):
            drawing = await self._compose(purpose, site, knowledge, last_error)
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
        """拉 Mod 随包的图纸格式资料：知识目录 + building 前缀的文档正文（有界）。"""
        try:
            catalog = await self._maicraft_call(LOOKUP, {"topic": "knowledge"})
            catalog_text = json.dumps(catalog.get("data") or catalog.get("notes") or {}, ensure_ascii=False)
            doc_ids = [
                token.strip("\"' ,")
                for token in catalog_text.replace("[", " ").replace("]", " ").replace('"', " ").split(",")
                if "building/" in token
            ][:_MAX_KNOWLEDGE_DOCS]
            sections = [f"资料目录：{catalog_text[:1500]}"]
            for doc_id in doc_ids:
                reply = await self._maicraft_call(LOOKUP, {"topic": "knowledge", "id": doc_id})
                body = json.dumps(reply.get("data") or reply.get("notes") or {}, ensure_ascii=False)[:3000]
                sections.append(f"资料 {doc_id}：{body}")
            return "\n".join(sections)
        except Exception as exc:
            logger.warning(f"图纸格式资料读取失败，靠校验回执兜格式：{exc}", exc=True)
            return ""

    async def _compose(self, purpose: str, site: str, knowledge: str, last_error: str) -> Dict[str, Any]:
        """用 minecraft_builder 档生成一次 drawing JSON（无工具，纯生成）。"""
        user_lines = [f"需求：{purpose}"]
        if site:
            user_lines.append(f"现场读取结果：{site}")
        if knowledge:
            user_lines.append(f"图纸格式资料：\n{knowledge}")
        if last_error:
            user_lines.append(f"上一次的图纸被 Mod 校验拒绝，原因如下；改掉这些问题重新输出完整 JSON：\n{last_error}")
        user_lines.append("只输出一个 drawing JSON 对象。")
        response = await self._llm.generate(
            [
                {
                    "role": "system",
                    "content": (
                        "你是 MaiCraft 的建筑图纸生成器：按需求产出一次 design create 用的 drawing JSON。"
                        "只输出一个 JSON 对象——不要 markdown 代码围栏、不要解释文字。"
                        "drawing 顶层键：name（图纸名）、coordinate_system（固定 minecraft_y_up）、"
                        "materials（材质名 → 方块 id）、objects（物件列表）。"
                        "物件写 name/type/location/dimensions/material，type 用 MESH 或 ROOF；"
                        "门窗洞用 BOOLEAN DIFFERENCE 修饰符，重复元素用 array 阵列。"
                    ),
                },
                {"role": "user", "content": "\n".join(user_lines)},
            ],
            profile=BUILDER_PROFILE,
        )
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
