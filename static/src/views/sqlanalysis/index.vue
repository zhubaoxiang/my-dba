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
        <el-button :loading="formatting" :disabled="!sql.trim()" @click="formatSql">格式化</el-button>
        <el-button type="primary" :loading="analyzing" :disabled="!sql.trim()" @click="analyze">分析</el-button>
        <el-tooltip content="只读语句会在目标库上真实执行；DML / DDL 只出执行计划，不会被执行" placement="top">
          <el-button :loading="executing" :disabled="!sql.trim() || !datasourceId" @click="runExecute">试运行</el-button>
        </el-tooltip>
        <span v-if="!datasourceId" class="muted">试运行需要先选择数据源</span>
      </div>
    </el-card>

    <!-- 左右分栏：确定性的结果在左，耗时的模型解读在右并保持可见，
         省得为了看解读一路滚到底 -->
    <el-row :gutter="16" class="split">
      <el-col :xs="24" :lg="15">
        <el-card v-if="formatted" shadow="never" class="section">
          <template #header>
            <div class="card-header">
              <span>格式化结果</span>
              <el-button link type="primary" size="small" @click="copyFormatted">复制</el-button>
            </div>
          </template>
          <el-alert
            v-if="formatted.syntax && !formatted.syntax.ok"
            class="format-warning"
            type="warning"
            :closable="false"
            show-icon
            title="SQL 未能解析，以下是原样返回的内容——请先修正语法"
          />
          <pre class="code">{{ formatted.formatted }}</pre>
        </el-card>

        <template v-if="result">
          <el-card shadow="never" class="section">
            <el-alert
              :type="verdictType(result.verdict.level)"
              :closable="false"
              show-icon
              :title="`${result.verdict.label}：${result.verdict.text}`"
            />
            <div v-if="interpretation && interpretation.summary" class="verdict-summary">
              <span class="summary-label">总体判断</span>
              {{ interpretation.summary }}
            </div>
          </el-card>

          <el-card shadow="never" class="section">
            <template #header><span>语法</span></template>
            <el-alert
              v-if="result.syntax.ok"
              type="success"
              :closable="false"
              show-icon
              :title="`解析通过，共 ${result.syntax.statement_count} 条语句：${kindSummary}`"
            />
            <el-alert v-else type="error" :closable="false" show-icon title="解析失败">
              <div v-for="(err, i) in result.syntax.errors" :key="i" class="error-line">
                <span v-if="err.line">第 {{ err.line }} 行第 {{ err.col }} 列：</span>{{ err.description }}
              </div>
            </el-alert>
          </el-card>

          <el-card shadow="never" class="section">
            <template #header>
              <div class="card-header">
                <span>问题清单（{{ result.issues.length }}）</span>
                <span class="muted">
                  结构校验：{{
                    result.schema_check.performed
                      ? `已进行，快照含 ${result.schema_check.table_count} 张表`
                      : result.schema_check.note
                  }}
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
              <el-table-column label="位置" width="110">
                <template #default="{ row }">
                  <span v-if="row.statement_index">第 {{ row.statement_index }} 条</span>
                  <span v-else class="muted">整条 SQL</span>
                </template>
              </el-table-column>
              <el-table-column prop="target" label="对象" min-width="130" show-overflow-tooltip />
              <el-table-column prop="description" label="说明" min-width="240" show-overflow-tooltip />
              <el-table-column prop="suggestion" label="建议" min-width="220" show-overflow-tooltip />
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
        </template>
      </el-col>

      <el-col :xs="24" :lg="9">
        <div class="interpret-column">
          <el-card shadow="never">
            <template #header>
              <div class="card-header">
                <span>模型解读</span>
                <el-tag v-if="interpreting" type="warning" size="small" effect="plain">进行中</el-tag>
              </div>
            </template>

            <el-empty v-if="!result" description="分析后这里会显示模型解读" :image-size="60" />

            <template v-else-if="interpreting">
              <div class="interpreting">
                <el-icon class="interpreting-icon is-loading"><Loading /></el-icon>
                <div>
                  <div class="interpreting-title">正在调用大模型解读…</div>
                  <div class="interpreting-hint">规则判定与问题清单已就绪，左侧可先查看，不受影响</div>
                </div>
              </div>
              <el-skeleton class="interpreting-skeleton" :rows="5" animated />
            </template>

            <el-alert
              v-else-if="interpretation && !interpretation.available"
              type="info"
              :closable="false"
              show-icon
              :title="interpretation.note || '本次没有大模型解读'"
            />

            <template v-else-if="interpretation">
              <el-empty
                v-if="!interpretation.explanations.length"
                :description="emptyExplanationText"
                :image-size="60"
              />
              <div v-else class="explanations">
                <div v-for="(item, i) in interpretation.explanations" :key="i" class="explanation">
                  <el-tag size="small" type="info">{{ ruleName(item.rule_code) }}</el-tag>
                  <span class="explanation-text">{{ item.text }}</span>
                </div>
              </div>

              <div v-if="interpretation.observations.length" class="observations">
                <el-alert type="warning" :closable="false" show-icon :title="interpretation.observations_note" />
                <ul class="observation-list">
                  <li v-for="(text, i) in interpretation.observations" :key="i">{{ text }}</li>
                </ul>
              </div>
            </template>
          </el-card>
        </div>
      </el-col>
    </el-row>
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
const interpretation = ref(null)
const execution = ref(null)
const formatted = ref(null)
const formatting = ref(false)
const analyzing = ref(false)
const interpreting = ref(false)
const executing = ref(false)
// 请求序号：解读是慢调用，期间若又发起了新的分析，旧结果必须丢弃
let interpretToken = 0

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

// 「规则没判出问题」与「模型有输出但没对上」是两回事，文案不能混——
// 用前者的说法去描述后者，等于把故障盖住了
const emptyExplanationText = computed(() => {
  const data = interpretation.value
  if (!data) return ''
  if (!result.value?.issues.length) return '规则未判出问题，没有可解读的条目'
  if (data.dropped_explanations) {
    return `模型给出了 ${data.dropped_explanations} 条解读，但都没能与本次问题清单对应上，已丢弃`
  }
  return '模型本次没有给出逐条解读'
})

function verdictType(level) {
  if (level === 1) return 'error'
  if (level === 2) return 'warning'
  if (level === 3) return 'info'
  return 'success'
}

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

async function formatSql() {
  if (!sql.value.trim()) return
  formatting.value = true
  try {
    formatted.value = await sqlAnalysisApi.format({
      sql: sql.value,
      dialect: dialect.value || undefined
    })
  } finally {
    formatting.value = false
  }
}

async function analyze() {
  if (!sql.value.trim()) return
  analyzing.value = true
  // 换了输入或重新分析，旧的格式化、解读与试运行结果都不再对应当前 SQL
  formatted.value = null
  interpretation.value = null
  execution.value = null
  try {
    result.value = await sqlAnalysisApi.analyze({
      sql: sql.value,
      dialect: dialect.value || undefined,
      datasource_id: datasourceId.value || undefined
    })
  } finally {
    analyzing.value = false
  }
  // 解读单独请求：它是十几秒的外部调用，不该拖住上面那份已经出来的结论与清单
  loadInterpretation()
}

async function loadInterpretation() {
  const token = ++interpretToken
  interpreting.value = true
  try {
    const data = await sqlAnalysisApi.interpret({
      sql: sql.value,
      dialect: dialect.value || undefined,
      datasource_id: datasourceId.value || undefined
    })
    if (token === interpretToken) interpretation.value = data
  } catch {
    // 解读失败不该影响已经出来的结论与清单；拦截器已提示过
  } finally {
    if (token === interpretToken) interpreting.value = false
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
  const text = formatted.value?.formatted || ''
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

.split {
  margin-top: 16px;
}

.section {
  margin-bottom: 16px;
}

/* 解读列保持可见：省得为了看解读一路滚到底。窄屏下退化为普通上下排列 */
.interpret-column {
  position: sticky;
  top: 16px;
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

.format-warning {
  margin-bottom: 10px;
}

.error-line {
  margin-top: 4px;
}

.skipped {
  margin-top: 10px;
}

.verdict-summary {
  margin-top: 12px;
  line-height: 1.7;
}

.summary-label {
  display: inline-block;
  margin-right: 8px;
  padding: 1px 8px;
  border-radius: 3px;
  background: #ecf5ff;
  color: #409eff;
  font-size: 12px;
}

/* 解读中的等待态：明确告诉使用者「还在跑」而不是「已经完了」 */
.interpreting {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 4px 0 14px;
}

.interpreting-icon {
  margin-top: 2px;
  font-size: 18px;
  color: #e6a23c;
}

.interpreting-title {
  font-size: 14px;
  color: #303133;
  margin-bottom: 4px;
}

.interpreting-hint {
  font-size: 12px;
  color: #909399;
  line-height: 1.6;
}

.interpreting-skeleton {
  margin-top: 4px;
}

.explanations {
  display: flex;
  flex-direction: column;
  gap: 10px;
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
