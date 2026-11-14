<template>
  <section class="card debug-card">
    <div class="card-head">
      <h2 class="card-title">TTS 语音</h2>
      <div class="backend-chips">
        <template v-if="available">
          <span class="backend-chip">
            <span class="chip-dot" :class="connected ? 'is-ok' : 'is-down'" />
            {{ chipLabel }}
          </span>
        </template>
        <span v-else-if="checked" class="backend-chip">
          <span class="chip-dot is-down" />TTS 引擎未装配
        </span>
      </div>
      <el-button size="small" :loading="statusesLoading" @click="loadStatus">刷新状态</el-button>
    </div>
    <div class="card-body">
      <el-input
        v-model="text"
        placeholder="输入要朗读的文本"
        maxlength="200"
        clearable
        @keyup.enter="testTts"
      />
      <!-- 参数区按引擎上报的描述表渲染；引擎未上报（非 GSV）时整块不渲染 -->
      <div v-if="debugParams && debugParams.length > 0" class="tts-param-grid">
        <label v-for="param in debugParams" :key="param.key" class="tts-param">
          <span>{{ param.label }}</span>
          <el-input-number
            v-model="paramValues[param.key]"
            :min="param.min"
            :max="param.max"
            :step="param.step"
            :precision="param.precision"
          />
        </label>
        <el-button
          class="tts-param-reset"
          link
          type="primary"
          :disabled="!debugParams"
          @click="resetTtsParams"
          >重置为引擎配置</el-button
        >
      </div>
      <div class="card-actions">
        <el-button :disabled="!available" :loading="testing" @click="testTts"
          >试说一句（扬声器）</el-button
        >
        <el-button
          type="primary"
          :disabled="!available || !text.trim()"
          :loading="rendering"
          @click="renderTtsAudio"
          >生成试听音频</el-button
        >
        <el-button v-if="audioUrl" tag="a" :href="audioUrl" :download="audioFilename"
          >下载 wav</el-button
        >
      </div>
      <audio v-if="audioUrl" controls :src="audioUrl" class="tts-audio" />
      <p class="card-hint">
        试说经扬声器播放并联动字幕；生成试听按上方参数合成（表单初始值为引擎配置，非 GSV
        引擎不支持音频合成），可在页面内播放与下载对比。
      </p>
    </div>
  </section>
</template>

<script setup lang="ts">
// TTS 直测详情：无定时器与订阅，状态在挂载时加载一次（keep-alive 下复用缓存状态，可手动刷新）
import { computed, onMounted, ref } from 'vue';
import { ElMessage } from 'element-plus';
import { getApiErrorMessage } from '@/utils/apiError';
import { debugApi } from '@/api';
import { DEFAULT_DEBUG_TEXT } from '@/components/debug/debugDefaults';
import type { TtsDebugParamInfo, TtsStatusResponse } from '@/types';

const status = ref<TtsStatusResponse | null>(null);
const checked = ref(false);
const statusesLoading = ref(false);
// 预填默认文本：引擎上报的参考转写优先，无则落到与字幕卡同源的内置默认文本
const text = ref('');
const testing = ref(false);
const rendering = ref(false);
// 试听参数表单：键集与值域完全来自引擎上报的描述表，初始/重置基准 = 表内 default
const debugParams = ref<TtsDebugParamInfo[] | null>(null);
const paramValues = ref<Record<string, number>>({});
const audioUrl = ref('');
const audioFilename = ref('tts-debug.wav');

const available = computed(() => status.value?.available ?? false);
const engineName = computed(() => {
  const name = status.value?.stats?.name;
  return typeof name === 'string' ? name : '';
});
const connected = computed(() => status.value?.stats?.is_connected === true);
/** 引擎对接的 GPT-SoVITS API 后端（api_v2），缺失时不显示 */
const backend = computed(() => {
  const value = status.value?.stats?.backend;
  return typeof value === 'string' ? value : '';
});
const chipLabel = computed(() =>
  backend.value
    ? `${engineName.value || 'TTS 引擎'} · ${backend.value}`
    : engineName.value || 'TTS 引擎',
);

function resetTtsParams(): void {
  if (!debugParams.value) return;
  const values: Record<string, number> = {};
  for (const param of debugParams.value) {
    values[param.key] = param.default;
  }
  paramValues.value = values;
}

async function loadStatus(): Promise<void> {
  statusesLoading.value = true;
  try {
    const { data } = await debugApi.getTtsStatus();
    status.value = data;
  } catch {
    // 503（未装配）也回填 available=false，页面据此显示"未装配"徽标
    status.value = { available: false, stats: {} };
  } finally {
    checked.value = true;
    statusesLoading.value = false;
  }
  // 文本框与参数表单默认值（仅首次填充，手动刷新状态不覆盖用户已输入内容）
  const stats = status.value?.stats;
  if (!text.value) {
    const promptText = stats?.prompt_text;
    text.value = typeof promptText === 'string' && promptText ? promptText : DEFAULT_DEBUG_TEXT;
  }
  if (debugParams.value === null && status.value?.debug_params?.length) {
    debugParams.value = status.value.debug_params;
    resetTtsParams();
  }
}

async function testTts(): Promise<void> {
  const value = text.value.trim();
  if (!value) {
    ElMessage.warning('请先输入朗读文本');
    return;
  }
  testing.value = true;
  try {
    const { data } = await debugApi.testTts({ text: value, play_on_device: true });
    if (data.success) {
      ElMessage.success('已播放');
    } else {
      ElMessage.error(data.error || '合成失败');
    }
  } catch (err) {
    ElMessage.error(getApiErrorMessage(err, '合成失败'));
  } finally {
    testing.value = false;
  }
}

/** 生成试听音频：按参数覆盖合成（不占声卡），返回 wav 供页面播放与下载 */
async function renderTtsAudio(): Promise<void> {
  const value = text.value.trim();
  if (!value) {
    ElMessage.warning('请先输入朗读文本');
    return;
  }
  rendering.value = true;
  try {
    const { data } = await debugApi.testTts({
      text: value,
      play_on_device: false,
      temperature: paramValues.value.temperature,
      speed_factor: paramValues.value.speed_factor,
      sample_steps: paramValues.value.sample_steps,
      top_k: paramValues.value.top_k,
      top_p: paramValues.value.top_p,
    });
    if (!data.success || !data.audio_base64) {
      ElMessage.error(data.error || '合成失败');
      return;
    }
    const bytes = Uint8Array.from(atob(data.audio_base64), ch => ch.charCodeAt(0));
    const blob = new Blob([bytes], { type: 'audio/wav' });
    if (audioUrl.value) URL.revokeObjectURL(audioUrl.value);
    audioUrl.value = URL.createObjectURL(blob);
    audioFilename.value = `tts-debug-${new Date().toISOString().slice(11, 19).replace(/:/g, '')}.wav`;
    ElMessage.success('试听音频已生成');
  } catch (err) {
    ElMessage.error(getApiErrorMessage(err, '合成失败'));
  } finally {
    rendering.value = false;
  }
}

onMounted(() => {
  void loadStatus();
});
</script>

<style scoped>
.debug-card {
  padding: var(--spacing-md) var(--spacing-lg);
}

.card-head {
  display: flex;
  align-items: center;
  gap: var(--spacing-md);
  margin-bottom: var(--spacing-md);
}

.card-title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
}

.backend-chips {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--spacing-sm);
  flex: 1;
}

.backend-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 2px 10px;
  border: 1px solid var(--border-color-light);
  border-radius: 999px;
  font-size: 12px;
  color: var(--text-regular);
}

.chip-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

.chip-dot.is-ok {
  background-color: var(--color-success);
  box-shadow: 0 0 6px var(--color-success);
}

.chip-dot.is-down {
  background-color: var(--text-secondary);
}

.card-body {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
}

.card-actions {
  display: flex;
  gap: var(--spacing-sm);
}

.card-hint {
  margin: 0;
  font-size: 12px;
  color: var(--text-secondary);
}

.tts-param-grid {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--spacing-sm) var(--spacing-md);
}

.tts-param {
  display: flex;
  align-items: center;
  gap: var(--spacing-xs);
  font-size: 12px;
  color: var(--text-secondary);
}

.tts-param :deep(.el-input-number) {
  width: 120px;
}

.tts-param-reset {
  margin-left: auto;
}

.tts-audio {
  width: 100%;
  height: 36px;
}
</style>
