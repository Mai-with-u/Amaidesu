"""
Dashboard 服务器主类

负责启动 FastAPI 服务器，协调各子模块。
"""

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from fastapi import WebSocket
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.modules.dashboard.api.router import create_app, setup_cors
from src.modules.logging import get_logger
from src.modules.logging.log_streamer import LogStreamer

try:
    import uvicorn

    UVICORN_AVAILABLE = True
except ImportError:
    UVICORN_AVAILABLE = False

if TYPE_CHECKING:
    from fastapi import FastAPI

    from src.domains.decision.provider_manager import DecisionProviderManager
    from src.domains.input.provider_manager import InputProviderManager
    from src.domains.output.provider_manager import OutputProviderManager
    from src.modules.config.service import ConfigService
    from src.modules.context.service import ContextService
    from src.modules.events.event_bus import EventBus

from src.modules.dashboard.websocket.broadcaster import EventBroadcaster
from src.modules.dashboard.websocket.handler import WebSocketHandler


class DashboardServer:
    """Dashboard 服务器主类"""

    def __init__(
        self,
        event_bus: "EventBus",
        input_manager: Optional["InputProviderManager"],
        decision_manager: Optional["DecisionProviderManager"],
        output_manager: Optional["OutputProviderManager"],
        context_service: "ContextService",
        config_service: "ConfigService",
        port: int = 60214,
        host: str = "127.0.0.1",
        cors_origins: Optional[list[str]] = None,
        max_history_messages: int = 1000,
        websocket_heartbeat: int = 30,
    ):
        self.event_bus = event_bus
        self.input_manager = input_manager
        self.decision_manager = decision_manager
        self.output_manager = output_manager
        self.context_service = context_service
        self.config_service = config_service
        self.port = port
        self.host = host
        self.logger = get_logger("DashboardServer")

        # 配置
        self.cors_origins = cors_origins or ["http://localhost:5173", "http://127.0.0.1:5173"]
        self.max_history_messages = max_history_messages
        self.websocket_heartbeat = websocket_heartbeat

        # 状态
        self._is_running = False

        # FastAPI 应用
        self.app: Optional["FastAPI"] = None
        self._server_task: Optional[asyncio.Task] = None

        # WebSocket 处理器
        self.ws_handler: Optional[WebSocketHandler] = None
        self.event_broadcaster: Optional[EventBroadcaster] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self.log_streamer: Optional[LogStreamer] = None

    async def start(self) -> None:
        """启动 Dashboard 服务器"""
        if not UVICORN_AVAILABLE:
            self.logger.warning("uvicorn 未安装，Dashboard 无法启动")
            self.logger.warning("请运行: uv add fastapi 'uvicorn[standard]'")
            return

        self.logger.info(f"Dashboard 服务器启动中: http://{self.host}:{self.port}")

        # 延迟导入依赖注入工具，避免循环导入
        from src.modules.dashboard.dependencies import set_dashboard_server

        # 创建 FastAPI 应用
        self.app = create_app()
        setup_cors(self.app, self.cors_origins)

        # 前端静态文件目录
        DASHBOARD_DIST = Path(__file__).parent.parent.parent.parent / "dashboard" / "dist"

        # 检查前端构建产物是否存在
        if DASHBOARD_DIST.exists() and DASHBOARD_DIST.is_dir():
            # 挂载静态资源目录
            assets_dir = DASHBOARD_DIST / "assets"
            if assets_dir.exists():
                self.app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

            # SPA fallback - 所有非 API/WS 路由返回 index.html
            @self.app.get("/{full_path:path}")
            async def serve_spa(full_path: str):
                """SPA fallback - 返回 index.html"""
                # 排除 API 和 WebSocket 路径
                if full_path.startswith("api/") or full_path == "ws":
                    return {"detail": "Not Found"}
                return FileResponse(str(DASHBOARD_DIST / "index.html"))

            self.logger.info(f"前端静态文件已挂载: {DASHBOARD_DIST}")
        else:
            # 添加根路径处理（开发模式）
            @self.app.get("/")
            async def root():
                return {
                    "name": "Amaidesu Dashboard API",
                    "version": "1.0.0",
                    "docs": "/docs",
                    "message": "前端未构建，开发模式请访问 http://localhost:5173",
                }

            self.logger.info("前端未构建，仅 API 模式运行")

        # 设置依赖注入
        set_dashboard_server(self)

        # 初始化 WebSocket 处理器
        self.ws_handler = WebSocketHandler(heartbeat_interval=self.websocket_heartbeat)

        # 初始化事件广播器
        self.event_broadcaster = EventBroadcaster(
            event_bus=self.event_bus,
            ws_handler=self.ws_handler,
        )
        await self.event_broadcaster.start()

        # 初始化日志流广播器
        self.log_streamer = LogStreamer(ws_handler=self.ws_handler, min_level="DEBUG")
        await self.log_streamer.start()

        # 添加 WebSocket 路由
        @self.app.websocket("/ws")
        async def websocket_endpoint(websocket: WebSocket):
            await self.ws_handler.run_client_handler(websocket)

        # 启动心跳任务
        self._heartbeat_task = asyncio.create_task(self._run_heartbeat())

        self._is_running = True
        self.logger.info(f"Dashboard 已启动: {self.get_url()}")

        # 启动 uvicorn
        config = uvicorn.Config(
            app=self.app,
            host=self.host,
            port=self.port,
            log_level="warning",
        )
        server = uvicorn.Server(config)
        self._server_task = asyncio.create_task(server.serve())

    async def stop(self) -> None:
        """停止 Dashboard 服务器"""
        self.logger.info("Dashboard 服务器停止中...")
        self._is_running = False

        # 停止日志流广播器
        if self.log_streamer:
            await self.log_streamer.stop()
            self.log_streamer = None

        # 停止事件广播器
        if self.event_broadcaster:
            await self.event_broadcaster.stop()
            self.event_broadcaster = None

        # 停止心跳任务
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None

        if self._server_task:
            self._server_task.cancel()
            try:
                await self._server_task
            except asyncio.CancelledError:
                pass
            self._server_task = None

    async def cleanup(self) -> None:
        """清理资源"""
        self.logger.info("Dashboard 清理资源中...")
        self.app = None
        self.event_broadcaster = None
        self.ws_handler = None
        self.log_streamer = None

    def get_url(self) -> str:
        """获取访问 URL"""
        return f"http://{self.host}:{self.port}"

    def get_config_path(self) -> Optional[str]:
        """获取配置文件路径"""
        if self.config_service and hasattr(self.config_service, "base_dir"):
            return str(Path(self.config_service.base_dir) / "config.toml")
        return None

    async def _run_heartbeat(self) -> None:
        """心跳任务"""
        while self._is_running:
            await asyncio.sleep(self.websocket_heartbeat)
            if self.ws_handler:
                await self.ws_handler.send_heartbeat()
