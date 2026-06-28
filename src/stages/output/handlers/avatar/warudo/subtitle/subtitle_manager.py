"""
Warudo 字幕管理器 - 完整的 ReplyGenerationManager 移植

迁移自旧插件 plugins_backup/warudo/talk_subtitle.py,适配新架构:
- 移除全局单例(get_reply_generation_manager) — 新架构用 DI
- 移除 aiohttp_cors(简化,OBS 浏览器源不需要复杂 CORS)
- 保持 aiohttp 启动 / WebSocket 广播 / 4 种消息协议完整
- 3 秒超时保护关闭逻辑保留

WebSocket 协议(JSON 消息):
- {action: "start", user_name: str}       - 开始新回复
- {action: "chunk", content: str}          - 增量追加内容
- {action: "complete"}                     - 完成(移除光标)
- {action: "clear"}                        - 清空

HTTP 路由:
- GET /                    - 字幕 HTML 页面(OBS 浏览器源)
- GET /ws                  - WebSocket 端点
- GET /api/current-reply   - 当前状态 JSON 查询
"""

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from aiohttp import WSMsgType, web

from src.modules.logging import get_logger
from src.stages.output.handlers.avatar.warudo.subtitle.templates import render_subtitle_html


class WarudoSubtitleManager:
    """Warudo 字幕管理器

    启动 aiohttp HTTP + WebSocket 服务器,为 OBS 浏览器源提供实时字幕推送。
    """

    def __init__(
        self,
        port: int = 8766,
        show_status: bool = False,
        logger: Optional[logging.Logger] = None,
    ):
        """
        初始化字幕管理器

        Args:
            port: HTTP 端口
            show_status: HTML 页面是否显示连接状态徽章
            logger: 日志器
        """
        self.port = port
        self.show_status = show_status
        self.logger = logger or get_logger("WarudoSubtitle")

        self.websockets: List[web.WebSocketResponse] = []
        self.app: Optional[web.Application] = None
        self.runner: Optional[web.AppRunner] = None
        self.site: Optional[web.TCPSite] = None
        self._server_starting: bool = False
        self.current_reply: str = ""
        self.current_user: str = ""

        self.logger.info(f"WarudoSubtitleManager 已初始化 (port={port}, show_status={show_status})")

    async def start_server(self) -> None:
        """启动 HTTP + WebSocket 服务器

        重复启动会被跳过;启动失败时清理资源并抛出异常。
        """
        if self.site is not None:
            self.logger.debug("字幕 Web 服务器已经启动,跳过重复启动")
            return

        if self._server_starting:
            self.logger.debug("字幕 Web 服务器正在启动中,等待...")
            while self._server_starting and self.site is None:
                await asyncio.sleep(0.1)
            return

        self._server_starting = True
        try:
            self.app = web.Application()
            self.app.router.add_get("/", self._reply_index_handler)
            self.app.router.add_get("/ws", self._reply_websocket_handler)
            self.app.router.add_get("/api/current-reply", self._get_current_reply_handler)

            self.runner = web.AppRunner(self.app)
            await self.runner.setup()
            self.site = web.TCPSite(self.runner, "localhost", self.port)
            await self.site.start()
            self.logger.info(f"🌐 字幕 Web 服务器已启动: http://localhost:{self.port}")
        except Exception as e:
            self.logger.error(f"启动字幕 Web 服务器失败: {e}")
            if self.runner:
                try:
                    await self.runner.cleanup()
                except Exception:
                    pass
            self.app = None
            self.runner = None
            self.site = None
            raise
        finally:
            self._server_starting = False

    async def stop_server(self) -> None:
        """停止服务器(3 秒超时保护)"""
        self.logger.info("正在停止字幕 Web 服务器...")

        try:
            # 1. 关闭所有 WebSocket
            websockets_copy = self.websockets.copy()
            close_tasks = []
            for ws in websockets_copy:
                if not ws.closed:
                    close_tasks.append(asyncio.create_task(self._close_websocket_safely(ws)))

            if close_tasks:
                try:
                    await asyncio.wait_for(asyncio.gather(*close_tasks, return_exceptions=True), timeout=3.0)
                except asyncio.TimeoutError:
                    self.logger.warning("WebSocket 关闭超时,强制继续")
            self.websockets.clear()

            # 2. 停止 site
            if self.site:
                try:
                    await asyncio.wait_for(self.site.stop(), timeout=3.0)
                except asyncio.TimeoutError:
                    self.logger.warning("TCPSite 停止超时,强制继续")

            # 3. 清理 runner
            if self.runner:
                try:
                    await asyncio.wait_for(self.runner.cleanup(), timeout=3.0)
                except asyncio.TimeoutError:
                    self.logger.warning("AppRunner 清理超时,强制继续")
        except Exception as e:
            self.logger.error(f"停止字幕服务器异常: {e}")
        finally:
            self.app = None
            self.runner = None
            self.site = None
            self._server_starting = False
            self.logger.info("字幕 Web 服务器已完全停止")

    async def _close_websocket_safely(self, ws: web.WebSocketResponse) -> None:
        try:
            await ws.close()
        except Exception as e:
            self.logger.error(f"关闭 WebSocket 连接异常: {e}")

    async def _reply_index_handler(self, request: web.Request) -> web.Response:
        """GET / - 返回字幕 HTML 页面"""
        html_content = render_subtitle_html(show_status=self.show_status, port=self.port)
        return web.Response(text=html_content, content_type="text/html")

    async def _reply_websocket_handler(self, request: web.Request) -> web.WebSocketResponse:
        """GET /ws - WebSocket 端点"""
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.websockets.append(ws)
        self.logger.debug(f"字幕 WebSocket 连接建立,当前连接数: {len(self.websockets)}")

        try:
            async for msg in ws:
                if msg.type == WSMsgType.ERROR:
                    self.logger.error(f"字幕 WebSocket 错误: {ws.exception()}")
                    break
        finally:
            if ws in self.websockets:
                self.websockets.remove(ws)
            self.logger.debug(f"字幕 WebSocket 连接断开,当前连接数: {len(self.websockets)}")

        return ws

    async def _get_current_reply_handler(self, request: web.Request) -> web.Response:
        """GET /api/current-reply - 当前状态 JSON"""
        if self.current_reply:
            return web.json_response(
                {
                    "action": "chunk",
                    "user_name": self.current_user,
                    "content": self.current_reply,
                }
            )
        return web.json_response({"action": "clear"})

    # ==================== 公共方法 ====================

    async def start_generation(self, user_name: str) -> None:
        """开始新回复(清空旧内容,广播 start)"""
        await self.clear_generation()
        self.current_user = user_name
        self.current_reply = ""
        await self._broadcast_to_websockets({"action": "start", "user_name": user_name})
        self.logger.info(f"开始为 {user_name} 生成回复")

    async def add_chunk(self, chunk: str) -> None:
        """追加内容(广播 chunk)"""
        self.current_reply += chunk
        await self._broadcast_to_websockets({"action": "chunk", "content": chunk})
        self.logger.debug(f"追加字幕 chunk ({len(chunk)} 字符)")

    async def complete_generation(self) -> None:
        """完成(广播 complete,前端移除光标)"""
        await self._broadcast_to_websockets({"action": "complete"})
        self.logger.info(f"字幕生成完成,总长度 {len(self.current_reply)} 字符")

    async def clear_generation(self) -> None:
        """清空当前内容"""
        self.current_reply = ""
        self.current_user = ""
        await self._broadcast_to_websockets({"action": "clear"})
        self.logger.info("字幕内容已清空")

    async def _broadcast_to_websockets(self, data: Dict[str, Any]) -> None:
        """向所有已连接 WebSocket 广播数据"""
        if not self.websockets:
            return

        message = json.dumps(data, ensure_ascii=False)
        websockets_copy = self.websockets.copy()
        removed_count = 0

        for ws in websockets_copy:
            if ws.closed:
                if ws in self.websockets:
                    self.websockets.remove(ws)
                    removed_count += 1
            else:
                try:
                    await ws.send_str(message)
                except Exception as e:
                    self.logger.error(f"广播字幕消息失败: {e}")
                    if ws in self.websockets:
                        self.websockets.remove(ws)
                        removed_count += 1

        if removed_count > 0:
            self.logger.debug(f"清理了 {removed_count} 个断开的 WebSocket 连接")
