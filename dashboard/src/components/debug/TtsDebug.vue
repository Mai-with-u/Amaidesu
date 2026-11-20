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

      <el-divider content-position="left">整场回放</el-divider>

      <!-- 整场回放：选场次逐句送当前引擎（与直播实机同一 handle_speech 入口），调参数、比合成速度 -->
      <div class="replay-picker">
        <el-select
          v-model="selectedSessionId"
          placeholder="选择要回放的场次"
          :loading="sessionsLoading"
          :disabled="running"
          class="replay-session-select"
        >
          <el-option
            v-for="session in sessions"
            :key="session.live_session_id"
            :label="sessionLabel(session)"
            :value="session.live_session_id"
          />
        </el-select>
        <el-button :disabled="selectedSessionId === null || running" :loading="linesLoading" @click="loadLines"
          >载入台词</el-button
        >
      </div>
      <p v-if="sessionsError" class="card-hint is-error">{{ sessionsError }}</p>
      <p v-if="linesError" class="card-hint is-error">{{ linesError }}</p>

      <!-- 回放模式与耗时口径 -->
      <div class="replay-mode-row">
        <el-radio-group v-model="mode" :disabled="running">
          <el-radio-button value="play">扬声器播放</el-radio-button>
          <el-radio-button value="synth">仅合成测速</el-radio-button>
        </el-radio-group>
        <span class="mode-hint">{{ modeHint }}</span>
      </div>

      <!-- 控制与进度 -->
      <div class="card-actions">
        <el-button type="primary" :disabled="!canStart" @click="startReplay">开始回放</el-button>
        <el-button v-if="running" type="warning" @click="stopReplay">停止</el-button>
        <span v-if="lines.length > 0" class="replay-progress">{{ doneCount }} / {{ lines.length }}</span>
      </div>
      <p v-if="currentLine" class="replay-current">
        正在回放：<TtsStreamText :text="liveText || currentLine.content" :lit="liveLit" />
      </p>

      <!-- 汇总统计（内部口径毫秒，显示按秒） -->
      <div v-if="summary.total > 0" class="replay-summary">
        <span>成功 {{ summary.okCount }}</span>
        <span v-if="summary.failedCount > 0" class="is-failed">失败 {{ summary.failedCount }}</span>
        <span>总耗时 {{ formatSeconds(summary.totalMs) }}</span>
        <span>平均每句 {{ formatSeconds(summary.avgMs) }}</span>
        <span v-if="summary.avgFirstAudioMs !== null" title="请求起点到首个音频块出声：观众等主播开口的时长"
          >平均首声 {{ formatSeconds(summary.avgFirstAudioMs) }}</span
        >
        <span>平均每字 {{ formatSeconds(summary.perCharMs) }}/字</span>
        <el-button link type="primary" @click="copySummary">复制汇总</el-button>
      </div>

      <!-- 每句耗时趋势：首声延迟/总耗时两线（无分段回退单线），均值虚线标尺 -->
      <TtsReplayChart
        v-if="results.length > 0"
        :results="results"
        :avg-ms="summary.avgMs"
        :avg-first-audio-ms="summary.avgFirstAudioMs"
      />

      <!-- 台词明细：回放前是预览，回放后逐行出耗时；弹性占满卡片剩余高度，表内滚动 -->
      <div v-if="lines.length > 0" class="replay-table-wrap">
        <el-table :data="lines" size="small" height="100%" class="replay-table">
          <el-table-column type="index" label="#" width="48" />
          <el-table-column label="台词" min-width="240">
            <template #default="{ row }">
              <TtsStreamText v-if="row.chat_id === liveChatId" :text="row.content" :lit="liveLit" />
              <span v-else :class="{ 'is-dropped': row.dropped }">{{ row.content }}</span>
              <el-tag v-if="row.dropped" size="small" type="info" class="replay-dropped-tag">未送达</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="字数" width="60">
            <template #default="{ row }">{{ row.content.length }}</template>
          </el-table-column>
          <el-table-column label="耗时" width="100">
            <template #default="{ row }">
              <span :class="{ 'is-failed': isFailedRow(row.chat_id) }" :title="errorOf(row.chat_id)">{{
                resultText(row.chat_id)
              }}</span>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <!-- 测速模式：最近一句合成音频试听 -->
      <template v-if="replayAudioUrl">
        <audio controls :src="replayAudioUrl" class="tts-audio" />
        <el-button tag="a" :href="replayAudioUrl" :download="replayAudioFilename">下载 wav</el-button>
      </template>

      <p class="card-hint">
        回放经当前引擎的 handle_speech 生产入口逐句驱动，与直播实机同一链路（同文本规范化、同合成参数）；数据为场次落库台词。
      </p>
    </div>
  </section>
</template>

<script setup lang="ts">
// TTS 直测详情：单句直测 + 整场回放共用卡片头的一份引擎状态（挂载时加载一次，
// keep-alive 下复用缓存状态，可手动刷新）。回放循环/统计/清理逻辑在 useTtsReplay，
// 引擎可用性与名称由本组件注入（单一状态源）；循环为纯请求串行（无定时器/订阅），
// 调试页 keep-alive 切走后继续跑，组件卸载时 dispose() 置停止并释放测速 blob URL。
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { ElMessage } from 'element-plus';
import { getApiErrorMessage } from '@/utils/apiError';
import { debugApi } from '@/api';
import { DEFAULT_DEBUG_TEXT } from '@/components/debug/debugDefaults';
import type { TtsDebugParamInfo, TtsStatusResponse, LiveSessionItem } from '@/types';
import { formatDateTimeCn, formatSeconds } from '@/utils/format';
import { useTtsReplay, type ReplayLineResult } from '@/composables/useTtsReplay';
import TtsReplayChart from '@/components/debug/TtsReplayChart.vue';
import TtsStreamText from '@/components/debug/TtsStreamText.vue';

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

// ----- 整场回放（引擎状态由上方 loadStatus 单一供给） -----
const {
  sessions,
  sessionsLoading,
  sessionsError,
  loadSessions,
  selectedSessionId,
  lines,
  linesLoading,
  linesError,
  loadLines,
  mode,
  running,
  doneCount,
  currentLine,
  canStart,
  startReplay,
  stopReplay,
  results,
  liveChatId,
  liveText,
  liveLit,
  audioUrl: replayAudioUrl,
  audioFilename: replayAudioFilename,
  summary,
  buildSummaryText,
  dispose,
} = useTtsReplay({ engineAvailable: available, engineName });

const modeHint = computed(() =>
  mode.value === 'play'
    ? '逐句合成并在扬声器播放，播完一句才发下一句；耗时 = 服务端处理时长（含播放）'
    : '只合成不播放，跑完全场更快；耗时 = 纯合成时间（引擎不支持合成接口时逐句报错）',
);

function sessionLabel(session: LiveSessionItem): string {
  const kindLabel = session.kind === 'rehearsal' ? '彩排 · ' : '';
  return `${kindLabel}${session.title || '未命名场次'} · ${formatDateTimeCn(session.started_at_ms)}`;
}

/** 回放结果按 chat_id 对回台词行（表格逐行出耗时用） */
const resultMap = computed(() => {
  const map = new Map<number, ReplayLineResult>();
  for (const result of results.value) map.set(result.chatId, result);
  return map;
});

function resultText(chatId: number): string {
  const result = resultMap.value.get(chatId);
  if (!result) return '—';
  if (!result.success) return '失败';
  return result.elapsedMs !== null ? formatSeconds(result.elapsedMs) : '请求失败';
}

function isFailedRow(chatId: number): boolean {
  const result = resultMap.value.get(chatId);
  return result ? !result.success : false;
}

function errorOf(chatId: number): string {
  return resultMap.value.get(chatId)?.error || '';
}

async function copySummary(): Promise<void> {
  try {
    await navigator.clipboard.writeText(buildSummaryText());
    ElMessage.success('汇总已复制');
  } catch {
    ElMessage.error('复制失败');
  }
}

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
  void loadSessions();
});

onBeforeUnmount(() => {
  dispose();
});
</script>

<style scoped>
.debug-card {
  padding: var(--spacing-md) var(--spacing-lg);
  /* 铺满详情面板高度（面板自身滚动，内容不足时卡片框压到底） */
  min-height: 100%;
  display: flex;
  flex-direction: column;
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
  /* 吃掉卡片除头部外的剩余高度，让台词明细表撑到面板底 */
  flex: 1;
  min-height: 0;
}

.card-actions {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
}

.card-hint {
  margin: 0;
  font-size: 12px;
  color: var(--text-secondary);
}

.card-hint.is-error {
  color: var(--color-danger, #f56c6c);
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

.replay-picker {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
}

.replay-session-select {
  width: 360px;
}

.replay-mode-row {
  display: flex;
  align-items: center;
  gap: var(--spacing-md);
}

.mode-hint {
  font-size: 12px;
  color: var(--text-secondary);
}

.replay-progress {
  font-size: 13px;
  color: var(--text-regular);
  font-variant-numeric: tabular-nums;
}

.replay-current {
  margin: 0;
  font-size: 12px;
  color: var(--text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.replay-summary {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--spacing-sm) var(--spacing-md);
  padding: var(--spacing-xs) var(--spacing-sm);
  border: 1px solid var(--border-color-light);
  border-radius: 6px;
  font-size: 12px;
  color: var(--text-regular);
  font-variant-numeric: tabular-nums;
}

.replay-summary .is-failed {
  color: var(--color-danger, #f56c6c);
}

.replay-table-wrap {
  /* 弹性占满卡片剩余高度，el-table height=100% 在其内部滚动 */
  flex: 1;
  min-height: 120px;
}

.replay-table .is-failed {
  color: var(--color-danger, #f56c6c);
}

.replay-table :deep(.is-dropped) {
  color: var(--text-secondary);
}

.replay-dropped-tag {
  margin-left: 6px;
}
</style>
