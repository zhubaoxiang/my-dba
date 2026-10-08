<template>
  <div class="rules-page">
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <div>
            <span>分析规则</span>
            <span class="hint">
              规则逻辑由代码声明；库表规则可调整启用、级别与阈值，SQL 规则只读
            </span>
          </div>
        </div>
      </template>

      <el-tabs v-model="activeTab">
        <!-- 库表规则：可调。清单以代码声明为基底，改过哪条才在库里留下记录 -->
        <el-tab-pane :label="`库表规则 ${dsTotal}`" name="datasource">
          <el-table :data="dsRows" v-loading="dsLoading" border>
            <el-table-column prop="code" label="规则标识" min-width="180" />
            <el-table-column prop="name" label="名称" min-width="150" />
            <el-table-column prop="description" label="说明" min-width="240" show-overflow-tooltip />
            <el-table-column prop="object_level_label" label="适用层级" width="100" />
            <el-table-column label="严重级别" width="130">
              <template #default="{ row }">
                <el-select :model-value="row.level" size="small" @change="(value) => changeLevel(row, value)">
                  <el-option v-for="item in LEVELS" :key="item.value" :value="item.value" :label="item.label" />
                </el-select>
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
            <!-- 启用开关与「恢复默认」同格：两者都是「对这条规则做的事」。
                 开关是持续状态，恢复默认是一次性纠正，故开关在前、按钮在后 -->
            <el-table-column label="操作" width="180">
              <template #default="{ row }">
                <div class="actions">
                  <el-switch
                    :model-value="row.enabled"
                    size="small"
                    @change="(value) => changeEnabled(row, value)"
                  />
                  <el-button link type="primary" size="small" :disabled="!row.is_overridden" @click="resetRule(row)">
                    恢复默认
                  </el-button>
                </div>
              </template>
            </el-table-column>
          </el-table>

          <el-pagination
            class="pager"
            layout="total, prev, pager, next"
            :total="dsTotal"
            :current-page="dsPage"
            :page-size="PAGE_SIZE"
            @current-change="onDsPageChange"
          />
        </el-tab-pane>

        <!-- SQL 规则：只读。它们没有覆盖机制，因此这里**不出现**启用开关与级别下拉——
             置灰的控件会暗示「本来可以，只是现在不行」，比没有控件更误导 -->
        <el-tab-pane :label="`SQL 规则 ${sqlTotal}`" name="sql">
          <el-table :data="sqlRows" v-loading="sqlLoading" border>
            <el-table-column prop="code" label="规则标识" min-width="200" />
            <el-table-column prop="name" label="名称" min-width="150" />
            <el-table-column prop="description" label="说明" min-width="240" show-overflow-tooltip />
            <el-table-column prop="level_label" label="严重级别" width="110" />
            <el-table-column label="需表结构" width="120">
              <template #default="{ row }">
                <el-tag v-if="row.needs_schema" type="info" size="small">需表结构</el-tag>
                <span v-else class="muted">—</span>
              </template>
            </el-table-column>
          </el-table>

          <p class="note">
            「需表结构」的规则要绑定数据源才能判定：拿不到表结构时它们会被跳过，并在分析结果里说明原因。
          </p>

          <el-pagination
            class="pager"
            layout="total, prev, pager, next"
            :total="sqlTotal"
            :current-page="sqlPage"
            :page-size="PAGE_SIZE"
            @current-change="onSqlPageChange"
          />
        </el-tab-pane>
      </el-tabs>
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
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { analysisRuleApi } from '@/api/datasource'
import { sqlAnalysisApi } from '@/api/sqlAnalysis'

const route = useRoute()

// 级别固定三档，与后端 IssueLevelEnum 一致
const LEVELS = [
  { value: 1, label: '高' },
  { value: 2, label: '中' },
  { value: 3, label: '低' }
]

const PAGE_SIZE = 20

// 首页两格各自深链到对应那组（?tab=sql）；没有参数时落在库表规则——那是可调的一组
const activeTab = ref(route.query.tab === 'sql' ? 'sql' : 'datasource')

const dsRows = ref([])
const dsTotal = ref(0)
const dsPage = ref(1)
const dsLoading = ref(false)

const sqlRows = ref([])
const sqlTotal = ref(0)
const sqlPage = ref(1)
const sqlLoading = ref(false)

const saving = ref(false)
const thresholdVisible = ref(false)
const thresholdDraft = ref({})
const current = ref(null)

function thresholdKeys(row) {
  return Object.keys(row?.thresholds || {})
}

async function loadDatasourceRules() {
  dsLoading.value = true
  try {
    const data = await analysisRuleApi.list({ page: dsPage.value, page_size: PAGE_SIZE })
    dsRows.value = data.results || []
    dsTotal.value = data.count || 0
  } finally {
    dsLoading.value = false
  }
}

async function loadSqlRules() {
  sqlLoading.value = true
  try {
    const data = await sqlAnalysisApi.rules({ page: sqlPage.value, page_size: PAGE_SIZE })
    sqlRows.value = data.results || []
    sqlTotal.value = data.count || 0
  } finally {
    sqlLoading.value = false
  }
}

function onDsPageChange(value) {
  dsPage.value = value
  loadDatasourceRules()
}

function onSqlPageChange(value) {
  sqlPage.value = value
  loadSqlRules()
}

// 两组各取各的接口：SQL 规则没有覆盖机制，与库表规则不是同一个资源，
// 硬合成一个「统一规则」接口只会多出一个没有归属的真相源
async function applyUpdate(row, payload) {
  const data = await analysisRuleApi.update(row.code, payload)
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
  const data = await analysisRuleApi.reset(row.code)
  Object.assign(row, data)
  ElMessage.success('已恢复默认值')
}

onMounted(() => {
  loadDatasourceRules()
  loadSqlRules()
})
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

.actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.note {
  margin-top: 10px;
  font-size: 12px;
  color: #909399;
  line-height: 1.6;
}

.pager {
  margin-top: 14px;
  justify-content: flex-end;
}
</style>
