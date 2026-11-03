<template>
  <div class="memory-page">
    <!-- 页面头：身份 + 刷新                                               -->
    <header class="page-header">
      <div class="header-left">
        <h1 class="page-title">记忆账本</h1>
        <p class="page-subtitle">
          她答应过的事，和每一场直播干了什么 · 下播后自动整理，这里只做查看与纠错
        </p>
      </div>
      <div class="header-actions">
        <el-button :loading="refreshing" @click="reload">刷新</el-button>
      </div>
    </header>

    <!-- 承诺账本 / 下播整理 / 场次档案：下播整理产出的可纠错视图                  -->
    <CommitmentPanel ref="commitmentPanel" />
    <SettlePanel ref="settlePanel" @view-archive="openArchive" />
    <ArchivePanel ref="archivePanel" :focus-session-id="archiveSessionId" />
  </div>
</template>

<script setup lang="ts">
/**
 * 记忆账本页 —— 她答应过的事与每场干了什么的查看 / 纠错入口
 *
 * 页面只剩账本三块：承诺（未了结 / 已了结 / 误记）、下播整理状态与场次档案。
 * 旧版长期记忆（MEMORY.md）与常客画像两套接口在记忆重设计中下线，记忆内容
 * 改由下播整理写进承诺账本与场次档案；三个面板各自在挂载时拉自己的数据，
 * 页头"刷新"一并重拉，整理状态面板的"查看该场档案"把场次交给档案面板并滚过去。
 */
import { nextTick, ref } from 'vue';
import CommitmentPanel from '@/components/memory/CommitmentPanel.vue';
import SettlePanel from '@/components/memory/SettlePanel.vue';
import ArchivePanel from '@/components/memory/ArchivePanel.vue';

/** 页头刷新按钮的 loading：三个面板都重拉完才停 */
const refreshing = ref(false);

/** 账本三个面板：页面头"刷新"一并刷新；整理状态的"查看该场档案"跳到档案面板 */
const commitmentPanel = ref<InstanceType<typeof CommitmentPanel> | null>(null);
const settlePanel = ref<InstanceType<typeof SettlePanel> | null>(null);
const archivePanel = ref<InstanceType<typeof ArchivePanel> | null>(null);
const archiveSessionId = ref<number | null>(null);

/** 页面头"刷新"：三个账本面板一起重拉（面板各自带 loading 态） */
async function reload(): Promise<void> {
  refreshing.value = true;
  try {
    await Promise.all([
      commitmentPanel.value?.reload(),
      settlePanel.value?.reload(),
      archivePanel.value?.reload(),
    ]);
  } finally {
    refreshing.value = false;
  }
}

/** 整理状态面板的"查看该场档案"：切到那一场并滚到档案面板 */
function openArchive(liveSessionId: number): void {
  archiveSessionId.value = liveSessionId;
  void nextTick(() => {
    document
      .getElementById('ledger-archive')
      ?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });
}
</script>

<style scoped>
.memory-page {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-md);
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

.header-actions {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
}

@media (max-width: 860px) {
  .page-header {
    flex-direction: column;
    align-items: flex-start;
  }
}
</style>
