"""
FastAPI 路由注册

集中注册所有 API 路由。
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.modules.dashboard.api import (
    agents,
    components,
    config,
    debug,
    events,
    llm,
    rundown,
    rundowns,
    sessions,
    simulator,
    streamer,
    system,
    tools,
    viewers,
    vision,
)


def create_app() -> FastAPI:
    """创建 FastAPI 应用。

    路由域：system / components / config / debug / llm / tools / events /
    rundown / rundowns / streamer / simulator / live-sessions / viewers /
    agents / vision，全部挂在 ``/api/v1`` 前缀下。
    """
    app = FastAPI(
        title="Amaidesu Dashboard API",
        description="WebUI Dashboard REST API",
        version="1.0.0",
    )

    # 注册路由
    app.include_router(system.router, prefix="/api/v1/system", tags=["System"])
    app.include_router(components.router, prefix="/api/v1/components", tags=["Components"])
    app.include_router(config.router, prefix="/api/v1/config", tags=["Config"])
    app.include_router(debug.router, prefix="/api/v1/debug", tags=["Debug"])
    app.include_router(llm.router, prefix="/api/v1/llm", tags=["LLM"])
    app.include_router(tools.router, prefix="/api/v1", tags=["Tools"])
    app.include_router(events.router, prefix="/api/v1", tags=["Events"])
    # 流程单运行态（状态视图 / 手动控制）
    app.include_router(rundown.router, prefix="/api/v1/rundown", tags=["Rundown"])
    # 流程单库 CRUD（列表 / 模板 / upsert / 删除 / 复制 / 设为当前）
    app.include_router(rundowns.router, prefix="/api/v1/rundowns", tags=["Rundown"])
    app.include_router(streamer.router, prefix="/api/v1/streamer", tags=["Streamer"])

    # 模拟器控制面（generate / replay 三模式工作台）
    app.include_router(simulator.router, prefix="/api/v1/simulator", tags=["Simulator"])

    # 直播场次控制面（列表 / 开启 / 结束 / 删除，LiveSessionManager 承载）
    app.include_router(sessions.router, prefix="/api/v1/live-sessions", tags=["Sessions"])

    # 观众统计只读端点（viewers 表最小消费面）
    app.include_router(viewers.router, prefix="/api/v1/viewers", tags=["Viewers"])

    # Agent 控制面（运行态观测 + 框架级控制）
    app.include_router(agents.router, prefix="/api/v1/agents", tags=["Agents"])

    # 视觉感知端点（mss 显示器枚举 + 预览抓帧），无 VLM / 不缓存
    app.include_router(vision.router, prefix="/api/v1/vision", tags=["Vision"])

    return app


def setup_cors(app: FastAPI, cors_origins: list[str]) -> None:
    """配置 CORS 中间件"""
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
