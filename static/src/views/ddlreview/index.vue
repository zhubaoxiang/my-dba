<template>
  <div class="ddl-review-page">
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <div>
            <span>DDL 评审</span>
            <span class="hint">在执行前检查建表 / 改表语句；依据最近一次采集快照，不执行任何语句</span>
          </div>
          <el-tag type="info" size="small" effect="plain">仅支持 PostgreSQL</el-tag>
        </div>
      </template>

      <el-form label-width="90px">
        <el-form-item label="数据源">
          <el-select
            v-model="datasourceId"
            clearable
            filterable
            placeholder="纯建表可不选；改表 / 索引 / 删除需要已采集的数据源"
            style="width: 420px"
          >
            <el-option v-for="item in datasources" :key="item.id" :value="item.id" :label="item.name" />
          </el-select>
        </el-form-item>
        <el-form-item label="DDL">
          <el-input
            v-model="sql"
            type="textarea"
            :autosize="{ minRows: 8, maxRows: 24 }"
            placeholder="粘贴一条或多条 DDL（CREATE / ALTER / DROP / INDEX / TRUNCATE），Ctrl+Enter 评审"
            @keydown.ctrl.enter="review"
          />
          <div class="field-hint">
            多语句按顺序评审；非 DDL 语句会标记为「未参与评审」。系统不会连接目标库，也不会执行任何语句。
          </div>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="loading" :disabled="!sql.trim()" @click="review">评审</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <template v-if="result">
      <el-card shadow="never" class="section">
        <el-alert
          :type="verdictType(result.verdict.level)"
          :closable="false"
          show-icon
          :title="`${result.verdict.label}：${result.verdict.text}`"
        />

        <div class="snapshot-line">
          <template v-if="result.snapshot.performed">
            依据快照：{{ result.snapshot.collect_time || '时间未知' }} · {{ result.snapshot.table_count }} 张表
          </template>
          <template v-else>
            {{ result.snapshot.note || '未绑定数据源' }}
          </template>
        </div>

        <div class="stat-row">
          <el-tag size="small" type="info">语句 {{ result.summary.statements }}</el-tag>
          <el-tag size="small" type="success">已合并到快照 {{ result.summary.applied }}</el-tag>
          <el-tag size="small" type="warning">新增 / 加重问题 {{ result.summary.design_issue_total }}</el-tag>
          <el-tag size="small" type="warning">语句风险 {{ result.summary.risk_total }}</el-tag>
          <el-tag v-if="result.summary.blocking_total" size="small" type="danger">
            执行阻断 {{ result.summary.blocking_total }}
          </el-tag>
          <el-tag v-if="result.summary.existing_issue_total" size="small" type="info">
            既有问题 {{ result.summary.existing_issue_total }} 条未重复列出
          </el-tag>
        </div>

        <el-alert
          v-if="result.summary.disabled_rules.length"
          class="disabled-note"
          type="warning"
          :closable="false"
          show-icon
          :title="`以下规则已被停用，本次未参与评审：${result.summary.disabled_rules.join('、')}`"
        />

        <el-alert
          v-if="result.not_reviewed.length"
          class="not-reviewed"
          type="warning"
          :closable="false"
          show-icon
          title="未评审内容"
        >
          <ul class="reason-list">
            <li v-for="(item, index) in result.not_reviewed" :key="index">
              <span v-if="item.index">第 {{ item.index }} 条：</span>{{ item.reason }}
            </li>
          </ul>
        </el-alert>

        <template v-if="!result.syntax.ok">
          <el-divider content-position="left">语法错误</el-divider>
          <el-alert type="error" :closable="false" show-icon title="SQL 解析失败">
            <div v-for="(error, index) in result.syntax.errors" :key="index" class="error-line">
              <span v-if="error.line">第 {{ error.line }} 行第 {{ error.col }} 列：</span>{{ error.description }}
            </div>
          </el-alert>
        </template>
      </el-card>

      <el-card v-for="statement in result.statements" :key="statement.index" shadow="never" class="section">
        <template #header>
          <div class="card-header">
            <div>
              <span>第 {{ statement.index }} 条 · {{ kindLabel(statement.kind) }}</span>
              <el-tag
                :type="statement.applied ? 'success' : 'danger'"
                size="small"
                effect="plain"
                class="applied-tag"
              >
                {{ statement.applied ? '已合并' : '未合并' }}
              </el-tag>
            </div>
          </div>
        </template>

        <pre class="code">{{ statement.sql }}</pre>

        <el-alert
          v-if="statement.apply_error"
          class="apply-error"
          type="error"
          :closable="false"
          show-icon
          :title="statement.apply_error"
        />

        <template v-if="statement.applied">
          <div v-if="changeEntries(statement.change_summary).length" class="change-row">
            <span class="change-label">变更摘要</span>
            <el-tag v-for="entry in changeEntries(statement.change_summary)" :key="entry.key" size="small" type="info">
              {{ entry.label }}：{{ entry.text }}
            </el-tag>
          </div>

          <el-divider content-position="left">
            新增 / 加重的设计问题（{{ statement.design_issues.length }}）
          </el-divider>
          <el-empty
            v-if="!statement.design_issues.length"
            description="本次变更未引入新的设计问题"
            :image-size="50"
          />
          <el-table v-else :data="statement.design_issues" border size="small">
            <el-table-column label="级别" width="70">
              <template #default="{ row }">
                <el-tag :type="levelTagType(row.issue_level)" size="small">{{ levelLabel(row.issue_level) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="变化" width="90">
              <template #default="{ row }">
                <el-tag :type="row.change === 'worsened' ? 'warning' : 'danger'" size="small" effect="plain">
                  {{ row.change === 'worsened' ? '加重' : '新增' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="rule_name" label="规则" width="150" />
            <el-table-column prop="target" label="对象" min-width="130" show-overflow-tooltip />
            <el-table-column prop="description" label="说明" min-width="220" show-overflow-tooltip />
            <el-table-column prop="suggestion" label="建议" min-width="180" show-overflow-tooltip />
          </el-table>

          <el-divider content-position="left">语句风险（{{ statement.risks.length }}）</el-divider>
          <el-empty v-if="!statement.risks.length" description="未发现语句级风险" :image-size="50" />
          <el-table v-else :data="statement.risks" border size="small">
            <el-table-column label="级别" width="70">
              <template #default="{ row }">
                <el-tag :type="levelTagType(row.level)" size="small">{{ levelLabel(row.level) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="阻断" width="70">
              <template #default="{ row }">
                <el-tag v-if="row.blocking" type="danger" size="small">是</el-tag>
                <span v-else class="muted">—</span>
              </template>
            </el-table-column>
            <el-table-column prop="code" label="风险标识" width="210" />
            <el-table-column prop="target" label="对象" min-width="120" show-overflow-tooltip />
            <el-table-column prop="description" label="说明" min-width="240" show-overflow-tooltip />
            <el-table-column prop="suggestion" label="建议" min-width="180" show-overflow-tooltip />
          </el-table>
        </template>
      </el-card>
    </template>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { datasourceApi } from '@/api/datasource'
import { ddlReviewApi } from '@/api/ddlReview'

const DRAFT_KEY = 'my-dba:ddl-review-draft'
const LEVEL_LABELS = { 1: '高', 2: '中', 3: '低' }
const LEVEL_TYPES = { 1: 'danger', 2: 'warning', 3: 'info' }
const KIND_LABELS = {
  CREATE_TABLE: 'CREATE TABLE',
  ALTER_TABLE: 'ALTER TABLE',
  CREATE_INDEX: 'CREATE INDEX',
  DROP_TABLE: 'DROP TABLE',
  DROP_INDEX: 'DROP INDEX',
  TRUNCATE: 'TRUNCATE',
  COMMENT: 'COMMENT',
  OTHER: '非 DDL'
}
const CHANGE_LABELS = {
  tables_added: '新增表',
  tables_dropped: '删除表',
  tables_renamed: '重命名表',
  columns_added: '新增列',
  columns_dropped: '删除列',
  columns_altered: '修改列',
  columns_renamed: '重命名列',
  indexes_added: '新增索引',
  indexes_dropped: '删除索引',
  constraints_added: '新增约束',
  constraints_dropped: '删除约束'
}

const datasources = ref([])
const datasourceId = ref(null)
const sql = ref('')
const loading = ref(false)
const result = ref(null)

function levelLabel(level) {
  return LEVEL_LABELS[level] || ''
}

function levelTagType(level) {
  return LEVEL_TYPES[level] || 'info'
}

function verdictType(level) {
  return LEVEL_TYPES[level] || 'success'
}

function kindLabel(kind) {
  return KIND_LABELS[kind] || kind
}

function changeEntries(summary) {
  return Object.entries(summary || {})
    .filter(([, value]) => (Array.isArray(value) ? value.length : value))
    .map(([key, value]) => ({
      key,
      label: CHANGE_LABELS[key] || key,
      text: Array.isArray(value)
        ? value.map((item) => (typeof item === 'object' ? JSON.stringify(item) : item)).join('、')
        : String(value)
    }))
}

async function loadDatasources() {
  const data = await datasourceApi.list({ page: 1, page_size: 200 })
  datasources.value = data.results || []
}

function consumeDraft() {
  const raw = sessionStorage.getItem(DRAFT_KEY)
  if (!raw) return
  sessionStorage.removeItem(DRAFT_KEY)
  try {
    const draft = JSON.parse(raw)
    sql.value = draft.sql || ''
    datasourceId.value = draft.datasource_id || null
  } catch {
    // 交接数据损坏就当没有；不阻止使用者手动输入
  }
}

async function review() {
  if (!sql.value.trim()) return
  loading.value = true
  try {
    result.value = await ddlReviewApi.review({
      sql: sql.value,
      datasource_id: datasourceId.value || undefined
    })
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  consumeDraft()
  await loadDatasources()
})
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

.field-hint {
  margin-top: 6px;
  font-size: 12px;
  color: #909399;
}

.section {
  margin-top: 16px;
}

.snapshot-line {
  margin-top: 12px;
  font-size: 12px;
  color: #606266;
}

.stat-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}

.disabled-note,
.not-reviewed {
  margin-top: 12px;
}

.reason-list {
  margin: 6px 0 0;
  padding-left: 18px;
  line-height: 1.7;
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
  max-height: 360px;
  overflow: auto;
}

.apply-error {
  margin-top: 10px;
}

.applied-tag {
  margin-left: 10px;
}

.change-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}

.change-label {
  font-size: 12px;
  color: #606266;
}

.error-line {
  margin-top: 4px;
}

.muted {
  color: #909399;
  font-size: 12px;
}
</style>
