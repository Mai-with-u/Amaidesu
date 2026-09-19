/**
 * 共享的 1s 响应式时钟：驱动相对时间标签、倒计时等随时间自动刷新。
 * 全局单个定时器按引用计数启停，组件卸载自动释放。
 */
import { onUnmounted, ref, type Ref } from 'vue';

const now = ref(Date.now());
let timer: ReturnType<typeof setInterval> | null = null;
let refCount = 0;

export function useNowTick(): Ref<number> {
  refCount += 1;
  if (!timer) {
    timer = setInterval(() => {
      now.value = Date.now();
    }, 1000);
  }
  onUnmounted(() => {
    refCount -= 1;
    if (refCount === 0 && timer) {
      clearInterval(timer);
      timer = null;
    }
  });
  return now;
}
