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
    <CandidatePanel ref="candidatePanel" @changed="onCandidateDecided" />
    <SettlePanel ref="settlePanel" @view-archive="openArchive" />
    <ArchivePanel ref="archivePanel" />

    <!-- 游戏里的人：她遇到过的玩家，运营者确认对应的直播间观众、删玩家                -->
    <PlayerPanel ref="playerPanel" />
    <!-- 玩家自述：他说的关于他自己的事，批准后只进他的玩家卡                      -->
    <PlayerCandidatePanel ref="playerCandidatePanel" />

    <!-- 直播间公告：按保留期配置拼出的观众告知文本，一键复制                  -->
    <AnnouncementPanel />
  </div>
</template>

<script setup lang="ts">
/**
 * 记忆账本页 —— 她答应过的事与每场干了什么的查看 / 纠错入口
 *
 * 账本四块：承诺（未了结 / 已了结 / 误记）、观众候选（人批准才进记忆）、下播整理状态与场次档案。
 * 批准一条候选会新增档案行或承诺，所以批准、驳回后连带重拉承诺与档案两个面板。
 * 旧版长期记忆（MEMORY.md）与常客画像两套接口在记忆重设计中下线，记忆内容
 * 改由下播整理写进承诺账本与场次档案；三个面板各自在挂载时拉自己的数据，
 * 页头"刷新"一并重拉，整理状态面板的"查看该场档案"经 ref 调档案面板的
 * selectSession 切场，再把页面滚到档案面板。底部的直播间公告按保留期配置
 * 拼出观众告知文本，供运营者贴进直播间简介。档案下面是“游戏里的人”：她在游戏里遇到过的玩家，
 * 运营者在这里确认玩家对应哪位观众、删玩家；再下面是玩家自述审核（批准后只进他的玩家卡）。
 */
import { nextTick, ref } from 'vue';
import AnnouncementPanel from '@/components/memory/AnnouncementPanel.vue';
import CandidatePanel from '@/components/memory/CandidatePanel.vue';
import CommitmentPanel from '@/components/memory/CommitmentPanel.vue';
import SettlePanel from '@/components/memory/SettlePanel.vue';
import ArchivePanel from '@/components/memory/ArchivePanel.vue';
import PlayerPanel from '@/components/memory/PlayerPanel.vue';
import PlayerCandidatePanel from '@/components/memory/PlayerCandidatePanel.vue';

/** 页头刷新按钮的 loading：所有面板都重拉完才停 */
const refreshing = ref(false);

/** 账本三个面板：页面头"刷新"一并刷新；整理状态的"查看该场档案"跳到档案面板 */
const commitmentPanel = ref<InstanceType<typeof CommitmentPanel> | null>(null);
const candidatePanel = ref<InstanceType<typeof CandidatePanel> | null>(null);
const settlePanel = ref<InstanceType<typeof SettlePanel> | null>(null);
const archivePanel = ref<InstanceType<typeof ArchivePanel> | null>(null);
const playerPanel = ref<InstanceType<typeof PlayerPanel> | null>(null);
const playerCandidatePanel = ref<InstanceType<typeof PlayerCandidatePanel> | null>(null);

/** 页面头"刷新"：账本面板与玩家面板一起重拉（面板各自带 loading 态） */
async function reload(): Promise<void> {
  refreshing.value = true;
  try {
    await Promise.all([
      commitmentPanel.value?.reload(),
      candidatePanel.value?.reload(),
      settlePanel.value?.reload(),
      archivePanel.value?.reload(),
      playerPanel.value?.reload(),
      playerCandidatePanel.value?.reload(),
    ]);
  } finally {
    refreshing.value = false;
  }
}

/** 批准或驳回一条候选后：批准可能新增了承诺或档案行，两个面板跟着重拉 */
function onCandidateDecided(): void {
  void commitmentPanel.value?.reload();
  void archivePanel.value?.reload();
}

/** 整理状态面板的"查看该场档案"：调档案面板切场并滚过去 */
function openArchive(liveSessionId: number): void {
  archivePanel.value?.selectSession(liveSessionId);
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
