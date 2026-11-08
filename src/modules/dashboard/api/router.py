"""
FastAPI 路由注册

集中注册所有 API 路由。
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.modules.dashboard.api import (
    agents,
    background,
    avatar,
    avatar_tuning,
    clips,
    components,
    config,
    debug,
    events,
    ledger,
    llm,
    logs,
    players,
    review,
    rundown,
    rundowns,
    sessions,
    simulator,
    streamer,
    system,
    tasks,
    tools,
    viewers,
    vision,
)


def create_app() -> FastAPI:
    """创建 FastAPI 应用。

    路由域：system / components / config / debug / llm / tools / events /
    logs / rundown / rundowns / streamer / simulator / live-sessions /
    viewers / agents / vision / memory / avatar，全部挂在 ``/api/v1`` 前缀下。
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
    # 日志落盘历史查询（日志查看器"加载更早/时间跳转/服务端搜索"数据源）
    app.include_router(logs.router, prefix="/api/v1", tags=["Logs"])
    # 流程单运行态（状态视图 / 手动控制）
    app.include_router(rundown.router, prefix="/api/v1/rundown", tags=["Rundown"])
    # 流程单库 CRUD（列表 / 模板 / upsert / 删除 / 复制 / 设为当前）
    app.include_router(rundowns.router, prefix="/api/v1/rundowns", tags=["Rundown"])
    app.include_router(streamer.router, prefix="/api/v1/streamer", tags=["Streamer"])

    # 模拟器控制面（generate / replay 三模式工作台）
    app.include_router(simulator.router, prefix="/api/v1/simulator", tags=["Simulator"])

    # 直播场次控制面（列表 / 开启 / 结束 / 删除，LiveSessionManager 承载）
    app.include_router(sessions.router, prefix="/api/v1/live-sessions", tags=["Sessions"])

    # 场次复盘只读聚合 + 数据包导出（services.session_review 承载口径）
    app.include_router(review.router, prefix="/api/v1/review", tags=["Review"])

    # 直播切片（录播匹配 / AI 选段 / 段编辑 / ffmpeg 切割受理）
    app.include_router(clips.router, prefix="/api/v1/clips", tags=["Clips"])

    # 后台任务可见性（底栏任务条刷新恢复：进行中任务清单；实时变化走 background.task.changed 推送）
    app.include_router(background.router, prefix="/api/v1/background-tasks", tags=["BackgroundTasks"])

    # 观众统计只读端点（viewers 表最小消费面）
    app.include_router(viewers.router, prefix="/api/v1/viewers", tags=["Viewers"])

    # Agent 控制面（运行态观测 + 框架级控制）
    app.include_router(agents.router, prefix="/api/v1/agents", tags=["Agents"])

    # 记忆账本：承诺与场次档案的查看、纠错与整理状态（旧画像与长期记忆端点不再存在）
    app.include_router(ledger.router, prefix="/api/v1/memory", tags=["Memory"])
    # 游戏里的人：玩家列表、观众对号确认与删玩家
    app.include_router(players.router, prefix="/api/v1/memory/players", tags=["Memory"])

    # 任务卡快照（进行中账本 + 已完结事件聚合，游戏 Agent 委派任务消费面）
    app.include_router(tasks.router, prefix="/api/v1/tasks", tags=["Tasks"])

    # 视觉感知端点（mss 显示器枚举 + 预览抓帧），无 VLM / 不缓存
    app.include_router(vision.router, prefix="/api/v1/vision", tags=["Vision"])

    # 表情差分图静态资源（小部件页取图；路由形如 /api/v1/avatar/expression_sheet/assets/<文件名>）
    app.include_router(avatar.router, prefix="/api/v1/avatar", tags=["Avatar"])
    # 形象调校：看着形象实时调面捕合成器（表情配方、眼神目标、平台校准），调好再写回 avatar.toml
    app.include_router(avatar_tuning.router, prefix="/api/v1/avatar/tuning", tags=["Avatar"])

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
