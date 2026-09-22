import axios from 'axios'
import { ElMessage } from 'element-plus'
import { useUserStore } from '@/store/user'

// 创建 axios 实例，统一配置 baseURL、超时时间
const service = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/my-dba',
  timeout: 15000
})

// 请求拦截器：附加 JWT Token 到请求头（与后端 utils/authentication.py 约定的 Token 字段一致）
service.interceptors.request.use(
  (config) => {
    const userStore = useUserStore()
    if (userStore.token) {
      config.headers['Token'] = userStore.token
    }
    return config
  },
  (error) => Promise.reject(error)
)

// 响应拦截器：按后端统一响应格式（{ code, message, data }）处理
service.interceptors.response.use(
  (response) => {
    const res = response.data
    // code === 2000 表示成功，直接返回 data
    if (res.code === 2000) {
      return res.data
    }
    // 其他业务错误码统一提示
    ElMessage.error(res.message || '请求失败')
    return Promise.reject(new Error(res.message || 'Error'))
  },
  (error) => {
    const status = error.response?.status
    let message = error.message
    if (status === 401) {
      message = '登录已过期，请重新登录'
      const userStore = useUserStore()
      userStore.logout()
    } else if (status === 403) {
      message = '没有访问权限'
    } else if (status >= 500) {
      message = '服务器异常，请稍后重试'
    }
    ElMessage.error(message)
    return Promise.reject(error)
  }
)

export default service
