<script setup lang="ts">
/**
 * 切片预览播放器：视频元素与播放交互的收拢。
 *
 * - 换源重建、起播定位（seekS）、自动停点（durationS）、播放位置回报（经
 *   wallStartMs 映射回墙钟，段级现转/产物同样有效）都在这里。
 * - 播放快捷键对齐剪辑软件惯例：空格暂停/续播，←/→ ±1 秒，Shift+←/→ ±5 秒；
 *   I 设入点（选中段起点）、O 设出点（终点）。输入框聚焦时不劫持按键。
 * - 转换等待显示已等秒数（每秒跳动），替代黑箱转圈。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { formatRelClock } from '@/utils/clipsTime';
import type { ClipPreviewState } from '@/types/clips';

const props = defineProps<{
  preview: ClipPreviewState | null;
  previewConverting: boolean;
  /** 有选中段时才可标记入出点 */
  canMark: boolean;
  /** 场次起点（位置读数用相对时刻） */
  sessionStartMs: number;
}>();

const emit = defineEmits<{
  (e: 'playhead', wallMs: number): void;
  (e: 'ready'): void;
  (e: 'close'): void;
  (e: 'mark-start', wallMs: number): void;
  (e: 'mark-end', wallMs: number): void;
}>();

// 播放器重建序号：换源才换 key 重建 <video>；同源（同 src）保持复用，播放
// 位置由 watch 直接改 currentTime——同源时 loadedmetadata 不会再触发，重建
// 反而让画面闪一下且重新拉流
const playerSeq = ref(0);
const videoEl = ref<HTMLVideoElement | null>(null);
/** 当前播放位置（墙钟毫秒）；读数与标记取位用 */
const posWallMs = ref<number | null>(null);
/** 转换已等待秒数（每秒跳动，给"还在工作"的信号） */
const convertingElapsedS = ref(0);
let elapsedTimer: ReturnType<typeof setInterval> | null = null;

watch(
  () => props.preview,
  (p, old) => {
    if (!p) {
      posWallMs.value = null;
      return;
    }
    if (old && p.src === old.src) {
      const player = videoEl.value;
      if (player && player.readyState >= 1) {
        player.currentTime = p.seekS;
        void player.play().catch(() => undefined); // 自动播放可能被浏览器策略拦下，交给用户点播放
      }
    } else {
      playerSeq.value += 1;
    }
  },
);

watch(
  () => props.previewConverting,
  converting => {
    if (elapsedTimer !== null) {
      clearInterval(elapsedTimer);
      elapsedTimer = null;
    }
    if (converting) {
      convertingElapsedS.value = 0;
      elapsedTimer = setInterval(() => {
        convertingElapsedS.value += 1;
      }, 1000);
    }
  },
  { immediate: true },
);

onBeforeUnmount(() => {
  if (elapsedTimer !== null) clearInterval(elapsedTimer);
});

const posLabel = computed(() =>
  posWallMs.value === null ? '--:--' : formatRelClock(posWallMs.value - props.sessionStartMs),
);

function currentWallMs(): number | null {
  const player = videoEl.value;
  const current = props.preview;
  if (!player || !current) return null;
  return current.wallStartMs + player.currentTime * 1000;
}

function emitPlayhead(): void {
  const wall = currentWallMs();
  if (wall !== null) {
    posWallMs.value = wall;
    emit('playhead', wall);
  }
}

function onLoadedMetadata(): void {
  const player = videoEl.value;
  const current = props.preview;
  if (player && current && current.seekS > 0) player.currentTime = current.seekS;
  void player?.play().catch(() => undefined); // 自动播放可能被浏览器策略拦下，交给用户点播放
  emitPlayhead();
  emit('ready'); // 元数据可读 = 片段/整场已可播（段级现转在此收尾）
}

function onTimeUpdate(): void {
  const player = videoEl.value;
  const current = props.preview;
  if (!player || !current) return;
  if (current.durationS !== null && player.currentTime > current.seekS + current.durationS) {
    player.pause();
  }
  emitPlayhead();
}

function togglePlay(): void {
  const player = videoEl.value;
  if (player === null) return;
  if (player.paused) void player.play().catch(() => undefined);
  else player.pause();
}

/** 跳到墙钟时刻（换算为视频内时间；越界由浏览器夹取），并回报新位置 */
function seekToWall(wallMs: number): void {
  const player = videoEl.value;
  const current = props.preview;
  if (!player || !current) return;
  player.currentTime = Math.max(0, (wallMs - current.wallStartMs) / 1000);
  emitPlayhead();
}

function nudgeWall(deltaMs: number): void {
  const wall = currentWallMs();
  if (wall !== null) seekToWall(wall + deltaMs);
}

/** 输入框聚焦时不劫持按键（标题/时间编辑中打字会被空格/方向键打断） */
function typingTarget(event: KeyboardEvent): boolean {
  const target = event.target as HTMLElement | null;
  if (target === null) return false;
  return target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName);
}

function onKeydown(event: KeyboardEvent): void {
  if (!props.preview || typingTarget(event)) return;
  if (event.code === 'Space') {
    event.preventDefault();
    togglePlay();
  } else if (event.key === 'ArrowLeft') {
    event.preventDefault();
    nudgeWall(event.shiftKey ? -5000 : -1000);
  } else if (event.key === 'ArrowRight') {
    event.preventDefault();
    nudgeWall(event.shiftKey ? 5000 : 1000);
  } else if ((event.key === 'i' || event.key === 'I') && props.canMark) {
    const wall = currentWallMs();
    if (wall !== null) emit('mark-start', wall);
  } else if ((event.key === 'o' || event.key === 'O') && props.canMark) {
    const wall = currentWallMs();
    if (wall !== null) emit('mark-end', wall);
  }
}

function markStart(): void {
  const wall = currentWallMs();
  if (wall !== null && props.canMark) emit('mark-start', wall);
}

function markEnd(): void {
  const wall = currentWallMs();
  if (wall !== null && props.canMark) emit('mark-end', wall);
}

onMounted(() => window.addEventListener('keydown', onKeydown));
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown));

defineExpose({ seekToWall, currentWallMs, togglePlay });
</script>

<template>
  <div class="clip-player-shell">
    <div class="clip-player-head">
      <span class="clip-player-title">
        {{
          previewConverting
            ? `预览准备中，就绪自动开播…（已等 ${convertingElapsedS} 秒）`
            : preview?.title || '预览'
        }}
      </span>
      <el-button v-if="preview || previewConverting" size="small" text @click="emit('close')"
        >关闭</el-button
      >
    </div>
    <video
      v-if="preview"
      ref="videoEl"
      :key="playerSeq"
      class="clip-player"
      :src="preview.src"
      controls
      preload="metadata"
      @loadedmetadata="onLoadedMetadata"
      @timeupdate="onTimeUpdate"
    />
    <div v-else class="clip-player-empty">
      <slot name="empty" />
    </div>
    <div v-if="preview" class="clip-player-marks">
      <span class="clip-player-pos">播放位置 {{ posLabel }}</span>
      <span class="clip-player-marks-hint">空格暂停 · ←→ ±1s · Shift±5s</span>
      <el-button size="small" :disabled="!canMark" @click="markStart">设为起点（I）</el-button>
      <el-button size="small" :disabled="!canMark" @click="markEnd">设为终点（O）</el-button>
    </div>
  </div>
</template>

<style scoped>
.clip-player-shell {
  display: flex;
  flex-direction: column;
  min-height: 0;
  flex: 1;
}

.clip-player-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.clip-player-head .el-button {
  color: #e5eaf3;
}

.clip-player-title {
  font-size: 13px;
  color: #e5eaf3;
}

.clip-player-shell .clip-player {
  flex: 1;
  min-height: 0;
  width: 100%;
  object-fit: contain;
  border-radius: 4px;
  background: #000;
}

.clip-player-marks {
  display: flex;
  align-items: center;
  gap: 10px;
  padding-top: 8px;
}

.clip-player-pos {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  font-variant-numeric: tabular-nums;
  min-width: 96px;
}

.clip-player-marks-hint {
  flex: 1;
  font-size: 12px;
  color: var(--el-text-color-placeholder);
  text-align: right;
  margin-right: 4px;
}

.clip-player-empty {
  flex: 1;
  min-height: 220px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 16px 32px;
  text-align: center;
  font-size: 13px;
  line-height: 1.8;
  color: var(--el-text-color-secondary);
  background: #000;
  border-radius: 4px;
}
</style>
