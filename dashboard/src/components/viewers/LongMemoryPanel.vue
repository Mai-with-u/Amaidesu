<template>
  <section class="long-memory-card">
    <header class="lm-head">
      <div class="lm-title-block">
        <h2 class="lm-title">长期记忆</h2>
        <p class="lm-subtitle">
          主播跨场次一直记着的事（答应过的事、老梗、长线目标）。每场收播自动整理，开播时整份带上；
          这里改完保存，下一次开播起生效。
        </p>
      </div>
      <span class="grow" />
      <el-radio-group
        :model-value="kind"
        size="small"
        aria-label="长期记忆类型"
        @update:model-value="onSwitch"
      >
        <el-radio-button value="live">正式</el-radio-button>
        <el-radio-button value="rehearsal">彩排</el-radio-button>
      </el-radio-group>
    </header>

    <div v-if="error" class="lm-error">{{ error }}</div>
    <el-input
      v-model="draft"
      v-loading="loading"
      type="textarea"
      :autosize="{ minRows: 6, maxRows: 18 }"
      :placeholder="
        kind === 'live'
          ? '还没有长期记忆——正式场收播后会自动整理出第一版'
          : '彩排线的长期记忆：只在彩排时被读到'
      "
      aria-label="长期记忆正文"
    />
    <footer class="lm-foot">
      <span class="lm-meta mono" :title="path">
        {{ draft.length }} 字<template v-if="updatedAtMs > 0">
          · 更新于 {{ formatTime(updatedAtMs) }}</template
        >
        <template v-if="path"> · {{ path }}</template>
      </span>
      <span class="grow" />
      <el-button size="small" :disabled="loading" @click="memory.load()">重新读取</el-button>
      <el-button size="small" type="primary" :loading="saving" :disabled="!dirty" @click="onSave"
        >保存</el-button
      >
    </footer>
  </section>
</template>

<script setup lang="ts">
/**
 * 长期记忆面板：查看与整份编辑 MEMORY.md（正式 / 彩排各一份）。
 *
 * 读写逻辑在 useLongMemory；本组件只负责展示与交互编排。切换类型时有未保存
 * 改动先确认，避免一键丢掉手写的内容。
 */
import { onMounted } from 'vue';
import { ElMessage } from 'element-plus';
import { useLongMemory } from '@/composables/useLongMemory';
import { confirmAction } from '@/utils/confirmAction';
import { formatDateTimeCn as formatTime } from '@/utils/format';
import type { SessionKind } from '@/types';

const memory = useLongMemory();
const { kind, draft, path, updatedAtMs, loading, saving, error, dirty } = memory;

async function onSwitch(next: string | number | boolean | undefined): Promise<void> {
  if (next !== 'live' && next !== 'rehearsal') return;
  if (
    dirty.value &&
    !(await confirmAction('有未保存的改动，切换会丢掉它们。继续？', '切换长期记忆'))
  ) {
    return;
  }
  await memory.switchKind(next as SessionKind);
}

async function onSave(): Promise<void> {
  if (await memory.save()) ElMessage.success('长期记忆已保存，下一次开播起生效');
}

onMounted(() => {
  void memory.load();
});
</script>

<style scoped>
.long-memory-card {
  margin-bottom: var(--spacing-md, 16px);
  padding: 12px 14px;
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-md, 8px);
  background: var(--bg-card);
}
.lm-head {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 8px;
}
.lm-title {
  margin: 0;
  font-size: 15px;
  font-weight: 700;
}
.lm-subtitle {
  margin: 2px 0 0;
  font-size: 12px;
  color: var(--text-secondary);
}
.lm-error {
  margin-bottom: 6px;
  font-size: 12px;
  color: var(--color-danger);
}
.lm-foot {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
}
.lm-meta {
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  font-size: 11px;
  color: var(--text-secondary);
}
.grow {
  flex: 1;
}
</style>
