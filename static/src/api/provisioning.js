import request from './request'

// 建库与写入可能持续较久，执行接口单独放宽超时
const PROVISION_TIMEOUT = 300000

export const provisioningApi = {
  // 只生成计划，不连接目标库
  preview(data) {
    return request.post('/v1/test-db/preview', data)
  },
  provision(data) {
    return request.post('/v1/test-db/provision', data, { timeout: PROVISION_TIMEOUT })
  },
  list(params) {
    return request.get('/v1/test-db', { params })
  },
  remove(id) {
    return request.delete(`/v1/test-db/${id}`)
  },
  // 向已创建的表追加数据
  appendRows(id, data) {
    return request.post(`/v1/test-db/${id}/rows`, data, { timeout: PROVISION_TIMEOUT })
  },
  // 清空表数据
  clearRows(id, params) {
    return request.delete(`/v1/test-db/${id}/rows`, { params })
  },
  // 新增表并造数
  addTable(id, data) {
    return request.post(`/v1/test-db/${id}/tables`, data, { timeout: PROVISION_TIMEOUT })
  },
  // 删除本功能创建的表
  dropTable(id, params) {
    return request.delete(`/v1/test-db/${id}/tables`, { params })
  }
}
