<template>
  <el-drawer
    :model-value="modelValue"
    direction="rtl"
    size="460px"
    title="提问箱 / 话题卡"
    class="talk-card-drawer"
    @update:model-value="emit('update:modelValue', $event)"
    @open="fetchCards"
  >
    <p class="drawer-hint">
      没人发弹幕、提纲也聊完时，她会从这里挑两张没用过的卡开话头；真人弹幕永远优先。
      直播中塞的新卡，她下一个冷场窗就能看到。
    </p>

    <!-- 快速塞卡 -->
    <section class="add-form">
      <el-select v-model="draft.kind" class="kind-select" aria-label="卡种">
        <el-option
          v-for="(label, key) in TALK_CARD_KIND_LABELS"
          :key="key"
          :label="label"
          :value="key"
        />
      </el-select>
      <el-input
        v-model="draft.title"
        maxlength="120"
        placeholder="一句问题或话头"
        aria-label="标题"
        @keyup.enter="addCard"
      />
      <el-input
        v-model="draft.body"
        type="textarea"
        :rows="2"
        maxlength="1000"
        placeholder="补充说明（可空：趣事经过、二选一的两个选项）"
        aria-label="补充说明"
      />
      <div class="add-row">
        <el-input
          v-model="draft.asker"
          maxlength="40"
          placeholder="提问人称呼（可空）"
          aria-label="提问人"
        />
        <el-button type="primary" :icon="Plus" :loading="saving" @click="addCard">
          塞进话题匣
        </el-button>
      </div>
    </section>

    <div class="list-header">
      <span>还能抽 {{ availableCount }} 张 · 共 {{ cards.length }} 张</span>
      <el-button size="small" :icon="RefreshLeft" :disabled="usedCount === 0" @click="resetUsed">
        复原已用（{{ usedCount }}）
      </el-button>
    </div>

    <el-skeleton v-if="loading && cards.length === 0" :rows="3" animated />
    <el-empty v-else-if="cards.length === 0" description="话题匣还是空的" :image-size="64" />
    <ul v-else class="card-list">
      <li
        v-for="card in cards"
        :key="card.id"
        class="card-item"
        :class="{ 'is-off': !card.enabled, 'is-used': card.used_at_ms != null }"
      >
        <div class="card-head">
          <el-tag size="small" effect="plain">{{ TALK_CARD_KIND_LABELS[card.kind] }}</el-tag>
          <span class="card-title">{{ card.title }}</span>
        </div>
        <p v-if="card.body" class="card-body">{{ card.body }}</p>
        <div class="card-foot">
          <span class="card-meta">
            <template v-if="card.asker">{{ card.asker }} · </template>
            {{ card.used_at_ms != null ? '已用过' : '没用过' }}
          </span>
          <span class="card-actions">
            <el-switch
              :model-value="card.enabled"
              size="small"
              inline-prompt
              active-text="启用"
              inactive-text="停用"
              @change="toggleEnabled(card)"
            />
            <el-button size="small" text type="danger" :icon="Delete" @click="removeCard(card)" />
          </span>
        </div>
      </li>
    </ul>
  </el-drawer>
</template>

<script setup lang="ts">
/**
 * 编排页提问箱抽屉：导演维护主播杂谈冷场时可抽的话题卡。
 *
 * 数据逻辑在 useTalkCards；抽屉每次打开拉一次最新卡池（无轮询、无订阅）。
 */
import { Delete, Plus, RefreshLeft } from '@element-plus/icons-vue';
import { TALK_CARD_KIND_LABELS, useTalkCards } from '@/composables/talk/useTalkCards';

defineProps<{ modelValue: boolean }>();
const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>();

const {
  cards,
  loading,
  saving,
  draft,
  usedCount,
  availableCount,
  fetchCards,
  addCard,
  toggleEnabled,
  removeCard,
  resetUsed,
} = useTalkCards();
</script>

<style scoped>
.drawer-hint {
  margin: 0 0 12px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
  line-height: 1.6;
}
.add-form {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}
.kind-select {
  width: 140px;
}
.add-row {
  display: flex;
  gap: 8px;
}
.list-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: 12px 0 8px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.card-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.card-item {
  padding: 8px 10px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
}
.card-item.is-off,
.card-item.is-used {
  opacity: 0.6;
}
.card-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.card-title {
  font-weight: 500;
  word-break: break-all;
}
.card-body {
  margin: 4px 0 0;
  color: var(--el-text-color-regular);
  font-size: 13px;
  white-space: pre-wrap;
}
.card-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 6px;
}
.card-meta {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.card-actions {
  display: flex;
  align-items: center;
  gap: 4px;
}
</style>
