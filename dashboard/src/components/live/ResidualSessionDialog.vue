<script setup lang="ts">
/**
 * 断线续播弹窗：上次进程未正常退出（崩溃 / kill / 断电）会留下未结账的
 * "进行中"场次行；应用启动时检测到即弹本窗，由用户拍板续播还是收尾。
 *
 * - 续播：场次接回为进行中场次并广播 live.started(resumed=True)，对话历史、
 *   流程单进度按场次主键接上，同一场直播不被切断；
 * - 收尾：补结束时间为最后活动时刻（等价旧版启动自动收口），本场归档不再续播；
 * - 稍后：关闭弹窗不动场次，待决状态保持（场次列表仍显示进行中），下次启动再问。
 *
 * 打开期间每次显隐都会重新拉取待决列表；动作完成后刷新列表，全部处置完自动关闭。
 * 挂在 App.vue 全局层（keep-alive 之外）：用户落在任何页面都能看到弹窗。
 */
import { ref, watch } from 'vue';
import { ElMessage } from 'element-plus';
import { liveSessionsApi } from '@/api';
import type { LiveSessionItem } from '@/types';
import { getApiErrorMessage } from '@/utils/apiError';
import { formatDateTimeCn } from '@/utils/format';

const props = defineProps<{
  /** 弹窗显隐（v-model） */
  modelValue: boolean;
}>();

const emit = defineEmits<{
  'update:modelValue': [value: boolean];
  /** 有场次被续播后上抛（父级可刷新依赖场次状态的视图） */
  resumed: [];
}>();

const residuals = ref<LiveSessionItem[]>([]);
const loadError = ref('');
const loading = ref(false);
const actingId = ref<number | null>(null);

async function load(): Promise<void> {
  loading.value = true;
  loadError.value = '';
  try {
    const response = await liveSessionsApi.listResidual();
    residuals.value = response.data.items;
  } catch {
    loadError.value = '读取待决残留场次失败';
  } finally {
    loading.value = false;
  }
}

watch(
  () => props.modelValue,
  visible => {
    if (visible) void load();
  },
);

async function onResume(item: LiveSessionItem): Promise<void> {
  actingId.value = item.live_session_id;
  try {
    await liveSessionsApi.resume(item.live_session_id);
    ElMessage.success('场次已续播，直播从中断处继续');
    emit('resumed');
    closeIfEmpty(item.live_session_id);
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, '续播失败'));
  } finally {
    actingId.value = null;
  }
}

async function onDiscard(item: LiveSessionItem): Promise<void> {
  actingId.value = item.live_session_id;
  try {
    await liveSessionsApi.discardResidual(item.live_session_id);
    ElMessage.success('残留场次已收尾归档');
    closeIfEmpty(item.live_session_id);
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, '收尾失败'));
  } finally {
    actingId.value = null;
  }
}

function closeIfEmpty(handledId: number): void {
  residuals.value = residuals.value.filter(item => item.live_session_id !== handledId);
  if (residuals.value.length === 0) {
    emit('update:modelValue', false);
  }
}

function onVisibleChange(visible: boolean): void {
  if (actingId.value !== null) return;
  emit('update:modelValue', visible);
}
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    title="检测到未结束的直播场次"
    width="520px"
    :close-on-click-modal="false"
    @update:model-value="onVisibleChange"
  >
    <div class="rs-hint">
      上次程序退出时有一场直播没有正常收尾。续播会把这场接回来继续播（对话与流程单进度都接上）；
      收尾则把这场按中断时刻归档。若不处理，消息不会落库；之后开新场次会自动把残留场次收尾。
    </div>
    <div v-if="loadError" class="rs-error">{{ loadError }}</div>
    <div
      v-for="item in residuals"
      :key="item.live_session_id"
      class="rs-item"
      v-loading="actingId === item.live_session_id"
    >
      <div class="rs-info">
        <div class="rs-title">
          {{ item.title || `未命名场次 #${item.live_session_id}` }}
          <el-tag size="small" :type="item.kind === 'live' ? 'danger' : 'info'">
            {{ item.kind === 'live' ? '正式' : '彩排' }}
          </el-tag>
        </div>
        <div class="rs-meta">
          开播于 {{ formatDateTimeCn(item.started_at_ms) }} · 已有 {{ item.message_count }} 条消息
        </div>
      </div>
      <div class="rs-actions">
        <el-button
          type="primary"
          size="small"
          :disabled="actingId !== null"
          @click="onResume(item)"
        >
          续播这场
        </el-button>
        <el-button size="small" :disabled="actingId !== null" @click="onDiscard(item)"
          >收尾归档</el-button
        >
      </div>
    </div>
    <template #footer>
      <el-button :disabled="actingId !== null" @click="emit('update:modelValue', false)"
        >稍后再说</el-button
      >
    </template>
  </el-dialog>
</template>

<style scoped>
.rs-hint {
  margin-bottom: 12px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--color-text-secondary, #909399);
}

.rs-error {
  margin-bottom: 12px;
  color: var(--color-danger);
}

.rs-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 12px;
  border: 1px solid var(--color-border, #dcdfe6);
  border-radius: 6px;
}

.rs-item + .rs-item {
  margin-top: 8px;
}

.rs-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
}

.rs-meta {
  margin-top: 4px;
  font-size: 12px;
  color: var(--color-text-secondary, #909399);
}

.rs-actions {
  flex-shrink: 0;
  white-space: nowrap;
}
</style>
