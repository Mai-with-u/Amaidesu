<script setup lang="ts">
/**
 * 切片预览播放器：视频元素与播放交互的收拢。
 *
 * 换源重建、起播定位（seek 到 baseS）、段范围自动停点、播放位置回报都在
 * 这里；页面只接播放头与关闭事件。播放位置映射回墙钟需要源文件起点——
 * 段级现转（range）的视频 t=0 对应段起点而非文件起点，映射不了，不回报。
 */
import { ref, watch } from 'vue';

const props = defineProps<{
  preview: {
    src: string;
    title: string;
    baseS: number;
    durationS: number | null;
    sourcePath: string;
    /** 段级现转：视频 t=0 对应段起点而非文件起点 */
    range: boolean;
  } | null;
  previewConverting: boolean;
  /** 预览源文件的起始墙钟毫秒；找不到文件时为 null（播放头无从映射） */
  sourceStartMs: number | null;
}>();

const emit = defineEmits<{
  (e: 'playhead', wallMs: number | null): void;
  (e: 'ready'): void;
  (e: 'close'): void;
}>();

// 播放器重建序号：换源才换 key 重建 <video>；同源（同整场文件）保持复用，
// 播放位置由 watch 直接改 currentTime——同源时 loadedmetadata 不会再触发，
// 重建反而让画面闪一下且重新拉流
const playerSeq = ref(0);
const videoEl = ref<HTMLVideoElement | null>(null);

watch(
  () => props.preview,
  (p, old) => {
    if (!p) return;
    if (old && p.src === old.src) {
      const player = videoEl.value;
      if (player && player.readyState >= 1) {
        player.currentTime = p.baseS;
        void player.play().catch(() => undefined); // 自动播放可能被浏览器策略拦下，交给用户点播放
      }
    } else {
      playerSeq.value += 1;
    }
  },
);

function onLoadedMetadata(): void {
  const player = videoEl.value;
  const current = props.preview;
  if (player && current && current.baseS > 0) player.currentTime = current.baseS;
  void player?.play().catch(() => undefined); // 自动播放可能被浏览器策略拦下，交给用户点播放
  emitPlayhead();
  emit('ready'); // 元数据可读 = 片段/整场已可播（段级现转在此收尾）
}

function emitPlayhead(): void {
  const player = videoEl.value;
  const current = props.preview;
  if (!player || !current || current.range || props.sourceStartMs === null) {
    emit('playhead', null);
    return;
  }
  emit('playhead', props.sourceStartMs + player.currentTime * 1000);
}

function onTimeUpdate(): void {
  const player = videoEl.value;
  const current = props.preview;
  if (!player || !current) return;
  if (current.durationS !== null && player.currentTime > current.baseS + current.durationS) {
    player.pause();
  }
  emitPlayhead();
}

defineExpose({ videoEl });
</script>

<template>
  <div class="clip-player-shell">
    <div class="clip-player-head">
      <span class="clip-player-title">
        {{ previewConverting ? '预览准备中，就绪自动开播…' : preview?.title || '预览' }}
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
