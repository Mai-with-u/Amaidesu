<template>
  <div class="replay-bar" role="group" aria-label="回放控制">
    <el-button size="small" :disabled="loading" @click="$emit('step-back')">⏮ 上一条</el-button>
    <el-button size="small" type="primary" :disabled="loading" @click="$emit('toggle-play')">
      {{ playing ? '暂停' : '播放' }}
    </el-button>
    <el-button size="small" :disabled="loading" @click="$emit('step-forward')">下一条 ⏭</el-button>
    <!-- 节奏：实际时间按事件真实时差推进，匀速按固定时长推进；倍速含义随之变化 -->
    <el-radio-group
      :model-value="pace"
      size="small"
      class="replay-pace"
      aria-label="播放节奏"
      title="实际时间：按事件真实间隔推进，长空档最多停 2 秒；匀速：每条固定时长推进"
      @update:model-value="$emit('update:pace', $event as ReplayPace)"
    >
      <el-radio-button value="realtime">实际时间</el-radio-button>
      <el-radio-button value="uniform">匀速</el-radio-button>
    </el-radio-group>
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
        :label="speedLabel(option)"
        :value="option"
      />
    </el-select>
    <!-- 实际时间节奏下说明长空档被压到上限，免得把倍速当成严格真实时间 -->
    <span v-if="pace === 'realtime'" class="replay-note">长空档最多停 2 秒</span>
    <!-- 时间轴：左端归档首条时刻，右端末条时刻，游标按时刻比例落点 -->
    <span class="replay-axis" aria-hidden="true">
      <span class="replay-axis-time">{{ formatTimeHMS(archiveStartMs) }}</span>
      <span class="replay-axis-track">
        <span class="replay-axis-dot" :style="{ left: `${axisPercent}%` }" />
      </span>
      <span class="replay-axis-time">{{ formatTimeHMS(archiveEndMs) }}</span>
    </span>
    <span class="replay-progress mono" title="游标时刻 / 归档条数">
      {{ loading ? '归档载入中…' : `${cursorTime} · 第 ${cursor + 1} / 共 ${total} 条` }}
    </span>
    <el-date-picker
      v-model="locateAt"
      type="datetime"
      size="small"
      class="replay-locate"
      placeholder="定位到"
      :disabled="loading"
      aria-label="定位到指定时刻"
      @change="onLocate"
    />
    <el-button size="small" type="warning" plain :disabled="loading" @click="$emit('exit')">
      退出回放
    </el-button>
  </div>
</template>

<script setup lang="ts">
/**
 * 回放控制条：播放/暂停、单步前进后退、节奏与倍速、时间轴、定位到指定时刻、退出回放。
 * 纯展示件——状态由 LiveObserver 经 props 传入，动作以事件上抛，
 * 回放状态机在 useTimelineReplay。
 */
import { computed, ref } from 'vue';

import { replaySpeedOptions, type ReplayPace } from '@/composables/live/useTimelineReplay';
import { formatTimeHMS } from '@/utils/format';

const props = defineProps<{
  /** 是否处于播放中（控制按钮文案） */
  playing: boolean;
  /** 当前倍速（含义随节奏变化） */
  speed: number;
  /** 当前播放节奏 */
  pace: ReplayPace;
  /** 游标下标（0 基；进度显示为第 cursor+1 条） */
  cursor: number;
  /** 归档总条数 */
  total: number;
  /** 归档载入中（禁用全部控制） */
  loading: boolean;
  /** 游标所在条目的时刻（毫秒）；未开始时为 null */
  cursorTsMs: number | null;
  /** 归档首条时刻（毫秒），时间轴左端 */
  archiveStartMs: number;
  /** 归档末条时刻（毫秒），时间轴右端 */
  archiveEndMs: number;
}>();

const emit = defineEmits<{
  'toggle-play': [];
  'step-forward': [];
  'step-back': [];
  'update:speed': [value: number];
  'update:pace': [value: ReplayPace];
  /** 定位到指定时刻（毫秒） */
  locate: [value: number];
  exit: [];
}>();

const speedOptions = computed<number[]>(() => [...replaySpeedOptions(props.pace)]);

/** 倍速文案随节奏变化：实际时间是加速倍率，匀速是每秒推进的条目数 */
function speedLabel(option: number): string {
  return props.pace === 'realtime' ? `${option}×` : `${option} 条/秒`;
}

/** 游标时刻读数（未开始时显示起始时刻） */
const cursorTime = computed<string>(() =>
  props.cursorTsMs != null ? formatTimeHMS(props.cursorTsMs) : '未开始',
);

/** 游标在时间轴上的位置（百分比，按时刻比例而非条数比例） */
const axisPercent = computed<number>(() => {
  if (props.cursorTsMs == null) return 0;
  const span = props.archiveEndMs - props.archiveStartMs;
  if (span <= 0) return 0;
  const ratio = (props.cursorTsMs - props.archiveStartMs) / span;
  return Math.min(100, Math.max(0, ratio * 100));
});

/** 定位输入：选定时刻后上抛定位动作，输入框随即清空（它是动作不是状态） */
const locateAt = ref<Date | null>(null);
function onLocate(value: Date | null): void {
  if (value == null) return;
  emit('locate', value.getTime());
  locateAt.value = null;
}
</script>

<style scoped>
.replay-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  width: 100%;
}

.replay-pace {
  flex-shrink: 0;
}

.replay-speed-select {
  width: 92px;
  flex-shrink: 0;
}

/* 长空档上限说明：只在使用实际时间节奏时出现 */
.replay-note {
  font-size: 11px;
  color: var(--text-placeholder);
  white-space: nowrap;
  flex-shrink: 0;
}

/* 时间轴：两端是归档起止时刻，圆点落在游标所在时刻 */
.replay-axis {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 1;
  min-width: 140px;
}

.replay-axis-time {
  font-size: 11px;
  color: var(--text-secondary);
  white-space: nowrap;
}

.replay-axis-track {
  position: relative;
  flex: 1;
  height: 3px;
  border-radius: 2px;
  background: var(--border-color-dark);
}

.replay-axis-dot {
  position: absolute;
  top: 50%;
  width: 7px;
  height: 7px;
  margin-left: -3.5px;
  border-radius: 50%;
  background: var(--color-primary);
  transform: translateY(-50%);
}

.replay-progress {
  font-size: 11px;
  color: var(--text-secondary);
  padding: 2px 8px;
  border-radius: var(--radius-sm);
  background: var(--bg-hover);
  white-space: nowrap;
  flex-shrink: 0;
}

.replay-locate {
  width: 170px;
  flex-shrink: 0;
}
</style>
