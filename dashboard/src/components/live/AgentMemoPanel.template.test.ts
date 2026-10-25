// AgentMemoPanel.vue 备忘录数据流语义的源码结构守护测试
//
// 面板数据流原为 useAgentMemo 组合式（有挂载级行为测试：双快照、事件定向
// 刷新、轮询兜底、失败静默），单消费者就地内联后，因项目无组件挂载测试
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
  it('挂载即拉主播与 minecraft 双快照', () => {
    expect(sfcSource).toContain("agentsApi.getAgentMemo('streamer')");
    expect(sfcSource).toContain("agentsApi.getAgentMemo('minecraft')");
    expect(sfcSource).toContain('void refreshMemo();');
  });

  it('streamer.notebook.changed 定向刷新主播侧（不重拉 minecraft）', () => {
    expect(sfcSource).toContain("'streamer.notebook.changed'");
    expect(sfcSource).toContain('void refreshStreamers();');
  });

  it('tool.result 的 minecraft 三工具名触发 minecraft 侧定向刷新', () => {
    for (const tool of ['minecraft_notebook', 'minecraft_todo', 'minecraft_report']) {
      expect(sfcSource).toContain(`'${tool}'`);
    }
    expect(sfcSource).toContain('void refreshMinecraft();');
  });

  it('30 秒轮询兜底走 useKeepAlivePolling（激活态启停 + 销毁兜底）', () => {
    expect(sfcSource).toContain('const MEMO_POLL_INTERVAL_MS = 30000;');
    expect(sfcSource).toContain(
      'useKeepAlivePolling(() => refreshMemo(), MEMO_POLL_INTERVAL_MS, {',
    );
  });

  it('快照失败静默保留旧值（catch 空处理，无错误弹窗调用）', () => {
    const catches = sfcSource.match(new RegExp(/} catch \{\n {4}\/\/ [^\n]*\n {2}\}\n/g)) ?? [];
    expect(catches.length).toBeGreaterThanOrEqual(2);
    expect(sfcSource).not.toContain('ElMessage');
  });
});
