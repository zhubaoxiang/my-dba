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

    <!-- 概览数字带：先给一个总纲，下面再铺开 -->
    <div class="summary">
      <!-- 库数与在线数合成一格：两者本来就共用「总数」这一个数，分成两格是重复 -->
      <div class="stat">
        <div class="stat-value" :class="onlineLevel">
          <template v-if="hasLibraryRatio">
            {{ summary.online_total }}<span class="slash">/</span>{{ summary.datasource_total }}
          </template>
          <template v-else>{{ dash(null) }}</template>
        </div>
        <!-- 「在线 2/3」比单看某个库更早暴露问题，异常时整块着色 -->
        <div class="stat-label">{{ onlineLabel }}</div>
      </div>
      <div class="stat">
        <div class="stat-value" :class="{ 'text-high': summary.issue_total > 0 }">
          {{ dash(summary.issue_total) }}
        </div>
        <div class="stat-label">
          问题合计<template v-if="summary.issue_high_total"> · 高危 {{ summary.issue_high_total }}</template>
        </div>
      </div>
      <div class="stat">
        <div class="stat-value">{{ dash(summary.knowledge_base_total) }}</div>
        <div class="stat-label">知识库 · {{ dash(summary.document_total) }} 篇文档</div>
      </div>
      <!-- 分析规则：系统的判断标准有多细。两个数并排在一格，用 · 而非 / ——
           它们不是比值关系，用斜杠会读成「7 分之 18」 -->
      <div class="stat">
        <div class="stat-value">
          {{ dash(rules.datasource_total) }}<span class="slash">·</span>{{ dash(rules.sql_total) }}
        </div>
        <div class="stat-label">分析规则 · 库表 / SQL</div>
        <el-button class="stat-link" link type="primary" size="small"
          @click="router.push('/datasource/rules')">查看规则清单</el-button>
      </div>
    </div>

    <!-- 已纳管的库 -->
    <section class="section">
      <div class="section-title">
        已纳管的库<span v-if="datasourceItems.length" class="count">{{ datasourceItems.length }}</span>
      </div>

      <el-alert v-if="!blocks.datasources.available" type="error" :closable="false" show-icon
        title="库数据暂不可用——这不代表「没有问题」，请稍后刷新" />

      <div v-else-if="!datasourceItems.length" class="empty-card">
        <div class="empty-title">还没有接入数据库</div>
        <div class="empty-hint">接入后可以查看表结构、分析问题</div>
        <el-button type="primary" @click="router.push('/datasource')">去接入</el-button>
      </div>

      <template v-else>
        <el-alert v-if="!blocks.metrics.available" class="metrics-alert" type="warning" :closable="false" show-icon
          title="指标数据暂不可用——库的采集状态与问题数仍可查看，指标待恢复后重新显示" />

        <div class="card-grid">
          <div v-for="card in cards" :key="card.item.id" class="ds-card" :class="card.state">
            <div class="card-head">
              <span class="dot" :class="card.state"></span>
              <span class="card-name" :title="card.item.name">{{ card.item.name }}</span>
              <span class="tag">{{ dbTypeLabel(card.item.db_type) }}</span>
              <span class="card-status" :class="card.state">{{ cardStatusText(card) }}</span>
            </div>

            <!-- 离线：整卡标红，**完整**给出失败原因（不截断），不展示过期指标、不画趋势 -->
            <div v-if="card.state === 'offline'" class="offline-reason">
              <div class="offline-title">连不上这个库</div>
              <div class="offline-text">{{ card.metric.fail_reason || '未记录失败原因' }}</div>
              <div class="offline-hint">下面的指标是上一次连通时采到的，已经不代表现状，因此不展示。</div>
            </div>

            <template v-else>
              <!-- 指标格 2×2。取不到的项显示占位符，**不记 0**——0 与「没取到」是两回事 -->
              <div class="metrics">
                <div class="metric">
                  <div class="metric-value" :class="connectionLevel(card)">
                    {{ connectionText(card) }}
                  </div>
                  <div class="metric-label">当前连接 / 上限</div>
                </div>
                <div class="metric">
                  <div class="metric-value">{{ sizeText(card) }}</div>
                  <div class="metric-label">数据库大小</div>
                </div>
                <div class="metric">
                  <div class="metric-value" :class="hitLevel(card)">{{ hitText(card) }}</div>
                  <div class="metric-label">缓存命中率</div>
                </div>
                <div class="metric">
                  <div class="metric-value">{{ card.item.table_count || 0 }}</div>
                  <div class="metric-label">表数量</div>
                </div>
              </div>

              <!-- 取不到的项要说清是哪一项、为什么是空的——只留一个「—」会让人以为是 0 -->
              <div v-if="card.metric && card.metric.unavailable.length" class="unavailable-note">
                这个库取不到：{{ unavailableText(card.metric.unavailable) }}
              </div>

              <!-- 趋势：形状自适应看变化，颜色按占上限比例报警。数据不足以画线时如实说明 -->
              <div class="trend">
                <div class="trend-head">
                  <span class="trend-title">近 24 小时连接数</span>
                  <span v-if="card.spark" class="trend-range">{{ card.spark.min }} ~ {{ card.spark.max }}</span>
                  <!-- 指标采集时刻：使用者据此判断新鲜度 -->
                  <span v-if="card.metric" class="trend-meta">采集于 {{ freshness(card.metric.collected_at) }}</span>
                </div>

                <svg v-if="card.spark" class="spark" viewBox="0 0 100 34" preserveAspectRatio="none"
                  role="img" :aria-label="`${card.item.name} 近 24 小时连接数趋势`">
                  <defs>
                    <linearGradient :id="`spark-${card.item.id}`" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" :stop-color="card.spark.color" stop-opacity="0.3" />
                      <stop offset="100%" :stop-color="card.spark.color" stop-opacity="0" />
                    </linearGradient>
                  </defs>
                  <path :d="card.spark.area" :fill="`url(#spark-${card.item.id})`" />
                  <polyline :points="card.spark.line" fill="none" :stroke="card.spark.color"
                    stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke" />
                </svg>

                <div v-else class="trend-pending">{{ pendingText }}</div>
              </div>
            </template>

            <div class="card-foot">
              <div class="foot-stats">
                <template v-if="card.item.collect_status === 'collected'">
                  <span v-if="card.item.issue_counts[1]" class="level high">高 {{ card.item.issue_counts[1] }}</span>
                  <span v-if="card.item.issue_counts[2]" class="level mid">中 {{ card.item.issue_counts[2] }}</span>
                  <span v-if="card.item.issue_counts[3]" class="level low">低 {{ card.item.issue_counts[3] }}</span>
                  <span v-if="!hasIssues(card.item)" class="level ok">未判出问题</span>
                  <span class="meta">{{ card.item.table_count }} 张表 · 采集于 {{ freshness(card.item.collect_time) }}</span>
                </template>
                <!-- 没有快照就不给问题数：「还没有数据」不能被读成「没有问题」 -->
                <span v-else class="meta">{{ statusHint(card.item) }}</span>
              </div>

              <div class="foot-actions">
                <el-button v-if="card.item.collect_status === 'collected'" link type="primary" size="small"
                  @click="router.push({ path: '/datasource/issues', query: { datasource_id: card.item.id } })">
                  看问题
                </el-button>
                <el-button v-if="card.item.collect_status !== 'running'" link type="primary" size="small"
                  :loading="collecting === card.item.id" @click="collect(card.item)">
                  {{ card.item.collect_status === 'collected' ? '重新采集' : '去采集' }}
                </el-button>
              </div>
            </div>
          </div>
        </div>
      </template>
    </section>

    <!-- 知识库 -->
    <section class="section">
      <div class="section-title">
        知识库<span v-if="knowledgeItems.length" class="count">{{ knowledgeItems.length }}</span>
      </div>

      <el-alert v-if="!blocks.knowledge_bases.available" type="error" :closable="false" show-icon
        title="知识库数据暂不可用——这不代表「没有问题」，请稍后刷新" />

      <div v-else-if="!knowledgeItems.length" class="empty-card">
        <div class="empty-title">还没有知识库</div>
        <div class="empty-hint">创建并上传文档后，问答才能检索到内容</div>
        <el-button type="primary" @click="router.push('/knowledge/bases')">去创建</el-button>
      </div>

      <div v-else class="card-grid">
        <div v-for="item in knowledgeItems" :key="item.id" class="kb-card"
          :class="{ danger: item.failed_count > 0, empty: !item.document_count }">
          <div class="card-head">
            <span class="card-name" :title="item.name">{{ item.name }}</span>
            <span v-if="item.failed_count" class="card-status offline">{{ item.failed_count }} 篇摄入失败</span>
          </div>

          <!-- 空库要说明**后果**，不能只显示「0 篇文档」 -->
          <div v-if="!item.document_count" class="offline-reason">
            <div class="empty-title">还没有文档，问答时检索不到内容</div>
            <el-button link type="primary" size="small" @click="router.push('/knowledge/bases')">去上传</el-button>
          </div>

          <template v-else>
            <div class="kb-stats">
              <div class="metric">
                <div class="metric-value">{{ item.document_count }}</div>
                <div class="metric-label">篇文档</div>
              </div>
              <!-- 「32 篇文档」是观感，检索块数才是实际能搜到多少内容 -->
              <div class="metric">
                <div class="metric-value">{{ formatNumber(item.chunk_count) }}</div>
                <div class="metric-label">个检索块</div>
              </div>
              <div class="metric">
                <div class="metric-value">{{ item.qa_session_count }}</div>
                <div class="metric-label">次提问</div>
              </div>
            </div>

            <!-- 摄入状态是比例关系，堆叠条比一串文字更直观 -->
            <div class="stack">
              <span v-for="seg in stackSegments(item)" :key="seg.key" class="seg"
                :class="seg.key" :style="{ width: seg.width }" :title="`${seg.label} ${seg.count}`" />
            </div>
            <div class="kb-legend">
              <span class="level ok">成功 {{ item.success_count }}</span>
              <span v-if="item.pending_count" class="level mid">待处理 {{ item.pending_count }}</span>
              <span v-if="item.failed_count" class="level high">失败 {{ item.failed_count }}</span>
              <span class="meta">最近更新 {{ item.last_updated ? freshness(item.last_updated) : '—' }}</span>
            </div>
          </template>

          <div class="card-foot">
            <div class="foot-stats">
              <span v-if="item.description" class="desc">{{ item.description }}</span>
              <!-- 「建了但没人用」也值得看出来 -->
              <span v-else-if="item.document_count && !item.qa_session_count" class="meta">还没有人在这里问过</span>
            </div>
            <div class="foot-actions">
              <el-button link type="primary" size="small" @click="router.push('/knowledge/bases')">
                {{ item.failed_count ? '查看失败原因' : '管理文档' }}
              </el-button>
              <el-button link type="primary" size="small" @click="router.push('/knowledge/qa')">去提问</el-button>
            </div>
          </div>
        </div>
      </div>
    </section>

    <div class="footer">
      <span class="meta">最近刷新 {{ refreshedAt }}</span>
      <el-button link type="primary" size="small" :loading="loading" @click="load">刷新</el-button>
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
  summary: {},
  datasources: { available: true, items: [] },
  metrics: { available: true, items: [] },
  knowledge_bases: { available: true, items: [] },
  rules: { available: true, datasource_total: null, sql_total: null }
})

const readiness = computed(() => blocks.value.readiness)
const summary = computed(() => blocks.value.summary || {})
const datasourceItems = computed(() => blocks.value.datasources.items)
const knowledgeItems = computed(() => blocks.value.knowledge_bases.items)
const rules = computed(() => blocks.value.rules)

const DB_TYPE_LABEL = { 1: 'PostgreSQL', 2: 'MySQL' }

// 后端记的是归类名（metrics.py 的 _CACHE / _IO），这里翻成人话
const UNAVAILABLE_LABEL = { cache: '缓存命中率', io: '读写量' }

// 占上限的比例分档：<80% 正常、80~95% 注意、>95% 危险（design.md D6）
const CONNECTION_WARN = 0.8
const CONNECTION_HIGH = 0.95

const STATUS_TEXT = {
  collected: '',
  never: '尚未采集',
  running: '采集中',
  failed: '采集失败'
}

const pendingText = computed(() =>
  blocks.value.metrics.available ? '指标采集中…' : '指标暂不可用'
)

function dash(value) {
  return value === null || value === undefined ? '—' : value
}

function dbTypeLabel(dbType) {
  return DB_TYPE_LABEL[dbType] || '未知类型'
}

function formatNumber(value) {
  return (value || 0).toLocaleString('zh-CN')
}

function formatBytes(bytes) {
  if (bytes === null || bytes === undefined) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let value = bytes
  let index = 0
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024
    index += 1
  }
  return `${value >= 10 || index === 0 ? Math.round(value) : value.toFixed(1)} ${units[index]}`
}

// 后端回的是 "YYYY-MM-DD HH:MM:SS"；Safari 不认这种带空格的写法，换成 ISO 再解析
function parseTime(text) {
  if (!text) return null
  const parsed = new Date(String(text).replace(' ', 'T'))
  return Number.isNaN(parsed.getTime()) ? null : parsed
}

/**
 * 新鲜度：能算出相对时间就给相对时间，算不出就照原样显示——
 * 不能因为算不了就把采集时刻抹掉
 */
function freshness(text) {
  const parsed = parseTime(text)
  if (!parsed) return text || '—'
  const minutes = Math.floor((Date.now() - parsed.getTime()) / 60000)
  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes} 分钟前`
  if (minutes < 60 * 24) return `${Math.floor(minutes / 60)} 小时前`
  return text
}

function ratio(value, total) {
  if (value === null || value === undefined || !total) return null
  return value / total
}

function levelOf(value, warn, high) {
  if (value === null) return ''
  if (value > high) return 'text-high'
  if (value > warn) return 'text-warn'
  return ''
}

function unavailableText(items) {
  return items.map((item) => UNAVAILABLE_LABEL[item] || item).join('、')
}

function hasIssues(item) {
  const counts = item.issue_counts || {}
  return Boolean(counts[1] || counts[2] || counts[3])
}

function statusHint(item) {
  if (item.collect_status === 'never') return '还没有表结构数据，采集后才能分析'
  if (item.collect_status === 'running') return '正在采集，稍后刷新查看结果'
  if (item.collect_status === 'failed') return item.fail_reason || '上次采集失败'
  return ''
}

// ----------------------------------------------------------------------
// 趋势火花线
// ----------------------------------------------------------------------

const SPARK_W = 100
const SPARK_H = 34
const SPARK_PAD = 4
const SPARK_COLORS = { ok: '#409eff', warn: '#e6a23c', high: '#f56c6c' }

/**
 * 自绘火花线：**不引图表库**——为一条 100×34 的小线引入几百 KB 的依赖不成比例
 *
 * 形状按数据范围自适应：连接数固定画到上限的话，9/100 会贴着底部、完全看不出变化。
 * 代价是微小波动会被放大，因此旁边并列显示绝对值（见卡片上的 `9/100`）。
 */
function sparkline(trend, maxConnections) {
  const min = Math.min(...trend)
  const max = Math.max(...trend)
  const span = max - min || 1 // 全平时给个 1，避免除零后画到界外
  const step = SPARK_W / (trend.length - 1)
  const points = trend.map((value, index) => {
    const x = index * step
    const y = SPARK_PAD + (1 - (value - min) / span) * (SPARK_H - SPARK_PAD * 2)
    return `${x.toFixed(2)},${y.toFixed(2)}`
  })
  return {
    line: points.join(' '),
    area: `M0,${SPARK_H} L${points.join(' L')} L${SPARK_W},${SPARK_H} Z`,
    min,
    max,
    // 颜色报警看危险：取最新一点占上限的比例，与卡片上的数字同口径
    color: SPARK_COLORS[levelKey(ratio(trend[trend.length - 1], maxConnections))] || SPARK_COLORS.ok
  }
}

function levelKey(value) {
  if (value === null) return 'ok'
  if (value > CONNECTION_HIGH) return 'high'
  if (value > CONNECTION_WARN) return 'warn'
  return 'ok'
}

// ----------------------------------------------------------------------
// 卡片视图模型：状态解析集中在这里，模板里只做展示
// ----------------------------------------------------------------------

const metricsById = computed(() => {
  const map = {}
  for (const item of blocks.value.metrics.items) map[item.datasource_id] = item
  return map
})

const cards = computed(() =>
  datasourceItems.value.map((item) => {
    const metric = metricsById.value[item.id] || null
    // 「没采到」与「指标整块取不到」是两回事，前端也要分开说
    let state = 'pending'
    if (!blocks.value.metrics.available) state = 'unavailable'
    else if (metric && !metric.is_online) state = 'offline'
    else if (metric) state = 'online'

    // 不足两个点画不出线——不插值补点，宁可不画也不画一条假线
    const canDraw = state === 'online' && metric.trend && metric.trend.length >= 2
    return {
      item,
      metric,
      state,
      spark: canDraw ? sparkline(metric.trend, metric.max_connections) : null
    }
  })
)

function cardStatusText(card) {
  if (card.state === 'offline') return '离线'
  if (card.state === 'online') return '在线'
  return card.item.collect_status === 'collected' ? '' : STATUS_TEXT[card.item.collect_status] || ''
}

function connectionText(card) {
  if (!card.metric || card.metric.connection_count === null) return '—'
  const max = card.metric.max_connections
  return max ? `${card.metric.connection_count}/${max}` : String(card.metric.connection_count)
}

function connectionLevel(card) {
  if (!card.metric) return ''
  return levelOf(ratio(card.metric.connection_count, card.metric.max_connections), CONNECTION_WARN, CONNECTION_HIGH)
}

function sizeText(card) {
  return card.metric ? formatBytes(card.metric.database_size) : '—'
}

function hitText(card) {
  if (!card.metric || card.metric.cache_hit_ratio === null || card.metric.cache_hit_ratio === undefined) return '—'
  return `${card.metric.cache_hit_ratio}%`
}

/** 命中率低时要能一眼看到——这里是「最近是不是不对劲」最直接的线索之一 */
function hitLevel(card) {
  const value = card.metric ? card.metric.cache_hit_ratio : null
  if (value === null || value === undefined) return ''
  if (value < 90) return 'text-high'
  if (value < 95) return 'text-warn'
  return ''
}

/** 一个库都没接时不给 0/0，那是个没有意义的比值 */
const hasLibraryRatio = computed(() => {
  const online = summary.value.online_total
  return Boolean(summary.value.datasource_total) && online !== null && online !== undefined
})

const onlineLevel = computed(() => {
  if (!hasLibraryRatio.value) return ''
  return summary.value.online_total < summary.value.datasource_total ? 'text-warn' : ''
})

const onlineLabel = computed(() => {
  const total = summary.value.datasource_total
  const online = summary.value.online_total
  if (!total) return '已纳管的库'
  if (online === null || online === undefined) return '已纳管的库 · 在线情况未知'
  return online < total ? `已纳管的库 · ${total - online} 个不在线` : '已纳管的库 · 全部在线'
})

// ----------------------------------------------------------------------
// 知识库的堆叠条
// ----------------------------------------------------------------------

const STACK_SEGMENTS = [
  { key: 'success', label: '成功' },
  { key: 'pending', label: '待处理' },
  { key: 'failed', label: '失败' }
]

function stackSegments(item) {
  const total = item.document_count
  if (!total) return []
  const counts = {
    success: item.success_count,
    pending: item.pending_count,
    failed: item.failed_count
  }
  return STACK_SEGMENTS.filter((seg) => counts[seg.key] > 0).map((seg) => ({
    ...seg,
    count: counts[seg.key],
    width: `${(counts[seg.key] / total) * 100}%`
  }))
}

// ----------------------------------------------------------------------
// 取数
// ----------------------------------------------------------------------

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

/* 概览数字带 */
.summary {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
  margin-bottom: 20px;
}

.stat {
  display: flex;
  flex-direction: column;
  padding: 14px 16px;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  background: #fafcff;
}

/* 只有规则那格带入口，靠 margin-top:auto 让它贴到等高格子的底部，不至于是半空中一行 */
.stat-link {
  margin-top: auto;
  padding-top: 4px;
  align-self: flex-start;
  height: auto;
  font-size: 12px;
}

.stat-value {
  font-size: 26px;
  font-weight: 600;
  line-height: 1.2;
  color: #303133;
}

.slash {
  margin: 0 2px;
  font-size: 20px;
  font-weight: 400;
  color: #c0c4cc;
}

.stat-label {
  margin-top: 4px;
  font-size: 12px;
  color: #909399;
}

.section {
  margin-bottom: 24px;
}

.section-title {
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

.metrics-alert {
  margin-bottom: 12px;
}

/* 2 列网格；窄屏（< lg，Element Plus 的 1200px 断点）退化为单列 */
.card-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  align-items: start;
}

@media (max-width: 1199.98px) {
  .card-grid,
  .summary {
    grid-template-columns: minmax(0, 1fr);
  }
}

.ds-card,
.kb-card,
.empty-card {
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  padding: 14px;
  background: #fff;
}

/* 离线整卡标红，让它在一片卡片里跳出来 */
.ds-card.offline {
  background: #fef0f0;
  border-color: #fbc4c4;
}

.kb-card.danger {
  background: #fef0f0;
  border-color: #fbc4c4;
}

.empty-card {
  border-style: dashed;
  text-align: center;
  padding: 32px 16px;
  color: #909399;
}

.empty-title {
  font-size: 14px;
  font-weight: 600;
  color: #303133;
}

.empty-hint {
  margin: 6px 0 14px;
  font-size: 12px;
}

.kb-card.empty .empty-title {
  font-weight: 400;
  color: #909399;
}

.card-head {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

/* 状态点带光晕 */
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex: none;
  background: #c0c4cc;
}

.dot.online {
  background: #67c23a;
  box-shadow: 0 0 0 3px rgba(103, 194, 58, 0.2);
}

.dot.offline {
  background: #f56c6c;
  box-shadow: 0 0 0 3px rgba(245, 108, 108, 0.22);
}

.dot.pending,
.dot.unavailable {
  background: #c0c4cc;
  box-shadow: 0 0 0 3px rgba(144, 147, 153, 0.18);
}

.card-name {
  font-weight: 600;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tag {
  flex: none;
  padding: 1px 6px;
  border-radius: 3px;
  font-size: 11px;
  color: #606266;
  background: #f0f2f5;
}

.card-status {
  margin-left: auto;
  flex: none;
  font-size: 12px;
  color: #909399;
}

.card-status.offline {
  color: #c45656;
}

.card-status.online {
  color: #529b2e;
}

/* 离线原因完整显示，不截断 */
.offline-reason {
  margin-top: 10px;
  padding: 10px;
  border-radius: 4px;
  background: rgba(255, 255, 255, 0.7);
}

.offline-title {
  font-size: 13px;
  font-weight: 600;
  color: #c45656;
}

.offline-text {
  margin-top: 4px;
  font-size: 12px;
  color: #606266;
  white-space: pre-wrap;
  word-break: break-all;
}

.offline-hint {
  margin-top: 6px;
  font-size: 12px;
  color: #909399;
}

.metrics,
.kb-stats {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px 12px;
  margin-top: 12px;
}

.kb-stats {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.metric {
  min-width: 0;
}

.metric-value {
  font-size: 18px;
  font-weight: 600;
  color: #303133;
  font-variant-numeric: tabular-nums;
}

.metric-label {
  font-size: 12px;
  color: #909399;
}

.unavailable-note {
  margin-top: 8px;
  font-size: 12px;
  color: #b88230;
}

.trend {
  margin-top: 12px;
}

.trend-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 12px;
  color: #909399;
}

.trend-title {
  flex: none;
}

.trend-range {
  flex: none;
  color: #c0c4cc;
}

.trend-meta {
  margin-left: auto;
  flex: none;
}

.spark {
  display: block;
  width: 100%;
  height: 34px;
  margin-top: 4px;
}

/* 画不出线时说明原因，不留白 */
.trend-pending {
  margin-top: 4px;
  height: 34px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px dashed #dcdfe6;
  border-radius: 4px;
  font-size: 12px;
  color: #909399;
}

/* 摄入状态堆叠条 */
.stack {
  display: flex;
  height: 6px;
  margin-top: 12px;
  border-radius: 3px;
  overflow: hidden;
  background: #f0f2f5;
}

.seg {
  height: 100%;
}

.seg.success {
  background: #67c23a;
}

.seg.pending {
  background: #e6a23c;
}

.seg.failed {
  background: #f56c6c;
}

.kb-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 6px;
  font-size: 12px;
}

.card-foot {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: 12px;
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px solid #f2f6fc;
}

.foot-stats {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
  min-width: 0;
}

.foot-actions {
  display: flex;
  flex: none;
  white-space: nowrap;
}

.desc {
  font-size: 12px;
  color: #909399;
  /* 描述一行省略：卡片的高度不该由描述长度决定 */
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.meta {
  font-size: 12px;
  color: #909399;
}

.level {
  font-size: 12px;
}

.level.high,
.text-high {
  color: #c45656;
}

.level.mid,
.text-warn {
  color: #b88230;
}

.level.low {
  color: #909399;
}

.level.ok {
  color: #529b2e;
}

.footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding-top: 12px;
  border-top: 1px dashed #e4e7ed;
}
</style>
