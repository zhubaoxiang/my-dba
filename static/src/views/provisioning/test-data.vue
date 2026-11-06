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

      <el-radio-group v-model="mode" class="mode-switch" @change="onModeChange">
        <el-radio-button value="datasource">从数据源</el-radio-button>
        <el-radio-button value="manual">手动输入表结构</el-radio-button>
      </el-radio-group>

      <el-row v-if="mode === 'datasource'" :gutter="12" class="picker-row">
        <el-col :span="12">
          <el-select
            v-model="datasourceId"
            placeholder="选择数据源"
            filterable
            style="width: 100%"
            @change="onDatasourceChange"
          >
            <el-option v-for="item in datasources" :key="item.id" :value="item.id" :label="item.name" />
          </el-select>
        </el-col>
      </el-row>

      <div v-else class="manual-box">
        <el-row :gutter="12" class="picker-row">
          <el-col :span="6">
            <el-select v-model="dbType" style="width: 100%" @change="onDbTypeChange">
              <el-option :value="1" label="PostgreSQL" />
              <el-option :value="2" label="MySQL" />
            </el-select>
          </el-col>
          <el-col :span="6">
            <el-button type="primary" :loading="parsing" @click="handleParse">解析表结构</el-button>
          </el-col>
        </el-row>
        <el-input
          v-model="ddlText"
          type="textarea"
          :rows="8"
          placeholder="粘贴 CREATE TABLE 语句，例如：&#10;CREATE TABLE users (&#10;  id integer PRIMARY KEY,&#10;  email varchar(64) NOT NULL,&#10;  amount numeric&#10;);"
        />
        <div class="meta manual-hint">
          支持 CREATE TABLE / CREATE INDEX / ALTER TABLE；注释类语句会被忽略，DROP / TRUNCATE 等不支持。
        </div>
      </div>

      <el-row :gutter="12" class="picker-row">
        <el-col :span="12">
          <div class="seed-field">
            <el-input-number
              v-model="seed"
              :min="0"
              :max="2147483647"
              placeholder="随机种子（可选）"
              controls-position="right"
              style="flex: 1"
            />
            <el-tooltip
              placement="top"
              content="随机种子用于复现：数据源/表结构、表、行数与种子都相同时，生成的数据相同；不填则每次随机。"
            >
              <el-icon class="hint-icon"><QuestionFilled /></el-icon>
            </el-tooltip>
          </div>
        </el-col>
        <el-col :span="12" class="ddl-switch">
          <el-checkbox v-model="includeDdl">包含建表语句</el-checkbox>
        </el-col>
      </el-row>

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

      <template v-else>
        <div class="toolbar">
          <div class="toolbar-left">
            <el-input
              v-model="tableKeyword"
              placeholder="按表名/注释搜索"
              clearable
              style="width: 240px"
            />
            <span class="meta">
              <template v-if="mode === 'datasource'">
                最新快照 #{{ snapshot.id }} · {{ snapshot.collect_time }} ·
              </template>
              共 {{ displayTables.length }} 张，匹配 {{ filteredTableRows.length }} 张，已选 {{ selectedTables.length }} 张
            </span>
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

        <el-table
          ref="tableRef"
          :data="filteredTableRows"
          v-loading="tableLoading"
          border
          max-height="420"
          row-key="key"
          @selection-change="onSelectionChange"
        >
          <el-table-column type="selection" width="45" reserve-selection />
          <el-table-column prop="name" label="表名" min-width="200" />
          <el-table-column prop="schema" label="schema" width="140" />
          <el-table-column prop="column_count" label="列数" width="80" />
          <el-table-column label="行数" width="160">
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

        <template v-if="result">
          <el-divider content-position="left">生成结果</el-divider>

          <el-row :gutter="12" class="summary-row">
            <el-col :span="8">
              <el-card shadow="hover">
                <div class="stat-value">{{ result.total_rows }}</div>
                <div class="stat-title">总行数</div>
              </el-card>
            </el-col>
            <el-col :span="8">
              <el-card shadow="hover">
                <div class="stat-value">{{ result.tables.length }}</div>
                <div class="stat-title">表数量</div>
              </el-card>
            </el-col>
            <el-col :span="8">
              <el-card shadow="hover">
                <div class="stat-value file-name">{{ result.filename }}</div>
                <div class="stat-title">文件名</div>
              </el-card>
            </el-col>
          </el-row>

          <el-alert
            v-for="(warning, index) in result.warnings"
            :key="index"
            class="notice"
            type="warning"
            :closable="false"
            show-icon
            :title="warning"
          />

          <el-button type="primary" @click="download">下载 SQL 文件</el-button>

          <el-collapse class="preview-collapse">
            <el-collapse-item title="查看文件内容预览" name="preview">
              <pre class="file-preview">{{ previewContent }}</pre>
              <div v-if="result.content.length > previewContent.length" class="muted">
                仅预览前 {{ previewContent.length }} 个字符，完整内容请下载文件。
              </div>
            </el-collapse-item>
          </el-collapse>
        </template>
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
const tableKeyword = ref('')
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

const displayTables = computed(() => (mode.value === 'manual' ? parsedTables.value : tableRows.value))
const filteredTableRows = computed(() => {
  const keyword = tableKeyword.value.trim().toLowerCase()
  if (!keyword) return displayTables.value
  return displayTables.value.filter((row) =>
    [row.name, row.schema, row.comment].some((value) =>
      String(value || '').toLowerCase().includes(keyword)
    )
  )
})
const previewContent = computed(() => (result.value ? result.value.content.slice(0, 8000) : ''))

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

function resetSelection() {
  result.value = null
  selectedTables.value = []
  tableKeyword.value = ''
  tableRef.value?.clearSelection()
  Object.keys(rowCounts).forEach((key) => delete rowCounts[key])
  for (const row of displayTables.value) {
    rowCounts[row.key] = 50
  }
}

function onModeChange() {
  resetSelection()
}

function onDbTypeChange() {
  // 方言变了，已解析的结构不能继续使用
  parsedTables.value = []
  resetSelection()
}

async function onDatasourceChange(value) {
  result.value = null
  selectedTables.value = []
  snapshot.value = null
  tableRows.value = []
  tableKeyword.value = ''
  tableRef.value?.clearSelection()
  Object.keys(rowCounts).forEach((key) => delete rowCounts[key])
  if (!value) return

  tableLoading.value = true
  try {
    const data = await catalogApi.snapshots({ datasource_id: value, page: 1, page_size: 1 })
    snapshot.value = (data.results || [])[0] || null
    if (!snapshot.value) return
    const tables = await catalogApi.tables({ snapshot_id: snapshot.value.id, page: 1, page_size: 1000 })
    tableRows.value = (tables.results || []).map((row) => {
      const key = `${row.schema}.${row.name}`
      rowCounts[key] = 50
      return { ...row, key }
    })
  } finally {
    tableLoading.value = false
  }
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
    resetSelection()
    ElMessage.success(`解析到 ${parsedTables.value.length} 张表`)
    if (data.warnings && data.warnings.length) {
      ElMessage.warning(data.warnings.join('；'))
    }
  } finally {
    parsing.value = false
  }
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

.mode-switch {
  margin-bottom: 12px;
}

.manual-box {
  margin-bottom: 12px;
}

.manual-hint {
  margin-top: 6px;
}

.picker-row {
  margin-bottom: 12px;
}

.ddl-switch {
  display: flex;
  align-items: center;
}

.seed-field {
  display: flex;
  align-items: center;
  gap: 6px;
}

.hint-icon {
  color: #909399;
  cursor: help;
  flex: none;
}

.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.toolbar-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.meta {
  font-size: 12px;
  color: #909399;
}

.summary-row {
  margin-bottom: 16px;
  text-align: center;
}

.stat-value {
  font-size: 22px;
  font-weight: 600;
  color: #303133;
}

.stat-value.file-name {
  font-size: 14px;
  word-break: break-all;
}

.stat-title {
  font-size: 13px;
  color: #909399;
  margin-top: 4px;
}

.preview-collapse {
  margin-top: 12px;
}

.file-preview {
  max-height: 360px;
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
