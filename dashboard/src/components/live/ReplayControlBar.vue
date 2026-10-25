<template>
  <div class="replay-bar" role="group" aria-label="回放控制">
    <span class="replay-badge">回放调试</span>
    <el-button size="small" :disabled="loading" @click="$emit('step-back')">⏮ 上一条</el-button>
    <el-button size="small" type="primary" :disabled="loading" @click="$emit('toggle-play')">
      {{ playing ? '暂停' : '播放' }}
    </el-button>
    <el-button size="small" :disabled="loading" @click="$emit('step-forward')">下一条 ⏭</el-button>
    <span class="replay-progress mono" title="游标位置 / 归档总条数">
      {{ loading ? '归档载入中…' : `第 ${cursor + 1} / 共 ${total} 条` }}
    </span>
    <span class="replay-speed">
      <span class="replay-speed-label">倍速</span>
      <el-select
        :model-value="speed"
        size="small"
        class="replay-speed-select"
        :disabled="loading"
        aria-label="回放倍速"
        @update:model-value="$emit('update:speed', $event as number)"
      >
        <el-option
          v-for="option in speedOptions"
          :key="option"
          :label="`${option}×`"
          :value="option"
        />
      </el-select>
    </span>
    <el-button size="small" type="warning" plain :disabled="loading" @click="$emit('exit')">
      退出回放
    </el-button>
  </div>
</template>

<script setup lang="ts">
/**
 * 回放控制条：播放/暂停、单步前进后退、倍速选择、进度显示与退出回放。
 * 纯展示件——状态由 LiveObserver 经 props 传入，动作以事件上抛，
 * 回放状态机在 useTimelineReplay。
 */
import { REPLAY_SPEED_OPTIONS } from '@/composables/live/useTimelineReplay';

defineProps<{
  /** 是否处于播放中（控制按钮文案） */
  playing: boolean;
  /** 当前倍速 */
  speed: number;
  /** 游标下标（0 基；进度显示为第 cursor+1 条） */
  cursor: number;
  /** 归档总条数 */
  total: number;
  /** 归档载入中（禁用全部控制） */
  loading: boolean;
}>();

defineEmits<{
  'toggle-play': [];
  'step-forward': [];
  'step-back': [];
  'update:speed': [value: number];
  exit: [];
}>();

const speedOptions = REPLAY_SPEED_OPTIONS;
</script>

<style scoped>
.replay-bar {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.replay-badge {
  padding: 1px 8px;
  border-radius: 999px;
  border: 1px solid var(--color-primary);
  color: var(--color-primary);
  font-size: 11px;
  font-weight: 700;
  flex-shrink: 0;
}

.replay-progress {
  font-size: 11px;
  color: var(--text-secondary);
  padding: 2px 8px;
  border-radius: var(--radius-sm);
  background: var(--bg-hover);
  white-space: nowrap;
}

.replay-speed {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.replay-speed-label {
  font-size: 11px;
  color: var(--text-secondary);
}

.replay-speed-select {
  width: 76px;
}
</style>
