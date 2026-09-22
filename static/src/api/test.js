import request from './request'

// 测试模块 API 封装示例，对应后端 apps/test/views.py 的 TestView
export const testApi = {
  // 获取列表（GET /my-dba/api/v1/test）
  getList(params) {
    return request.get('/api/v1/test', { params })
  },
  // 获取详情（GET /my-dba/api/v1/test/{id}）
  getDetail(id) {
    return request.get(`/api/v1/test/${id}`)
  },
  // 创建（POST /my-dba/api/v1/test）
  create(data) {
    return request.post('/api/v1/test', data)
  },
  // 自定义 Action（POST /my-dba/api/v1/test/banned）
  createBannedRule(data) {
    return request.post('/api/v1/test/banned', data)
  }
}
