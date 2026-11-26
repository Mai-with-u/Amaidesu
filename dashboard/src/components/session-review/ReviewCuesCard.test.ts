// @vitest-environment happy-dom
// ReviewCuesCard 的来源筛选测试（筛选状态在卡片本地，条目更新时复位为"全部"）

import { flushPromises, mount } from '@vue/test-utils';
import { describe, expect, it } from 'vitest';
import { createMemoryHistory, createRouter } from 'vue-router';
import { ElRadioButton, ElRadioGroup, ElTag } from 'element-plus';
import ReviewCuesCard from './ReviewCuesCard.vue';
import type { ReviewCueItem } from '@/types';

function cue(id: number, sources: string[]): ReviewCueItem {
  return {
    id,
    pushed_at_ms: 1_700_000_000_000 + id,
    card_kind: 'rule',
    label: `档#${id}`,
    title: `第 ${id} 行`,
    archive_line_id: id,
    commitment_id: null,
    sources,
    detail: {},
    self_triggered: false,
    used_at_ms: null,
  };
}

async function mountCard(items: ReviewCueItem[]) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/memory', name: 'memory', component: { template: '<div />' } }],
  });
  await router.push('/memory');
  await router.isReady();
  const wrapper = mount(ReviewCuesCard, {
    props: { items, loading: false, error: '' },
    global: {
      plugins: [router],
      components: { ElRadioButton, ElRadioGroup, ElTag },
      directives: { loading: () => undefined },
    },
  });
  await flushPromises();
  return wrapper;
}

describe('ReviewCuesCard 来源筛选', () => {
  it('多来源时出现筛选按钮，选中来源后只显示该来源的线索', async () => {
    const wrapper = await mountCard([cue(1, ['danmaku']), cue(2, ['self'])]);

    const radio = wrapper.findComponent(ElRadioGroup);
    expect(radio.exists()).toBe(true);
    wrapper.findComponent(ElRadioGroup).vm.$emit('update:modelValue', 'self');
    await flushPromises();

    const text = wrapper.text();
    expect(text).toContain('第 2 行');
    expect(text).not.toContain('第 1 行');
  });

  it('单来源不出筛选按钮', async () => {
    const wrapper = await mountCard([cue(1, ['danmaku'])]);
    expect(wrapper.findComponent(ElRadioGroup).exists()).toBe(false);
    expect(wrapper.text()).toContain('第 1 行');
  });

  it('条目更新（换场）后筛选复位为全部', async () => {
    const wrapper = await mountCard([cue(1, ['danmaku']), cue(2, ['self'])]);
    wrapper.findComponent(ElRadioGroup).vm.$emit('update:modelValue', 'self');
    await flushPromises();
    expect(wrapper.text()).not.toContain('第 1 行');

    // 换场后旧筛选失效：place 来源的新条目可见（若筛选未复位则它会被滤掉）
    await wrapper.setProps({ items: [cue(9, ['place'])] });
    await flushPromises();
    expect(wrapper.text()).toContain('第 9 行');
  });
});
