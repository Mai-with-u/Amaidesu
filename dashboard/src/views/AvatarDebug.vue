<template>
  <div class="avatar-debug">
    <div class="page-header">
      <h2>形象调试</h2>
      <el-button :loading="loading" @click="refresh">刷新状态</el-button>
    </div>

    <el-alert
      v-if="loadError"
      type="error"
      :title="loadError"
      :closable="false"
      class="status-alert"
    />

    <el-row :gutter="16">
      <el-col v-for="platform in platforms" :key="platform.platform" :span="12">
        <el-card class="platform-card" :class="{ disconnected: platform.connected === false }">
          <template #header>
            <div class="card-header">
              <span class="platform-name">{{ platformLabel(platform.platform) }}</span>
              <el-tag :type="tagType(platform.connected)" size="small">{{
                connectedText(platform.connected)
              }}</el-tag>
            </div>
          </template>

          <p v-if="platform.detail" class="detail-text">{{ platform.detail }}</p>

          <div v-if="platform.current_expression" class="current-row">
            当前表情：<strong>{{ platform.current_expression }}</strong>
          </div>

          <div v-if="platform.stats && Object.keys(platform.stats).length" class="stats-row">
            <el-tag
              v-for="(value, key) in platform.stats"
              :key="key"
              size="small"
              type="info"
              class="stat-tag"
            >
              {{ key }}: {{ value }}
            </el-tag>
          </div>

          <!-- 预设动作触发（目录来自 status 的契约方法 list_preset_actions） -->
          <div v-if="platform.actions.length" class="action-section">
            <div class="section-title">预设动作</div>
            <el-button
              v-for="action in platform.actions"
              :key="action.name"
              size="small"
              :disabled="platform.connected !== true || triggering"
              @click="triggerAction(platform.platform, action.name)"
            >
              {{ action.name }}
            </el-button>
          </div>

          <!-- 表情触发（17 情绪枚举 + 强度滑杆，走契约 set_expression） -->
          <div class="section-title">表情触发</div>
          <div class="expression-form">
            <el-select
              v-model="expressionForms[platform.platform].emotion"
              filterable
              placeholder="情绪"
              class="emotion-select"
            >
              <el-option
                v-for="emotion in platform.emotions"
                :key="emotion"
                :label="emotion"
                :value="emotion"
              />
            </el-select>
            <el-slider
              v-model="expressionForms[platform.platform].intensity"
              :min="0"
              :max="1"
              :step="0.05"
              class="intensity-slider"
            />
            <span class="intensity-value">{{
              expressionForms[platform.platform].intensity.toFixed(2)
            }}</span>
            <el-button
              type="primary"
              size="small"
              :disabled="
                platform.connected !== true ||
                !expressionForms[platform.platform].emotion ||
                triggering
              "
              @click="triggerExpression(platform.platform)"
            >
              触发
            </el-button>
          </div>

          <!-- 参数直调（仅保留参数写入方法的平台开放，其余隐藏入口） -->
          <template v-if="parameterCapable.includes(platform.platform)">
            <div class="section-title">
              参数直调（{{ platformLabel(platform.platform) }} 设备参数）
            </div>
            <div class="parameter-form">
              <el-input
                v-model="parameterForms[platform.platform].key"
                placeholder="参数名（如 MouthSmile）"
                class="param-input"
                size="small"
              />
              <!-- 后端未暴露参数取值范围元数据，只给数值输入框不给滑杆 -->
              <el-input-number
                v-model="parameterForms[platform.platform].value"
                :step="0.1"
                size="small"
                class="param-value"
              />
              <el-button
                type="warning"
                size="small"
                :disabled="
                  platform.connected !== true ||
                  !parameterForms[platform.platform].key ||
                  triggering
                "
                @click="triggerParameter(platform.platform)"
              >
                写入
              </el-button>
            </div>
          </template>

          <p
            v-if="triggerResults[platform.platform]"
            class="trigger-result"
            :class="{ fail: !triggerResults[platform.platform].success }"
          >
            {{ triggerResults[platform.platform].text }}
          </p>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue';
import { avatarApi } from '@/api';
import { getApiErrorMessage } from '@/utils/apiError';
import type { AvatarDebugPlatformStatus } from '@/types';

/**
 * 形象表情调试台（/avatar-debug）。
 *
 * 数据与执行全部走后端平台无关契约端点（GET /avatar/debug/status /
 * POST /avatar/debug/trigger）：状态卡显示连接态与统计摘要；表情触发用
 * 17 情绪枚举 + 强度滑杆（契约语义 0.0-1.0，范围由契约保证故可用滑杆）；
 * 参数直调只在保留参数写入方法的平台（VTS）开放，后端未返回参数范围
 * 元数据，因此只提供数值输入框。预设动作按钮目录来自 status 聚合。
 *
 * 生命周期语义：本页为常规壳内页面（非 keep-alive 容器），setup 拉取
 * 一次状态 + 手动刷新，无定时器与订阅，无激活态启停需求。
 */

const PLATFORM_LABELS: Record<string, string> = {
  vts: 'VTube Studio',
  vrchat: 'VRChat',
  warudo: 'Warudo',
  expression_sheet: '表情差分形象',
};

/** 支持参数直调的平台（与后端 PARAMETER_CAPABLE_PLATFORMS 一致） */
const parameterCapable = ['vts'];

const platforms = ref<AvatarDebugPlatformStatus[]>([]);
const loading = ref(false);
const loadError = ref('');
const triggering = ref(false);

interface ExpressionForm {
  emotion: string;
  intensity: number;
}
interface ParameterForm {
  key: string;
  value: number;
}

const expressionForms = reactive<Record<string, ExpressionForm>>({});
const parameterForms = reactive<Record<string, ParameterForm>>({});
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
    for (const platform of data.platforms) {
      if (!expressionForms[platform.platform]) {
        expressionForms[platform.platform] = { emotion: '', intensity: 0.5 };
      }
      if (!parameterForms[platform.platform]) {
        parameterForms[platform.platform] = { key: '', value: 1 };
      }
    }
  } catch (error) {
    loadError.value = getApiErrorMessage(error, '状态加载失败');
  } finally {
    loading.value = false;
  }
}

async function doTrigger(
  platform: string,
  kind: 'expression' | 'parameter' | 'action',
  key: string,
  value: number,
): Promise<void> {
  triggering.value = true;
  try {
    const { data } = await avatarApi.triggerDebug({ platform, kind, key, value });
    if (data.success) {
      triggerResults[platform] = {
        success: true,
        text: `已触发 ${kind}:${key}（强度/数值 ${data.value}）`,
      };
    } else {
      triggerResults[platform] = { success: false, text: `触发被拒绝：${data.detail}` };
    }
  } catch (error) {
    triggerResults[platform] = { success: false, text: getApiErrorMessage(error, '触发失败') };
  } finally {
    triggering.value = false;
  }
}

async function triggerExpression(platform: string): Promise<void> {
  const form = expressionForms[platform];
  if (!form.emotion) return;
  await doTrigger(platform, 'expression', form.emotion, form.intensity);
}

async function triggerParameter(platform: string): Promise<void> {
  const form = parameterForms[platform];
  if (!form.key) return;
  await doTrigger(platform, 'parameter', form.key, form.value);
}

async function triggerAction(platform: string, action: string): Promise<void> {
  await doTrigger(platform, 'action', action, 1);
}

onMounted(() => {
  void refresh();
});
</script>

<style scoped>
.avatar-debug {
  padding: var(--spacing-lg);
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--spacing-md);
}

.status-alert {
  margin-bottom: var(--spacing-md);
}

.platform-card {
  margin-bottom: var(--spacing-md);
}

.platform-card.disconnected {
  opacity: 0.75;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.platform-name {
  font-weight: 600;
}

.detail-text {
  margin: 0 0 var(--spacing-sm);
  color: var(--text-secondary);
  font-size: 13px;
}

.current-row {
  margin-bottom: var(--spacing-sm);
}

.stats-row {
  display: flex;
  flex-wrap: wrap;
  gap: var(--spacing-xs);
  margin-bottom: var(--spacing-sm);
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

.action-section {
  margin-bottom: var(--spacing-xs);
}

.action-section .el-button {
  margin: 0 var(--spacing-xs) var(--spacing-xs) 0;
}

.expression-form,
.parameter-form {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
}

.emotion-select {
  width: 160px;
}

.intensity-slider {
  flex: 1;
}

.intensity-value {
  width: 42px;
  font-family: var(--font-mono);
  font-size: 13px;
}

.param-input {
  width: 200px;
}

.trigger-result {
  margin: var(--spacing-sm) 0 0;
  font-size: 13px;
  color: var(--color-success, #67c23a);
}

.trigger-result.fail {
  color: var(--color-danger, #f56c6c);
}
</style>
