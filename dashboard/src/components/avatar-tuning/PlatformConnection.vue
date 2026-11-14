<template>
  <el-card class="platform-connection" shadow="never">
    <template #header>
      <div class="card-header">
        <span>平台连接</span>
        <span class="hint">排查各平台连没连上、参数写入是否生效时展开用</span>
      </div>
    </template>

    <el-collapse v-model="expanded" @change="onExpanded">
      <el-collapse-item name="diagnosis" title="连接诊断与参数直调">
        <div class="toolbar">
          <el-button size="small" :loading="loading" @click="refresh">刷新</el-button>
        </div>

        <el-alert
          v-if="loadError"
          type="error"
          :title="loadError"
          :closable="false"
          class="status-alert"
        />

        <div
          v-for="platformStatus in platforms"
          :key="platformStatus.platform"
          class="platform-row"
        >
          <div class="platform-title">
            <span class="platform-name">{{ platformLabel(platformStatus.platform) }}</span>
            <el-tag :type="tagType(platformStatus.connected)" size="small">{{
              connectedText(platformStatus.connected)
            }}</el-tag>
          </div>

          <p v-if="platformStatus.detail" class="detail-text">{{ platformStatus.detail }}</p>

          <div v-if="platformStatus.current_expression" class="current-row">
            当前表情：<strong>{{ platformStatus.current_expression }}</strong>
          </div>

          <div
            v-if="platformStatus.stats && Object.keys(platformStatus.stats).length"
            class="stats-row"
          >
            <el-tag
              v-for="(value, key) in platformStatus.stats"
              :key="key"
              size="small"
              type="info"
              class="stat-tag"
            >
              {{ key }}: {{ value }}
            </el-tag>
          </div>

          <!-- 参数直调只对保留参数写入方法的平台开放（与后端 PARAMETER_CAPABLE_PLATFORMS 一致） -->
          <template v-if="parameterCapable.includes(platformStatus.platform)">
            <div class="section-title">
              参数直调（{{ platformLabel(platformStatus.platform) }} 设备参数）
            </div>
            <div class="parameter-form">
              <el-input
                v-model="parameterForm.key"
                placeholder="参数名（如 MouthSmile）"
                class="param-input"
                size="small"
              />
              <!-- 后端未暴露参数取值范围元数据，只给数值输入框不给滑杆 -->
              <el-input-number
                v-model="parameterForm.value"
                :step="0.1"
                size="small"
                class="param-value"
              />
              <el-button
                type="warning"
                size="small"
                :disabled="platformStatus.connected !== true || !parameterForm.key || triggering"
                @click="triggerParameter(platformStatus.platform)"
              >
                写入
              </el-button>
            </div>
          </template>

          <p
            v-if="triggerResults[platformStatus.platform]"
            class="trigger-result"
            :class="{ fail: !triggerResults[platformStatus.platform].success }"
          >
            {{ triggerResults[platformStatus.platform].text }}
          </p>
        </div>
      </el-collapse-item>
    </el-collapse>
  </el-card>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue';
import { avatarApi } from '@/api';
import type { AvatarDebugPlatformStatus } from '@/types';
import { getApiErrorMessage } from '@/utils/apiError';

/**
 * 平台连接诊断区块（形象调校页右栏）：四平台连接态徽标、失败原因、统计摘要
 * 与 VTS 参数直调。
 *
 * 这是排查时才用的诊断工具，不是日常调参视野的一部分，所以折叠面板默认
 * 收起；数据在展开时拉取一次，之后靠面板内的"刷新"手动重取，不做轮询。
 *
 * 生命周期语义（keep-alive）：无定时器、无订阅，展开时的那次拉取是唯一的
 * 主动请求，页面失活无需做任何清理。
 */

const PLATFORM_LABELS: Record<string, string> = {
  vts: 'VTube Studio',
  vrchat: 'VRChat',
  warudo: 'Warudo',
  expression_sheet: '表情差分形象',
};

/** 支持参数直调的平台（与后端 PARAMETER_CAPABLE_PLATFORMS 一致） */
const parameterCapable = ['vts'];

const expanded = ref<string[]>([]);
const platforms = ref<AvatarDebugPlatformStatus[]>([]);
const loading = ref(false);
const loadError = ref('');
const triggering = ref(false);

interface ParameterForm {
  key: string;
  value: number;
}

const parameterForm = reactive<ParameterForm>({ key: '', value: 1 });
const triggerResults = reactive<Record<string, { success: boolean; text: string }>>({});

function platformLabel(platform: string): string {
  return PLATFORM_LABELS[platform] ?? platform;
}

function tagType(connected: boolean | null): 'success' | 'danger' | 'info' {
  if (connected === true) return 'success';
  if (connected === false) return 'danger';
  return 'info';
}

function connectedText(connected: boolean | null): string {
  if (connected === true) return '已连接';
  if (connected === false) return '未连接';
  return '未知';
}

async function refresh(): Promise<void> {
  loading.value = true;
  loadError.value = '';
  try {
    const { data } = await avatarApi.getDebugStatus();
    platforms.value = data.platforms;
  } catch (error) {
    loadError.value = getApiErrorMessage(error, '连接状态加载失败');
  } finally {
    loading.value = false;
  }
}

/** 首次展开时拉取一次状态；之后不再自动刷新，靠面板内"刷新"按钮 */
async function onExpanded(names: string | string[]): Promise<void> {
  const list = Array.isArray(names) ? names : [names];
  if (list.includes('diagnosis') && platforms.value.length === 0) {
    await refresh();
  }
}

async function triggerParameter(platform: string): Promise<void> {
  if (!parameterForm.key) return;
  triggering.value = true;
  try {
    const { data } = await avatarApi.triggerDebug({
      platform,
      kind: 'parameter',
      key: parameterForm.key,
      value: parameterForm.value,
    });
    if (data.success) {
      triggerResults[platform] = {
        success: true,
        text: `已写入 ${parameterForm.key}（数值 ${data.value}）`,
      };
    } else {
      triggerResults[platform] = { success: false, text: `写入被拒绝：${data.detail}` };
    }
  } catch (error) {
    triggerResults[platform] = { success: false, text: getApiErrorMessage(error, '写入失败') };
  } finally {
    triggering.value = false;
  }
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

.toolbar {
  margin-bottom: var(--spacing-sm);
}

.status-alert {
  margin-bottom: var(--spacing-sm);
}

.platform-row {
  padding: var(--spacing-sm) 0;
  border-bottom: 1px solid var(--el-border-color-lighter, #ebeef5);
}

.platform-row:last-child {
  border-bottom: none;
}

.platform-title {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
}

.platform-name {
  font-weight: 600;
}

.detail-text {
  margin: var(--spacing-xs) 0 0;
  color: var(--text-secondary);
  font-size: 13px;
}

.current-row {
  margin-top: var(--spacing-xs);
  font-size: 13px;
}

.stats-row {
  display: flex;
  flex-wrap: wrap;
  gap: var(--spacing-xs);
  margin-top: var(--spacing-xs);
}

.stat-tag {
  font-family: var(--font-mono);
}

.section-title {
  margin: var(--spacing-sm) 0 var(--spacing-xs);
  font-size: 13px;
  font-weight: 600;
  color: var(--text-regular);
}

.parameter-form {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
}

.param-input {
  width: 200px;
}

.trigger-result {
  margin: var(--spacing-xs) 0 0;
  font-size: 13px;
  color: var(--color-success, #67c23a);
}

.trigger-result.fail {
  color: var(--color-danger, #f56c6c);
}
</style>
