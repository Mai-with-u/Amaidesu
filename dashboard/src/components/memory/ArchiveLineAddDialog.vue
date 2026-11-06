<template>
  <el-dialog
    :model-value="modelValue"
    title="新增一行档案"
    width="640px"
    append-to-body
    @update:model-value="(value: boolean) => emit('update:modelValue', value)"
    @open="loadSpeech"
  >
    <p class="dialog-hint">
      整理漏写的事：先点选这一场她播出的一句台词，再从里面截一段当原话（要逐字），写上标题。
      新增的行人锁住、放进最新一份快照，下一场开播会记得。
    </p>
    <p v-if="loadError" class="dialog-error">{{ loadError }}</p>
    <ul v-loading="loading" class="speech-list">
      <li
        v-for="item in speech"
        :key="item.chat_id"
        class="speech-item"
        :class="{ 'is-chosen': chosen?.chat_id === item.chat_id }"
        @click="choose(item)"
      >
        <span class="speech-time mono">{{ formatTimeHMS(item.timestamp_ms) }}</span>
        <span class="speech-text">{{ item.content }}</span>
      </li>
      <li v-if="!loading && speech.length === 0" class="speech-empty">这一场没有播出去的台词</li>
    </ul>
    <el-form label-width="64px" class="add-form" @submit.prevent>
      <el-form-item label="原话">
        <el-input
          v-model="quote"
          type="textarea"
          :rows="2"
          :maxlength="ARCHIVE_QUOTE_MAX_LENGTH"
          show-word-limit
          :disabled="chosen === null"
          placeholder="先点选一句台词，再删到只剩要记的那一段"
        />
      </el-form-item>
      <el-form-item label="标题">
        <el-input
          v-model="title"
          :maxlength="ARCHIVE_TITLE_MAX_LENGTH"
          show-word-limit
          placeholder="一句话说清是什么事"
        />
      </el-form-item>
      <el-form-item label="">
        <el-checkbox v-model="lasting">名字、住处、关系这类稳定的事</el-checkbox>
      </el-form-item>
    </el-form>
    <p v-if="formError" class="dialog-error">{{ formError }}</p>
    <template #footer>
      <el-button @click="emit('update:modelValue', false)">取消</el-button>
      <el-button type="primary" :loading="saving" @click="submit">新增</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
/**
 * 新增一行档案的对话框（记忆页审核）
 *
 * 打开时读这一场的逐句台词，只列播出去的（没送到观众面前的不算她说过）。点选一句后原话预填为整句，
 * 运营者删到只剩要记的那一段；提交前先在页面上核一遍原话是不是那句的逐字片段，后端还会再核。
 * 新增成功后抛出 created，由父组件重拉档案行。
 *
 * 不含定时器 / 订阅 / WebSocket，无 keep-alive 生命周期语义。
 */
import { ref } from 'vue';
import { ElMessage } from 'element-plus';
import type { ReviewSpeechLine } from '@/types';
import { ledgerApi, reviewApi } from '@/api';
import { getApiErrorMessage } from '@/utils/apiError';
import { formatTimeHMS } from '@/utils/format';
import {
  ARCHIVE_QUOTE_MAX_LENGTH,
  ARCHIVE_TITLE_MAX_LENGTH,
  quoteFitsSpeech,
  validateArchiveTitle,
} from './ledgerView';

const props = defineProps<{ modelValue: boolean; liveSessionId: number | null }>();
const emit = defineEmits<{ 'update:modelValue': [value: boolean]; created: [] }>();

const speech = ref<ReviewSpeechLine[]>([]);
const loading = ref(false);
const loadError = ref('');
const chosen = ref<ReviewSpeechLine | null>(null);
const quote = ref('');
const title = ref('');
const lasting = ref(false);
const saving = ref(false);
const formError = ref('');

async function loadSpeech(): Promise<void> {
  chosen.value = null;
  quote.value = '';
  title.value = '';
  lasting.value = false;
  formError.value = '';
  loadError.value = '';
  speech.value = [];
  if (props.liveSessionId === null) return;
  loading.value = true;
  try {
    const { data } = await reviewApi.listSpeech(props.liveSessionId);
    speech.value = data.items.filter(item => !item.dropped);
  } catch (err) {
    loadError.value = getApiErrorMessage(err, '这一场的台词加载失败');
  } finally {
    loading.value = false;
  }
}

/** 点选一句：原话预填整句（连续空白压成一个空格，截到上限） */
function choose(item: ReviewSpeechLine): void {
  chosen.value = item;
  quote.value = item.content.split(/\s+/).join(' ').trim().slice(0, ARCHIVE_QUOTE_MAX_LENGTH);
  formError.value = '';
}

async function submit(): Promise<void> {
  if (chosen.value === null || props.liveSessionId === null) {
    formError.value = '先点选一句台词';
    return;
  }
  const quoteText = quote.value.trim();
  if (!quoteText || !quoteFitsSpeech(quoteText, chosen.value.content)) {
    formError.value = '原话要从那句台词里逐字截取';
    return;
  }
  const titleError = validateArchiveTitle(title.value);
  if (titleError) {
    formError.value = titleError;
    return;
  }
  saving.value = true;
  formError.value = '';
  try {
    await ledgerApi.createArchiveLine(props.liveSessionId, {
      evidence_chat_id: chosen.value.chat_id,
      quote: quoteText,
      text: title.value.trim(),
      lasting: lasting.value,
    });
    ElMessage.success('已新增这一行，下一场开播会记得');
    emit('created');
    emit('update:modelValue', false);
  } catch (err) {
    formError.value = getApiErrorMessage(err, '新增档案行失败');
  } finally {
    saving.value = false;
  }
}
</script>

<style scoped>
.dialog-hint {
  margin: 0 0 var(--spacing-sm);
  font-size: 12px;
  color: var(--text-secondary);
}

.dialog-error {
  margin: var(--spacing-sm) 0 0;
  font-size: 12px;
  color: var(--color-danger);
}

.speech-list {
  list-style: none;
  margin: 0 0 var(--spacing-md);
  padding: 0;
  max-height: 260px;
  min-height: 40px;
  overflow-y: auto;
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-sm);
}

.speech-item {
  display: flex;
  gap: var(--spacing-sm);
  padding: 6px 10px;
  font-size: 13px;
  line-height: 1.5;
  cursor: pointer;
  border-top: 1px dashed var(--border-color-light);
}

.speech-item:first-child {
  border-top: none;
}

.speech-item.is-chosen {
  background: var(--el-color-primary-light-9);
}

.speech-time {
  flex-shrink: 0;
  font-size: 12px;
  color: var(--text-secondary);
}

.speech-text {
  color: var(--text-regular);
}

.speech-empty {
  padding: var(--spacing-sm);
  text-align: center;
  font-size: 12px;
  color: var(--text-placeholder);
}
</style>
