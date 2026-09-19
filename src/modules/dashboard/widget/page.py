"""弹幕小部件页面

widget 页面 HTML 以 Python 常量内联承载——pyproject wheel 只打包 ``*.py``，
独立 ``.html`` 资源文件需要额外打包配置，不值得为单页引入。
"""

WIDGET_HTML = """<!DOCTYPE html>
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
