<template>
  <div class="datasource-page">
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span>数据源管理</span>
          <div>
            <el-button type="primary" @click="openCreate">新建数据源</el-button>
            <el-button @click="loadList">刷新</el-button>
          </div>
        </div>
      </template>

      <el-table :data="rows" v-loading="loading" border>
        <el-table-column prop="name" label="名称" min-width="140" />
        <el-table-column prop="db_type_label" label="类型" width="120" />
        <el-table-column label="连接信息" min-width="240">
          <template #default="{ row }">
            <span class="mono">{{ row.host }}:{{ row.port }}/{{ row.db_name }}</span>
            <div class="sub-text">账号：{{ row.username }}</div>
          </template>
        </el-table-column>
        <el-table-column prop="description" label="描述" min-width="160" show-overflow-tooltip />
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="row.is_enabled ? 'success' : 'info'">{{ row.is_enabled ? '启用' : '停用' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="create_time" label="创建时间" width="170" />
        <el-table-column label="操作" width="280" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="handleTest(row)">测试连接</el-button>
            <el-button link type="primary" @click="handleCollect(row)">采集</el-button>
            <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button link type="danger" @click="handleRemove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-pagination
        class="pager"
        background
        layout="total, sizes, prev, pager, next"
        :total="total"
        :current-page="query.page"
        :page-size="query.page_size"
        :page-sizes="[10, 20, 50]"
        @current-change="onPageChange"
        @size-change="onSizeChange"
      />
    </el-card>

    <el-dialog v-model="dialogVisible" :title="editing ? '编辑数据源' : '新建数据源'" width="560px">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="100px">
        <el-form-item label="名称" prop="name">
          <el-input v-model="form.name" maxlength="64" placeholder="如：生产订单库" />
        </el-form-item>
        <el-form-item label="数据库类型" prop="db_type">
          <el-select v-model="form.db_type" style="width: 100%">
            <el-option :value="1" label="PostgreSQL" />
            <el-option :value="2" label="MySQL" />
          </el-select>
        </el-form-item>
        <el-form-item label="主机" prop="host">
          <el-input v-model="form.host" maxlength="128" placeholder="IP 或域名" />
        </el-form-item>
        <el-form-item label="端口" prop="port">
          <el-input-number v-model="form.port" :min="1" :max="65535" controls-position="right" />
        </el-form-item>
        <el-form-item label="数据库名" prop="db_name">
          <el-input v-model="form.db_name" maxlength="128" />
        </el-form-item>
        <el-form-item label="用户名" prop="username">
          <el-input v-model="form.username" maxlength="64" />
        </el-form-item>
        <el-form-item label="密码" :prop="editing ? '' : 'password'">
          <el-input
            v-model="form.password"
            type="password"
            show-password
            :placeholder="editing ? '留空表示不修改' : '连接目标库的密码'"
          />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" maxlength="255" />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="form.is_enabled" />
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="handleTestForm" :loading="testing">测试连接</el-button>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleSubmit">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="taskVisible" title="采集任务" width="680px">
      <el-table :data="taskRows" v-loading="taskLoading" border max-height="360">
        <el-table-column prop="id" label="任务" width="80" />
        <el-table-column prop="status_label" label="状态" width="100" />
        <el-table-column prop="start_time" label="开始" width="170" />
        <el-table-column prop="end_time" label="结束" width="170" />
        <el-table-column prop="fail_reason" label="失败原因" min-width="160" show-overflow-tooltip />
      </el-table>
      <template #footer>
        <el-button @click="loadTasks">刷新</el-button>
        <el-button type="primary" @click="taskVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { datasourceApi } from '@/api/datasource'

const rows = ref([])
const total = ref(0)
const loading = ref(false)
const query = reactive({ page: 1, page_size: 10 })

const dialogVisible = ref(false)
const editing = ref(null)
const saving = ref(false)
const testing = ref(false)
const formRef = ref()
const form = reactive(emptyForm())

const taskVisible = ref(false)
const taskRows = ref([])
const taskLoading = ref(false)
const taskDatasourceId = ref(null)

const rules = {
  name: [{ required: true, message: '请输入名称', trigger: 'blur' }],
  db_type: [{ required: true, message: '请选择数据库类型', trigger: 'change' }],
  host: [{ required: true, message: '请输入主机', trigger: 'blur' }],
  port: [{ required: true, message: '请输入端口', trigger: 'blur' }],
  db_name: [{ required: true, message: '请输入数据库名', trigger: 'blur' }],
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }]
}

function emptyForm() {
  return {
    name: '',
    db_type: 1,
    host: '',
    port: 5432,
    db_name: '',
    username: '',
    password: '',
    description: '',
    is_enabled: true
  }
}

async function loadList() {
  loading.value = true
  try {
    const data = await datasourceApi.list({ page: query.page, page_size: query.page_size })
    rows.value = data.results || []
    total.value = data.total || 0
  } finally {
    loading.value = false
  }
}

function onPageChange(page) {
  query.page = page
  loadList()
}

function onSizeChange(size) {
  query.page_size = size
  query.page = 1
  loadList()
}

function openCreate() {
  editing.value = null
  Object.assign(form, emptyForm())
  dialogVisible.value = true
}

function openEdit(row) {
  editing.value = row
  Object.assign(form, emptyForm(), { ...row, password: '' })
  dialogVisible.value = true
}

async function handleSubmit() {
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return
  saving.value = true
  try {
    const payload = { ...form }
    if (editing.value) {
      if (!payload.password) delete payload.password
      await datasourceApi.update(editing.value.id, payload)
      ElMessage.success('已保存')
    } else {
      await datasourceApi.create(payload)
      ElMessage.success('已创建')
    }
    dialogVisible.value = false
    loadList()
  } finally {
    saving.value = false
  }
}

async function handleTestForm() {
  testing.value = true
  try {
    const data = await datasourceApi.testUnsaved({ ...form })
    ElMessage.success(`连接成功，数据库版本：${data.database_version || '未知'}`)
  } catch (err) {
    // 拦截器已提示失败原因
  } finally {
    testing.value = false
  }
}

async function handleTest(row) {
  try {
    const data = await datasourceApi.testSaved(row.id)
    ElMessage.success(`连接成功，数据库版本：${data.database_version || '未知'}`)
  } catch (err) {
    // 拦截器已提示
  }
}

async function handleCollect(row) {
  try {
    const task = await datasourceApi.collect(row.id)
    ElMessage.success(`采集任务 #${task.id} 已提交`)
    taskDatasourceId.value = row.id
    taskVisible.value = true
    loadTasks()
  } catch (err) {
    // 拦截器已提示
  }
}

async function loadTasks() {
  if (!taskDatasourceId.value) return
  taskLoading.value = true
  try {
    const data = await datasourceApi.tasks(taskDatasourceId.value, { page: 1, page_size: 10 })
    taskRows.value = data.results || []
  } finally {
    taskLoading.value = false
  }
}

async function handleRemove(row) {
  await ElMessageBox.confirm(`确认删除数据源「${row.name}」？其历史快照会保留。`, '提示', { type: 'warning' })
  await datasourceApi.remove(row.id)
  ElMessage.success('已删除')
  loadList()
}

onMounted(loadList)
</script>

<style scoped>
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.pager {
  margin-top: 16px;
  justify-content: flex-end;
}

.mono {
  font-family: Consolas, Monaco, monospace;
}

.sub-text {
  font-size: 12px;
  color: #909399;
}
</style>
