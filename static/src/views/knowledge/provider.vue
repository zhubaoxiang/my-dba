<template>
  <div class="provider-page">
    <el-alert
      class="tip"
      type="info"
      :closable="false"
      show-icon
      title="模型一律通过 API 接入。API Key 加密存储，任何接口响应与日志都不会返回它；连通性测试会真实调用一次模型接口。"
    />

    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span>模型配置</span>
          <div>
            <el-button type="primary" @click="openCreate">新建配置</el-button>
            <el-button @click="loadList">刷新</el-button>
          </div>
        </div>
      </template>

      <el-table :data="rows" v-loading="loading" border>
        <el-table-column label="生效" width="80">
          <template #default="{ row }">
            <el-tag v-if="row.is_active" type="success" size="small">生效中</el-tag>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="name" label="名称" min-width="140" />
        <el-table-column label="用途" width="110">
          <template #default="{ row }">
            <el-tag :type="row.model_type === 2 ? 'warning' : 'primary'" size="small">
              {{ row.model_type_label }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="provider_type_label" label="接口类型" width="130" />
        <el-table-column label="接口地址" min-width="200">
          <template #default="{ row }">
            <span class="mono">{{ row.base_url }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="model_name" label="模型名" min-width="170" />
        <el-table-column label="启用" width="80">
          <template #default="{ row }">
            <el-tag :type="row.is_enabled ? 'success' : 'info'" size="small">
              {{ row.is_enabled ? '启用' : '停用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="260" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" :disabled="row.is_active || !row.is_enabled" @click="handleActivate(row)">
              设为生效
            </el-button>
            <el-button link type="primary" @click="handleTest(row)">测试连接</el-button>
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

    <el-dialog v-model="dialogVisible" :title="editing ? '编辑模型配置' : '新建模型配置'" width="560px">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="110px">
        <el-form-item label="名称" prop="name">
          <el-input v-model="form.name" maxlength="64" placeholder="如：内网模型代理" />
        </el-form-item>
        <el-form-item label="用途" prop="model_type">
          <el-select v-model="form.model_type" style="width: 100%">
            <el-option :value="1" label="对话模型" />
            <el-option :value="2" label="嵌入模型" />
          </el-select>
          <div class="hint">
            一条配置只承载一种用途。对话与嵌入各自独立选「生效」，一端没配不影响另一端；
            同一个网关若同时提供两类模型，就建两条配置指向它。
          </div>
        </el-form-item>
        <el-form-item label="接口类型" prop="provider_type">
          <el-select v-model="form.provider_type" style="width: 100%">
            <el-option :value="1" label="OpenAI 兼容接口" />
          </el-select>
        </el-form-item>
        <el-form-item label="接口地址" prop="base_url">
          <el-input v-model="form.base_url" maxlength="255" placeholder="如 http://10.0.0.1:8080/v1" />
        </el-form-item>
        <el-form-item label="模型名" prop="model_name">
          <el-input v-model="form.model_name" maxlength="128" placeholder="如 deepseek-flash / bge-m3" />
          <div class="hint" v-if="form.model_type === 2">
            嵌入模型须与后端 EMBEDDING_DIMENSIONS（当前 1536）维度一致
          </div>
        </el-form-item>
        <el-form-item label="API Key" :prop="editing ? '' : 'api_key'">
          <el-input
            v-model="form.api_key"
            type="password"
            show-password
            :placeholder="editing ? '留空表示不更换' : '模型服务的 API Key'"
          />
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="form.is_enabled" />
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleSubmit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { providerApi } from '@/api/knowledge'

const rows = ref([])
const total = ref(0)
const loading = ref(false)
const query = reactive({ page: 1, page_size: 10 })

const dialogVisible = ref(false)
const editing = ref(null)
const saving = ref(false)
const formRef = ref()
const form = reactive(emptyForm())

const rules = {
  name: [{ required: true, message: '请输入名称', trigger: 'blur' }],
  model_type: [{ required: true, message: '请选择用途', trigger: 'change' }],
  provider_type: [{ required: true, message: '请选择接口类型', trigger: 'change' }],
  base_url: [{ required: true, message: '请输入接口地址', trigger: 'blur' }],
  model_name: [{ required: true, message: '请输入模型名', trigger: 'blur' }],
  api_key: [{ required: true, message: '请输入 API Key', trigger: 'blur' }]
}

function emptyForm() {
  return {
    name: '',
    model_type: 1,
    provider_type: 1,
    base_url: '',
    model_name: '',
    api_key: '',
    is_enabled: true
  }
}

async function loadList() {
  loading.value = true
  try {
    const data = await providerApi.list({ page: query.page, page_size: query.page_size })
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
  Object.assign(form, emptyForm(), { ...row, api_key: '' })
  dialogVisible.value = true
}

async function handleSubmit() {
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return
  saving.value = true
  try {
    const payload = { ...form }
    if (editing.value) {
      if (!payload.api_key) delete payload.api_key
      await providerApi.update(editing.value.id, payload)
      ElMessage.success('已保存')
    } else {
      await providerApi.create(payload)
      ElMessage.success('已创建')
    }
    dialogVisible.value = false
    loadList()
  } finally {
    saving.value = false
  }
}

async function handleActivate(row) {
  await providerApi.activate(row.id)
  ElMessage.success(`「${row.name}」已设为生效配置`)
  loadList()
}

async function handleTest(row) {
  try {
    const data = await providerApi.test(row.id)
    ElMessage.success(`连接成功，对话模型：${data.chat_model || '未知'}`)
  } catch (err) {
    // 拦截器已提示失败原因（后端已做脱敏）
  }
}

async function handleRemove(row) {
  await ElMessageBox.confirm(`确认删除模型配置「${row.name}」？`, '提示', { type: 'warning' })
  await providerApi.remove(row.id)
  ElMessage.success('已删除')
  loadList()
}

onMounted(loadList)
</script>

<style scoped>
.tip {
  margin-bottom: 12px;
}

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

.muted {
  color: #c0c4cc;
}

.hint {
  font-size: 12px;
  color: #909399;
  line-height: 1.4;
}
</style>
