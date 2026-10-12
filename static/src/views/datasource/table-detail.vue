<template>
  <el-drawer v-model="visible" :title="title" size="62%" @open="load">
    <div v-loading="loading">
      <template v-if="detail">
        <el-descriptions :column="3" border size="small">
          <el-descriptions-item label="表名">{{ detail.schema }}.{{ detail.name }}</el-descriptions-item>
          <el-descriptions-item label="行数（估）">{{ formatRows(detail.row_count) }}</el-descriptions-item>
          <el-descriptions-item label="总占用">{{ formatBytes(detail.total_size) }}</el-descriptions-item>
          <el-descriptions-item label="数据大小">{{ formatBytes(detail.data_size) }}</el-descriptions-item>
          <el-descriptions-item label="索引大小">{{ formatBytes(detail.index_size) }}</el-descriptions-item>
          <el-descriptions-item label="主键">
            <span v-if="detail.primary_key">{{ (detail.primary_key.columns || []).join(', ') }}</span>
            <el-tag v-else type="danger" size="small">无主键</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="注释" :span="3">{{ detail.comment || '—' }}</el-descriptions-item>
        </el-descriptions>

        <el-tabs class="detail-tabs">
          <el-tab-pane :label="`列（${(detail.columns || []).length}）`">
            <el-table :data="detail.columns" border size="small" max-height="360">
              <el-table-column prop="name" label="列名" min-width="140" />
              <el-table-column label="类型" min-width="150">
                <template #default="{ row }">
                  {{ row.data_type }}{{ row.length > 0 ? `(${row.length})` : '' }}
                </template>
              </el-table-column>
              <el-table-column label="可空" width="80">
                <template #default="{ row }">{{ row.nullable ? '是' : '否' }}</template>
              </el-table-column>
              <el-table-column prop="default" label="默认值" min-width="120" show-overflow-tooltip />
              <el-table-column prop="comment" label="注释" min-width="140" show-overflow-tooltip />
            </el-table>
          </el-tab-pane>

          <el-tab-pane :label="`索引（${(detail.indexes || []).length}）`">
            <el-table :data="detail.indexes" border size="small" max-height="360">
              <el-table-column prop="name" label="索引名" min-width="180" />
              <el-table-column label="列" min-width="180">
                <template #default="{ row }">{{ (row.columns || []).join(', ') }}</template>
              </el-table-column>
              <el-table-column prop="index_type" label="类型" width="100" />
              <el-table-column label="唯一" width="80">
                <template #default="{ row }">{{ row.unique ? '是' : '否' }}</template>
              </el-table-column>
              <el-table-column label="使用次数" width="110">
                <template #default="{ row }">
                  <span v-if="row.scans === null || row.scans === undefined">未采集</span>
                  <span v-else>{{ row.scans }}</span>
                </template>
              </el-table-column>
            </el-table>
          </el-tab-pane>

          <el-tab-pane :label="`关系（${relationCount}）`">
            <!-- 两个方向**分开列**：本表的外键只说明「它引用了谁」；
                 「谁引用了它」必须扫全库才知道——外键是单向声明的，本表可能一个外键都没有。 -->
            <div class="relation-block">
              <div class="relation-title">引用了哪些表（{{ relations.references.length }}）</div>
              <el-table v-if="relations.references.length" :data="relations.references" border size="small">
                <el-table-column label="本表列" min-width="140">
                  <template #default="{ row }">{{ (row.columns || []).join(', ') }}</template>
                </el-table-column>
                <el-table-column label="引用" min-width="240">
                  <template #default="{ row }">
                    <el-button v-if="row.resolvable" link type="primary" size="small" @click="goTo(row)">
                      {{ targetLabel(row) }}
                    </el-button>
                    <span v-else class="muted">{{ targetLabel(row) }}（不在本次快照中）</span>
                  </template>
                </el-table-column>
                <el-table-column prop="name" label="约束名" min-width="160" show-overflow-tooltip />
              </el-table>
              <div v-else class="relation-empty">没有引用任何表</div>
            </div>

            <div class="relation-block">
              <div class="relation-title">被哪些表引用（{{ relations.referenced_by.length }}）</div>
              <el-table v-if="relations.referenced_by.length" :data="relations.referenced_by" border size="small">
                <el-table-column label="来源" min-width="240">
                  <template #default="{ row }">
                    <el-button v-if="row.resolvable" link type="primary" size="small" @click="goTo(row)">
                      {{ targetLabel(row) }}
                    </el-button>
                    <span v-else class="muted">{{ targetLabel(row) }}（不在本次快照中）</span>
                  </template>
                </el-table-column>
                <el-table-column label="来源列" min-width="130">
                  <template #default="{ row }">{{ (row.columns || []).join(', ') }}</template>
                </el-table-column>
                <el-table-column label="本表列" min-width="130">
                  <template #default="{ row }">{{ (row.ref_columns || []).join(', ') }}</template>
                </el-table-column>
                <el-table-column prop="name" label="约束名" min-width="160" show-overflow-tooltip />
              </el-table>
              <div v-else class="relation-empty">没有被任何表引用</div>
            </div>
          </el-tab-pane>
        </el-tabs>
      </template>
    </div>
  </el-drawer>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { catalogApi } from '@/api/datasource'
import { formatBytes, formatRows } from '@/utils/format'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  snapshotId: { type: [Number, String], default: null },
  table: { type: Object, default: null }
})
const emit = defineEmits(['update:modelValue', 'navigate'])

const visible = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value)
})

const detail = ref(null)
const loading = ref(false)
const title = computed(() => (props.table ? `表详情 · ${props.table.schema}.${props.table.name}` : '表详情'))

async function load() {
  if (!props.table || !props.snapshotId) return
  loading.value = true
  detail.value = null
  try {
    detail.value = await catalogApi.tableDetail({
      snapshot_id: props.snapshotId,
      schema: props.table.schema,
      table: props.table.name
    })
  } finally {
    loading.value = false
  }
}

// 关系两侧都要能跳：对方在本次快照中时才给链接，不在就只显示名字（点了会落空）
const relations = computed(
  () => (detail.value && detail.value.relations) || { references: [], referenced_by: [] }
)
const relationCount = computed(() => relations.value.references.length + relations.value.referenced_by.length)

function targetLabel(row) {
  return row.schema ? `${row.schema}.${row.table}` : row.table
}

function goTo(row) {
  emit('navigate', { schema: row.schema, name: row.table })
}

// 从「关系」跳到另一张表时抽屉**是开着的**，`@open` 不会再触发，得跟着 props 重新加载
watch(
  () => props.table,
  (value) => {
    if (value && props.modelValue) load()
  }
)

defineExpose({ load })
</script>

<style scoped>
.detail-tabs {
  margin-top: 16px;
}

/* 两个方向分开列，各自带标题与条数 */
.relation-block + .relation-block {
  margin-top: 18px;
}

.relation-title {
  margin-bottom: 8px;
  font-size: 13px;
  font-weight: 600;
  color: #303133;
}

.relation-empty {
  padding: 10px 0;
  font-size: 12px;
  color: #909399;
}

.muted {
  font-size: 12px;
  color: #909399;
}
</style>
