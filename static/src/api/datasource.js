import request from './request'

// 数据源配置 API
export const datasourceApi = {
  // 列表（分页）
  list(params) {
    return request.get('/v1/datasource', { params })
  },
  // 详情
  detail(id) {
    return request.get(`/v1/datasource/${id}`)
  },
  // 新建
  create(data) {
    return request.post('/v1/datasource', data)
  },
  // 修改（password 留空表示不修改）
  update(id, data) {
    return request.put(`/v1/datasource/${id}`, data)
  },
  // 软删除
  remove(id) {
    return request.delete(`/v1/datasource/${id}`)
  },
  // 测试已保存数据源的连通性
  testSaved(id) {
    return request.post(`/v1/datasource/${id}/test`)
  },
  // 测试尚未保存的连接参数
  testUnsaved(data) {
    return request.post('/v1/datasource/test', data)
  },
  // 触发元数据采集
  collect(id) {
    return request.post(`/v1/datasource/${id}/collect`)
  },
  // 采集任务列表
  tasks(id, params) {
    return request.get(`/v1/datasource/${id}/tasks`, { params })
  }
}

// 分析规则注册表 API
export const analysisRuleApi = {
  // 规则清单（分页）
  list(params) {
    return request.get('/v1/analysis-rule', { params })
  },
  // 修改启用开关 / 严重级别 / 阈值
  update(id, data) {
    return request.put(`/v1/analysis-rule/${id}`, data)
  },
  // 把可覆盖项恢复为代码声明的默认值
  reset(id) {
    return request.post(`/v1/analysis-rule/${id}/reset`)
  },
  // 从代码声明同步规则清单（不覆盖已改过的开关、级别与阈值）
  sync() {
    return request.post('/v1/analysis-rule/sync')
  }
}

// 元数据查询 API
export const catalogApi = {
  // 快照列表
  snapshots(params) {
    return request.get('/v1/catalog/snapshots', { params })
  },
  // 数据源最新快照
  latestSnapshot(params) {
    return request.get('/v1/catalog/latest-snapshot', { params })
  },
  // 库表总览统计
  summary(params) {
    return request.get('/v1/catalog/summary', { params })
  },
  // 表清单（分页 + 关键字）
  tables(params) {
    return request.get('/v1/catalog/tables', { params })
  },
  // 表详情
  tableDetail(params) {
    return request.get('/v1/catalog/table', { params })
  },
  // 问题清单（可按级别筛选）
  issues(params) {
    return request.get('/v1/catalog/issues', { params })
  },
  // 两个快照的结构差异
  diff(params) {
    return request.get('/v1/catalog/diff', { params })
  }
}
