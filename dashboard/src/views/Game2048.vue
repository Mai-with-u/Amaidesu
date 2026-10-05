<template>
  <div class="game2048-container">
    <div class="left-panel">
      <div class="stat">
        <div class="stat-label stroke">得分</div>
        <div class="stat-value stroke">{{ scoreRef }}</div>
      </div>
      <div class="stat">
        <div class="stat-label stroke">步数</div>
        <div class="stat-value stroke">{{ movesRef }}</div>
      </div>
      <div class="stat">
        <div class="stat-label stroke">最大块</div>
        <div class="stat-value stroke">{{ maxTileRef }}</div>
      </div>
    </div>
    <div ref="boardRef" class="board">
      <div
        v-for="i in 16"
        :key="'bg' + i"
        class="bg-cell"
        :style="{ left: pxStyle((i - 1) % 4), top: pxStyle(Math.floor((i - 1) / 4)) }"
      ></div>
      <div v-if="overRef" class="overlay">
        <div class="overlay-title">游戏结束</div>
        <div class="overlay-sub">得分 {{ scoreRef }} · 按 restart 重开</div>
      </div>
    </div>
    <div class="side">
      <div class="keypad">
        <div class="keycap blank"></div>
        <div ref="keyUp" class="keycap">↑</div>
        <div class="keycap blank"></div>
        <div ref="keyLeft" class="keycap">←</div>
        <div ref="keyRestart" class="keycap key-restart">R</div>
        <div ref="keyRight" class="keycap">→</div>
        <div class="keycap blank"></div>
        <div ref="keyDown" class="keycap">↓</div>
        <div class="keycap blank"></div>
      </div>
      <div class="hist-wrap">
        <div class="hist-label stroke">操作历史</div>
        <div class="history">
          <span
            v-for="(d, i) in historyShown"
            :key="i + '-' + d"
            class="hist"
            :class="{ latest: i === 0 }"
            >{{ ARROWS[d] || '?' }}</span
          >
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue';

/** game.state.changed 快照载荷（/ws/game2048 单槽推送，与 GameBoardStatePayload 对齐） */
interface Game2048State {
  board: number[][];
  score: number;
  moves: number;
  max_tile: number;
  over: boolean;
  last_direction: string | null;
  history: string[];
}

const SIZE = 4;
const CELL = 96;
const GAP = 10;
const PAD = 10;
const TILE_CLASSES = [
  'v2',
  'v4',
  'v8',
  'v16',
  'v32',
  'v64',
  'v128',
  'v256',
  'v512',
  'v1024',
  'v2048',
];
const ARROWS: Record<string, string> = { up: '↑', down: '↓', left: '←', right: '→' };
const HISTORY_SHOWN = 10; // 纵向最多展示条数（新的在上），与后端透明页同口径

const boardRef = ref<HTMLElement | null>(null);
const scoreRef = ref(0);
const movesRef = ref(0);
const maxTileRef = ref(0);
const overRef = ref(false);
const historyRef = ref<string[]>([]);
const keyUp = ref<HTMLElement | null>(null);
const keyDown = ref<HTMLElement | null>(null);
const keyLeft = ref<HTMLElement | null>(null);
const keyRight = ref<HTMLElement | null>(null);
const keyRestart = ref<HTMLElement | null>(null);

const historyShown = computed(() => [...historyRef.value].reverse().slice(0, HISTORY_SHOWN));

// tile 实体表（命令式动画：与后端透明页同构，保证两处呈现一致）
interface TileEntity {
  el: HTMLElement;
  value: number;
}
let tiles = new Map<string, TileEntity>();
let lastBoard: number[][] | null = null;
let lastMoves: number | null = null;
let ws: WebSocket | null = null;

const keyEls = computed(() => ({
  up: keyUp.value,
  down: keyDown.value,
  left: keyLeft.value,
  right: keyRight.value,
  restart: keyRestart.value,
}));

function key(r: number, c: number): string {
  return r + ',' + c;
}
function px(n: number): number {
  return PAD + n * (CELL + GAP);
}
const pxStyle = (n: number) => px(n) + 'px';
function tileClass(v: number): string {
  const idx = Math.log2(v) - 1;
  return idx >= 0 && idx < TILE_CLASSES.length ? TILE_CLASSES[idx] : 'vbig';
}

function createTileEl(r: number, c: number, v: number): HTMLElement {
  const el = document.createElement('div');
  el.className = 'tile ' + tileClass(v);
  el.textContent = String(v);
  el.style.transform = `translate(${px(c)}px, ${px(r)}px)`;
  boardRef.value?.insertBefore(el, boardRef.value.querySelector('.overlay'));
  return el;
}

function rebuild(boardMatrix: number[][]) {
  for (const ent of tiles.values()) ent.el.remove();
  tiles = new Map();
  for (let r = 0; r < SIZE; r++) {
    for (let c = 0; c < SIZE; c++) {
      const v = boardMatrix[r][c];
      if (v) tiles.set(key(r, c), { el: createTileEl(r, c, v), value: v });
    }
  }
}

interface MovePlan {
  fromR: number;
  fromC: number;
  toR: number;
  toC: number;
  value: number;
  merge: boolean;
}

// 本地重放：按方向对上一帧做同规则滑动，推演每块轨迹与合并、新块位置。
// 推演结果与新帧不一致（理论不发生）时返回 null，调用方退化直渲染。
function computeMoves(
  prev: number[][],
  dir: string,
  next: number[][],
): { moved: MovePlan[]; spawn: { r: number; c: number; value: number } | null } | null {
  const lineCells = (li: number): { r: number; c: number }[] => {
    const cells: { r: number; c: number }[] = [];
    for (let k = 0; k < SIZE; k++) {
      let r: number, c: number;
      if (dir === 'left') {
        r = li;
        c = k;
      } else if (dir === 'right') {
        r = li;
        c = SIZE - 1 - k;
      } else if (dir === 'up') {
        r = k;
        c = li;
      } else {
        r = SIZE - 1 - k;
        c = li;
      }
      cells.push({ r, c });
    }
    return cells;
  };
  const moved: MovePlan[] = [];
  const after: number[][] = Array.from({ length: SIZE }, () => [0, 0, 0, 0]);
  for (let li = 0; li < SIZE; li++) {
    const cells = lineCells(li)
      .map(p => ({ ...p, v: prev[p.r][p.c] }))
      .filter(x => x.v !== 0);
    const slots: {
      v: number;
      first: { r: number; c: number };
      merged: boolean;
      second?: { r: number; c: number };
    }[] = [];
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
      moved.push({
        fromR: slot.first.r,
        fromC: slot.first.c,
        toR: t.r,
        toC: t.c,
        value: slot.v,
        merge: false,
      });
      if (slot.merged && slot.second) {
        moved.push({
          fromR: slot.second.r,
          fromC: slot.second.c,
          toR: t.r,
          toC: t.c,
          value: slot.v,
          merge: true,
        });
      }
    });
  }
  let spawn: { r: number; c: number; value: number } | null = null;
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

function flashKey(dir: string) {
  const el = keyEls.value[dir as keyof typeof keyEls.value];
  if (!el) return;
  el.classList.add('pressed');
  setTimeout(() => el.classList.remove('pressed'), 500);
}

function applyFrame(state: Game2048State) {
  const prevBoard = lastBoard;
  scoreRef.value = state.score || 0;
  movesRef.value = state.moves || 0;
  maxTileRef.value = state.max_tile || 0;
  overRef.value = state.over;
  historyRef.value = [...(state.history || [])];

  // 键帽反馈：落子方向闪对应键；步数回退视为重开（闪 R）
  if (state.last_direction) {
    flashKey(state.last_direction);
  } else if (lastMoves !== null && state.moves < lastMoves) {
    flashKey('restart');
  }

  const plan =
    prevBoard && state.last_direction
      ? computeMoves(prevBoard, state.last_direction, state.board)
      : null;
  if (!plan) {
    rebuild(state.board);
  } else {
    const snapshot = [...tiles.entries()];
    tiles = new Map();
    for (const m of plan.moved) {
      const found = snapshot.find(([k]) => k === key(m.fromR, m.fromC));
      if (!found) {
        rebuild(state.board);
        break;
      }
      const ent = found[1];
      ent.el.style.transform = `translate(${px(m.toC)}px, ${px(m.toR)}px)`;
      if (m.merge) {
        ent.el.classList.add('dying');
        setTimeout(() => ent.el.remove(), 240);
      } else {
        if (ent.value !== m.value) {
          const el = ent.el;
          const v = m.value;
          setTimeout(() => {
            el.textContent = String(v);
            el.className = 'tile ' + tileClass(v) + ' pop';
          }, 230);
          ent.value = v;
        }
        tiles.set(key(m.toR, m.toC), ent);
      }
    }
    if (plan.spawn) {
      const sp = plan.spawn;
      setTimeout(() => {
        const el = createTileEl(sp.r, sp.c, sp.value);
        el.classList.add('pop');
        tiles.set(key(sp.r, sp.c), { el, value: sp.value });
      }, 230);
    }
  }
  lastBoard = state.board.map(row => [...row]);
  lastMoves = state.moves;
}

function connect() {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(`${protocol}//${window.location.host}/ws/game2048`);

  ws.onmessage = event => {
    try {
      const data = JSON.parse(event.data);
      // /ws/game2048 专用协议（与主 WS 事件流无关）：
      // 接入即推单槽最新快照，后续每步落子增量推送：{ type: "state", state: {...} }
      if (data.type === 'state' && data.state) {
        applyFrame(data.state as Game2048State);
      }
    } catch (e) {
      console.error('解析消息失败:', e);
    }
  };

  ws.onclose = () => {
    setTimeout(connect, 3000);
  };

  ws.onerror = () => {
    ws?.close();
  };
}

onMounted(() => {
  connect();
});

onUnmounted(() => {
  if (ws) {
    ws.close();
  }
});
</script>

<style scoped>
.game2048-container {
  min-height: 100vh;
  display: flex;
  flex-direction: row;
  align-items: center;
  justify-content: center;
  gap: 22px;
  background: transparent;
  font-family: 'Microsoft YaHei', 'PingFang SC', sans-serif;
  color: #fff;
}

.stroke {
  text-shadow:
    -1px -1px 0 rgba(0, 0, 0, 0.9),
    1px -1px 0 rgba(0, 0, 0, 0.9),
    -1px 1px 0 rgba(0, 0, 0, 0.9),
    1px 1px 0 rgba(0, 0, 0, 0.9),
    0 0 10px rgba(0, 0, 0, 0.5);
}

.left-panel {
  display: flex;
  flex-direction: column;
  gap: 18px;
  align-items: center;
  min-width: 96px;
}

.stat {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 3px;
}

.stat-label {
  font-size: 16px;
  font-weight: 700;
}

.stat-value {
  font-size: 34px;
  font-weight: 800;
  line-height: 1.1;
}

.board {
  position: relative;
  width: 436px;
  height: 436px;
  background: rgba(0, 0, 0, 0.4);
  border-radius: 12px;
}

.board :deep(.bg-cell) {
  position: absolute;
  width: 96px;
  height: 96px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.12);
}

.board :deep(.tile) {
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
  text-shadow: 1px 1px 2px rgba(0, 0, 0, 0.55);
  transition: transform 0.22s ease-in-out;
  will-change: transform;
}

.board :deep(.tile.v2) {
  background: #eee4da;
  color: #776e65;
  text-shadow: none;
}
.board :deep(.tile.v4) {
  background: #ede0c8;
  color: #776e65;
  text-shadow: none;
}
.board :deep(.tile.v8) {
  background: #f2b179;
}
.board :deep(.tile.v16) {
  background: #f59563;
}
.board :deep(.tile.v32) {
  background: #f67c5f;
}
.board :deep(.tile.v64) {
  background: #f65e3b;
}
.board :deep(.tile.v128) {
  background: #edcf72;
}
.board :deep(.tile.v256) {
  background: #edcc61;
}
.board :deep(.tile.v512) {
  background: #edc850;
}
.board :deep(.tile.v1024) {
  background: #edc53f;
  font-size: 32px;
}
.board :deep(.tile.v2048) {
  background: #edc22e;
  font-size: 32px;
}
.board :deep(.tile.vbig) {
  background: #3c3a32;
  font-size: 30px;
}
.board :deep(.tile.pop) {
  animation: g2048-pop 0.18s ease-out;
}
.board :deep(.tile.dying) {
  opacity: 0;
}

@keyframes g2048-pop {
  0% {
    scale: 0.7;
  }
  100% {
    scale: 1;
  }
}

.overlay {
  position: absolute;
  inset: 0;
  border-radius: 12px;
  background: rgba(0, 0, 0, 0.6);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  font-size: 28px;
  font-weight: bold;
  text-shadow: 1px 1px 3px rgba(0, 0, 0, 0.8);
  z-index: 5;
}

.overlay-sub {
  font-size: 17px;
  font-weight: normal;
  margin-top: 8px;
  opacity: 0.85;
}

.side {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 14px;
}

.keypad {
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
  background: rgba(0, 0, 0, 0.55);
  border: 1px solid rgba(255, 255, 255, 0.4);
  font-size: 22px;
  font-weight: bold;
  color: rgba(255, 255, 255, 0.92);
  text-shadow: 0 1px 2px rgba(0, 0, 0, 0.8);
  transition: all 0.1s ease-out;
}

.keycap.pressed {
  background: #edc22e;
  border-color: #edc22e;
  color: #3c3a32;
  text-shadow: none;
  transform: scale(0.9);
}

.keycap.blank {
  visibility: hidden;
}

.key-restart {
  font-size: 16px;
}

.hist-wrap {
  display: flex;
  flex-direction: column;
  gap: 6px;
  align-items: center;
}

.hist-label {
  font-size: 15px;
  font-weight: 700;
}

.history {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-height: 28px;
  max-height: 316px;
  overflow: hidden;
}

.hist {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 38px;
  height: 28px;
  border-radius: 7px;
  background: rgba(0, 0, 0, 0.68);
  font-size: 18px;
  font-weight: 800;
  color: #fff;
}

.hist.latest {
  background: #edc22e;
  color: #3c3a32;
}
</style>
