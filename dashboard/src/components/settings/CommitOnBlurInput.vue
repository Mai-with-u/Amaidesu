<template>
  <el-input
    :model-value="draft"
    :placeholder="placeholder"
    @update:model-value="onInput"
    @blur="onCommit"
    @keydown.enter="onCommit"
  />
</template>

<script setup lang="ts">
/**
 * 失焦或回车才提交的文本输入框
 *
 * 用途：改名要问引用方要不要跟着改，逐键提交会把只输入了前几个字符的中间态
 * 名字写进引用方。逐键输入只落本地草稿，失焦或回车才把最终值交出去。
 *
 * 输入必须是双向的：Element Plus 的输入框在只给值、不接更新事件时，每次
 * 键盘输入后都会把输入框里的文字重置回原值，字符根本留不住。
 *
 * 提交时机认失焦（blur）与回车，不认原生的 change 事件——change 只在用户
 * 真的改过值时才随失焦触发，脚本改值或某些输入法场景下不触发，改名会静默
 * 丢在草稿里。
 *
 * 外部改掉已存值（改名生效、撤销改动）时草稿随之同步；父组件不采纳这次
 * 提交（改名被拒）时草稿回落成已存值，输入框不会停在被拒的值上。
 */
import { nextTick, ref, watch } from 'vue';

const props = defineProps<{
  /** 已存值 */
  value: string;
  placeholder?: string;
}>();

const emit = defineEmits<{
  (e: 'commit', value: string): void;
}>();

const draft = ref(props.value);

function onInput(text: string): void {
  draft.value = text;
}

function onCommit(): void {
  const value = draft.value.trim();
  draft.value = value;
  emit('commit', value);
  nextTick(() => {
    // 父组件未采纳（改名被拒）时把草稿回落成已存值
    if (props.value !== draft.value) draft.value = props.value;
  });
}

watch(
  () => props.value,
  value => {
    draft.value = value;
  },
);
</script>
