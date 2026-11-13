// @vitest-environment happy-dom
// 失焦提交输入框的行为守护
//
// 这类输入框存在的理由：改名要问引用方要不要跟着改，逐键提交会把只输入了
// 前几个字符的中间态名字写进引用方。所以逐键输入只落本地草稿，失焦或回车
// 才把最终值交出去。
//
// 同时守护一个 Element Plus 的坑：输入框只给值、不接更新事件时，每次键盘
// 输入后输入框里的文字都会被重置回原值，字符根本留不住。

import { mount } from '@vue/test-utils';
import { describe, expect, it, vi } from 'vitest';
import { defineComponent, h, nextTick, ref } from 'vue';
import ElementPlus from 'element-plus';
import CommitOnBlurInput from './CommitOnBlurInput.vue';

/** 采纳提交的宿主：父组件把提交值写回已存值（页面里的真实做法） */
const AdoptingHost = defineComponent({
  props: { initial: { type: String, default: '' } },
  setup(props) {
    const stored = ref(props.initial);
    return { stored };
  },
  render() {
    return h(CommitOnBlurInput, {
      value: this.stored,
      onCommit: (value: string) => {
        this.stored = value;
      },
    });
  },
});

describe('CommitOnBlurInput 草稿与提交', () => {
  it('逐键输入不丢字', async () => {
    const wrapper = mount(CommitOnBlurInput, {
      props: { value: 'new-model', placeholder: '模型唯一名' },
      global: { plugins: [ElementPlus] },
    });
    const input = wrapper.find('input');

    // 只发 input 事件（一次击键），不触发提交
    (input.element as HTMLInputElement).value = 'Mai-Replier';
    await input.trigger('input');
    await nextTick();

    expect((input.element as HTMLInputElement).value).toBe('Mai-Replier');
  });

  it('失焦时交出最终值', async () => {
    const wrapper = mount(CommitOnBlurInput, {
      props: { value: 'new-model' },
      global: { plugins: [ElementPlus] },
    });
    const input = wrapper.find('input');

    (input.element as HTMLInputElement).value = 'Mai-Replier';
    await input.trigger('input');
    await input.trigger('blur');

    expect(wrapper.emitted('commit')?.[0]).toEqual(['Mai-Replier']);
  });

  it('回车同样提交', async () => {
    const wrapper = mount(CommitOnBlurInput, {
      props: { value: 'new-model' },
      global: { plugins: [ElementPlus] },
    });
    const input = wrapper.find('input');

    (input.element as HTMLInputElement).value = 'Mai-Replier';
    await input.trigger('input');
    await input.trigger('keydown', { key: 'Enter' });

    expect(wrapper.emitted('commit')?.[0]).toEqual(['Mai-Replier']);
  });

  it('父组件采纳提交后已存值与输入框一致', async () => {
    const wrapper = mount(AdoptingHost, {
      props: { initial: 'new-model' },
      global: { plugins: [ElementPlus] },
    });
    const input = wrapper.find('input');

    (input.element as HTMLInputElement).value = 'Mai-Replier';
    await input.trigger('input');
    await input.trigger('blur');
    await nextTick();
    await nextTick();

    expect(wrapper.vm.stored).toBe('Mai-Replier');
    expect((input.element as HTMLInputElement).value).toBe('Mai-Replier');
  });

  it('父组件不采纳提交时草稿回落已存值', async () => {
    const wrapper = mount(CommitOnBlurInput, {
      props: { value: 'new-model' },
      global: { plugins: [ElementPlus] },
    });
    const input = wrapper.find('input');

    (input.element as HTMLInputElement).value = '';
    await input.trigger('input');
    await input.trigger('blur');
    await nextTick();
    await nextTick();

    expect((input.element as HTMLInputElement).value).toBe('new-model');
  });

  it('已存值被外部改掉时草稿跟着同步', async () => {
    const onCommit = vi.fn();
    const wrapper = mount(CommitOnBlurInput, {
      props: { value: 'new-model', onCommit },
      global: { plugins: [ElementPlus] },
    });
    const input = wrapper.find('input');

    (input.element as HTMLInputElement).value = 'Mai-Replier';
    await input.trigger('input');
    await wrapper.setProps({ value: 'Mai-Replier' });
    await nextTick();

    expect((input.element as HTMLInputElement).value).toBe('Mai-Replier');
  });
});
