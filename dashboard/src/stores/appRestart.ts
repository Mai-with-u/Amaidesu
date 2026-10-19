/**
 * 应用重启的全局单例状态与流程：发重启请求 → 进度遮罩 → 健康检查循环。
 *
 * 遮罩 UI 挂在 AppLayout（全局唯一挂点），任何页面经 beginRestart() 触发
 * 都会点亮同一遮罩；健康检查失败可重试或整页刷新。定时器只服务于一次
 * 重启流程，流程终止（成功/失败）即清理；store 与应用壳同生命周期，
 * 不随页面卸载。
 */
import { ref } from 'vue';
import { defineStore } from 'pinia';
import { ElMessage } from 'element-plus';

import { configApi, systemApi } from '@/api';
import { getApiErrorMessage } from '@/utils/apiError';

export type RestartOverlayStatus = 'requesting' | 'restarting' | 'checking' | 'success' | 'failed';

export const useAppRestartStore = defineStore('app-restart', () => {
  const restarting = ref(false);
  const overlayVisible = ref(false);
  const overlayStatus = ref<RestartOverlayStatus>('requesting');
  const overlayProgress = ref(0);
  const overlayElapsed = ref(0);
  const overlayCheckAttempts = ref(0);
  const overlayMaxAttempts = 60;

  let progressTimer: ReturnType<typeof setInterval> | null = null;
  let elapsedTimer: ReturnType<typeof setInterval> | null = null;
  let healthTimer: ReturnType<typeof setInterval> | null = null;

  function clearAllTimers(): void {
    if (progressTimer !== null) {
      clearInterval(progressTimer);
      progressTimer = null;
    }
    if (elapsedTimer !== null) {
      clearInterval(elapsedTimer);
      elapsedTimer = null;
    }
    if (healthTimer !== null) {
      clearInterval(healthTimer);
      healthTimer = null;
    }
  }

  function startProgressAnimation(): void {
    progressTimer = setInterval(() => {
      if (overlayProgress.value < 90) overlayProgress.value += 1;
    }, 200);
    elapsedTimer = setInterval(() => {
      overlayElapsed.value += 1;
    }, 1000);
  }

  /** 健康检查循环：恢复可达即收尾，连续失败到上限置失败（可手动重试） */
  function pollHealth(): void {
    let attempts = 0;
    healthTimer = setInterval(async () => {
      attempts += 1;
      overlayCheckAttempts.value = attempts;
      try {
        await systemApi.getHealth();
        clearAllTimers();
        overlayProgress.value = 100;
        overlayStatus.value = 'success';
        setTimeout(() => {
          overlayVisible.value = false;
          restarting.value = false;
          ElMessage.success('服务重启完成');
        }, 1200);
      } catch {
        if (attempts >= overlayMaxAttempts) {
          clearAllTimers();
          overlayStatus.value = 'failed';
          restarting.value = false;
        }
      }
    }, 2000);
  }

  /** 触发重启并接管全局遮罩流程；调用方负责先行向用户确认 */
  async function beginRestart(): Promise<void> {
    restarting.value = true;
    overlayVisible.value = true;
    overlayStatus.value = 'requesting';
    overlayProgress.value = 0;
    overlayElapsed.value = 0;
    overlayCheckAttempts.value = 0;

    try {
      const response = await configApi.restart();
      if (!response.data.success) {
        overlayStatus.value = 'failed';
        ElMessage.error(`重启失败: ${response.data.message}`);
        restarting.value = false;
        return;
      }

      overlayStatus.value = 'restarting';
      startProgressAnimation();

      // 等后端退出后开始检查
      await new Promise(resolve => setTimeout(resolve, 3000));

      overlayStatus.value = 'checking';
      pollHealth();
    } catch (e) {
      clearAllTimers();
      overlayStatus.value = 'failed';
      ElMessage.error(`重启失败: ${getApiErrorMessage(e, '网络错误')}`);
      restarting.value = false;
    }
  }

  /** 健康检查失败后的手动重试 */
  function retryHealthCheck(): void {
    clearAllTimers();
    overlayStatus.value = 'checking';
    overlayCheckAttempts.value = 0;
    pollHealth();
  }

  return {
    restarting,
    overlayVisible,
    overlayStatus,
    overlayProgress,
    overlayElapsed,
    overlayCheckAttempts,
    overlayMaxAttempts,
    beginRestart,
    retryHealthCheck,
  };
});
