// @vitest-environment happy-dom
// ReviewDecisionBars 的全量分布测试：按次数降序、触发原因译中文、空分布不渲染

import { mount } from '@vue/test-utils';
import { describe, expect, it } from 'vitest';
import ReviewDecisionBars from './ReviewDecisionBars.vue';

describe('ReviewDecisionBars', () => {
  it('按次数降序渲染全量原因，原值经词表译中文', () => {
    const wrapper = mount(ReviewDecisionBars, {
      props: { distribution: { batch_full: 8, 'proactive:signoff': 4, unknown: 1 }, total: 13 },
    });
    const rows = wrapper.findAll('.bar-row');
    expect(rows).toHaveLength(3);
    expect(rows[0].text()).toContain('弹幕攒满');
    expect(rows[1].text()).toContain('主动·收播道别');
    expect(rows[2].text()).toContain('未知');
    expect(rows[0].text()).toContain('8');
  });

  it('未收录的新枚举值原值透出', () => {
    const wrapper = mount(ReviewDecisionBars, {
      props: { distribution: { 'proactive:future_kind': 2 }, total: 2 },
    });
    expect(wrapper.findAll('.bar-row')).toHaveLength(1);
    expect(wrapper.text()).toContain('proactive:future_kind');
  });

  it('空分布不渲染面板', () => {
    const wrapper = mount(ReviewDecisionBars, { props: { distribution: {}, total: 0 } });
    expect(wrapper.find('[aria-label="决策触发原因分布"]').exists()).toBe(false);
  });
});
