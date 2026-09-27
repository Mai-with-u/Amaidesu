/**
 * 顶栏与环节横幅的派生徽章：决策管线阶段 chip、模拟器模式 chip、当前环节横幅。
 *
 * 前两者从 events store 只读投影（取最近一条对应事件）；模拟器模式不走事件流，
 * 挂载时拉一次状态接口（模拟器启停在别页操作，控制台只观测）。
 */

import { computed, ref } from 'vue';
import { storeToRefs } from 'pinia';
import { useEventsStore } from '@/stores';
import { simulatorApi } from '@/api';
import { STAGE_LABEL, bool, isRecord, str } from '@/utils/liveFeed';

interface RundownBanner {
  order: number;
  label: string;
  actionLabel: string;
  note: string;
  startLabel: string;
  expectedLabel: string;
  changedAtMs: number;
}

export function useLiveStatus() {
  const { events } = storeToRefs(useEventsStore());

  // 当前环节横幅：取最近一条 rundown.changed

  const rundownBanner = computed<RundownBanner | null>(() => {
    const list = events.value;
    for (let i = list.length - 1; i >= 0; i -= 1) {
      const event = list[i];
      if (event.type !== 'rundown.changed') continue;
      const data = isRecord(event.data) ? event.data : {};
      const by = typeof data.by === 'string' ? data.by : 'agent';
      const changedAtMs = typeof data.at_ms === 'number' ? data.at_ms : null;
      const index = typeof data.index === 'number' ? data.index : 0;
      return {
        order: index + 1,
        label: str(data.segment_title) || '未命名环节',
        actionLabel: by === 'human' ? '手动切换' : by === 'system' ? '系统切换' : 'Agent 切换',
        note: '',
        startLabel: '',
        expectedLabel: '',
        changedAtMs: changedAtMs ?? event.timestamp_ms,
      };
    }
    return null;
  });

  // 顶栏徽章：决策管线阶段 + 模拟器模式

  const stageChip = computed<{ label: string; running: boolean; detail: string } | null>(() => {
    const list = events.value;
    for (let i = list.length - 1; i >= 0; i -= 1) {
      const event = list[i];
      if (event.type !== 'streamer.stage') continue;
      const data = isRecord(event.data) ? event.data : {};
      const stage = str(data.stage);
      const running = str(data.agent_state) === 'running';
      return {
        label: STAGE_LABEL[stage] ?? stage,
        running,
        detail: str(data.detail),
      };
    }
    return null;
  });

  const simulatorChip = ref<{ label: string; on: boolean } | null>(null);

  async function loadSimulatorStatus(): Promise<void> {
    try {
      const response = await simulatorApi.getStatus();
      const mode = str(response.data.mode) || 'off';
      const running = bool(response.data.is_running);
      const label =
        mode === 'generate'
          ? running
            ? '模拟器 · 生成中'
            : '模拟器 · 生成待启'
          : mode === 'replay'
            ? running
              ? '模拟器 · 回放中'
              : '模拟器 · 回放待启'
            : '模拟器未启用';
      simulatorChip.value = { label, on: mode !== 'off' && running };
    } catch {
      simulatorChip.value = null;
    }
  }

  return { rundownBanner, stageChip, simulatorChip, loadSimulatorStatus };
}
