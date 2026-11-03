// 场次档案行（ArchiveLineList.vue）渲染与删除交互的源码结构守护测试
//
// 与既有 *.template.test.ts 同一做法（vitest 配置为 node 环境、未装配
// @vitejs/plugin-vue，挂载级测试需改 dashboard/vitest.config.ts，超出本次
// dashboard/src/** 的改动范围）：渲染与删除交互断言在源码结构上，覆盖列表
// 组件本身、记忆页档案面板（选场次 / 二次确认 / 原地重拉）与场次复盘页的
// 档案块接入。

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const lineListSource = readFileSync(
  fileURLToPath(new URL('./ArchiveLineList.vue', import.meta.url)),
  'utf-8',
);
const archivePanelSource = readFileSync(
  fileURLToPath(new URL('./ArchivePanel.vue', import.meta.url)),
  'utf-8',
);
const sessionReviewSource = readFileSync(
  fileURLToPath(new URL('../../views/SessionReview.vue', import.meta.url)),
  'utf-8',
);

describe('档案行渲染契约', () => {
  it('一行显示标题与原话，删除按钮由 deletable 控制并抛出删除意图', () => {
    expect(lineListSource).toContain('{{ line.text }}');
    expect(lineListSource).toContain('“{{ line.quote }}”');
    expect(lineListSource).toContain('v-if="deletable"');
    expect(lineListSource).toContain("emit('delete', line)");
  });

  it('没有档案行时显示空态提示', () => {
    expect(lineListSource).toContain('v-else class="empty-hint">{{ emptyText }}');
  });
});

describe('记忆页档案面板交互契约', () => {
  it('候选是已结束的正式场，选中后拉该场档案行', () => {
    expect(archivePanelSource).toContain("liveSessionsApi.list({ kind: 'live', limit: 60 })");
    expect(archivePanelSource).toContain('item.ended_at_ms !== null');
    expect(archivePanelSource).toContain('ledgerApi.listArchive(selectedId.value)');
  });

  it('删单行先二次确认，成功后原地重拉这一场（不整页刷新）', () => {
    expect(archivePanelSource).toContain('await confirmAction(');
    expect(archivePanelSource).toContain('await ledgerApi.deleteArchiveLine(line.id)');
    expect(archivePanelSource).toContain('await loadLines()');
  });

  it('整理状态面板能指定要看的场次（focusSessionId）', () => {
    expect(archivePanelSource).toContain('focusSessionId');
    expect(archivePanelSource).toContain('selectSession(liveSessionId)');
  });
});

describe('场次复盘页档案块接入', () => {
  it('单场选择时拉取本场档案行，随复盘一起加载', () => {
    expect(sessionReviewSource).toContain('ledgerApi.listArchive(sessionId)');
    expect(sessionReviewSource).toContain('await loadArchive();');
  });

  it('档案块渲染档案行，删单行后原地刷新', () => {
    expect(sessionReviewSource).toContain('<ArchiveLineList');
    expect(sessionReviewSource).toContain(':items="archiveLines"');
    expect(sessionReviewSource).toContain('@delete="removeArchiveLine"');
    expect(sessionReviewSource).toContain('await confirmAction(');
    expect(sessionReviewSource).toContain('await ledgerApi.deleteArchiveLine(line.id)');
  });
});
