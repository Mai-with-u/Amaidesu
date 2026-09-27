/**
 * 工具熔断状态（WS tool.health.*）：实时接收熔断事件并就地更新工具条目，
 * 供行内徽标与 tooltip 展示。工具清单由 useToolCatalog 持有、参数注入。
 * WS 订阅清理走 onScopeDispose（setup 中等价于组件卸载）。
 */

import { onScopeDispose, type Ref } from 'vue';
import { useWebSocketStore } from '@/stores/websocket';
import type { ToolEntry, ToolHealth, ToolHealthEventData, WebSocketMessage } from '@/types';

export function useToolHealth(tools: Ref<ToolEntry[]>) {
  const wsStore = useWebSocketStore();

  function applyHealthUpdate(toolName: string, next: ToolHealth | null): void {
    const target = tools.value.find(t => t.name === toolName);
    if (!target) return;
    target.health = next;
  }

  function isToolHealthEventData(data: unknown): data is ToolHealthEventData {
    if (!data || typeof data !== 'object') return false;
    const d = data as Record<string, unknown>;
    return (
      typeof d.tool_name === 'string' &&
      typeof d.state === 'string' &&
      typeof d.timestamp_ms === 'number'
    );
  }

  function handleHealthMessage(msg: WebSocketMessage): void {
    if (!msg.type.startsWith('tool.health.')) return;
    if (!isToolHealthEventData(msg.data)) return;
    const payload = msg.data;
    if (payload.state === 'open') {
      applyHealthUpdate(payload.tool_name, {
        state: 'tripped',
        failure_count: payload.failure_count,
        last_error: payload.last_error,
        tripped_at_ms: payload.timestamp_ms,
      });
    } else if (payload.state === 'closed') {
      applyHealthUpdate(payload.tool_name, null);
    }
  }

  function healthTooltip(row: ToolEntry): string {
    const h = row.health;
    if (!h) return '';
    // el-tooltip 默认按纯文本渲染，\n 不会换行，用分号分隔两段信息
    return `${h.last_error}；连续失败 ${h.failure_count} 次`;
  }

  wsStore.subscribe(handleHealthMessage);

  onScopeDispose(() => {
    wsStore.unsubscribe(handleHealthMessage);
  });

  return { healthTooltip };
}
