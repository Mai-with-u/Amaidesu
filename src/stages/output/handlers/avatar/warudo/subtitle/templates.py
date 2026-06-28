"""
Warudo 字幕 HTML/CSS/JS 模板

迁移自旧插件 plugins_backup/warudo/talk_subtitle.py 的 reply_index_handler。
内嵌为 Python 常量,避免引入静态文件依赖。

特性:
- 透明背景(适合 OBS 浏览器源叠加)
- 自动重连 WebSocket(每 3 秒)
- 打字机光标(▊ 闪烁)
- 中文字体优化('Microsoft YaHei')
- 字体大小 45px
- HTML 转义(div.textContent 防 XSS)
- 可选 show_status 徽章
"""

from typing import Final


def _build_html(show_status: bool, port: int) -> str:
    """构建字幕 HTML 页面

    Args:
        show_status: 是否显示连接状态徽章
        port: WebSocket 端口(用于客户端连接)

    Returns:
        完整 HTML 字符串
    """
    status_display = "block" if show_status else "none"
    status_content = "'正在连接...'" if show_status else "''"

    status_onopen = (
        'const statusEl = document.getElementById("status");'
        ' if (statusEl && statusEl.style.display !== "none") {'
        ' statusEl.textContent = "✓ 已连接"; statusEl.style.color = "#ff8800"; }'
        if show_status
        else ""
    )
    status_onclose = (
        'const statusEl = document.getElementById("status");'
        ' if (statusEl && statusEl.style.display !== "none") {'
        ' statusEl.textContent = "⚠ 连接断开,正在重连..."; statusEl.style.color = "#ff6b6b"; }'
        if show_status
        else ""
    )
    status_onerror = (
        'const statusEl = document.getElementById("status");'
        ' if (statusEl && statusEl.style.display !== "none") {'
        ' statusEl.textContent = "✗ 连接错误"; statusEl.style.color = "#ff6b6b"; }'
        if show_status
        else ""
    )

    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Warudo 字幕</title>
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
            display: {status_display};
        }}
        .reply-content {{
            background: rgba(0, 0, 0, 0.3);
            padding: 20px;
            border-radius: 10px;
            border-left: 4px solid #ff8800;
            backdrop-filter: blur(5px);
            flex-grow: 1;
            overflow-y: auto;
            font-size: 45px;
            line-height: 1.6;
            word-wrap: break-word;
            white-space: pre-wrap;
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
        <div class="status" id="status">{status_content}</div>
        <div class="reply-content" id="reply-content"></div>
    </div>

    <script>
        let ws;
        let reconnectInterval;

        function connectWebSocket() {{
            console.log('正在连接 WebSocket...');
            ws = new WebSocket('ws://localhost:{port}/ws');

            ws.onopen = function() {{
                console.log('WebSocket 连接已建立');
                {status_onopen}
                if (reconnectInterval) {{
                    clearInterval(reconnectInterval);
                    reconnectInterval = null;
                }}
            }};

            ws.onmessage = function(event) {{
                try {{
                    const data = JSON.parse(event.data);
                    updateReply(data);
                }} catch (e) {{
                    console.error('解析消息失败:', e, event.data);
                }}
            }};

            ws.onclose = function(event) {{
                console.log('WebSocket 连接关闭:', event.code, event.reason);
                {status_onclose}
                if (!reconnectInterval) {{
                    reconnectInterval = setInterval(connectWebSocket, 3000);
                }}
            }};

            ws.onerror = function(error) {{
                console.error('WebSocket 错误:', error);
                {status_onerror}
            }};
        }}

        function updateReply(data) {{
            const replyContentDiv = document.getElementById('reply-content');

            if (data.action === 'start') {{
                replyContentDiv.innerHTML = '<span class="typing-indicator">▊</span>';
            }} else if (data.action === 'chunk') {{
                const currentContent = replyContentDiv.textContent.replace('▊', '');
                replyContentDiv.innerHTML = escapeHtml(currentContent + data.content) +
                    '<span class="typing-indicator">▊</span>';
                replyContentDiv.scrollTop = replyContentDiv.scrollHeight;
            }} else if (data.action === 'complete') {{
                const currentContent = replyContentDiv.textContent.replace('▊', '');
                replyContentDiv.innerHTML = escapeHtml(currentContent);
            }} else if (data.action === 'clear') {{
                replyContentDiv.innerHTML = '';
            }}
        }}

        function escapeHtml(text) {{
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }}

        // 初始加载
        fetch('/api/current-reply')
            .then(response => response.json())
            .then(data => updateReply(data))
            .catch(err => console.error('加载初始数据失败:', err));

        // 连接
        connectWebSocket();
    </script>
</body>
</html>"""


def render_subtitle_html(show_status: bool = False, port: int = 8766) -> str:
    """渲染字幕 HTML 页面

    Args:
        show_status: 是否显示连接状态徽章
        port: WebSocket 端口(默认 8766)

    Returns:
        完整 HTML 字符串
    """
    return _build_html(show_status=show_status, port=port)


# 常量:默认端口
DEFAULT_SUBTITLE_PORT: Final[int] = 8766
