<template>
  <section class="panel player-panel" aria-label="游戏里的人">
    <header class="panel-head">
      <div class="head-text">
        <div class="panel-title">
          游戏里的人
          <el-tag v-if="items.length > 0" size="small" type="info">{{ items.length }} 位</el-tag>
          <el-tag v-if="forgetPendingCount > 0" size="small" type="danger"
            >{{ forgetPendingCount }} 位待删除</el-tag
          >
        </div>
        <p class="panel-hint">
          她在游戏里遇到过的玩家。玩家开口时她会看到一条玩家线索；可以手动维护纯游戏侧的常用称呼，帮助她在游戏公屏与记忆里认准同一人。
          这里的关联观众她看不到——确认“这位玩家就是直播间的某位观众”后，删这位观众、或任何一方发“忘了我”，两边一起删除；同名、名字像都不会自动关联。
        </p>
      </div>
      <div class="head-actions">
        <el-input
          v-model="query"
          size="small"
          clearable
          class="search-input"
          placeholder="按名字、曾用名或常用称呼找"
        />
        <el-button size="small" :loading="loading" @click="reload">刷新</el-button>
      </div>
    </header>

    <p v-if="loadError" class="panel-error">{{ loadError }}</p>
    <p v-if="actionError" class="panel-error">{{ actionError }}</p>
    <ul v-loading="loading" class="player-list">
      <li v-for="item in shown" :key="item.id" class="player-item">
        <div class="player-head">
          <span class="player-name">{{ formatPlayerDisplayName(item.name, item.aliases) }}</span>
          <span class="player-game mono">{{ item.game }}</span>
          <el-tag
            v-if="item.forget_requested_at_ms !== null"
            size="small"
            type="danger"
            effect="plain"
            >{{
              formatDateTimeCn(item.forget_requested_at_ms)
            }}
            在游戏公屏发过“忘了我”，下播清理时删除</el-tag
          >
          <span class="grow" />
          <span class="player-seen mono">最近见到 {{ formatDateTimeCn(item.last_seen_ms) }}</span>
        </div>
        <p v-if="item.former_names.length > 0" class="player-meta">
          {{ formerNamesText(item.former_names) }}
        </p>
        <div class="player-aliases">
          <span class="links-label">常用称呼</span>
          <template v-if="editingAliasId === item.id">
            <el-input
              v-model="aliasDraft"
              size="small"
              clearable
              class="alias-input"
              placeholder="多个称呼用顿号或逗号分隔，留空清空"
              @keyup.enter="saveAliases(item)"
            />
            <el-button
              size="small"
              type="primary"
              :loading="busyId === item.id"
              :disabled="busyId !== null && busyId !== item.id"
              @click="saveAliases(item)"
              >保存</el-button
            >
            <el-button size="small" :disabled="busyId === item.id" @click="cancelEditAliases"
              >取消</el-button
            >
          </template>
          <template v-else>
            <el-tag
              v-for="alias in item.aliases"
              :key="alias"
              size="small"
              type="success"
              effect="plain"
              disable-transitions
              >{{ alias }}</el-tag
            >
            <span v-if="item.aliases.length === 0" class="links-empty">未设置</span>
            <el-button
              size="small"
              link
              type="primary"
              :disabled="busyId !== null"
              @click="startEditAliases(item)"
              >编辑称呼</el-button
            >
          </template>
        </div>
        <p v-if="item.saves.length > 0" class="player-meta">{{ savesText(item.saves) }}</p>
        <!-- 离线模式的存档：服务器不验正版账号，在那里认人只认得名字，标出来让运营者心里有数 -->
        <p v-if="offlineSavesNote(item.saves)" class="player-meta offline-note">
          {{ offlineSavesNote(item.saves) }}
        </p>
        <p class="player-meta">{{ interactionsText(item.interactions) }}</p>
        <div class="player-links">
          <span class="links-label">关联观众</span>
          <el-tag
            v-for="viewer in item.linked_viewers"
            :key="viewer.viewer_id"
            size="small"
            closable
            disable-transitions
            @close="unlink(item, viewer)"
            >{{ linkedViewerLabel(viewer) }}</el-tag
          >
          <span v-if="item.linked_viewers.length === 0" class="links-empty">还没有</span>
        </div>
        <div class="player-actions">
          <ViewerPicker
            class="link-picker"
            :model-value="linkTargets[item.id] ?? null"
            placeholder="搜索观众昵称，确认就是这位玩家"
            @update:model-value="value => (linkTargets[item.id] = value)"
            @select="viewer => (linkPicked[item.id] = viewer)"
          />
          <el-button
            size="small"
            :loading="busyId === item.id"
            :disabled="!linkTargets[item.id] || (busyId !== null && busyId !== item.id)"
            @click="link(item)"
            >确认关联</el-button
          >
          <span class="grow" />
          <el-button
            size="small"
            type="danger"
            plain
            :loading="busyId === item.id"
            :disabled="busyId !== null && busyId !== item.id"
            @click="remove(item)"
            >删除玩家…</el-button
          >
        </div>
      </li>
      <li v-if="!loading && !loadError && shown.length === 0" class="empty-hint">
        {{ items.length === 0 ? '还没在游戏里遇到过玩家' : '没有找到这个名字' }}
      </li>
    </ul>
  </section>
</template>

<script setup lang="ts">
/**
 * 游戏里的人 —— 她在游戏里遇到过的玩家：看名字与互动、确认对应的直播间观众、删玩家
 *
 * 对号只由运营者确认：从观众搜索里挑出一位，点确认才记下关联；关联只用来连带删除（删这位观众、
 * 或任何一方发“忘了我”时两边一起删），不会告诉她。删玩家会删掉他的互动、游戏公屏和对他的承诺，
 * 名字换成“一位玩家”，UUID 进不记录名单；在这里删玩家不动关联的观众。见过他的存档里，服务器不验正版账号
 * （离线模式）的单独标明：那里按名字认人，改名算新人，冒名认不出。
 *
 * 不含定时器 / 订阅 / WebSocket，无 keep-alive 生命周期语义。
 */
import { computed, onMounted, reactive, ref } from 'vue';
import { ElMessage } from 'element-plus';
import type { PlayerItem, PlayerLinkedViewer, ViewerListItem } from '@/types';
import { playersApi } from '@/api';
import ViewerPicker from '@/components/viewers/ViewerPicker.vue';
import { getApiErrorMessage } from '@/utils/apiError';
import { confirmAction } from '@/utils/confirmAction';
import { formatDateTimeCn } from '@/utils/format';
import {
  deletePlayerWarning,
  formatAliasesInput,
  formatPlayerDisplayName,
  formerNamesText,
  interactionsText,
  linkedViewerLabel,
  linkViewerWarning,
  matchesPlayerQuery,
  offlineSavesNote,
  parseAliasesInput,
  savesText,
} from './playersView';

const items = ref<PlayerItem[]>([]);
const query = ref('');
const loading = ref(false);
const loadError = ref('');
const actionError = ref('');
const busyId = ref<number | null>(null);
/** 正在行内编辑常用称呼的玩家 ID（一次仅编辑一位，与观众关联互不干扰） */
const editingAliasId = ref<number | null>(null);
const aliasDraft = ref('');
/** 每位玩家行里观众选择器选中的观众（点“确认关联”时交上去） */
const linkTargets = reactive<Record<number, number | null>>({});
/** 选择器选中的那位观众（昵称等）：确认对话框点名用 */
const linkPicked = reactive<Record<number, ViewerListItem | null>>({});

/** 按名字、曾用名或常用称呼过滤后的列表 */
const shown = computed(() => items.value.filter(item => matchesPlayerQuery(item, query.value)));
/** 发过“忘了我”、还在宽限期里的玩家数 */
const forgetPendingCount = computed(
  () => items.value.filter(item => item.forget_requested_at_ms !== null).length,
);

async function reload(): Promise<void> {
  loading.value = true;
  loadError.value = '';
  try {
    const { data } = await playersApi.list();
    items.value = data.items;
  } catch (err) {
    loadError.value = getApiErrorMessage(err, '玩家列表加载失败');
  } finally {
    loading.value = false;
  }
}

function startEditAliases(item: PlayerItem): void {
  actionError.value = '';
  editingAliasId.value = item.id;
  aliasDraft.value = formatAliasesInput(item.aliases);
}

function cancelEditAliases(): void {
  editingAliasId.value = null;
  aliasDraft.value = '';
}

async function saveAliases(item: PlayerItem): Promise<void> {
  const aliases = parseAliasesInput(aliasDraft.value, item.name);
  const ok = await mutate(
    item,
    () => playersApi.updateAliases(item.id, { aliases }),
    '已更新常用称呼',
    '更新常用称呼失败',
  );
  if (ok) {
    cancelEditAliases();
  }
}

async function link(item: PlayerItem): Promise<void> {
  const viewerId = linkTargets[item.id];
  if (!viewerId) return;
  // 确认前点名是哪位观众（昵称加编号）、讲清后果：之后删其中一方或一方发“忘了我”，另一方一起删
  const picked = linkPicked[item.id];
  const warning = linkViewerWarning(item, {
    viewer_id: viewerId,
    user_name: picked?.viewer_id === viewerId ? picked.user_name : '',
  });
  if (!(await confirmAction(warning, '确认关联', { type: 'info' }))) return;
  await mutate(
    item,
    () => playersApi.linkViewer(item.id, { viewer_id: viewerId }),
    '已确认关联',
    '关联失败',
  );
  linkTargets[item.id] = null;
  linkPicked[item.id] = null;
}

async function unlink(item: PlayerItem, viewer: PlayerLinkedViewer): Promise<void> {
  const warning = `取消「${item.name}」和「${linkedViewerLabel(viewer)}」的关联？之后删这位观众不再连带删他。`;
  if (!(await confirmAction(warning, '取消关联'))) return;
  await mutate(
    item,
    () => playersApi.unlinkViewer(item.id, viewer.viewer_id),
    '已取消关联',
    '取消关联失败',
  );
}

async function remove(item: PlayerItem): Promise<void> {
  if (!(await confirmAction(deletePlayerWarning(item), '删除玩家', { type: 'error' }))) return;
  await mutate(item, () => playersApi.deletePlayer(item.id), '已删除玩家', '删除玩家失败');
}

async function mutate(
  item: PlayerItem,
  action: () => Promise<unknown>,
  success: string,
  fallback: string,
): Promise<boolean> {
  busyId.value = item.id;
  actionError.value = '';
  try {
    await action();
    ElMessage.success(success);
    await reload();
    return true;
  } catch (err) {
    actionError.value = getApiErrorMessage(err, fallback);
    return false;
  } finally {
    busyId.value = null;
  }
}

onMounted(() => {
  void reload();
});

defineExpose({ reload });
</script>

<style scoped>
.player-panel {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
  padding: 14px 16px;
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-md);
  background: var(--bg-card);
}

.panel-head {
  display: flex;
  align-items: flex-start;
  gap: var(--spacing-md);
}

.head-text {
  flex: 1;
  min-width: 0;
}

.panel-title {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  font-size: 15px;
  font-weight: 650;
  color: var(--text-primary);
}

.panel-hint {
  margin: 2px 0 0;
  font-size: 12px;
  color: var(--text-secondary);
}

.head-actions {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  flex-shrink: 0;
}

.search-input {
  width: 200px;
}

.panel-error {
  margin: 0;
  font-size: 12px;
  color: var(--color-danger);
}

.player-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
  min-height: 40px;
}

.player-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px 12px;
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-sm);
}

.player-head,
.player-aliases,
.player-links,
.player-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--spacing-sm);
}

.alias-input {
  width: 260px;
}

.player-name {
  font-weight: 600;
  color: var(--text-primary);
}

.player-game,
.player-seen,
.links-label,
.links-empty,
.player-meta {
  font-size: 12px;
  color: var(--text-secondary);
}

.player-meta {
  margin: 0;
}

.offline-note {
  color: var(--color-warning);
}

.link-picker {
  width: 260px;
}

.grow {
  flex: 1;
}

.empty-hint {
  padding: 12px 0;
  font-size: 12px;
  color: var(--text-secondary);
  text-align: center;
}
</style>
