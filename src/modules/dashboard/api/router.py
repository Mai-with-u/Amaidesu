"""
FastAPI 路由注册

集中注册所有 API 路由。
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.modules.dashboard.api import config, debug, llm, maibot, messages, providers, system


def create_app() -> FastAPI:
    """创建 FastAPI 应用"""
    app = FastAPI(
        title="Amaidesu Dashboard API",
        description="WebUI Dashboard REST API",
        version="1.0.0",
    )

    # 注册路由
    app.include_router(system.router, prefix="/api/v1/system", tags=["System"])
    app.include_router(providers.router, prefix="/api/v1/providers", tags=["Providers"])
    app.include_router(messages.router, prefix="/api/v1", tags=["Messages"])
    app.include_router(config.router, prefix="/api/v1/config", tags=["Config"])
    app.include_router(debug.router, prefix="/api/v1/debug", tags=["Debug"])
    app.include_router(llm.router, prefix="/api/v1/llm", tags=["LLM"])
    app.include_router(maibot.router, prefix="/api/v1/maibot", tags=["MaiBot"])

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
