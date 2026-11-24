/**
 * useClips 组合式测试：经真实 pinia + mock 的 api 驱动，验证加载/写操作/错误路径
 * 的状态流转（对齐 useBackgroundTasks.test.ts 的形态）。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';

const mocks = vi.hoisted(() => ({
  clipsApi: {
    listSessions: vi.fn(),
    listRecordings: vi.fn(),
    listSegments: vi.fn(),
    select: vi.fn(),
    createSegment: vi.fn(),
    updateSegment: vi.fn(),
    deleteSegment: vi.fn(),
    cut: vi.fn(),
    getConfig: vi.fn(),
    saveConfig: vi.fn(),
    assignRecording: vi.fn(),
    detachRecording: vi.fn(),
    restoreRecording: vi.fn(),
  },
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

vi.mock('@/api/clips', () => ({ clipsApi: mocks.clipsApi }));
vi.mock('element-plus', () => ({ ElMessage: mocks.ElMessage }));
// websocket store 构造会 new WebSocketClient（依赖 window），node 环境下用替身
vi.mock('@/api/websocket', () => ({
  wsClient: {
    onConnect: vi.fn(),
    onDisconnect: vi.fn(),
    onMessage: vi.fn(),
    subscribe: vi.fn(),
    unsubscribe: vi.fn(),
    connect: vi.fn().mockResolvedValue(undefined),
    disconnect: vi.fn(),
    isConnected: vi.fn(() => false),
  },
}));

import { useClips } from '@/composables/useClips';
import { getApiErrorMessage } from '@/utils/apiError';

function ok<T>(data: T) {
  return Promise.resolve({ data });
}

const SESSIONS = {
  sessions: [
    { live_session_id: 7, started_at_ms: 1000, ended_at_ms: 2000, title: '一场', file_count: 2 },
  ],
};
const RECORDINGS = { live_session_id: 7, files: [], unmatched_files: [] };
const SEGMENTS = { live_session_id: 7, segments: [] };

beforeEach(() => {
  setActivePinia(createPinia());
  vi.clearAllMocks();
});

describe('useClips', () => {
  it('loadSessions 成功填充列表；失败弹错误且不抛', async () => {
    const clips = useClips();
    mocks.clipsApi.listSessions.mockReturnValue(ok(SESSIONS));
    await clips.loadSessions();
    expect(clips.sessions.value).toHaveLength(1);
    expect(clips.sessionsLoading.value).toBe(false);

    mocks.clipsApi.listSessions.mockRejectedValue(new Error('网络炸了'));
    await clips.loadSessions();
    expect(mocks.ElMessage.error).toHaveBeenCalledWith(
      getApiErrorMessage(new Error('网络炸了'), '加载场次列表失败'),
    );
  });

  it('openSession 并行取录播与段；失败清空并报错', async () => {
    const clips = useClips();
    mocks.clipsApi.listRecordings.mockReturnValue(ok(RECORDINGS));
    mocks.clipsApi.listSegments.mockReturnValue(ok(SEGMENTS));
    await clips.openSession(7);
    expect(clips.recordings.value).toEqual([]);
    expect(clips.detailLoading.value).toBe(false);

    mocks.clipsApi.listSegments.mockRejectedValue(new Error('boom'));
    await clips.openSession(7);
    expect(mocks.ElMessage.error).toHaveBeenCalled();
  });

  it('runSelection 受理后返回 true 并置进行中；失败返回 false 且不置进行中', async () => {
    const clips = useClips();
    mocks.clipsApi.select.mockReturnValue(ok({ accepted: true }));
    expect(await clips.runSelection(7)).toBe(true);
    expect(clips.selecting.value).toBe(true); // 任务终态（handleTaskChanged）负责关闭

    const clips2 = useClips();
    mocks.clipsApi.select.mockRejectedValue(new Error('LLM 挂了'));
    expect(await clips2.runSelection(7)).toBe(false);
    expect(clips2.selecting.value).toBe(false);
  });

  it('reloadSegments 只认当前打开的场次（防串场）', async () => {
    const clips = useClips();
    mocks.clipsApi.listSegments.mockReturnValue(ok(SEGMENTS));
    await clips.openSession(7);
    mocks.clipsApi.listSegments.mockClear();

    await clips.reloadSegments(8); // 别的场次：静默跳过
    expect(mocks.clipsApi.listSegments).not.toHaveBeenCalled();

    await clips.reloadSegments(7); // 当前场次：回读
    expect(mocks.clipsApi.listSegments).toHaveBeenCalledWith(7);
  });

  it('cutSegments 成功弹受理提示并回读；失败返回 false', async () => {
    const clips = useClips();
    mocks.clipsApi.cut.mockReturnValue(ok({ accepted: 3 }));
    mocks.clipsApi.listSegments.mockReturnValue(ok(SEGMENTS));
    expect(await clips.cutSegments(7, [1, 2, 3])).toBe(true);
    expect(mocks.ElMessage.success).toHaveBeenCalled();

    mocks.clipsApi.cut.mockRejectedValue(new Error('拒绝'));
    expect(await clips.cutSegments(7, [1])).toBe(false);
    expect(mocks.ElMessage.error).toHaveBeenCalled();
  });

  it('createSegment/updateSegment 返回服务端回传的段且不回读列表；失败返回 null', async () => {
    const clips = useClips();
    const created = { id: 9, title: '新段' };
    mocks.clipsApi.createSegment.mockReturnValue(ok({ segment: created }));
    expect(await clips.createSegment(7, { start_ms: 1, end_ms: 2, title: '新段' })).toEqual(created);
    expect(mocks.clipsApi.listSegments).not.toHaveBeenCalled(); // 就地插入，不整表回读

    const updated = { id: 5, title: '改名' };
    mocks.clipsApi.updateSegment.mockReturnValue(ok({ segment: updated }));
    expect(await clips.updateSegment(5, { title: '改名' })).toEqual(updated);

    mocks.clipsApi.createSegment.mockRejectedValue(new Error('x'));
    expect(await clips.createSegment(7, { start_ms: 1, end_ms: 2, title: '' })).toBeNull();

    mocks.clipsApi.updateSegment.mockRejectedValue(new Error('x'));
    expect(await clips.updateSegment(5, { title: 'y' })).toBeNull();
  });

  it('assignRecording 成功后回读录播与场次；失败弹错', async () => {
    const clips = useClips();
    mocks.clipsApi.assignRecording.mockReturnValue(ok({ deleted: false }));
    mocks.clipsApi.listRecordings.mockReturnValue(
      ok({ live_session_id: 7, files: [], detached_files: [], unmatched_files: [] }),
    );
    mocks.clipsApi.listSessions.mockReturnValue(ok(SESSIONS));
    expect(await clips.assignRecording(7, 'E:/rec/x.mp4', 1000)).toBe(true);
    expect(mocks.clipsApi.listRecordings).toHaveBeenCalledWith(7);
    expect(mocks.clipsApi.listSessions).toHaveBeenCalled();

    mocks.clipsApi.assignRecording.mockRejectedValue(new Error('越界'));
    expect(await clips.assignRecording(7, 'C:/outside.mp4', 1)).toBe(false);
    expect(mocks.ElMessage.error).toHaveBeenCalled();
  });

  it('loadConfig 填充装配态；saveConfig 成功回读配置并弹成功提示', async () => {
    const clips = useClips();
    const state = {
      assembled: false,
      config: {
        enabled: false,
        recording_dir: '',
        ffmpeg_path: '',
        output_dir: '',
        llm_profile: 'settle',
        reencode: false,
        min_segment_seconds: 30,
        max_segment_seconds: 300,
      },
      recording_dir_exists: false,
    };
    mocks.clipsApi.getConfig.mockReturnValue(ok(state));
    expect(await clips.loadConfig()).toEqual(state);
    expect(clips.configState.value).toEqual(state);

    mocks.clipsApi.saveConfig.mockReturnValue(
      ok({
        success: true,
        message: '切片配置已保存并生效',
        assembled: true,
        recording_dir_exists: true,
      }),
    );
    mocks.clipsApi.getConfig.mockReturnValue(
      ok({ ...state, assembled: true, recording_dir_exists: true }),
    );
    expect(
      await clips.saveConfig({ ...state.config, enabled: true, recording_dir: 'E:\\rec' }),
    ).toBe(true);
    expect(clips.configState.value?.assembled).toBe(true);
    expect(mocks.ElMessage.success).toHaveBeenCalled();

    mocks.clipsApi.saveConfig.mockRejectedValue(new Error('写盘失败'));
    expect(await clips.saveConfig(state.config)).toBe(false);
    expect(mocks.ElMessage.error).toHaveBeenCalled();
  });
});
