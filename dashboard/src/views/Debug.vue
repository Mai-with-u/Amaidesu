<template>
  <div class="debug-page">
    <!-- 页面头：身份 + 定位说明                                                       -->
    <header class="page-header">
      <div class="header-left">
        <h1 class="page-title">调试</h1>
        <p class="page-subtitle">基础设施直测 · 发一条看效果，不经过 LLM 与弹幕链路</p>
      </div>
      <div class="header-actions">
        <el-button :loading="statusesLoading" @click="loadStatuses">刷新状态</el-button>
      </div>
    </header>

    <!-- 字幕卡：后端状态徽标 + 发送 / 清空                                            -->
    <section class="card debug-card">
      <div class="card-head">
        <h2 class="card-title">字幕</h2>
        <div class="backend-chips">
          <template v-if="subtitleStatus && subtitleStatus.available">
            <span v-for="b in subtitleStatus.backends" :key="b.name" class="backend-chip">
              <span class="chip-dot" :class="b.enabled ? 'is-ok' : 'is-down'" />
              {{ b.name }}
            </span>
            <span v-if="subtitleStatus.backend_count === 0" class="backend-chip">
              <span class="chip-dot is-down" />未启用任何后端
            </span>
          </template>
          <span v-else-if="subtitleChecked" class="backend-chip">
            <span class="chip-dot is-down" />字幕服务未注入
          </span>
        </div>
      </div>
      <div class="card-body">
        <el-input
          v-model="subtitleText"
          placeholder="输入要显示的字幕文本"
          maxlength="200"
          clearable
          @keyup.enter="sendSubtitle"
        />
        <div class="card-actions">
          <el-button
            type="primary"
            :disabled="!subtitleAvailable"
            :loading="subtitleSending"
            @click="sendSubtitle"
            >发送测试字幕</el-button
          >
          <el-button
            :disabled="!subtitleAvailable"
            :loading="subtitleClearing"
            @click="clearSubtitle"
            >清空字幕</el-button
          >
        </div>
        <p class="card-hint">将实际显示在桌面悬浮窗与 OBS 字幕位；直播中测试观众会看见。</p>
      </div>
    </section>

    <!-- TTS 卡：引擎状态徽标 + 试说                                                   -->
    <section class="card debug-card">
      <div class="card-head">
        <h2 class="card-title">TTS 语音</h2>
        <div class="backend-chips">
          <template v-if="ttsAvailable">
            <span class="backend-chip">
              <span class="chip-dot" :class="ttsConnected ? 'is-ok' : 'is-down'" />
              {{ ttsEngineName || 'TTS 引擎' }}
            </span>
          </template>
          <span v-else-if="ttsChecked" class="backend-chip">
            <span class="chip-dot is-down" />TTS 引擎未装配
          </span>
        </div>
      </div>
      <div class="card-body">
        <el-input
          v-model="ttsText"
          placeholder="输入要朗读的文本"
          maxlength="200"
          clearable
          @keyup.enter="testTts"
        />
        <div class="card-actions">
          <el-button type="primary" :disabled="!ttsAvailable" :loading="ttsTesting" @click="testTts"
            >试说一句</el-button
          >
        </div>
        <p class="card-hint">
          经真实语音引擎合成并从扬声器播放，字幕会同步显示；等待时长随文本长度。
        </p>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { ElMessage } from 'element-plus';
import { debugApi } from '@/api';
import type { SubtitleStatusResponse, TtsStatusResponse } from '@/types';

// 字幕卡状态
const subtitleStatus = ref<SubtitleStatusResponse | null>(null);
const subtitleChecked = ref(false);
const subtitleText = ref('');
const subtitleSending = ref(false);
const subtitleClearing = ref(false);

// TTS 卡状态
const ttsStatus = ref<TtsStatusResponse | null>(null);
const ttsChecked = ref(false);
const ttsText = ref('');
const ttsTesting = ref(false);

const statusesLoading = ref(false);

const subtitleAvailable = computed(() => subtitleStatus.value?.available ?? false);
const ttsAvailable = computed(() => ttsStatus.value?.available ?? false);
const ttsEngineName = computed(() => {
  const name = ttsStatus.value?.stats?.name;
  return typeof name === 'string' ? name : '';
});
const ttsConnected = computed(() => ttsStatus.value?.stats?.is_connected === true);

/** 从 axios 错误中取可读文案：优先后端 detail（如 503 的"未注入"），退回通用文案 */
function extractError(err: unknown, fallback: string): string {
  const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
  return detail || fallback;
}

async function loadStatuses(): Promise<void> {
  statusesLoading.value = true;
  try {
    const [subRes, ttsRes] = await Promise.allSettled([
      debugApi.getSubtitleStatus(),
      debugApi.getTtsStatus(),
    ]);
    // 503（未注入/未装配）也回填 available=false，页面据此显示"未装配"徽标
    subtitleStatus.value =
      subRes.status === 'fulfilled'
        ? subRes.value.data
        : { available: false, backend_count: 0, backends: [] };
    subtitleChecked.value = true;
    ttsStatus.value =
      ttsRes.status === 'fulfilled' ? ttsRes.value.data : { available: false, stats: {} };
    ttsChecked.value = true;
  } finally {
    statusesLoading.value = false;
  }
}

async function sendSubtitle(): Promise<void> {
  const text = subtitleText.value.trim();
  if (!text) {
    ElMessage.warning('请先输入字幕文本');
    return;
  }
  subtitleSending.value = true;
  try {
    const { data } = await debugApi.testSubtitle({ text });
    if (data.success) {
      ElMessage.success(`已推送到 ${data.backend_count} 个字幕后端`);
    } else {
      ElMessage.error(data.error || '推送失败');
    }
  } catch (err) {
    ElMessage.error(extractError(err, '推送失败'));
  } finally {
    subtitleSending.value = false;
  }
}

async function clearSubtitle(): Promise<void> {
  subtitleClearing.value = true;
  try {
    const { data } = await debugApi.clearSubtitle();
    if (data.success) {
      ElMessage.success('已清空全部字幕后端');
    } else {
      ElMessage.error(data.error || '清空失败');
    }
  } catch (err) {
    ElMessage.error(extractError(err, '清空失败'));
  } finally {
    subtitleClearing.value = false;
  }
}

async function testTts(): Promise<void> {
  const text = ttsText.value.trim();
  if (!text) {
    ElMessage.warning('请先输入朗读文本');
    return;
  }
  ttsTesting.value = true;
  try {
    const { data } = await debugApi.testTts({ text });
    if (data.success) {
      ElMessage.success('已播放');
    } else {
      ElMessage.error(data.error || '合成失败');
    }
  } catch (err) {
    ElMessage.error(extractError(err, '合成失败'));
  } finally {
    ttsTesting.value = false;
  }
}

onMounted(() => {
  void loadStatuses();
});
</script>

<style scoped>
.debug-page {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-md);
  max-width: 860px;
}

.page-header {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--spacing-md);
}

.page-title {
  margin: 0;
  font-size: 22px;
  font-weight: 650;
  color: var(--text-primary);
}

.page-subtitle {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--text-secondary);
}

.debug-card {
  padding: var(--spacing-md) var(--spacing-lg);
}

.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
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
</style>
