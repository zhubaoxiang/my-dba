<template>
  <div class="provisioning-page">
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span>测试库</span>
          <div class="header-actions">
            <el-button type="primary" @click="openCreate">新建测试库</el-button>
            <el-button @click="loadRecords">刷新</el-button>
          </div>
        </div>
      </template>

      <el-dialog
        v-model="createVisible"
        title="新建测试库"
        width="min(900px, 94vw)"
        top="8vh"
        :close-on-click-modal="false"
        destroy-on-close
      >
        <div class="create-dialog-body">
          <el-alert
            class="notice"
            type="warning"
            :closable="false"
            show-icon
            title="近似结构：不会还原 CHECK 约束、触发器、视图、序列、扩展与权限；生成的数据是合成数据。"
          />

          <el-steps :active="createStep" simple class="create-steps">
            <el-step title="选择表" />
            <el-step title="预览执行" />
          </el-steps>

          <div v-show="createStep === 0">
            <el-row :gutter="12" class="picker-row">
              <el-col :span="10">
                <el-select
                  v-model="datasourceId"
                  placeholder="请选择测试数据源"
                  filterable
                  style="width: 100%"
                  @change="onDatasourceChange"
                >
                  <el-option
                    v-for="item in datasources"
                    :key="item.id"
                    :value="item.id"
                    :label="`${item.name}（${item.env_type_label}）`"
                  />
                </el-select>
              </el-col>
              <el-col :span="6">
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
                    content="随机种子用于复现：数据源、表、行数与种子都相同时，生成的数据相同；不填则每次随机。"
                  >
                    <el-icon class="hint-icon"><QuestionFilled /></el-icon>
                  </el-tooltip>
                </div>
              </el-col>
              <el-col :span="8" class="env-info">
                <el-tag v-if="currentDatasource" :type="isTestEnv ? 'warning' : 'info'">
                  {{ currentDatasource.env_type_label }}
                </el-tag>
                <el-tag
                  v-if="currentDatasource"
                  :type="currentDatasource.has_write_credential ? 'success' : 'danger'"
                >
                  {{ currentDatasource.has_write_credential ? '已配置写账号' : '未配置写账号' }}
                </el-tag>
              </el-col>
            </el-row>

            <el-alert
              v-if="currentDatasource && !isTestEnv"
              class="notice"
              type="error"
              :closable="false"
              show-icon
              title="只有测试环境的数据源可以预置测试库；请到「数据源管理」编辑该数据源，把环境类型改为测试"
            />
            <el-alert
              v-if="currentDatasource && isTestEnv && !currentDatasource.has_write_credential"
              class="notice"
              type="warning"
              :closable="false"
              show-icon
              title="尚未配置预置写账号，请到「数据源管理」编辑该数据源并填写预置写账号"
            />

            <el-empty
              v-if="!datasourceId"
              :description="datasources.length ? '请选择数据源后加载库表' : '你还没有创建数据源，请先到「数据源管理」新建'"
            />
            <el-empty v-else-if="!snapshot" description="该数据源尚未采集，请先到「数据源管理」触发采集" />

            <template v-else>
              <div class="toolbar">
                <div class="toolbar-left">
                  <el-input
                    v-model="tableKeyword"
                    placeholder="按表名/注释搜索"
                    clearable
                    style="width: 220px"
                    @keyup.enter="searchTables"
                    @clear="searchTables"
                  />
                  <el-button @click="searchTables">搜索</el-button>
                  <span class="meta">
                    快照 #{{ snapshot.id }} · 共 {{ tableTotal }} 张 · 已选 {{ selectedTables.length }} 张
                  </span>
                  <el-button v-if="selectedTables.length" link type="primary" @click="clearSelectedTables">
                    清空已选
                  </el-button>
                </div>
              </div>

              <el-empty
                v-if="!tableTotal"
                :description="tableKeyword ? '没有匹配的表' : '该数据源没有表'"
              />
              <template v-else>
                <el-table
                  ref="tableRef"
                  :data="tableRows"
                  v-loading="tableLoading"
                  border
                  row-key="key"
                  @selection-change="onSelectionChange"
                >
                  <el-table-column type="selection" width="45" reserve-selection />
                  <el-table-column prop="name" label="表名" min-width="160" show-overflow-tooltip />
                  <el-table-column prop="schema" label="schema" width="200" />
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

                <el-pagination
                  class="table-pager"
                  background
                  layout="total, prev, pager, next"
                  :total="tableTotal"
                  :current-page="tableQuery.page"
                  :page-size="tableQuery.page_size"
                  @current-change="onTablePageChange"
                />
              </template>
            </template>
          </div>

          <div v-if="plan" v-show="createStep === 1">
            <div class="preview-summary">
              <div class="summary-item">
                <span class="summary-label">将创建</span>
                <span class="summary-value mono">{{ plan.db_name }}</span>
              </div>
              <div class="summary-item">
                <span class="summary-label">表数量</span>
                <span class="summary-value">{{ plan.tables.length }}</span>
              </div>
              <div class="summary-item">
                <span class="summary-label">总行数</span>
                <span class="summary-value">{{ plan.total_rows }}</span>
              </div>
            </div>

            <el-alert
              v-for="(warning, index) in plan.warnings"
              :key="index"
              class="notice"
              type="warning"
              :closable="false"
              show-icon
              :title="warning"
            />

            <el-tabs v-model="previewTab" class="preview-tabs">
              <el-tab-pane label="表清单" name="tables">
                <el-table :data="plan.tables" border size="small" max-height="240">
                  <el-table-column prop="schema" label="schema" width="180" />
                  <el-table-column prop="name" label="表名" min-width="160" show-overflow-tooltip />
                  <el-table-column prop="rows" label="行数" width="80" />
                  <el-table-column label="语义列" min-width="220">
                    <template #default="{ row }">
                      <span v-if="!row.semantic_columns.length" class="muted">—</span>
                      <el-tag
                        v-for="item in row.semantic_columns"
                        :key="item.column"
                        size="small"
                        :type="item.applied ? 'success' : 'info'"
                        class="semantic-tag"
                      >
                        {{ item.column }}: {{ item.semantic }}{{ item.applied ? '' : '（回退）' }}
                      </el-tag>
                    </template>
                  </el-table-column>
                </el-table>
              </el-tab-pane>
              <el-tab-pane label="DDL" name="ddl">
                <pre class="ddl preview-ddl">{{ plan.statements.join(';\n\n') }};</pre>
              </el-tab-pane>
            </el-tabs>

            <el-checkbox v-model="confirmed" class="confirm-check">
              我已确认对测试环境数据源发起建库与造数，库名为「{{ plan.db_name }}」
            </el-checkbox>
          </div>
        </div>

        <template #footer>
          <div v-if="createStep === 0" class="dialog-footer">
            <el-button @click="createVisible = false">取消</el-button>
            <el-button
              type="primary"
              :disabled="!canPreview"
              :loading="previewing"
              @click="handlePreview"
            >
              下一步：预览
            </el-button>
          </div>
          <div v-else class="dialog-footer">
            <el-button @click="backToSelect">返回修改</el-button>
            <el-button type="danger" :disabled="!confirmed" :loading="executing" @click="handleProvision">
              执行预置
            </el-button>
          </div>
        </template>
      </el-dialog>

      <div class="toolbar">
        <div class="toolbar-left">
          <el-input
            v-model="recordsKeyword"
            placeholder="按库名/数据源搜索"
            clearable
            style="width: 240px"
            @keyup.enter="searchRecords"
            @clear="searchRecords"
          />
          <el-button @click="searchRecords">搜索</el-button>
          <span class="meta">已创建的测试库 · 共 {{ recordsTotal }} 个</span>
        </div>
      </div>
          <el-table :data="records" v-loading="recordsLoading" border>
            <el-table-column type="expand">
              <template #default="{ row }">
                <div class="record-tables">
                  <div class="meta record-meta">表 {{ row.tables ? row.tables.length : 0 }} 张</div>
                  <div v-if="!row.tables || !row.tables.length" class="muted">没有表信息</div>
                  <el-table v-else :data="row.tables" border size="small">
                    <el-table-column prop="schema" label="schema" width="140" />
                    <el-table-column prop="name" label="表名" min-width="200" />
                    <el-table-column prop="rows" label="行数" width="100" />
                    <el-table-column label="操作" width="220" fixed="right">
                      <template #default="{ row: table }">
                        <template v-if="row.status === 2">
                          <el-button link type="primary" @click="openAppendRows(row, table)">加数据</el-button>
                          <el-button link type="warning" @click="handleClearRows(row, table)">清空数据</el-button>
                          <el-button link type="danger" @click="handleDropTable(row, table)">删表</el-button>
                        </template>
                      </template>
                    </el-table-column>
                  </el-table>
                  <div v-if="row.status !== 2" class="muted record-hint">
                    只有状态为「可用」的测试库才能继续操作
                  </div>
                </div>
              </template>
            </el-table-column>
            <el-table-column prop="db_name" label="库名" min-width="260" />
            <el-table-column prop="datasource_name" label="数据源" width="140" />
            <el-table-column label="表数" width="80">
              <template #default="{ row }">{{ row.tables ? row.tables.length : 0 }}</template>
            </el-table-column>
            <el-table-column label="表" min-width="240" show-overflow-tooltip>
              <template #default="{ row }">
                <span v-if="!row.tables || !row.tables.length" class="muted">—</span>
                <span v-else>{{ tableNames(row.tables) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="100">
              <template #default="{ row }">
                <el-tag :type="statusTagType(row.status)">{{ row.status_label }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="row_count" label="行数" width="100" />
            <el-table-column prop="creator" label="创建人" width="120" />
            <el-table-column prop="create_time" label="创建时间" width="170" />
            <el-table-column prop="error" label="失败原因" min-width="180" show-overflow-tooltip />
            <el-table-column label="操作" width="160" fixed="right">
              <template #default="{ row }">
                <el-button
                  v-if="row.status !== 4"
                  link
                  type="primary"
                  :disabled="row.status !== 2"
                  @click="openAddTable(row)"
                >
                  新增表
                </el-button>
                <el-button v-if="row.status !== 4" link type="danger" @click="handleRemove(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
          <el-pagination
            class="pager"
            background
            layout="total, prev, pager, next"
            :total="recordsTotal"
            :current-page="recordsQuery.page"
            :page-size="recordsQuery.page_size"
            @current-change="onRecordsPageChange"
          />
      <el-dialog v-model="manageVisible" :title="manageTitle" width="560px">
        <el-form label-width="90px">
          <el-form-item v-if="manageMode === 'addTable'" label="表">
            <el-select
              v-model="manageForm.key"
              filterable
              style="width: 100%"
              placeholder="选择源快照中的表"
            >
              <el-option
                v-for="item in addTableCandidates"
                :key="item.key"
                :value="item.key"
                :label="`${item.schema}.${item.name}`"
              />
            </el-select>
          </el-form-item>
          <el-form-item v-else label="表">
            <span>{{ manageForm.schema }}.{{ manageForm.name }}</span>
          </el-form-item>
          <el-form-item label="行数">
            <el-input-number v-model="manageForm.rows" :min="1" :max="1000" controls-position="right" />
          </el-form-item>
          <el-form-item label="随机种子">
            <div class="seed-field">
              <el-input-number
                v-model="manageForm.seed"
                :min="0"
                :max="2147483647"
                placeholder="可选"
                controls-position="right"
              />
              <el-tooltip
                placement="top"
                content="相同种子与相同的表/行数会生成相同数据；不填则每次随机。追加数据时内部会错开编号，避免与已有行冲突。"
              >
                <el-icon class="hint-icon"><QuestionFilled /></el-icon>
              </el-tooltip>
            </div>
          </el-form-item>
          <el-alert
            v-if="manageMode === 'append'"
            type="info"
            :closable="false"
            show-icon
            title="追加数据写入现有表；唯一约束冲突时本次操作整体回滚。"
          />
          <el-alert
            v-else
            type="warning"
            :closable="false"
            show-icon
            title="只从源快照新增表；不会还原 CHECK / 触发器 / 视图等。"
          />
        </el-form>
        <template #footer>
          <el-button @click="manageVisible = false">取消</el-button>
          <el-button type="primary" :loading="manageLoading" @click="submitManage">确定</el-button>
        </template>
      </el-dialog>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { authApi } from '@/api/auth'
import { catalogApi, datasourceApi } from '@/api/datasource'
import { provisioningApi } from '@/api/provisioning'
import { useUserStore } from '@/store/user'

const userStore = useUserStore()
const isAdmin = computed(() => userStore.userInfo.role === 'admin')

const createVisible = ref(false)
const createStep = ref(0)
const datasources = ref([])
const datasourceId = ref(null)
const snapshot = ref(null)
const tableRows = ref([])
const tableTotal = ref(0)
const tableKeyword = ref('')
const tableQuery = reactive({ page: 1, page_size: 5 })
const tableLoading = ref(false)
const tableRef = ref(null)
const selectedTables = ref([])
const rowCounts = reactive({})
const seed = ref(null)
const previewTab = ref('tables')

const previewing = ref(false)
const executing = ref(false)
const plan = ref(null)
const confirmed = ref(false)

const records = ref([])
const recordsTotal = ref(0)
const recordsLoading = ref(false)
const recordsKeyword = ref('')
const recordsQuery = reactive({ page: 1, page_size: 10 })

const manageVisible = ref(false)
const manageLoading = ref(false)
const manageMode = ref('append')
const manageRecord = ref(null)
const manageForm = reactive({ key: '', schema: '', name: '', rows: 50, seed: null })
const addTableCandidates = ref([])
const manageTitle = computed(() =>
  manageMode.value === 'addTable' ? '新增表' : `向 ${manageForm.schema}.${manageForm.name} 追加数据`
)

const currentDatasource = computed(() => datasources.value.find((item) => item.id === datasourceId.value) || null)
const isTestEnv = computed(() => currentDatasource.value?.env_type === 2)
const canPreview = computed(
  () =>
    Boolean(selectedTables.value.length) &&
    Boolean(isTestEnv.value) &&
    Boolean(currentDatasource.value?.has_write_credential)
)

async function openCreate() {
  createStep.value = 0
  datasourceId.value = null
  snapshot.value = null
  tableRows.value = []
  tableTotal.value = 0
  tableKeyword.value = ''
  tableQuery.page = 1
  selectedTables.value = []
  plan.value = null
  confirmed.value = false
  previewTab.value = 'tables'
  tableRef.value?.clearSelection()
  Object.keys(rowCounts).forEach((key) => delete rowCounts[key])
  await loadDatasources()
  createVisible.value = true
}

async function loadDatasources() {
  // 普通用户只对接自己的数据源；管理员可看到全部
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
}

async function onDatasourceChange(value) {
  createStep.value = 0
  plan.value = null
  confirmed.value = false
  previewTab.value = 'tables'
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
    tableRows.value = (data.results || []).map((row) => {
      const key = `${row.schema}.${row.name}`
      if (rowCounts[key] === undefined) rowCounts[key] = 50
      return { ...row, key }
    })
    tableTotal.value = data.total || 0
  } finally {
    tableLoading.value = false
  }
}

function searchTables() {
  tableQuery.page = 1
  loadTables()
}

function onTablePageChange(page) {
  tableQuery.page = page
  loadTables()
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

async function handlePreview() {
  if (!canPreview.value) return
  previewing.value = true
  try {
    plan.value = await provisioningApi.preview({
      datasource_id: datasourceId.value,
      tables: buildTables(),
      seed: seed.value ?? undefined
    })
    confirmed.value = false
    previewTab.value = 'tables'
    createStep.value = 1
  } finally {
    previewing.value = false
  }
}

function backToSelect() {
  createStep.value = 0
  plan.value = null
  confirmed.value = false
  previewTab.value = 'tables'
}

async function handleProvision() {
  if (!plan.value || !confirmed.value) return
  executing.value = true
  try {
    const data = await provisioningApi.provision({
      datasource_id: datasourceId.value,
      // 执行使用预览过的表与行数，避免预览后改选择造成「确认的和执行的不一致」
      tables: plan.value.tables.map(({ schema, name, rows }) => ({ schema, name, rows })),
      db_name: plan.value.db_name,
      seed: plan.value.seed,
      confirm: true
    })
    ElMessage.success(`测试库 ${data.db_name} 已创建，共 ${data.row_count} 行`)
    plan.value = null
    confirmed.value = false
    createStep.value = 0
    previewTab.value = 'tables'
    createVisible.value = false
    await loadRecords()
  } finally {
    executing.value = false
  }
}

async function loadRecords() {
  recordsLoading.value = true
  try {
    const data = await provisioningApi.list({
      page: recordsQuery.page,
      page_size: recordsQuery.page_size,
      keyword: recordsKeyword.value
    })
    records.value = data.results || []
    recordsTotal.value = data.total || 0
  } finally {
    recordsLoading.value = false
  }
}

function searchRecords() {
  recordsQuery.page = 1
  loadRecords()
}

function onRecordsPageChange(page) {
  recordsQuery.page = page
  loadRecords()
}

async function handleRemove(row) {
  await ElMessageBox.confirm(`确认删除测试库「${row.db_name}」？此操作不可恢复。`, '提示', { type: 'warning' })
  await provisioningApi.remove(row.id)
  ElMessage.success('已删除')
  loadRecords()
}

function openAppendRows(record, table) {
  manageMode.value = 'append'
  manageRecord.value = record
  manageForm.key = ''
  manageForm.schema = table.schema || ''
  manageForm.name = table.name
  manageForm.rows = 50
  manageForm.seed = null
  manageVisible.value = true
}

async function openAddTable(record) {
  manageMode.value = 'addTable'
  manageRecord.value = record
  addTableCandidates.value = []
  manageForm.key = ''
  manageForm.rows = 50
  manageForm.seed = null
  try {
    const snapshots = await catalogApi.snapshots({
      datasource_id: record.datasource_id,
      page: 1,
      page_size: 1
    })
    const snapshot = (snapshots.results || [])[0]
    if (!snapshot) {
      ElMessage.warning('该数据源没有快照，无法新增表')
      return
    }
    const existing = new Set((record.tables || []).map((item) => `${item.schema}.${item.name}`))
    const data = await catalogApi.tables({ snapshot_id: snapshot.id, page: 1, page_size: 1000 })
    addTableCandidates.value = (data.results || [])
      .map((item) => ({ ...item, key: `${item.schema}.${item.name}` }))
      .filter((item) => !existing.has(item.key))
    if (!addTableCandidates.value.length) {
      ElMessage.info('源快照中没有可新增的表')
      return
    }
    manageForm.key = addTableCandidates.value[0].key
    manageVisible.value = true
  } catch {
    // 拦截器已提示
  }
}

async function submitManage() {
  if (!manageRecord.value) return
  let payload
  if (manageMode.value === 'addTable') {
    const candidate = addTableCandidates.value.find((item) => item.key === manageForm.key)
    if (!candidate) {
      ElMessage.warning('请选择要新增的表')
      return
    }
    payload = {
      schema: candidate.schema,
      name: candidate.name,
      rows: manageForm.rows,
      seed: manageForm.seed ?? undefined
    }
  } else {
    payload = {
      schema: manageForm.schema,
      name: manageForm.name,
      rows: manageForm.rows,
      seed: manageForm.seed ?? undefined
    }
  }

  manageLoading.value = true
  try {
    const data =
      manageMode.value === 'addTable'
        ? await provisioningApi.addTable(manageRecord.value.id, payload)
        : await provisioningApi.appendRows(manageRecord.value.id, payload)
    ElMessage.success(manageMode.value === 'addTable' ? '表已新增并写入数据' : '数据已追加')
    if (data.warnings && data.warnings.length) {
      ElMessage.warning(data.warnings.join('；'))
    }
    manageVisible.value = false
    await loadRecords()
  } finally {
    manageLoading.value = false
  }
}

async function handleClearRows(record, table) {
  const label = `${table.schema ? `${table.schema}.` : ''}${table.name}`
  await ElMessageBox.confirm(
    `确认清空「${label}」的全部数据？被其他表外键引用时会失败。`,
    '提示',
    { type: 'warning' }
  )
  await provisioningApi.clearRows(record.id, { schema: table.schema, name: table.name })
  ElMessage.success('数据已清空')
  loadRecords()
}

async function handleDropTable(record, table) {
  const label = `${table.schema ? `${table.schema}.` : ''}${table.name}`
  await ElMessageBox.confirm(
    `确认删除表「${label}」？此操作不可恢复；被其他表外键引用时会失败。`,
    '提示',
    { type: 'warning' }
  )
  await provisioningApi.dropTable(record.id, { schema: table.schema, name: table.name })
  ElMessage.success('表已删除')
  loadRecords()
}

function tableNames(tables) {
  return (tables || [])
    .map((item) => `${item.schema ? `${item.schema}.` : ''}${item.name}`)
    .join('、')
}

function statusTagType(status) {
  if (status === 2) return 'success'
  if (status === 3) return 'danger'
  if (status === 4) return 'info'
  return 'warning'
}

onMounted(loadRecords)
</script>

<style scoped>
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.create-dialog-body {
  max-height: 64vh;
  overflow: auto;
  padding-right: 4px;
}

.header-actions {
  display: flex;
  gap: 8px;
}

.notice {
  margin-bottom: 12px;
}

.picker-row {
  margin-bottom: 12px;
}

.env-info {
  display: flex;
  align-items: center;
  gap: 8px;
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

.record-tables {
  padding: 8px 16px;
}

.record-meta {
  margin-bottom: 8px;
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

.record-hint {
  margin-top: 8px;
}

.meta {
  font-size: 12px;
  color: #909399;
}

.create-steps {
  margin-bottom: 16px;
}

.table-pager {
  margin-top: 12px;
  justify-content: flex-end;
}

.preview-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 24px;
  padding: 12px 16px;
  margin-bottom: 12px;
  background: #f5f7fa;
  border-radius: 4px;
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
  font-size: 14px;
  font-family: Consolas, Monaco, monospace;
  word-break: break-all;
}

.preview-tabs {
  margin-top: 8px;
}

.preview-ddl {
  max-height: 240px;
}

.confirm-check {
  display: block;
  margin-top: 12px;
}

.dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

.semantic-tag {
  margin: 2px 4px 2px 0;
}

.muted {
  color: #909399;
  font-size: 12px;
}

.ddl {
  max-height: 320px;
  overflow: auto;
  padding: 12px;
  background: #f5f7fa;
  border-radius: 4px;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
}

.pager {
  margin-top: 16px;
  justify-content: flex-end;
}
</style>
