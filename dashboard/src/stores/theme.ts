import { defineStore } from 'pinia';
import { computed, nextTick, ref, watch } from 'vue';

export type Theme = 'light' | 'dark';

/** 切换动画的圆心（视口坐标），一般取主题按钮中心 */
export interface ThemeToggleOrigin {
  x: number;
  y: number;
}

const STORAGE_KEY = 'theme';
/** 切换时圆形展开的时长；期间暂停全局过渡，避免新画面里的颜色再渐变一次 */
const REVEAL_DURATION_MS = 480;
/** 过渡迟迟拿不到首帧（窗口被遮挡、渲染被节流）时放弃动画，保证主题照样切过去 */
const REVEAL_START_TIMEOUT_MS = 1000;

/** View Transitions API 在当前 TS DOM 库里还没有类型，按用到的最小形状声明 */
interface ViewTransitionLike {
  ready: Promise<void>;
  finished: Promise<void>;
  skipTransition: () => void;
}
type DocumentWithViewTransition = Document & {
  startViewTransition?: (update: () => Promise<void> | void) => ViewTransitionLike;
};

/** 非浏览器环境（单测跑在 node）没有 matchMedia，按"浅色、不减动效"处理 */
function mediaQuery(query: string): MediaQueryList | null {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    ? window.matchMedia(query)
    : null;
}

const systemDarkQuery = mediaQuery('(prefers-color-scheme: dark)');
const reducedMotionQuery = mediaQuery('(prefers-reduced-motion: reduce)');

function readStoredTheme(): Theme | null {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    return stored === 'light' || stored === 'dark' ? stored : null;
  } catch {
    // 隐私模式等场景 localStorage 不可用：当作未选过，跟随系统
    return null;
  }
}

function storeTheme(theme: Theme): void {
  try {
    localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    // 存不下只影响下次打开的初始主题，本次切换照常生效
  }
}

/**
 * 主题写到 <html>：data-theme 驱动项目自有变量，dark 类驱动 Element Plus 暗色变量。
 * index.html 的首帧脚本按同一规则先写过一次，这里接管之后的切换。
 */
function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  root.setAttribute('data-theme', theme);
  root.classList.toggle('dark', theme === 'dark');
}

export const useThemeStore = defineStore('theme', () => {
  /** 用户手动选过的主题；null 表示没选过，跟随系统 */
  const preference = ref<Theme | null>(readStoredTheme());
  const systemTheme = ref<Theme>(systemDarkQuery?.matches ? 'dark' : 'light');
  const theme = computed<Theme>(() => preference.value ?? systemTheme.value);

  systemDarkQuery?.addEventListener('change', event => {
    systemTheme.value = event.matches ? 'dark' : 'light';
  });

  // 同步写 DOM：图表色板在主题变化后立即读 CSS 变量，必须先于它们拿到新主题
  watch(theme, applyTheme, { immediate: true, flush: 'sync' });

  /** 圆形展开进行中；非响应式，只用来挡连点 */
  let switching = false;

  /**
   * 切换主题：支持 View Transitions 的浏览器以 origin 为圆心圆形展开新主题，
   * 不支持或用户开了"减少动态效果"时直接切换。
   */
  async function toggleTheme(origin?: ThemeToggleOrigin): Promise<void> {
    // 上一次圆形展开还没放完时忽略连点，否则两段过渡互相打断、按钮图标来回跳
    if (switching) {
      return;
    }
    const next: Theme = theme.value === 'light' ? 'dark' : 'light';
    const commit = (): void => {
      preference.value = next;
      storeTheme(next);
    };

    const doc = document as DocumentWithViewTransition;
    if (!doc.startViewTransition || reducedMotionQuery?.matches) {
      commit();
      return;
    }

    const x = origin?.x ?? window.innerWidth / 2;
    const y = origin?.y ?? 0;
    const radius = Math.hypot(
      Math.max(x, window.innerWidth - x),
      Math.max(y, window.innerHeight - y),
    );
    const root = document.documentElement;
    switching = true;
    root.classList.add('theme-switching');
    const transition = doc.startViewTransition(async () => {
      commit();
      await nextTick();
    });
    // 跳过过渡时浏览器仍会执行上面的回调，主题提交不会丢
    const startTimer = window.setTimeout(
      () => transition.skipTransition(),
      REVEAL_START_TIMEOUT_MS,
    );
    try {
      await transition.ready;
      window.clearTimeout(startTimer);
      root.animate(
        { clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${radius}px at ${x}px ${y}px)`] },
        {
          duration: REVEAL_DURATION_MS,
          easing: 'cubic-bezier(0.4, 0, 0.2, 1)',
          pseudoElement: '::view-transition-new(root)',
        },
      );
      await transition.finished;
    } catch (error) {
      // 动画被跳过或打断不影响主题本身，已在 commit 里生效
      console.warn('[theme] 主题切换动画中断', error);
    } finally {
      window.clearTimeout(startTimer);
      root.classList.remove('theme-switching');
      switching = false;
    }
  }

  return {
    theme,
    preference,
    toggleTheme,
  };
});
