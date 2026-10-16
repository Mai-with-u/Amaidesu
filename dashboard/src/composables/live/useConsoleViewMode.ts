/**
 * 直播控制台视图档位：完整（运营全量）/ 简洁（纯浏览时间线）。
 *
 * 页面私有状态，走 composable 而非 Pinia——档位只属于当前页签，没有跨页
 * 共享语义。localStorage 持久化：简洁档常用于挂第二屏纯观看，刷新或重开
 * 不丢档位；读不到或值非法时回落完整档（运营操作不会因脏数据被藏住）。
 * 无定时器/订阅，无 keep-alive 激活态语义。
 */

import { computed, ref, watch } from 'vue';

export type ConsoleViewMode = 'full' | 'simple';

const STORAGE_KEY = 'live-console-view-mode';

function readStoredMode(): ConsoleViewMode {
  try {
    return localStorage.getItem(STORAGE_KEY) === 'simple' ? 'simple' : 'full';
  } catch {
    // 隐私模式 / node 单测等 localStorage 不可用：当作未选过，回落完整档
    return 'full';
  }
}

export function useConsoleViewMode() {
  const viewMode = ref<ConsoleViewMode>(readStoredMode());

  watch(viewMode, mode => {
    try {
      localStorage.setItem(STORAGE_KEY, mode);
    } catch {
      // 存不下只丢跨刷新记忆，本次档位照常生效
    }
  });

  /** 简洁档藏起全部运营操作（干预输入/任务条/生命周期开关等），页面显隐统一走它 */
  const isFullMode = computed(() => viewMode.value === 'full');

  return { viewMode, isFullMode };
}
