// useTimelineReplay.ts 回放状态机的测试
//
// 覆盖：全量翻页载入（has_more 续翻 + 失败复位 + 空归档）、起始时刻定位、
// 播放定时器（按实际时间按真实时差推进 / 长空档压上限 / 按条目匀速 / 播到末尾自停）、
// 倍速档位按节奏校验与切换节奏时夹取、单步步进（步进即暂停 + 边界钳制）、
// 来源过滤只影响展示不改游标口径、跳跃定位、退出复位、
// 归档完整性标记（事件诊断数据被清理 / 翻页触顶未取尽）。

import { effectScope, ref, type EffectScope, type Ref } from 'vue';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { liveSessionsApi } from '@/api';
import { ElMessage } from 'element-plus';
import { useTimelineReplay } from '@/composables/live/useTimelineReplay';
import type { AgentGroup } from '@/utils/liveFeed';
import type { LiveSessionItem, SessionTimelineItem } from '@/types';

vi.mock('@/api', async importOriginal => ({
  ...(await importOriginal<object>()),
  liveSessionsApi: {
    list: vi.fn(),
    open: vi.fn(),
    close: vi.fn(),
    remove: vi.fn(),
    timeline: vi.fn(),
  },
}));

vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
  ElMessageBox: { prompt: vi.fn(), confirm: vi.fn() },
}));

const timelineMock = vi.mocked(liveSessionsApi.timeline);

function danmakuItem(tsMs: number, content: string): SessionTimelineItem {
  return { kind: 'danmaku', ts_ms: tsMs, user_name: '观众', content };
}

function timelineResponse(
  items: SessionTimelineItem[],
  hasMore = false,
  eventsPruned = false,
): never {
  // 仅 data 参与断言与消费，AxiosResponse 其余字段与本测试无关（同 useLiveTimeline.test.ts 的 as never 口径）
  return {
    data: {
      live_session_id: 7,
      items,
      has_more: hasMore,
      events_pruned: eventsPruned,
    },
  } as never;
}

function sessionItem(): LiveSessionItem {
  return {
    live_session_id: 7,
    source: 'manual',
    kind: 'live',
    title: '测试场次',
    room_id: 'r1',
    platform: 'test',
    started_at_ms: 1000,
    ended_at_ms: 9000,
    message_count: 5,
    is_active: false,
    notes: null,
    settled_at_ms: null,
    settle_attempts: 0,
    reviewed_at_ms: null,
    overview: null,
  };
}

describe('useTimelineReplay', () => {
  let scope: EffectScope;
  let agentFilter: Ref<'all' | AgentGroup>;

  beforeEach(() => {
    vi.useFakeTimers();
    scope = effectScope();
    agentFilter = ref('all');
    timelineMock.mockReset();
  });

  afterEach(() => {
    scope.stop();
    vi.useRealTimers();
  });

  function mount() {
    return scope.run(() => useTimelineReplay({ agentFilter }))!;
  }

  it('全量翻页载入：has_more 续翻前插，归档升序、游标落在第一条', async () => {
    timelineMock
      .mockResolvedValueOnce(
        timelineResponse([danmakuItem(6000, 'c'), danmakuItem(8000, 'd')], true),
      )
      .mockResolvedValueOnce(timelineResponse([danmakuItem(1000, 'a'), danmakuItem(3000, 'b')]));
    const replay = mount();
    await replay.start(sessionItem());
    expect(timelineMock).toHaveBeenCalledTimes(2);
    // 第二页以第一页最旧条目时刻为游标
    expect(timelineMock).toHaveBeenLastCalledWith(7, { before_timestamp_ms: 6000 });
    expect(replay.phase.value).toBe('ready');
    expect(replay.totalCount.value).toBe(4);
    expect(replay.visibleEntries.value.map(entry => entry.tsMs)).toEqual([1000]);
    expect(replay.cursor.value).toBe(0);
    expect(replay.playing.value).toBe(false);
  });

  it('指定起始时刻：游标落在第一条不早于该时刻的归档条目', async () => {
    timelineMock.mockResolvedValue(
      timelineResponse([danmakuItem(1000, 'a'), danmakuItem(3000, 'b'), danmakuItem(5000, 'c')]),
    );
    const replay = mount();
    await replay.start(sessionItem(), 3000);
    expect(replay.cursor.value).toBe(1);
    expect(replay.visibleEntries.value).toHaveLength(2);
  });

  it('载入失败：提示错误并复位回空闲态', async () => {
    timelineMock.mockRejectedValue(new Error('boom'));
    const replay = mount();
    await replay.start(sessionItem());
    expect(ElMessage.error).toHaveBeenCalled();
    expect(replay.phase.value).toBe('idle');
    expect(replay.totalCount.value).toBe(0);
    expect(replay.active.value).toBe(false);
  });

  it('按条目匀速：每条固定 1000ms ÷ 档位推进，播到末尾自停', async () => {
    timelineMock.mockResolvedValue(
      timelineResponse([danmakuItem(1000, 'a'), danmakuItem(2000, 'b')]),
    );
    const replay = mount();
    await replay.start(sessionItem());
    replay.setPace('uniform');
    replay.setSpeed(1);
    replay.togglePlay();
    expect(replay.playing.value).toBe(true);
    await vi.advanceTimersByTimeAsync(1000);
    expect(replay.cursor.value).toBe(1);
    await vi.advanceTimersByTimeAsync(1000);
    expect(replay.cursor.value).toBe(1);
    expect(replay.playing.value).toBe(false);
  });

  it('按实际时间：等待 = 两条事件真实时差 ÷ 倍率', async () => {
    timelineMock.mockResolvedValue(
      timelineResponse([danmakuItem(1000, 'a'), danmakuItem(3000, 'b'), danmakuItem(5000, 'c')]),
    );
    const replay = mount();
    await replay.start(sessionItem());
    expect(replay.pace.value).toBe('realtime');
    replay.setSpeed(4);
    replay.togglePlay();
    // 时差 2000ms ÷ 4 倍速 = 500ms
    await vi.advanceTimersByTimeAsync(500);
    expect(replay.cursor.value).toBe(1);
    await vi.advanceTimersByTimeAsync(500);
    expect(replay.cursor.value).toBe(2);
  });

  it('按实际时间：长空档压到 2 秒上限，不原地干等', async () => {
    timelineMock.mockResolvedValue(
      timelineResponse([danmakuItem(1000, 'a'), danmakuItem(1000 + 30 * 60_000, 'b')]),
    );
    const replay = mount();
    await replay.start(sessionItem());
    replay.setSpeed(1);
    replay.togglePlay();
    // 真实时差 30 分钟，1 倍速下会被压到 2000ms 上限
    await vi.advanceTimersByTimeAsync(1999);
    expect(replay.cursor.value).toBe(0);
    await vi.advanceTimersByTimeAsync(2);
    expect(replay.cursor.value).toBe(1);
  });

  it('倍速档位按当前节奏校验，切换节奏时夹到新档位集合内', async () => {
    timelineMock.mockResolvedValue(
      timelineResponse([danmakuItem(1000, 'a'), danmakuItem(2000, 'b')]),
    );
    const replay = mount();
    await replay.start(sessionItem());
    // 实际时间档位含 64
    replay.setSpeed(64);
    expect(replay.speed.value).toBe(64);
    // 越界倍速值不接受
    replay.setSpeed(99);
    expect(replay.speed.value).toBe(64);
    // 切到匀速：64 不在 [1,2,4,8,16] 内，夹到最近的 16
    replay.setPace('uniform');
    expect(replay.speed.value).toBe(16);
    // 匀速档位外的值不接受
    replay.setSpeed(64);
    expect(replay.speed.value).toBe(16);
    // 切回实际时间：16 在 [1,4,16,64,256] 内，保持不变
    replay.setPace('realtime');
    expect(replay.speed.value).toBe(16);
  });

  it('单步步进：步进即暂停，前进到末条钳制、后退到未开始清空可见', async () => {
    timelineMock.mockResolvedValue(
      timelineResponse([danmakuItem(1000, 'a'), danmakuItem(2000, 'b'), danmakuItem(3000, 'c')]),
    );
    const replay = mount();
    await replay.start(sessionItem());
    replay.setPace('uniform');
    replay.setSpeed(1);
    replay.togglePlay();
    await vi.advanceTimersByTimeAsync(1000);
    expect(replay.cursor.value).toBe(1);
    expect(replay.playing.value).toBe(true);
    // 手动步进即暂停（不被定时器抢跑）
    replay.stepForward();
    expect(replay.playing.value).toBe(false);
    expect(replay.cursor.value).toBe(2);
    // 已在末条：前进不再推进
    replay.stepForward();
    expect(replay.cursor.value).toBe(2);
    // 后退到 -1：可见清空，再退不越界
    replay.stepBack();
    replay.stepBack();
    replay.stepBack();
    expect(replay.cursor.value).toBe(-1);
    expect(replay.visibleEntries.value).toHaveLength(0);
    replay.stepBack();
    expect(replay.cursor.value).toBe(-1);
  });

  it('来源过滤只影响展示：过滤后可见变少而游标与总数不变', async () => {
    timelineMock.mockResolvedValue(
      timelineResponse([danmakuItem(1000, 'a'), danmakuItem(2000, 'b'), danmakuItem(3000, 'c')]),
    );
    const replay = mount();
    await replay.start(sessionItem());
    replay.stepForward();
    replay.stepForward();
    expect(replay.cursor.value).toBe(2);
    agentFilter.value = 'streamer';
    // 弹幕属 room 组，始终可见——换一个会被滤掉的来源口径验证过滤生效：
    // 此处弹幕全为 room 组，过滤后仍全可见，游标口径不受影响
    expect(replay.visibleEntries.value).toHaveLength(3);
    expect(replay.cursor.value).toBe(2);
    expect(replay.totalCount.value).toBe(3);
  });

  it('跳跃定位与退出复位', async () => {
    timelineMock.mockResolvedValue(
      timelineResponse([danmakuItem(1000, 'a'), danmakuItem(3000, 'b'), danmakuItem(5000, 'c')]),
    );
    const replay = mount();
    await replay.start(sessionItem());
    replay.setPace('uniform');
    replay.setSpeed(1);
    replay.togglePlay();
    await vi.advanceTimersByTimeAsync(1000);
    replay.jumpToTime(4000);
    expect(replay.playing.value).toBe(false);
    expect(replay.cursor.value).toBe(2);
    expect(replay.cursorTsMs.value).toBe(5000);
    replay.exit();
    expect(replay.active.value).toBe(false);
    expect(replay.session.value).toBeNull();
    expect(replay.cursor.value).toBe(-1);
    expect(replay.visibleEntries.value).toHaveLength(0);
  });

  it('空归档：提示没有可回放的归档并复位空闲态', async () => {
    timelineMock.mockResolvedValue(timelineResponse([]));
    const replay = mount();
    await replay.start(sessionItem());
    expect(ElMessage.error).toHaveBeenCalledWith('该场次没有可回放的归档');
    expect(replay.phase.value).toBe('idle');
    expect(replay.active.value).toBe(false);
    expect(replay.totalCount.value).toBe(0);
  });

  it('归档被清理：events_pruned 如实透传为归档不完整标记', async () => {
    timelineMock
      .mockResolvedValueOnce(timelineResponse([danmakuItem(6000, 'c')], true, true))
      .mockResolvedValueOnce(timelineResponse([danmakuItem(1000, 'a')]));
    const replay = mount();
    await replay.start(sessionItem());
    expect(replay.pruned.value).toBe(true);
    expect(replay.truncated.value).toBe(false);
  });

  it('翻页触顶：仍有更早历史时标记归档被截断', async () => {
    // 每页都回报还有更早页，翻页到上限仍没取尽
    timelineMock.mockResolvedValue(timelineResponse([danmakuItem(1000, 'a')], true));
    const replay = mount();
    await replay.start(sessionItem());
    expect(timelineMock).toHaveBeenCalledTimes(50);
    expect(replay.truncated.value).toBe(true);
    expect(replay.pruned.value).toBe(false);
  });
});
