<script setup lang="ts">
/**
 * 直播切片页：左列场次（有录播匹配的），右侧单场的录播文件、AI 建议段、
 * 预览播放器与段列表编辑、发起 ffmpeg 切割。
 *
 * 工作流排布：段列表 + 预览播放器同屏（主工作区），时间轴在其下（微调/建段），
 * 录播素材收成底部聚合条。段的时间在界面上统一用"自开播起"的相对时刻。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { ElMessage } from 'element-plus';
import { confirmAction } from '@/utils/confirmAction';
import { formatDateTimeCn, formatTimeHM } from '@/utils/format';
import { formatLocalDateTime, formatRelClock, parseLocalDateTime } from '@/utils/clipsTime';
import { useClips } from '@/composables/useClips';
import { useClipReview } from '@/composables/useClipReview';
import ClipPreviewPlayer from '@/components/clips/ClipPreviewPlayer.vue';
import SegmentTimeline from '@/components/clips/SegmentTimeline.vue';
import type {
  ClipConfig,
  ClippedSessionItem,
  ClipSegment,
  ClipSegmentStatus,
  RecordingFileInfo,
  UnmatchedFileInfo,
} from '@/types/clips';

const {
  sessions,
  sessionsLoading,
  configState,
  configSaving,
  recordings,
  detachedFiles,
  unmatchedFiles,
  segments,
  sessionDensity,
  detailLoading,
  selecting,
  cutting,
  preview,
  previewConverting,
  previewReadiness,
  refreshPreviewReadiness,
  openPreview,
  closePreview,
  preparePreviews,
  assignRecording,
  detachRecording,
  restoreRecording,
  readCreationTime,
  renameRecording,
  loadSessions,
  loadConfig,
  saveConfig,
  openSession,
  runSelection,
  createSegment,
  updateSegment,
  deleteSegment,
  cutSegments,
  revealOutput,
} = useClips();

const selectedSessionId = ref<number | null>(null);

// ---- 页内配置卡：草稿独立于读面，保存后端免重启生效 ----
const settingsVisible = ref(false);
const configDraft = ref<ClipConfig | null>(null);

/** 未装配 / 未启用 / 未配录播目录 → 引导配置（自动展开设置卡） */
const needsSetup = computed(() => {
  const state = configState.value;
  if (!state) return false;
  return !state.assembled || !state.config.enabled || !state.config.recording_dir.trim();
});

/**
 * 目录不存在警告只针对"已保存的值"：草稿与已保存的录播目录一致且后端确认
 * 该目录不存在时才提示。输入中拿旧保存值告警会冤枉正在输入的正确路径。
 */
const savedDirMissing = computed(() => {
  const state = configState.value;
  const draft = configDraft.value;
  if (!state || !draft) return false;
  return (
    !state.recording_dir_exists &&
    draft.recording_dir.trim() !== '' &&
    draft.recording_dir.trim() === state.config.recording_dir.trim()
  );
});

watch(configState, state => {
  if (state && configDraft.value === null) {
    configDraft.value = { ...state.config };
  }
  if (state && (!state.assembled || !state.config.enabled || !state.config.recording_dir.trim())) {
    settingsVisible.value = true;
  }
});

async function onSaveSettings(): Promise<void> {
  if (!configDraft.value) return;
  if (configDraft.value.enabled && !configDraft.value.recording_dir.trim()) {
    ElMessage.warning('要先填录播目录才能启用');
    return;
  }
  const ok = await saveConfig(configDraft.value);
  if (ok) {
    settingsVisible.value = false;
    await loadSessions(); // 重装配后场次列表立即可用
  }
}

const selectedSession = computed(
  () => sessions.value.find(item => item.live_session_id === selectedSessionId.value) ?? null,
);

/** 场次终点：进行中场次用当前时刻兜底（时间轴照常可划） */
const sessionEndMs = computed(() => {
  if (!selectedSession.value) return 0;
  return selectedSession.value.ended_at_ms ?? Date.now();
});

// ---- 核对工作流（选中/草稿/单行替换/自动前进/入出点标记）收拢在 useClipReview ----
const {
  selectedSegmentId,
  selectedSegment,
  isReviewable,
  draftOf,
  isDirty,
  saveRow,
  onResize,
  onCreate,
  onConfirmSegment,
  onDismissSegment,
  onDeleteSegment,
  markSegmentStart,
  markSegmentEnd,
  selectSegment,
  resetForSession,
} = useClipReview({
  segments,
  session: computed(() => selectedSession.value),
  sessionEndMs,
  createSegment,
  updateSegment,
  deleteSegment,
  previewSegment: segment => onPreviewSegment(segment),
});

// ---- 预览播放器：视频交互收拢在 ClipPreviewPlayer，页面只接播放头与就绪 ----
const playerRef = ref<InstanceType<typeof ClipPreviewPlayer> | null>(null);
/** 播放头位置（墙钟毫秒；整场/段级现转/产物都有映射） */
const playheadMs = ref<number | null>(null);

function onPlayhead(wallMs: number): void {
  playheadMs.value = wallMs;
}

/** 元数据可读 = 片段/整场已可播（段级现转在此收尾） */
function onPreviewReady(): void {
  previewConverting.value = false;
}

// 预览关闭后时间轴上的播放头一并撤掉
watch(preview, p => {
  if (!p) playheadMs.value = null;
});

/** 核对键盘：Enter 确认当前段、X 弃用（播放键与入出点在播放器组件内监听） */
function interactiveTarget(event: KeyboardEvent): boolean {
  const target = event.target as HTMLElement | null;
  if (target === null) return false;
  return target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT', 'BUTTON'].includes(target.tagName);
}

function onReviewKeydown(event: KeyboardEvent): void {
  if (interactiveTarget(event)) return;
  const seg = selectedSegment.value;
  if (!seg || !isReviewable(seg)) return;
  if (event.key === 'Enter') {
    event.preventDefault();
    void onConfirmSegment(seg);
  } else if (event.key === 'x' || event.key === 'X') {
    event.preventDefault();
    void onDismissSegment(seg);
  }
}

const STATUS_LABELS: Record<ClipSegmentStatus, string> = {
  suggested: 'AI 建议',
  confirmed: '待切',
  cutting: '切割中',
  done: '已出片',
  failed: '失败',
  dismissed: '已弃用',
};

const STATUS_TAG_TYPES: Record<
  ClipSegmentStatus,
  'primary' | 'warning' | 'info' | 'success' | 'danger'
> = {
  suggested: 'primary',
  confirmed: 'warning',
  cutting: 'info',
  done: 'success',
  failed: 'danger',
  dismissed: 'info',
};

/** 待切段 = 非 dismissed 且未在切/已出的段（"生成切片"的默认对象） */
const cuttableSegments = computed(() =>
  segments.value.filter(s => s.status === 'suggested' || s.status === 'confirmed'),
);

const doneCount = computed(() => segments.value.filter(s => s.status === 'done').length);
const failedCount = computed(() => segments.value.filter(s => s.status === 'failed').length);
const dismissedCount = computed(() => segments.value.filter(s => s.status === 'dismissed').length);

// ---- 段列表过滤：打开场次默认聚焦待核对（核对是第一任务），计数挂在页签上 ----
type SegmentFilter = 'reviewable' | 'all' | 'done' | 'failed' | 'dismissed';
const segmentFilter = ref<SegmentFilter>('reviewable');

const filteredSegments = computed<ClipSegment[]>(() => {
  switch (segmentFilter.value) {
    case 'reviewable':
      return segments.value.filter(s => isReviewable(s));
    case 'all':
      return segments.value;
    case 'done':
      return segments.value.filter(s => s.status === 'done');
    case 'failed':
      return segments.value.filter(s => s.status === 'failed');
    case 'dismissed':
      return segments.value.filter(s => s.status === 'dismissed');
  }
});

/** 素材折叠条标题里的就绪数（mp4 视为恒就绪） */
const readyCount = computed(
  () => recordings.value.filter(f => previewReadiness.value[f.path]).length,
);

async function openSessionSegments(): Promise<void> {
  if (selectedSessionId.value !== null) {
    resetForSession();
    await openSession(selectedSessionId.value);
  }
}

async function onSelectSession(sessionId: number): Promise<void> {
  selectedSessionId.value = sessionId;
  resetForSession();
  await openSession(sessionId);
}

async function onRunSelection(): Promise<void> {
  if (!selectedSession.value) return;
  const accepted = await runSelection(selectedSession.value.live_session_id);
  if (accepted) ElMessage.success('AI 选段已受理，完成后段列表自动刷新');
}

async function onCut(segmentIds: number[]): Promise<void> {
  if (!selectedSession.value || segmentIds.length === 0) return;
  const ok = await cutSegments(selectedSession.value.live_session_id, segmentIds);
  if (ok) {
    // 切割是后台任务；状态由下次进入页面/手动刷新/任务终态回读看到
    await openSessionSegments();
  }
}

async function onCutAll(): Promise<void> {
  if (!selectedSession.value) return;
  const targets = cuttableSegments.value;
  if (targets.length === 0) {
    ElMessage.info('没有待切的段');
    return;
  }
  const confirmed = await confirmAction(
    `把 ${targets.length} 段交给 ffmpeg 切割？流复制模式下每段几秒内完成`,
    '发起切割',
  );
  if (confirmed) await onCut(targets.map(s => s.id));
}

async function onCutOne(segment: ClipSegment): Promise<void> {
  await onCut([segment.id]);
}

/** 段卡片"更多"菜单：看产物 / 删除（删除走 confirmAction 二次确认） */
function onSegmentMoreCommand(segment: ClipSegment, command: string): void {
  if (command === 'output') onPreviewOutput(segment);
  else if (command === 'delete') void onDeleteSegment(segment);
}

/** 列表主行：`11-07 周五 20:30` 形态（同自然年内不带年份，安静） */
function sessionListDate(ms: number): string {
  const date = new Date(ms);
  const pad = (n: number): string => String(n).padStart(2, '0');
  const week = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][date.getDay()];
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${week} ${formatTimeHM(ms)}`;
}

/** 列表副行：自定义标题（默认命名含日期，与主行重复时不重复）+ 待切/已出/文件数；
 *  零匹配场次标出来（这种场次照样可选——未识别文件可从它的素材折叠条人工指派） */
function sessionListSub(item: ClippedSessionItem): string {
  const date = new Date(item.started_at_ms);
  const pad = (n: number): string => String(n).padStart(2, '0');
  const yearDate = `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  const customTitle = item.title && !item.title.includes(yearDate) ? item.title : '';
  return [
    customTitle,
    item.file_count === 0 ? '无匹配录播文件' : '',
    item.pending_count > 0 ? `${item.pending_count} 待切` : '',
    item.done_count > 0 ? `${item.done_count} 已出` : '',
    item.file_count > 1 ? `${item.file_count} 个文件` : '',
  ]
    .filter(Boolean)
    .join(' · ');
}

function outputsOf(segment: ClipSegment): string[] {
  return segment.output_path ? segment.output_path.split('\n').filter(Boolean) : [];
}

// ---- 视频预览入口与联动（视频交互本体在 ClipPreviewPlayer）----

/** 打开一场后：预热整场预览缓存 + 刷新各文件的就绪态 */
watch(recordings, list => {
  const paths = list.map(f => f.path);
  void preparePreviews(
    list.filter(f => !f.file_name.toLowerCase().endsWith('.mp4')).map(f => f.path),
  );
  void refreshPreviewReadiness(paths);
});

function fileExtOf(file: RecordingFileInfo): string {
  const match = /\.([a-z0-9]+)$/i.exec(file.file_name);
  return (match?.[1] ?? '').toUpperCase();
}

function onPreviewFile(file: RecordingFileInfo): void {
  void openPreview({
    sourcePath: file.path,
    title: `整段素材：${file.file_name}`,
    seekWallMs: file.started_at_ms,
    durationMs: null,
  });
}

/** 覆盖某墙钟时刻的录播文件（多文件场按起点选对文件；取不到回落第一份） */
function pickFileForWallMs(wallMs: number): RecordingFileInfo | undefined {
  const covering = recordings.value
    .filter(f => f.started_at_ms <= wallMs)
    .sort((a, b) => b.started_at_ms - a.started_at_ms);
  return covering[0] ?? recordings.value[0];
}

/** 核对预览：整场就绪自由浏览全场；未就绪现转段窗口（前后各留 5 秒上下文） */
const REVIEW_CONTEXT_MS = 5000;

function onPreviewSegment(segment: ClipSegment): void {
  const file = pickFileForWallMs(segment.start_ms);
  if (!file) {
    ElMessage.info('该场没有匹配到录播文件，无法预览');
    return;
  }
  void openPreview({
    sourcePath: file.path,
    title: `源片段：${segment.title || '未命名段'}`,
    seekWallMs: segment.start_ms,
    durationMs: segment.end_ms - segment.start_ms,
    contextMs: REVIEW_CONTEXT_MS,
  });
}

function onPreviewOutput(segment: ClipSegment): void {
  const output = outputsOf(segment)[0];
  if (!output) return;
  void openPreview({
    sourcePath: output,
    title: `产物：${segment.title || '未命名段'}`,
    seekWallMs: segment.start_ms,
    durationMs: segment.end_ms - segment.start_ms,
    wallStartMs: segment.start_ms,
  });
}

/** 时间轴跳转：整场直服中直接改播放位置；否则秒开跳转点附近 ±30 秒的现转窗口 */
const SEEK_WINDOW_MS = 30000;

function seekPreviewTo(ms: number): void {
  const file = pickFileForWallMs(ms);
  const current = preview.value;
  if (file && current?.wholeFile && current.sourcePath === file.path && playerRef.value) {
    // 同一整场已在播：不重建播放器，直接改播放位置
    playerRef.value.seekToWall(ms);
    return;
  }
  if (!file) {
    ElMessage.info('该场没有匹配到录播文件，无法跳转');
    return;
  }
  void openPreview({
    sourcePath: file.path,
    title: `跳转定位：${file.file_name}`,
    seekWallMs: ms,
    durationMs: 0,
    contextMs: SEEK_WINDOW_MS,
  });
}

// ---- 段列表滚动定位：时间轴点段块时把对应行滚进可视区 ----
const segmentRowEls = new Map<number, HTMLElement>();

function setSegmentRowEl(segmentId: number, el: unknown): void {
  if (el instanceof HTMLElement) segmentRowEls.set(segmentId, el);
  else segmentRowEls.delete(segmentId);
}

watch(selectedSegmentId, segmentId => {
  if (segmentId !== null) segmentRowEls.get(segmentId)?.scrollIntoView({ block: 'nearest' });
});

// ---- 归属人工修正：移出 / 恢复 / 指派（指派需补录制开始时刻）----
async function onDetachFile(file: RecordingFileInfo): Promise<void> {
  if (!selectedSession.value) return;
  const ok = await confirmAction(`把「${file.file_name}」移出本场？自动匹配会被人工覆盖。`, '移出');
  if (!ok) return;
  await detachRecording(selectedSession.value.live_session_id, file.path);
}

async function onRestoreFile(file: RecordingFileInfo): Promise<void> {
  if (!selectedSession.value) return;
  await restoreRecording(selectedSession.value.live_session_id, file.path);
}

const assignDialogVisible = ref(false);
const assignTarget = ref<UnmatchedFileInfo | null>(null);
const assignStartText = ref('');
const readingMeta = ref(false);

function onAssignFile(file: UnmatchedFileInfo): void {
  if (!selectedSession.value) return;
  assignTarget.value = file;
  assignStartText.value = formatLocalDateTime(selectedSession.value.started_at_ms); // 默认预填开播时刻
  assignDialogVisible.value = true;
}

/** 从视频容器元数据（ffprobe creation_time）读录制时刻填入输入框 */
async function onReadMetadata(): Promise<void> {
  if (!assignTarget.value) return;
  readingMeta.value = true;
  try {
    const moment = await readCreationTime(assignTarget.value.path);
    if (moment === null) {
      ElMessage.info('视频元数据里没有录制时刻（该容器未写入 creation_time）');
      return;
    }
    assignStartText.value = formatLocalDateTime(moment);
  } finally {
    readingMeta.value = false;
  }
}

async function onConfirmAssign(): Promise<void> {
  if (!selectedSession.value || !assignTarget.value) return;
  const startMs = parseLocalDateTime(assignStartText.value);
  if (startMs === null) {
    ElMessage.warning('开始时刻格式不认识；用 YYYY-MM-DD HH:mm:ss');
    return;
  }
  const ok = await assignRecording(
    selectedSession.value.live_session_id,
    assignTarget.value.path,
    startMs,
  );
  if (ok) assignDialogVisible.value = false;
}

// ---- WebUI 改名（同目录、扩展名不可改；归属覆盖随新路径迁移）----
const renameDialogVisible = ref(false);
const renameTarget = ref<{ path: string; file_name: string } | null>(null);
const renameNewName = ref('');

function onRenameFile(file: { path: string; file_name: string }): void {
  if (!selectedSession.value) return;
  renameTarget.value = file;
  renameNewName.value = file.file_name;
  renameDialogVisible.value = true;
}

async function onConfirmRename(): Promise<void> {
  if (!selectedSession.value || !renameTarget.value) return;
  const newName = renameNewName.value.trim();
  if (!newName || newName === renameTarget.value.file_name) {
    renameDialogVisible.value = false;
    return;
  }
  const ok = await renameRecording(renameTarget.value.path, newName);
  if (ok) renameDialogVisible.value = false;
}

/** 场次列表刷新：未装配时接口是 503，静默清空不打扰（配置卡才是主入口） */
async function refreshSessions(): Promise<void> {
  if (!configState.value?.assembled) {
    sessions.value = [];
    return;
  }
  await loadSessions();
}

onMounted(async () => {
  window.addEventListener('keydown', onReviewKeydown);
  const state = await loadConfig();
  // 服务未装配时场次列表接口是 503，先看配置再决定拉不拉列表
  if (state?.assembled) await loadSessions();
});

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onReviewKeydown);
});
</script>

<template>
  <div class="clips-shell">
    <div class="clips-page">
      <aside class="md-list-panel" aria-label="场次列表">
        <header class="md-list-header">
          <div class="md-list-header-main">
            <h2 class="md-list-title">直播切片</h2>
            <span class="md-list-count">
              <span class="md-list-count-total">共 {{ sessions.length }} 场</span>
            </span>
          </div>
          <div class="md-batch-actions">
            <el-button
              size="small"
              :loading="sessionsLoading"
              :disabled="!configState?.assembled"
              @click="refreshSessions"
              >刷新</el-button
            >
          </div>
        </header>
        <div v-loading="sessionsLoading" class="md-list">
          <button
            v-for="item in sessions"
            :key="item.live_session_id"
            class="md-row clip-session-row"
            :class="{ 'is-selected': item.live_session_id === selectedSessionId }"
            type="button"
            @click="onSelectSession(item.live_session_id)"
          >
            <span class="clip-session-main">
              <span class="clip-session-date">{{ sessionListDate(item.started_at_ms) }}</span>
              <span class="clip-session-sub">{{ sessionListSub(item) }}</span>
            </span>
          </button>
          <div v-if="!sessionsLoading && sessions.length === 0" class="md-list-empty">
            还没有任何直播场次记录。文件名识别不了的录制文件（如自定义命名的
            OBS 录像）会自动读视频元数据里的录制时刻归场；读不到的在选中场次
            下方的「录播素材」里可人工指派。
          </div>
        </div>
      </aside>

      <section class="md-detail-panel">
        <header class="md-detail-header">
          <div class="md-detail-title-block">
            <div class="md-detail-title-row">
              <h2 class="md-detail-name">{{ selectedSession?.title || '直播切片' }}</h2>
              <span v-if="selectedSession" class="md-type-chip"
                >#{{ selectedSession.live_session_id }}</span
              >
            </div>
            <p class="md-detail-description">
              <template v-if="selectedSession">
                {{ formatDateTimeCn(selectedSession.started_at_ms) }} 起播 · 共
                {{ recordings.length }} 个录播文件 · {{ segments.length }} 个切片段
                <span class="clip-meta-chip is-cut">{{ cuttableSegments.length }} 待切</span>
                <span v-if="doneCount" class="clip-meta-chip is-done">{{ doneCount }} 已出片</span>
                <span v-if="failedCount" class="clip-meta-chip is-failed"
                  >{{ failedCount }} 失败</span
                >
                <span v-if="dismissedCount" class="clip-meta-chip"
                  >{{ dismissedCount }} 已弃用</span
                >
              </template>
              <template v-else
                >从左侧选一场开始；录播目录与 ffmpeg 在「设置」里配置，保存即生效</template
              >
            </p>
          </div>
          <div class="md-detail-actions">
            <el-button
              :type="needsSetup ? 'warning' : 'default'"
              :plain="!needsSetup"
              @click="settingsVisible = !settingsVisible"
            >
              {{ needsSetup ? '先完成配置' : '设置' }}
            </el-button>
            <template v-if="configState?.assembled">
              <el-button type="primary" :loading="selecting" @click="onRunSelection">
                {{ segments.some(s => s.origin === 'ai') ? '重新 AI 选段' : 'AI 选段' }}
              </el-button>
              <el-button text :loading="detailLoading" @click="openSessionSegments">刷新</el-button>
              <el-button
                type="success"
                :loading="cutting"
                :disabled="cuttableSegments.length === 0"
                @click="onCutAll"
              >
                生成切片（{{ cuttableSegments.length }} 段）
              </el-button>
            </template>
          </div>
        </header>

        <div v-if="settingsVisible && configDraft" class="clip-section clip-settings-card">
          <h3 class="clip-section-title">
            切片设置<span class="clip-settings-hint">保存即生效，不用重启</span>
          </h3>
          <el-form label-width="110px" class="clip-settings-form" @submit.prevent>
            <el-form-item label="启用切片">
              <el-switch v-model="configDraft.enabled" />
            </el-form-item>
            <el-form-item label="录播目录">
              <el-input
                v-model="configDraft.recording_dir"
                placeholder="录播文件所在目录（录播姬、OBS 或其他录制软件均可）"
              />
              <div v-if="savedDirMissing" class="clip-dir-warning">
                该目录当前不存在，检查盘符与路径拼写
              </div>
            </el-form-item>
            <el-form-item label="ffmpeg 路径">
              <el-input
                v-model="configDraft.ffmpeg_path"
                placeholder="留空 = 使用 PATH 上的 ffmpeg"
              />
            </el-form-item>
            <el-form-item label="输出目录">
              <el-input
                v-model="configDraft.output_dir"
                placeholder="留空 = 录播目录下的 切片 子目录"
              />
            </el-form-item>
            <el-collapse class="clip-advanced">
              <el-collapse-item title="高级" name="advanced">
                <el-form-item label="LLM 档位">
                  <el-input
                    v-model="configDraft.llm_profile"
                    placeholder="AI 选段用的 model.toml 档位引用名"
                  />
                </el-form-item>
                <el-form-item label="重编码切片">
                  <el-switch v-model="configDraft.reencode" />
                  <span class="clip-settings-hint"
                    >开 = 边界精确到帧但接近实时速度；关 = 流复制秒级出片（边界可能差几秒）</span
                  >
                </el-form-item>
                <el-form-item label="片段时长(秒)">
                  <el-input-number
                    v-model="configDraft.min_segment_seconds"
                    :min="5"
                    :max="3600"
                    controls-position="right"
                  />
                  <span class="clip-time-sep">~</span>
                  <el-input-number
                    v-model="configDraft.max_segment_seconds"
                    :min="10"
                    :max="7200"
                    controls-position="right"
                  />
                </el-form-item>
              </el-collapse-item>
            </el-collapse>
            <el-form-item>
              <el-button type="primary" :loading="configSaving" @click="onSaveSettings"
                >保存并生效</el-button
              >
              <el-button @click="settingsVisible = false">收起</el-button>
            </el-form-item>
          </el-form>
        </div>

        <template v-if="selectedSession && configState?.assembled">
          <div class="clip-workspace">
            <section class="clip-card clip-player-col">
              <ClipPreviewPlayer
                ref="playerRef"
                :preview="preview"
                :preview-converting="previewConverting"
                :can-mark="!!selectedSegment && isReviewable(selectedSegment)"
                :session-start-ms="selectedSession?.started_at_ms ?? 0"
                @playhead="onPlayhead"
                @ready="onPreviewReady"
                @close="closePreview"
                @mark-start="wall => markSegmentStart(wall)"
                @mark-end="wall => markSegmentEnd(wall)"
              >
                <template #empty>
                  <template v-if="segments.length > 0">
                    <p class="clip-player-empty-stat">
                      本场 {{ formatRelClock(sessionEndMs - selectedSession.started_at_ms) }} · 待切
                      {{ cuttableSegments.length }} · 已出 {{ doneCount
                      }}<template v-if="failedCount"> · 失败 {{ failedCount }}</template>
                    </p>
                    <el-button
                      type="primary"
                      plain
                      size="small"
                      @click="onPreviewSegment(segments[0])"
                      >预览第一段</el-button
                    >
                  </template>
                  <p v-else class="clip-player-empty-stat">
                    还没有切片段，先点「AI 选段」或拖时间轴建段
                  </p>
                  <p class="clip-player-empty-hint">播放器固定在这里，调段不用来回滚</p>
                </template>
              </ClipPreviewPlayer>
            </section>

            <section class="clip-card clip-segments-col">
              <h3 class="clip-section-title">切片段（{{ segments.length }}）</h3>
              <div class="clip-filter-row" @click.stop>
                <el-radio-group v-model="segmentFilter" size="small">
                  <el-radio-button value="reviewable">待核对 {{ cuttableSegments.length }}</el-radio-button>
                  <el-radio-button value="all">全部 {{ segments.length }}</el-radio-button>
                  <el-radio-button value="done">已出 {{ doneCount }}</el-radio-button>
                  <el-radio-button v-if="failedCount" value="failed">失败 {{ failedCount }}</el-radio-button>
                  <el-radio-button v-if="dismissedCount" value="dismissed">
                    弃用 {{ dismissedCount }}
                  </el-radio-button>
                </el-radio-group>
              </div>
              <div class="clip-segments-scroll">
                <div v-if="segments.length === 0" class="clip-no-segments">
                  还没有切片段。点「AI
                  选段」让它读本场记录提议几段，或按住下方时间轴拖动框选手动建段。
                </div>
                <div v-else-if="filteredSegments.length === 0" class="clip-no-segments">
                  这个筛选下没有切片段。
                </div>
                <div
                  v-for="segment in filteredSegments"
                  :key="segment.id"
                  :ref="el => setSegmentRowEl(segment.id, el)"
                  class="clip-segment-row"
                  :class="{
                    'is-selected': segment.id === selectedSegmentId,
                    'is-dismissed': segment.status === 'dismissed',
                  }"
                  @click="selectSegment(segment.id)"
                >
                  <div class="clip-segment-main">
                    <div class="clip-segment-top">
                      <el-tag
                        size="small"
                        :type="STATUS_TAG_TYPES[segment.status]"
                        :class="{ 'is-cutting-tag': segment.status === 'cutting' }"
                      >
                        {{ STATUS_LABELS[segment.status] }}
                      </el-tag>
                      <el-tag v-if="segment.origin === 'ai'" size="small" effect="plain">AI</el-tag>
                      <el-input
                        v-model="draftOf(segment).title"
                        class="clip-title-input"
                        placeholder="片段标题"
                        size="small"
                        @click.stop
                      />
                    </div>
                    <div class="clip-segment-time" @click.stop>
                      <el-input
                        v-model="draftOf(segment).startText"
                        size="small"
                        class="clip-time-input"
                      />
                      <span class="clip-time-sep">~</span>
                      <el-input
                        v-model="draftOf(segment).endText"
                        size="small"
                        class="clip-time-input"
                      />
                      <el-button
                        v-if="isDirty(segment)"
                        size="small"
                        type="primary"
                        @click.stop="saveRow(segment)"
                      >
                        保存
                      </el-button>
                    </div>
                    <p v-if="segment.reason" class="clip-segment-reason">{{ segment.reason }}</p>
                    <p
                      v-if="segment.status === 'failed' && segment.error_message"
                      class="clip-segment-error"
                    >
                      {{ segment.error_message }}
                    </p>
                    <ul v-if="outputsOf(segment).length" class="clip-output-list">
                      <li v-for="path in outputsOf(segment)" :key="path" class="clip-output-item">
                        <span class="clip-output-path" :title="path">{{ path }}</span>
                        <el-button size="small" text type="primary" @click="revealOutput(path)"
                          >打开文件夹</el-button
                        >
                      </li>
                    </ul>
                  </div>
                  <div class="clip-segment-actions" @click.stop>
                    <el-button size="small" type="primary" plain @click="onPreviewSegment(segment)"
                      >预览</el-button
                    >
                    <el-button
                      v-if="segment.status === 'suggested'"
                      size="small"
                      @click="onConfirmSegment(segment)"
                    >
                      确认
                    </el-button>
                    <el-button
                      v-if="segment.status !== 'done' && segment.status !== 'cutting'"
                      size="small"
                      type="success"
                      plain
                      @click="onCutOne(segment)"
                    >
                      切这段
                    </el-button>
                    <el-button
                      v-if="segment.status !== 'dismissed'"
                      size="small"
                      text
                      @click="onDismissSegment(segment)"
                    >
                      弃用
                    </el-button>
                    <el-dropdown
                      class="clip-segment-more"
                      trigger="click"
                      @command="(command: string) => onSegmentMoreCommand(segment, command)"
                    >
                      <el-button size="small" text aria-label="更多操作">⋯</el-button>
                      <template #dropdown>
                        <el-dropdown-menu>
                          <el-dropdown-item v-if="outputsOf(segment).length" command="output"
                            >看产物</el-dropdown-item
                          >
                          <el-dropdown-item command="delete" class="clip-more-danger"
                            >删除</el-dropdown-item
                          >
                        </el-dropdown-menu>
                      </template>
                    </el-dropdown>
                  </div>
                </div>
              </div>
            </section>
          </div>

          <section v-loading="detailLoading" class="clip-card">
            <h3 class="clip-section-title">
              切段时间轴<span class="clip-settings-hint"
                >点击跳转播放 · 按住空白拖动建段 · 拖段边微调</span
              >
            </h3>
            <SegmentTimeline
              :segments="segments"
              :session-start-ms="selectedSession.started_at_ms"
              :session-end-ms="sessionEndMs"
              :selected-id="selectedSegmentId"
              :playhead-ms="playheadMs"
              :density="sessionDensity"
              @select="selectSegment($event)"
              @resize="onResize"
              @create="onCreate"
              @seek="seekPreviewTo"
            />
          </section>

          <el-collapse class="clip-files-collapse">
            <el-collapse-item name="files">
              <template #title>
                <span class="clip-files-summary">
                  录播素材 · {{ recordings.length }} 个文件<template v-if="readyCount">
                    · {{ readyCount }} 个预览就绪</template
                  ><template v-if="unmatchedFiles.length">
                    · {{ unmatchedFiles.length }} 个未识别</template
                  >
                </span>
              </template>
              <div class="clip-files-scroll">
                <ul class="clip-file-list">
                  <li v-for="file in recordings" :key="file.path" class="clip-file-row">
                    <div class="clip-file-info">
                      <span class="clip-file-name" :title="file.file_name">{{
                        file.file_name
                      }}</span>
                      <span class="clip-file-time">
                        自
                        {{ formatRelClock(file.started_at_ms - selectedSession.started_at_ms) }} 起
                        · {{ fileExtOf(file) }} ·
                        {{ previewReadiness[file.path] ? '预览就绪' : '首次预览现转该段' }}
                      </span>
                    </div>
                    <div class="clip-file-actions">
                      <el-button size="small" text type="primary" @click="onPreviewFile(file)"
                        >预览</el-button
                      >
                      <el-button size="small" text @click="onRenameFile(file)">改名</el-button>
                      <el-button size="small" text type="warning" @click="onDetachFile(file)"
                        >移出本场</el-button
                      >
                    </div>
                  </li>
                  <li
                    v-for="file in detachedFiles"
                    :key="`detached-${file.path}`"
                    class="clip-file-row is-detached"
                  >
                    <div class="clip-file-info">
                      <span class="clip-file-name" :title="file.file_name">{{
                        file.file_name
                      }}</span>
                      <span class="clip-file-time">已被移出本场（人工）</span>
                    </div>
                    <div class="clip-file-actions">
                      <el-button size="small" text @click="onRenameFile(file)">改名</el-button>
                      <el-button size="small" text @click="onRestoreFile(file)">恢复</el-button>
                    </div>
                  </li>
                  <li
                    v-for="file in unmatchedFiles"
                    :key="`unmatched-${file.path}`"
                    class="clip-file-row"
                  >
                    <div class="clip-file-info">
                      <span class="clip-file-name" :title="file.file_name">{{
                        file.file_name
                      }}</span>
                      <span class="clip-file-time">文件名解析不出时刻（手工改名归档？）</span>
                    </div>
                    <div class="clip-file-actions">
                      <el-button size="small" text @click="onRenameFile(file)">改名</el-button>
                      <el-button size="small" text type="primary" @click="onAssignFile(file)"
                        >指派到本场</el-button
                      >
                    </div>
                  </li>
                </ul>
                <p
                  v-if="
                    recordings.length === 0 &&
                    detachedFiles.length === 0 &&
                    unmatchedFiles.length === 0
                  "
                  class="clip-unmatched-note"
                >
                  录播目录里没有属于本场的文件。
                </p>
              </div>
            </el-collapse-item>
          </el-collapse>
        </template>
        <div v-else class="md-list-empty clip-empty-detail">
          <template v-if="needsSetup"
            >先把上面的切片设置填好保存（不用重启），场次列表就会出现。</template
          >
          <template v-else>从左侧选一场直播开始切。没匹配到录播文件的场次也能选，可在其素材列表里人工指派文件。</template>
        </div>
      </section>
    </div>

    <el-dialog v-model="assignDialogVisible" title="指派到本场" width="480px">
      <p class="clip-assign-path">{{ assignTarget?.file_name }}</p>
      <el-form label-width="110px" @submit.prevent>
        <el-form-item label="录制开始">
          <el-input v-model="assignStartText" placeholder="YYYY-MM-DD HH:mm:ss">
            <template #append>
              <el-button :loading="readingMeta" @click="onReadMetadata">读元数据</el-button>
            </template>
          </el-input>
          <div class="clip-dir-warning">
            文件名解析不出时刻，切片偏移按这个时刻换算；默认预填本场开播时刻
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="assignDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="onConfirmAssign">指派</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="renameDialogVisible" title="改名" width="480px">
      <el-form label-width="90px" @submit.prevent>
        <el-form-item label="新文件名">
          <el-input v-model="renameNewName" placeholder="含扩展名；扩展名不可改" />
          <div class="clip-dir-warning">
            改成 录制-房间号-日期-时刻-毫秒-标题.扩展名
            形态即可恢复自动匹配；改名后该文件的人工归属记录跟着迁移
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="renameDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="onConfirmRename">改名</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
@import '@/styles/component-master-detail.css';

/* 与 Agents 页同构：定高 shell + 左定宽列表 + 右自适应详情。
   选中态强调色在页根注入（md- 系列样式依赖这两个变量，Agents 页同法） */
.clips-shell {
  --md-accent: var(--el-color-primary);
  --md-accent-bg: var(--el-color-primary-light-9);

  height: calc(100vh - var(--header-height, 56px) - 2 * var(--spacing-lg, 16px));
  min-height: 640px;
}

.clips-page {
  display: grid;
  grid-template-columns: 264px minmax(0, 1fr);
  gap: 12px;
  height: 100%;
  padding: 12px;
}

/* 详情区纵向 flex：工作区（播放器+段列表）吃掉剩余高度，
   段列表内部滚动——核对切点时时间轴、播放器、列表同屏 */
.clip-workspace {
  flex: 1;
  min-height: 360px;
  display: grid;
  grid-template-columns: minmax(360px, 3fr) 2fr;
  gap: 12px;
}

.clip-player-col {
  display: flex;
  flex-direction: column;
  min-height: 0;
}

/* 空态文案是父组件插槽内容，样式留在父作用域；容器在 ClipPreviewPlayer（底色已换主题变量） */
.clip-player-empty-stat {
  margin: 0;
  color: var(--el-text-color-primary);
  font-variant-numeric: tabular-nums;
}

.clip-player-empty-hint {
  margin: 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.clip-segments-col {
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.clip-segments-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding-right: 4px;
}

@media (max-width: 1279px) {
  .clip-workspace {
    grid-template-columns: minmax(0, 1fr);
    /* 单列堆叠后不再给工作区定高：播放器给固定高度，列表放开内层滚动，
       让详情区外层统一滚动（双层滚动条打架） */
    flex: initial;
    min-height: 0;
  }

  .clip-player-col :deep(.clip-player) {
    height: 280px;
    flex: none;
  }

  .clip-player-col :deep(.clip-player-empty) {
    min-height: 220px;
    flex: none;
  }

  .clip-segments-scroll {
    overflow-y: visible;
  }
}

/* 页头 meta 行里的统计小片 */
.clip-meta-chip {
  display: inline-block;
  margin-left: 8px;
  padding: 1px 8px;
  border-radius: 10px;
  font-size: 12px;
  background: var(--el-fill-color);
  color: var(--el-text-color-secondary);
  font-variant-numeric: tabular-nums;
}

.clip-meta-chip.is-cut {
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
}

.clip-meta-chip.is-done {
  background: var(--el-color-success-light-9);
  color: var(--el-color-success);
}

.clip-meta-chip.is-failed {
  background: var(--el-color-danger-light-9);
  color: var(--el-color-danger);
}

.clip-session-row {
  display: block;
  width: 100%;
  border: none;
  background: transparent;
  box-shadow: none;
  font: inherit;
  text-align: left;
  padding: 9px 12px;
}

.clip-session-row.is-selected {
  background: var(--md-accent-bg);
  box-shadow: inset 3px 0 0 0 var(--md-accent);
}

.clip-session-main {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.clip-session-date {
  font-size: 13px;
  color: var(--text-primary);
  font-variant-numeric: tabular-nums;
}

.clip-session-row.is-selected .clip-session-date {
  font-weight: 600;
}

.clip-session-sub {
  font-size: 12px;
  color: var(--text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.clip-section {
  margin-bottom: 12px;
}

.clip-settings-card {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  padding: 12px 16px 4px;
}

.clip-settings-hint {
  margin-left: 10px;
  font-size: 12px;
  font-weight: normal;
  color: var(--el-text-color-secondary);
}

.clip-settings-form {
  max-width: 640px;
}

.clip-advanced {
  margin-bottom: 18px;
  border: none;
}

.clip-dir-warning {
  width: 100%;
  font-size: 12px;
  color: var(--el-color-danger);
  line-height: 1.6;
}

.clip-card {
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-lg, 8px);
  padding: 14px 18px;
}

.clip-file-list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.clip-file-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 6px 0;
}

.clip-file-row + .clip-file-row {
  border-top: 1px solid var(--border-color-light);
}

.clip-file-row.is-detached {
  opacity: 0.55;
}

.clip-file-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.clip-file-actions {
  flex: none;
  display: flex;
  align-items: center;
}

.clip-file-item {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 4px 0;
  font-size: 13px;
}

.clip-file-item.is-unmatched .clip-file-name {
  color: var(--el-text-color-secondary);
}

.clip-file-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.clip-file-time {
  flex: none;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.clip-files-collapse {
  border-top: none;
}

.clip-files-collapse :deep(.el-collapse-item__content) {
  padding: 4px 0 8px;
}

/* 折叠内容可滚动：未识别文件多时不撑破定高页面 */
.clip-files-scroll {
  max-height: 280px;
  overflow-y: auto;
  padding-left: 8px;
}

.clip-files-summary {
  font-size: 13px;
}

.clip-unmatched-note {
  margin: 8px 0 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--el-text-color-secondary);
}

.clip-no-segments {
  padding: 16px;
  font-size: 13px;
  color: var(--el-text-color-secondary);
  border: 1px dashed var(--el-border-color-lighter);
  border-radius: 6px;
}

.clip-segment-row {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  margin-bottom: 8px;
}

.clip-segment-row.is-selected {
  border-color: var(--el-color-primary-light-5);
  background: var(--el-color-primary-light-9);
}

/* 已弃用段视觉降级：留在"全部"里可查，但不与待核对内容抢注意力 */
.clip-segment-row.is-dismissed {
  opacity: 0.55;
}

.clip-filter-row {
  margin-bottom: 10px;
}

.clip-segment-main {
  flex: 1;
  min-width: 0;
}

.clip-segment-top {
  display: flex;
  align-items: center;
  gap: 8px;
}

.clip-title-input {
  max-width: 320px;
}

.clip-segment-time {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 8px;
}

.clip-time-input {
  width: 96px;
}

.clip-time-sep {
  color: var(--el-text-color-secondary);
}

.clip-segment-reason {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.clip-segment-error {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--el-color-danger);
}

.clip-output-list {
  list-style: none;
  margin: 6px 0 0;
  padding: 0;
}

.clip-output-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  font-family: monospace;
  min-width: 0;
}

.clip-output-path {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 切割中：tag 前一枚旋转小圈，进度感不打断阅读 */
.clip-tag-cutting::before {
  content: '';
  display: inline-block;
  width: 8px;
  height: 8px;
  margin-right: 4px;
  border: 1.5px solid currentColor;
  border-top-color: transparent;
  border-radius: 50%;
  animation: clip-spin 0.9s linear infinite;
}

@keyframes clip-spin {
  to {
    transform: rotate(360deg);
  }
}

@media (prefers-reduced-motion: reduce) {
  .clip-tag-cutting::before {
    animation: none;
  }
}

/* 操作行收进卡片底部：横排小按钮，"更多"菜单靠右 */
.clip-segment-actions {
  flex: none;
  display: flex;
  align-items: center;
  gap: 6px;
}

.clip-segment-actions .el-button + .el-button {
  margin-left: 0;
}

.clip-segment-more {
  margin-left: auto;
}

.clip-more-danger {
  color: var(--el-color-danger);
}

/* 标题与时间输入平时无边框（去框减噪），hover/focus 才显边框提示可编辑 */
.clip-title-input :deep(.el-input__wrapper),
.clip-time-input :deep(.el-input__wrapper) {
  box-shadow: none;
  background: transparent;
}

.clip-title-input:hover :deep(.el-input__wrapper),
.clip-time-input:hover :deep(.el-input__wrapper),
.clip-title-input :deep(.el-input__wrapper.is-focus),
.clip-time-input :deep(.el-input__wrapper.is-focus) {
  box-shadow: 0 0 0 1px var(--el-border-color) inset;
  background: var(--el-fill-color-blank);
}

.clip-assign-path {
  margin: 0 0 12px;
  font-size: 13px;
  color: var(--el-text-color-regular);
  word-break: break-all;
}

.clip-empty-detail {
  padding: 40px;
}
</style>
