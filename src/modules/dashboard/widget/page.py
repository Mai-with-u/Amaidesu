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

GAME2048_HTML = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>2048</title>
    <style>
        html, body {
            background: transparent !important;
            margin: 0;
            padding: 12px;
            font-family: 'Microsoft YaHei', 'PingFang SC', sans-serif;
            overflow: hidden;
            color: #fff;
        }
        #game { display: flex; flex-direction: row; align-items: center; gap: 22px; }
        .stroke {
            text-shadow: -1px -1px 0 rgba(0,0,0,0.9), 1px -1px 0 rgba(0,0,0,0.9),
                         -1px 1px 0 rgba(0,0,0,0.9), 1px 1px 0 rgba(0,0,0,0.9),
                         0 0 10px rgba(0,0,0,0.5);
        }
        #left-panel { display: flex; flex-direction: column; gap: 18px; align-items: center; min-width: 96px; }
        .stat { display: flex; flex-direction: column; align-items: center; gap: 3px; }
        .stat-label { font-size: 16px; font-weight: 700; opacity: 1; }
        .stat-value { font-size: 34px; font-weight: 800; line-height: 1.1; }
        #board {
            position: relative;
            width: 436px;
            height: 436px;
            background: rgba(0,0,0,0.4);
            border-radius: 12px;
        }
        .bg-cell {
            position: absolute;
            width: 96px;
            height: 96px;
            border-radius: 8px;
            background: rgba(255,255,255,0.12);
        }
        .tile {
            position: absolute;
            width: 96px;
            height: 96px;
            border-radius: 8px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 38px;
            font-weight: bold;
            color: #fff;
            text-shadow: 1px 1px 2px rgba(0,0,0,0.55);
            transition: transform 0.22s ease-in-out;
            will-change: transform;
        }
        .tile.v2     { background: #eee4da; color: #776e65; text-shadow: none; }
        .tile.v4     { background: #ede0c8; color: #776e65; text-shadow: none; }
        .tile.v8     { background: #f2b179; }
        .tile.v16    { background: #f59563; }
        .tile.v32    { background: #f67c5f; }
        .tile.v64    { background: #f65e3b; }
        .tile.v128   { background: #edcf72; }
        .tile.v256   { background: #edcc61; }
        .tile.v512   { background: #edc850; }
        .tile.v1024  { background: #edc53f; font-size: 32px; }
        .tile.v2048  { background: #edc22e; font-size: 32px; }
        .tile.vbig   { background: #3c3a32; font-size: 30px; }
        .tile.pop    { animation: pop 0.18s ease-out; }
        .tile.dying  { opacity: 0; }
        @keyframes pop {
            0%   { scale: 0.7; }
            100% { scale: 1; }
        }
        #overlay {
            position: absolute;
            inset: 0;
            border-radius: 12px;
            background: rgba(0,0,0,0.6);
            display: none;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            font-size: 28px;
            font-weight: bold;
            text-shadow: 1px 1px 3px rgba(0,0,0,0.8);
            z-index: 5;
        }
        #overlay .sub { font-size: 17px; font-weight: normal; margin-top: 8px; opacity: 0.85; }
        #side { display: flex; flex-direction: column; align-items: center; gap: 14px; }
        #keypad {
            display: grid;
            grid-template-columns: repeat(3, 52px);
            grid-template-rows: repeat(3, 52px);
            gap: 6px;
        }
        .keycap {
            display: flex;
            align-items: center;
            justify-content: center;
            border-radius: 9px;
            background: rgba(0,0,0,0.55);
            border: 1px solid rgba(255,255,255,0.4);
            font-size: 22px;
            font-weight: bold;
            color: rgba(255,255,255,0.92);
            text-shadow: 0 1px 2px rgba(0,0,0,0.8);
            transition: all 0.1s ease-out;
        }
        .keycap.pressed {
            background: #edc22e;
            border-color: #edc22e;
            color: #3c3a32;
            text-shadow: none;
            transform: scale(0.9);
        }
        #keypad .blank { visibility: hidden; }
        #hist-wrap { display: flex; flex-direction: column; gap: 6px; align-items: center; }
        #hist-label { font-size: 15px; font-weight: 700; opacity: 1; }
        #history {
            display: flex;
            flex-direction: column;
            gap: 4px;
            min-height: 28px;
            max-height: 316px;
            overflow: hidden;
        }
        #history .hist {
            display: flex;
            align-items: center;
            justify-content: center;
            width: 38px;
            height: 28px;
            border-radius: 7px;
            background: rgba(0,0,0,0.68);
            font-size: 18px;
            font-weight: 800;
            color: #fff;
        }
        #history .hist.latest {
            background: #edc22e;
            color: #3c3a32;
        }
    </style>
</head>
<body>
    <div id="game">
        <div id="left-panel">
            <div class="stat">
                <div class="stat-label stroke">得分</div>
                <div class="stat-value stroke" id="score">0</div>
            </div>
            <div class="stat">
                <div class="stat-label stroke">步数</div>
                <div class="stat-value stroke" id="moves">0</div>
            </div>
            <div class="stat">
                <div class="stat-label stroke">最大块</div>
                <div class="stat-value stroke" id="maxtile">0</div>
            </div>
        </div>
        <div id="board">
            <div id="overlay"><span>游戏结束</span><span class="sub" id="overlay-sub"></span></div>
        </div>
        <div id="side">
            <div id="keypad">
                <div class="keycap blank"></div>
                <div class="keycap" id="key-up">↑</div>
                <div class="keycap blank"></div>
                <div class="keycap" id="key-left">←</div>
                <div class="keycap" id="key-restart" style="font-size:16px;">R</div>
                <div class="keycap" id="key-right">→</div>
                <div class="keycap blank"></div>
                <div class="keycap" id="key-down">↓</div>
                <div class="keycap blank"></div>
            </div>
            <div id="hist-wrap">
                <div id="hist-label" class="stroke">操作历史</div>
                <div id="history"></div>
            </div>
        </div>
    </div>
    <script>
        const board = document.getElementById('board');
        const overlay = document.getElementById('overlay');
        const overlaySub = document.getElementById('overlay-sub');
        const SIZE = 4;
        const CELL = 96, GAP = 10, PAD = 10;
        const TILE_CLASSES = ['v2','v4','v8','v16','v32','v64','v128','v256','v512','v1024','v2048'];
        const ARROWS = { up: '↑', down: '↓', left: '←', right: '→' };
        const HISTORY_SHOWN = 10;  // 纵向最多展示条数（新的在上）

        // 静态背景格（一次绘制）
        for (let r = 0; r < SIZE; r++) {
            for (let c = 0; c < SIZE; c++) {
                const cell = document.createElement('div');
                cell.className = 'bg-cell';
                cell.style.left = (PAD + c * (CELL + GAP)) + 'px';
                cell.style.top = (PAD + r * (CELL + GAP)) + 'px';
                board.insertBefore(cell, overlay);
            }
        }

        function tileClass(v) {
            const idx = Math.log2(v) - 1;
            return idx >= 0 && idx < TILE_CLASSES.length ? TILE_CLASSES[idx] : 'vbig';
        }
        function key(r, c) { return r + ',' + c; }
        function px(n) { return PAD + n * (CELL + GAP); }

        // tile 实体表：key(r,c) -> {el, value}
        let tiles = new Map();
        let lastBoard = null;   // 上一帧矩阵（滑动推演数据源）
        let lastMoves = null;   // 上一帧步数（restart 判定）

        function createTileEl(r, c, v) {
            const el = document.createElement('div');
            el.className = 'tile ' + tileClass(v);
            el.textContent = v;
            el.style.transform = 'translate(' + px(c) + 'px,' + px(r) + 'px)';
            board.insertBefore(el, overlay);
            return el;
        }

        function rebuild(boardMatrix) {
            for (const ent of tiles.values()) ent.el.remove();
            tiles = new Map();
            for (let r = 0; r < SIZE; r++) {
                for (let c = 0; c < SIZE; c++) {
                    const v = boardMatrix[r][c];
                    if (v) tiles.set(key(r, c), { el: createTileEl(r, c, v), value: v });
                }
            }
        }

        // 本地重放：按方向对上一帧做同规则滑动，推演每块轨迹与合并、新块位置。
        // 推演结果与新帧不一致（理论不发生）时返回 null，调用方退化直渲染。
        function computeMoves(prev, dir, next) {
            const lineCells = (li) => {
                const cells = [];
                for (let k = 0; k < SIZE; k++) {
                    let r, c;
                    if (dir === 'left') { r = li; c = k; }
                    else if (dir === 'right') { r = li; c = SIZE - 1 - k; }
                    else if (dir === 'up') { r = k; c = li; }
                    else { r = SIZE - 1 - k; c = li; }
                    cells.push({ r, c });
                }
                return cells;
            };
            const moved = [];
            const after = Array.from({ length: SIZE }, () => [0, 0, 0, 0]);
            for (let li = 0; li < SIZE; li++) {
                const cells = lineCells(li).map(p => ({ ...p, v: prev[p.r][p.c] })).filter(x => x.v !== 0);
                const slots = [];
                for (const cell of cells) {
                    const last = slots[slots.length - 1];
                    if (last && !last.merged && last.v === cell.v) {
                        last.v *= 2;
                        last.merged = true;
                        last.second = cell;
                    } else {
                        slots.push({ v: cell.v, first: cell, merged: false });
                    }
                }
                const targets = lineCells(li).slice(0, slots.length);
                slots.forEach((slot, i) => {
                    const t = targets[i];
                    after[t.r][t.c] = slot.v;
                    moved.push({ fromR: slot.first.r, fromC: slot.first.c, toR: t.r, toC: t.c, value: slot.v, merge: false });
                    if (slot.merged) {
                        moved.push({ fromR: slot.second.r, fromC: slot.second.c, toR: t.r, toC: t.c, value: slot.v, merge: true });
                    }
                });
            }
            let spawn = null;
            for (let r = 0; r < SIZE; r++) {
                for (let c = 0; c < SIZE; c++) {
                    if (after[r][c] !== next[r][c]) {
                        if (after[r][c] === 0 && prev[r][c] === 0 && spawn === null) {
                            spawn = { r, c, value: next[r][c] };
                        } else {
                            return null;
                        }
                    }
                }
            }
            return { moved, spawn };
        }

        function flashKey(dir) {
            const el = document.getElementById('key-' + dir);
            if (!el) return;
            el.classList.add('pressed');
            setTimeout(() => el.classList.remove('pressed'), 500);
        }

        function renderHistory(history) {
            const el = document.getElementById('history');
            el.innerHTML = '';
            const items = [...(history || [])].reverse().slice(0, HISTORY_SHOWN);  // 新在前
            items.forEach((d, i) => {
                const span = document.createElement('span');
                span.className = 'hist' + (i === 0 ? ' latest' : '');
                span.textContent = ARROWS[d] || '?';
                el.appendChild(span);
            });
        }

        function applyFrame(state) {
            const prevBoard = lastBoard;
            document.getElementById('score').textContent = state.score || 0;
            document.getElementById('moves').textContent = state.moves || 0;
            document.getElementById('maxtile').textContent = state.max_tile || 0;
            renderHistory(state.history);

            // 键帽反馈：落子方向闪对应键；步数回退视为重开（闪 R）
            if (state.last_direction) {
                flashKey(state.last_direction);
            } else if (lastMoves !== null && state.moves < lastMoves) {
                flashKey('restart');
            }

            const plan = (prevBoard && state.last_direction)
                ? computeMoves(prevBoard, state.last_direction, state.board)
                : null;

            if (!plan) {
                rebuild(state.board);
            } else {
                const snapshot = [...tiles.entries()];
                tiles = new Map();
                for (const m of plan.moved) {
                    const found = snapshot.find(([k]) => k === key(m.fromR, m.fromC));
                    if (!found) { rebuild(state.board); lastBoard = state.board.map(row => [...row]); lastMoves = state.moves; return; }
                    const ent = found[1];
                    ent.el.style.transform = 'translate(' + px(m.toC) + 'px,' + px(m.toR) + 'px)';
                    if (m.merge) {
                        ent.el.classList.add('dying');
                        setTimeout(() => ent.el.remove(), 240);
                    } else {
                        if (ent.value !== m.value) {
                            const el = ent.el;
                            const v = m.value;
                            setTimeout(() => {
                                el.textContent = v;
                                el.className = 'tile ' + tileClass(v) + ' pop';
                            }, 230);
                            ent.value = v;
                        }
                        tiles.set(key(m.toR, m.toC), ent);
                    }
                }
                if (plan.spawn) {
                    setTimeout(() => {
                        const el = createTileEl(plan.spawn.r, plan.spawn.c, plan.spawn.value);
                        el.classList.add('pop');
                        tiles.set(key(plan.spawn.r, plan.spawn.c), { el, value: plan.spawn.value });
                    }, 230);
                }
            }

            if (state.over) {
                overlay.style.display = 'flex';
                overlaySub.textContent = '得分 ' + (state.score || 0) + ' · 按 restart 重开';
            } else {
                overlay.style.display = 'none';
            }
            lastBoard = state.board.map(row => [...row]);
            lastMoves = state.moves;
        }

        let ws;
        function connect() {
            const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
            ws = new WebSocket(protocol + '//' + location.host + '/ws/game2048');
            ws.onmessage = (event) => {
                const data = JSON.parse(event.data);
                if (data.type === 'state' && data.state) applyFrame(data.state);
            };
            ws.onclose = () => setTimeout(connect, 3000);
            ws.onerror = () => ws.close();
        }
        connect();
    </script>
</body>
</html>"""
