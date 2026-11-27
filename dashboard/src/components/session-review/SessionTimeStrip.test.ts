// @vitest-environment happy-dom
// SessionTimeStrip 的铺底与锚点测试：环节段按时间比例定位、锚点/竖线逐个渲染并带回看深链

import { flushPromises, mount, type VueWrapper } from '@vue/test-utils';
import { describe, expect, it } from 'vitest';
import { createMemoryHistory, createRouter, type Router } from 'vue-router';
import SessionTimeStrip from './SessionTimeStrip.vue';
import type { ReviewGameEventItem, RundownSegmentRun } from '@/types';

const START = 1_700_000_000_000;
const SPAN = 1_000_000;

interface StripProps {
  sessionId: number;
  startMs: number;
  endMs: number;
  segments: RundownSegmentRun[];
  gameItems: ReviewGameEventItem[];
  silenceAtMs: number | null;
  latencyMaxAtMs: number | null;
}

function seg(
  index: number,
  enteredOffsetMs: number,
  leftOffsetMs: number | null,
): RundownSegmentRun {
  return {
    seg_index: index,
    seg_id: `seg-${index}`,
    seg_title: `环节${index}`,
    entered_at_ms: START + enteredOffsetMs,
    left_at_ms: leftOffsetMs === null ? null : START + leftOffsetMs,
    dwell_ms: (leftOffsetMs ?? SPAN) - enteredOffsetMs,
    expected_ms: 300_000,
    pause_ms: 0,
    pause_count: 0,
    by: 'agent',
    action: 'begin',
  };
}

function gameItem(offsetMs: number): ReviewGameEventItem {
  return { game: 'mc', event_type: 'death', message: '摔死了', timestamp_ms: START + offsetMs };
}

async function mountStrip(overrides: Partial<StripProps> = {}): Promise<VueWrapper> {
  const router: Router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/live', name: 'live', component: { template: '<div />' } }],
  });
  await router.push('/live');
  await router.isReady();
  const props: StripProps = {
    sessionId: 7,
    startMs: START,
    endMs: START + SPAN,
    segments: [],
    gameItems: [],
    silenceAtMs: null,
    latencyMaxAtMs: null,
    ...overrides,
  };
  const wrapper = mount(SessionTimeStrip, { props, global: { plugins: [router] } });
  await flushPromises();
  return wrapper;
}

describe('SessionTimeStrip', () => {
  it('全部内容为空时不渲染', async () => {
    const wrapper = await mountStrip();
    expect(wrapper.find('[aria-label="场次时间条"]').exists()).toBe(false);
  });

  it('环节段按时间比例铺底，未封口段延伸到右端', async () => {
    const wrapper = await mountStrip({
      segments: [seg(1, 0, 400_000), seg(2, 400_000, null)],
    });
    const els = wrapper.findAll('.strip-seg');
    expect(els).toHaveLength(2);
    expect(els[0].attributes('style')).toContain('left: 0%');
    expect(els[0].attributes('style')).toContain('width: 40%');
    expect(els[1].attributes('style')).toContain('left: 40%');
    expect(els[1].attributes('style')).toContain('width: 60%');
  });

  it('游戏事件竖线与两锚点逐个渲染，深链带场次与时刻', async () => {
    const wrapper = await mountStrip({
      gameItems: [gameItem(500_000)],
      silenceAtMs: 200_000,
      latencyMaxAtMs: 800_000,
    });
    expect(wrapper.findAll('.strip-tick')).toHaveLength(1);
    const anchors = wrapper.findAll('.strip-anchor');
    expect(anchors).toHaveLength(2);
    expect(anchors[0].classes()).toContain('is-silence');
    expect(anchors[1].classes()).toContain('is-latency');
    expect(anchors[0].attributes('href')).toContain('session=7');
    expect(anchors[0].attributes('href')).toContain('at=200000');
    expect(anchors[1].attributes('href')).toContain('at=800000');
  });

  it('零宽窗口不渲染', async () => {
    const wrapper = await mountStrip({ endMs: START, silenceAtMs: 0 });
    expect(wrapper.find('[aria-label="场次时间条"]').exists()).toBe(false);
  });
});
