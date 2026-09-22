<template>
  <div class="tables-page">
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span>库表分析</span>
          <div class="header-actions">
            <el-select v-model="datasourceId" placeholder="选择数据源" style="width: 220px" @change="onDatasourceChange">
              <el-option v-for="item in datasources" :key="item.id" :value="item.id" :label="item.name" />
            </el-select>
            <el-button @click="reload" :disabled="!datasourceId">刷新</el-button>
          </div>
        </div>
      </template>

      <el-empty v-if="!datasourceId" description="请先选择数据源" />
      <el-empty v-else-if="!snapshot" description="该数据源尚未采集，请到「数据源管理」触发采集" />

      <template v-else>
        <el-row :gutter="12" class="summary-row">
          <el-col :span="6">
            <el-card shadow="hover">
              <div class="stat-value">{{ summary.table_count || 0 }}</div>
              <div class="stat-title">表数量</div>
            </el-card>
          </el-col>
          <el-col :span="6">
            <el-card shadow="hover">
              <div class="stat-value danger">{{ summary.issue_counts?.[1] || 0 }}</div>
              <div class="stat-title">高危问题</div>
            </el-card>
          </el-col>
          <el-col :span="6">
            <el-card shadow="hover">
              <div class="stat-value warning">{{ summary.issue_counts?.[2] || 0 }}</div>
              <div class="stat-title">中危问题</div>
            </el-card>
          </el-col>
          <el-col :span="6">
            <el-card shadow="hover">
              <div class="stat-value info">{{ summary.issue_counts?.[3] || 0 }}</div>
              <div class="stat-title">低危问题</div>
            </el-card>
          </el-col>
        </el-row>

        <el-alert
          v-if="(summary.unavailable || []).length"
          class="unavailable-alert"
          type="info"
          :closable="false"
          show-icon
          :title="`以下分析项因目标库权限或版本原因未采集，相关规则已跳过：${(summary.unavailable || []).join('、')}`"
        />

        <div class="toolbar">
          <el-input
            v-model="keyword"
            placeholder="按表名或注释搜索"
            clearable
            style="width: 280px"
            @keyup.enter="search"
            @clear="search"
          />
          <el-button type="primary" @click="search">搜索</el-button>
          <span class="meta">
            快照 #{{ snapshot.id }} · {{ snapshot.collect_time }} · 数据库版本 {{ summary.database_version || '未知' }}
          </span>
        </div>

        <el-table :data="rows" v-loading="loading" border @row-click="openDetail">
          <el-table-column prop="name" label="表名" min-width="180" />
          <el-table-column prop="comment" label="注释" min-width="180" show-overflow-tooltip />
          <el-table-column label="行数（估）" width="130">
            <template #default="{ row }">{{ formatRows(row.row_count) }}</template>
          </el-table-column>
          <el-table-column label="数据大小" width="120">
            <template #default="{ row }">{{ formatBytes(row.data_size) }}</template>
          </el-table-column>
          <el-table-column label="索引大小" width="120">
            <template #default="{ row }">{{ formatBytes(row.index_size) }}</template>
          </el-table-column>
          <el-table-column prop="column_count" label="列数" width="80" />
          <el-table-column prop="index_count" label="索引数" width="90" />
        </el-table>

        <el-pagination
          class="pager"
          background
          layout="total, sizes, prev, pager, next"
          :total="total"
          :current-page="query.page"
          :page-size="query.page_size"
          :page-sizes="[10, 20, 50, 100]"
          @current-change="onPageChange"
          @size-change="onSizeChange"
        />
      </template>
    </el-card>

    <table-detail v-model="detailVisible" :snapshot-id="snapshot?.id" :table="currentTable" />
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { catalogApi, datasourceApi } from '@/api/datasource'
import { formatBytes, formatRows } from '@/utils/format'
import TableDetail from './table-detail.vue'

const route = useRoute()

const datasources = ref([])
const datasourceId = ref(null)
const snapshot = ref(null)
const summary = ref({})
const rows = ref([])
const total = ref(0)
const loading = ref(false)
const keyword = ref('')
const query = reactive({ page: 1, page_size: 20 })

const detailVisible = ref(false)
const currentTable = ref(null)

async function loadDatasources() {
  const data = await datasourceApi.list({ page: 1, page_size: 200 })
  datasources.value = data.results || []
  const preset = Number(route.query.datasource_id)
  if (preset && datasources.value.some((item) => item.id === preset)) {
    datasourceId.value = preset
  } else if (datasources.value.length) {
    datasourceId.value = datasources.value[0].id
  }
  if (datasourceId.value) await onDatasourceChange(datasourceId.value)
}

async function onDatasourceChange(value) {
  query.page = 1
  keyword.value = ''
  detailVisible.value = false
  const data = await catalogApi.snapshots({ datasource_id: value, page: 1, page_size: 1 })
  snapshot.value = (data.results || [])[0] || null
  if (!snapshot.value) {
    summary.value = {}
    rows.value = []
    total.value = 0
    return
  }
  await Promise.all([loadSummary(), loadTables()])
  await maybeOpenFromQuery()
}

async function loadSummary() {
  summary.value = await catalogApi.summary({ snapshot_id: snapshot.value.id })
}

async function loadTables() {
  loading.value = true
  try {
    const data = await catalogApi.tables({
      snapshot_id: snapshot.value.id,
      keyword: keyword.value,
      page: query.page,
      page_size: query.page_size
    })
    rows.value = data.results || []
    total.value = data.total || 0
  } finally {
    loading.value = false
  }
}

function search() {
  query.page = 1
  loadTables()
}

function onPageChange(page) {
  query.page = page
  loadTables()
}

function onSizeChange(size) {
  query.page_size = size
  query.page = 1
  loadTables()
}

function openDetail(row) {
  currentTable.value = row
  detailVisible.value = true
}

// 从问题清单跳转过来时，自动打开对应表详情
async function maybeOpenFromQuery() {
  const tableName = route.query.table
  if (!tableName) return
  const target = rows.value.find((item) => item.name === tableName)
  if (target) openDetail(target)
}

async function reload() {
  if (!datasourceId.value) return
  await onDatasourceChange(datasourceId.value)
}

onMounted(loadDatasources)
</script>

<style scoped>
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.header-actions {
  display: flex;
  gap: 8px;
}

.summary-row {
  margin-bottom: 16px;
  text-align: center;
}

.stat-value {
  font-size: 24px;
  font-weight: 600;
  color: #303133;
}

.stat-value.danger {
  color: #f56c6c;
}

.stat-value.warning {
  color: #e6a23c;
}

.stat-value.info {
  color: #909399;
}

.stat-title {
  font-size: 13px;
  color: #909399;
  margin-top: 4px;
}

.unavailable-alert {
  margin-bottom: 12px;
}

.toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}

.meta {
  margin-left: auto;
  font-size: 12px;
  color: #909399;
}

.pager {
  margin-top: 16px;
  justify-content: flex-end;
}
</style>
