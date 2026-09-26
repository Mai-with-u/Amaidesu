<template>
  <div class="intervention-bar">
    <div class="intervention-shell">
      <el-input
        ref="inputRef"
        v-model="text"
        type="textarea"
        :rows="1"
        :autosize="{ minRows: 1, maxRows: 5 }"
        resize="none"
        class="intervention-main"
        :disabled="sending"
        :placeholder="modeDef.placeholder"
        @keydown="onKeydown"
      />
      <div class="intervention-toolbar">
        <el-select
          :model-value="mode"
          size="small"
          class="intervention-mode"
          popper-class="intervention-mode-popper"
          :disabled="sending"
          :title="modeDef.desc"
          @update:model-value="setMode"
        >
          <el-option v-for="m in modes" :key="m.key" :label="m.label" :value="m.key">
            <div class="intervention-option">
              <span class="intervention-option-label">{{ m.label }}</span>
              <span class="intervention-option-desc">{{ m.desc }}</span>
            </div>
          </el-option>
        </el-select>
        <!-- 页面自有工具项：观众昵称 / 在途状态等，键盘行为与主输入一致 -->
        <slot name="toolbar" :on-keydown="onKeydown" />
        <span class="intervention-kbd mono" title="在输入框内按 Tab 切换模式，↑↓ 回溯发送历史">
          Tab 切模式 · ↑↓ 历史
        </span>
        <el-button
          class="intervention-send"
          type="primary"
          circle
          :loading="sending"
          @click="emitSend"
        >
          <el-icon v-if="!sending"><Promotion /></el-icon>
        </el-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 干预输入条（code agent 风格）：圆角卡片容器内嵌无边框输入 + 模式下拉 +
 * 圆形发送钮。Tab/Shift+Tab 循环切模式、Enter 发送、↑↓ 回溯发送历史
 * （Terminal 习惯），发送成功才入历史（失败原样留在框里改了重发）。
 *
 * 模式与传输归页面（本组件只管输入交互）：页面监听 send 事件按当前
 * 模式调各自 API；toolbar 具名插槽承载页面自有工具项（如观众昵称、
 * 在途状态），并回传 onKeydown 保持键盘行为一致。
 */
import { computed, nextTick, ref } from 'vue';
import { Promotion } from '@element-plus/icons-vue';

export interface InterventionSendMode {
  key: string;
  label: string;
  /** 模式说明：下拉选项与输入条 title 共用 */
  desc: string;
  placeholder: string;
}

const props = defineProps<{
  modes: InterventionSendMode[];
  /** 默认选中的模式 key（缺省取 modes 首项） */
  defaultMode?: string;
  /** 发送中状态（由页面按在途请求置位） */
  sending?: boolean;
}>();

const emit = defineEmits<{
  send: [modeKey: string, text: string];
  /** 模式切换通知：页面据此切换工具项显隐（如观众昵称仅弹幕模式） */
  modeChange: [modeKey: string];
}>();

const mode = ref<string>(props.defaultMode ?? props.modes[0]?.key ?? '');
function setMode(key: string): void {
  if (key === mode.value) return;
  mode.value = key;
  emit('modeChange', key);
}
const modeDef = computed<InterventionSendMode>(
  () => props.modes.find(m => m.key === mode.value) ?? props.modes[0],
);
const text = ref('');
const inputRef = ref<{ focus: (options?: { preventScroll?: boolean }) => void } | null>(null);

// 发送历史（跨模式共享，去重保尾，上限 50 条）；↑ 进入历史前暂存草稿，
// ↓ 翻到尽头自动还原草稿
const history = ref<string[]>([]);
const historyIdx = ref<number | null>(null);
let draft = '';

function cycleMode(step: number): void {
  const idx = props.modes.findIndex(m => m.key === mode.value);
  const next = (idx + step + props.modes.length) % props.modes.length;
  setMode(props.modes[next].key);
}

function historyNav(step: number): void {
  const list = history.value;
  if (list.length === 0) return;
  if (historyIdx.value === null) {
    if (step > 0) return;
    draft = text.value;
    historyIdx.value = list.length - 1;
  } else {
    const next = historyIdx.value + step;
    if (next < 0) return;
    if (next >= list.length) {
      historyIdx.value = null;
      text.value = draft;
      return;
    }
    historyIdx.value = next;
  }
  text.value = list[historyIdx.value];
}

function onKeydown(event: KeyboardEvent): void {
  if (event.isComposing) return; // IME 组字中不劫持按键
  if (event.key === 'Tab') {
    event.preventDefault();
    cycleMode(event.shiftKey ? -1 : 1);
  } else if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    emitSend();
  } else if (event.key === 'ArrowUp') {
    event.preventDefault();
    historyNav(-1);
  } else if (event.key === 'ArrowDown') {
    event.preventDefault();
    historyNav(1);
  }
}

function emitSend(): void {
  emit('send', mode.value, text.value.trim());
}

/** 发送成功收场：文本入历史（去重保尾）+ 清空输入 + 拉回焦点。
 *  失败时页面不调它——输入框里的文字原样保留改了重发。 */
async function settle(): Promise<void> {
  const value = text.value.trim();
  if (value && history.value[history.value.length - 1] !== value) {
    history.value.push(value);
    if (history.value.length > 50) history.value.shift();
  }
  historyIdx.value = null;
  text.value = '';
  // sending 翻转让输入框经历 disabled 往返而失焦——连发/接着切模式都依赖焦点在此
  await nextTick(() => inputRef.value?.focus());
}

defineExpose({ focus: () => inputRef.value?.focus(), settle });
</script>

<style scoped>
.intervention-shell {
  display: flex;
  flex-direction: column;
  gap: 4px;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  background: var(--bg-page);
  padding: 8px 10px 6px;
  transition: border-color 0.15s ease;
}

.intervention-shell:focus-within {
  border-color: var(--color-primary, var(--el-color-primary));
}

.intervention-main :deep(.el-textarea__inner) {
  box-shadow: none;
  background: transparent;
  padding: 2px 4px;
  font-size: 13px;
}

.intervention-toolbar {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  min-height: 26px;
}

.intervention-mode {
  width: 108px;
  flex-shrink: 0;
}

.intervention-option {
  display: flex;
  flex-direction: column;
  line-height: 1.4;
  padding: 2px 0;
}

.intervention-option-label {
  font-weight: 600;
}

.intervention-option-desc {
  font-size: 11px;
  color: var(--text-secondary);
  white-space: normal;
  max-width: 320px;
}

.intervention-kbd {
  margin-left: auto;
  font-size: 11px;
  color: var(--text-tertiary, var(--text-secondary));
  white-space: nowrap;
}

.intervention-send {
  flex-shrink: 0;
}
</style>

<style>
/* 模式下拉的 popper 挂在 body 下，scoped 样式够不着——popper-class 全局放行 */
.intervention-mode-popper .intervention-option-desc {
  color: var(--text-secondary);
  font-size: 11px;
  white-space: normal;
  max-width: 320px;
}
</style>
