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

    <!-- 账本工作台：七个 tab 各装一个面板，惰性挂载，tab 名上的数字是待处理计数 -->
    <el-tabs v-model="activeTab" class="memory-tabs">
      <el-tab-pane name="commitment" :lazy="true">
        <template #label>
          <span class="tab-label">
            承诺<span v-if="badges.commitment > 0" class="tab-badge">{{ badges.commitment }}</span>
          </span>
        </template>
        <CommitmentPanel ref="commitmentPanel" @changed="refreshBadges" />
      </el-tab-pane>

      <el-tab-pane name="candidates" :lazy="true">
        <template #label>
          <span class="tab-label">
            观众候选<span v-if="badges.candidates > 0" class="tab-badge">{{
              badges.candidates
            }}</span>
          </span>
        </template>
        <CandidatePanel ref="candidatePanel" @changed="onCandidateDecided" />
      </el-tab-pane>

      <el-tab-pane name="settle" :lazy="true">
        <template #label>
          <span class="tab-label">
            整理<span v-if="badges.settle > 0" class="tab-badge">{{ badges.settle }}</span>
          </span>
        </template>
        <SettlePanel ref="settlePanel" @view-archive="openArchive" @changed="refreshBadges" />
      </el-tab-pane>

      <el-tab-pane name="archive" :lazy="true">
        <template #label>
          <span class="tab-label">档案</span>
        </template>
        <ArchivePanel ref="archivePanel" />
      </el-tab-pane>

      <el-tab-pane name="players" :lazy="true">
        <template #label>
          <span class="tab-label">
            玩家<span v-if="badges.players > 0" class="tab-badge">{{ badges.players }}</span>
          </span>
        </template>
        <PlayerPanel ref="playerPanel" />
      </el-tab-pane>

      <el-tab-pane name="player-candidates" :lazy="true">
        <template #label>
          <span class="tab-label">
            自述<span v-if="badges['player-candidates'] > 0" class="tab-badge">{{
              badges['player-candidates']
            }}</span>
          </span>
        </template>
        <PlayerCandidatePanel ref="playerCandidatePanel" @changed="refreshBadges" />
      </el-tab-pane>

      <el-tab-pane name="announcement" :lazy="true">
        <template #label>
          <span class="tab-label">公告</span>
        </template>
        <AnnouncementPanel ref="announcementPanel" />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
/**
 * 记忆账本页 —— 她答应过的事与每场干了什么的查看 / 纠错入口
 *
 * 页内七个 tab（承诺 / 观众候选 / 整理 / 档案 / 玩家 / 自述 / 公告），各装一个
 * 面板：惰性挂载（切到才挂、挂载时自己拉数据），挂过保留；面板都不含定时器 /
 * 订阅 / WebSocket，无 keep-alive 启停语义。tab 键写进 route query 的 tab 参数，
 * 别的页面可 ?tab=archive 深链直达。
 *
 * tab 徽标是各面板的待处理计数（未了结承诺 / 待审候选 / 待整理场次 / 待审玩家
 * 自述 / 待删除玩家），页面自己轻量拉取；面板的行级写操作成功后发 changed 刷新。
 * 跨面板联动用脏标记：批准或驳回一条观众候选可能新增承诺或档案行，但目标面板
 * 可能还没挂载，所以只标脏，切到该 tab 时才重拉（首次挂载本来就拉新数据，不受
 * 脏标记影响）。整理状态面板的"查看该场档案"切到档案 tab 后调档案面板的
 * selectSession 切场。页头"刷新"重拉徽标与所有已挂载面板。
 */
import { nextTick, onMounted, reactive, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import AnnouncementPanel from '@/components/memory/AnnouncementPanel.vue';
import CandidatePanel from '@/components/memory/CandidatePanel.vue';
import CommitmentPanel from '@/components/memory/CommitmentPanel.vue';
import SettlePanel from '@/components/memory/SettlePanel.vue';
import ArchivePanel from '@/components/memory/ArchivePanel.vue';
import PlayerPanel from '@/components/memory/PlayerPanel.vue';
import PlayerCandidatePanel from '@/components/memory/PlayerCandidatePanel.vue';
import { resolveMemoryTab, type MemoryTabKey } from '@/components/memory/pageTabs';
import { ledgerApi, playersApi } from '@/api';

const route = useRoute();
const router = useRouter();

const activeTab = ref<MemoryTabKey>(resolveMemoryTab(route.query.tab));

// ===== tab 徽标：各面板的待处理计数，页面自己轻量拉取 =====

const badges = reactive({
  commitment: 0,
  candidates: 0,
  settle: 0,
  players: 0,
  'player-candidates': 0,
});

/** 拉五个待处理计数；单项失败只丢该项的这次更新（保留旧值），记日志不打断页面 */
async function refreshBadges(): Promise<void> {
  const [commitments, candidates, settle, players, playerCandidates] = await Promise.allSettled([
    ledgerApi.listCommitments({ limit: 1 }),
    ledgerApi.listCandidates('pending'),
    ledgerApi.getSettleState(),
    playersApi.list(),
    playersApi.listCandidates('pending'),
  ]);
  for (const result of [commitments, candidates, settle, players, playerCandidates]) {
    if (result.status === 'rejected') console.error('记忆页徽标计数刷新失败', result.reason);
  }
  if (commitments.status === 'fulfilled') badges.commitment = commitments.value.data.open_count;
  if (candidates.status === 'fulfilled') badges.candidates = candidates.value.data.pending_count;
  if (settle.status === 'fulfilled') badges.settle = settle.value.data.pending_count;
  if (players.status === 'fulfilled')
    badges.players = players.value.data.items.filter(
      item => item.forget_requested_at_ms !== null,
    ).length;
  if (playerCandidates.status === 'fulfilled')
    badges['player-candidates'] = playerCandidates.value.data.pending_count;
}

// ===== 跨面板联动：候选决定 → 承诺/档案标脏；整理状态 → 切档案 tab 切场 =====

/** 批准候选可能新增了承诺或档案行：目标面板没挂载就标脏，切到该 tab 时重拉 */
const staleAfterCandidate = reactive({ commitment: false, archive: false });

function onTabEnter(key: MemoryTabKey): void {
  if (key === 'commitment' && staleAfterCandidate.commitment) {
    staleAfterCandidate.commitment = false;
    if (commitmentPanel.value) void commitmentPanel.value.reload();
  } else if (key === 'archive' && staleAfterCandidate.archive) {
    staleAfterCandidate.archive = false;
    if (archivePanel.value) void archivePanel.value.reload();
  }
}

/** 批准或驳回一条候选：承诺与档案两个 tab 标脏，徽标计数跟着刷新 */
function onCandidateDecided(): void {
  staleAfterCandidate.commitment = true;
  staleAfterCandidate.archive = true;
  void refreshBadges();
}

/** 整理状态面板的"查看该场档案"：切到档案 tab，面板挂载/就绪后切到该场 */
function openArchive(liveSessionId: number): void {
  activeTab.value = 'archive';
  staleAfterCandidate.archive = false;
  void nextTick(() => {
    archivePanel.value?.selectSession(liveSessionId);
  });
}

// ===== tab 深链：tab 键写进 ?tab=，别处跳进来的参数变化也跟随 =====

watch(activeTab, key => {
  router.replace({ query: { ...route.query, tab: key } });
  onTabEnter(key);
});

watch(
  () => route.query.tab,
  raw => {
    const key = resolveMemoryTab(raw);
    if (key !== activeTab.value) activeTab.value = key;
  },
);

// ===== 页面头"刷新"：徽标与所有已挂载面板一起重拉（未挂载的 tab 切到时自然会拉新的） =====

const refreshing = ref(false);

const commitmentPanel = ref<InstanceType<typeof CommitmentPanel> | null>(null);
const candidatePanel = ref<InstanceType<typeof CandidatePanel> | null>(null);
const settlePanel = ref<InstanceType<typeof SettlePanel> | null>(null);
const archivePanel = ref<InstanceType<typeof ArchivePanel> | null>(null);
const playerPanel = ref<InstanceType<typeof PlayerPanel> | null>(null);
const playerCandidatePanel = ref<InstanceType<typeof PlayerCandidatePanel> | null>(null);
const announcementPanel = ref<InstanceType<typeof AnnouncementPanel> | null>(null);

async function reload(): Promise<void> {
  refreshing.value = true;
  try {
    await Promise.all([
      refreshBadges(),
      commitmentPanel.value?.reload(),
      candidatePanel.value?.reload(),
      settlePanel.value?.reload(),
      archivePanel.value?.reload(),
      playerPanel.value?.reload(),
      playerCandidatePanel.value?.reload(),
      announcementPanel.value?.load(),
    ]);
    // 已挂载面板都重拉过了，候选决定留下的脏标记就此消化
    staleAfterCandidate.commitment = false;
    staleAfterCandidate.archive = false;
  } finally {
    refreshing.value = false;
  }
}

onMounted(() => {
  void refreshBadges();
});
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

.tab-label {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.tab-badge {
  min-width: 18px;
  height: 16px;
  line-height: 16px;
  padding: 0 5px;
  border-radius: 999px;
  background: var(--color-warning);
  color: #fff;
  font-size: 11px;
  font-weight: 600;
}

@media (max-width: 860px) {
  .page-header {
    flex-direction: column;
    align-items: flex-start;
  }
}
</style>
