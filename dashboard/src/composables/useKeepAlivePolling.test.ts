/* eslint-disable vue/one-component-per-file -- 测试需要宿主+消费两个内联组件 */
// @vitest-environment happy-dom
// useKeepAlivePolling 组合式的测试
//
// 覆盖：激活建立轮询 / 失活停止 / 再激活恢复（effectScope 不触发
// activated/deactivated，须以真实组件挂在 <KeepAlive> 内验证）、args 传参、
// startOnSetup 的 setup 同步启动、stopOnDispose 的销毁兜底。

import { defineComponent, effectScope, h, KeepAlive, type EffectScope } from 'vue';
import { mount, type VueWrapper } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useKeepAlivePolling } from '@/composables/useKeepAlivePolling';

describe('useKeepAlivePolling（KeepAlive 内消费）', () => {
  let scope: EffectScope;
  let wrapper: VueWrapper | null = null;
  const task = vi.fn();

  beforeEach(() => {
    vi.useFakeTimers();
    scope = effectScope();
    task.mockReset();
  });

  afterEach(() => {
    wrapper?.unmount();
    wrapper = null;
    scope.stop();
    vi.useRealTimers();
  });

  /** 宿主组件：消费组件挂在 <KeepAlive> 内，动态显隐触发激活钩子 */
  function mountInKeepAlive(
    periodic: () => void,
    intervalMs = 1000,
    options?: Parameters<typeof useKeepAlivePolling<() => void>>[2],
  ) {
    const Consumer = defineComponent({
      setup() {
        useKeepAlivePolling(periodic, intervalMs, options);
        return () => h('div');
      },
    });
    const Host = defineComponent({
      props: { show: { type: Boolean, default: true } },
      setup(props) {
        return () => h(KeepAlive, () => (props.show ? h(Consumer) : null));
      },
    });
    return mount(Host);
  }

  it('激活时建立轮询：一个周期后触发，setup 阶段不触发', async () => {
    wrapper = mountInKeepAlive(() => task());
    expect(task).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1000);
    expect(task).toHaveBeenCalledTimes(1);
  });

  it('失活后停止，再激活恢复', async () => {
    wrapper = mountInKeepAlive(() => task());
    await vi.advanceTimersByTimeAsync(1000);
    expect(task).toHaveBeenCalledTimes(1);

    await wrapper.setProps({ show: false });
    await vi.advanceTimersByTimeAsync(5000);
    expect(task).toHaveBeenCalledTimes(1);

    await wrapper.setProps({ show: true });
    await vi.advanceTimersByTimeAsync(1000);
    expect(task).toHaveBeenCalledTimes(2);
  });

  it('args 选项按原样传参（静默轮询形态）', async () => {
    const silentTask = vi.fn((_silent: boolean) => undefined);
    wrapper = mountInKeepAlive(() => silentTask(true), 1000, { args: [true] });
    await vi.advanceTimersByTimeAsync(1000);
    expect(silentTask).toHaveBeenCalledWith(true);
  });

  it('keep-alive 首次挂载即触发 activated，无需显式启动', async () => {
    wrapper = mountInKeepAlive(() => task());
    await vi.advanceTimersByTimeAsync(3000);
    expect(task).toHaveBeenCalledTimes(3);
  });
});

describe('useKeepAlivePolling（KeepAlive 外消费选项）', () => {
  let scope: EffectScope;

  beforeEach(() => {
    vi.useFakeTimers();
    scope = effectScope();
  });

  afterEach(() => {
    scope.stop();
    vi.useRealTimers();
  });

  function run(options: Parameters<typeof useKeepAlivePolling<() => void>>[2]) {
    const task = vi.fn();
    scope.run(() => useKeepAlivePolling(task, 1000, options));
    return task;
  }

  it('默认 setup 不启动（视图先例：首启交 activated）', async () => {
    const task = run({});
    await vi.advanceTimersByTimeAsync(5000);
    expect(task).not.toHaveBeenCalled();
  });

  it('startOnSetup 同步启动轮询', async () => {
    const task = run({ startOnSetup: true });
    await vi.advanceTimersByTimeAsync(1000);
    expect(task).toHaveBeenCalledTimes(1);
  });

  it('stopOnDispose 在作用域销毁后停止', async () => {
    const task = run({ startOnSetup: true, stopOnDispose: true });
    await vi.advanceTimersByTimeAsync(1000);
    scope.stop();
    await vi.advanceTimersByTimeAsync(5000);
    expect(task).toHaveBeenCalledTimes(1);
  });
});
