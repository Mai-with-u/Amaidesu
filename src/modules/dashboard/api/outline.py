"""
直播大纲 Dashboard API

提供 WebUI 控制端点，把"加载/控制/查看/编辑"指令转发到 Decision 阶段：

- ``GET  /api/v1/outline/state``     → 当前大纲运行时状态（环节/进度/暂停等）
- ``POST /api/v1/outline/load``      → 加载指定 TOML 大纲文件
- ``POST /api/v1/outline/control``   → skip/pause/resume/rewind/jump 手动控制
- ``PUT  /api/v1/outline/file``      → 写回 TOML（下一段生效）
- ``GET  /api/v1/outline/segments``  → 当前大纲完整环节列表（供编辑页渲染）

调用语义（参见 :mod:`src.stages.decision.manager`）：
- manager 层用 ``hasattr`` 鸭子类型转发到"实现了 ``outline_*`` 接口"的 Decider
- 未实现的 Decider 静默跳过，**任何** Decider 都未实现时由 manager 返回
  ``{"error": "not_implemented", ...}`` 风格响应；本 API 层将其映射为 HTTP 501
- 失败的 Decider 被隔离（异常吞掉 + 日志），不会影响其他 Decider

注意
----
- 本模块**只**负责 API 端点 + 字段映射 + 错误码转换；具体的 ``outline_state`` /
  ``outline_load`` / ``outline_control`` / ``outline_save_file`` 实现由 Decider
  (T10) 在 ``AmaidesuDecider`` 上提供鸭子类型方法。本模块不假定任何特定 Decider
  类型，通过 ``hasattr`` 检查实现可用性。
- "下一段生效"是 ``PUT /outline/file`` 的既定契约：保存文件后**不**触发热重载，
  由下次 ``skip`` / 自然推进 / 重新 ``load`` 触发。Decider 侧可选择监听文件变化，
  但本 API 不主动触发。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.modules.dashboard.dependencies import get_dashboard_server
from src.modules.logging import get_logger

if TYPE_CHECKING:
    from src.modules.dashboard.server import DashboardServer

router = APIRouter()
logger = get_logger("OutlineAPI")

ServerDep = Annotated["DashboardServer", Depends(get_dashboard_server)]


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class OutlineLoadRequest(BaseModel):
    """加载大纲请求体"""

    path: str = Field(
        ...,
        description="大纲 TOML 文件路径（相对项目根或绝对路径）",
    )


class OutlineControlRequest(BaseModel):
    """手动控制请求体"""

    action: Literal["skip", "pause", "resume", "rewind", "jump"] = Field(
        ...,
        description="控制动作：skip=跳到下一环节；pause/resume=暂停/继续；rewind=回退；jump=跳到指定 segment_id",
    )
    segment_id: Optional[str] = Field(
        default=None,
        description="jump 时必填的目标环节 id；其余动作忽略",
    )


class OutlineFileWriteRequest(BaseModel):
    """写回大纲 TOML 文件请求体"""

    path: str = Field(
        ...,
        description="要写入的大纲 TOML 文件路径",
    )
    content: str = Field(
        ...,
        description="TOML 文件完整内容（覆盖写入）",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ensure_decision_manager(server: "DashboardServer") -> Any:
    """检查 ``decision_manager`` 已注入；未注入返回 404 风格 HTTPException。

    与 :mod:`proactive` 保持一致的语义：Decider 未加载时 API 视为"目标不可用"。
    """
    if server.decision_manager is None:
        raise HTTPException(status_code=404, detail="决策器未加载")
    return server.decision_manager


def _raise_if_not_implemented(result: Any) -> None:
    """检查 manager 返回的 dict 是否标记 ``not_implemented``；是则抛出 501。

    Manager 层在"没有任何 Decider 实现 outline 接口"时返回::

        {"error": "not_implemented", "status_code": 501, "message": "..."}

    本助手把这种标记转换为 ``HTTPException(501)``，让客户端拿到有意义的 HTTP 状态码。

    Args:
        result: manager 方法的返回值（dict / 其他）

    Raises:
        HTTPException: status_code=501，未实现提示
    """
    if not isinstance(result, dict):
        return
    if result.get("error") == "not_implemented":
        status = int(result.get("status_code", 501))
        detail = result.get("message") or "outline 接口尚未由任何 Decider 实现"
        raise HTTPException(status_code=status, detail=detail)


def _build_state_response(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """把 ``OutlineState.get_snapshot()`` 风格 dict 重映射为 API 约定响应。

    字段对照（snapshot → API contract）::

        snapshot.current_segment.{id, title, remaining_ms, expanded, needs_expansion}
            → current_segment.{id, title, elapsed_ms, duration_ms, remaining_ms}
                其中 elapsed_ms = max(0, duration_ms - remaining_ms)
                      expanded_ready = expanded
                      (needs_expansion 不透传给前端，避免混淆)

        snapshot.elapsed_live_ms / total_planned_ms / progress_percent
            → live_progress.{elapsed_ms, total_ms, percent}

    Args:
        snapshot: ``OutlineState.get_snapshot()`` 返回的 dict（由 Decider 透传）

    Returns:
        满足 API 约定的响应 dict
    """
    # ---- current_segment ----
    raw_current = snapshot.get("current_segment")
    current_segment: Optional[Dict[str, Any]] = None
    if raw_current:
        duration_ms = int(raw_current.get("duration_ms") or 0)
        remaining_ms = int(raw_current.get("remaining_ms") or 0)
        elapsed_ms = max(0, duration_ms - remaining_ms) if duration_ms > 0 else 0
        current_segment = {
            "id": raw_current.get("id"),
            "title": raw_current.get("title", ""),
            "elapsed_ms": elapsed_ms,
            "duration_ms": duration_ms,
            "remaining_ms": remaining_ms,
        }

    # ---- next_segment ----
    next_segment = snapshot.get("next_segment")

    # ---- live_progress ----
    elapsed_live = snapshot.get("elapsed_live_ms")
    total_planned = snapshot.get("total_planned_ms")
    progress_percent = snapshot.get("progress_percent")
    live_progress = {
        "elapsed_ms": elapsed_live if elapsed_live is not None else 0,
        "total_ms": total_planned if total_planned is not None else 0,
        "percent": float(progress_percent) if progress_percent is not None else 0.0,
    }

    # ---- expanded_ready ----
    expanded_ready = bool((raw_current or {}).get("expanded", False))

    return {
        "status": snapshot.get("status", "inactive"),
        "current_segment": current_segment,
        "next_segment": next_segment,
        "completed_count": int(snapshot.get("completed_count", 0) or 0),
        "total_count": int(snapshot.get("total_count", 0) or 0),
        "is_paused": bool(snapshot.get("is_paused", False)),
        "expanded_ready": expanded_ready,
        "live_progress": live_progress,
    }


def _build_segments_response(snapshot_or_segments: Any) -> List[Dict[str, Any]]:
    """从 manager 返回值中提取完整环节列表，转换为编辑页友好的 dict。

    期望 ``manager.outline_segments()``（T10 提供）返回形如::

        {"segments": [OutlineSegment, ...], "outline_id": "...", "title": "..."}
        或[OutlineSegment, ...]

    本助手做以下事情：
        - 接受 list 或 dict（取 ``segments`` 字段）
        - 遍历每个 segment，提取 ``id / title / task_description / duration_ms /
          min_duration_ms / key_points / branches`` 字段
        - ``branches`` 转 list-of-dict
    """
    if isinstance(snapshot_or_segments, dict):
        raw_segments = snapshot_or_segments.get("segments", [])
    elif isinstance(snapshot_or_segments, list):
        raw_segments = snapshot_or_segments
    else:
        raw_segments = []

    result: List[Dict[str, Any]] = []
    for seg in raw_segments:
        branches_raw = getattr(seg, "branches", None) or []
        branches = [
            {
                "branch_id": getattr(b, "branch_id", ""),
                "description": getattr(b, "description", ""),
                "target_segment_id": getattr(b, "target_segment_id", ""),
            }
            for b in branches_raw
        ]
        result.append(
            {
                "id": getattr(seg, "id", ""),
                "title": getattr(seg, "title", ""),
                "task_description": getattr(seg, "task_description", ""),
                "duration_ms": int(getattr(seg, "duration_ms", 0) or 0),
                "min_duration_ms": getattr(seg, "min_duration_ms", None),
                "key_points": list(getattr(seg, "key_points", []) or []),
                "branches": branches,
            }
        )
    return result


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/outline/state")
async def get_outline_state(server: ServerDep) -> Dict[str, Any]:
    """获取当前大纲运行时状态。

    委托 ``DeciderManager.outline_state()``（鸭子类型转发到已实现该方法的 Decider）；
    若无 Decider 实现，manager 返回 ``{"error": "not_implemented", ...}``，本端点
    将其转换为 HTTP 501。

    返回字段（与任务规格 11 一致）::

        status: str
        current_segment: {id, title, elapsed_ms, duration_ms, remaining_ms} | null
        next_segment: {id, title} | null
        completed_count: int
        total_count: int
        is_paused: bool
        expanded_ready: bool
        live_progress: {elapsed_ms: int, total_ms: int, percent: float}
    """
    manager = _ensure_decision_manager(server)

    # ``outline_state`` 由 ``DeciderManager`` 额外提供，未在 ``ManagerStatusProvider``
    # 协议声明，故需 type: ignore[attr-defined]。组合根注入的是 ``DeciderManager`` 实例。
    raw = await manager.outline_state()  # type: ignore[attr-defined]
    _raise_if_not_implemented(raw)

    if not isinstance(raw, dict):
        # Decider 返回非 dict 视为格式错误，触发 500
        raise HTTPException(status_code=500, detail="Decider 返回的 outline_state 格式错误（非 dict）")

    return _build_state_response(raw)


@router.post("/outline/load")
async def load_outline(payload: OutlineLoadRequest, server: ServerDep) -> Dict[str, Any]:
    """加载指定的大纲 TOML 文件。

    委托 ``DeciderManager.outline_load(path)``：
        - manager 用 ``hasattr`` 检查出首个实现了 ``outline_load`` 的 Decider
        - Decider 内部校验文件存在性 + TOML 解析（Pydantic ``StreamOutline``）
        - 文件不存在 → HTTP 404；TOML 解析失败 → HTTP 400；其他错误 → HTTP 500
        - 无 Decider 实现 → HTTP 501
    """
    manager = _ensure_decision_manager(server)
    raw = await manager.outline_load(payload.path)  # type: ignore[attr-defined]
    _raise_if_not_implemented(raw)

    if not isinstance(raw, dict):
        raise HTTPException(status_code=500, detail="Decider 返回的 outline_load 格式错误（非 dict）")

    # Decider 应返回 {"ok": True, "path": "...", "outline_id": "..."}
    # 或 {"ok": False, "error": "not_found" | "parse_error" | ..., "detail": "..."}
    ok = bool(raw.get("ok", False))
    if ok:
        return {
            "status": "loaded",
            "path": raw.get("path", payload.path),
            "outline_id": raw.get("outline_id"),
        }
    # 错误路径：依据 error 字段映射 HTTP 状态码
    err = raw.get("error") or "unknown"
    detail = raw.get("detail") or f"加载大纲失败: {err}"
    status_code = 500
    if err in ("not_found", "file_not_found"):
        status_code = 404
    elif err in ("parse_error", "validation_error", "invalid_toml"):
        status_code = 400
    raise HTTPException(status_code=status_code, detail=detail)


@router.post("/outline/control")
async def control_outline(payload: OutlineControlRequest, server: ServerDep) -> Dict[str, Any]:
    """手动控制大纲推进（skip/pause/resume/rewind/jump）。

    委托 ``DeciderManager.outline_control(action, **kwargs)``：
        - ``jump`` 必须带 ``segment_id``；缺失返回 HTTP 400
        - 无 Decider 实现 → HTTP 501
        - 执行失败 → HTTP 500
    """
    if payload.action == "jump" and not payload.segment_id:
        raise HTTPException(status_code=400, detail="jump 操作必须指定 segment_id")

    manager = _ensure_decision_manager(server)
    kwargs: Dict[str, Any] = {}
    if payload.segment_id is not None:
        kwargs["segment_id"] = payload.segment_id
    raw = await manager.outline_control(payload.action, **kwargs)  # type: ignore[attr-defined]
    _raise_if_not_implemented(raw)

    if not isinstance(raw, dict):
        raise HTTPException(status_code=500, detail="Decider 返回的 outline_control 格式错误（非 dict）")

    ok = bool(raw.get("ok", False))
    if ok:
        return {
            "status": "ok",
            "action": payload.action,
            "segment_id": payload.segment_id,
            "current_segment_id": raw.get("current_segment_id"),
        }
    err = raw.get("error") or "unknown"
    detail = raw.get("detail") or f"控制失败: {err}"
    status_code = 400 if err in ("invalid_action", "segment_not_found", "no_active_outline") else 500
    raise HTTPException(status_code=status_code, detail=detail)


@router.put("/outline/file")
async def write_outline_file(payload: OutlineFileWriteRequest, server: ServerDep) -> Dict[str, Any]:
    """把编辑后的大纲 TOML 写回磁盘（下一段生效，本环节不变）。

    委托 ``DeciderManager.outline_save_file(path, content)``：
        - Decider 内部完成 ``Path(path).parent.mkdir(parents=True, exist_ok=True)`` +
          ``Path(path).write_text(content, encoding="utf-8")``；无需 manager 关心
        - 路径校验（防越权写入绝对路径或 ``..``）由 Decider 负责
        - 无 Decider 实现 → HTTP 501
        - 写入失败 → HTTP 500
    """
    manager = _ensure_decision_manager(server)
    raw = await manager.outline_save_file(payload.path, payload.content)  # type: ignore[attr-defined]
    _raise_if_not_implemented(raw)

    if not isinstance(raw, dict):
        raise HTTPException(status_code=500, detail="Decider 返回的 outline_save_file 格式错误（非 dict）")

    ok = bool(raw.get("ok", False))
    if ok:
        return {
            "status": "saved",
            "path": raw.get("path", payload.path),
            "bytes_written": raw.get("bytes_written"),
            "note": "下一段生效；当前环节不变",
        }
    err = raw.get("error") or "unknown"
    detail = raw.get("detail") or f"写回文件失败: {err}"
    status_code = 400 if err in ("invalid_path", "permission_denied") else 500
    raise HTTPException(status_code=status_code, detail=detail)


@router.get("/outline/segments")
async def get_outline_segments(server: ServerDep) -> Dict[str, Any]:
    """获取当前大纲完整环节列表（供编辑页渲染）。

    委托 ``DeciderManager.outline_segments()``（T10 提供）：返回当前已加载大纲的
    完整环节数组 + 元数据。若无大纲加载 → 返回空 segments + ``loaded=False``。
    无 Decider 实现 → HTTP 501。
    """
    manager = _ensure_decision_manager(server)
    raw = await manager.outline_segments()  # type: ignore[attr-defined]
    _raise_if_not_implemented(raw)

    if not isinstance(raw, dict):
        raise HTTPException(status_code=500, detail="Decider 返回的 outline_segments 格式错误（非 dict）")

    return {
        "loaded": bool(raw.get("loaded", False)),
        "outline_id": raw.get("outline_id"),
        "title": raw.get("title"),
        "fallback_segment_id": raw.get("fallback_segment_id"),
        "path": raw.get("path"),
        "segments": _build_segments_response(raw),
    }
