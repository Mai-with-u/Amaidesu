import { defineStore } from 'pinia';
import { computed, ref, watch } from 'vue';

export type Theme = 'light' | 'dark';

const STORAGE_KEY = 'theme';

/** 非浏览器环境（单测跑在 node）没有 matchMedia，按浅色处理 */
function mediaQuery(query: string): MediaQueryList | null {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    ? window.matchMedia(query)
    : null;
}

const systemDarkQuery = mediaQuery('(prefers-color-scheme: dark)');

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

  /** 手动切换后记住选择，此后不再跟随系统 */
  function toggleTheme(): void {
    preference.value = theme.value === 'light' ? 'dark' : 'light';
    storeTheme(preference.value);
  }

  return {
    theme,
    preference,
    toggleTheme,
  };
});
