<template>
  <aside class="request-detail">
    <header class="detail-head">
      <span class="detail-kind">{{ message.label }}</span>
      <span v-if="message.toolName" class="detail-tool mono">{{ message.toolName }}</span>
      <span class="detail-clock mono">{{ formatTimeHMS(message.tsMs) }}</span>
      <span class="grow" />
      <a
        v-if="record.llmRequestId"
        class="detail-link"
        :href="`/llm/history?request_id=${record.llmRequestId}`"
      >
        完整请求 ↗
      </a>
      <button type="button" class="detail-close" title="关闭" @click="emit('close')">×</button>
    </header>

    <div class="detail-body">
      <p class="detail-text">{{ message.text }}</p>

      <section v-if="hasPayload" class="detail-block">
        <h4 class="detail-title">载荷</h4>
        <!-- 与工具卡同一套渲染：可折叠的 JSON 树 + 深底高对比配色，读者不必自己数括号 -->
        <div v-if="jsonPayload !== null" class="detail-json">
          <VueJsonPretty :data="jsonPayload" theme="dark" show-line />
        </div>
        <pre v-else class="detail-json-text">{{ payloadText }}</pre>
      </section>

      <p v-else class="detail-empty">这一行没有附加载荷。</p>
    </div>
  </aside>
</template>

<script setup lang="ts">
// 轨迹行详情：点一行在右侧面板看完整内容（参数、结果、原始载荷）。
//
// 不在行下就地展开：一条记录里常有七八次调用，逐个展开会把整轮推高到几屏，
// 反而失去"一屏看完一轮"的用途；侧栏与列表并排，切换查看时位置不跳动。

import { computed } from 'vue';
import VueJsonPretty from 'vue-json-pretty';
import 'vue-json-pretty/lib/styles.css';
import { formatTimeHMS } from '@/utils/format';
import type { RequestMessage, RequestRecord } from '@/utils/requestRecords';

const props = defineProps<{
  record: RequestRecord;
  message: RequestMessage;
}>();

const emit = defineEmits<{ (e: 'close'): void }>();

const payloadText = computed<string>(() => {
  if (props.message.payload === undefined) return '';
  try {
    return JSON.stringify(props.message.payload, null, 2) ?? '';
  } catch {
    return String(props.message.payload);
  }
});

/** vue-json-pretty 只吃 JSON 结构：往返一次剥掉 undefined / 循环引用等非 JSON 值 */
type JsonData = string | number | boolean | null | JsonData[] | { [key: string]: JsonData };

const jsonPayload = computed<JsonData | null>(() => {
  if (props.message.payload === undefined) return null;
  try {
    return JSON.parse(JSON.stringify(props.message.payload)) as JsonData;
  } catch {
    return null;
  }
});

/** 载荷区只在真有内容时出现——空对象/空串挂一节空白标题没有意义 */
const hasPayload = computed<boolean>(() => {
  if (props.message.payload === undefined) return false;
  const data = jsonPayload.value;
  if (data === null) return payloadText.value.length > 0;
  if (typeof data === 'string') return data.length > 0;
  if (Array.isArray(data)) return data.length > 0;
  if (typeof data === 'object') return Object.keys(data).length > 0;
  return true;
});
</script>

<style scoped>
.request-detail {
  flex: 0 0 clamp(360px, 32%, 560px);
  min-width: 0;
  display: flex;
  flex-direction: column;
  border-left: 1px solid var(--border-color, #dcdfe6);
  background: var(--bg-elevated, #fff);
  z-index: 5;
}

.detail-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--border-color, #ebeef5);
  font-size: 12px;
  color: var(--text-secondary, #606266);
}

.detail-kind {
  color: var(--text-primary, #303133);
  font-weight: 500;
}

.detail-tool {
  color: var(--color-primary, #409eff);
  font-size: 11px;
}

.detail-link {
  color: var(--color-primary, #409eff);
  text-decoration: none;
  font-size: 11px;
}

.detail-close {
  border: none;
  background: none;
  cursor: pointer;
  font-size: 16px;
  line-height: 1;
  color: var(--text-tertiary, #909399);
  padding: 0 2px;
}

.detail-close:hover {
  color: var(--text-primary, #303133);
}

.detail-body {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: auto;
  padding: 10px 12px 16px;
}

/* 载荷是面板的正文主体：占满剩余高度，内容再长就在这层里滚，
   否则深色块只铺到内容高度、下面留一大片空白 */
.detail-block:last-child {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.detail-text {
  margin: 0 0 12px;
  font-size: 12px;
  line-height: 1.65;
  color: var(--text-primary, #303133);
  white-space: pre-wrap;
  word-break: break-word;
}

.detail-title {
  margin: 0 0 6px;
  font-size: 11px;
  font-weight: 500;
  color: var(--text-tertiary, #909399);
}

.detail-json {
  flex: 1 1 auto;
  min-height: 120px;
  overflow: auto;
  padding: 8px 10px;
  background: #1e1e1e;
  border-radius: 4px;
  font-size: 12px;
}

/* vue-json-pretty 的 key 不自带颜色；深底上必须显式给高对比配色（与工具卡一致） */
.detail-json :deep(.vjs-tree) {
  color: #d4d4d4;
}

.detail-json :deep(.vjs-value-string) {
  color: #7ee787;
}

.detail-json :deep(.vjs-value-number),
.detail-json :deep(.vjs-value-boolean),
.detail-json :deep(.vjs-value-null) {
  color: #79c0ff;
}

.detail-json-text {
  flex: 1 1 auto;
  margin: 0;
  min-height: 120px;
  padding: 8px 10px;
  overflow: auto;
  font-size: 11px;
  line-height: 1.6;
  background: var(--fill-light, #f5f7fa);
  border-radius: 4px;
  white-space: pre-wrap;
  word-break: break-all;
}

.detail-empty {
  margin: 0;
  font-size: 11px;
  color: var(--text-tertiary, #909399);
}

.grow {
  flex: 1;
}
</style>
