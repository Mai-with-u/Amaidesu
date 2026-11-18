/**
 * 顶栏派生徽章：决策管线阶段 chip、观众模拟器模式 chip。
 *
 * 前者从 events store 只读投影（取最近一条对应事件）；观众模拟器模式不走事件流，
 * 挂载时拉一次状态接口（观众模拟器启停在别页操作，控制台只观测）。
 *
 * 环节横幅不在这儿——它按场次取数（useRundownConsole）：事件流里的
 * rundown.changed 不带场次范围，回看别场时会对错场。
 */

import { computed, ref } from 'vue';
import { storeToRefs } from 'pinia';
import { useEventsStore } from '@/stores';
import { simulatorApi } from '@/api';
import { bool, isRecord, str } from '@/utils/guards';
import { STAGE_LABEL } from '@/utils/liveFeed';

export function useLiveStatus() {
  const { events } = storeToRefs(useEventsStore());

  // 顶栏徽章：决策管线阶段 + 观众模拟器模式

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
            ? '观众模拟器 · 生成中'
            : '观众模拟器 · 生成待启'
          : mode === 'replay'
            ? running
              ? '观众模拟器 · 回放中'
              : '观众模拟器 · 回放待启'
            : '观众模拟器未启用';
      simulatorChip.value = { label, on: mode !== 'off' && running };
    } catch {
      simulatorChip.value = null;
    }
  }

  return { stageChip, simulatorChip, loadSimulatorStatus };
}
