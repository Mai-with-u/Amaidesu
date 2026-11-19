// AgentLeadControl.vue 挂载与交互结构守护测试
//
// 遵循项目 .template.test.ts 惯例（无 SFC 编译环境下用源码结构断言守护关键契约）：
// - 直播控制台（LiveObserver.vue）与 Agent 控制页（Agents.vue）均挂载 AgentLeadControl；
// - 组件经 useAgentLead 消费数据面与 WS 订阅，不直接手写 instanceof 错误分支；
// - 展示固定主播主控（streamer_only）提示、当前主控徽章与导演一键切权控件。

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const sfcSource = readFileSync(
  fileURLToPath(new URL('./AgentLeadControl.vue', import.meta.url)),
  'utf-8',
);
const liveObserverSource = readFileSync(
  fileURLToPath(new URL('../../views/LiveObserver.vue', import.meta.url)),
  'utf-8',
);
const agentsViewSource = readFileSync(
  fileURLToPath(new URL('../../views/Agents.vue', import.meta.url)),
  'utf-8',
);

describe('AgentLeadControl 挂载关系', () => {
  it('LiveObserver 顶栏引入并挂载了 AgentLeadControl', () => {
    expect(liveObserverSource).toContain(
      "import AgentLeadControl from '@/components/agents/AgentLeadControl.vue'",
    );
    expect(liveObserverSource).toContain('<AgentLeadControl />');
  });

  it('Agents 页左栏引入并挂载了块级 AgentLeadControl', () => {
    expect(agentsViewSource).toContain(
      "import AgentLeadControl from '@/components/agents/AgentLeadControl.vue'",
    );
    expect(agentsViewSource).toContain('<AgentLeadControl block />');
  });
});

describe('AgentLeadControl 契约守护', () => {
  it('委托 useAgentLead 承载数据与 WS 订阅，组件内不直接订阅且不手写 instanceof', () => {
    expect(sfcSource).toContain('useAgentLead');
    expect(sfcSource).not.toMatch(/subscribe\(/);
    expect(sfcSource).not.toContain('instanceof');
  });

  it('包含状态徽章、streamer_only 固定态提示与一键切权按钮渲染', () => {
    expect(sfcSource).toContain('v-if="available"');
    expect(sfcSource).toContain('streamer_only');
    expect(sfcSource).toContain('v-for="opt in switchOptions"');
    expect(sfcSource).toContain('@click="onSwitch(opt.target)"');
  });
});
