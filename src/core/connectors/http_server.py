"""
HTTP服务器模块（基于FastAPI）

提供独立的HTTP服务器，支持Provider注册路由。
由AmaidesuCore管理生命周期。

用法:
    server = HttpServer(host="0.0.0.0", port=8080)
    server.register_route("/callback", handler, methods=["POST"])
    await server.start()
    # ...
    await server.stop()
"""

import asyncio
from typing import Callable, Dict, List, Optional, Any

from src.core.utils.logger import get_logger

# 延迟导入以避免导入时的依赖问题
_fastapi_available = False
_uvicorn_available = False

try:
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    _fastapi_available = True
except ImportError:
    pass

try:
    import uvicorn

    _uvicorn_available = True
except ImportError:
    pass


class HttpServerError(Exception):
    """HTTP服务器错误"""

    pass


class HttpServer:
    """
    HTTP服务器（基于FastAPI）

    提供独立的HTTP服务器，支持:
    - Provider注册路由
    - 健康检查接口
    - 异步启动/停止

    Attributes:
        host: 监听地址
        port: 监听端口
        app: FastAPI应用实例
    """

    def __init__(self, host: str = "0.0.0.0", port: int = 8080, title: str = "Amaidesu HTTP Server"):
        """
        初始化HTTP服务器

        Args:
            host: 监听地址
            port: 监听端口
            title: API文档标题
        """
        self.host = host
        self.port = port
        self.title = title
        self.logger = get_logger("HttpServer")

        # 检查依赖
        if not _fastapi_available:
            self.logger.warning("FastAPI未安装，HTTP服务器将不可用。请安装: pip install fastapi")
            self.app = None
            self._available = False
            return

        if not _uvicorn_available:
            self.logger.warning("uvicorn未安装，HTTP服务器将不可用。请安装: pip install uvicorn")
            self.app = None
            self._available = False
            return

        self._available = True

        # 创建FastAPI应用
        self.app = FastAPI(
            title=title,
            description="Amaidesu HTTP Server - 支持Provider注册路由的HTTP服务器",
            version="1.0.0",
        )

        # 路由注册表
        self._routes: Dict[str, Dict[str, Any]] = {}

        # 服务器状态
        self._server: Optional[uvicorn.Server] = None
        self._server_task: Optional[asyncio.Task] = None
        self._is_running = False

        # 添加默认健康检查
        self._add_default_routes()

        self.logger.debug(f"HttpServer初始化完成 (host={host}, port={port})")

    @property
    def is_available(self) -> bool:
        """检查HTTP服务器是否可用（依赖已安装）"""
        return self._available

    @property
    def is_running(self) -> bool:
        """检查HTTP服务器是否正在运行"""
        return self._is_running

    def _add_default_routes(self) -> None:
        """添加默认路由"""
        if not self.app:
            return

        # 健康检查
        @self.app.get("/health", tags=["System"])
        async def health_check():
            """健康检查接口"""
            return {"status": "ok", "service": "amaidesu"}

        # 已注册路由列表
        @self.app.get("/routes", tags=["System"])
        async def list_routes():
            """列出所有已注册的路由"""
            return {
                "routes": [
                    {"path": path, "methods": info.get("methods", ["GET"])} for path, info in self._routes.items()
                ]
            }

        self.logger.debug("默认路由已添加 (/health, /routes)")

    def register_route(
        self,
        path: str,
        handler: Callable,
        methods: Optional[List[str]] = None,
        name: Optional[str] = None,
        tags: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> bool:
        """
        注册HTTP路由

        Args:
            path: 路径（如 "/maicore/callback"）
            handler: 处理函数（异步函数，接收 Request 返回 Response 或 dict）
            methods: 允许的HTTP方法（如 ["GET", "POST"]），默认为 ["GET"]
            name: 路由名称（用于文档）
            tags: 标签（用于API文档分组）
            **kwargs: 传递给 FastAPI.add_api_route 的其他参数

        Returns:
            是否注册成功
        """
        if not self.app:
            self.logger.warning(f"HTTP服务器不可用，无法注册路由: {path}")
            return False

        if path in self._routes:
            self.logger.warning(f"路由 {path} 已存在，将被覆盖")

        methods = methods or ["GET"]
        tags = tags or ["Provider"]

        # 记录路由
        self._routes[path] = {
            "handler": handler,
            "methods": methods,
            "name": name,
            "tags": tags,
        }

        # 注册到FastAPI
        try:
            self.app.add_api_route(
                path,
                handler,
                methods=methods,
                name=name,
                tags=tags,  # type: ignore[arg-type]
                response_class=JSONResponse,
                **kwargs,
            )
            self.logger.info(f"HTTP路由已注册: {methods} {path}")
            return True
        except Exception as e:
            self.logger.error(f"注册HTTP路由失败 {path}: {e}", exc_info=True)
            return False

    async def start(self) -> None:
        """
        启动HTTP服务器

        Raises:
            HttpServerError: 启动失败时
        """
        if not self._available:
            self.logger.warning("HTTP服务器依赖未安装，跳过启动")
            return

        if self._is_running:
            self.logger.warning("HTTP服务器已在运行")
            return

        try:
            # 确保 app 存在（在 _available=True 时一定存在）
            assert self.app is not None, "FastAPI app 未初始化"

            # 配置uvicorn
            config = uvicorn.Config(
                self.app,
                host=self.host,
                port=self.port,
                log_level="warning",  # 减少uvicorn日志输出
                access_log=False,
            )
            self._server = uvicorn.Server(config)

            # 启动服务器任务
            self._server_task = asyncio.create_task(self._server.serve(), name="HttpServerTask")
            self._is_running = True

            self.logger.info(f"HTTP服务器已启动: http://{self.host}:{self.port}")
            self.logger.info(f"API文档: http://{self.host}:{self.port}/docs")

        except Exception as e:
            self._is_running = False
            raise HttpServerError(f"启动HTTP服务器失败: {e}") from e

    async def stop(self) -> None:
        """停止HTTP服务器"""
        if not self._is_running:
            return

        self.logger.info("正在停止HTTP服务器...")

        try:
            # 通知uvicorn服务器关闭
            if self._server:
                self._server.should_exit = True

            # 等待任务完成或取消
            if self._server_task:
                try:
                    await asyncio.wait_for(self._server_task, timeout=5.0)
                except asyncio.TimeoutError:
                    self.logger.warning("HTTP服务器停止超时，强制取消")
                    self._server_task.cancel()
                    try:
                        await self._server_task
                    except asyncio.CancelledError:
                        pass
                except asyncio.CancelledError:
                    pass

        except Exception as e:
            self.logger.error(f"停止HTTP服务器时出错: {e}", exc_info=True)
        finally:
            self._server = None
            self._server_task = None
            self._is_running = False
            self.logger.info("HTTP服务器已停止")

    def get_stats(self) -> Dict[str, Any]:
        """
        获取服务器统计信息

        Returns:
            统计信息字典
        """
        return {
            "available": self._available,
            "running": self._is_running,
            "host": self.host,
            "port": self.port,
            "routes_count": len(self._routes),
            "routes": list(self._routes.keys()),
        }
