<template>
  <section class="card debug-card">
    <div class="card-head">
      <h2 class="card-title">字幕</h2>
      <div class="backend-chips">
        <template v-if="status && status.available">
          <span v-for="b in status.backends" :key="b.name" class="backend-chip">
            <span class="chip-dot" :class="b.enabled ? 'is-ok' : 'is-down'" />
            {{ b.name }}
          </span>
          <span v-if="status.backend_count === 0" class="backend-chip">
            <span class="chip-dot is-down" />未启用任何后端
          </span>
        </template>
        <span v-else-if="checked" class="backend-chip">
          <span class="chip-dot is-down" />字幕服务未注入
        </span>
      </div>
      <el-button size="small" :loading="statusesLoading" @click="loadStatus">刷新状态</el-button>
    </div>
    <div class="card-body">
      <el-input
        v-model="text"
        placeholder="输入要显示的字幕文本"
        maxlength="200"
        clearable
        @keyup.enter="sendSubtitle"
      />
      <div class="card-actions">
        <el-button type="primary" :disabled="!available" :loading="sending" @click="sendSubtitle"
          >发送测试字幕</el-button
        >
        <el-button :disabled="!available" :loading="clearing" @click="clearSubtitle">清空字幕</el-button>
      </div>
      <p class="card-hint">将实际显示在桌面悬浮窗与 OBS 字幕位；直播中测试观众会看见。</p>
      <!-- 页内预览：直接嵌入字幕组件页（与桌面悬浮窗 / OBS 字幕源同一页面），发测试即可看到真实效果 -->
      <div class="subtitle-preview-wrap">
        <div class="preview-head">
          <span class="preview-label">页内预览</span>
          <el-link
            href="/widget/subtitle"
            target="_blank"
            type="primary"
            :underline="false"
            class="preview-open-link"
            >新窗口打开</el-link
          >
        </div>
        <iframe
          src="/widget/subtitle"
          class="subtitle-preview"
          title="字幕页内预览"
        />
        <p class="card-hint">字幕位在画面底部，预览上下留白模拟实际画面；新窗口地址可直接填入 OBS 浏览器源。</p>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
// 字幕直测详情：无定时器与订阅，状态在挂载时加载一次（keep-alive 下复用缓存状态，可手动刷新）
import { computed, onMounted, ref } from 'vue';
import { ElMessage } from 'element-plus';
import { confirmAction } from '@/utils/confirmAction';
import { getApiErrorMessage } from '@/utils/apiError';
import { debugApi } from '@/api';
import { DEFAULT_DEBUG_TEXT } from '@/components/debug/debugDefaults';
import type { SubtitleStatusResponse } from '@/types';

const status = ref<SubtitleStatusResponse | null>(null);
const checked = ref(false);
const statusesLoading = ref(false);
// 预填内置默认测试文本：打开页面即可直接点发送
const text = ref(DEFAULT_DEBUG_TEXT);
const sending = ref(false);
const clearing = ref(false);

const available = computed(() => status.value?.available ?? false);

async function loadStatus(): Promise<void> {
  statusesLoading.value = true;
  try {
    const { data } = await debugApi.getSubtitleStatus();
    status.value = data;
  } catch {
    // 503（未注入）也回填 available=false，页面据此显示"未注入"徽标
    status.value = { available: false, backend_count: 0, backends: [] };
  } finally {
    checked.value = true;
    statusesLoading.value = false;
  }
}

async function sendSubtitle(): Promise<void> {
  const value = text.value.trim();
  if (!value) {
    ElMessage.warning('请先输入字幕文本');
    return;
  }
  sending.value = true;
  try {
    const { data } = await debugApi.testSubtitle({ text: value });
    if (data.success) {
      ElMessage.success(`已推送到 ${data.backend_count} 个字幕后端`);
    } else {
      ElMessage.error(data.error || '推送失败');
    }
  } catch (err) {
    ElMessage.error(getApiErrorMessage(err, '推送失败'));
  } finally {
    sending.value = false;
  }
}

async function clearSubtitle(): Promise<void> {
  if (
    !(await confirmAction(
      '将清空后端当前字幕（OBS 字幕源会立即变空白）。确定要清空吗？',
      '清空字幕',
    ))
  )
    return;
  clearing.value = true;
  try {
    const { data } = await debugApi.clearSubtitle();
    if (data.success) {
      ElMessage.success('已清空全部字幕后端');
    } else {
      ElMessage.error(data.error || '清空失败');
    }
  } catch (err) {
    ElMessage.error(getApiErrorMessage(err, '清空失败'));
  } finally {
    clearing.value = false;
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

.subtitle-preview-wrap {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-xs);
  margin-top: var(--spacing-sm);
}

.preview-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--spacing-sm);
}

.preview-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-regular);
}

.preview-open-link {
  font-size: 12px;
}

/* 字幕位通常在画面底部：上下留白模拟实际画面比例 */
.subtitle-preview {
  width: 100%;
  height: 240px;
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-md);
  background: var(--bg-elevated);
}
</style>
