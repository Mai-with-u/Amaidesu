<template>
  <el-card class="tuning-fields" shadow="never">
    <template #header>
      <div class="card-header">
        <span>人设与行为</span>
        <span class="hint">拖动即生效；鼠标停在名字上看说明</span>
      </div>
    </template>

    <section v-for="group in groups" :key="group.title" class="group">
      <div class="group-title">{{ group.title }}</div>
      <div v-for="field in group.fields" :key="field.key" class="field-row">
        <el-tooltip :content="field.description" placement="top-start" :show-after="300">
          <span class="field-name">{{ field.title }}</span>
        </el-tooltip>
        <el-switch
          v-if="field.type === 'boolean'"
          :model-value="Boolean(field.value)"
          @change="(v: string | number | boolean) => update(field.key, Boolean(v))"
        />
        <template v-else>
          <el-slider
            :model-value="Number(field.value)"
            :min="field.min ?? 0"
            :max="field.max ?? 1"
            :step="fieldStep(field)"
            size="small"
            class="grow"
            @input="(v: number | number[]) => update(field.key, Number(v))"
          />
          <span class="field-value">{{ formatField(field) }}</span>
        </template>
      </div>
    </section>

    <section v-for="calibration in calibrations" :key="calibration.driver" class="group">
      <div class="group-title">{{ calibration.driver.toUpperCase() }} 校准（换模型时调）</div>
      <div v-for="item in calibration.items" :key="item.key" class="field-row">
        <el-tooltip :content="item.description" placement="top-start" :show-after="300">
          <span class="field-name">{{ item.title }}</span>
        </el-tooltip>
        <el-slider
          :model-value="Number(item.value)"
          :min="item.min ?? 0"
          :max="item.max ?? 1"
          :step="0.01"
          size="small"
          class="grow"
          @input="(v: number | number[]) => calibrate(calibration.driver, item.key, Number(v))"
        />
        <span class="field-value">{{ Number(item.value).toFixed(2) }}</span>
      </div>
    </section>
    <p v-if="!calibrations.length" class="hint">
      没有挂着带校准项的平台驱动（VTS 连上后这里会出现它的校准）。
    </p>
  </el-card>
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus';
import { avatarTuningApi } from '@/api';
import type { AvatarTuningField } from '@/types';
import { getApiErrorMessage } from '@/utils/apiError';
import { throttleLatest, type TuningFieldGroup } from '@/utils/avatarTuning';

/**
 * 人设、行为调参与平台校准（形象调校页的一块）。
 *
 * 字段的标题、说明、取值范围都来自后端配置 Schema；开关立刻提交，滑条拖动时限流
 * 提交。改动只在运行中生效，页面统一"保存"后才写回 avatar.toml。父组件持有字段
 * 数据：拖动时先经事件把新值交回去（滑条不会弹回旧值），再限流提交后端。
 *
 * 生命周期语义：无定时器与订阅；限流的补发在 120 毫秒内完成。
 */

defineProps<{
  groups: TuningFieldGroup[];
  calibrations: { driver: string; items: AvatarTuningField[] }[];
}>();

const emit = defineEmits<{
  /** 调参在运行中改了 */
  changed: [changes: Record<string, unknown>];
  /** 某个驱动的校准项在运行中改了 */
  calibrated: [driver: string, values: Record<string, number>];
}>();

/** 滑条步长：整数字段按量级给（毫秒类 100 一档），小数字段分 100 档 */
function fieldStep(field: AvatarTuningField): number {
  const span = (field.max ?? 1) - (field.min ?? 0);
  if (field.type === 'integer') return span > 5000 ? 100 : span > 100 ? 10 : 1;
  return span >= 10 ? 0.5 : 0.01;
}

function formatField(field: AvatarTuningField): string {
  const value = Number(field.value);
  return field.type === 'integer' ? String(Math.round(value)) : value.toFixed(2);
}

// 每个字段各自限流：同时拖两个滑条不会互相吞掉
const senders = new Map<string, (value: unknown) => void>();

function update(key: string, value: unknown): void {
  emit('changed', { [key]: value });
  let sender = senders.get(key);
  if (!sender) {
    sender = throttleLatest((v: unknown) => {
      avatarTuningApi
        .patchConfig({ [key]: v })
        .catch(error =>
          ElMessage.error(getApiErrorMessage(error, '修改调参失败（点"刷新"取回实际值）')),
        );
    }, 120);
    senders.set(key, sender);
  }
  sender(value);
}

const calibrators = new Map<string, (value: number) => void>();

function calibrate(driver: string, key: string, value: number): void {
  emit('calibrated', driver, { [key]: value });
  const id = `${driver}.${key}`;
  let sender = calibrators.get(id);
  if (!sender) {
    sender = throttleLatest((v: number) => {
      avatarTuningApi
        .calibrate(driver, { [key]: v })
        .catch(error =>
          ElMessage.error(getApiErrorMessage(error, '修改校准失败（点"刷新"取回实际值）')),
        );
    }, 120);
    calibrators.set(id, sender);
  }
  sender(value);
}
</script>

<style scoped>
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.hint {
  font-size: 12px;
  color: var(--text-secondary);
}

.group + .group {
  margin-top: var(--spacing-md);
}

.group-title {
  margin-bottom: var(--spacing-xs);
  font-size: 13px;
  font-weight: 600;
  color: var(--text-regular);
}

.field-row {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  min-height: 30px;
}

.field-name {
  width: 150px;
  flex-shrink: 0;
  font-size: 13px;
  cursor: help;
}

.grow {
  flex: 1;
}

.field-value {
  width: 56px;
  text-align: right;
  font-family: var(--font-mono);
  font-size: 12px;
}
</style>
