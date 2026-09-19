<template>
  <div class="insights-page">
    <!-- 页面头                                                        -->
    <header class="page-header">
      <div class="header-left">
        <h1 class="page-title">互动分析</h1>
        <p class="page-subtitle">直播间观众互动健康度 · 全量累计视角</p>
      </div>
      <div class="header-actions">
        <el-select v-model="days" class="days-select" @change="load">
          <el-option label="近 7 天" :value="7" />
          <el-option label="近 30 天" :value="30" />
          <el-option label="近 90 天" :value="90" />
        </el-select>
        <el-button type="primary" plain :loading="loading" @click="load">刷新</el-button>
        <router-link to="/viewers" class="back-link">观众列表 →</router-link>
      </div>
    </header>

    <template v-if="data">
      <!-- 统计卡行                                                      -->
      <section class="stat-cards">
        <div class="stat-card">
          <span class="stat-value mono">{{ data.total_viewers }}</span>
          <span class="stat-label">观众总数</span>
        </div>
        <div class="stat-card">
          <span class="stat-value mono">{{ activeWithinWeek }}</span>
          <span class="stat-label">近 7 天活跃</span>
        </div>
        <div class="stat-card">
          <span class="stat-value mono">{{ replyCoverageText }}</span>
          <span class="stat-label">被回复覆盖率</span>
        </div>
        <div class="stat-card">
          <span class="stat-value mono">{{ data.gift_viewers }}</span>
          <span class="stat-label">送过礼的观众</span>
        </div>
      </section>

      <div class="main-grid">
        <!-- 左列：活跃分桶 + 弹幕量                                        -->
        <div class="left-column">
          <section class="card">
            <div class="card-head">
              <h2 class="card-title">活跃分布</h2>
              <span class="card-hint">按最后活跃时间分桶（互斥）</span>
            </div>
            <div class="bucket-list">
              <div v-for="bucket in buckets" :key="bucket.label" class="bucket-row">
                <span class="bucket-label">{{ bucket.label }}</span>
                <div class="bucket-bar-track">
                  <div class="bucket-bar" :style="{ width: bucket.percent + '%' }" />
                </div>
                <span class="bucket-count mono">{{ bucket.value }}</span>
              </div>
            </div>
          </section>

          <section class="card">
            <div class="card-head">
              <h2 class="card-title">弹幕量</h2>
              <span class="card-hint">近 {{ days }} 天按天（观众弹幕）</span>
            </div>
            <PulseChart
              :series="danmakuSeries"
              :labels="data.daily_danmaku.map(point => point.day.slice(5))"
              :height="150"
              empty-text="该区间还没有弹幕记录"
            />
          </section>
        </div>

        <!-- 右列：Top 榜                                                  -->
        <div class="right-column">
          <section class="card">
            <div class="card-head">
              <h2 class="card-title">礼物件数 Top 5</h2>
            </div>
            <ul class="top-list">
              <li v-for="(item, index) in giftTop" :key="item.user_id" class="top-row">
                <span class="top-rank mono">#{{ index + 1 }}</span>
                <router-link
                  class="top-name"
                  :to="`/viewers/${encodeURIComponent(item.user_id)}`"
                  :title="item.user_name || item.user_id"
                >
                  {{ item.user_name || item.user_id }}
                </router-link>
                <span class="top-value mono">{{ item.gift_count }} 件</span>
              </li>
              <li v-if="giftTop.length === 0" class="empty-hint">暂无礼物记录</li>
            </ul>
          </section>

          <section class="card">
            <div class="card-head">
              <h2 class="card-title">被回复 Top 5</h2>
              <span class="card-hint">主播回应最多的观众</span>
            </div>
            <ul class="top-list">
              <li v-for="(item, index) in repliedTop" :key="item.user_id" class="top-row">
                <span class="top-rank mono">#{{ index + 1 }}</span>
                <router-link
                  class="top-name"
                  :to="`/viewers/${encodeURIComponent(item.user_id)}`"
                  :title="item.user_name || item.user_id"
                >
                  {{ item.user_name || item.user_id }}
                </router-link>
                <span class="top-value mono">{{ item.replied_count }} 次</span>
              </li>
              <li v-if="repliedTop.length === 0" class="empty-hint">暂无回复记录</li>
            </ul>
          </section>
        </div>
      </div>
    </template>
    <p v-else-if="!loading" class="empty-hint">互动数据不可用（存储未装配？）</p>
  </div>
</template>

<script setup lang="ts">
/**
 * 互动分析 —— 观众互动健康度聚合
 *
 * 数据面：GET /viewers/insights（活跃分桶 + 回复覆盖 + 按天弹幕量）+
 * 两次 GET /viewers 排序取 Top 榜。礼物维度以件数计（礼物事件无金额）；
 * SC 金额无 viewers 表聚合维度，故 Top 榜取"礼物件数 / 被回复"两维。
 */
import { computed, onMounted, ref } from 'vue';
import { viewersApi } from '@/api';
import PulseChart from '@/components/dashboard/PulseChart.vue';
import type { PulseSeries } from '@/components/dashboard/PulseChart.vue';
import type { ViewerInsights, ViewerListItem } from '@/types';

const days = ref(30);
const loading = ref(false);
const data = ref<ViewerInsights | null>(null);
const giftTop = ref<ViewerListItem[]>([]);
const repliedTop = ref<ViewerListItem[]>([]);

const activeWithinWeek = computed(
  () => (data.value?.active_today ?? 0) + (data.value?.active_week ?? 0),
);

const replyCoverageText = computed(() => {
  const insights = data.value;
  if (!insights || insights.total_viewers === 0) return '—';
  const covered = insights.total_viewers - insights.never_replied;
  return `${Math.round((covered / insights.total_viewers) * 100)}%`;
});

const buckets = computed(() => {
  const insights = data.value;
  if (!insights) return [];
  const rows = [
    { label: '今天', value: insights.active_today },
    { label: '近 7 天', value: insights.active_week },
    { label: '近 30 天', value: insights.active_month },
    { label: '更早', value: insights.active_older },
  ];
  const max = Math.max(1, ...rows.map(row => row.value));
  return rows.map(row => ({ ...row, percent: (row.value / max) * 100 }));
});

const danmakuSeries = computed<PulseSeries[]>(() => [
  {
    label: '弹幕',
    color: 'var(--color-primary)',
    values: data.value?.daily_danmaku.map(p => p.count) ?? [],
  },
]);

async function load(): Promise<void> {
  loading.value = true;
  try {
    const [insightsResp, giftResp, repliedResp] = await Promise.allSettled([
      viewersApi.insights({ days: days.value }),
      viewersApi.list({ order_by: 'gift_count', limit: 5 }),
      viewersApi.list({ order_by: 'replied_count', limit: 5 }),
    ]);
    if (insightsResp.status === 'fulfilled') data.value = insightsResp.value.data;
    if (giftResp.status === 'fulfilled') giftTop.value = giftResp.value.data.items;
    if (repliedResp.status === 'fulfilled') repliedTop.value = repliedResp.value.data.items;
  } finally {
    loading.value = false;
  }
}

onMounted(() => {
  void load();
});
</script>

<style scoped>
.insights-page {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-md);
}

.page-header {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--spacing-md);
}

.page-title {
  margin: 0;
  font-size: 22px;
  font-weight: 650;
  color: var(--text-primary);
}

.page-subtitle {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--text-secondary);
}

.header-actions {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
}

.days-select {
  width: 110px;
}

.back-link {
  font-size: 13px;
  color: var(--color-primary);
  text-decoration: none;
}
.back-link:hover {
  text-decoration: underline;
}

.stat-cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: var(--spacing-sm);
}

.stat-card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: var(--spacing-md);
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-lg);
}

.stat-value {
  font-size: 22px;
  font-weight: 650;
  color: var(--text-primary);
}

.stat-label {
  font-size: 11px;
  color: var(--text-secondary);
}

.main-grid {
  display: grid;
  grid-template-columns: 3fr 2fr;
  gap: var(--spacing-sm);
  align-items: start;
}

.left-column,
.right-column {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
}

.card {
  padding: var(--spacing-md);
  background: var(--bg-card);
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-lg);
}

.card-head {
  display: flex;
  align-items: baseline;
  gap: var(--spacing-sm);
  margin-bottom: var(--spacing-sm);
}

.card-title {
  margin: 0;
  font-size: 13px;
  font-weight: 700;
  color: var(--text-primary);
}

.card-hint {
  font-size: 11px;
  color: var(--text-placeholder);
}

/* 活跃分桶条形 */

.bucket-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.bucket-row {
  display: grid;
  grid-template-columns: 64px 1fr 48px;
  align-items: center;
  gap: var(--spacing-sm);
}

.bucket-label {
  font-size: 12px;
  color: var(--text-secondary);
}

.bucket-bar-track {
  height: 12px;
  border-radius: 6px;
  background: var(--bg-hover);
  overflow: hidden;
}

.bucket-bar {
  height: 100%;
  border-radius: 6px;
  background: var(--color-primary);
  opacity: 0.75;
  transition: width var(--transition-normal);
}

.bucket-count {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-primary);
  text-align: right;
}

/* Top 榜 */

.top-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.top-row {
  display: grid;
  grid-template-columns: 34px 1fr auto;
  align-items: center;
  gap: var(--spacing-sm);
}

.top-rank {
  font-size: 11px;
  color: var(--text-placeholder);
}

.top-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  text-decoration: none;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.top-name:hover {
  color: var(--color-primary);
}

.top-value {
  font-size: 12px;
  color: var(--text-secondary);
}

.empty-hint {
  padding: var(--spacing-lg);
  text-align: center;
  font-size: 13px;
  color: var(--text-placeholder);
  margin: 0;
  list-style: none;
}

@media (max-width: 1100px) {
  .main-grid {
    grid-template-columns: 1fr;
  }
}
</style>
