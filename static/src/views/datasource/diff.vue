<template>
  <div class="diff-page">
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span>结构对比</span>
          <div class="header-actions">
            <el-checkbox v-model="ignoreSchema" :disabled="comparing" @change="onSnapshotChange">
              忽略 schema（按表名对齐）
            </el-checkbox>
            <el-button
              type="primary"
              :disabled="!canCompare || sameSnapshot"
              :loading="comparing"
              @click="runDiff"
            >
              开始对比
            </el-button>
            <el-button @click="reload">刷新</el-button>
          </div>
        </div>
      </template>

      <!-- 两侧各自选数据源与快照；默认取最新，同源时对比侧默认取上一次 -->
      <el-row :gutter="16" class="pickers">
        <el-col :span="12">
          <div class="picker">
            <div class="picker-title">基准快照</div>
            <div class="picker-controls">
              <el-select
                v-model="baseDatasourceId"
                placeholder="选择数据源"
                filterable
                style="width: 200px"
                @change="onDatasourceChange('base')"
              >
                <el-option v-for="item in datasources" :key="item.id" :value="item.id" :label="item.name" />
              </el-select>
              <el-select
                v-model="baseSnapshotId"
                placeholder="选择快照"
                filterable
                :loading="baseLoading"
                style="width: 280px"
                @change="onSnapshotChange"
              >
                <el-option v-for="s in baseSnapshots" :key="s.id" :value="s.id" :label="snapshotLabel(s)" />
              </el-select>
            </div>
          </div>
        </el-col>
        <el-col :span="12">
          <div class="picker">
            <div class="picker-title">对比对象</div>
            <div class="picker-controls">
              <el-select
                v-model="compareDatasourceId"
                placeholder="选择数据源"
                filterable
                style="width: 200px"
                @change="onDatasourceChange('compare')"
              >
                <el-option v-for="item in datasources" :key="item.id" :value="item.id" :label="item.name" />
              </el-select>
              <el-select
                v-model="compareSnapshotId"
                placeholder="选择快照"
                filterable
                :loading="compareLoading"
                style="width: 280px"
                @change="onSnapshotChange"
              >
                <el-option v-for="s in compareSnapshots" :key="s.id" :value="s.id" :label="snapshotLabel(s)" />
              </el-select>
            </div>
          </div>
        </el-col>
      </el-row>

      <el-alert
        v-if="sameSnapshot"
        class="notice"
        type="info"
        :closable="false"
        show-icon
        title="两侧选择了同一个快照，请选择不同的快照"
      />
      <el-alert
        v-for="(warning, index) in warnings"
        :key="index"
        class="notice"
        type="warning"
        :closable="false"
        show-icon
        :title="warning"
      />

      <el-empty v-if="notCollected" description="某一侧数据源尚未采集，请先到「数据源管理」触发采集" />
      <el-empty v-else-if="!canCompare" description="请为两侧选择数据源与快照" />
      <el-empty v-else-if="!result" description="点击「开始对比」查看结构差异" />

      <template v-else>
        <el-row :gutter="12" class="summary-row">
          <el-col :span="8">
            <el-card shadow="hover">
              <div class="stat-value success">{{ summary.added_table_count }}</div>
              <div class="stat-title">新增表</div>
            </el-card>
          </el-col>
          <el-col :span="8">
            <el-card shadow="hover">
              <div class="stat-value danger">{{ summary.removed_table_count }}</div>
              <div class="stat-title">删除表</div>
            </el-card>
          </el-col>
          <el-col :span="8">
            <el-card shadow="hover">
              <div class="stat-value warning">{{ summary.changed_table_count }}</div>
              <div class="stat-title">变更表</div>
            </el-card>
          </el-col>
        </el-row>

        <el-empty v-if="!hasDiff" description="两个快照之间没有结构差异" />

        <el-tabs v-else v-model="activeTab">
          <el-tab-pane :label="`新增表 ${tableDiff.added_tables.length}`" name="added">
            <el-table :data="addedRows" border>
              <el-table-column prop="table" :label="tableKeyLabel" min-width="260" />
            </el-table>
          </el-tab-pane>

          <el-tab-pane :label="`删除表 ${tableDiff.removed_tables.length}`" name="removed">
            <el-table :data="removedRows" border>
              <el-table-column prop="table" :label="tableKeyLabel" min-width="260" />
            </el-table>
          </el-tab-pane>

          <el-tab-pane :label="`变更表 ${tableDiff.changed_tables.length}`" name="changed">
            <el-table :data="tableDiff.changed_tables" border row-key="table">
              <el-table-column type="expand">
                <template #default="{ row }">
                  <div class="detail">
                    <div class="detail-block">
                      <div class="detail-title">列</div>
                      <div v-if="!row.added_columns.length && !row.removed_columns.length && !row.changed_columns.length" class="muted">
                        列没有变化
                      </div>
                      <div v-if="row.added_columns.length" class="tag-line">
                        <span class="tag-label">新增</span>
                        <el-tag v-for="name in row.added_columns" :key="`a-${name}`" type="success" size="small">
                          {{ name }}
                        </el-tag>
                      </div>
                      <div v-if="row.removed_columns.length" class="tag-line">
                        <span class="tag-label">删除</span>
                        <el-tag v-for="name in row.removed_columns" :key="`r-${name}`" type="danger" size="small">
                          {{ name }}
                        </el-tag>
                      </div>
                      <el-table
                        v-if="row.changed_columns.length"
                        :data="row.changed_columns"
                        border
                        size="small"
                        class="inner-table"
                      >
                        <el-table-column prop="name" label="列" width="180" />
                        <el-table-column prop="before" label="变更前" min-width="200" />
                        <el-table-column prop="after" label="变更后" min-width="200" />
                      </el-table>
                    </div>

                    <div class="detail-block">
                      <div class="detail-title">索引</div>
                      <div v-if="!row.added_indexes.length && !row.removed_indexes.length" class="muted">
                        索引没有变化
                      </div>
                      <div v-if="row.added_indexes.length" class="tag-line">
                        <span class="tag-label">新增</span>
                        <el-tag v-for="name in row.added_indexes" :key="`ai-${name}`" type="success" size="small">
                          {{ name }}
                        </el-tag>
                      </div>
                      <div v-if="row.removed_indexes.length" class="tag-line">
                        <span class="tag-label">删除</span>
                        <el-tag v-for="name in row.removed_indexes" :key="`ri-${name}`" type="danger" size="small">
                          {{ name }}
                        </el-tag>
                      </div>
                    </div>
                  </div>
                </template>
              </el-table-column>
              <el-table-column prop="table" :label="tableKeyLabel" min-width="260" />
              <el-table-column label="列" width="200">
                <template #default="{ row }">
                  <div class="tag-line">
                    <el-tag v-if="row.added_columns.length" type="success" size="small">+{{ row.added_columns.length }}</el-tag>
                    <el-tag v-if="row.removed_columns.length" type="danger" size="small">-{{ row.removed_columns.length }}</el-tag>
                    <el-tag v-if="row.changed_columns.length" type="warning" size="small">~{{ row.changed_columns.length }}</el-tag>
                    <span
                      v-if="!row.added_columns.length && !row.removed_columns.length && !row.changed_columns.length"
                      class="muted"
                    >
                      —
                    </span>
                  </div>
                </template>
              </el-table-column>
              <el-table-column label="索引" width="160">
                <template #default="{ row }">
                  <div class="tag-line">
                    <el-tag v-if="row.added_indexes.length" type="success" size="small">+{{ row.added_indexes.length }}</el-tag>
                    <el-tag v-if="row.removed_indexes.length" type="danger" size="small">-{{ row.removed_indexes.length }}</el-tag>
                    <span v-if="!row.added_indexes.length && !row.removed_indexes.length" class="muted">—</span>
                  </div>
                </template>
              </el-table-column>
            </el-table>
          </el-tab-pane>
        </el-tabs>
      </template>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { catalogApi, datasourceApi } from '@/api/datasource'

const route = useRoute()

const datasources = ref([])
const baseDatasourceId = ref(null)
const compareDatasourceId = ref(null)
const baseSnapshots = ref([])
const compareSnapshots = ref([])
const baseSnapshotId = ref(null)
const compareSnapshotId = ref(null)
const baseLoading = ref(false)
const compareLoading = ref(false)
const datasourcesLoaded = ref(false)
const ignoreSchema = ref(false)
const comparing = ref(false)
const result = ref(null)
const activeTab = ref('changed')

const canCompare = computed(() => Boolean(baseSnapshotId.value && compareSnapshotId.value))
const sameSnapshot = computed(() => canCompare.value && baseSnapshotId.value === compareSnapshotId.value)
// 两侧数据源都选了、但至少一侧没有快照——与「还没选完」分开提示
const notCollected = computed(
  () =>
    datasourcesLoaded.value &&
    Boolean(baseDatasourceId.value && compareDatasourceId.value) &&
    !baseLoading.value &&
    !compareLoading.value &&
    (!baseSnapshots.value.length || !compareSnapshots.value.length)
)
const tableDiff = computed(() => result.value?.diff || { added_tables: [], removed_tables: [], changed_tables: [] })
const summary = computed(() => result.value?.summary || {})
const warnings = computed(() => result.value?.warnings || [])
const tableKeyLabel = computed(() => (ignoreSchema.value ? '表名' : '表（schema.表名）'))
const addedRows = computed(() => tableDiff.value.added_tables.map((name) => ({ table: name })))
const removedRows = computed(() => tableDiff.value.removed_tables.map((name) => ({ table: name })))
const hasDiff = computed(
  () =>
    Boolean(tableDiff.value.added_tables.length) ||
    Boolean(tableDiff.value.removed_tables.length) ||
    Boolean(tableDiff.value.changed_tables.length)
)

async function loadDatasources() {
  const data = await datasourceApi.list({ page: 1, page_size: 200 })
  datasources.value = data.results || []
  baseDatasourceId.value = pickDatasource(Number(route.query.datasource_id))
  compareDatasourceId.value = pickDatasource(Number(route.query.compare_datasource_id), baseDatasourceId.value)
  await Promise.all([loadSnapshots('base'), loadSnapshots('compare')])
  datasourcesLoaded.value = true
  if (route.query.snapshot_id || route.query.compare_snapshot_id) runDiff()
}

function pickDatasource(preferred, exclude) {
  if (preferred && datasources.value.some((item) => item.id === preferred)) return preferred
  const candidate = datasources.value.find((item) => item.id !== exclude) || datasources.value[0]
  return candidate?.id || null
}

async function loadSnapshots(side) {
  const isBase = side === 'base'
  const datasourceId = isBase ? baseDatasourceId.value : compareDatasourceId.value
  const list = isBase ? baseSnapshots : compareSnapshots
  const selected = isBase ? baseSnapshotId : compareSnapshotId
  const loading = isBase ? baseLoading : compareLoading
  const presetKey = isBase ? 'snapshot_id' : 'compare_snapshot_id'

  result.value = null
  if (!datasourceId) {
    list.value = []
    selected.value = null
    return
  }
  loading.value = true
  try {
    const data = await catalogApi.snapshots({ datasource_id: datasourceId, page: 1, page_size: 50 })
    list.value = data.results || []
    const preset = Number(route.query[presetKey])
    if (preset && list.value.some((snapshot) => snapshot.id === preset)) {
      selected.value = preset
    } else {
      selected.value = defaultSnapshotId(side)
    }
  } finally {
    loading.value = false
  }
}

function defaultSnapshotId(side) {
  const isBase = side === 'base'
  const list = isBase ? baseSnapshots.value : compareSnapshots.value
  if (!list.length) return null
  // 同一数据源对比时，对比侧默认取上一次快照，否则两侧会默认选到同一份
  if (!isBase && baseDatasourceId.value === compareDatasourceId.value && list.length > 1) {
    return list[1].id
  }
  return list[0].id
}

async function onDatasourceChange(side) {
  if (side === 'base') baseSnapshotId.value = null
  else compareSnapshotId.value = null
  result.value = null
  await loadSnapshots(side)
}

function onSnapshotChange() {
  result.value = null
}

async function runDiff() {
  if (!canCompare.value || sameSnapshot.value) return
  comparing.value = true
  try {
    result.value = await catalogApi.diff({
      snapshot_id: baseSnapshotId.value,
      compare_snapshot_id: compareSnapshotId.value,
      ignore_schema: ignoreSchema.value ? 1 : undefined
    })
    activeTab.value = firstNonEmptyTab()
  } finally {
    comparing.value = false
  }
}

function firstNonEmptyTab() {
  const diff = result.value?.diff
  if (!diff) return 'changed'
  if (diff.added_tables.length) return 'added'
  if (diff.removed_tables.length) return 'removed'
  return 'changed'
}

async function reload() {
  await Promise.all([loadSnapshots('base'), loadSnapshots('compare')])
  result.value = null
}

function snapshotLabel(snapshot) {
  return `#${snapshot.id} · ${snapshot.collect_time} · ${snapshot.table_count} 张表`
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
  align-items: center;
  gap: 8px;
}

.pickers {
  margin-bottom: 12px;
}

.picker {
  padding: 12px;
  background: #f5f7fa;
  border-radius: 4px;
}

.picker-title {
  margin-bottom: 8px;
  font-size: 13px;
  color: #606266;
}

.picker-controls {
  display: flex;
  gap: 8px;
}

.notice {
  margin-bottom: 8px;
}

.summary-row {
  margin: 16px 0;
  text-align: center;
}

.stat-value {
  font-size: 24px;
  font-weight: 600;
  color: #303133;
}

.stat-value.success {
  color: #67c23a;
}

.stat-value.danger {
  color: #f56c6c;
}

.stat-value.warning {
  color: #e6a23c;
}

.stat-title {
  font-size: 13px;
  color: #909399;
  margin-top: 4px;
}

.detail {
  padding: 8px 16px;
}

.detail-block + .detail-block {
  margin-top: 16px;
}

.detail-title {
  margin-bottom: 8px;
  font-weight: 600;
  font-size: 13px;
  color: #303133;
}

.tag-line {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
}

.tag-label {
  font-size: 12px;
  color: #909399;
  margin-right: 4px;
}

.inner-table {
  margin-top: 8px;
}

.muted {
  color: #909399;
  font-size: 12px;
}
</style>
