"""
Dashboard 服务器主类

负责启动 FastAPI 服务器，协调各子模块。
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from fastapi import WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from src.modules.dashboard.api.router import create_app, setup_cors
from src.modules.dashboard.config import DashboardConfig
from src.modules.dashboard.schemas.manager_protocol import ManagerStatusProvider
from src.modules.dashboard.widget import DanmakuWidgetService
from src.modules.dashboard.widget.models import DanmakuWidgetConfig, SubtitleWidgetConfig
from src.modules.logging import get_logger
from src.modules.logging.log_streamer import LogStreamer

try:
    import uvicorn

    UVICORN_AVAILABLE = True
except ImportError:
    UVICORN_AVAILABLE = False

if TYPE_CHECKING:
    from fastapi import FastAPI

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
        input_manager: Optional[ManagerStatusProvider],
        decision_manager: Optional[ManagerStatusProvider],
        output_manager: Optional[ManagerStatusProvider],
        context_service: "ContextService",
        config_service: "ConfigService",
        dashboard_config: DashboardConfig,
        log_streamer: Optional[LogStreamer] = None,
    ):
        self.event_bus = event_bus
        self.input_manager = input_manager
        self.decision_manager = decision_manager
        self.output_manager = output_manager
        self.context_service = context_service
        self.config_service = config_service

        self.port = dashboard_config.port
        self.host = dashboard_config.host
        self.cors_origins = dashboard_config.cors_origins
        self.max_history_messages = dashboard_config.max_history_messages
        self.websocket_heartbeat = dashboard_config.websocket_heartbeat
        self.dashboard_config = dashboard_config
        self.logger = get_logger("DashboardServer")

        self._is_running = False

        self.app: Optional["FastAPI"] = None
        self._server_task: Optional[asyncio.Task] = None

        self.ws_handler: Optional[WebSocketHandler] = None
        self.event_broadcaster: Optional[EventBroadcaster] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._external_log_streamer = log_streamer
        self.log_streamer: Optional[LogStreamer] = None

        self.widget_service: Optional[DanmakuWidgetService] = None
        self._widget_clients: set[WebSocket] = set()
        self._danmaku_clients: set[WebSocket] = set()
        self._subtitle_clients: set[WebSocket] = set()

        self.dev_mode: bool = dashboard_config.dev_mode
        self.vite_dev_port: int = dashboard_config.vite_dev_port
        self._vite_process: Optional[asyncio.subprocess.Process] = None

    async def start(self) -> None:
        """启动 Dashboard 服务器"""
        if not UVICORN_AVAILABLE:
            self.logger.warning("uvicorn 未安装，Dashboard 无法启动")
            self.logger.warning("请运行: uv add fastapi 'uvicorn[standard]'")
            return

        self.logger.info(f"Dashboard 服务器启动中: http://{self.host}:{self.port}")

        from src.modules.dashboard.dependencies import set_dashboard_server

        self.app = create_app()
        setup_cors(self.app, self.cors_origins)

        DASHBOARD_DIST = Path(__file__).parent.parent.parent.parent / "dashboard" / "dist"

        if DASHBOARD_DIST.exists() and DASHBOARD_DIST.is_dir():
            assets_dir = DASHBOARD_DIST / "assets"
            if assets_dir.exists():
                self.app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

            @self.app.get("/{full_path:path}")
            async def serve_spa(full_path: str):
                if full_path.startswith("api/") or full_path == "ws":
                    return {"detail": "Not Found"}
                return FileResponse(str(DASHBOARD_DIST / "index.html"))

            self.logger.info(f"前端静态文件已挂载: {DASHBOARD_DIST}")
        else:

            @self.app.get("/")
            async def root():
                return {
                    "name": "Amaidesu Dashboard API",
                    "version": "1.0.0",
                    "docs": "/docs",
                    "message": "前端未构建（dashboard/dist/ 不存在），开发模式请先执行 'cd dashboard && npm run dev' 并访问 http://localhost:60315",
                }

            self.logger.info("前端未构建，仅 API 模式运行")

        set_dashboard_server(self)

        # 初始化 WebSocket 处理器
        self.ws_handler = WebSocketHandler(heartbeat_interval=self.websocket_heartbeat)

        # 初始化事件广播器
        self.event_broadcaster = EventBroadcaster(
            event_bus=self.event_bus,
            ws_handler=self.ws_handler,
        )
        await self.event_broadcaster.start()

        # 初始化日志流广播器（使用外部传入的或创建新的）
        if self._external_log_streamer:
            # 外部传入的 log_streamer 需要更新 ws_handler
            self._external_log_streamer.ws_handler = self.ws_handler
            self.log_streamer = self._external_log_streamer
            # 如果还未启动，则启动
            if not hasattr(self.log_streamer, "_is_running") or not self.log_streamer._is_running:
                await self.log_streamer.start()
        else:
            self.log_streamer = LogStreamer(ws_handler=self.ws_handler, min_level="DEBUG")
            await self.log_streamer.start()

        # 添加 WebSocket 路由
        @self.app.websocket("/ws")
        async def websocket_endpoint(websocket: WebSocket):
            # 获取 client_id 用于推送历史日志
            client_id = await self.ws_handler.connect(websocket)
            # 推送历史日志
            if self.log_streamer:
                await self.log_streamer.broadcast_history(client_id)
            # 运行客户端消息处理循环
            try:
                while True:
                    message = await websocket.receive_text()
                    await self.ws_handler.handle_message(client_id, message)
            except WebSocketDisconnect:
                pass
            except Exception as e:
                self.logger.warning(f"WebSocket 消息循环异常（将清理连接）: {e}")
            finally:
                await self.ws_handler.disconnect(client_id)

        # 启动心跳任务
        self._heartbeat_task = asyncio.create_task(self._run_heartbeat())

        # 初始化弹幕叠加服务
        await self._setup_widget_service()

        self._is_running = True
        self.logger.info(f"Dashboard 已启动: {self.get_url()}")

        if self.dev_mode:
            await self._start_vite_dev_server()

        # 启动 uvicorn。
        # 显式指定 sansio 实现避免触发 websockets.legacy 弃用警告。
        # 注：uvicorn 0.49 仍未修复 auto.py 的 legacy 引用；uvicorn 0.50+ 将以字符串选项 'websockets-sansio' 公开支持，
        # 届时应改为 ws="websockets-sansio" 并删除此处导入。
        # 降级 websockets 到 13.x 不可行：uvicorn 引用了 websockets 14+ 才引入的 WebSocketServerProtocol。
        from uvicorn.protocols.websockets.websockets_sansio_impl import (
            WebSocketsSansIOProtocol,
        )

        config = uvicorn.Config(
            app=self.app,
            host=self.host,
            port=self.port,
            log_level="warning",
            ws=WebSocketsSansIOProtocol,
        )
        server = uvicorn.Server(config)
        self._server_task = asyncio.create_task(server.serve())

    async def stop(self) -> None:
        """停止 Dashboard 服务器"""
        self.logger.info("Dashboard 服务器停止中...")
        self._is_running = False

        # 停止弹幕小部件服务
        if self.widget_service:
            await self.widget_service.stop()
            self.widget_service = None

        # 关闭 widget WebSocket 客户端
        for client in list(self._widget_clients):
            try:
                await client.close()
            except Exception as e:
                self.logger.debug(f"关闭 widget WebSocket 客户端失败（已忽略）: {e}")
        self._widget_clients.clear()

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

        if self._vite_process:
            await self._stop_vite_dev_server()

    async def cleanup(self) -> None:
        """清理资源"""
        self.logger.info("Dashboard 清理资源中...")
        self.app = None
        self.event_broadcaster = None
        self.ws_handler = None
        self.log_streamer = None
        self.widget_service = None
        self._widget_clients.clear()

    def get_url(self) -> str:
        """获取访问 URL"""
        return f"http://{self.host}:{self.port}"

    def get_config_path(self) -> Optional[str]:
        """获取配置文件路径"""
        if self.config_service and hasattr(self.config_service, "base_dir"):
            return str(Path(self.config_service.base_dir) / "config.toml")
        return None

    async def _start_vite_dev_server(self) -> None:
        """启动 Vite 开发服务器子进程（开发模式专用）"""
        dashboard_dir = Path(__file__).parent.parent.parent.parent / "dashboard"
        if not dashboard_dir.exists():
            self.logger.error(f"dashboard 目录不存在: {dashboard_dir}")
            return

        npm_cmd = "npm.cmd" if sys.platform == "win32" else "npm"
        self.logger.info(f"开发模式：启动 Vite 开发服务器 (cwd={dashboard_dir}, port={self.vite_dev_port})")
        try:
            self._vite_process = await asyncio.create_subprocess_exec(
                npm_cmd,
                "run",
                "dev",
                cwd=str(dashboard_dir),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            self.logger.info(f"Vite 已启动 (pid={self._vite_process.pid})，访问 http://localhost:{self.vite_dev_port}")
        except FileNotFoundError:
            self.logger.error(f"未找到 {npm_cmd}，请先安装 Node.js 与 npm")
            self._vite_process = None
        except Exception as e:
            self.logger.error(f"启动 Vite 失败: {e}")
            self._vite_process = None

    async def _stop_vite_dev_server(self) -> None:
        """终止 Vite 开发服务器子进程"""
        if not self._vite_process:
            return
        self.logger.info(f"停止 Vite 开发服务器 (pid={self._vite_process.pid})...")
        try:
            self._vite_process.terminate()
            try:
                await asyncio.wait_for(self._vite_process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self.logger.warning("Vite 未在 5s 内退出，强制 kill")
                self._vite_process.kill()
                await self._vite_process.wait()
        except Exception as e:
            self.logger.error(f"停止 Vite 失败: {e}")
        finally:
            self._vite_process = None

    async def _run_heartbeat(self) -> None:
        """心跳任务"""
        while self._is_running:
            await asyncio.sleep(self.websocket_heartbeat)
            if self.ws_handler:
                await self.ws_handler.send_heartbeat()

    async def _setup_widget_service(self) -> None:
        """初始化弹幕小部件服务"""
        danmaku_widget_config = self.dashboard_config.danmaku_widget
        subtitle_widget_config = self.dashboard_config.subtitle_widget

        if danmaku_widget_config is None or not danmaku_widget_config.enabled:
            self.logger.info("弹幕小部件服务已禁用")
            return

        widget_config = DanmakuWidgetConfig(
            enabled=danmaku_widget_config.enabled,
            enable_html_page=danmaku_widget_config.enable_html_page,
            max_messages=danmaku_widget_config.max_messages,
            show_danmaku=danmaku_widget_config.show_danmaku,
            show_gift=danmaku_widget_config.show_gift,
            show_super_chat=danmaku_widget_config.show_super_chat,
            show_guard=danmaku_widget_config.show_guard,
            show_enter=danmaku_widget_config.show_enter,
            show_reply=danmaku_widget_config.show_reply,
            min_importance=danmaku_widget_config.min_importance,
        )

        subtitle_config = SubtitleWidgetConfig(
            enabled=subtitle_widget_config.enabled,
            enable_html_page=subtitle_widget_config.enable_html_page,
            max_messages=subtitle_widget_config.max_messages,
            auto_hide_after_ms=subtitle_widget_config.auto_hide_after_ms,
            font_size=subtitle_widget_config.font_size,
            font_color=subtitle_widget_config.font_color,
            background_color=subtitle_widget_config.background_color,
            border_color=subtitle_widget_config.border_color,
            position=subtitle_widget_config.position,
        )

        self.widget_service = DanmakuWidgetService(
            event_bus=self.event_bus,
            config=widget_config,
            subtitle_config=subtitle_config,
        )

        self.widget_service.set_danmaku_callback(self._broadcast_to_danmaku_clients)
        self.widget_service.set_subtitle_callback(self._broadcast_to_subtitle_clients)

        await self.widget_service.start()
        self.logger.info(f"弹幕小部件服务已启动 (max_messages={widget_config.max_messages})")

        if widget_config.enable_html_page:

            @self.app.get("/widget", response_class=HTMLResponse)
            async def widget_page():
                return self._get_widget_html()

        @self.app.websocket("/ws/danmaku")
        async def danmaku_websocket(websocket: WebSocket):
            await websocket.accept()
            self._danmaku_clients.add(websocket)

            try:
                history = self.widget_service.get_recent_messages(15)
                await websocket.send_json({"type": "history", "messages": history})

                while True:
                    await websocket.receive_text()
            except WebSocketDisconnect:
                pass
            except Exception as e:
                self.logger.debug(f"Danmaku WebSocket 错误: {e}")
            finally:
                self._danmaku_clients.discard(websocket)

        @self.app.websocket("/ws/subtitle")
        async def subtitle_websocket(websocket: WebSocket):
            await websocket.accept()
            self._subtitle_clients.add(websocket)

            try:
                while True:
                    await websocket.receive_text()
            except WebSocketDisconnect:
                pass
            except Exception as e:
                self.logger.debug(f"Subtitle WebSocket 错误: {e}")
            finally:
                self._subtitle_clients.discard(websocket)

        @self.app.websocket("/ws/widget")
        async def widget_websocket(websocket: WebSocket):
            await websocket.accept()
            self._widget_clients.add(websocket)

            try:
                history = self.widget_service.get_recent_messages(15)
                await websocket.send_json({"type": "history", "messages": history})

                while True:
                    await websocket.receive_text()
            except WebSocketDisconnect:
                pass
            except Exception as e:
                self.logger.debug(f"Widget WebSocket 错误: {e}")
            finally:
                self._widget_clients.discard(websocket)

        # 添加 widget API 端点
        @self.app.get("/api/widget/messages")
        async def get_widget_messages():
            return {"messages": self.widget_service.get_recent_messages(15)}

        @self.app.get("/api/widget/subtitles")
        async def get_widget_subtitles():
            return {"subtitles": self.widget_service.get_recent_subtitles(5)}

        @self.app.get("/api/widget/stats")
        async def get_widget_stats():
            return self.widget_service.get_stats()

        self.logger.info("弹幕小部件路由已注册: /danmaku, /subtitle, /widget, /ws/danmaku, /ws/subtitle, /ws/widget")

    async def _broadcast_to_danmaku_clients(self, data: dict) -> None:
        """广播弹幕消息到所有 danmaku 客户端"""
        if not self._danmaku_clients:
            return

        message = json.dumps(data, ensure_ascii=False, default=str)
        disconnected = set()

        for client in self._danmaku_clients:
            try:
                await client.send_text(message)
            except Exception as e:
                self.logger.debug(f"广播时客户端已断开: {e}")
                disconnected.add(client)

        self._danmaku_clients -= disconnected

    async def _broadcast_to_subtitle_clients(self, data: dict) -> None:
        """广播字幕到所有 subtitle 客户端"""
        if not self._subtitle_clients:
            return

        message = json.dumps(data, ensure_ascii=False, default=str)
        disconnected = set()

        for client in self._subtitle_clients:
            try:
                await client.send_text(message)
            except Exception as e:
                self.logger.debug(f"广播时客户端已断开: {e}")
                disconnected.add(client)

        self._subtitle_clients -= disconnected

    async def _broadcast_to_widget_clients(self, data: dict) -> None:
        """广播消息到所有 widget 客户端"""
        if not self._widget_clients:
            return

        message = json.dumps(data, ensure_ascii=False, default=str)
        disconnected = set()

        for client in self._widget_clients:
            try:
                await client.send_text(message)
            except Exception as e:
                self.logger.debug(f"广播时客户端已断开: {e}")
                disconnected.add(client)

        self._widget_clients -= disconnected

    def _get_widget_html(self) -> str:
        """返回 widget 页面 HTML"""
        return """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>弹幕小部件</title>
    <style>
        html, body {
            background: transparent !important;
            margin: 0;
            padding: 15px 20px;
            font-family: 'Microsoft YaHei', 'PingFang SC', sans-serif;
            color: #fff;
            overflow: hidden;
            text-shadow: 1px 1px 2px rgba(0,0,0,0.8);
        }
        #messages {
            display: flex;
            flex-direction: column;
            gap: 8px;
        }
        .message {
            padding: 8px 14px;
            border-radius: 6px;
            animation: slideIn 0.4s ease-out;
            backdrop-filter: blur(4px);
        }
        .message.danmaku {
            background: rgba(0, 0, 0, 0.45);
            border-left: 3px solid #00ff88;
        }
        .message.gift {
            background: rgba(255, 136, 0, 0.35);
            border-left: 3px solid #ff8800;
        }
        .message.superchat {
            background: linear-gradient(90deg, rgba(255,107,107,0.4), rgba(107,255,107,0.3));
            border-left: 3px solid #ff6b6b;
            animation: slideIn 0.4s ease-out, rainbow 3s ease infinite;
            background-size: 200% 200%;
        }
        .message.guard {
            background: rgba(78, 205, 196, 0.35);
            border-left: 3px solid #4ecdc4;
        }
        .message.enter {
            background: rgba(100, 100, 100, 0.3);
            border-left: 3px solid #888;
        }
        .username {
            color: #00ff88;
            font-weight: 600;
            margin-right: 6px;
        }
        .content {
            color: #fff;
        }
        @keyframes slideIn {
            from { opacity: 0; transform: translateX(-20px); }
            to { opacity: 1; transform: translateX(0); }
        }
        @keyframes rainbow {
            0%, 100% { background-position: 0% 50%; }
            50% { background-position: 100% 50%; }
        }
    </style>
</head>
<body>
    <div id="messages"></div>
    <script>
        const container = document.getElementById('messages');
        const maxMessages = 15;
        let ws;

        function connect() {
            const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
            ws = new WebSocket(protocol + '//' + location.host + '/ws/widget');

            ws.onmessage = (event) => {
                const data = JSON.parse(event.data);
                if (data.type === 'new_message') {
                    addMessage(data.message);
                } else if (data.type === 'history') {
                    container.innerHTML = '';
                    data.messages.forEach(addMessage);
                }
            };

            ws.onclose = () => setTimeout(connect, 3000);
            ws.onerror = () => ws.close();
        }

        function addMessage(msg) {
            const div = document.createElement('div');
            div.className = 'message ' + msg.message_type;
            div.innerHTML = '<span class="username">' + escapeHtml(msg.user_name) + '：</span>' +
                           '<span class="content">' + escapeHtml(msg.content) + '</span>';
            container.appendChild(div);

            while (container.children.length > maxMessages) {
                container.removeChild(container.firstChild);
            }
        }

        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text || '';
            return div.innerHTML;
        }

        connect();
    </script>
</body>
</html>"""
