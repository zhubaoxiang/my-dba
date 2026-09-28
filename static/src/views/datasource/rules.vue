<template>
  <div class="rules-page">
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <div>
            <span>分析规则</span>
            <span class="hint">规则逻辑由代码声明，此处只调整启用、级别与阈值</span>
          </div>
          <el-button type="primary" :loading="syncing" @click="syncRules">同步规则</el-button>
        </div>
      </template>

      <el-table :data="rows" v-loading="loading" border>
        <el-table-column prop="code" label="规则标识" min-width="180" />
        <el-table-column prop="name" label="名称" min-width="150" />
        <el-table-column prop="object_level_label" label="适用层级" width="100" />
        <el-table-column label="严重级别" width="130">
          <template #default="{ row }">
            <el-select :model-value="row.level" size="small" @change="(value) => changeLevel(row, value)">
              <el-option v-for="item in LEVELS" :key="item.value" :value="item.value" :label="item.label" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column label="启用" width="90">
          <template #default="{ row }">
            <el-switch :model-value="row.enabled" @change="(value) => changeEnabled(row, value)" />
          </template>
        </el-table-column>
        <el-table-column label="阈值" min-width="180">
          <template #default="{ row }">
            <span v-if="!thresholdKeys(row).length" class="muted">无</span>
            <template v-else>
              <span class="muted">{{ thresholdKeys(row).length }} 项</span>
              <el-button link type="primary" size="small" @click="openThresholds(row)">编辑</el-button>
            </template>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag v-if="row.is_overridden" type="warning" size="small">已调整</el-tag>
            <span v-else class="muted">默认</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="110">
          <template #default="{ row }">
            <el-button link type="primary" size="small" :disabled="!row.is_overridden" @click="resetRule(row)">
              恢复默认
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-pagination
        class="pager"
        layout="total, prev, pager, next"
        :total="total"
        :current-page="page"
        :page-size="pageSize"
        @current-change="onPageChange"
      />
    </el-card>

    <el-dialog v-model="thresholdVisible" :title="`阈值 · ${current?.name || ''}`" width="460px">
      <el-form label-width="180px">
        <el-form-item v-for="key in thresholdKeys(current)" :key="key" :label="key">
          <el-input-number v-model="thresholdDraft[key]" :min="0" controls-position="right" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="thresholdVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveThresholds">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { analysisRuleApi } from '@/api/datasource'

// 级别固定三档，与后端 IssueLevelEnum 一致
const LEVELS = [
  { value: 1, label: '高' },
  { value: 2, label: '中' },
  { value: 3, label: '低' }
]

const rows = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = 20
const loading = ref(false)
const syncing = ref(false)
const saving = ref(false)
const thresholdVisible = ref(false)
const thresholdDraft = ref({})
const current = ref(null)

function thresholdKeys(row) {
  return Object.keys(row?.thresholds || {})
}

async function load() {
  loading.value = true
  try {
    const data = await analysisRuleApi.list({ page: page.value, page_size: pageSize })
    rows.value = data.results || []
    total.value = data.count || 0
  } finally {
    loading.value = false
  }
}

function onPageChange(value) {
  page.value = value
  load()
}

async function applyUpdate(row, payload) {
  const data = await analysisRuleApi.update(row.id, payload)
  Object.assign(row, data)
  ElMessage.success('已保存')
}

async function changeEnabled(row, value) {
  await applyUpdate(row, { enabled: value })
}

async function changeLevel(row, value) {
  await applyUpdate(row, { level: value })
}

function openThresholds(row) {
  current.value = row
  thresholdDraft.value = { ...(row.thresholds || {}) }
  thresholdVisible.value = true
}

async function saveThresholds() {
  saving.value = true
  try {
    await applyUpdate(current.value, { thresholds: { ...thresholdDraft.value } })
    thresholdVisible.value = false
  } finally {
    saving.value = false
  }
}

async function resetRule(row) {
  await ElMessageBox.confirm(`把「${row.name}」恢复为代码声明的默认值？`, '提示', { type: 'warning' })
  const data = await analysisRuleApi.reset(row.id)
  Object.assign(row, data)
  ElMessage.success('已恢复默认值')
}

async function syncRules() {
  syncing.value = true
  try {
    const data = await analysisRuleApi.sync()
    ElMessage.success(`同步完成：新增 ${data.created} 条，更新 ${data.updated} 条，共 ${data.total} 条`)
    await load()
  } finally {
    syncing.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.hint {
  margin-left: 10px;
  font-size: 12px;
  color: #909399;
}

.muted {
  color: #909399;
  font-size: 12px;
  margin-right: 6px;
}

.pager {
  margin-top: 14px;
  justify-content: flex-end;
}
</style>
