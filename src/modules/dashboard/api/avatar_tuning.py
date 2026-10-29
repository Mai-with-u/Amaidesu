"""形象调校 API：看着形象实时调面捕合成器，调好再写回 avatar.toml

端点（前缀 ``/api/v1/avatar/tuning``）：

- ``GET ""``：调校页需要的全部静态信息——当前生效的调参与各字段说明、表情配方
  （默认 / 覆盖 / 生效）、配方可写的键、通用通道、各驱动的校准项、动作目录，以及
  运行中的值和 avatar.toml 是否不一致（刷新页面后仍能提示"有未保存的修改"）；
- ``GET /state``：此刻的身体状态（最近一帧、主表情、心情、在想 / 在说、手动摆姿）；
- ``PATCH /config``：改若干调参，校验通过立刻生效（不写文件）；
- ``PUT / DELETE /recipes/{emotion}``：改某个情绪的配方 / 恢复默认（立刻生效，不写文件）；
- ``POST /preview``：摆一个表情保持几秒；``POST /look``：看向某一点一会儿；
  ``POST /act``：做一个动作（点头、VTS 热键……）；
- ``PUT / DELETE /manual``：手动摆姿（指定通道的值，到期自动交还）/ 结束；
- ``PATCH /calibration/{driver}``：改某个驱动的换算校准项（立刻生效，不写文件）；
- ``POST /save``：把当前生效的调参、配方覆盖与校准项写回 avatar.toml。

改动先只在运行中生效，"保存"才落盘：调的时候可以放心乱拖，重启即回到文件里的值。
面捕合成器没装配时所有端点返回 503。字段标题、说明、取值范围一律取自配置 Schema，
这里不另抄一份。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Dict, List, Optional, Type

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, ValidationError

from src.modules.avatar.tracking import TrackingConfig, TrackingRuntime
from src.modules.avatar.tracking.channels import CHANNEL_LIMITS, CHANNELS, EYE_LOOK
from src.modules.avatar.tracking.expression import (
    MOTION_KEY,
    MOTION_RANGE,
    RECIPE_KEYS,
    RECIPES,
    Recipe,
    recipe_key_span,
)
from src.modules.config.avatar_schemas import AvatarPlatformConfig
from src.modules.dashboard.dependencies import get_dashboard_server, server_component
from src.modules.dashboard.services.config_adapter import apply_config_updates
from src.modules.dashboard.utils.component_helper import config_dir as get_config_dir
from src.modules.dashboard.utils.component_helper import read_toml_dict
from src.modules.logging import get_logger

if TYPE_CHECKING:
    from src.modules.dashboard.server import DashboardServer

logger = get_logger("DashboardAvatarTuningAPI")

router = APIRouter()

Tracking = Annotated[
    TrackingRuntime,
    Depends(
        server_component(
            "avatar_tracking",
            missing_detail="面捕合成器未装配（avatar.toml 的 [tracking] enabled 关着，或启动时构造失败）",
        )
    ),
]

# 调参里不在调校页上改的字段：总开关（关了页面自己也没了）、配方（走专门的端点）
_NOT_TUNABLE = ("enabled", "recipes")
# 手动摆姿最长保持多久（页面在调时不断续期，关掉页面后很快交还给自动行为）
MANUAL_HOLD_MAX_MS = 10_000


class ConfigPatchRequest(BaseModel):
    changes: Dict[str, Any] = Field(description="字段名 → 新值（只改给出的字段）")


class RecipeRequest(BaseModel):
    offsets: Dict[str, float] = Field(description="通道 → 满强度时的偏移（整份替换该情绪的默认偏移）")
    motion: Optional[float] = Field(default=None, description="头部微动幅度倍数（不给则沿用默认配方）")


class PreviewRequest(BaseModel):
    emotion: str
    intensity: float = Field(default=0.8, ge=0.0, le=1.0)
    seconds: float = Field(default=3.0, gt=0.0, le=30.0)


class LookRequest(BaseModel):
    x: float = Field(ge=-1.0, le=1.0)
    y: float = Field(ge=-1.0, le=1.0)
    duration_ms: int = Field(default=1500, ge=100, le=10_000)


class ActRequest(BaseModel):
    action: str


class ManualRequest(BaseModel):
    values: Dict[str, float] = Field(description="通道 → 值（未给出的通道照常自动）")
    hold_ms: int = Field(default=3000, ge=100, le=MANUAL_HOLD_MAX_MS)


class CalibrationRequest(BaseModel):
    values: Dict[str, float]


def _field_meta(model: Type[BaseModel], names: List[str], values: Dict[str, Any]) -> List[Dict[str, Any]]:
    """从配置 Schema 取字段的标题、说明、类型、取值范围（单一事实源）。"""
    properties = model.model_json_schema().get("properties", {})
    out: List[Dict[str, Any]] = []
    for name in names:
        prop = properties.get(name, {})
        out.append(
            {
                "key": name,
                "value": values.get(name),
                "title": prop.get("title", name),
                "description": prop.get("description", ""),
                "type": prop.get("type", "number"),
                "min": prop.get("minimum"),
                "max": prop.get("maximum"),
                "advanced": bool(prop.get("x-ui-advanced", False)),
            }
        )
    return out


def _recipe_view(runtime: TrackingRuntime) -> Dict[str, Dict[str, Any]]:
    """每个情绪的默认配方、覆盖与生效配方（页面据此显示"已改过"并能一键恢复）。"""
    effective: Dict[str, Recipe] = runtime.recipes()
    overrides = runtime.config.recipes
    return {
        emotion: {
            "family": default.family,
            "offsets": dict(effective[emotion].offsets),
            "motion": effective[emotion].motion,
            "default_offsets": dict(default.offsets),
            "default_motion": default.motion,
            "overridden": emotion in overrides,
        }
        for emotion, default in RECIPES.items()
    }


def _platform_schema(driver: str) -> Optional[Type[BaseModel]]:
    """驱动名对应的平台配置段 Schema（校准项的说明与范围从这里取）。"""
    field = AvatarPlatformConfig.model_fields.get(driver)
    annotation = field.annotation if field is not None else None
    return annotation if isinstance(annotation, type) and issubclass(annotation, BaseModel) else None


def _calibration_view(runtime: TrackingRuntime) -> List[Dict[str, Any]]:
    views: List[Dict[str, Any]] = []
    for driver, values in runtime.calibrations().items():
        schema = _platform_schema(driver)
        items = (
            _field_meta(schema, list(values), values)
            if schema is not None
            else [{"key": k, "value": v, "title": k, "description": "", "type": "number"} for k, v in values.items()]
        )
        views.append({"driver": driver, "items": items})
    return views


def _unsaved(server: "DashboardServer", runtime: TrackingRuntime) -> bool:
    """运行中的调参、配方与校准项是否和 avatar.toml 里的不一致（读不到文件时按一致处理）。"""
    if server.config_service is None:
        return False
    saved = read_toml_dict(get_config_dir(server) / "avatar.toml")
    if not saved:
        return False
    tracking = saved.get("tracking", {})
    if TrackingConfig.from_dict(tracking if isinstance(tracking, dict) else {}) != runtime.config:
        return True
    platforms = saved.get("platform", {})
    for driver, values in runtime.calibrations().items():
        section = platforms.get(driver, {}) if isinstance(platforms, dict) else {}
        if any(section.get(key) != value for key, value in values.items()):
            return True
    return False


def _apply_config(runtime: TrackingRuntime, changes: Dict[str, Any]) -> TrackingConfig:
    """在当前生效调参上改若干字段，校验通过才换上（失败 422，原样不动）。"""
    try:
        config = TrackingConfig.model_validate({**runtime.config.model_dump(), **changes})
    except ValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    runtime.reconfigure(config)
    return config


@router.get("", summary="调校页静态信息")
async def get_tuning_profile(
    runtime: Tracking,
    server: "DashboardServer" = Depends(get_dashboard_server),  # noqa: B008 - FastAPI 依赖注入模式
) -> Dict[str, Any]:
    config = runtime.config.model_dump()
    tunable = [name for name in TrackingConfig.model_fields if name not in _NOT_TUNABLE]
    return {
        "fields": _field_meta(TrackingConfig, tunable, config),
        "recipes": _recipe_view(runtime),
        "recipe_keys": [{"key": key, "span": recipe_key_span(key)} for key in RECIPE_KEYS],
        "motion_range": list(MOTION_RANGE),
        # 眼球朝向系数由视线换算，不单独列（手动摆姿写了也会被视线盖掉）
        "channels": [
            {"name": name, "min": CHANNEL_LIMITS[name][0], "max": CHANNEL_LIMITS[name][1]}
            for name in CHANNELS
            if name not in EYE_LOOK
        ],
        "calibrations": _calibration_view(runtime),
        "actions": runtime.action_catalog(),
        "unsaved": _unsaved(server, runtime),
    }


@router.get("/state", summary="此刻的身体状态")
async def get_tuning_state(runtime: Tracking) -> Dict[str, Any]:
    return runtime.snapshot()


@router.patch("/config", summary="改调参（立刻生效，不写文件）")
async def patch_tuning_config(request: ConfigPatchRequest, runtime: Tracking) -> Dict[str, Any]:
    blocked = [name for name in request.changes if name in _NOT_TUNABLE]
    if blocked:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"调校页不改这些字段: {blocked}")
    unknown = [name for name in request.changes if name not in TrackingConfig.model_fields]
    if unknown:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"未知字段: {unknown}")
    config = _apply_config(runtime, request.changes)
    return {"config": {name: getattr(config, name) for name in request.changes}}


@router.put("/recipes/{emotion}", summary="改某个情绪的配方（立刻生效，不写文件）")
async def put_recipe(emotion: str, request: RecipeRequest, runtime: Tracking) -> Dict[str, Any]:
    override: Dict[str, float] = dict(request.offsets)
    if request.motion is not None:
        override[MOTION_KEY] = request.motion
    _apply_config(runtime, {"recipes": {**runtime.config.recipes, emotion: override}})
    return _recipe_view(runtime)[emotion]


@router.delete("/recipes/{emotion}", summary="某个情绪恢复默认配方（立刻生效，不写文件）")
async def delete_recipe(emotion: str, runtime: Tracking) -> Dict[str, Any]:
    if emotion not in RECIPES:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"未知情绪 '{emotion}'")
    recipes = {name: values for name, values in runtime.config.recipes.items() if name != emotion}
    _apply_config(runtime, {"recipes": recipes})
    return _recipe_view(runtime)[emotion]


@router.post("/preview", summary="摆一个表情保持几秒")
async def preview_expression(request: PreviewRequest, runtime: Tracking) -> Dict[str, Any]:
    if request.emotion not in RECIPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"未知情绪 '{request.emotion}'（合法名：{', '.join(RECIPES)}）",
        )
    outcome = runtime.hold_expression(request.emotion, request.intensity, request.seconds)
    return {"outcome": outcome}


@router.post("/look", summary="看向某一点一会儿")
async def look_at_point(request: LookRequest, runtime: Tracking) -> Dict[str, Any]:
    runtime.look_at(request.x, request.y, request.duration_ms)
    return {"success": True}


@router.post("/act", summary="做一个动作")
async def act(request: ActRequest, runtime: Tracking) -> Dict[str, Any]:
    result = await runtime.act(request.action)
    if result is None:
        names = [entry["name"] for entry in runtime.action_catalog()]
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"未知动作 '{request.action}'（可用：{', '.join(names)}）"
        )
    return {"success": bool(result)}


@router.put("/manual", summary="手动摆姿（到期自动交还）")
async def set_manual(request: ManualRequest, runtime: Tracking) -> Dict[str, Any]:
    unknown = [name for name in request.values if name not in CHANNEL_LIMITS]
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"未知通道 {unknown}（合法名：{', '.join(CHANNELS)}）",
        )
    runtime.set_manual(request.values, request.hold_ms)
    return {"success": True}


@router.delete("/manual", summary="结束手动摆姿")
async def clear_manual(runtime: Tracking) -> Dict[str, Any]:
    runtime.clear_manual()
    return {"success": True}


@router.patch("/calibration/{driver}", summary="改驱动的换算校准项（立刻生效，不写文件）")
async def patch_calibration(driver: str, request: CalibrationRequest, runtime: Tracking) -> Dict[str, Any]:
    schema = _platform_schema(driver)
    if schema is not None:
        for item in _field_meta(schema, list(request.values), request.values):
            low, high, value = item["min"], item["max"], item["value"]
            if (low is not None and value < low) or (high is not None and value > high):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=f"{item['title']}（{item['key']}）= {value} 超出 {low}~{high}",
                )
    try:
        found = runtime.calibrate(driver, request.values)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    if not found:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"驱动 '{driver}' 没挂上或没有校准项")
    return {"values": runtime.calibrations()[driver]}


@router.post("/save", summary="把当前生效的调参、配方与校准项写回 avatar.toml")
async def save_tuning(
    runtime: Tracking,
    server: "DashboardServer" = Depends(get_dashboard_server),  # noqa: B008 - FastAPI 依赖注入模式
) -> Dict[str, Any]:
    config_service = server.config_service
    if config_service is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="配置服务不可用，无法保存")
    changes = [(f"avatar.tracking.{name}", value) for name, value in runtime.config.model_dump().items()]
    for driver, values in runtime.calibrations().items():
        changes.extend((f"avatar.platform.{driver}.{key}", value) for key, value in values.items())
    outcome = await apply_config_updates(config_service, get_config_dir(server), changes)
    if not outcome.success:
        logger.warning(f"形象调校保存失败: {outcome.message}")
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=outcome.message)
    logger.info(f"形象调校已保存（{len(outcome.applied)} 项，写入 {outcome.target_file}）")
    return {"success": True, "saved": len(outcome.applied), "target_file": outcome.target_file}


__all__ = ["router"]
