/**
 * 工具详情抽屉与调试调用：详情弹窗、入参表单（按 parameters schema 生成）、
 * 真实执行（与 Agent 同路径经 registry）、async 工具的 WS 结果回传覆盖、
 * 结果区视图合成、描述折叠展开。页面私有状态。
 * async 回传的 WS 订阅清理走 onScopeDispose（setup 中等价于组件卸载）。
 */

import { computed, nextTick, onScopeDispose, ref, watch } from 'vue';
import { ElMessage } from 'element-plus';
import { toolsApi } from '@/api';
import { useWebSocketStore } from '@/stores/websocket';
import type { ToolEntry, ToolInvokeResult, WebSocketMessage } from '@/types';

// vue-json-pretty data prop 的容许类型（包内 JSONDataType 的等价内联）
type JsonTreeData = string | number | boolean | unknown[] | Record<string, unknown> | null;

interface ResultView {
  kind: 'text' | 'image';
  /** JSON 视图的数据源（isJson 为 true 时有效） */
  jsonData: JsonTreeData;
  isJson: boolean;
  /** 非 JSON 文本原样内容 */
  text: string;
  /** 图像 base64 与 MIME（kind = 'image' 时有效） */
  data: string;
  mime: string;
}

export function useToolDetail() {
  const wsStore = useWebSocketStore();

  // 抽屉详情

  const detailOpen = ref(false);
  const activeTool = ref<ToolEntry | null>(null);

  const detailTitle = computed(() =>
    activeTool.value ? `工具详情 · ${activeTool.value.name}` : '工具详情',
  );

  const descRef = ref<HTMLElement | null>(null);
  const descClamped = ref(false);
  const descExpanded = ref(false);

  function measureDescClamp(): void {
    // scrollHeight > clientHeight = 两行放不下、出现了省略
    descClamped.value = descRef.value
      ? descRef.value.scrollHeight > descRef.value.clientHeight + 1
      : false;
  }

  function openDetail(row: ToolEntry) {
    activeTool.value = row;
    detailOpen.value = true;
    // 等 DOM 渲染后测描述是否溢出（决定"展开全文"入口是否显示）
    void nextTick(() => {
      descExpanded.value = false;
      measureDescClamp();
    });
  }

  function rowClass({ row }: { row: ToolEntry }): string {
    return row.disabled ? 'is-disabled-row' : '';
  }

  const paramEntries = computed(() => {
    if (!activeTool.value) return [];
    return Object.entries(activeTool.value.parameters ?? {})
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([key, spec]) => ({ key, spec }));
  });

  // 调试调用（与 Agent 同路径经 registry 真实执行）
  //
  // formModel 形状由工具的 parameters 决定：default 预填，boolean 落 false，
  // 数字型落 undefined（el-input-number 空态）、字符串落空串。
  const invoking = ref(false);
  const formModel = ref<Record<string, unknown>>({});
  const invokeResult = ref<ToolInvokeResult | null>(null);
  // async 工具受理后等待 WS tool.result 回传：true 期间监听 asyncEventName
  const waitingAsyncResult = ref(false);
  const asyncEventName = ref('');

  const JSON_PARAM_PLACEHOLDER = 'JSON 对象/数组，如 {"k": 1}';

  const isInternalTool = computed(() => {
    const cat = activeTool.value?.category;
    return cat === 'framework' || cat === 'game';
  });

  function resetInvokeState() {
    const next: Record<string, unknown> = {};
    for (const [key, spec] of Object.entries(activeTool.value?.parameters ?? {})) {
      if (spec.type === 'json') {
        // JSON 参数以文本承载：default 对象序列化预填，提交时解析回值
        next[key] =
          spec.default !== undefined && spec.default !== null
            ? JSON.stringify(spec.default, null, 2)
            : '';
      } else if (spec.default !== undefined && spec.default !== null) {
        next[key] = spec.default;
      } else if (spec.type === 'boolean') next[key] = false;
      else if (spec.type === 'integer' || spec.type === 'number') next[key] = undefined;
      else next[key] = '';
    }
    formModel.value = next;
    invokeResult.value = null;
    waitingAsyncResult.value = false;
    asyncEventName.value = '';
  }

  watch(activeTool, resetInvokeState);

  // 工具描述折叠/展开：默认两行省略，溢出时点击或点"展开全文"看全文

  async function toggleDesc(): Promise<void> {
    if (!descClamped.value && !descExpanded.value) return;
    descExpanded.value = !descExpanded.value;
    if (!descExpanded.value) {
      // 收起后重测：窗口尺寸变化可能已不再溢出
      await nextTick();
      measureDescClamp();
    }
  }

  function extractInvokeDetail(err: unknown): string {
    // axios 错误：后端 400/404 返回 {detail: "..."}，穿透 axios 默认 message
    const ax = err as { response?: { data?: { detail?: string } } };
    return ax?.response?.data?.detail ?? (err instanceof Error ? err.message : '调用失败');
  }

  async function onInvoke() {
    const tool = activeTool.value;
    if (!tool || invoking.value) return;
    // 组装 arguments：json 参数解析文本为值（MCP 复杂参数），其余原样透传
    const args: Record<string, unknown> = {};
    for (const [key, spec] of Object.entries(tool.parameters ?? {})) {
      const raw = formModel.value[key];
      if (spec.type === 'json') {
        const text = typeof raw === 'string' ? raw.trim() : '';
        if (!text) {
          if (spec.required) {
            ElMessage.warning(`必填参数 ${key} 未填写`);
            return;
          }
          continue;
        }
        try {
          args[key] = JSON.parse(text);
        } catch {
          ElMessage.warning(`参数 ${key} 不是合法 JSON`);
          return;
        }
        continue;
      }
      if (spec.required && (raw === undefined || raw === null || raw === '')) {
        ElMessage.warning(`必填参数 ${key} 未填写`);
        return;
      }
      args[key] = raw;
    }
    invoking.value = true;
    try {
      const resp = await toolsApi.invoke(tool.full_name, args);
      invokeResult.value = resp.data;
      if (tool.kind === 'async') {
        waitingAsyncResult.value = true;
        asyncEventName.value = tool.result_event ?? `tool.result.${tool.full_name}`;
      }
    } catch (e) {
      ElMessage.error(extractInvokeDetail(e));
    } finally {
      invoking.value = false;
    }
  }

  // WS 回传（tool.result.<full_name>）覆盖受理回执（async 工具专用）
  function handleToolResultMessage(msg: WebSocketMessage): void {
    const tool = activeTool.value;
    if (!tool || !waitingAsyncResult.value || msg.type !== asyncEventName.value) return;
    const d = msg.data;
    const name = typeof d.tool_name === 'string' ? d.tool_name : '';
    if (name && name !== tool.full_name && name !== tool.name) return;
    waitingAsyncResult.value = false;
    invokeResult.value = {
      success: d.status === 'success',
      content: '',
      blocks: [],
      error_message: typeof d.error_message === 'string' ? d.error_message : '',
      structured_content: d.result ?? null,
      duration_ms: 0,
      timestamp_ms: typeof d.timestamp_ms === 'number' ? d.timestamp_ms : 0,
    };
  }

  // 结果区视图合成
  //
  // ToolExecutionResult 的 content / blocks / structured_content 三处可能携带
  // 同源内容（如 MCP mapper 把同一段 JSON 同时填进三处），按规范化形态去重；
  // 能解析为 JSON 的文本走 vue-json-pretty 树渲染（同 LLM 历史详情页约定），
  // 其余原样。

  function normalizeForResult(t: string): string {
    try {
      return JSON.stringify(JSON.parse(t));
    } catch {
      return t.trim();
    }
  }

  function tryParseJson(t: string): { ok: boolean; value: JsonTreeData } {
    try {
      return { ok: true, value: JSON.parse(t) as JsonTreeData };
    } catch {
      return { ok: false, value: null };
    }
  }

  const resultViews = computed<ResultView[]>(() => {
    const result = invokeResult.value;
    if (!result) return [];
    const views: ResultView[] = [];
    const seen = new Set<string>();
    const pushText = (raw: string) => {
      if (!raw || !raw.trim() || seen.has(normalizeForResult(raw))) return;
      seen.add(normalizeForResult(raw));
      const parsed = tryParseJson(raw);
      if (parsed.ok) {
        views.push({
          kind: 'text',
          jsonData: parsed.value,
          isJson: true,
          text: '',
          data: '',
          mime: '',
        });
      } else {
        views.push({ kind: 'text', jsonData: null, isJson: false, text: raw, data: '', mime: '' });
      }
    };
    pushText(result.content);
    for (const block of result.blocks) {
      if (block.kind === 'image' && block.data) {
        views.push({
          kind: 'image',
          jsonData: null,
          isJson: false,
          text: '',
          data: block.data,
          mime: block.mime_type,
        });
      } else {
        pushText(block.text);
      }
    }
    if (result.structured_content !== null && result.structured_content !== undefined) {
      pushText(JSON.stringify(result.structured_content));
    }
    return views;
  });

  wsStore.subscribe(handleToolResultMessage);

  onScopeDispose(() => {
    wsStore.unsubscribe(handleToolResultMessage);
  });

  return {
    detailOpen,
    activeTool,
    detailTitle,
    openDetail,
    rowClass,
    paramEntries,
    invoking,
    formModel,
    invokeResult,
    waitingAsyncResult,
    asyncEventName,
    JSON_PARAM_PLACEHOLDER,
    isInternalTool,
    descRef,
    descClamped,
    descExpanded,
    toggleDesc,
    onInvoke,
    resultViews,
  };
}
