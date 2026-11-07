<template>
  <div class="test-data-page">
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span>数据文件</span>
        </div>
      </template>

      <el-alert
        class="notice"
        type="info"
        :closable="false"
        show-icon
        title="只生成可下载的 SQL 文件，不连接也不执行目标库；下载后请自行确认并导入。"
      />

      <div class="source-panel">
        <el-radio-group v-model="mode" class="mode-switch" @change="onModeChange">
          <el-radio-button value="datasource">从数据源</el-radio-button>
          <el-radio-button value="manual">手动输入表结构</el-radio-button>
        </el-radio-group>

        <div v-if="mode === 'datasource'" class="filter-row">
          <el-select
            v-model="datasourceId"
            class="source-select"
            placeholder="选择数据源"
            filterable
            @change="onDatasourceChange"
          >
            <el-option v-for="item in datasources" :key="item.id" :value="item.id" :label="item.name" />
          </el-select>
          <div class="seed-field">
            <el-input-number
              v-model="seed"
              class="seed-input"
              :min="0"
              :max="2147483647"
              placeholder="随机种子（可选）"
              controls-position="right"
            />
            <el-tooltip
              placement="top"
              content="随机种子用于复现：数据源/表结构、表、行数与种子都相同时，生成的数据相同；不填则每次随机。"
            >
              <el-icon class="hint-icon"><QuestionFilled /></el-icon>
            </el-tooltip>
          </div>
          <el-checkbox v-model="includeDdl">包含建表语句</el-checkbox>
        </div>

        <template v-else>
          <div class="filter-row">
            <el-select v-model="dbType" class="db-type-select" @change="onDbTypeChange">
              <el-option :value="1" label="PostgreSQL" />
              <el-option :value="2" label="MySQL" />
            </el-select>
            <el-button type="primary" :loading="parsing" @click="handleParse">解析表结构</el-button>
            <div class="seed-field">
              <el-input-number
                v-model="seed"
                class="seed-input"
                :min="0"
                :max="2147483647"
                placeholder="随机种子（可选）"
                controls-position="right"
              />
              <el-tooltip
                placement="top"
                content="随机种子用于复现：数据源/表结构、表、行数与种子都相同时，生成的数据相同；不填则每次随机。"
              >
                <el-icon class="hint-icon"><QuestionFilled /></el-icon>
              </el-tooltip>
            </div>
            <el-checkbox v-model="includeDdl">包含建表语句</el-checkbox>
          </div>
          <el-input
            v-model="ddlText"
            type="textarea"
            :rows="6"
            class="ddl-input"
            placeholder="粘贴 CREATE TABLE 语句，例如：&#10;CREATE TABLE users (&#10;  id integer PRIMARY KEY,&#10;  email varchar(64) NOT NULL,&#10;  amount numeric&#10;);"
          />
          <div class="meta manual-hint">
            支持 CREATE TABLE / CREATE INDEX / ALTER TABLE；注释类语句会被忽略，DROP / TRUNCATE 等不支持。
          </div>
        </template>
      </div>

      <el-empty
        v-if="mode === 'datasource' && !datasourceId"
        :description="datasources.length ? '请选择数据源' : '你还没有创建数据源，请先到「数据源管理」新建'"
      />
      <el-empty
        v-else-if="mode === 'datasource' && !snapshot"
        description="该数据源尚未采集，请先到「数据源管理」触发采集"
      />
      <el-empty
        v-else-if="mode === 'manual' && !parsedTables.length"
        description="请输入建表语句并点击「解析表结构」"
      />

      <div v-else class="table-section">
        <div class="table-toolbar">
          <div class="toolbar-left">
            <el-input
              v-model="tableKeyword"
              class="search-input"
              placeholder="按表名/注释搜索"
              clearable
              @keyup.enter="searchTables"
              @clear="searchTables"
            />
            <el-button @click="searchTables">搜索</el-button>
            <span class="meta">共 {{ visibleTotal }} 张 · 已选 {{ selectedTables.length }} 张</span>
            <el-button v-if="selectedTables.length" link type="primary" @click="clearSelectedTables">
              清空已选
            </el-button>
          </div>
          <el-button
            type="primary"
            :disabled="!selectedTables.length"
            :loading="generating"
            @click="handleGenerate"
          >
            生成 SQL 文件
          </el-button>
        </div>

        <el-empty v-if="!visibleTotal" :description="tableKeyword ? '没有匹配的表' : '没有可选择的表'" />
        <template v-else>
          <el-table
            ref="tableRef"
            :data="visibleTables"
            v-loading="tableLoading"
            border
            row-key="key"
            @selection-change="onSelectionChange"
          >
            <el-table-column type="selection" width="45" reserve-selection />
            <el-table-column prop="name" label="表名" min-width="180" show-overflow-tooltip />
            <el-table-column prop="schema" label="schema" width="200" />
            <el-table-column prop="column_count" label="列数" width="80" />
            <el-table-column label="行数" width="140">
              <template #default="{ row }">
                <el-input-number
                  v-model="rowCounts[row.key]"
                  :min="1"
                  :max="1000"
                  size="small"
                  controls-position="right"
                />
              </template>
            </el-table-column>
          </el-table>
          <el-pagination
            class="table-pager"
            background
            layout="total, prev, pager, next"
            :total="visibleTotal"
            :current-page="tableQuery.page"
            :page-size="tableQuery.page_size"
            @current-change="onTablePageChange"
          />
        </template>
      </div>

      <template v-if="result">
        <el-divider content-position="left">生成结果</el-divider>
        <div class="result-section">
          <div class="result-summary">
            <div class="summary-file">
              <span class="summary-label">文件</span>
              <span class="summary-value mono">{{ result.filename }}</span>
            </div>
            <div class="summary-item">
              <span class="summary-label">表数量</span>
              <span class="summary-value">{{ result.tables.length }}</span>
            </div>
            <div class="summary-item">
              <span class="summary-label">总行数</span>
              <span class="summary-value">{{ result.total_rows }}</span>
            </div>
            <el-button type="primary" @click="download">下载 SQL 文件</el-button>
          </div>

          <el-alert
            v-for="(warning, index) in result.warnings"
            :key="index"
            class="notice"
            type="warning"
            :closable="false"
            show-icon
            :title="warning"
          />

          <el-collapse class="preview-collapse">
            <el-collapse-item title="查看文件内容预览" name="preview">
              <pre class="file-preview">{{ previewContent }}</pre>
              <div v-if="result.content.length > previewContent.length" class="muted">
                仅预览前 {{ previewContent.length }} 个字符，完整内容请下载文件。
              </div>
            </el-collapse-item>
          </el-collapse>
        </div>
      </template>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { authApi } from '@/api/auth'
import { catalogApi, datasourceApi } from '@/api/datasource'
import { provisioningApi } from '@/api/provisioning'
import { useUserStore } from '@/store/user'

const userStore = useUserStore()
const isAdmin = computed(() => userStore.userInfo.role === 'admin')

const mode = ref('datasource')
const datasources = ref([])
const datasourceId = ref(null)
const snapshot = ref(null)
const tableRows = ref([])
const parsedTables = ref([])
const tableTotal = ref(0)
const tableKeyword = ref('')
const tableQuery = reactive({ page: 1, page_size: 10 })
const tableLoading = ref(false)
const parsing = ref(false)
const tableRef = ref(null)
const selectedTables = ref([])
const rowCounts = reactive({})
const seed = ref(null)
const includeDdl = ref(false)
const dbType = ref(1)
const ddlText = ref('')

const generating = ref(false)
const result = ref(null)

const manualFiltered = computed(() => {
  const keyword = tableKeyword.value.trim().toLowerCase()
  if (!keyword) return parsedTables.value
  return parsedTables.value.filter((row) =>
    [row.name, row.schema].some((value) => String(value || '').toLowerCase().includes(keyword))
  )
})
const visibleTables = computed(() => {
  if (mode.value !== 'manual') return tableRows.value
  const start = (tableQuery.page - 1) * tableQuery.page_size
  return manualFiltered.value.slice(start, start + tableQuery.page_size)
})
const visibleTotal = computed(() => (mode.value === 'manual' ? manualFiltered.value.length : tableTotal.value))
const previewContent = computed(() => (result.value ? result.value.content.slice(0, 8000) : ''))

function initRowCounts(rows) {
  for (const row of rows) {
    if (rowCounts[row.key] === undefined) rowCounts[row.key] = 50
  }
}

async function loadDatasources() {
  if (!userStore.userInfo.username) {
    try {
      userStore.setUserInfo(await authApi.me())
    } catch {
      // 拦截器已处理登录失效
    }
  }
  const data = await datasourceApi.list({ page: 1, page_size: 200 })
  const rows = data.results || []
  datasources.value = isAdmin.value
    ? rows
    : rows.filter((item) => item.creator === userStore.userInfo.username)
  datasourceId.value = datasources.value[0]?.id || null
  if (datasourceId.value) await onDatasourceChange(datasourceId.value)
}

async function onDatasourceChange(value) {
  result.value = null
  selectedTables.value = []
  snapshot.value = null
  tableRows.value = []
  tableTotal.value = 0
  tableKeyword.value = ''
  tableQuery.page = 1
  tableRef.value?.clearSelection()
  Object.keys(rowCounts).forEach((key) => delete rowCounts[key])
  if (!value) return

  tableLoading.value = true
  try {
    const data = await catalogApi.snapshots({ datasource_id: value, page: 1, page_size: 1 })
    snapshot.value = (data.results || [])[0] || null
    if (!snapshot.value) return
    await loadTables()
  } finally {
    tableLoading.value = false
  }
}

async function loadTables() {
  if (!snapshot.value) return
  tableLoading.value = true
  try {
    const data = await catalogApi.tables({
      snapshot_id: snapshot.value.id,
      keyword: tableKeyword.value,
      page: tableQuery.page,
      page_size: tableQuery.page_size
    })
    tableRows.value = (data.results || []).map((row) => ({ ...row, key: `${row.schema}.${row.name}` }))
    initRowCounts(tableRows.value)
    tableTotal.value = data.total || 0
  } finally {
    tableLoading.value = false
  }
}

function onModeChange() {
  result.value = null
  selectedTables.value = []
  tableKeyword.value = ''
  tableQuery.page = 1
  tableRef.value?.clearSelection()
  Object.keys(rowCounts).forEach((key) => delete rowCounts[key])
  if (mode.value === 'manual') {
    initRowCounts(parsedTables.value)
  } else if (datasourceId.value) {
    loadTables()
  } else {
    tableRows.value = []
    tableTotal.value = 0
  }
}

function onDbTypeChange() {
  parsedTables.value = []
  result.value = null
  selectedTables.value = []
  tableKeyword.value = ''
  tableQuery.page = 1
  tableRef.value?.clearSelection()
  Object.keys(rowCounts).forEach((key) => delete rowCounts[key])
}

async function handleParse() {
  if (!ddlText.value.trim()) {
    ElMessage.warning('请输入建表语句')
    return
  }
  parsing.value = true
  try {
    const data = await provisioningApi.parseTableStructure({ db_type: dbType.value, ddl: ddlText.value })
    parsedTables.value = (data.tables || []).map((table) => ({
      ...table,
      key: `${table.schema}.${table.name}`
    }))
    result.value = null
    selectedTables.value = []
    tableKeyword.value = ''
    tableQuery.page = 1
    tableRef.value?.clearSelection()
    Object.keys(rowCounts).forEach((key) => delete rowCounts[key])
    initRowCounts(parsedTables.value)
    ElMessage.success(`解析到 ${parsedTables.value.length} 张表`)
    if (data.warnings && data.warnings.length) {
      ElMessage.warning(data.warnings.join('；'))
    }
  } finally {
    parsing.value = false
  }
}

function searchTables() {
  tableQuery.page = 1
  if (mode.value === 'datasource') loadTables()
}

function onTablePageChange(page) {
  tableQuery.page = page
  if (mode.value === 'datasource') loadTables()
}

function clearSelectedTables() {
  selectedTables.value = []
  tableRef.value?.clearSelection()
}

function onSelectionChange(rows) {
  selectedTables.value = rows
}

function buildTables() {
  return selectedTables.value.map((row) => ({
    schema: row.schema,
    name: row.name,
    rows: rowCounts[row.key] || 1
  }))
}

async function handleGenerate() {
  if (!selectedTables.value.length) return
  const payload = {
    tables: buildTables(),
    seed: seed.value ?? undefined,
    include_ddl: includeDdl.value
  }
  if (mode.value === 'datasource') {
    payload.datasource_id = datasourceId.value
  } else {
    payload.db_type = dbType.value
    payload.ddl = ddlText.value
  }

  generating.value = true
  try {
    result.value = await provisioningApi.generateTestData(payload)
  } finally {
    generating.value = false
  }
}

function download() {
  if (!result.value) return
  const blob = new Blob([result.value.content], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = result.value.filename
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(url)
}

onMounted(loadDatasources)
</script>

<style scoped>
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.notice {
  margin-bottom: 12px;
}

.source-panel {
  padding: 14px 16px;
  margin-bottom: 16px;
  background: #f7f8fa;
  border-radius: 6px;
}

.mode-switch {
  margin-bottom: 12px;
}

.filter-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
}

.source-select {
  width: 280px;
}

.db-type-select {
  width: 140px;
}

.seed-field {
  display: flex;
  align-items: center;
  gap: 6px;
}

.seed-input {
  width: 160px;
}

.hint-icon {
  color: #909399;
  cursor: help;
  flex: none;
}

.ddl-input {
  margin-top: 12px;
  margin-bottom: 6px;
}

.manual-hint {
  margin-bottom: 12px;
}

.table-section {
  margin-top: 0;
}

.table-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}

.toolbar-left {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
}

.search-input {
  width: 240px;
}

.meta {
  font-size: 12px;
  color: #909399;
}

.table-pager {
  margin-top: 12px;
  justify-content: flex-end;
}

.result-section {
  margin-top: 8px;
}

.result-summary {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 24px;
  padding: 12px 16px;
  margin-bottom: 12px;
  background: #f5f7fa;
  border-radius: 4px;
}

.summary-file {
  display: flex;
  flex: 1;
  min-width: 220px;
  flex-direction: column;
  gap: 4px;
}

.summary-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.summary-label {
  font-size: 12px;
  color: #909399;
}

.summary-value {
  font-size: 16px;
  font-weight: 600;
  color: #303133;
}

.summary-value.mono {
  font-size: 13px;
  font-family: Consolas, Monaco, monospace;
  word-break: break-all;
}

.preview-collapse {
  margin-top: 8px;
}

.file-preview {
  max-height: 320px;
  overflow: auto;
  padding: 12px;
  background: #f5f7fa;
  border-radius: 4px;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
}

.muted {
  margin-top: 8px;
  color: #909399;
  font-size: 12px;
}
</style>
