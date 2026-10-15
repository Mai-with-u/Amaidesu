/* eslint-disable vue/one-component-per-file -- 测试需要宿主+消费两个内联组件 */
// @vitest-environment happy-dom
// useNowTick.ts 共享时钟的生命周期回归测试：本日曾连续出现三个 keep-alive
// 生命周期 bug（定时器/轮询/时钟常驻），纯 node 环境无法触发 activated/
// deactivated，此处用 VTU + happy-dom 渲染 <KeepAlive> 宿主组件补盲区。
//
// 覆盖：共享定时器随首个消费方启动、deactivated 停走、reactivated 重启并
// 把时间跳到当前值、卸载释放引用、多消费方一隐一显时定时器不停。
//
// 读取方式：keep-alive 隐藏后组件离开 DOM，无法从渲染结果读 now，
// 各用例经闭包捕获消费组件持有的共享 ref 直接断言。

import { defineComponent, h, KeepAlive, type Component, type Ref } from 'vue';
import { mount, type VueWrapper } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// 模块级单例状态（now/timer/refCount）在用例间必须归零，逐用例动态导入新实例
type UseNowTick = () => Ref<number>;
let useNowTick: UseNowTick;

/** 在 <KeepAlive> 内消费 useNowTick 的宿主组件：动态组件显隐触发激活钩子 */
function makeHost(Consumer: Component) {
  return defineComponent({
    props: { show: { type: Boolean, default: true } },
    setup(props) {
      return () => h(KeepAlive, () => (props.show ? h(Consumer) : null));
    },
  });
}

/** 消费组件：setup 里把共享时钟 ref 存入 out 供断言 */
function makeConsumer(out: { now?: Ref<number> }): Component {
  return defineComponent({
    setup() {
      out.now = useNowTick();
      return () => h('div', { class: 'tick' }, String(out.now!.value));
    },
  });
}

describe('useNowTick 生命周期', () => {
  let wrapper: VueWrapper | null = null;

  beforeEach(async () => {
    vi.resetModules();
    ({ useNowTick } = (await import('./useNowTick')) as { useNowTick: UseNowTick });
    // Date 一并接管：模块初值与跳位断言都依赖 Date.now() 随虚拟时钟前进
    vi.useFakeTimers({ now: 1_000_000, toFake: ['setInterval', 'clearInterval', 'Date'] });
  });

  afterEach(() => {
    wrapper?.unmount();
    wrapper = null;
    vi.useRealTimers();
  });

  it('挂载在 keep-alive 内的消费组件：共享定时器启动、now 随时间前进', async () => {
    const out: { now?: Ref<number> } = {};
    wrapper = mount(makeHost(makeConsumer(out)));
    expect(out.now!.value).toBe(1_000_000);

    await vi.advanceTimersByTimeAsync(2500);
    // 整秒 tick：2500ms 内 tick 到 2000ms
    expect(out.now!.value).toBe(1_002_000);
  });

  it('deactivated：定时器停止，now 不再前进', async () => {
    const out: { now?: Ref<number> } = {};
    wrapper = mount(makeHost(makeConsumer(out)));
    await vi.advanceTimersByTimeAsync(1000);
    expect(out.now!.value).toBe(1_001_000);

    await wrapper.setProps({ show: false });
    await vi.advanceTimersByTimeAsync(10_000);
    expect(out.now!.value).toBe(1_001_000);
  });

  it('reactivated：定时器重启且 now 立即跳到当前时间', async () => {
    const out: { now?: Ref<number> } = {};
    wrapper = mount(makeHost(makeConsumer(out)));
    await vi.advanceTimersByTimeAsync(1000);
    expect(out.now!.value).toBe(1_001_000);

    await wrapper.setProps({ show: false });
    // 隐藏期间真实时间继续走，共享时钟停走
    await vi.advanceTimersByTimeAsync(10_000);
    expect(out.now!.value).toBe(1_001_000);

    await wrapper.setProps({ show: true });
    // 恢复即跳位：不落后于隐藏期间流逝的时间
    expect(out.now!.value).toBe(1_011_000);
    await vi.advanceTimersByTimeAsync(1000);
    expect(out.now!.value).toBe(1_012_000);
  });

  it('卸载：引用释放、定时器停止', async () => {
    const out: { now?: Ref<number> } = {};
    wrapper = mount(makeHost(makeConsumer(out)));
    await vi.advanceTimersByTimeAsync(1000);
    wrapper.unmount();
    wrapper = null;

    await vi.advanceTimersByTimeAsync(10_000);
    // 卸载后共享时钟不再走动；新消费方挂载读回当前时间（此刻已过 11s）
    const probeOut: { now?: Ref<number> } = {};
    const probe = mount(makeHost(makeConsumer(probeOut)));
    expect(probeOut.now!.value).toBe(1_011_000);
    probe.unmount();
  });

  it('多消费方一隐一显：定时器不停，可见方继续前进', async () => {
    const out: { now?: Ref<number> } = {};
    const Consumer = makeConsumer(out);
    wrapper = mount(makeHost(Consumer));

    // 第二个消费方（同一共享时钟的独立引用计数）
    const host2 = mount(makeHost(Consumer));
    await vi.advanceTimersByTimeAsync(1000);
    expect(out.now!.value).toBe(1_001_000);

    await host2.setProps({ show: false });
    await vi.advanceTimersByTimeAsync(2000);
    // 一隐一显时共享定时器仍在走
    expect(out.now!.value).toBe(1_003_000);
    host2.unmount();
  });
});
