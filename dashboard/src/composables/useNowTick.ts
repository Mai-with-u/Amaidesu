/**
 * 共享的 1s 响应式时钟：驱动相对时间标签、倒计时等随时间自动刷新。
 * 全局单个定时器按引用计数启停；消费方多在全局 keep-alive 内，
 * 以激活态持有引用——隐藏即停走，恢复时把时间跳到位；作用域销毁兜底释放。
 */
import { onActivated, onDeactivated, onScopeDispose, onUnmounted, ref, type Ref } from 'vue';

const now = ref(Date.now());
let timer: ReturnType<typeof setInterval> | null = null;
let refCount = 0;

function startTimer(): void {
  if (!timer) {
    timer = setInterval(() => {
      now.value = Date.now();
    }, 1000);
  }
}

function stopTimer(): void {
  if (timer) {
    clearInterval(timer);
    timer = null;
  }
}

export function useNowTick(): Ref<number> {
  // acquire 路径（setup 与 onActivated）与 release 路径（onDeactivated/
  // onUnmounted/onScopeDispose）不一一配对，由本标志保证收支平衡、重复释放安全
  let holding = false;
  const acquire = (): void => {
    if (holding) return;
    holding = true;
    refCount += 1;
    startTimer();
  };
  const release = (): void => {
    if (!holding) return;
    holding = false;
    refCount -= 1;
    if (refCount === 0) {
      stopTimer();
    }
  };
  acquire();
  // keep-alive 场景：隐藏释放引用停走，恢复时重新持有并把时间跳到位
  onActivated(() => {
    acquire();
    now.value = Date.now();
  });
  onDeactivated(release);
  // 非 keep-alive 消费方：setup 持有、卸载释放
  onUnmounted(release);
  // 作用域销毁兜底，覆盖无组件上下文的边缘用法
  onScopeDispose(release);
  return now;
}
