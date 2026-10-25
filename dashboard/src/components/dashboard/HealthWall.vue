<!--
  连接健康墙（运行总览区块）

  回答一个问题：开播前各外部连接都在吗？按域分组展示组件连接态快照，
  仅对后端声明支持手动重连的条目显示"重连"按钮（复用工具提供者的
  重连端点）。

  keep-alive 生命周期语义：轮询经 useKeepAlivePolling 声明——激活态
  （含 keep-alive 首次挂载）建 interval，失活即清；首屏快照由 onMounted
  的显式拉取负责，轮询只做周期保鲜。
-->
<template>
  <section class="health-wall" aria-label="连接健康">
    <div class="health-head">
      <h2 class="health-title">连接健康</h2>
      <span v-if="lastCheckedText" class="health-checked mono">最近检查 {{ lastCheckedText }}</span>
      <span class="grow" />
      <el-button size="small" :loading="checking" @click="runSelfCheck">一键自检</el-button>
    </div>
    <div v-if="loadError" class="health-error">{{ loadError }}</div>
    <div v-else class="health-grid">
      <article
        v-for="group in groups"
        :key="group.domain"
        class="health-card"
        :class="`is-${group.worst}`"
      >
        <div class="health-card-head">
          <span
            class="health-dot"
            :class="`is-${HEALTH_STATE_CLASS[group.worst]}`"
            aria-hidden="true"
          />
          <span class="health-domain">{{ group.label }}</span>
        </div>
        <ul class="health-items">
          <li v-for="item in group.items" :key="`${group.domain}-${item.name}`" class="health-item">
            <span
              class="health-dot is-small"
              :class="`is-${HEALTH_STATE_CLASS[item.state]}`"
              aria-hidden="true"
            />
            <span class="health-name" :title="item.last_error || item.detail">{{ item.name }}</span>
            <span class="health-detail">{{ item.detail }}</span>
            <el-button
              v-if="isReconnectable(item)"
              class="health-reconnect"
              link
              type="primary"
              size="small"
              :loading="reconnectingName === item.name"
              @click="onReconnect(item)"
            >
              重连
            </el-button>
          </li>
        </ul>
      </article>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { ElMessage } from 'element-plus';
import { systemApi, toolsApi } from '@/api';
import type { SystemHealthItem } from '@/types';
import { getApiErrorMessage } from '@/utils/apiError';
import { formatTimeHMS } from '@/utils/format';
import { HEALTH_STATE_CLASS, isReconnectable, summarizeDomains } from '@/utils/healthWall';
import { useKeepAlivePolling } from '@/composables/useKeepAlivePolling';

/** 周期保鲜间隔（毫秒）：健康墙变化低频，15s 足够开播自检兜底 */
const POLL_INTERVAL_MS = 15000;

const groups = computed(() => summarizeDomains(health.value?.domains));
const health = ref<Awaited<ReturnType<typeof systemApi.fetchSystemHealth>>['data'] | null>(null);
const checking = ref(false);
const loadError = ref('');
const reconnectingName = ref('');

const lastCheckedText = computed(() => {
  const checkedAt = health.value?.checked_at_ms;
  return typeof checkedAt === 'number' ? formatTimeHMS(checkedAt) : '';
});

async function refresh(): Promise<void> {
  const resp = await systemApi.fetchSystemHealth();
  health.value = resp.data;
}

/** 一键自检：立即重拉健康快照并刷新最近检查时间（薄刷新，不做逐项探测编排） */
async function runSelfCheck(): Promise<void> {
  checking.value = true;
  try {
    await refresh();
    loadError.value = '';
  } catch (error) {
    loadError.value = getApiErrorMessage(error, '获取连接健康失败');
  } finally {
    checking.value = false;
  }
}

async function onReconnect(item: SystemHealthItem): Promise<void> {
  reconnectingName.value = item.name;
  try {
    const resp = await toolsApi.reconnectProvider(item.name);
    if (resp.data.ok) {
      ElMessage.success(`${item.name} 重连成功`);
    } else {
      // 后端失败体带 error 摘要（未入 TS 类型，就地窄化读取）
      const reason = (resp.data as Partial<{ error: string }>).error ?? '未知原因';
      ElMessage.error(`${item.name} 重连失败: ${reason}`);
    }
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, `${item.name} 重连请求失败`));
  } finally {
    reconnectingName.value = '';
    void runSelfCheck();
  }
}

// 轮询静默：周期路径失败只留旧快照，错误提示仅由"一键自检"显式给出
useKeepAlivePolling(() => void runSelfCheck(), POLL_INTERVAL_MS);

onMounted(() => {
  void runSelfCheck();
});
</script>

<style scoped>
.health-wall {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
  padding: var(--spacing-md) var(--spacing-lg);
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-lg);
}
.health-head {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
}
.health-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0;
}
.health-checked {
  font-size: 11px;
  color: var(--text-placeholder);
}
.health-error {
  font-size: 12px;
  color: var(--color-danger);
}
.health-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: var(--spacing-sm);
}
.health-card {
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-md);
  padding: var(--spacing-sm) var(--spacing-md);
  display: flex;
  flex-direction: column;
  gap: var(--spacing-xs);
}
.health-card-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.health-domain {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}
.health-items {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
}
.health-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  padding: 2px 0;
}
.health-name {
  color: var(--text-regular);
  font-weight: 500;
}
.health-detail {
  color: var(--text-placeholder);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.health-reconnect {
  margin-left: auto;
}
.health-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
  background: var(--text-placeholder);
}
.health-dot.is-small {
  width: 6px;
  height: 6px;
}
.health-dot.is-ok {
  background: var(--color-success);
}
.health-dot.is-degraded {
  background: var(--color-warning);
}
.health-dot.is-offline {
  background: var(--color-danger);
}
.health-dot.is-unknown {
  background: var(--text-placeholder);
}
.grow {
  flex: 1;
}
</style>
