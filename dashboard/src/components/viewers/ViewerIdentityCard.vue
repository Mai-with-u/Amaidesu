<template>
  <section class="identity-card">
    <!-- 待确认：与老观众同名但超出关联时限或候选不唯一，等人工判断是不是同一个人 -->
    <el-alert
      v-if="detail.link_status === 'pending'"
      type="warning"
      :closable="false"
      show-icon
      class="pending-alert"
    >
      <template #title>
        这位观众与
        <router-link
          v-if="detail.link_candidate"
          :to="`/viewers/${detail.link_candidate.viewer_id}`"
          class="inline-link"
          >「{{ detail.link_candidate.user_name }}」（#{{
            detail.link_candidate.viewer_id
          }}）</router-link
        >
        <template v-else>一位老观众</template>
        同名，等待确认是不是同一个人
      </template>
      <div class="pending-actions">
        <el-button
          v-if="detail.link_candidate"
          size="small"
          type="primary"
          :loading="busy"
          @click="mergeInto(detail.link_candidate.viewer_id, detail.link_candidate.user_name)"
        >
          是同一个人，并入
        </el-button>
        <el-button size="small" :loading="busy" @click="keepSeparate">不是，保持新建</el-button>
      </div>
    </el-alert>

    <div class="identity-grid">
      <!-- 外部 ID：同一位观众可能同时有账号 ID 与开放平台 ID；两种都有时可以拆开 -->
      <div class="identity-block">
        <h3 class="block-title">外部 ID</h3>
        <div v-for="row in idRows" :key="row.kind" class="id-row">
          <span class="id-label">{{ row.label }}</span>
          <code class="mono id-value" :title="row.value ?? ''">{{ row.value ?? '—' }}</code>
          <span v-if="row.note" class="id-note">{{ row.note }}</span>
          <el-button
            v-if="row.value && bothIds"
            link
            size="small"
            type="danger"
            :loading="busy"
            @click="unlink(row.kind, row.label)"
          >
            取消关联
          </el-button>
        </div>
      </div>

      <!-- 用过的昵称：场次回顾按当时的名字写，靠它对上"以前的谁" -->
      <div class="identity-block">
        <h3 class="block-title">用过的昵称</h3>
        <div v-if="detail.names.length > 0" class="name-chips">
          <span
            v-for="name in detail.names"
            :key="name.user_name"
            class="name-chip"
            :class="{ current: name.user_name === detail.user_name }"
            :title="`${formatDateTimeCn(name.first_seen_ms)} – ${formatDateTimeCn(name.last_seen_ms)}`"
          >
            {{ name.user_name }}
          </span>
        </div>
        <p v-else class="empty-hint">还没有昵称记录</p>
      </div>

      <!-- 手动合并：认出这位和另一位是同一个人时，并入那一位（这一位的记录全部移过去） -->
      <div class="identity-block">
        <h3 class="block-title">并入另一位观众</h3>
        <div class="merge-row">
          <ViewerPicker v-model="mergeTarget" :exclude-id="detail.viewer_id" />
          <el-button
            size="small"
            type="primary"
            plain
            :disabled="mergeTarget == null"
            :loading="busy"
            @click="mergeTarget != null && mergeInto(mergeTarget)"
          >
            并入
          </el-button>
        </div>
        <p class="hint">两边各有不同的账号 ID 或开放平台 ID 时不能合并——那是两个不同账号</p>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
/**
 * 观众身份卡 —— 档案页上的外部 ID、用过的昵称与人工处理（合并 / 保持新建 / 取消关联）
 *
 * 写操作走 POST /viewers/{viewerId}/merge|keep|unlink，每次都先弹确认框；完成后
 * 以 ``navigate`` 事件告诉档案页该看哪位观众（合并后看目标观众，拆分后看拆出的新观众，
 * 保持新建仍看当前观众）。
 */
import { computed, ref } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { viewersApi } from '@/api';
import ViewerPicker from '@/components/viewers/ViewerPicker.vue';
import type { ViewerDetail } from '@/types';
import { getApiErrorMessage } from '@/utils/apiError';
import { formatDateTimeCn } from '@/utils/format';

const props = defineProps<{ detail: ViewerDetail }>();
const emit = defineEmits<{ navigate: [viewerId: number] }>();

const busy = ref(false);
const mergeTarget = ref<number | null>(null);

const bothIds = computed(() => Boolean(props.detail.account_id && props.detail.open_id));

const idRows = computed(() => [
  { kind: 'account' as const, label: '账号 ID', value: props.detail.account_id, note: '' },
  {
    kind: 'open' as const,
    label: '开放平台 ID',
    value: props.detail.open_id,
    note: props.detail.open_id ? `应用 ${props.detail.open_app_id || '不明'}` : '',
  },
]);

/** 弹确认框 → 调接口 → 提示并跳转；用户取消时什么都不做 */
async function act(
  confirmText: string,
  title: string,
  run: () => Promise<number>,
  done: string,
): Promise<void> {
  try {
    await ElMessageBox.confirm(confirmText, title, {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
    });
  } catch {
    return;
  }
  busy.value = true;
  try {
    const viewerId = await run();
    ElMessage.success(done);
    mergeTarget.value = null;
    emit('navigate', viewerId);
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, `${title}失败`));
  } finally {
    busy.value = false;
  }
}

function mergeInto(targetId: number, targetName?: string): void {
  void act(
    `把「${props.detail.user_name}」（#${props.detail.viewer_id}）并入${targetName ? `「${targetName}」` : ''}（#${targetId}）？` +
      '外部 ID、计数、昵称记录与对话记录都会移过去，这一位不再单独存在；合并没有一键撤销。',
    '合并观众',
    async () => (await viewersApi.merge(props.detail.viewer_id, targetId)).data.viewer_id,
    '已合并',
  );
}

function keepSeparate(): void {
  void act(
    `确认「${props.detail.user_name}」与建议的老观众不是同一个人，保持为独立观众？`,
    '保持新建',
    async () => (await viewersApi.keepSeparate(props.detail.viewer_id)).data.viewer_id,
    '已保持为独立观众',
  );
}

function unlink(kind: 'account' | 'open', label: string): void {
  void act(
    `把${label}从「${props.detail.user_name}」身上拆出去，单独建一位观众？` +
      '带这个 ID 的对话与付费记录会移过去并重新计数；画像留在当前观众身上，已删除场次的计数拆不出来。',
    '取消关联',
    async () => (await viewersApi.unlink(props.detail.viewer_id, kind)).data.viewer_id,
    '已拆成新观众',
  );
}
</script>

<style scoped>
.identity-card {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
  padding: var(--spacing-md);
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-lg);
}

.pending-actions {
  display: flex;
  gap: var(--spacing-sm);
  margin-top: var(--spacing-xs);
}

.inline-link {
  color: var(--color-primary);
}

.identity-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: var(--spacing-md);
}

.identity-block {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.block-title {
  margin: 0;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
}

.id-row {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  font-size: 12px;
}

.id-label {
  flex-shrink: 0;
  width: 84px;
  white-space: nowrap;
  color: var(--text-secondary);
}

.id-value {
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-primary);
}

.id-note {
  white-space: nowrap;
  font-size: 11px;
  color: var(--text-placeholder);
}

.name-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.name-chip {
  padding: 2px 10px;
  border: 1px solid var(--border-color-light);
  border-radius: 999px;
  font-size: 12px;
  color: var(--text-secondary);
}

.name-chip.current {
  border-color: var(--color-primary);
  color: var(--color-primary);
}

.merge-row {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
}

.hint,
.empty-hint {
  margin: 0;
  font-size: 11px;
  color: var(--text-placeholder);
}
</style>
