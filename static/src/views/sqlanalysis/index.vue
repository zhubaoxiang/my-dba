<template>
  <div class="sql-analysis-page">
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <div>
            <span>SQL 分析</span>
            <span class="hint">语法、规范与性能；绑定数据源后可校验表结构并试运行</span>
          </div>
          <div class="controls">
            <el-select v-model="dialect" placeholder="方言（默认 PostgreSQL）" clearable style="width: 190px">
              <el-option :value="1" label="PostgreSQL" />
              <el-option :value="2" label="MySQL" />
            </el-select>
            <el-select v-model="datasourceId" placeholder="不绑定数据源" clearable style="width: 180px">
              <el-option v-for="item in datasources" :key="item.id" :value="item.id" :label="item.name" />
            </el-select>
          </div>
        </div>
      </template>

      <el-input
        v-model="sql"
        type="textarea"
        :autosize="{ minRows: 6, maxRows: 18 }"
        placeholder="粘贴一条 SQL（查询、DML、DDL 均可），Ctrl+Enter 分析"
        @keydown.ctrl.enter="analyze"
      />

      <div class="actions">
        <el-button type="primary" :loading="analyzing" :disabled="!sql.trim()" @click="analyze">分析</el-button>
        <el-tooltip
          content="只读语句会在目标库上真实执行；DML / DDL 只出执行计划，不会被执行"
          placement="top"
        >
          <el-button
            :loading="executing"
            :disabled="!sql.trim() || !datasourceId"
            @click="runExecute"
          >
            试运行
          </el-button>
        </el-tooltip>
        <span v-if="!datasourceId" class="muted">试运行需要先选择数据源</span>
      </div>
    </el-card>

    <template v-if="result">
      <el-card shadow="never" class="section">
        <template #header><span>语法</span></template>
        <el-alert v-if="result.syntax.ok" type="success" :closable="false" show-icon
          :title="`解析通过，共 ${result.syntax.statement_count} 条语句：${kindSummary}`" />
        <el-alert v-else type="error" :closable="false" show-icon title="解析失败">
          <div v-for="(err, i) in result.syntax.errors" :key="i" class="error-line">
            <span v-if="err.line">第 {{ err.line }} 行第 {{ err.col }} 列：</span>{{ err.description }}
          </div>
        </el-alert>
      </el-card>

      <el-card shadow="never" class="section">
        <template #header>
          <div class="card-header">
            <span>格式化</span>
            <el-button link type="primary" size="small" @click="copyFormatted">复制</el-button>
          </div>
        </template>
        <pre class="code">{{ result.formatted }}</pre>
      </el-card>

      <el-card shadow="never" class="section">
        <template #header>
          <div class="card-header">
            <span>问题清单（{{ result.issues.length }}）</span>
            <span class="muted">
              结构校验：{{ result.schema_check.performed ? `已进行，快照含 ${result.schema_check.table_count} 张表` : result.schema_check.note }}
            </span>
          </div>
        </template>

        <el-empty v-if="!result.issues.length" description="规则未判出问题" :image-size="60" />
        <el-table v-else :data="result.issues" border>
          <el-table-column label="级别" width="80">
            <template #default="{ row }">
              <el-tag :type="levelTagType(row.issue_level)" size="small">{{ levelLabel(row.issue_level) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="rule_name" label="规则" width="170" />
          <el-table-column label="位置" width="120">
            <template #default="{ row }">
              <span v-if="row.statement_index">第 {{ row.statement_index }} 条</span>
              <span v-else class="muted">整条 SQL</span>
            </template>
          </el-table-column>
          <el-table-column prop="target" label="对象" min-width="140" show-overflow-tooltip />
          <el-table-column prop="description" label="说明" min-width="260" show-overflow-tooltip />
          <el-table-column prop="suggestion" label="建议" min-width="240" show-overflow-tooltip />
        </el-table>

        <div v-if="result.skipped_rules.length" class="muted skipped">
          因缺少表结构而跳过的规则：{{ result.skipped_rules.join('、') }}
        </div>
      </el-card>

      <el-card v-if="execution" shadow="never" class="section">
        <template #header>
          <div class="card-header">
            <span>试运行</span>
            <el-tag v-if="execution.executed" type="success" size="small">
              已执行 · {{ execution.row_count }} 行 · {{ execution.duration_ms }}ms
            </el-tag>
            <el-tag v-else type="warning" size="small">未执行</el-tag>
          </div>
        </template>
        <el-alert v-if="execution.note" type="warning" :closable="false" show-icon :title="execution.note" />
        <pre v-if="execution.plan" class="code plan">{{ execution.plan }}</pre>
      </el-card>

      <el-card shadow="never" class="section">
        <template #header><span>解读</span></template>

        <el-alert
          v-if="!result.interpretation.available"
          type="info"
          :closable="false"
          show-icon
          :title="result.interpretation.note || '本次没有大模型解读'"
        />

        <template v-else>
          <el-empty v-if="!result.interpretation.explanations.length" description="规则未判出问题，没有可解读的条目" :image-size="50" />
          <div v-else class="explanations">
            <div v-for="(item, i) in result.interpretation.explanations" :key="i" class="explanation">
              <el-tag size="small" type="info">{{ ruleName(item.rule_code) }}</el-tag>
              <span class="explanation-text">{{ item.text }}</span>
            </div>
          </div>

          <div v-if="result.interpretation.observations.length" class="observations">
            <el-alert
              type="warning"
              :closable="false"
              show-icon
              :title="result.interpretation.observations_note"
            />
            <ul class="observation-list">
              <li v-for="(text, i) in result.interpretation.observations" :key="i">{{ text }}</li>
            </ul>
          </div>
        </template>
      </el-card>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { datasourceApi } from '@/api/datasource'
import { sqlAnalysisApi } from '@/api/sqlAnalysis'

const LEVELS = { 1: '高', 2: '中', 3: '低' }

const sql = ref('')
const dialect = ref(null)
const datasourceId = ref(null)
const datasources = ref([])
const result = ref(null)
const execution = ref(null)
const analyzing = ref(false)
const executing = ref(false)

const kindSummary = computed(() => {
  const items = result.value?.syntax?.statements || []
  if (!items.length) return ''
  const counts = {}
  items.forEach((item) => {
    counts[item.kind_label] = (counts[item.kind_label] || 0) + 1
  })
  return Object.entries(counts)
    .map(([label, n]) => `${label} ${n} 条`)
    .join('、')
})

function levelLabel(level) {
  return LEVELS[level] || '?'
}

function levelTagType(level) {
  if (level === 1) return 'danger'
  if (level === 2) return 'warning'
  return 'info'
}

function ruleName(code) {
  const issue = (result.value?.issues || []).find((item) => item.rule_code === code)
  return issue ? issue.rule_name : code
}

async function loadDatasources() {
  const data = await datasourceApi.list({ page: 1, page_size: 200 })
  datasources.value = data.results || []
}

async function analyze() {
  if (!sql.value.trim()) return
  analyzing.value = true
  execution.value = null // 换了输入或重新分析，旧的试运行结果不再对应当前 SQL
  try {
    result.value = await sqlAnalysisApi.analyze({
      sql: sql.value,
      dialect: dialect.value || undefined,
      datasource_id: datasourceId.value || undefined
    })
  } finally {
    analyzing.value = false
  }
}

async function runExecute() {
  executing.value = true
  try {
    execution.value = await sqlAnalysisApi.execute({
      sql: sql.value,
      dialect: dialect.value || undefined,
      datasource_id: datasourceId.value
    })
  } finally {
    executing.value = false
  }
}

async function copyFormatted() {
  const text = result.value?.formatted || ''
  if (!text) return
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success('已复制格式化后的 SQL')
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
  gap: 12px;
}

.hint {
  margin-left: 10px;
  font-size: 12px;
  color: #909399;
}

.controls {
  display: flex;
  gap: 8px;
}

.actions {
  margin-top: 12px;
  display: flex;
  align-items: center;
  gap: 12px;
}

.muted {
  color: #909399;
  font-size: 12px;
}

.section {
  margin-top: 16px;
}

.code {
  margin: 0;
  padding: 12px;
  background: #f5f7fa;
  border-radius: 4px;
  font-family: Consolas, Monaco, monospace;
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 420px;
  overflow: auto;
}

.plan {
  margin-top: 10px;
}

.error-line {
  margin-top: 4px;
}

.skipped {
  margin-top: 10px;
}

.explanations {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.explanation {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  line-height: 1.6;
}

.explanation-text {
  flex: 1;
}

/* 模型推测与规则判定在视觉上分区，避免使用者把推测当成规则结论 */
.observations {
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px dashed #e4e7ed;
}

.observation-list {
  margin: 10px 0 0;
  padding-left: 20px;
  line-height: 1.8;
}
</style>
