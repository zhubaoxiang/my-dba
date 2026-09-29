<template>
  <div class="home-page" v-loading="loading">
    <!-- 就绪状态：告诉使用者「某个功能用不了」是不是因为还没配置。
         只读后端配置，不探测外部服务，因此不会拖慢首页 -->
    <el-alert
      v-if="readiness"
      class="readiness"
      :type="readiness.ready ? 'success' : 'warning'"
      :closable="false"
      show-icon
    >
      <template v-if="readiness.ready">系统就绪：对话模型、嵌入模型均已配置</template>
      <template v-else>
        <div v-for="(item, i) in readiness.missing" :key="i" class="missing-item">
          还没配置<b>{{ item.item }}</b> —— {{ item.affected }}不可用
          <el-button link type="primary" size="small" @click="router.push('/knowledge/provider')">
            去配置
          </el-button>
        </div>
      </template>
    </el-alert>

    <el-row :gutter="16" class="split">
      <!-- 左：已纳管的库 -->
      <el-col :xs="24" :lg="12">
        <div class="column">
          <div class="column-title">
            已纳管的库<span v-if="datasourceItems.length" class="count">{{ datasourceItems.length }}</span>
          </div>

          <el-alert v-if="!blocks.datasources.available" type="error" :closable="false" show-icon
            title="库数据暂不可用——这不代表「没有问题」，请稍后刷新" />

          <el-empty v-else-if="!datasourceItems.length" description="还没有接入数据库">
            <el-button type="primary" @click="router.push('/datasource')">去接入</el-button>
          </el-empty>

          <div v-else class="list">
            <div
              v-for="item in datasourceItems"
              :key="item.id"
              class="row"
              :class="{ warn: item.collect_status !== 'collected' }"
            >
              <div class="row-main">
                <div class="row-head">
                  <span class="name">{{ item.name }}</span>
                  <span class="meta">{{ statusText(item) }}</span>
                </div>

                <div v-if="item.collect_status === 'collected'" class="row-stats">
                  <span class="meta">{{ item.table_count }} 张表</span>
                  <template v-if="hasIssues(item)">
                    <span v-if="item.issue_counts[1]" class="level high">高 {{ item.issue_counts[1] }}</span>
                    <span v-if="item.issue_counts[2]" class="level mid">中 {{ item.issue_counts[2] }}</span>
                    <span v-if="item.issue_counts[3]" class="level low">低 {{ item.issue_counts[3] }}</span>
                  </template>
                  <span v-else class="level ok">未判出问题</span>
                </div>

                <div v-else class="row-stats meta">
                  {{ statusHint(item) }}
                </div>
              </div>

              <div class="row-actions">
                <el-button v-if="item.collect_status === 'collected'" link type="primary" size="small"
                  @click="router.push({ path: '/datasource/issues', query: { datasource_id: item.id } })">
                  看问题
                </el-button>
                <el-button v-if="item.collect_status !== 'running'" link type="primary" size="small"
                  :loading="collecting === item.id" @click="collect(item)">
                  {{ item.collect_status === 'collected' ? '重新采集' : '去采集' }}
                </el-button>
              </div>
            </div>
          </div>
        </div>
      </el-col>

      <!-- 右：知识库 -->
      <el-col :xs="24" :lg="12">
        <div class="column">
          <div class="column-title">
            知识库<span v-if="knowledgeItems.length" class="count">{{ knowledgeItems.length }}</span>
          </div>

          <el-alert v-if="!blocks.knowledge_bases.available" type="error" :closable="false" show-icon
            title="知识库数据暂不可用——这不代表「没有问题」，请稍后刷新" />

          <el-empty v-else-if="!knowledgeItems.length" description="还没有知识库">
            <el-button type="primary" @click="router.push('/knowledge/bases')">去创建</el-button>
          </el-empty>

          <div v-else class="list">
            <div
              v-for="item in knowledgeItems"
              :key="item.id"
              class="row"
              :class="{ danger: item.failed_count > 0 }"
            >
              <div class="row-main">
                <div class="row-head">
                  <span class="name">{{ item.name }}</span>
                  <span v-if="item.failed_count" class="meta danger-text">{{ item.failed_count }} 篇摄入失败</span>
                </div>
                <div class="row-stats">
                  <span class="meta">{{ item.document_count }} 篇文档</span>
                  <span v-if="item.pending_count" class="level mid">待处理 {{ item.pending_count }}</span>
                  <span v-else-if="!item.failed_count" class="level ok">全部就绪</span>
                </div>
              </div>

              <div class="row-actions">
                <el-button link type="primary" size="small" @click="router.push('/knowledge/bases')">
                  {{ item.failed_count ? '查看失败原因' : '管理文档' }}
                </el-button>
                <el-button link type="primary" size="small" @click="router.push('/knowledge/qa')">去提问</el-button>
              </div>
            </div>
          </div>
        </div>
      </el-col>
    </el-row>

    <div class="footer">
      <span class="meta">数据为进入页面时加载</span>
      <span>
        <span class="meta">最近刷新 {{ refreshedAt }}</span>
        <el-button link type="primary" size="small" :loading="loading" @click="load">刷新</el-button>
      </span>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { datasourceApi } from '@/api/datasource'
import { overviewApi } from '@/api/overview'

const router = useRouter()

const loading = ref(false)
const collecting = ref(null)
const refreshedAt = ref('')
const blocks = ref({
  readiness: { available: true, ready: false, missing: [] },
  datasources: { available: true, items: [] },
  knowledge_bases: { available: true, items: [] }
})

const readiness = computed(() => blocks.value.readiness)
const datasourceItems = computed(() => blocks.value.datasources.items)
const knowledgeItems = computed(() => blocks.value.knowledge_bases.items)

const STATUS_TEXT = {
  collected: '',
  never: '尚未采集',
  running: '采集中',
  failed: '采集失败'
}

function statusText(item) {
  if (item.collect_status === 'collected') return item.collect_time
  return STATUS_TEXT[item.collect_status] || ''
}

function statusHint(item) {
  if (item.collect_status === 'never') return '还没有表结构数据，采集后才能分析'
  if (item.collect_status === 'running') return '正在采集，稍后刷新查看结果'
  if (item.collect_status === 'failed') return item.fail_reason || '上次采集失败'
  return ''
}

function hasIssues(item) {
  const counts = item.issue_counts || {}
  return Boolean(counts[1] || counts[2] || counts[3])
}

async function load() {
  loading.value = true
  try {
    blocks.value = await overviewApi.get()
    refreshedAt.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
  } finally {
    loading.value = false
  }
}

async function collect(item) {
  await ElMessageBox.confirm(`对「${item.name}」发起一次元数据采集？`, '提示', { type: 'warning' })
  collecting.value = item.id
  try {
    await datasourceApi.collect(item.id)
    ElMessage.success('已发起采集，稍后刷新查看结果')
    await load()
  } finally {
    collecting.value = null
  }
}

onMounted(load)
</script>

<style scoped>
.home-page {
  min-height: 200px;
}

.readiness {
  margin-bottom: 16px;
}

.missing-item + .missing-item {
  margin-top: 4px;
}

.split {
  align-items: flex-start;
}

.column {
  margin-bottom: 16px;
}

.column-title {
  font-size: 14px;
  font-weight: 600;
  margin-bottom: 10px;
  color: #303133;
}

.count {
  margin-left: 6px;
  font-size: 12px;
  font-weight: 400;
  color: #909399;
}

.list {
  border: 1px solid #e4e7ed;
  border-radius: 4px;
  /* 列表限高可滚动：首页不该被撑得过长 */
  max-height: 420px;
  overflow-y: auto;
}

.row {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  padding: 12px;
  border-bottom: 1px solid #f2f6fc;
}

.row:last-child {
  border-bottom: none;
}

/* 需要处理的整行着色，让它在列表里跳出来 */
.row.warn {
  background: #fdf6ec;
}

.row.danger {
  background: #fef0f0;
}

.row-main {
  flex: 1;
  min-width: 0;
}

.row-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 8px;
}

.name {
  font-weight: 600;
  word-break: break-all;
}

.meta {
  font-size: 12px;
  color: #909399;
  white-space: nowrap;
}

.danger-text {
  color: #c45656;
}

.row-stats {
  margin-top: 6px;
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
}

.level {
  font-size: 12px;
}

.level.high {
  color: #c45656;
}

.level.mid {
  color: #b88230;
}

.level.low {
  color: #909399;
}

.level.ok {
  color: #529b2e;
}

.row-actions {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  white-space: nowrap;
}

.footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding-top: 12px;
  border-top: 1px dashed #e4e7ed;
}
</style>
