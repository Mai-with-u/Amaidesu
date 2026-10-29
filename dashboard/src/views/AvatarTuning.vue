<template>
  <div class="avatar-tuning">
    <div class="page-header">
      <div class="title-block">
        <h2>形象调校</h2>
        <div v-if="state" class="status-tags">
          <el-tag v-if="state.drivers.length" type="success" size="small">
            已挂上：{{ state.drivers.join('、') }}
          </el-tag>
          <el-tag v-else type="danger" size="small"
            >没有平台驱动：形象不会动（检查 VTS 是否连上）</el-tag
          >
          <el-tag size="small">表情：{{ emotionLabel(state.emotion) }}</el-tag>
          <el-tag size="small" type="info">
            心情：愉快 {{ state.mood.valence.toFixed(2) }} / 兴奋
            {{ state.mood.arousal.toFixed(2) }}
          </el-tag>
          <el-tag v-if="state.thinking" size="small" type="warning">在想</el-tag>
          <el-tag v-if="state.speaking" size="small" type="warning">在说</el-tag>
          <el-tag v-if="state.manual" size="small" type="danger">手动摆姿中</el-tag>
          <el-tag
            v-if="state.running"
            size="small"
            :type="state.loop.stalls ? 'warning' : 'info'"
            title="最近约 10 秒：实际帧率 / 最长帧间隔 / 超过两倍帧周期的卡顿次数"
          >
            {{ state.loop.fps.toFixed(1) }} 帧/秒 · 最长 {{ Math.round(state.loop.max_gap_ms) }} 毫秒 · 卡顿
            {{ state.loop.stalls }} 次
          </el-tag>
        </div>
      </div>
      <div class="header-actions">
        <span v-if="dirty" class="dirty-hint">有未保存的修改（重启后会丢）</span>
        <el-button :loading="loading" @click="loadProfile">刷新</el-button>
        <el-button type="primary" :loading="saving" :disabled="!profile" @click="save">
          保存到配置
        </el-button>
      </div>
    </div>

    <el-alert
      v-if="loadError"
      type="error"
      :title="loadError"
      :closable="false"
      class="page-alert"
    />

    <el-row v-if="profile" :gutter="16">
      <el-col :span="24" :lg="14" class="tuning-column">
        <GazeTargets :fields="profile.fields" :frame="frame" @changed="onFieldsChanged" />
        <RecipeEditor
          :recipes="profile.recipes"
          :recipe-keys="profile.recipe_keys"
          :motion-range="profile.motion_range"
          class="block"
          @changed="onRecipeChanged"
        />
        <el-card shadow="never" class="block">
          <template #header>动作</template>
          <el-button
            v-for="action in actions"
            :key="action.name"
            size="small"
            class="action-button"
            :title="action.description"
            @click="act(action.name)"
          >
            {{ action.name }}
            <span v-if="action.kind !== '动作'" class="action-kind">{{ action.kind }}</span>
          </el-button>
        </el-card>
      </el-col>
      <el-col :span="24" :lg="10" class="tuning-column">
        <LiveChannels :channels="profile.channels" :frame="frame" />
        <TuningFields
          :groups="groups"
          :calibrations="profile.calibrations"
          class="block"
          @changed="onFieldsChanged"
          @calibrated="onCalibrated"
        />
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { ElMessage } from 'element-plus';
import { avatarTuningApi } from '@/api';
import GazeTargets from '@/components/avatar-tuning/GazeTargets.vue';
import LiveChannels from '@/components/avatar-tuning/LiveChannels.vue';
import RecipeEditor from '@/components/avatar-tuning/RecipeEditor.vue';
import TuningFields from '@/components/avatar-tuning/TuningFields.vue';
import { useKeepAlivePolling } from '@/composables/useKeepAlivePolling';
import type { AvatarTuningProfile, AvatarTuningRecipe, AvatarTuningState } from '@/types';
import { getApiErrorMessage } from '@/utils/apiError';
import { emotionLabel, groupTuningFields } from '@/utils/avatarTuning';

/**
 * 形象调校页（/avatar-tuning）：看着形象实时调面捕合成器。
 *
 * 改动（调参、表情配方、眼神目标、平台校准）都只在运行中生效，点"保存到配置"
 * 才写回 avatar.toml；没保存就重启会回到文件里的值，页面顶部据此提示。静态信息
 * （字段说明、配方、通道范围、动作目录）进页时取一次，"刷新"重取；身体状态每
 * 250 毫秒轮询一次喂给各块。
 *
 * 生命周期语义（keep-alive）：状态轮询随页面激活启停（useKeepAlivePolling），
 * 离开页面即停；手动摆姿的续期与收尾由实时通道那一块自己负责。
 */

const STATE_POLL_MS = 250;

const profile = ref<AvatarTuningProfile | null>(null);
const state = ref<AvatarTuningState | null>(null);
const loading = ref(false);
const saving = ref(false);
const loadError = ref('');
const dirty = ref(false);

const frame = computed<Record<string, number>>(() => state.value?.frame ?? {});
const groups = computed(() => (profile.value ? groupTuningFields(profile.value.fields) : []));
// "保持表情"要同时给情绪与秒数，在配方那一块用预览代替，这里不列
const actions = computed(() => (profile.value?.actions ?? []).filter(a => a.kind !== '表情'));

async function loadProfile(): Promise<void> {
  loading.value = true;
  loadError.value = '';
  try {
    const { data } = await avatarTuningApi.getProfile();
    profile.value = data;
    // 以后端为准：别的页面或刷新前改过但没保存的，这里也提示
    dirty.value = data.unsaved;
  } catch (error) {
    loadError.value = getApiErrorMessage(error, '调校信息加载失败');
  } finally {
    loading.value = false;
  }
}

async function refreshState(): Promise<void> {
  if (!profile.value) return;
  try {
    const { data } = await avatarTuningApi.getState();
    state.value = data;
  } catch {
    // 轮询静默：后端短暂不可达时保留上一帧，下一拍再试；整页错误由"刷新"报告
  }
}

function onFieldsChanged(changes: Record<string, unknown>): void {
  if (!profile.value) return;
  for (const field of profile.value.fields) {
    if (field.key in changes) field.value = changes[field.key];
  }
  dirty.value = true;
}

function onRecipeChanged(emotion: string, recipe: AvatarTuningRecipe): void {
  if (!profile.value) return;
  profile.value.recipes[emotion] = recipe;
  dirty.value = true;
}

function onCalibrated(driver: string, values: Record<string, number>): void {
  const calibration = profile.value?.calibrations.find(c => c.driver === driver);
  if (!calibration) return;
  for (const item of calibration.items) {
    if (item.key in values) item.value = values[item.key];
  }
  dirty.value = true;
}

async function act(action: string): Promise<void> {
  try {
    const { data } = await avatarTuningApi.act(action);
    if (!data.success) ElMessage.warning(`"${action}" 没有做成（平台没回应或冷却中）`);
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, '动作失败'));
  }
}

async function save(): Promise<void> {
  saving.value = true;
  try {
    const { data } = await avatarTuningApi.save();
    dirty.value = false;
    ElMessage.success(`已写回 ${data.target_file ?? 'avatar.toml'}（${data.saved} 项）`);
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, '保存失败'));
  } finally {
    saving.value = false;
  }
}

useKeepAlivePolling(refreshState, STATE_POLL_MS);

onMounted(async () => {
  await loadProfile();
  await refreshState();
});
</script>

<style scoped>
.avatar-tuning {
  padding: var(--spacing-lg);
}

.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: var(--spacing-md);
}

.page-header h2 {
  margin: 0 0 var(--spacing-xs);
}

.status-tags {
  display: flex;
  flex-wrap: wrap;
  gap: var(--spacing-xs);
}

.header-actions {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
}

.dirty-hint {
  font-size: 12px;
  color: var(--el-color-warning, #e6a23c);
}

.page-alert {
  margin-bottom: var(--spacing-md);
}

.block {
  margin-top: var(--spacing-md);
}

/* 窄屏两栏上下堆叠时，栏与栏之间留出间距 */
.tuning-column {
  margin-bottom: var(--spacing-md);
}

.action-button {
  margin: 0 var(--spacing-xs) var(--spacing-xs) 0;
}

.action-kind {
  margin-left: 4px;
  font-size: 11px;
  color: var(--text-secondary);
}
</style>
