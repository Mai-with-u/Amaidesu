// 承诺账本面板（CommitmentPanel.vue）渲染与行操作语义的源码结构守护测试
//
// 与既有 *.template.test.ts 同一做法：dashboard/vitest.config.ts 是 node 环境、
// 没有装配 @vitejs/plugin-vue，测试进程编译不了 .vue 单文件组件（挂载级交互
// 测试要改 vitest 配置，超出本次 dashboard/src/** 的改动范围），因此把渲染与
// 交互契约断言在源码结构上；状态标签、对谁、筛选换算等展示逻辑由
// ledgerView.test.ts 做行为级覆盖。

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const panelSource = readFileSync(
  fileURLToPath(new URL('./CommitmentPanel.vue', import.meta.url)),
  'utf-8',
);
const settleSource = readFileSync(
  fileURLToPath(new URL('./SettlePanel.vue', import.meta.url)),
  'utf-8',
);
const memorySource = readFileSync(
  fileURLToPath(new URL('../../views/Memory.vue', import.meta.url)),
  'utf-8',
);

describe('承诺列表渲染契约', () => {
  it('一行显示标题、她说过的原话、对谁、游戏与状态标签', () => {
    expect(panelSource).toContain('{{ item.what }}');
    expect(panelSource).toContain('{{ item.quote }}');
    expect(panelSource).toContain('commitmentTargetLabel(item)');
    expect(panelSource).toContain('v-if="item.game"');
    expect(panelSource).toContain('commitmentStatusLabel(item.status)');
  });

  it('登记时刻与登记场次可追溯，手工登记标明没有原话', () => {
    expect(panelSource).toContain('formatDateTimeCn(item.created_at_ms)');
    expect(panelSource).toContain('item.created_session_id');
    expect(panelSource).toContain('手工登记，没有原话');
  });

  it('四档状态筛选，切换后从第一页重拉', () => {
    for (const label of ['全部', '未了结', '已了结', '误记']) {
      expect(panelSource).toContain(`label: '${label}'`);
    }
    expect(panelSource).toContain('offset: 0');
    expect(panelSource).toContain('commitmentStatusQuery(statusFilter.value)');
  });

  it('“加载更多”按已有条数续拉并追加，不重置列表', () => {
    expect(panelSource).toContain('offset: items.value.length');
    expect(panelSource).toContain('items.value = [...items.value, ...response.data.items]');
  });

  it('手建对话框限制标题长度，失败留在对话框内提示', () => {
    expect(panelSource).toContain('ledgerApi.createCommitment(');
    expect(panelSource).toContain('maxlength="60"');
    expect(panelSource).toContain("createError.value = getApiErrorMessage(error, '登记失败')");
  });

  it('标了结 / 判误记 / 删除走各自端点，删除前二次确认', () => {
    expect(panelSource).toContain('ledgerApi.closeCommitment(item.id');
    expect(panelSource).toContain('ledgerApi.voidCommitment(item.id)');
    expect(panelSource).toContain('ledgerApi.deleteCommitment(item.id)');
    expect(panelSource).toContain('await confirmAction(');
  });

  it('行操作失败（含 409：已了结/判误记/不存在）只在该行提示，不整页报错', () => {
    expect(panelSource).toContain('rowErrors.value = {');
    expect(panelSource).toContain('[item.id]: getApiErrorMessage(error,');
    expect(panelSource).toContain('v-if="rowErrors[item.id]"');
    // 行内提示是唯一的失败出口：面板本身不用整页 ElMessage.error
    expect(panelSource).not.toContain('ElMessage.error');
  });
});

describe('记忆页账本区接入', () => {
  it('承诺 / 下播整理 / 场次档案三个面板挂在记忆页', () => {
    expect(memorySource).toContain('<CommitmentPanel');
    expect(memorySource).toContain('<SettlePanel');
    expect(memorySource).toContain('<ArchivePanel');
  });

  it('页面头刷新连同三个账本面板一起重拉', () => {
    expect(memorySource).toContain('commitmentPanel.value?.reload()');
    expect(memorySource).toContain('settlePanel.value?.reload()');
    expect(memorySource).toContain('archivePanel.value?.reload()');
  });

  it('整理状态的“查看该场档案”切到对应场次并滚到档案面板', () => {
    expect(memorySource).toContain('@view-archive="openArchive"');
    expect(memorySource).toContain(':focus-session-id="archiveSessionId"');
    expect(memorySource).toContain("getElementById('ledger-archive')");
  });

  it('整理状态逐条显示失败原因与未登记清单的原话/原因，失败场次可重新整理', () => {
    expect(settleSource).toContain('ledgerApi.getSettleState()');
    expect(settleSource).toContain('row.settle_report?.failure_reason');
    expect(settleSource).toContain('row.settle_report.unregistered');
    expect(settleSource).toContain('{{ item.reason }}');
    expect(settleSource).toContain('“{{ item.quote }}”');
    expect(settleSource).toContain('ledgerApi.resettle(row.live_session_id)');
    expect(settleSource).toContain('await confirmAction(');
    expect(settleSource).toContain("emit('view-archive', row.live_session_id)");
  });
});
