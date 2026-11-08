/**
 * 切片页数据面组合式：场次列表、单场录播文件、切片段的加载与写操作。
 *
 * 订阅 background.task.changed（经共享 websocket store）做"任务终态自动回读"：
 * clip-select / clip-cut 任务结束时回读对应场次的段列表，状态不用手动刷新。
 * 预览是混合式：整场缓存就绪直接播整场；未就绪走段级端点秒开。仅
 * "整段素材预览且源未转换"时才有 2 秒间隔的整场就绪轮询。
 * 生命周期语义：页面在 keep-alive 内，订阅与轮询按激活态启停——onActivated
 * 订阅、onDeactivated 退订并停轮询、作用域销毁兜底；切割进行中切页则暂停
 * 回读，回页时段状态以下次进入/手动刷新为准。
 */
import { onActivated, onDeactivated, onScopeDispose, ref } from 'vue';
import { ElMessage } from 'element-plus';
import { clipsApi } from '@/api';
import { useWebSocketStore } from '@/stores';
import { getApiErrorMessage } from '@/utils/apiError';
import { EVENT } from '@/types/events';
import type { BackgroundTask } from '@/types/events';
import type { WebSocketMessage } from '@/types';
import type { ClipConfig, ClipConfigState, ClippedSessionItem, ClipSegment, RecordingFileInfo, UnmatchedFileInfo } from '@/types/clips';

// 本组合式负责的后台任务号前缀（任务号 clip-<动作>-<场次id>）
const CLIP_TASK_RE = /^clip-(?:select|cut)-(\d+)$/;

export function useClips() {
  const sessions = ref<ClippedSessionItem[]>([]);
  const sessionsLoading = ref(false);

  const configState = ref<ClipConfigState | null>(null);
  const configSaving = ref(false);

  const recordings = ref<RecordingFileInfo[]>([]);
  const detachedFiles = ref<RecordingFileInfo[]>([]);
  const unmatchedFiles = ref<UnmatchedFileInfo[]>([]);

  const segments = ref<ClipSegment[]>([]);
  const detailLoading = ref(false);

  const selecting = ref(false);
  const cutting = ref(false);

  // 当前打开的场次 + 打开序号：快速切换场次时丢弃过期响应（慢响应不得覆盖新场次）
  let openSeq = 0;
  const activeSessionId = ref<number | null>(null);

  async function loadSessions(): Promise<void> {
    sessionsLoading.value = true;
    try {
      const { data } = await clipsApi.listSessions();
      sessions.value = data.sessions;
    } catch (error) {
      ElMessage.error(getApiErrorMessage(error, '加载场次列表失败'));
    } finally {
      sessionsLoading.value = false;
    }
  }

  /** 读切片配置（服务未装配时后端也返回当前配置，装配态用 assembled 区分） */
  async function loadConfig(): Promise<ClipConfigState | null> {
    try {
      const { data } = await clipsApi.getConfig();
      configState.value = data;
      return data;
    } catch (error) {
      ElMessage.error(getApiErrorMessage(error, '加载切片配置失败'));
      return null;
    }
  }

  /** 保存切片配置（后端写盘 + 免重启重装配），返回是否成功 */
  async function saveConfig(config: ClipConfig): Promise<boolean> {
    configSaving.value = true;
    try {
      const { data } = await clipsApi.saveConfig(config);
      if (data.success) {
        ElMessage.success(data.message || '切片配置已保存');
        await loadConfig();
        return true;
      }
      ElMessage.error(data.message || '保存失败');
      return false;
    } catch (error) {
      ElMessage.error(getApiErrorMessage(error, '保存切片配置失败'));
      return false;
    } finally {
      configSaving.value = false;
    }
  }

  /** 打开一场：录播文件与切片段一起取（段为空是正常态，不弹错误） */
  async function openSession(sessionId: number): Promise<void> {
    const seq = ++openSeq;
    activeSessionId.value = sessionId;
    detailLoading.value = true;
    recordings.value = [];
    detachedFiles.value = [];
    unmatchedFiles.value = [];
    segments.value = [];
    try {
      const [recordingRes, segmentRes] = await Promise.all([
        clipsApi.listRecordings(sessionId),
        clipsApi.listSegments(sessionId),
      ]);
      if (seq !== openSeq) return; // 期间已切到别的场次：丢弃过期响应
      recordings.value = recordingRes.data.files;
      detachedFiles.value = recordingRes.data.detached_files;
      unmatchedFiles.value = recordingRes.data.unmatched_files;
      segments.value = segmentRes.data.segments;
    } catch (error) {
      if (seq !== openSeq) return;
      ElMessage.error(getApiErrorMessage(error, '加载场次切片数据失败'));
    } finally {
      if (seq === openSeq) detailLoading.value = false;
    }
  }

  /** 重新加载单场段列表（写操作后回读；已切到别的场次时静默跳过，防串场） */
  async function reloadSegments(sessionId: number): Promise<void> {
    if (sessionId !== activeSessionId.value) return;
    try {
      const { data } = await clipsApi.listSegments(sessionId);
      if (sessionId !== activeSessionId.value) return;
      segments.value = data.segments;
    } catch (error) {
      ElMessage.error(getApiErrorMessage(error, '刷新切片段失败'));
    }
  }

  /** 触发/重跑 AI 选段；完成后回读段列表并返回提议数 */
  async function runSelection(sessionId: number): Promise<number | null> {
    selecting.value = true;
    try {
      const { data } = await clipsApi.select(sessionId);
      await reloadSegments(sessionId);
      return data.suggestion_count;
    } catch (error) {
      ElMessage.error(getApiErrorMessage(error, 'AI 选段失败'));
      return null;
    } finally {
      selecting.value = false;
    }
  }

  /** 手建一段；返回新段（调用方滚动定位），失败返回 null */
  async function createSegment(
    sessionId: number,
    body: { start_ms: number; end_ms: number; title: string },
  ): Promise<ClipSegment | null> {
    try {
      const { data } = await clipsApi.createSegment(sessionId, body);
      await reloadSegments(sessionId);
      return data.segment;
    } catch (error) {
      ElMessage.error(getApiErrorMessage(error, '新建切片段失败'));
      return null;
    }
  }

  /** 手调一段（起止/标题/状态） */
  async function updateSegment(
    segmentId: number,
    body: Partial<{ start_ms: number; end_ms: number; title: string; status: string }>,
  ): Promise<boolean> {
    try {
      await clipsApi.updateSegment(segmentId, body);
      return true;
    } catch (error) {
      ElMessage.error(getApiErrorMessage(error, '保存切片段失败'));
      return false;
    }
  }

  async function deleteSegment(segmentId: number): Promise<boolean> {
    try {
      await clipsApi.deleteSegment(segmentId);
      return true;
    } catch (error) {
      ElMessage.error(getApiErrorMessage(error, '删除切片段失败'));
      return false;
    }
  }

  /** 发起切割（后台任务执行）；成功后回读段列表（状态变 cutting） */
  async function cutSegments(sessionId: number, segmentIds: number[]): Promise<boolean> {
    cutting.value = true;
    try {
      const { data } = await clipsApi.cut(sessionId, segmentIds);
      ElMessage.success(`已受理 ${data.accepted} 段的切割任务，进度见底部任务条`);
      await reloadSegments(sessionId);
      return true;
    } catch (error) {
      ElMessage.error(getApiErrorMessage(error, '发起切割失败'));
      return false;
    } finally {
      cutting.value = false;
    }
  }

  // ---- 预览（混合式）：视频本体由 <video> 直连不走 axios 免超时 ----
  // 整场缓存就绪 → 直接用整场文件（可自由拖动，从段起点开播）；
  // 未就绪 → 走段级端点（后端只重封装那几分钟，秒级可播），不等整场转换。
  // durationS 为 null 表示"整段素材从头播、不自动停"，此时整场未就绪才退回
  // "等整场转换"的轮询流程。
  const preview = ref<{ src: string; title: string; baseS: number; durationS: number | null } | null>(null);
  const previewConverting = ref(false);
  let previewPollTimer: ReturnType<typeof setInterval> | null = null;

  function stopPreviewPoll(): void {
    if (previewPollTimer !== null) {
      clearInterval(previewPollTimer);
      previewPollTimer = null;
    }
  }

  function previewFileUrl(sourcePath: string): string {
    return `/api/v1/clips/preview?path=${encodeURIComponent(sourcePath)}`;
  }

  function previewRangeUrl(sourcePath: string, offsetS: number, durationS: number): string {
    return (
      `/api/v1/clips/preview/range?path=${encodeURIComponent(sourcePath)}` +
      `&start_s=${offsetS.toFixed(3)}&duration_s=${durationS.toFixed(3)}`
    );
  }

  /** 等整场转换就绪的轮询（仅"整段素材预览且源未转换"时使用） */
  function pollUntilReady(sourcePath: string, title: string): void {
    preview.value = null;
    previewConverting.value = true;
    previewPollTimer = setInterval(() => {
      void (async () => {
        try {
          const check = await clipsApi.previewStatus(sourcePath);
          if (check.data.ready) {
            stopPreviewPoll();
            previewConverting.value = false;
            preview.value = { src: previewFileUrl(sourcePath), title, baseS: 0, durationS: null };
          }
        } catch {
          /* 单次轮询失败静默，下个周期再试 */
        }
      })();
    }, 2000);
  }

  async function openPreview(
    sourcePath: string,
    title: string,
    offsetS: number,
    durationS: number | null,
  ): Promise<void> {
    stopPreviewPoll();
    try {
      const { data } = await clipsApi.previewStatus(sourcePath);
      if (data.ready) {
        previewConverting.value = false;
        preview.value = { src: previewFileUrl(sourcePath), title, baseS: offsetS, durationS };
        return;
      }
      if (durationS === null) {
        pollUntilReady(sourcePath, title);
        return;
      }
      // 段级秒开：后端现切现转那几分钟，loadedmetadata 即就绪
      previewConverting.value = true;
      preview.value = {
        src: previewRangeUrl(sourcePath, offsetS, durationS),
        title,
        baseS: 0,
        durationS,
      };
    } catch (error) {
      previewConverting.value = false;
      ElMessage.error(getApiErrorMessage(error, '打开预览失败'));
    }
  }

  function closePreview(): void {
    stopPreviewPoll();
    previewConverting.value = false;
    preview.value = null;
  }

  // ---- 归属人工覆盖：指派 / 移出 / 恢复（成功后回读录播与场次列表）----
  async function afterRecordingMutation(sessionId: number): Promise<void> {
    await loadSessions(); // 场次列表是全局面（文件数变了要刷新），不受"当前打开场次"守卫限制
    const seq = openSeq;
    try {
      const { data } = await clipsApi.listRecordings(sessionId);
      if (seq !== openSeq || activeSessionId.value !== sessionId) return;
      recordings.value = data.files;
      detachedFiles.value = data.detached_files;
      unmatchedFiles.value = data.unmatched_files;
    } catch (error) {
      ElMessage.error(getApiErrorMessage(error, '刷新录播文件失败'));
    }
  }

  async function assignRecording(sessionId: number, path: string, startMs: number | null): Promise<boolean> {
    try {
      await clipsApi.assignRecording(sessionId, path, startMs);
      await afterRecordingMutation(sessionId);
      return true;
    } catch (error) {
      ElMessage.error(getApiErrorMessage(error, '指派失败'));
      return false;
    }
  }

  async function detachRecording(sessionId: number, path: string): Promise<boolean> {
    try {
      await clipsApi.detachRecording(sessionId, path);
      await afterRecordingMutation(sessionId);
      return true;
    } catch (error) {
      ElMessage.error(getApiErrorMessage(error, '移出失败'));
      return false;
    }
  }

  async function restoreRecording(sessionId: number, path: string): Promise<boolean> {
    try {
      await clipsApi.restoreRecording(sessionId, path);
      await afterRecordingMutation(sessionId);
      return true;
    } catch (error) {
      ElMessage.error(getApiErrorMessage(error, '恢复失败'));
      return false;
    }
  }

  /** 读容器元数据里的录制时刻（没有返回 null） */
  async function readCreationTime(path: string): Promise<number | null> {
    try {
      const { data } = await clipsApi.creationTime(path);
      return data.creation_time_ms;
    } catch (error) {
      ElMessage.error(getApiErrorMessage(error, '读取元数据失败'));
      return null;
    }
  }

  async function renameRecording(path: string, newName: string): Promise<boolean> {
    try {
      await clipsApi.renameRecording(path, newName);
      // 改名与场次无关，但当前打开场次的素材列表要按新路径刷新（未打开场次时守卫会跳过）
      if (activeSessionId.value !== null) await afterRecordingMutation(activeSessionId.value);
      else await loadSessions();
      return true;
    } catch (error) {
      ElMessage.error(getApiErrorMessage(error, '改名失败'));
      return false;
    }
  }

  /** 批量预热整场预览缓存（失败静默——预热是体验优化，不值得打扰） */
  async function preparePreviews(paths: string[]): Promise<void> {
    if (paths.length === 0) return;
    lastPreparedPaths.value = paths;
    try {
      await clipsApi.preparePreviews(paths);
    } catch {
      /* 静默 */
    }
  }

  // ---- 素材卡就绪态：整场缓存是否已转好（按文件查询，任务终态后刷新）----
  const previewReadiness = ref<Record<string, boolean>>({});
  const lastPreparedPaths = ref<string[]>([]);

  async function refreshPreviewReadiness(paths: string[]): Promise<void> {
    const checks = await Promise.all(
      paths.map(async path => {
        if (path.toLowerCase().endsWith('.mp4')) return [path, true] as const;
        try {
          const { data } = await clipsApi.previewStatus(path);
          return [path, data.ready] as const;
        } catch {
          return [path, false] as const;
        }
      }),
    );
    const next: Record<string, boolean> = { ...previewReadiness.value };
    for (const [path, ready] of checks) next[path] = ready;
    previewReadiness.value = next;
  }

  // ---- 任务终态自动回读（订阅按激活态启停，见文件头生命周期语义）----
  // keep-alive 内的组件首次挂载也会触发 onActivated，无需 setup 期额外订阅
  function handleTaskChanged(msg: WebSocketMessage): void {
    if (msg.type !== EVENT.backgroundTaskChanged) return;
    const task = msg.data as unknown as BackgroundTask;
    if (task.task_id.startsWith('clip-preview-')) {
      void refreshPreviewReadiness(lastPreparedPaths.value);
      return;
    }
    const match = CLIP_TASK_RE.exec(task.task_id);
    if (match === null || task.state === 'running') return;
    void reloadSegments(Number(match[1]));
  }

  const wsStore = useWebSocketStore();
  onActivated(() => wsStore.subscribe(handleTaskChanged));
  onDeactivated(() => {
    wsStore.unsubscribe(handleTaskChanged);
    stopPreviewPoll();
  });
  onScopeDispose(() => {
    wsStore.unsubscribe(handleTaskChanged);
    stopPreviewPoll();
  });

  return {
    sessions,
    sessionsLoading,
    configState,
    configSaving,
    recordings,
    detachedFiles,
    unmatchedFiles,
    segments,
    detailLoading,
    selecting,
    cutting,
    preview,
    previewConverting,
    previewReadiness,
    refreshPreviewReadiness,
    loadSessions,
    loadConfig,
    saveConfig,
    openSession,
    reloadSegments,
    runSelection,
    createSegment,
    updateSegment,
    deleteSegment,
    cutSegments,
    openPreview,
    closePreview,
    preparePreviews,
    assignRecording,
    detachRecording,
    restoreRecording,
    readCreationTime,
    renameRecording,
  };
}
