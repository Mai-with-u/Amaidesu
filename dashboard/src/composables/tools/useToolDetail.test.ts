// useToolDetail.ts 工具详情与调试调用的测试：详情抽屉状态派生、入参表单
// 预填（json/boolean/integer/string/default 各类型）、调试调用组装（json
// 解析、必填校验）、async 工具的 WS 回传覆盖、结果区视图合成去重。

import { effectScope, type EffectScope } from 'vue';
import { describe, expect, it, vi, beforeEach } from 'vitest';

import { toolsApi } from '@/api';
import { ElMessage } from 'element-plus';
import { useToolDetail } from '@/composables/tools/useToolDetail';
import type { ToolEntry, ToolInvokeResult, WebSocketMessage } from '@/types';

const subscribers: Array<(msg: WebSocketMessage) => void> = [];

vi.mock('@/api', async importOriginal => ({
  ...(await importOriginal<object>()),
  toolsApi: {
    invoke: vi.fn(),
  },
}));

vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

vi.mock('@/stores/websocket', () => ({
  useWebSocketStore: () => ({
    subscribe: (fn: (msg: WebSocketMessage) => void) => {
      subscribers.push(fn);
    },
    unsubscribe: vi.fn(),
  }),
}));

function tool(partial: Partial<ToolEntry>): ToolEntry {
  return {
    name: 'wave',
    full_name: `vts_${partial.name ?? 'wave'}`,
    parameters: {},
    provider: 'vts',
    category: 'avatar',
    kind: 'sync',
    disabled: false,
    ...partial,
  } as ToolEntry;
}

describe('useToolDetail 详情派生', () => {
  const detail = useToolDetail();

  it('openDetail：落位选中项并拼详情标题', () => {
    detail.openDetail(tool({ name: 'wave' }));
    expect(detail.activeTool.value?.name).toBe('wave');
    expect(detail.detailOpen.value).toBe(true);
    expect(detail.detailTitle.value).toBe('工具详情 · wave');
    expect(detail.detailTitle.value).not.toBe('工具详情');
  });

  it('rowClass：停用行加类；paramEntries 按 key 排序；isInternalTool 判定', async () => {
    detail.openDetail(
      tool({
        name: 'b_tool',
        category: 'framework',
        disabled: true,
        parameters: {
          z_param: { type: 'string', required: false },
          a_param: { type: 'integer', required: true },
        },
      }),
    );
    await vi.waitFor(() => {
      expect(detail.formModel.value).toBeDefined();
    });
    expect(detail.rowClass({ row: detail.activeTool.value! })).toBe('is-disabled-row');
    expect(detail.paramEntries.value.map(e => e.key)).toEqual(['a_param', 'z_param']);
    expect(detail.isInternalTool.value).toBe(true);
  });
});

describe('useToolDetail 入参预填', () => {
  let scope: EffectScope | null = null;

  function setup(t: ToolEntry): ReturnType<typeof useToolDetail> {
    scope?.stop();
    scope = effectScope();
    let d!: ReturnType<typeof useToolDetail>;
    scope.run(() => {
      d = useToolDetail();
      d.openDetail(t);
    });
    return d;
  }

  it('各类型默认值：json 序列化 default、boolean false、数字 undefined、字符串空串', async () => {
    const d = setup(
      tool({
        parameters: {
          opts: { type: 'json', required: false, default: { k: 1 } },
          flag: { type: 'boolean', required: false },
          count: { type: 'integer', required: true },
          label: { type: 'string', required: false, default: 'hi' },
          empty: { type: 'string', required: false },
        },
      }),
    );
    await vi.waitFor(() => expect(d.formModel.value.opts).toBe('{\n  "k": 1\n}'));
    expect(d.formModel.value.flag).toBe(false);
    expect(d.formModel.value.count).toBeUndefined();
    expect(d.formModel.value.label).toBe('hi');
    expect(d.formModel.value.empty).toBe('');
  });
});

describe('useToolDetail 调试调用', () => {
  let scope: EffectScope | null = null;

  function setup(t: ToolEntry): ReturnType<typeof useToolDetail> {
    scope?.stop();
    scope = effectScope();
    let d!: ReturnType<typeof useToolDetail>;
    scope.run(() => {
      d = useToolDetail();
      d.openDetail(t);
    });
    return d;
  }

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('组装 arguments：json 文本解析为值，其余透传', async () => {
    vi.mocked(toolsApi.invoke).mockResolvedValue({
      data: {
        success: true,
        content: 'ok',
        blocks: [],
        error_message: '',
        duration_ms: 1,
        timestamp_ms: 1,
      },
    } as never);
    const d = setup(
      tool({
        parameters: {
          payload: { type: 'json', required: true },
          count: { type: 'integer', required: false },
        },
      }),
    );
    await vi.waitFor(() => expect(d.formModel.value.payload).toBeDefined());
    d.formModel.value.payload = '{"k": 2}';
    d.formModel.value.count = 3;
    await d.onInvoke();

    expect(toolsApi.invoke).toHaveBeenCalledWith('vts_wave', { payload: { k: 2 }, count: 3 });
    expect(d.invokeResult.value?.success).toBe(true);
  });

  it('必填 json 参数为空：警告并中止，不调接口', async () => {
    const d = setup(tool({ parameters: { payload: { type: 'json', required: true } } }));
    await vi.waitFor(() => expect(d.formModel.value.payload).toBe(''));
    d.formModel.value.payload = '  ';
    await d.onInvoke();

    expect(ElMessage.warning).toHaveBeenCalledWith('必填参数 payload 未填写');
    expect(toolsApi.invoke).not.toHaveBeenCalled();
  });

  it('json 参数非法文本：警告并中止', async () => {
    const d = setup(tool({ parameters: { payload: { type: 'json', required: true } } }));
    await vi.waitFor(() => expect(d.formModel.value.payload).toBe(''));
    d.formModel.value.payload = '{broken';
    await d.onInvoke();

    expect(ElMessage.warning).toHaveBeenCalledWith('参数 payload 不是合法 JSON');
    expect(toolsApi.invoke).not.toHaveBeenCalled();
  });

  it('async 工具：受理后等待 WS 回传，事件名取 result_event 或按 full_name 合成', async () => {
    vi.mocked(toolsApi.invoke).mockResolvedValue({
      data: {
        success: true,
        content: '受理',
        blocks: [],
        error_message: '',
        duration_ms: 1,
        timestamp_ms: 1,
      },
    } as never);
    const d = setup(tool({ name: 'adv', kind: 'async' }));
    await vi.waitFor(() => expect(d.formModel.value).toBeDefined());
    await d.onInvoke();

    expect(d.waitingAsyncResult.value).toBe(true);
    expect(d.asyncEventName.value).toBe('tool.result.vts_adv');

    // WS 回传覆盖受理回执
    subscribers[subscribers.length - 1]({
      type: 'tool.result.vts_adv',
      timestamp_ms: 9,
      data: { tool_name: 'vts_adv', status: 'success', result: { done: true }, timestamp_ms: 9 },
    });
    expect(d.waitingAsyncResult.value).toBe(false);
    expect(d.invokeResult.value?.success).toBe(true);
    expect(d.invokeResult.value?.structured_content).toEqual({ done: true });
  });

  it('WS 回传：事件名不匹配或工具名不匹配时忽略', async () => {
    vi.mocked(toolsApi.invoke).mockResolvedValue({
      data: {
        success: true,
        content: '受理',
        blocks: [],
        error_message: '',
        duration_ms: 1,
        timestamp_ms: 1,
      },
    } as never);
    const d = setup(tool({ name: 'adv', kind: 'async', result_event: 'tool.result.custom' }));
    await vi.waitFor(() => expect(d.waitingAsyncResult.value).toBe(false));
    await d.onInvoke();
    expect(d.asyncEventName.value).toBe('tool.result.custom');

    const before = d.invokeResult.value;
    const handler = subscribers[subscribers.length - 1];
    handler({
      type: 'tool.result.other',
      timestamp_ms: 1,
      data: { tool_name: 'vts_adv', status: 'success' },
    });
    handler({
      type: 'tool.result.custom',
      timestamp_ms: 1,
      data: { tool_name: 'other_tool', status: 'success' },
    });
    expect(d.invokeResult.value).toBe(before);
  });
});

describe('useToolDetail 结果视图合成', () => {
  let scope: EffectScope | null = null;

  function viewsOf(
    result: Partial<ToolInvokeResult>,
  ): ReturnType<typeof useToolDetail>['resultViews']['value'] {
    scope?.stop();
    scope = effectScope();
    let d!: ReturnType<typeof useToolDetail>;
    scope.run(() => {
      d = useToolDetail();
      d.openDetail(tool({}));
    });
    d.invokeResult.value = {
      success: true,
      content: '',
      blocks: [],
      error_message: '',
      duration_ms: 0,
      timestamp_ms: 0,
      ...result,
    } as ToolInvokeResult;
    return d.resultViews.value;
  }

  it('content 与 structured_content 同源内容去重，JSON 文本走树渲染', () => {
    const views = viewsOf({
      content: '{"a":1}',
      structured_content: { a: 1 },
    });
    expect(views).toHaveLength(1);
    expect(views[0].isJson).toBe(true);
    expect(views[0].jsonData).toEqual({ a: 1 });
  });

  it('图像块独立成视图，非 JSON 文本原样保留', () => {
    const views = viewsOf({
      content: '纯文本',
      blocks: [{ kind: 'image', text: '', data: 'base64==', mime_type: 'image/png' }],
    });
    expect(views).toHaveLength(2);
    expect(views[0]).toMatchObject({ kind: 'text', isJson: false, text: '纯文本' });
    expect(views[1]).toMatchObject({ kind: 'image', data: 'base64==', mime: 'image/png' });
  });
});
