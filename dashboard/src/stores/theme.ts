import { defineStore } from 'pinia';
import { ref, watch } from 'vue';

export type Theme = 'light' | 'dark';

export const useThemeStore = defineStore('theme', () => {
  const theme = ref<Theme>((localStorage.getItem('theme') as Theme) || 'light');

  // 应用主题到 DOM
  function applyTheme(newTheme: Theme) {
    document.documentElement.setAttribute('data-theme', newTheme);
    localStorage.setItem('theme', newTheme);
  }

  // 切换主题
  function toggleTheme() {
    theme.value = theme.value === 'light' ? 'dark' : 'light';
  }

  // 设置主题
  function setTheme(newTheme: Theme) {
    theme.value = newTheme;
  }

  // 监听主题变化
  watch(
    theme,
    (newTheme) => {
      applyTheme(newTheme);
    },
    { immediate: true },
  );

  return {
    theme,
    toggleTheme,
    setTheme,
  };
});
