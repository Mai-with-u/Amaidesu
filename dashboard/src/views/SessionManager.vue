<template>
  <div class="sessions-page">
    <!-- 页面头：这一页管的是"所有场次"（直播控制台管正在播的那一场） -->
    <header class="page-header">
      <div class="header-left">
        <h1 class="page-title">场次管理</h1>
        <p class="page-subtitle">
          所有场次在一张表里 · 筛出要处理的，逐场或批量处置
          <span class="scope-note">改类型会动到该场登记的账本，删除不可撤销</span>
        </p>
      </div>
      <div class="header-actions">
        <el-button type="primary" plain :loading="loading" @click="load">刷新</el-button>
      </div>
    </header>

    <!-- 筛选条：服务端筛选，筛选状态写回 URL（清单视图可直接分享） -->
    <div class="toolbar">
      <el-input
        v-model="query"
        class="search-input"
        placeholder="搜索场次标题"
        clearable
        aria-label="搜索场次标题"
      />
      <el-select v-model="sourceFilter" class="filter-select" aria-label="按来源筛选场次">
        <template #prefix><span class="filter-cap">来源</span></template>
        <el-option label="全部" :value="SESSION_FILTER_ALL" />
        <el-option
          v-for="opt in SESSION_SOURCE_OPTIONS"
          :key="opt.value"
          :label="opt.label"
          :value="opt.value"
        />
      </el-select>
      <el-select v-model="kindFilter" class="filter-select" aria-label="按类型筛选场次">
        <template #prefix><span class="filter-cap">类型</span></template>
        <el-option label="全部" :value="SESSION_FILTER_ALL" />
        <el-option
          v-for="opt in SESSION_KIND_OPTIONS"
          :key="opt.value"
          :label="opt.label"
          :value="opt.value"
        />
      </el-select>
      <el-select v-model="settleFilter" class="filter-select" aria-label="按整理状态筛选场次">
        <template #prefix><span class="filter-cap">整理</span></template>
        <el-option label="全部" value="all" />
        <el-option label="待整理" value="pending" />
        <el-option label="整理失败" value="failed" />
        <el-option label="已整理待审" value="review" />
        <el-option label="已审" value="reviewed" />
      </el-select>
      <el-date-picker
        v-model="dateRange"
        type="daterange"
        class="date-range"
        range-separator="→"
        start-placeholder="开播起"
        end-placeholder="开播止"
        aria-label="开播日期区间"
      />
      <span class="grow" />
      <span class="count-hint mono">{{ shownRange }} / {{ total }}</span>
    </div>

    <!-- 批量条：勾选后出现 -->
    <div v-if="selectedIds.length" class="batch-bar">
      <span class="batch-count">已选 {{ selectedIds.length }} 场</span>
      <el-button size="small" @click="batchSetKind('live')">批量改正式</el-button>
      <el-button size="small" @click="batchSetKind('rehearsal')">批量改彩排</el-button>
      <el-button size="small" @click="downloadExport()">导出数据包</el-button>
      <el-button size="small" type="danger" plain @click="batchRemove()">批量删除</el-button>
      <span class="grow" />
      <el-button size="small" link @click="clearSelection()">取消选择</el-button>
    </div>

    <!-- 汇总：全部由当页这批场次算出 -->
    <section class="summary-card">
      <div class="summary-stats">
        <span class="stat"
          >场次 <b>{{ summary.count }}</b></span
        >
        <span class="stat"
          >总时长 <b class="mono">{{ durationText(summary.totalDurationMs) }}</b></span
        >
        <span class="stat"
          >平均 <b class="mono">{{ durationText(summary.averageDurationMs) }}</b></span
        >
        <span class="stat"
          >最长 <b class="mono">{{ durationText(summary.longestDurationMs) }}</b></span
        >
        <span class="stat"
          >消息 <b>{{ formatNumber(summary.messageTotal) }}</b> 条</span
        >
        <span class="stat">
          正式 <b>{{ summary.kindCounts.live }}</b> · 彩排 <b>{{ summary.kindCounts.rehearsal }}</b>
        </span>
        <span class="stat source-line">
          来源
          <template v-for="(count, source) in summary.sourceCounts" :key="source">
            <span class="source-chip">{{ sourceLabel(source) }} {{ count }}</span>
          </template>
        </span>
      </div>

      <!-- 场次日历热力图：哪天在播、播了几场 -->
      <div class="summary-heat">
        <div class="heat-head">
          <span class="heat-title">按开播日</span>
          <span class="heat-note">近 {{ heatmap.weeks.length }} 周</span>
        </div>
        <div class="heat-body">
          <div class="heat-weekdays" aria-hidden="true">
            <span>一</span>
            <span>三</span>
            <span>五</span>
          </div>
          <div class="heat-scroll">
            <div class="heat-grid">
              <div
                v-for="week in heatmap.weeks"
                :key="week.days[0]?.date ?? week.monthLabel"
                class="heat-week"
              >
                <span class="heat-month">{{ week.monthLabel }}</span>
                <el-tooltip
                  v-for="(day, dayIndex) in week.days"
                  :key="dayIndex"
                  :content="dayTip(day)"
                  placement="top"
                  :disabled="!day"
                >
                  <span
                    class="heat-cell"
                    :class="[`lv${heatmapLevel(day)}`, { 'is-blank': !day }]"
                  />
                </el-tooltip>
              </div>
            </div>
          </div>
        </div>
        <div class="heat-legend">
          <span>少</span>
          <span class="heat-cell lv0" /><span class="heat-cell lv1" /><span
            class="heat-cell lv2"
          /><span class="heat-cell lv3" /><span class="heat-cell lv4" />
          <span>多</span>
        </div>
      </div>
    </section>

    <!-- 场次表 -->
    <section class="table-card">
      <el-table
        v-loading="loading"
        :data="items"
        row-key="live_session_id"
        class="session-table"
        @selection-change="setSelection"
      >
        <el-table-column type="selection" width="42" />
        <el-table-column label="场次" min-width="180">
          <template #default="{ row }">
            <span class="cell-name" :title="row.title ?? `场次 #${row.live_session_id}`">
              {{ row.title ?? `场次 #${row.live_session_id}` }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="类型" width="64">
          <template #default="{ row }">
            <span class="badge" :class="`is-${row.kind}`">{{ kindLabel(row.kind) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="来源" width="64">
          <template #default="{ row }">
            <span class="badge" :class="`is-${row.source}`">{{ sourceLabel(row.source) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="开播" width="160">
          <template #default="{ row }">
            <span class="mono nowrap">{{ startedLabel(row.started_at_ms) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="用时" width="120">
          <template #default="{ row }">
            <span class="mono nowrap" :class="{ 'is-running': row.is_active }">
              {{ sessionDurationLabel(row, nowTick) ?? '—' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="消息" width="64" align="right">
          <template #default="{ row }">
            <span class="mono">{{ row.message_count }}</span>
          </template>
        </el-table-column>
        <el-table-column label="备注" min-width="180">
          <template #default="{ row }">
            <span
              v-if="row.notes"
              class="cell-notes is-editable"
              :title="row.notes"
              @click.stop="openNotes(row)"
              >{{ row.notes }}</span
            >
            <span v-else class="cell-empty">—</span>
          </template>
        </el-table-column>
        <el-table-column label="概要" min-width="180">
          <template #default="{ row }">
            <span v-if="row.overview" class="cell-topic" :title="row.overview">
              {{ row.overview }}
            </span>
            <span v-else class="cell-empty">—</span>
          </template>
        </el-table-column>
        <el-table-column label="整理" width="104">
          <template #default="{ row }">
            <span class="settle-chip" :class="`is-${settleStateOf(row).tone}`">
              {{ settleStateOf(row).label }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="150" align="right">
          <template #default="{ row }">
            <el-button link size="small" @click="goReview(row)">复盘</el-button>
            <el-button link size="small" @click="goReplay(row)">回看</el-button>
            <el-dropdown trigger="click" @command="(cmd: string) => onRowCommand(cmd, row)">
              <el-button link size="small">更多</el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="rename">改名</el-dropdown-item>
                  <el-dropdown-item command="notes">写备注</el-dropdown-item>
                  <el-dropdown-item v-if="!row.is_active" command="kind">
                    改{{ row.kind === 'live' ? '彩排' : '正式' }}
                  </el-dropdown-item>
                  <el-dropdown-item command="resettle">重新整理</el-dropdown-item>
                  <el-dropdown-item command="reviewed">标记已审</el-dropdown-item>
                  <el-dropdown-item command="delete" divided>删除</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </template>
        </el-table-column>
        <template #empty>
          <div class="empty-hint">
            {{
              query || sourceFilter !== SESSION_FILTER_ALL || kindFilter !== SESSION_FILTER_ALL
                ? '没有匹配的场次'
                : '还没有场次——开启直播后这里会出现记录'
            }}
          </div>
        </template>
      </el-table>

      <el-pagination
        v-if="total > pageSize"
        class="pager"
        layout="total, prev, pager, next"
        :total="total"
        :page-size="pageSize"
        :current-page="page"
        @current-change="onPageChange"
      />
    </section>

    <!-- 备注编辑：手写的一段话（与自动整理出的场次档案不是一回事） -->
    <el-dialog v-model="notesVisible" title="场次备注" width="520px">
      <p class="dialog-hint">
        这段话只存在这场次里，写给自己看：{{
          notesTarget?.title ?? `场次 #${notesTarget?.live_session_id}`
        }}
      </p>
      <el-input
        v-model="notesDraft"
        type="textarea"
        :rows="7"
        placeholder="这场想留的话（留空即清除备注）"
      />
      <template #footer>
        <el-button @click="notesVisible = false">取消</el-button>
        <el-button type="primary" @click="submitNotes">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
/**
 * 场次管理页：所有场次的一张表。
 *
 * 与其它三页的分工：直播控制台管正在播的那一场，场次复盘看这一场播得怎么样
 * （只读聚合），记忆账本管整理出来的东西记对了吗；这一页管"有哪些场、什么状态、
 * 要处置哪些"——以全部场次为单位。
 *
 * 表格每列的数据来源（前端只做装配与显示，判定都在 utils/sessionManager.ts）：
 * - 标题：live_sessions 的 title
 * - 类型、来源：live_sessions 的 kind / source
 * - 开播时刻：started_at_ms（毫秒）
 * - 用时：起止时刻之差，进行中的场次按共享时钟走
 * - 消息数：live_chat 行数
 * - 备注：live_sessions 的 notes（人手写的自由文本；列里可点开就着改）
 * - 概要：live_sessions 的 overview（模型写的这场发生了什么；只给人看，不进记忆）
 * - 整理状态：整理三列（整理完成时刻 / 重新整理尝试次数 / 标已审时刻）算出的标签
 * - 汇总与日历：同一批场次在前端算，不另查库
 */
import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import { useSessionManager } from '@/composables/useSessionManager';
import {
  SESSION_FILTER_ALL,
  SESSION_KIND_OPTIONS,
  SESSION_SOURCE_OPTIONS,
  buildHeatmap,
  heatmapLevel,
  kindLabel,
  sourceLabel,
  type HeatmapDay,
} from '@/utils/sessionManager';
import { formatDateYMD, formatElapsedMs, formatNumber, formatTimeHM } from '@/utils/format';
import type { LiveSessionItem } from '@/types';

const router = useRouter();
const {
  items,
  total,
  loading,
  page,
  pageSize,
  query,
  sourceFilter,
  kindFilter,
  dateRange,
  settleFilter,
  selectedIds,
  summary,
  nowTick,
  load,
  applyQueryFromUrl,
  setSelection,
  clearSelection,
  exportUrl,
  renameSession,
  saveNotes,
  toggleSessionKind,
  removeSession,
  batchSetKind,
  batchRemove,
  resettle,
  markReviewed,
  sessionDurationLabel,
  settleStateOf,
} = useSessionManager();

/** 时长展示：固定带小时位（短场次与长场次形状一致） */
function durationText(ms: number): string {
  return formatElapsedMs(ms, { alwaysHours: true });
}

/** 开播时刻：日期 + 时分（秒用不上，列宽留给标题） */
function startedLabel(ms: number): string {
  return `${formatDateYMD(ms)} ${formatTimeHM(ms)}`;
}

/** 当前页显示的区间（"第几到第几 / 共多少"） */
const shownRange = computed<string>(() => {
  if (!total.value) return '0';
  const from = (page.value - 1) * pageSize.value + 1;
  const to = Math.min(page.value * pageSize.value, total.value);
  return `${from}-${to}`;
});

/** 场次日历热力图（同一批场次在前端铺格子） */
const heatmap = computed(() => buildHeatmap(items.value, nowTick.value));

/** 格子的悬停说明：那天播了几场、合计多久 */
function dayTip(day: HeatmapDay | null): string {
  if (!day) return '';
  if (day.count === 0) return `${day.date} 没播`;
  return `${day.date}：${day.count} 场 · ${formatElapsedMs(day.durationMs, { alwaysHours: true })}`;
}

function onPageChange(next: number): void {
  page.value = next;
}

/** 导出数据包：地址由组合式给，点击下载的动作在页面这边做 */
function downloadExport(): void {
  const url = exportUrl();
  if (!url) return;
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.rel = 'noopener';
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
}

/** 跳复盘页：带上场次主键，复盘页认这个参数直接进那一场 */
function goReview(row: LiveSessionItem): void {
  void router.push({ path: '/review', query: { session: String(row.live_session_id) } });
}

/** 跳直播控制台回看：事件流按这场预过滤 */
function goReplay(row: LiveSessionItem): void {
  void router.push({ path: '/live', query: { session: String(row.live_session_id) } });
}

// -------------------- 备注编辑 --------------------

const notesVisible = ref(false);
const notesTarget = ref<LiveSessionItem | null>(null);
const notesDraft = ref('');

function openNotes(row: LiveSessionItem): void {
  notesTarget.value = row;
  notesDraft.value = row.notes ?? '';
  notesVisible.value = true;
}

async function submitNotes(): Promise<void> {
  const target = notesTarget.value;
  if (!target) return;
  await saveNotes(target, notesDraft.value);
  notesVisible.value = false;
}

// -------------------- 行内菜单 --------------------

async function onRowCommand(command: string, row: LiveSessionItem): Promise<void> {
  if (command === 'rename') await renameSession(row);
  else if (command === 'notes') openNotes(row);
  else if (command === 'kind') await toggleSessionKind(row);
  else if (command === 'resettle') await resettle(row);
  else if (command === 'reviewed') await markReviewed(row);
  else if (command === 'delete') await removeSession(row);
}

// -------------------- 按开播日的场次数 --------------------

onMounted(() => {
  applyQueryFromUrl();
  void load();
});
</script>

<style scoped>
.sessions-page {
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

.scope-note {
  margin-left: var(--spacing-sm);
  font-size: 11px;
  color: var(--text-placeholder);
}

.header-actions {
  display: flex;
  align-items: center;
  gap: var(--spacing-md);
}

/* 筛选条 */
.toolbar {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  flex-wrap: wrap;
}

.search-input {
  width: 220px;
}

.filter-select {
  width: 128px;
}

.date-range {
  width: 240px;
}

/* 下拉框常驻前缀（来源 / 类型 / 整理）：不选值也知道自己筛的是什么 */
.filter-cap {
  font-size: 11px;
  color: var(--text-placeholder);
}

.count-hint {
  font-size: 12px;
  color: var(--text-secondary);
}

/* 批量条 */
.batch-bar {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  padding: 8px 12px;
  border: 1px solid var(--color-primary);
  border-radius: var(--radius-md);
  background: var(--bg-active);
}

.batch-count {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-primary);
}

/* 汇总 */
.summary-card {
  display: flex;
  align-items: center;
  gap: var(--spacing-lg);
  padding: 10px 14px;
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-lg);
  background: var(--bg-card);
}

.summary-stats {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: var(--spacing-md);
  flex: 1;
  min-width: 0;
}

.stat {
  font-size: 12px;
  color: var(--text-secondary);
  white-space: nowrap;
}

.stat b {
  margin: 0 2px;
  font-size: 14px;
  color: var(--text-primary);
}

.source-line {
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
}

.source-chip {
  padding: 1px 7px;
  border-radius: var(--radius-sm);
  background: var(--bg-hover);
  font-size: 11px;
  color: var(--text-regular);
}

/* 场次日历热力图（GitHub 贡献图那种：一列一周、一格一天） */
.summary-heat {
  flex-shrink: 0;
  width: 424px;
}

.heat-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 6px;
}

.heat-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-primary);
}

.heat-note {
  font-size: 11px;
  color: var(--text-placeholder);
}

.heat-body {
  display: flex;
  align-items: flex-start;
  gap: 4px;
}

/* 左边星期标：一三五三行（与格子的行位对齐） */
.heat-weekdays {
  display: grid;
  grid-template-rows: repeat(7, 11px);
  gap: 3px;
  padding-top: 14px;
  flex-shrink: 0;
}

.heat-weekdays span {
  font-size: 9px;
  line-height: 11px;
  color: var(--text-placeholder);
  text-align: right;
  width: 10px;
}

.heat-scroll {
  overflow-x: auto;
  overflow-y: hidden;
  padding-bottom: 2px;
}

.heat-grid {
  display: flex;
  align-items: flex-start;
  gap: 3px;
}

/* 一列一周：顶上月份标签，下面周一到周日七格 */
/* 一列一周：顶上月份标签，下面周一到周日七格。
   列宽固定 11px，月份标签允许溢出到右边的空标签位（GitHub 的做法） */
.heat-week {
  display: flex;
  flex-direction: column;
  gap: 3px;
  flex: 0 0 11px;
  min-width: 0;
}

.heat-month {
  height: 14px;
  line-height: 14px;
  font-size: 9px;
  color: var(--text-placeholder);
  white-space: nowrap;
  overflow: visible;
}

.heat-cell {
  width: 11px;
  height: 11px;
  border-radius: 2px;
  background: var(--bg-hover);
}

.heat-cell.lv1 {
  background: var(--color-primary);
  opacity: 0.38;
}

.heat-cell.lv2 {
  background: var(--color-primary);
  opacity: 0.62;
}

.heat-cell.lv3 {
  background: var(--color-primary);
  opacity: 0.85;
}

.heat-cell.lv4 {
  background: var(--color-primary);
}

.heat-cell.is-blank {
  background: transparent;
}

.heat-legend {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 3px;
  margin-top: 6px;
  font-size: 10px;
  color: var(--text-placeholder);
}

.heat-legend .heat-cell {
  width: 9px;
  height: 9px;
}

/* 表格 */
.table-card {
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-lg);
  background: var(--bg-card);
  overflow: hidden;
}

.cell-name {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 备注与话题都是长文本：单行截断，完整内容在悬停提示里 */
.cell-notes,
.cell-topic {
  font-size: 11px;
  color: var(--text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 备注可点开就着改 */
.cell-notes.is-editable {
  cursor: pointer;
  border-bottom: 1px dashed var(--border-color-dark);
}

.cell-notes.is-editable:hover {
  color: var(--color-primary);
  border-bottom-color: var(--color-primary);
}

.cell-empty {
  color: var(--text-placeholder);
}

/* 时刻与时长不折行（列宽已按内容留够） */
.nowrap {
  white-space: nowrap;
}

.badge {
  padding: 0 6px;
  border-radius: var(--radius-sm);
  font-size: 11px;
  font-weight: 700;
  border: 1px solid var(--border-color-light);
  background: var(--bg-card);
  color: var(--text-secondary);
}

.badge.is-live {
  color: var(--color-primary);
  border-color: var(--color-primary);
}

.badge.is-rehearsal {
  color: var(--text-placeholder);
  border-style: dashed;
}

.badge.is-replay {
  color: var(--color-rundown);
  border-color: var(--color-rundown);
}

.is-running {
  color: var(--color-danger);
  font-weight: 700;
}

.settle-chip {
  padding: 1px 8px;
  white-space: nowrap;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
  background: var(--bg-hover);
  color: var(--text-secondary);
}

.settle-chip.is-warning {
  background: var(--color-warning-bg);
  color: var(--color-warning);
}

.settle-chip.is-danger {
  background: var(--color-danger-bg);
  color: var(--color-danger);
}

.settle-chip.is-success {
  background: var(--color-agent-bg);
  color: var(--color-agent);
}

.empty-hint {
  padding: 24px;
  text-align: center;
  font-size: 12px;
  color: var(--text-placeholder);
}

.pager {
  padding: 8px 12px;
  display: flex;
  justify-content: flex-end;
}

.dialog-hint {
  margin: 0 0 8px;
  font-size: 12px;
  color: var(--text-secondary);
}
</style>
