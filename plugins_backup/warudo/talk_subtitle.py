import asyncio
import json
import logging
from typing import List, Optional

import aiohttp_cors
from aiohttp import WSMsgType, web

logger = logging.getLogger("reply_web")


class ReplyGenerationManager:
    """回复生成实时展示管理器"""

    def __init__(self, port: int = 8767, show_status: bool = False, logger: logging.Logger = logger):
        self.logger = logger
        self.port = port
        self.show_status = show_status  # 是否显示连接状态
        self.websockets: List[web.WebSocketResponse] = []
        self.app = None
        self.runner = None
        self.site = None
        self._server_starting = False
        self.current_reply = ""  # 当前正在生成的回复内容
        self.current_user = ""  # 当前正在回复的用户
        self.logger = logger

    async def start_server(self):
        """启动回复生成web服务器"""
        if self.site is not None:
            logger.debug("回复生成Web服务器已经启动，跳过重复启动")
            return

        if self._server_starting:
            logger.debug("回复生成Web服务器正在启动中，等待启动完成...")
            while self._server_starting and self.site is None:
                await asyncio.sleep(0.1)
            return

        self._server_starting = True

        try:
            self.app = web.Application()

            # 设置CORS
            cors = aiohttp_cors.setup(
                self.app,
                defaults={
                    "*": aiohttp_cors.ResourceOptions(
                        allow_credentials=True, expose_headers="*", allow_headers="*", allow_methods="*"
                    )
                },
            )

            # 添加路由
            self.app.router.add_get("/", self.reply_index_handler)
            self.app.router.add_get("/ws", self.reply_websocket_handler)
            self.app.router.add_get("/api/current-reply", self.get_current_reply_handler)

            # 为所有路由添加CORS
            for route in list(self.app.router.routes()):
                cors.add(route)

            self.runner = web.AppRunner(self.app)
            await self.runner.setup()

            self.site = web.TCPSite(self.runner, "localhost", self.port)
            await self.site.start()

            logger.info(f"🌐 回复生成网页服务器启动成功在 http://localhost:{self.port}")

        except Exception as e:
            logger.error(f"❌ 启动回复生成Web服务器失败: {e}")
            if self.runner:
                await self.runner.cleanup()
            self.app = None
            self.runner = None
            self.site = None
            raise
        finally:
            self._server_starting = False

    async def stop_server(self):
        """停止回复生成web服务器"""
        logger.info("正在停止回复生成Web服务器...")

        try:
            # 首先关闭所有WebSocket连接
            websockets_copy = self.websockets.copy()
            close_tasks = []
            for ws in websockets_copy:
                if not ws.closed:
                    close_tasks.append(asyncio.create_task(self._close_websocket_safely(ws)))

            # 等待所有WebSocket关闭，但设置超时
            if close_tasks:
                try:
                    await asyncio.wait_for(asyncio.gather(*close_tasks, return_exceptions=True), timeout=3.0)
                except asyncio.TimeoutError:
                    self.logger.warning("WebSocket关闭超时，强制继续")

            # 清空WebSocket列表
            self.websockets.clear()
            self.logger.debug("已清空所有WebSocket连接")

            # 停止服务器
            if self.site:
                try:
                    await asyncio.wait_for(self.site.stop(), timeout=3.0)
                    self.logger.debug("TCPSite已停止")
                except asyncio.TimeoutError:
                    self.logger.warning("TCPSite停止超时，强制继续")

            if self.runner:
                try:
                    await asyncio.wait_for(self.runner.cleanup(), timeout=3.0)
                    self.logger.debug("AppRunner已清理")
                except asyncio.TimeoutError:
                    self.logger.warning("AppRunner清理超时，强制继续")

        except Exception as e:
            logger.error(f"停止服务器时出现异常: {e}")
        finally:
            # 无论如何都要重置所有变量
            self.app = None
            self.runner = None
            self.site = None
            self._server_starting = False

            self.logger.info("回复生成Web服务器已完全停止")

    async def _close_websocket_safely(self, ws):
        """安全关闭WebSocket连接"""
        try:
            await ws.close()
            self.logger.debug("关闭WebSocket连接")
        except Exception as e:
            self.logger.error(f"关闭WebSocket连接时出错: {e}")

    async def reply_index_handler(self, request):
        """回复生成主页处理器"""
        html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>回复生成实时展示</title>
    <style>
        html, body {{
            background: transparent !important;
            background-color: transparent !important;
            margin: 0;
            padding: 20px;
            font-family: 'Microsoft YaHei', Arial, sans-serif;
            color: #ffffff;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.8);
            height: 100vh;
            overflow: hidden;
        }}
        .container {{
            max-width: 800px;
            margin: 0 auto;
            background: transparent !important;
            height: 100%;
            display: flex;
            flex-direction: column;
        }}
        .status {{
            position: fixed;
            top: 20px;
            right: 20px;
            background: rgba(0, 0, 0, 0.7);
            color: #888;
            font-size: 12px;
            padding: 8px 12px;
            border-radius: 20px;
            backdrop-filter: blur(10px);
            z-index: 1000;
            display: {"block" if self.show_status else "none"};
        }}
        .user-info {{
            display: none;  /* 完全隐藏包含状态点的区域 */
        }}
        .status-dot {{
            display: none;  /* 隐藏状态点 */
        }}
        .status-dot.generating {{
            display: none;
        }}
        .status-dot.idle {{
            display: none;
        }}
        .reply-content {{
            background: rgba(0, 0, 0, 0.3);
            padding: 20px;
            border-radius: 10px;
            border-left: 4px solid #ff8800;
            backdrop-filter: blur(5px);
            flex-grow: 1;
            overflow-y: auto;
            font-size: 45px;  /* 增大字体从24px到32px */
            line-height: 1.6;
            word-wrap: break-word;
            white-space: pre-wrap;
        }}
        .no-generation {{
            display: none;  /* 隐藏"暂无正在生成的回复"提示 */
        }}
        .typing-indicator {{
            display: inline-block;
            animation: blink 1s infinite;
        }}
        @keyframes blink {{
            0%, 50% {{ opacity: 1; }}
            51%, 100% {{ opacity: 0; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="status" id="status">{"正在连接..." if self.show_status else ""}</div>
        <div class="user-info" id="user-info">
            <div class="status-dot idle" id="status-dot"></div>
        </div>
        <div class="reply-content" id="reply-content">
            <!-- 初始状态保持空白 -->
        </div>
    </div>

    <script>
        let ws;
        let reconnectInterval;
        
        function connectWebSocket() {{
            console.log('正在连接WebSocket...');
            ws = new WebSocket('ws://localhost:{self.port}/ws');
            
            ws.onopen = function() {{
                console.log('WebSocket连接已建立');
                {('const statusEl = document.getElementById("status"); if (statusEl && statusEl.style.display !== "none") {{ statusEl.textContent = "✅ 已连接"; statusEl.style.color = "#ff8800"; }}' if self.show_status else "")}
                if (reconnectInterval) {{
                    clearInterval(reconnectInterval);
                    reconnectInterval = null;
                }}
            }};
            
            ws.onmessage = function(event) {{
                console.log('收到WebSocket消息:', event.data);
                try {{
                    const data = JSON.parse(event.data);
                    updateReply(data);
                }} catch (e) {{
                    console.error('解析消息失败:', e, event.data);
                }}
            }};
            
            ws.onclose = function(event) {{
                console.log('WebSocket连接关闭:', event.code, event.reason);
                {('const statusEl = document.getElementById("status"); if (statusEl && statusEl.style.display !== "none") {{ statusEl.textContent = "❌ 连接断开，正在重连..."; statusEl.style.color = "#ff6b6b"; }}' if self.show_status else "")}
                
                if (!reconnectInterval) {{
                    reconnectInterval = setInterval(connectWebSocket, 3000);
                }}
            }};
            
            ws.onerror = function(error) {{
                console.error('WebSocket错误:', error);
                {('const statusEl = document.getElementById("status"); if (statusEl && statusEl.style.display !== "none") {{ statusEl.textContent = "❌ 连接错误"; statusEl.style.color = "#ff6b6b"; }}' if self.show_status else "")}
            }};
        }}
        
        function updateReply(data) {{
            const replyContentDiv = document.getElementById('reply-content');
            
            if (data.action === 'start') {{
                replyContentDiv.innerHTML = '<span class="typing-indicator">▊</span>';
            }} else if (data.action === 'chunk') {{
                const currentContent = replyContentDiv.textContent.replace('▊', '');
                replyContentDiv.innerHTML = escapeHtml(currentContent + data.content) + '<span class="typing-indicator">▊</span>';
                // 滚动到底部
                replyContentDiv.scrollTop = replyContentDiv.scrollHeight;
            }} else if (data.action === 'complete') {{
                const currentContent = replyContentDiv.textContent.replace('▊', '');
                replyContentDiv.innerHTML = escapeHtml(currentContent);
                // 播放完成后保持显示，不自动清空
            }} else if (data.action === 'clear') {{
                replyContentDiv.innerHTML = '';  /* 清空后保持空白，不显示提示文字 */
            }}
        }}
        
        function escapeHtml(text) {{
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }}
        
        // 初始加载数据
        fetch('/api/current-reply')
            .then(response => response.json())
            .then(data => {{
                console.log('初始数据加载成功:', data);
                updateReply(data);
            }})
            .catch(err => console.error('加载初始数据失败:', err));
        
        // 连接WebSocket
        connectWebSocket();
    </script>
</body>
</html>
        """
        return web.Response(text=html_content, content_type="text/html")

    async def reply_websocket_handler(self, request):
        """回复生成WebSocket处理器"""
        ws = web.WebSocketResponse()
        await ws.prepare(request)

        self.websockets.append(ws)
        self.logger.debug(f"回复生成WebSocket连接建立，当前连接数: {len(self.websockets)}")

        async for msg in ws:
            if msg.type == WSMsgType.ERROR:
                self.logger.error(f"回复生成WebSocket错误: {ws.exception()}")
                break

        # 清理断开的连接
        if ws in self.websockets:
            self.websockets.remove(ws)
        self.logger.debug(f"回复生成WebSocket连接断开，当前连接数: {len(self.websockets)}")

        return ws

    async def get_current_reply_handler(self, request):
        """获取当前回复状态API"""
        if self.current_reply:
            return web.json_response({"action": "chunk", "user_name": self.current_user, "content": self.current_reply})
        else:
            return web.json_response({"action": "clear"})

    async def start_generation(self, user_name: str):
        """开始新的回复生成"""
        # 先清空之前的内容
        await self.clear_generation()

        self.current_user = user_name
        self.current_reply = ""

        data = {"action": "start", "user_name": user_name}
        await self._broadcast_to_websockets(data)
        self.logger.info(f"开始为用户 {user_name} 生成回复")

    async def add_chunk(self, chunk: str):
        """添加回复内容块"""
        self.current_reply += chunk

        data = {"action": "chunk", "content": chunk}
        await self._broadcast_to_websockets(data)
        self.logger.debug(f"添加回复块: {chunk}")

    async def complete_generation(self):
        """完成回复生成"""
        data = {"action": "complete"}
        await self._broadcast_to_websockets(data)
        self.logger.info(f"完成回复生成，总长度: {len(self.current_reply)}")

    async def clear_generation(self):
        """清空当前生成内容"""
        self.current_reply = ""
        self.current_user = ""

        data = {"action": "clear"}
        await self._broadcast_to_websockets(data)
        self.logger.info("清空回复生成内容")

    async def _broadcast_to_websockets(self, data: dict):
        """向所有WebSocket连接广播数据"""
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
                    self.logger.error(f"发送回复生成WebSocket消息失败: {e}")
                    if ws in self.websockets:
                        self.websockets.remove(ws)
                        removed_count += 1

        if removed_count > 0:
            self.logger.debug(f"清理了 {removed_count} 个断开的回复生成WebSocket连接")


# 全局回复生成管理器实例
_reply_generation_manager: Optional[ReplyGenerationManager] = None


def get_reply_generation_manager() -> ReplyGenerationManager:
    """获取回复生成管理器实例"""
    global _reply_generation_manager
    if _reply_generation_manager is None:
        _reply_generation_manager = ReplyGenerationManager(logger=logger)
    return _reply_generation_manager


async def init_reply_generation_manager():
    """初始化回复生成管理器"""
    manager = get_reply_generation_manager()
    await manager.start_server()
    return manager
