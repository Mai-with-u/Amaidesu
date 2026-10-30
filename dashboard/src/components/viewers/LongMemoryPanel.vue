<template>
  <section class="long-memory-card">
    <header class="lm-head">
      <div class="lm-title-block">
        <h2 class="lm-title">长期记忆</h2>
        <p class="lm-subtitle">
          主播跨场次一直记着的事（答应过的事、老梗、长线目标、关于自己）。每场收播自动整理：只改动有新情况的条目，
          连续几场没人再提的挪进"搁置"；开播时带上，对某位观众的约定只在 ta
          发言时给。行尾〔〕里是状态标签；
          你新加或改过的行会记为"人工"，之后整理只读不改。这里改完保存，下一次开播起生效。
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

    <el-collapse class="lm-history" aria-label="长期记忆改动记录">
      <el-collapse-item :title="`改动记录（${history.length}）`" name="history">
        <div v-loading="historyLoading">
          <p v-if="history.length === 0" class="lm-meta">还没有改动记录</p>
          <div v-for="item in history" :key="item.id" class="lm-entry">
            <div class="lm-entry-head">
              <span class="mono lm-meta">{{ formatTime(item.at_ms) }}</span>
              <span class="lm-entry-reason">{{ item.reason || '未注明来由' }}</span>
              <span class="grow" />
              <template v-if="item.has_snapshot">
                <el-button size="small" link @click="onPeek(item.id)">看改之前</el-button>
                <el-button
                  size="small"
                  link
                  type="warning"
                  :disabled="saving"
                  @click="onRestore(item.id)"
                  >撤回这次改动</el-button
                >
              </template>
            </div>
            <ul v-if="item.changes.length" class="lm-entry-changes">
              <li v-for="(change, index) in item.changes" :key="index">{{ change }}</li>
            </ul>
          </div>
        </div>
      </el-collapse-item>
    </el-collapse>

    <el-dialog v-model="peekVisible" title="这次改动之前的长期记忆" width="640px">
      <el-input
        :model-value="peekText"
        type="textarea"
        readonly
        :autosize="{ minRows: 6, maxRows: 24 }"
        aria-label="改动之前的全文"
      />
    </el-dialog>
  </section>
</template>

<script setup lang="ts">
/**
 * 长期记忆面板：查看与整份编辑 MEMORY.md（正式 / 彩排各一份）。
 *
 * 读写逻辑在 useLongMemory；本组件只负责展示与交互编排。切换类型时有未保存
 * 改动先确认，避免一键丢掉手写的内容；撤回某次改动会覆盖编辑区，同样先确认。
 * 改动记录列出每次写入的来由与逐条改动，可只读查看改之前的全文。
 */
import { onMounted, ref } from 'vue';
import { ElMessage } from 'element-plus';
import { useLongMemory } from '@/composables/useLongMemory';
import { confirmAction } from '@/utils/confirmAction';
import { formatDateTimeCn as formatTime } from '@/utils/format';
import type { SessionKind } from '@/types';

const memory = useLongMemory();
const { kind, draft, path, updatedAtMs, loading, saving, error, dirty, history, historyLoading } =
  memory;
const peekVisible = ref(false);
const peekText = ref('');

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

async function onPeek(entryId: string): Promise<void> {
  const text = await memory.loadSnapshot(entryId);
  if (text === null) return;
  peekText.value = text;
  peekVisible.value = true;
}

async function onRestore(entryId: string): Promise<void> {
  const warning = dirty.value ? '编辑区有未保存的改动，撤回会丢掉它们。' : '';
  if (
    !(await confirmAction(
      `${warning}恢复到这次改动之前的版本？撤回本身也会记一条，可以再撤回。`,
      '撤回改动',
    ))
  ) {
    return;
  }
  if (await memory.restore(entryId)) ElMessage.success('已撤回，下一次开播起生效');
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
.lm-history {
  margin-top: 8px;
  border: none;
}
.lm-entry {
  padding: 6px 0;
  border-top: 1px dashed var(--border-color-light);
}
.lm-entry-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.lm-entry-reason {
  font-size: 12px;
}
.lm-entry-changes {
  margin: 4px 0 0;
  padding-left: 18px;
  font-size: 12px;
  color: var(--text-secondary);
}
</style>
