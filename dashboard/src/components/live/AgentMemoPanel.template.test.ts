// AgentMemoPanel.vue 备忘录数据流语义的源码结构守护测试
//
// 面板只服务游戏 Agent（主播备忘录能力随长期记忆一起下线，不再有来源过滤与
// 主播快照）。数据流原为 useAgentMemo 组合式（有挂载级行为测试：快照、事件
// 定向刷新、轮询兜底、失败静默），单消费者就地内联后，因项目无组件挂载测试
// 环境（无 jsdom 环境下的 vue 插件，无法编译 .vue 单文件组件），按既有惯例
// 降级为源码结构断言，守护数据流的关键不变量。轮询启停的行为级覆盖在
// useKeepAlivePolling.test.ts（本面板经该原语轮询）。

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const sfcSource = readFileSync(
  fileURLToPath(new URL('./AgentMemoPanel.vue', import.meta.url)),
  'utf-8',
);

describe('AgentMemoPanel 数据流语义', () => {
  it('挂载即拉 minecraft 快照（单 Agent，不再拉主播快照）', () => {
    expect(sfcSource).toContain("agentsApi.getAgentMemo('minecraft')");
    expect(sfcSource).toContain('void refreshMemo();');
    expect(sfcSource).not.toContain("getAgentMemo('streamer')");
  });

  it('tool.result 的 minecraft 三工具名触发刷新', () => {
    for (const tool of ['minecraft_notebook', 'minecraft_todo', 'minecraft_report']) {
      expect(sfcSource).toContain(`'${tool}'`);
    }
    expect(sfcSource).toContain('void refreshMemo();');
  });

  it('30 秒轮询兜底走 useKeepAlivePolling（激活态启停 + 销毁兜底）', () => {
    expect(sfcSource).toContain('const MEMO_POLL_INTERVAL_MS = 30000;');
    expect(sfcSource).toContain(
      'useKeepAlivePolling(() => refreshMemo(), MEMO_POLL_INTERVAL_MS, {',
    );
  });

  it('快照失败静默保留旧值（catch 空处理，无错误弹窗调用）', () => {
    const catches = sfcSource.match(new RegExp(/} catch \{\n {4}\/\/ [^\n]*\n {2}\}\n/g)) ?? [];
    expect(catches.length).toBeGreaterThanOrEqual(1);
    expect(sfcSource).not.toContain('ElMessage');
  });
});

describe('AgentMemoPanel 只服务游戏 Agent', () => {
  it('没有主播分支：不再订阅 streamer.notebook.changed、不渲染主播节', () => {
    expect(sfcSource).not.toContain('streamer.notebook.changed');
    expect(sfcSource).not.toContain('主播');
    expect(sfcSource).not.toContain('直播 · 备忘录');
  });

  it('不再有来源过滤 prop（面板固定只显示 minecraft 三节）', () => {
    expect(sfcSource).not.toContain('defineProps');
    expect(sfcSource).not.toContain('props.target');
    expect(sfcSource).not.toContain('showStreamer');
    expect(sfcSource).not.toContain('is-single');
  });
});
