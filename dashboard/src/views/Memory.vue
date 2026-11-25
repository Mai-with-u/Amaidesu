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
        <CommitmentPanel ref="commitmentPanel" @changed="onPanelChanged" />
      </el-tab-pane>

      <el-tab-pane name="candidates" :lazy="true">
        <template #label>
          <span class="tab-label">
            观众候选<span v-if="badges.candidates > 0" class="tab-badge">{{
              badges.candidates
            }}</span>
          </span>
        </template>
        <CandidatePanel ref="candidatePanel" @changed="onPanelChanged" />
      </el-tab-pane>

      <el-tab-pane name="settle" :lazy="true">
        <template #label>
          <span class="tab-label">
            整理<span v-if="badges.settle > 0" class="tab-badge">{{ badges.settle }}</span>
          </span>
        </template>
        <SettlePanel ref="settlePanel" @view-archive="openArchive" @changed="onPanelChanged" />
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
        <PlayerCandidatePanel ref="playerCandidatePanel" @changed="onPanelChanged" />
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
 * 自述 / 待删除玩家），页面自己轻量拉取；面板的行级写操作成功后发 changed。
 *
 * 一个面板改了数据，别的面板显示的内容可能跟着过期（重整一场会替换这一场的
 * 档案行与未了结的承诺，批准一条观众候选会新增承诺或档案行），而面板只在自己
 * 挂载时拉一次。页面为此记一份"待重拉的 tab"集合：任一面板发 changed，七个 tab
 * 全部进集合并刷新徽标；运营者进入某个 tab 时，它若在集合里就重拉它的面板并
 * 移出集合，不在就不动。页面不记录"这次改动影响哪些 tab"，所以会连带重拉其实
 * 没受影响的面板。目标面板还没挂载时无从重拉，也不用重拉——首次挂载拉到的
 * 本来就是新数据，该 tab 照样移出集合。
 *
 * 重拉只在进入 tab 时发生：改动到达时运营者正停在某个 tab 上，这个 tab 不会
 * 当场重拉，但它留在集合里，切走再回来就会重拉。
 *
 * 整理状态面板的"查看该场档案"切到档案 tab 后调档案面板的 selectSession 切场，
 * 档案 tab 待重拉时先等档案面板重拉完再切场。页头"刷新"清空集合并重拉徽标与
 * 所有已挂载面板。
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
import { MEMORY_TAB_KEYS, resolveMemoryTab, type MemoryTabKey } from '@/components/memory/pageTabs';
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

// ===== 跨面板联动：面板改动 → tab 进待重拉集合，进入 tab 时重拉；整理状态 → 切档案 tab 切场 =====

/**
 * 待重拉的 tab 集合：集合里的 tab，其面板正在显示的内容可能已经过期，进入该 tab
 * 时要重拉。模板和侦听器都不读它，所以是普通变量，不占响应式状态。
 */
const tabsToReload = new Set<MemoryTabKey>();

/** 任一面板报告改动：七个 tab 全部记为待重拉（不区分这次改动影响哪些 tab），徽标计数跟着刷新 */
function onPanelChanged(): void {
  for (const key of MEMORY_TAB_KEYS) tabsToReload.add(key);
  void refreshBadges();
}

/**
 * 切到一个 tab：它在待重拉集合里就重拉它的面板并移出集合，不在就不动。面板还没
 * 挂载时重拉是空操作，tab 照样移出——它首次挂载时拉到的本来就是新数据。
 */
function onTabEnter(key: MemoryTabKey): void {
  if (!tabsToReload.has(key)) return;
  tabsToReload.delete(key);
  void PANEL_RELOAD_BY_TAB[key]();
}

/**
 * 整理状态面板的"查看该场档案"：切到档案 tab，面板挂载/就绪后切到那一场。
 *
 * 档案 tab 待重拉且档案面板已挂载时，先等面板重拉完再切场：重拉会刷新场次列表
 * （重整会改这一场的标题）并重载当前场次的档案行，切场随后拉目标场次的行。
 * 这里自己负责重拉并等待，不走进入 tab 的通用联动——那边不等待重拉完成。档案
 * 面板还没挂载时不必重拉：首次挂载拉到的就是新数据。
 */
async function openArchive(liveSessionId: number): Promise<void> {
  // 面板是否已挂载要在切 tab 之前看：切过去之后它总是已挂载
  const reloadFirst = archivePanel.value !== null && tabsToReload.has('archive');
  // 档案 tab 由这里从待重拉集合里取走，进入 tab 的通用联动就不会重复重拉一遍
  tabsToReload.delete('archive');
  activeTab.value = 'archive';
  await nextTick();
  if (reloadFirst) await archivePanel.value?.reload();
  archivePanel.value?.selectSession(liveSessionId);
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

/**
 * 各 tab 对应面板的重拉入口，以 tab 键索引，切 tab 与页头"刷新"共用。公告面板
 * 暴露的方法名是 load，其余面板都是 reload。该 tab 的面板还没挂载时返回 undefined。
 */
const PANEL_RELOAD_BY_TAB: Record<MemoryTabKey, () => Promise<void> | undefined> = {
  commitment: () => commitmentPanel.value?.reload(),
  candidates: () => candidatePanel.value?.reload(),
  settle: () => settlePanel.value?.reload(),
  archive: () => archivePanel.value?.reload(),
  players: () => playerPanel.value?.reload(),
  'player-candidates': () => playerCandidatePanel.value?.reload(),
  announcement: () => announcementPanel.value?.load(),
};

async function reload(): Promise<void> {
  refreshing.value = true;
  try {
    // 集合在重拉之前清空：重拉进行期间到达的改动会重新把 tab 记进集合，
    // 不会被这一轮重拉的结果冲掉
    tabsToReload.clear();
    await Promise.all([refreshBadges(), ...MEMORY_TAB_KEYS.map(key => PANEL_RELOAD_BY_TAB[key]())]);
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
