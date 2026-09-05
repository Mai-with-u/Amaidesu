"""
FastAPI 路由注册

集中注册所有 API 路由。
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.modules.dashboard.api import (
    agenda,
    capabilities,
    components,
    config,
    debug,
    events,
    llm,
    messages,
    mock,
    simulator,
    streamer,
    system,
    traces,
)


def create_app() -> FastAPI:
    """创建 FastAPI 应用。

    v2 路由列表（Wave N / ADR-006 follow-up 后续波次）：
    - 删除 maibot / outline / proactive 旧阶段路由；
    - 模拟直播能力由 ``SimulatorService``（LLM 生成式模拟）+ ``MockCollector``
      （确定性 JSONL 回放）双承载，分别挂载在 ``/api/v1/simulator/*`` 与
      ``/api/v1/mock/*`` 控制面。
    """
    app = FastAPI(
        title="Amaidesu Dashboard API",
        description="WebUI Dashboard REST API",
        version="1.0.0",
    )

    # 注册路由
    app.include_router(system.router, prefix="/api/v1/system", tags=["System"])
    app.include_router(components.router, prefix="/api/v1/components", tags=["Components"])
    app.include_router(messages.router, prefix="/api/v1", tags=["Messages"])
    app.include_router(config.router, prefix="/api/v1/config", tags=["Config"])
    app.include_router(debug.router, prefix="/api/v1/debug", tags=["Debug"])
    app.include_router(llm.router, prefix="/api/v1/llm", tags=["LLM"])
    app.include_router(capabilities.router, prefix="/api/v1", tags=["Capabilities"])
    app.include_router(events.router, prefix="/api/v1", tags=["Events"])
    app.include_router(traces.router, prefix="/api/v1", tags=["Traces"])
    app.include_router(agenda.router, prefix="/api/v1/agenda", tags=["Agenda"])
    app.include_router(streamer.router, prefix="/api/v1/streamer", tags=["Streamer"])

    # 模拟器与 Mock 采集器控制面（ADR-006 follow-up）
    app.include_router(simulator.router, prefix="/api/v1/simulator", tags=["Simulator"])
    app.include_router(mock.router, prefix="/api/v1/mock", tags=["MockCollector"])

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
