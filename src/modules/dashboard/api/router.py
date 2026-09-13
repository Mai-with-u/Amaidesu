"""
FastAPI 路由注册

集中注册所有 API 路由。
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.modules.dashboard.api import (
    agenda,
    components,
    config,
    debug,
    events,
    llm,
    sessions,
    simulator,
    streamer,
    system,
    tools,
    traces,
    viewers,
)


def create_app() -> FastAPI:
    """创建 FastAPI 应用。

    v2 路由列表：
    - 删除 maibot / outline / proactive 旧阶段路由；
    - 模拟直播能力由 ``SimulatorService`` 统一承载（generate 生成 / replay
      回放三模式），挂载在 ``/api/v1/simulator/*`` 控制面。
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
    app.include_router(traces.router, prefix="/api/v1", tags=["Traces"])
    app.include_router(agenda.router, prefix="/api/v1/agenda", tags=["Agenda"])
    app.include_router(streamer.router, prefix="/api/v1/streamer", tags=["Streamer"])

    # 模拟器控制面（generate / replay 三模式工作台）
    app.include_router(simulator.router, prefix="/api/v1/simulator", tags=["Simulator"])

    # 直播场次控制面（列表 / 开启 / 结束 / 删除，LiveSessionManager 承载）
    app.include_router(sessions.router, prefix="/api/v1/live-sessions", tags=["Sessions"])

    # 观众统计只读端点（viewers 表最小消费面）
    app.include_router(viewers.router, prefix="/api/v1/viewers", tags=["Viewers"])

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
