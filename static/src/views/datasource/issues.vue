<template>
  <div class="issues-page">
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span>问题清单</span>
          <div class="header-actions">
            <el-select v-model="datasourceId" placeholder="选择数据源" style="width: 200px" @change="onDatasourceChange">
              <el-option v-for="item in datasources" :key="item.id" :value="item.id" :label="item.name" />
            </el-select>
            <el-select v-model="issueLevel" placeholder="全部级别" clearable style="width: 140px" @change="reload">
              <el-option :value="1" label="高" />
              <el-option :value="2" label="中" />
              <el-option :value="3" label="低" />
            </el-select>
            <el-button @click="reload" :disabled="!datasourceId">刷新</el-button>
          </div>
        </div>
      </template>

      <el-empty v-if="!datasourceId" description="请先选择数据源" />
      <el-empty v-else-if="loaded && !rows.length" description="没有问题记录。可能是尚未采集，或本次分析未发现问题" />

      <template v-else>
        <el-table :data="rows" v-loading="loading" border>
          <el-table-column label="级别" width="90">
            <template #default="{ row }">
              <el-tag :type="levelTagType(row.issue_level)" size="small">{{ row.issue_level_label }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="issue_type_label" label="问题类型" width="150" />
          <el-table-column prop="target" label="对象" min-width="220" show-overflow-tooltip />
          <el-table-column prop="description" label="说明" min-width="280" show-overflow-tooltip />
          <el-table-column prop="suggestion" label="建议" min-width="240" show-overflow-tooltip />
          <el-table-column label="操作" width="110" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" :disabled="!row.table_name" @click="openTable(row)">查看表</el-button>
            </template>
          </el-table-column>
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
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { catalogApi, datasourceApi } from '@/api/datasource'

const route = useRoute()
const router = useRouter()

const datasources = ref([])
const datasourceId = ref(null)
const issueLevel = ref(null)
const rows = ref([])
const total = ref(0)
const loading = ref(false)
const loaded = ref(false)
const query = reactive({ page: 1, page_size: 20 })

function levelTagType(level) {
  if (level === 1) return 'danger'
  if (level === 2) return 'warning'
  return 'info'
}

async function loadDatasources() {
  const data = await datasourceApi.list({ page: 1, page_size: 200 })
  datasources.value = data.results || []
  const preset = Number(route.query.datasource_id)
  if (preset && datasources.value.some((item) => item.id === preset)) {
    datasourceId.value = preset
  } else if (datasources.value.length) {
    datasourceId.value = datasources.value[0].id
  }
  if (datasourceId.value) await loadIssues()
}

async function loadIssues() {
  if (!datasourceId.value) return
  loading.value = true
  try {
    const params = { datasource_id: datasourceId.value, page: query.page, page_size: query.page_size }
    if (issueLevel.value) params.issue_level = issueLevel.value
    const data = await catalogApi.issues(params)
    rows.value = data.results || []
    total.value = data.total || 0
    loaded.value = true
  } catch (err) {
    rows.value = []
    total.value = 0
    loaded.value = true
  } finally {
    loading.value = false
  }
}

function onDatasourceChange() {
  query.page = 1
  issueLevel.value = null
  loaded.value = false
  loadIssues()
}

function reload() {
  query.page = 1
  loadIssues()
}

function onPageChange(page) {
  query.page = page
  loadIssues()
}

function onSizeChange(size) {
  query.page_size = size
  query.page = 1
  loadIssues()
}

function openTable(row) {
  router.push({ path: '/datasource/tables', query: { datasource_id: datasourceId.value, table: row.table_name } })
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

.pager {
  margin-top: 16px;
  justify-content: flex-end;
}
</style>
