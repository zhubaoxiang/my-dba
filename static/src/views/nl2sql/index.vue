<template>
  <div class="nl2sql-page">
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span>SQL 生成</span>
          <span class="hint">选定数据源，用自然语言描述查询需求</span>
        </div>
      </template>

      <el-form label-width="80px">
        <el-form-item label="数据源" required>
          <el-select
            v-model="datasourceId"
            filterable
            placeholder="选择一个已采集的数据源"
            style="width: 320px"
            @change="onDatasourceChange"
          >
            <el-option v-for="item in datasources" :key="item.id" :value="item.id" :label="item.name" />
          </el-select>
          <span v-if="!datasources.length" class="field-hint">暂无数据源，请先在「数据源管理」中接入并采集。</span>
        </el-form-item>

        <el-form-item label="表范围">
          <el-select
            v-model="tables"
            multiple
            filterable
            remote
            reserve-keyword
            :remote-method="searchTables"
            :loading="tablesLoading"
            :disabled="!datasourceId"
            placeholder="留空则使用全库表结构"
            style="width: 100%"
          >
            <el-option v-for="item in tableOptions" :key="item.name" :value="item.name" :label="item.name">
              <span>{{ item.name }}</span>
              <span v-if="item.comment" class="option-comment">{{ item.comment }}</span>
            </el-option>
          </el-select>
          <div class="field-hint">
            表数量较多时建议限定范围：参与的表越多，越可能超出模型可承载的结构范围。
          </div>
        </el-form-item>

        <el-form-item label="需求">
          <el-input
            v-model="question"
            type="textarea"
            :rows="3"
            maxlength="2000"
            show-word-limit
            placeholder="例如：找出上个月下单超过 3 次的用户，按订单数倒序取前 20"
          />
        </el-form-item>

        <el-form-item>
          <el-button type="primary" :loading="generating" :disabled="!datasourceId" @click="generate">生成</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card v-if="result" shadow="never" class="result-card">
      <template #header>
        <div class="card-header">
          <span>生成结果</span>
          <el-tag v-if="result.structure" type="info" size="small" effect="plain">
            依据 {{ result.structure.table_count }} 张表
          </el-tag>
        </div>
      </template>

      <!-- 模型不可用时如实说明，不展示空壳结果 -->
      <el-alert
        v-if="!result.generation.available"
        type="info"
        :closable="false"
        show-icon
        :title="result.generation.note || '本次未能生成'"
      />

      <template v-else>
        <el-alert
          :type="verdictType(result.verdict.level)"
          :closable="false"
          show-icon
          :title="`${result.verdict.label}：${result.verdict.text}`"
        />

        <!-- 自修经过：改过就要说，没改过就不提 -->
        <el-alert
          v-if="result.repair.note"
          class="repair-note"
          type="warning"
          :closable="false"
          show-icon
          :title="result.repair.note"
        />

        <el-divider content-position="left">生成的语句</el-divider>
        <pre class="code">{{ result.sql }}</pre>

        <!-- 写语句必须标明不会被执行：否则会让人以为系统替他跑了 -->
        <el-alert
          v-if="result.modifies_data"
          class="write-note"
          type="warning"
          :closable="false"
          show-icon
          title="该语句会修改数据或结构；系统不会执行它，试运行也不会。"
        />

        <div class="actions">
          <el-button size="small" @click="copySql">复制</el-button>
          <el-tooltip :content="runTip" placement="top">
            <el-button size="small" type="primary" :loading="running" @click="runSql">试运行</el-button>
          </el-tooltip>
        </div>

        <!-- 这条必须常驻：能力越像「智能」，越容易被把「结构没问题」读成「结果没问题」 -->
        <el-alert class="scope-note" type="info" :closable="false" show-icon>
          <template #title>
            校验仅覆盖<strong>结构</strong>：表名与列名是否存在、类型是否可比。<strong>不覆盖业务语义</strong>。
          </template>
        </el-alert>

        <div class="muted section-hint">
          结构校验：{{
            result.schema_check.performed
              ? `已进行，快照含 ${result.schema_check.table_count} 张表`
              : result.schema_check.note
          }}
        </div>

        <el-divider content-position="left">问题清单（{{ result.issues.length }}）</el-divider>
        <el-empty v-if="!result.issues.length" description="规则未判出问题" :image-size="60" />
        <el-table v-else :data="result.issues" border size="small">
          <el-table-column label="级别" width="70">
            <template #default="{ row }">
              <el-tag :type="levelTagType(row.issue_level)" size="small">{{ levelLabel(row.issue_level) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="rule_name" label="规则" width="150" />
          <el-table-column prop="target" label="对象" min-width="120" show-overflow-tooltip />
          <el-table-column prop="description" label="说明" min-width="200" show-overflow-tooltip />
        </el-table>

        <div v-if="result.skipped_rules.length" class="muted skipped">
          因缺少表结构而跳过的规则：{{ result.skipped_rules.join('、') }}
        </div>

        <template v-if="execution">
          <el-divider content-position="left">试运行</el-divider>
          <el-alert v-if="execution.note" type="warning" :closable="false" show-icon :title="execution.note" />
          <div class="execution-meta">
            <el-tag v-if="execution.executed" type="success" size="small">
              已执行 · {{ execution.row_count }} 行 · {{ execution.duration_ms }}ms
            </el-tag>
            <el-tag v-else type="warning" size="small">未执行</el-tag>
          </div>
          <pre v-if="execution.plan" class="code plan">{{ execution.plan }}</pre>
        </template>
      </template>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { datasourceApi, catalogApi } from '@/api/datasource'
import { nl2sqlApi } from '@/api/nl2sql'
import { sqlAnalysisApi } from '@/api/sqlAnalysis'

const LEVEL_LABELS = { 1: '高', 2: '中', 3: '低' }
const LEVEL_TYPES = { 1: 'danger', 2: 'warning', 3: 'info' }

const datasources = ref([])
const datasourceId = ref(null)
const tables = ref([])
const tableOptions = ref([])
const tablesLoading = ref(false)
const question = ref('')
const generating = ref(false)
const result = ref(null)
const running = ref(false)
const execution = ref(null)

// 试运行对写语句只出计划——tip 要说准，对写语句讲「会在真实库上执行」是错的
const runTip = computed(() =>
  result.value?.modifies_data
    ? '试运行不会执行这类语句，仅生成执行计划'
    : '试运行会在真实库上执行这条语句'
)

function levelLabel(level) {
  return LEVEL_LABELS[level] || ''
}

function levelTagType(level) {
  return LEVEL_TYPES[level] || 'info'
}

function verdictType(level) {
  // 结论级别与问题级别同源：高=danger、中=warning、低/无=success
  return LEVEL_TYPES[level] || 'success'
}

async function loadDatasources() {
  const data = await datasourceApi.list({ page: 1, page_size: 100 })
  datasources.value = data.results || []
}

async function searchTables(keyword) {
  if (!datasourceId.value) {
    return
  }
  tablesLoading.value = true
  try {
    const data = await catalogApi.tables({
      datasource_id: datasourceId.value,
      keyword: keyword || '',
      page: 1,
      page_size: 50
    })
    tableOptions.value = data.results || []
  } finally {
    tablesLoading.value = false
  }
}

// 换数据源必须清掉表范围：上一条数据源的表名在新库里多半不存在
function onDatasourceChange() {
  tables.value = []
  tableOptions.value = []
  result.value = null
  execution.value = null
  searchTables('')
}

async function generate() {
  if (!question.value.trim()) {
    ElMessage.warning('请输入查询需求')
    return
  }
  generating.value = true
  execution.value = null
  try {
    result.value = await nl2sqlApi.generate({
      datasource_id: datasourceId.value,
      question: question.value.trim(),
      tables: tables.value
    })
  } finally {
    generating.value = false
  }
}

async function runSql() {
  running.value = true
  try {
    execution.value = await sqlAnalysisApi.execute({
      sql: result.value.sql,
      datasource_id: datasourceId.value
    })
  } finally {
    running.value = false
  }
}

async function copySql() {
  try {
    await navigator.clipboard.writeText(result.value.sql)
    ElMessage.success('已复制')
  } catch {
    ElMessage.warning('复制失败，请手动选中复制')
  }
}

onMounted(loadDatasources)
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

.field-hint {
  margin-top: 4px;
  font-size: 12px;
  color: #909399;
  line-height: 1.6;
}

.option-comment {
  float: right;
  margin-left: 16px;
  font-size: 12px;
  color: #909399;
}

.result-card {
  margin-top: 16px;
}

.repair-note,
.scope-note,
.write-note {
  margin-top: 12px;
}

.code {
  margin: 0;
  padding: 12px;
  background: #f5f7fa;
  border: 1px solid #e4e7ed;
  border-radius: 4px;
  font-size: 13px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-all;
}

.code.plan {
  margin-top: 10px;
  font-size: 12px;
}

.actions {
  margin-top: 10px;
  display: flex;
  align-items: center;
}

.section-hint {
  margin-top: 12px;
  font-size: 12px;
}

.muted {
  color: #909399;
}

.skipped {
  margin-top: 8px;
  font-size: 12px;
}

.execution-meta {
  margin-top: 8px;
}
</style>
