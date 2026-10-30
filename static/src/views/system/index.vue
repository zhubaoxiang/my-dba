<template>
  <el-card shadow="never" class="system-page">
    <template #header>
      <span>系统管理</span>
    </template>

    <el-tabs v-model="activeTab">
      <el-tab-pane label="模型用量" name="usage">
        <div class="toolbar">
          <el-radio-group v-model="range" @change="onRangeChange">
            <el-radio-button value="today">今天</el-radio-button>
            <el-radio-button value="7d">近 7 天</el-radio-button>
            <el-radio-button value="30d">近 30 天</el-radio-button>
          </el-radio-group>
          <el-radio-group v-model="groupBy" @change="loadSummary">
            <el-radio-button value="user">按用户</el-radio-button>
            <el-radio-button value="model">按模型</el-radio-button>
            <el-radio-button value="feature">按功能</el-radio-button>
          </el-radio-group>
          <el-button :loading="loading" @click="loadAll">刷新</el-button>
        </div>

        <el-row :gutter="12" class="cards">
          <el-col v-for="card in cards" :key="card.label" :span="6">
            <el-card shadow="never" class="stat-card">
              <div class="stat-label">{{ card.label }}</div>
              <div class="stat-value">{{ card.value }}</div>
            </el-card>
          </el-col>
        </el-row>

        <el-card shadow="never" class="block">
          <template #header>按天趋势</template>
          <div v-if="!trendAvailable" class="muted">不可用</div>
          <el-empty v-else-if="!trendItems.length" description="暂无数据" :image-size="60" />
          <div v-else class="trend">
            <div v-for="item in trendItems" :key="item.date" class="trend-row">
              <span class="trend-date">{{ item.date }}</span>
              <div class="trend-bar">
                <div class="trend-fill" :style="{ width: barWidth(item.calls) }" />
              </div>
              <span class="trend-calls">{{ item.calls }}</span>
              <span class="trend-latency">{{ latencyText(item.avg_latency_ms) }}</span>
            </div>
          </div>
        </el-card>

        <el-card shadow="never" class="block">
          <template #header>{{ groupTitle }}</template>
          <div v-if="!groupsAvailable" class="muted">不可用</div>
          <el-empty v-else-if="!groupItems.length" description="暂无数据" :image-size="60" />
          <el-table v-else :data="groupItems" border>
            <el-table-column prop="label" :label="groupTitle" min-width="160" />
            <el-table-column prop="calls" label="调用次数" width="110" />
            <el-table-column label="成功率" width="110">
              <template #default="{ row }">{{ percent(row.success_rate) }}</template>
            </el-table-column>
            <el-table-column label="总 token" width="140">
              <template #default="{ row }">{{ tokenText(row.total_tokens) }}</template>
            </el-table-column>
            <el-table-column label="平均耗时" width="130">
              <template #default="{ row }">{{ latencyText(row.avg_latency_ms) }}</template>
            </el-table-column>
          </el-table>
        </el-card>

        <el-card shadow="never" class="block">
          <template #header>
            <div class="card-header">
              <span>调用明细</span>
              <div class="header-actions">
                <el-select v-model="callsQuery.success" style="width: 120px" @change="searchCalls">
                  <el-option label="全部结果" value="" />
                  <el-option label="成功" value="true" />
                  <el-option label="失败" value="false" />
                </el-select>
                <el-input-number
                  v-model="callsQuery.min_latency"
                  :min="0"
                  :step="100"
                  placeholder="最小耗时(ms)"
                  style="width: 160px"
                  @change="searchCalls"
                />
              </div>
            </div>
          </template>

          <el-table :data="calls" v-loading="callsLoading" border>
            <el-table-column prop="create_time" label="时间" width="170" />
            <el-table-column prop="username" label="用户" min-width="110" />
            <el-table-column prop="feature_label" label="功能" min-width="110" />
            <el-table-column prop="call_type_label" label="类型" width="80" />
            <el-table-column prop="model_name" label="模型" min-width="140" show-overflow-tooltip />
            <el-table-column label="token" width="100">
              <template #default="{ row }">{{ tokenText(row.total_tokens) }}</template>
            </el-table-column>
            <el-table-column label="耗时" width="100">
              <template #default="{ row }">{{ row.latency_ms }} ms</template>
            </el-table-column>
            <el-table-column label="结果" width="100">
              <template #default="{ row }">
                <el-tag :type="row.success ? 'success' : 'danger'" size="small">
                  {{ row.success ? '成功' : row.error_code || '失败' }}
                </el-tag>
              </template>
            </el-table-column>
          </el-table>

          <el-pagination
            class="pager"
            background
            layout="total, sizes, prev, pager, next"
            :total="callsTotal"
            :current-page="callsQuery.page"
            :page-size="callsQuery.page_size"
            :page-sizes="[10, 20, 50, 100]"
            @current-change="onCallsPageChange"
            @size-change="onCallsSizeChange"
          />
        </el-card>
      </el-tab-pane>
    </el-tabs>
  </el-card>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { llmUsageApi } from '@/api/llmUsage'

const activeTab = ref('usage')
const range = ref('7d')
const groupBy = ref('user')
const loading = ref(false)
const summaryData = ref(null)
const trendData = ref(null)
const groupsData = ref(null)

const calls = ref([])
const callsTotal = ref(0)
const callsLoading = ref(false)
const callsQuery = reactive({ page: 1, page_size: 20, success: '', min_latency: null })

const groupTitleMap = { user: '按用户', model: '按模型', feature: '按功能' }
const groupTitle = computed(() => groupTitleMap[groupBy.value])

const summaryAvailable = computed(() => summaryData.value?.available !== false)
const trendAvailable = computed(() => trendData.value?.available !== false)
const groupsAvailable = computed(() => groupsData.value?.available !== false)
const trendItems = computed(() => trendData.value?.items || [])
const groupItems = computed(() => groupsData.value?.items || [])

const cards = computed(() => {
  const data = summaryData.value
  const unavailable = !summaryAvailable.value
  return [
    { label: '调用次数', value: unavailable ? '不可用' : data?.calls ?? '—' },
    { label: '成功率', value: unavailable ? '不可用' : percent(data?.success_rate) },
    { label: '总 token', value: unavailable ? '不可用' : tokenText(data?.total_tokens) },
    { label: '平均耗时', value: unavailable ? '不可用' : latencyText(data?.avg_latency_ms) }
  ]
})

function percent(rate) {
  return rate == null ? '—' : `${(rate * 100).toFixed(1)}%`
}

// token 为 null 表示网关未返回用量，不能显示成 0
function tokenText(value) {
  return value == null ? '不可用' : value.toLocaleString()
}

function latencyText(value) {
  return value == null ? '—' : `${value} ms`
}

function barWidth(count) {
  const max = Math.max(...trendItems.value.map((item) => item.calls), 1)
  return `${Math.max((count / max) * 100, 2)}%`
}

async function loadSummary() {
  loading.value = true
  try {
    const data = await llmUsageApi.summary({ range: range.value, group_by: groupBy.value })
    summaryData.value = data.summary
    trendData.value = data.trend
    groupsData.value = data.groups
  } finally {
    loading.value = false
  }
}

async function loadCalls() {
  callsLoading.value = true
  try {
    const params = { range: range.value, page: callsQuery.page, page_size: callsQuery.page_size }
    if (callsQuery.success !== '') params.success = callsQuery.success
    if (callsQuery.min_latency != null) params.min_latency = callsQuery.min_latency
    const data = await llmUsageApi.calls(params)
    calls.value = data.results || []
    callsTotal.value = data.total || 0
  } finally {
    callsLoading.value = false
  }
}

function loadAll() {
  loadSummary()
  loadCalls()
}

function onRangeChange() {
  callsQuery.page = 1
  loadAll()
}

function searchCalls() {
  callsQuery.page = 1
  loadCalls()
}

function onCallsPageChange(page) {
  callsQuery.page = page
  loadCalls()
}

function onCallsSizeChange(size) {
  callsQuery.page_size = size
  callsQuery.page = 1
  loadCalls()
}

onMounted(loadAll)
</script>

<style scoped>
.toolbar {
  display: flex;
  gap: 12px;
  align-items: center;
  margin-bottom: 16px;
}

.cards {
  margin-bottom: 16px;
}

.stat-card {
  text-align: center;
}

.stat-label {
  color: var(--el-text-color-secondary);
  font-size: 13px;
  margin-bottom: 8px;
}

.stat-value {
  font-size: 24px;
  font-weight: 600;
}

.block {
  margin-bottom: 16px;
}

.muted {
  color: var(--el-text-color-secondary);
  padding: 16px 0;
  text-align: center;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.header-actions {
  display: flex;
  gap: 8px;
}

.trend-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 4px 0;
}

.trend-date {
  width: 96px;
  color: var(--el-text-color-secondary);
}

.trend-bar {
  flex: 1;
  height: 14px;
  background: var(--el-fill-color-light);
  border-radius: 4px;
  overflow: hidden;
}

.trend-fill {
  height: 100%;
  background: var(--el-color-primary);
  border-radius: 4px;
}

.trend-calls {
  width: 60px;
  text-align: right;
}

.trend-latency {
  width: 90px;
  text-align: right;
  color: var(--el-text-color-secondary);
}

.pager {
  margin-top: 16px;
}
</style>
