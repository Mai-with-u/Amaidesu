<template>
  <el-card class="live-channels" shadow="never">
    <template #header>
      <div class="card-header">
        <span>实时通道</span>
        <div class="header-actions">
          <span class="hint">手动摆姿</span>
          <el-switch v-model="manualOn" :disabled="!channels.length" @change="onManualToggle" />
        </div>
      </div>
    </template>

    <p class="card-tip">
      {{
        manualOn
          ? '拖滑条直接指定通道的值，形象马上照做；关掉开关或离开页面后几秒内交还给自动行为。'
          : '面捕合成器此刻发给平台的值。打开"手动摆姿"可以逐个通道试效果。'
      }}
    </p>

    <div v-for="channel in channels" :key="channel.name" class="channel-row">
      <span class="channel-name">{{ channelLabel(channel.name) }}</span>
      <el-slider
        v-if="manualOn"
        v-model="manualValues[channel.name]"
        :min="channel.min"
        :max="channel.max"
        :step="channel.max - channel.min >= 10 ? 1 : 0.01"
        size="small"
        class="channel-slider"
        @input="sendManual"
      />
      <div v-else class="channel-bar">
        <div class="bar-fill" :style="barStyle(channel)" />
        <div v-if="channel.min < 0" class="bar-zero" />
      </div>
      <span class="channel-value">{{ formatValue(frame[channel.name]) }}</span>
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { onDeactivated, reactive, ref } from 'vue';
import { avatarTuningApi } from '@/api';
import { useKeepAlivePolling } from '@/composables/useKeepAlivePolling';
import { channelLabel, throttleLatest } from '@/utils/avatarTuning';
import { getApiErrorMessage } from '@/utils/apiError';
import { ElMessage } from 'element-plus';

/**
 * 实时通道 + 手动摆姿（形象调校页的一块）。
 *
 * 平时显示面捕合成器最近一帧各通道的值（数据由页面轮询后经 props 传入）；打开
 * "手动摆姿"后每个通道变成滑条，指定的值盖过所有自动行为。手动摆姿在后端有保持
 * 时限，这里每 2 秒续期一次。
 *
 * 生命周期语义（keep-alive）：本块在页面数据到齐后才挂载，那时页面的激活早已发生，
 * 所以续期定时器在 setup 即启动，之后随页面失活 / 激活停启、卸载时兜底停止；页面失活
 * 时直接关掉手动摆姿并通知后端结束，不留一个停在半空的姿势。
 */

const props = defineProps<{
  channels: { name: string; min: number; max: number }[];
  frame: Record<string, number>;
}>();

/** 手动摆姿在后端保持多久（续期间隔要明显短于它） */
const HOLD_MS = 3000;
const RENEW_MS = 2000;

const manualOn = ref(false);
const manualValues = reactive<Record<string, number>>({});

function formatValue(value: number | undefined): string {
  return value === undefined ? '—' : value.toFixed(2);
}

/** 条形图：取值范围含负数的通道从中线往两边画，其余从左往右画 */
function barStyle(channel: { name: string; min: number; max: number }): Record<string, string> {
  const value = props.frame[channel.name] ?? 0;
  const span = channel.max - channel.min || 1;
  if (channel.min < 0) {
    const zero = ((0 - channel.min) / span) * 100;
    const pos = ((Math.max(channel.min, Math.min(channel.max, value)) - channel.min) / span) * 100;
    return { left: `${Math.min(zero, pos)}%`, width: `${Math.abs(pos - zero)}%` };
  }
  return { left: '0%', width: `${((value - channel.min) / span) * 100}%` };
}

async function pushManual(): Promise<void> {
  try {
    await avatarTuningApi.setManual({ ...manualValues }, HOLD_MS);
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, '手动摆姿失败'));
    manualOn.value = false;
  }
}

const sendManual = throttleLatest(() => void pushManual(), 80);

async function onManualToggle(on: string | number | boolean): Promise<void> {
  if (on) {
    // 从此刻的姿态起步，开开关那一下形象不会跳
    for (const channel of props.channels) {
      manualValues[channel.name] = props.frame[channel.name] ?? 0;
    }
    await pushManual();
    return;
  }
  try {
    await avatarTuningApi.clearManual();
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, '结束手动摆姿失败（几秒后会自动交还）'));
  }
}

useKeepAlivePolling(
  () => {
    if (manualOn.value) void pushManual();
  },
  RENEW_MS,
  { startOnSetup: true, stopOnDispose: true },
);

onDeactivated(() => {
  if (manualOn.value) {
    manualOn.value = false;
    void onManualToggle(false);
  }
});
</script>

<style scoped>
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: var(--spacing-xs);
}

.hint,
.card-tip {
  font-size: 12px;
  color: var(--text-secondary);
}

.card-tip {
  margin: 0 0 var(--spacing-sm);
}

.channel-row {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  min-height: 28px;
}

.channel-name {
  width: 120px;
  flex-shrink: 0;
  font-size: 13px;
}

.channel-bar {
  position: relative;
  flex: 1;
  height: 8px;
  border-radius: 4px;
  background: var(--el-fill-color, #f0f2f5);
  overflow: hidden;
}

.bar-fill {
  position: absolute;
  top: 0;
  bottom: 0;
  background: var(--el-color-primary, #409eff);
  transition:
    left 0.15s linear,
    width 0.15s linear;
}

.bar-zero {
  position: absolute;
  top: 0;
  bottom: 0;
  left: 50%;
  width: 1px;
  background: var(--text-secondary);
  opacity: 0.5;
}

.channel-slider {
  flex: 1;
}

.channel-value {
  width: 48px;
  text-align: right;
  font-family: var(--font-mono);
  font-size: 12px;
}
</style>
