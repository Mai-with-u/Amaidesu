// 记忆页（Memory.vue）收敛为记忆账本后的源码结构守护测试
//
// 与既有 *.template.test.ts 同一做法（dashboard 测试环境是 node、编译不了 .vue，
// 组件行为由 components/memory/*.test.ts 的纯函数测试覆盖）：这里只守护两件事——
// 页面只剩承诺 / 下播整理 / 场次档案三块，以及旧画像与长期记忆的接口、类型、
// API 方法确实从 dashboard 里删干净了。删干净才有"旧后端端点下线不会留死代码"。

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const memorySource = readFileSync(fileURLToPath(new URL('./Memory.vue', import.meta.url)), 'utf-8');
const apiSource = readFileSync(fileURLToPath(new URL('../api/index.ts', import.meta.url)), 'utf-8');
const typesSource = readFileSync(
  fileURLToPath(new URL('../types/index.ts', import.meta.url)),
  'utf-8',
);

describe('记忆页收敛为账本', () => {
  it('页头说明这是她答应过的事与每场干了什么', () => {
    expect(memorySource).toContain('记忆账本');
    expect(memorySource).toContain('她答应过的事');
    expect(memorySource).toContain('每一场直播干了什么');
  });

  it('正文只有承诺 / 下播整理 / 场次档案三块，页头刷新一并重拉', () => {
    expect(memorySource).toContain('<CommitmentPanel');
    expect(memorySource).toContain('<SettlePanel');
    expect(memorySource).toContain('<ArchivePanel');
    expect(memorySource).toContain('commitmentPanel.value?.reload()');
    expect(memorySource).toContain('settlePanel.value?.reload()');
    expect(memorySource).toContain('archivePanel.value?.reload()');
    // 三块之外没有别的页面区块（旧的画像表 / 印象纠正对话框都随接口下线）
    expect(memorySource).not.toMatch(/<el-table/);
    expect(memorySource).not.toMatch(/<el-pagination/);
    expect(memorySource).not.toMatch(/<el-dialog/);
  });

  it('整理状态的“查看该场档案”仍然切场次并滚到档案面板', () => {
    expect(memorySource).toContain('@view-archive="openArchive"');
    expect(memorySource).toContain(':focus-session-id="archiveSessionId"');
    expect(memorySource).toContain("getElementById('ledger-archive')");
  });

  it('页面不再引用旧画像 / 长期记忆的 API、类型与组件', () => {
    expect(memorySource).not.toContain('memoryApi');
    expect(memorySource).not.toContain('listProfiles');
    expect(memorySource).not.toContain('getStats');
    expect(memorySource).not.toContain('ViewerProfileItem');
    expect(memorySource).not.toContain('LongMemoryPanel');
  });
});

describe('旧记忆接口与类型从 dashboard 下线', () => {
  it('api 客户端不再导出旧画像 / 统计 / 长期记忆端点', () => {
    expect(apiSource).not.toContain('export const memoryApi');
    expect(apiSource).not.toContain("'/memory/profiles'");
    expect(apiSource).not.toContain("'/memory/stats'");
    expect(apiSource).not.toContain('/memory/long-term');
  });

  it('类型定义不再保留旧画像与长期记忆的响应模型', () => {
    for (const typeName of [
      'ViewerProfileItem',
      'ViewerProfileListResponse',
      'ViewerProfileUpdatePayload',
      'LongMemoryResponse',
      'LongMemoryHistoryItem',
      'LongMemoryHistoryResponse',
      'LongMemorySnapshotResponse',
      'MemoryStatsResponse',
    ]) {
      expect(typesSource).not.toContain(typeName);
    }
  });

  it('记忆账本的端点与类型仍在（承诺 / 档案 / 整理状态不受影响）', () => {
    expect(apiSource).toContain('export const ledgerApi');
    expect(apiSource).toContain("'/memory/commitments'");
    expect(typesSource).toContain('export interface CommitmentItem');
    expect(typesSource).toContain('export interface ArchiveLineItem');
  });
});
