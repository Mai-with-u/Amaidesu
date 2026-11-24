/**
 * 直播切片 API 客户端：录播匹配 / AI 选段 / 段增删改查 / ffmpeg 切割受理。
 *
 * 独立成模块的原因：切片域的端点持续增长（配置、归属覆盖、预览、密度等），
 * 集中在 api/index.ts 会让该文件持续膨胀；本模块复用共享 axios 实例
 * （超时、拦截器与全局行为一致），仅按域收拢端点定义。
 */
import api from '@/api';
import type {
  ClipConfig,
  ClipConfigApplyResult,
  ClipConfigState,
  ClippedSessionItem,
  ClipSegment,
  RecordingFileInfo,
  UnmatchedFileInfo,
} from '@/types/clips';

// `POST .../select` 与 `POST .../cut` 都是受理制：HTTP 立即返回，LLM 调用 /
// ffmpeg 执行在后台任务里跑，逐段状态回填到段列表（前端经任务终态回读刷新）。
export const clipsApi = {
  // 配置读写：保存即免重启生效（后端写盘 + 运行期重装配），无需去设置页重启
  getConfig: () => api.get<ClipConfigState>('/clips/config'),
  saveConfig: (config: ClipConfig) => api.post<ClipConfigApplyResult>('/clips/config', config),
  listSessions: () => api.get<{ sessions: ClippedSessionItem[] }>('/clips/sessions'),
  listRecordings: (sessionId: number) =>
    api.get<{
      live_session_id: number;
      files: RecordingFileInfo[];
      detached_files: RecordingFileInfo[];
      unmatched_files: UnmatchedFileInfo[];
    }>(`/clips/sessions/${sessionId}/recordings`),
  // 归属人工覆盖（本地 JSON，不入库）：指派/移出/恢复自动匹配
  assignRecording: (sessionId: number, path: string, startMs: number | null) =>
    api.post<{ deleted: boolean }>(`/clips/sessions/${sessionId}/recordings/assign`, {
      path,
      start_ms: startMs,
    }),
  detachRecording: (sessionId: number, path: string) =>
    api.post<{ deleted: boolean }>(`/clips/sessions/${sessionId}/recordings/detach`, { path }),
  restoreRecording: (sessionId: number, path: string) =>
    api.post<{ deleted: boolean }>(`/clips/sessions/${sessionId}/recordings/restore`, { path }),
  // 容器元数据里的录制时刻（指派对话框的取时来源）；WebUI 改名（归属覆盖随新路径迁移）
  creationTime: (path: string) =>
    api.get<{ creation_time_ms: number | null }>('/clips/recordings/creation-time', {
      params: { path },
    }),
  renameRecording: (path: string, newName: string) =>
    api.post<{ path: string }>('/clips/recordings/rename', { path, new_name: newName }),
  select: (sessionId: number) =>
    api.post<{ accepted: boolean }>(`/clips/sessions/${sessionId}/select`),
  listSegments: (sessionId: number) =>
    api.get<{ live_session_id: number; segments: ClipSegment[] }>(
      `/clips/sessions/${sessionId}/segments`,
    ),
  // 密度轨：按桶计数的场次聊天密度（counts[i] 覆盖 start+i*bucket_ms 起的区间）
  sessionDensity: (sessionId: number, bucketSeconds = 30) =>
    api.get<{ live_session_id: number; bucket_ms: number; started_at_ms: number; counts: number[] }>(
      `/clips/sessions/${sessionId}/density`,
      { params: { bucket_seconds: bucketSeconds } },
    ),
  createSegment: (sessionId: number, body: { start_ms: number; end_ms: number; title: string }) =>
    api.post<{ segment: ClipSegment | null }>(`/clips/sessions/${sessionId}/segments`, body),
  updateSegment: (
    segmentId: number,
    body: Partial<{ start_ms: number; end_ms: number; title: string; status: string }>,
  ) => api.put<{ segment: ClipSegment | null }>(`/clips/segments/${segmentId}`, body),
  deleteSegment: (segmentId: number) =>
    api.delete<{ deleted: boolean }>(`/clips/segments/${segmentId}`),
  cut: (sessionId: number, segmentIds: number[]) =>
    api.post<{ accepted: number }>(`/clips/sessions/${sessionId}/cut`, { segment_ids: segmentIds }),
  // 产物定位：在系统文件管理器里打开切片产物所在位置（越界路径后端 422）
  revealOutput: (path: string) =>
    api.post<{ opened: boolean }>('/clips/outputs/reveal', { path }),
  // 预览：status 轻量轮询就绪态；文件本体走 <video> 直连 /clips/preview（不走 axios，免超时）
  previewStatus: (path: string) =>
    api.get<{ ready: boolean; converting: boolean }>('/clips/preview/status', { params: { path } }),
  preparePreviews: (paths: string[]) =>
    api.post<{ started: number }>('/clips/preview/prepare', { paths }),
};
