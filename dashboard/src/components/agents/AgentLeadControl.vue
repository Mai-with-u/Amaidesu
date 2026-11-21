<!--
  动态主从状态徽章与导演切权浮层（主播主控 / 游戏主控）。

  常态以单枚胶囊徽章呈现当前主控角色、调度模式与已主控时长，点击展开浮层可一键切换
  （自动跟随 / 切回主播主控 / 交给就绪游戏主控）并切换动态主从开关，不喧宾夺主。
  后端未注入 LeadCoordinator 时（available=false）退化为中性灰只读胶囊，占位说明原因。
-->
<template>
  <div class="lead-control" :class="{ 'is-block': block }">
    <!-- 后端未装配：中性灰只读占位，非按钮、无浮层 -->
    <span
      v-if="!available"
      class="lead-chip is-unavailable"
      :class="{ 'is-block': block }"
      title="后端未装配主控协调器，动态主从控制面不可用"
    >
      主控 · 不可用
    </span>

    <el-popover
      v-else
      placement="bottom-end"
      :width="320"
      trigger="click"
      popper-class="agent-lead-popper"
    >
      <template #reference>
        <button
          type="button"
          class="lead-chip"
          :class="{
            'is-game': isGameLead,
            'is-locked': isManualLocked,
            'is-disabled': !enabled,
            'is-block': block,
          }"
          :title="chipTooltip"
          :aria-label="`当前主控：${badgeLabel}，${modeDetail}`"
        >
          <span class="lead-dot" aria-hidden="true" />
          <span class="lead-role">{{ badgeLabel }}</span>
          <span class="lead-sep" aria-hidden="true">·</span>
          <span class="lead-mode">{{ modeLabel }}</span>
          <span class="lead-caret" aria-hidden="true">▾</span>
        </button>
      </template>

      <div class="lead-panel">
        <header class="lead-panel-head">
          <div class="lead-panel-title-wrap">
            <span class="lead-panel-title">主控权调度</span>
            <span
              class="lead-panel-badge"
              :class="{ 'is-game': isGameLead, 'is-disabled': !enabled }"
            >
              {{ badgeLabel }}
            </span>
          </div>
          <el-button
            size="small"
            text
            :loading="loading"
            aria-label="刷新主控状态"
            title="刷新主控状态"
            @click="onRefresh"
          >
            刷新
          </el-button>
        </header>

        <dl class="lead-meta">
          <div class="lead-meta-row">
            <dt>当前模式</dt>
            <dd :class="{ 'is-locked': isManualLocked }">{{ modeDetail }}</dd>
          </div>
          <div v-if="leadDurationLabel" class="lead-meta-row">
            <dt>持续时长</dt>
            <dd>{{ leadDurationLabel }}</dd>
          </div>
          <div v-if="reasonLabel" class="lead-meta-row">
            <dt>最近原因</dt>
            <dd :title="leadState?.reason">{{ reasonLabel }}</dd>
          </div>
        </dl>

        <div v-if="!enabled" class="lead-readonly-notice" role="status">
          动态主从未启用，当前保持固定主播主控（<code>streamer_only</code>）。
        </div>

        <!-- 动态主从开关：写配置 agents.agents.streamer.lead_mode（dynamic / streamer_only），保存即触发热重建生效 -->
        <div class="lead-mode-toggle">
          <el-switch
            :model-value="leadModeEnabled"
            :loading="savingLeadMode"
            size="small"
            aria-label="动态主从开关"
            @change="onLeadModeChange"
          />
          <div class="lead-mode-toggle-text">
            <span class="lead-mode-toggle-cap">动态主从</span>
            <span class="lead-mode-toggle-note">
              关闭后固定主播主控（streamer_only），全部场景由主播决策
            </span>
          </div>
        </div>

        <div class="lead-actions" role="group" aria-label="导演切权控制">
          <div class="lead-actions-cap">导演一键切换</div>
          <div class="lead-btn-list">
            <el-button
              v-for="opt in switchOptions"
              :key="opt.target"
              size="small"
              class="lead-action-btn"
              :type="opt.active ? 'primary' : 'default'"
              :plain="!opt.active"
              :disabled="
                opt.disabled || (switchingTarget !== null && switchingTarget !== opt.target)
              "
              :loading="switchingTarget === opt.target"
              :title="opt.hint"
              @click="onSwitch(opt.target)"
            >
              <span class="lead-btn-inner">
                <span>{{ opt.label }}</span>
                <span
                  v-if="opt.kind === 'game'"
                  class="lead-ready-tag"
                  :class="opt.ready ? 'is-ready' : 'is-unready'"
                >
                  {{ opt.ready ? '就绪' : '未就绪' }}
                </span>
                <span v-else-if="opt.active" class="lead-current-tag">当前</span>
              </span>
            </el-button>
          </div>
          <p v-if="enabled && !hasGameCandidates" class="lead-empty-candidates">
            当前暂无支持自主领航的游戏候选 Agent
          </p>
        </div>

        <p v-if="errorMessage" class="lead-error" role="alert">{{ errorMessage }}</p>
      </div>
    </el-popover>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { ElMessage } from 'element-plus';
import { configApi } from '@/api';
import { useAgentLead } from '@/composables/useAgentLead';
import { useNowTick } from '@/composables/useNowTick';
import { getApiErrorMessage } from '@/utils/apiError';
import { formatElapsedMs } from '@/utils/format';

withDefaults(
  defineProps<{
    /** 块级模式：用于侧栏等窄容器内占满整行宽度 */
    block?: boolean;
  }>(),
  { block: false },
);

const {
  leadState,
  available,
  enabled,
  loading,
  switchingTarget,
  errorMessage,
  isGameLead,
  isManualLocked,
  badgeLabel,
  modeLabel,
  modeDetail,
  reasonLabel,
  switchOptions,
  refresh,
  switchLead,
} = useAgentLead();

// 主控已持续时长：since_ms 起算，1s 共享时钟驱动重算（keep-alive 隐藏即停走）
const nowMs = useNowTick();

const leadDurationLabel = computed<string>(() => {
  const sinceMs = leadState.value?.since_ms ?? 0;
  if (!sinceMs) return '';
  const diffSec = Math.max(0, Math.floor((nowMs.value - sinceMs) / 1000));
  if (diffSec < 5) return '刚切换';
  if (diffSec < 60) return `${diffSec} 秒`;
  return formatElapsedMs(diffSec * 1000);
});

const hasGameCandidates = computed(() => switchOptions.value.some(opt => opt.kind === 'game'));

const chipTooltip = computed<string>(() => {
  const parts = [`${badgeLabel.value} · ${modeDetail.value}`];
  if (leadDurationLabel.value) parts.push(`主控已 ${leadDurationLabel.value}`);
  if (reasonLabel.value) parts.push(`原因：${reasonLabel.value}`);
  parts.push('点击切换主控权');
  return parts.join('\n');
});

// ==================== 动态主从开关（agents.agents.streamer.lead_mode） ====================

const leadModeEnabled = ref(leadState.value?.enabled ?? false);
// 快照刷新（WS 补拉 / 轮询）后同步开关初始态；保存进行中不回写，避免覆盖用户操作
watch(
  () => leadState.value?.enabled,
  value => {
    if (value !== undefined && !savingLeadMode.value) leadModeEnabled.value = value;
  },
);

const savingLeadMode = ref(false);

/** 保存 lead_mode：开=dynamic、关=streamer_only；配置键是三段复合形态
 *  （域 agents + 文件内段 agents + 键 streamer.lead_mode）。保存会触发主播侧
 *  按新配置重建，热生效即回 */
async function onLeadModeChange(value: string | number | boolean): Promise<void> {
  savingLeadMode.value = true;
  try {
    const response = await configApi.saveBatch([
      { key: 'agents.agents.streamer.lead_mode', value: value ? 'dynamic' : 'streamer_only' },
    ]);
    if (response.data.requires_restart) ElMessage.info('重启后生效');
    else ElMessage.info('已保存，主播侧即时生效（按新配置重建）');
    leadModeEnabled.value = Boolean(value);
    await refresh({ silent: true });
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, '保存主控模式失败'));
  } finally {
    savingLeadMode.value = false;
  }
}

function onRefresh(): void {
  void refresh({ silent: false });
}

function onSwitch(target: string): void {
  void switchLead(target);
}
</script>

<style scoped>
.lead-control {
  display: inline-flex;
  align-items: center;
  flex-shrink: 0;
}

.lead-control.is-block {
  display: block;
  width: 100%;
}

/* 顶栏/侧栏紧凑胶囊：对齐 stage-chip / sim-chip 的视觉语言，克制不抢主标题 */
.lead-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 2px 10px;
  border-radius: 999px;
  border: 1px solid var(--color-agent);
  background: var(--color-agent-bg);
  color: var(--color-agent);
  font-size: var(--text-2xs);
  font-weight: 600;
  line-height: 1.5;
  cursor: pointer;
  white-space: nowrap;
  transition:
    border-color var(--transition-fast),
    background-color var(--transition-fast),
    color var(--transition-fast);
}

.lead-chip.is-block {
  width: 100%;
  justify-content: flex-start;
  border-radius: var(--radius-md);
  padding: 6px 10px;
}

.lead-chip:hover {
  filter: brightness(0.96);
}

/* 游戏主控态：切到游戏语义色（粉），一眼可辨当前由游戏自主领航 */
.lead-chip.is-game {
  border-color: var(--color-game);
  background: var(--color-game-bg);
  color: var(--color-game);
}

/* 禁用态（streamer_only）：退回中性灰，明确传达只读固定态 */
.lead-chip.is-disabled {
  border-color: var(--border-color-dark);
  background: var(--bg-card);
  color: var(--text-secondary);
}

/* 后端未装配（available=false）：中性灰只读占位，非按钮不可点 */
.lead-chip.is-unavailable {
  border-color: var(--border-color-dark);
  background: var(--bg-hover);
  color: var(--text-placeholder);
  cursor: default;
}

.lead-chip.is-unavailable:hover {
  filter: none;
}

.lead-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
  flex-shrink: 0;
}

.lead-role {
  overflow: hidden;
  text-overflow: ellipsis;
}

.lead-sep {
  opacity: 0.5;
}

.lead-mode {
  font-weight: 500;
  opacity: 0.9;
}

/* 手动锁定：加重模式字重提醒导演当前处于人工接管 */
.lead-chip.is-locked .lead-mode {
  font-weight: 700;
}

.lead-caret {
  margin-left: auto;
  font-size: 10px;
  opacity: 0.7;
}

/* ==================== 浮层面板 ==================== */

.lead-panel {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
}

.lead-panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--spacing-sm);
}

.lead-panel-title-wrap {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.lead-panel-title {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--text-primary);
}

.lead-panel-badge {
  padding: 1px 8px;
  border-radius: 999px;
  font-size: var(--text-2xs);
  font-weight: 600;
  background: var(--color-agent-bg);
  color: var(--color-agent);
}

.lead-panel-badge.is-game {
  background: var(--color-game-bg);
  color: var(--color-game);
}

.lead-panel-badge.is-disabled {
  background: var(--bg-hover);
  color: var(--text-secondary);
}

.lead-meta {
  margin: 0;
  padding: 6px 8px;
  border-radius: var(--radius-sm);
  background: var(--bg-hover);
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.lead-meta-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  font-size: var(--text-xs);
}

.lead-meta-row dt {
  color: var(--text-secondary);
  flex-shrink: 0;
}

.lead-meta-row dd {
  margin: 0;
  color: var(--text-primary);
  text-align: right;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.lead-meta-row dd.is-locked {
  color: var(--color-warning);
  font-weight: 600;
}

.lead-readonly-notice {
  padding: 6px 8px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border-color);
  background: var(--bg-card);
  font-size: var(--text-xs);
  color: var(--text-secondary);
  line-height: 1.4;
}

.lead-readonly-notice code {
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
}

/* 动态主从开关行：开关 + 名称与说明小字，紧贴只读提示下方 */
.lead-mode-toggle {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 6px 8px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border-color);
  background: var(--bg-card);
}

.lead-mode-toggle :deep(.el-switch) {
  flex-shrink: 0;
  margin-top: 1px;
}

.lead-mode-toggle-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.lead-mode-toggle-cap {
  font-size: var(--text-xs);
  font-weight: 600;
  color: var(--text-primary);
}

.lead-mode-toggle-note {
  font-size: var(--text-2xs);
  color: var(--text-secondary);
  line-height: 1.4;
}

.lead-actions {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.lead-actions-cap {
  font-size: var(--text-2xs);
  color: var(--text-secondary);
}

.lead-btn-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.lead-action-btn {
  width: 100%;
  margin-left: 0 !important;
  justify-content: flex-start;
}

.lead-btn-inner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  gap: 8px;
}

.lead-ready-tag,
.lead-current-tag {
  font-size: var(--text-2xs);
  padding: 0 6px;
  border-radius: 999px;
  line-height: 1.5;
}

.lead-ready-tag.is-ready {
  background: var(--color-success-bg);
  color: var(--color-success);
}

.lead-ready-tag.is-unready {
  background: var(--bg-hover);
  color: var(--text-placeholder);
}

.lead-current-tag {
  background: rgba(255, 255, 255, 0.22);
}

.lead-empty-candidates {
  margin: 2px 0 0;
  font-size: var(--text-2xs);
  color: var(--text-placeholder);
}

.lead-error {
  margin: 0;
  font-size: var(--text-xs);
  color: var(--color-danger);
  line-height: 1.4;
}
</style>
