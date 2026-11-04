<template>
  <section class="panel commitment-panel" aria-label="承诺账本">
    <header class="panel-head">
      <div class="head-text">
        <div class="panel-title">承诺账本</div>
        <p class="panel-hint">
          她在直播里当众答应过的事。整理时逐字校验原话；“对全场”是不针对具体某位观众的约定。
        </p>
      </div>
      <div class="head-actions">
        <el-button size="small" :loading="loading" @click="reload()">刷新</el-button>
        <el-button size="small" type="primary" @click="openCreate">手建承诺</el-button>
      </div>
    </header>

    <div class="filter-row">
      <el-radio-group
        :model-value="statusFilter"
        size="small"
        aria-label="按状态筛选承诺"
        @update:model-value="onFilterChange"
      >
        <el-radio-button
          v-for="option in STATUS_OPTIONS"
          :key="option.value"
          :value="option.value"
          >{{ option.label }}</el-radio-button
        >
      </el-radio-group>
      <span class="count-hint mono">{{ summaryText }}</span>
    </div>

    <p v-if="loadError" class="panel-error">{{ loadError }}</p>

    <div v-loading="loading" class="commitment-list">
      <article
        v-for="item in items"
        :key="item.id"
        class="commitment-item"
        :class="`status-${item.status}`"
      >
        <div class="item-main">
          <div class="item-head">
            <span class="item-what">{{ item.what }}</span>
            <el-tag size="small" :type="commitmentStatusTagType(item.status)">{{
              commitmentStatusLabel(item.status)
            }}</el-tag>
            <span class="item-target">{{ commitmentTargetLabel(item) }}</span>
            <el-tag v-if="item.game" size="small" type="info" effect="plain">{{
              item.game
            }}</el-tag>
          </div>
          <blockquote v-if="item.quote" class="item-quote">“{{ item.quote }}”</blockquote>
          <p v-else-if="commitmentQuoteState(item) === 'deleted'" class="item-quote is-empty">
            原句已删除（引用它的那句台词已被删除，这条承诺保留）
          </p>
          <p v-else class="item-quote is-empty">手工登记，没有原话</p>
          <p class="item-meta mono">
            登记于 {{ formatDateTimeCn(item.created_at_ms) }}
            <template v-if="item.created_session_id !== null">
              · 场次 #{{ item.created_session_id }}
            </template>
            <template v-if="item.close_quote"> · 了结原话 “{{ item.close_quote }}”</template>
          </p>
          <p v-if="rowErrors[item.id]" class="row-error">{{ rowErrors[item.id] }}</p>
        </div>
        <div class="item-actions">
          <template v-if="item.status === 'open'">
            <el-button
              link
              size="small"
              type="primary"
              :loading="busyId === item.id"
              :disabled="busyId !== null"
              @click="markClosed(item)"
              >标了结</el-button
            >
            <el-button
              link
              size="small"
              type="warning"
              :loading="busyId === item.id"
              :disabled="busyId !== null"
              @click="markVoid(item)"
              >判误记</el-button
            >
          </template>
          <el-button
            link
            size="small"
            type="danger"
            :loading="busyId === item.id"
            :disabled="busyId !== null"
            @click="remove(item)"
            >删除</el-button
          >
        </div>
      </article>

      <p v-if="!loading && items.length === 0 && !loadError" class="empty-hint">{{ emptyHint }}</p>
    </div>

    <div v-if="items.length < total" class="more-row">
      <el-button size="small" :loading="loadingMore" @click="loadMore">
        加载更多（已显示 {{ items.length }}/{{ total }}）
      </el-button>
    </div>

    <el-dialog v-model="createVisible" title="手建一条承诺" width="520px">
      <div class="form-field">
        <label class="form-label">她答应的事</label>
        <el-input
          v-model="createForm.what"
          maxlength="60"
          show-word-limit
          placeholder="动作标题，例如：下次直播唱《晴天》"
        />
      </div>
      <div class="form-field">
        <label class="form-label">原话（可空）</label>
        <el-input
          v-model="createForm.quote"
          maxlength="200"
          show-word-limit
          placeholder="她当时说的原句；没有就留空"
        />
      </div>
      <div class="form-field">
        <label class="form-label">对谁（可空）</label>
        <ViewerPicker v-model="createForm.viewerId" placeholder="留空 = 对全场" />
      </div>
      <div class="form-field">
        <label class="form-label">游戏（可空）</label>
        <el-input v-model="createForm.game" maxlength="60" placeholder="例如：Minecraft" />
      </div>
      <p v-if="createError" class="row-error">{{ createError }}</p>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="submitCreate">登记</el-button>
      </template>
    </el-dialog>
  </section>
</template>

<script setup lang="ts">
/**
 * 承诺账本面板 —— 她答应过的事（未了结 / 已了结 / 误记）的查看与人工纠错
 *
 * 列表按状态筛选、按页续拉（加载更多）；行级操作只有 open 态才给"标了结"
 * "判误记"，删除任何状态都能做且必须二次确认。行操作失败（含 409：这条已
 * 被别的标签处理过或已不存在）只写该行行内提示，不打断整页；409 另重拉列表
 * 对齐最新状态但保留行内提示。列表请求带序号（筛选/刷新/行操作并发时只有最后
 * 发起的那次生效），行操作成功后按已加载条数重拉，不把分页打回第一页。
 * 手建对话框复用观众选择器，观众 ID 留空表示对全场。
 */
import { computed, onMounted, ref } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import type { CommitmentCreatePayload, CommitmentItem } from '@/types';
import { ledgerApi } from '@/api';
import ViewerPicker from '@/components/viewers/ViewerPicker.vue';
import { confirmAction } from '@/utils/confirmAction';
import { getApiErrorMessage, getApiErrorStatus } from '@/utils/apiError';
import { formatDateTimeCn } from '@/utils/format';
import {
  commitmentQuoteState,
  commitmentStatusLabel,
  commitmentStatusQuery,
  commitmentStatusTagType,
  commitmentTargetLabel,
  validateCloseQuote,
  type CommitmentStatusFilter,
} from './ledgerView';

const PAGE_SIZE = 20;

const STATUS_OPTIONS: Array<{ value: CommitmentStatusFilter; label: string }> = [
  { value: 'all', label: '全部' },
  { value: 'open', label: '未了结' },
  { value: 'closed', label: '已了结' },
  { value: 'void', label: '误记' },
];

const statusFilter = ref<CommitmentStatusFilter>('all');
const items = ref<CommitmentItem[]>([]);
const total = ref(0);
const openCount = ref(0);
const voidCount = ref(0);
const loading = ref(false);
const loadingMore = ref(false);
const loadError = ref('');
/** 行级操作错误：按承诺 id 收，只在该行显示 */
const rowErrors = ref<Record<number, string>>({});
const busyId = ref<number | null>(null);
/** 列表请求序号：筛选 / 刷新 / 行操作并发时只有最后发起的那次生效 */
let requestSeq = 0;
/** 「加载更多」独立序号（刷新会让在途的加载更多作废） */
let moreSeq = 0;

const summaryText = computed(
  () => `未了结 ${openCount.value} · 误记 ${voidCount.value} · 当前筛选 ${total.value} 条`,
);

const emptyHint = computed(() => {
  switch (statusFilter.value) {
    case 'open':
      return '没有未了结的承诺';
    case 'closed':
      return '还没有已了结的承诺';
    case 'void':
      return '没有判为误记的承诺';
    default:
      return '还没有承诺——每场正式场收播后，主播会把答应过的事整理进来';
  }
});

function clearRowError(id: number): void {
  if (!(id in rowErrors.value)) return;
  const next = { ...rowErrors.value };
  delete next[id];
  rowErrors.value = next;
}

interface ReloadOptions {
  /** 保留行内错误（409 后重拉时用：重新对齐列表但不抹掉"这条已被处理"的提示） */
  keepRowErrors?: boolean;
  /** 重拉的条数；行操作后按"已加载条数"重拉，不把分页打回第一页 */
  limit?: number;
}

/**
 * 从第一页重拉列表。筛选变化、页头刷新、行操作后都走这里：
 * 序号丢弃过期响应；行操作后可指定条数保持当前已加载量，并保留行内错误。
 */
async function reload(options: ReloadOptions = {}): Promise<void> {
  const seq = ++requestSeq;
  moreSeq += 1; // 让在途的「加载更多」作废
  loadingMore.value = false;
  loading.value = true;
  loadError.value = '';
  if (!options.keepRowErrors) rowErrors.value = {};
  try {
    const response = await ledgerApi.listCommitments({
      status: commitmentStatusQuery(statusFilter.value),
      limit: options.limit ?? PAGE_SIZE,
      offset: 0,
    });
    if (seq !== requestSeq) return;
    items.value = response.data.items;
    total.value = response.data.total;
    openCount.value = response.data.open_count;
    voidCount.value = response.data.void_count;
  } catch (error) {
    if (seq !== requestSeq) return;
    loadError.value = getApiErrorMessage(error, '承诺列表加载失败');
  } finally {
    if (seq === requestSeq) loading.value = false;
  }
}

async function loadMore(): Promise<void> {
  if (loading.value || loadingMore.value || items.value.length >= total.value) return;
  const seq = ++moreSeq;
  const seqAtStart = requestSeq;
  const offset = items.value.length;
  loadingMore.value = true;
  try {
    const response = await ledgerApi.listCommitments({
      status: commitmentStatusQuery(statusFilter.value),
      limit: PAGE_SIZE,
      offset,
    });
    if (seq !== moreSeq || seqAtStart !== requestSeq) return;
    items.value = [...items.value, ...response.data.items];
    total.value = response.data.total;
    openCount.value = response.data.open_count;
    voidCount.value = response.data.void_count;
  } catch (error) {
    if (seq !== moreSeq || seqAtStart !== requestSeq) return;
    loadError.value = getApiErrorMessage(error, '承诺列表加载失败');
  } finally {
    loadingMore.value = false;
  }
}

function onFilterChange(value: string | number | boolean | undefined): void {
  const matched = STATUS_OPTIONS.find(option => option.value === value);
  if (!matched || matched.value === statusFilter.value) return;
  statusFilter.value = matched.value;
  void reload();
}

/**
 * 行级写操作：成功后按已加载条数重拉（不把分页打回第一页），失败只在该行留提示。
 * 409 表示这条已被别的标签处理过或已不存在——重拉列表对齐最新状态，但保留行内提示。
 */
async function mutate(
  item: CommitmentItem,
  run: () => Promise<unknown>,
  doneText: string,
): Promise<void> {
  busyId.value = item.id;
  clearRowError(item.id);
  const refreshLimit = Math.max(PAGE_SIZE, items.value.length);
  try {
    await run();
    ElMessage.success(doneText);
    await reload({ limit: refreshLimit });
  } catch (error) {
    rowErrors.value = {
      ...rowErrors.value,
      [item.id]: getApiErrorMessage(error, '操作失败'),
    };
    if (getApiErrorStatus(error) === 409) {
      await reload({ keepRowErrors: true, limit: refreshLimit });
    }
  } finally {
    busyId.value = null;
  }
}

async function markClosed(item: CommitmentItem): Promise<void> {
  let closeQuote = '';
  try {
    const { value } = await ElMessageBox.prompt(
      '已了结的承诺不能再改回未了结。可以留下她做完（或不做了）的那句原话。',
      '标为已了结',
      {
        confirmButtonText: '标为了结',
        cancelButtonText: '取消',
        inputType: 'textarea',
        inputPlaceholder: '了结依据的原话（可空）',
        // 与后端 CommitmentCloseRequest.close_quote 的 200 字上限对齐
        inputValidator: (value: string) => validateCloseQuote(value ?? '') ?? true,
      },
    );
    closeQuote = (value ?? '').trim();
  } catch {
    return;
  }
  await mutate(
    item,
    () => ledgerApi.closeCommitment(item.id, { close_quote: closeQuote || undefined }),
    '已标为完成',
  );
}

async function markVoid(item: CommitmentItem): Promise<void> {
  if (
    !(await confirmAction(
      `把「${item.what}」判为误记？会留一行墓碑，之后整理再遇到同一件事不会重新登记；误记不能改回未了结。`,
      '判为误记',
    ))
  ) {
    return;
  }
  await mutate(item, () => ledgerApi.voidCommitment(item.id), '已判为误记');
}

async function remove(item: CommitmentItem): Promise<void> {
  if (
    !(await confirmAction(
      `彻底删除承诺「${item.what}」？连墓碑也不留，之后整理若再提到会重新登记。此操作不可恢复。`,
      '删除承诺',
    ))
  ) {
    return;
  }
  await mutate(item, () => ledgerApi.deleteCommitment(item.id), '已删除承诺');
}

// ===== 手建承诺 =====

const createVisible = ref(false);
const creating = ref(false);
const createError = ref('');
const createForm = ref<{ what: string; quote: string; viewerId: number | null; game: string }>({
  what: '',
  quote: '',
  viewerId: null,
  game: '',
});

function openCreate(): void {
  createForm.value = { what: '', quote: '', viewerId: null, game: '' };
  createError.value = '';
  createVisible.value = true;
}

async function submitCreate(): Promise<void> {
  const what = createForm.value.what.trim();
  if (!what) {
    createError.value = '标题不能为空';
    return;
  }
  const payload: CommitmentCreatePayload = {
    what,
    quote: createForm.value.quote.trim() || undefined,
    viewer_id: createForm.value.viewerId,
    game: createForm.value.game.trim() || undefined,
  };
  creating.value = true;
  createError.value = '';
  try {
    await ledgerApi.createCommitment(payload);
    ElMessage.success('已登记');
    createVisible.value = false;
    // 新承诺一定是未了结态：停在“已了结/误记”筛选上会看不到刚建的那条
    if (statusFilter.value === 'closed' || statusFilter.value === 'void')
      statusFilter.value = 'all';
    await reload();
  } catch (error) {
    createError.value = getApiErrorMessage(error, '登记失败');
  } finally {
    creating.value = false;
  }
}

onMounted(() => {
  void reload();
});

defineExpose({ reload });
</script>

<style scoped>
.commitment-panel {
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

.filter-row {
  display: flex;
  align-items: center;
  gap: var(--spacing-md);
  flex-wrap: wrap;
}

.count-hint {
  font-size: 12px;
  color: var(--text-secondary);
}

.panel-error {
  margin: 0;
  font-size: 12px;
  color: var(--color-danger);
}

.commitment-list {
  display: flex;
  flex-direction: column;
  min-height: 40px;
}

.commitment-item {
  display: flex;
  align-items: flex-start;
  gap: var(--spacing-md);
  padding: 10px 0;
  border-top: 1px dashed var(--border-color-light);
}

.commitment-item:first-child {
  border-top: none;
}

.item-main {
  flex: 1;
  min-width: 0;
}

.item-head {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  flex-wrap: wrap;
}

.item-what {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.item-target {
  font-size: 12px;
  color: var(--text-secondary);
}

.item-quote {
  margin: 6px 0 0;
  padding-left: var(--spacing-sm);
  border-left: 2px solid var(--border-color-light);
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-regular);
}

.item-quote.is-empty {
  border-left-color: transparent;
  color: var(--text-placeholder);
  font-size: 12px;
}

.item-meta {
  margin: 4px 0 0;
  font-size: 11px;
  color: var(--text-placeholder);
}

.item-actions {
  display: flex;
  align-items: center;
  gap: 2px;
  flex-shrink: 0;
}

.row-error {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--color-danger);
}

/* 已了结 / 误记的行弱化，方便一眼扫出还欠着的 */
.commitment-item.status-closed .item-what,
.commitment-item.status-void .item-what {
  font-weight: 500;
  color: var(--text-secondary);
}

.commitment-item.status-void {
  opacity: 0.7;
}

.more-row {
  display: flex;
  justify-content: center;
}

.empty-hint {
  padding: var(--spacing-md);
  text-align: center;
  font-size: 13px;
  color: var(--text-placeholder);
}

.form-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-bottom: var(--spacing-sm);
}

.form-label {
  font-size: 12px;
  color: var(--text-secondary);
}

@media (max-width: 860px) {
  .panel-head {
    flex-direction: column;
  }
  .commitment-item {
    flex-direction: column;
  }
  .item-actions {
    align-self: flex-end;
  }
}
</style>
