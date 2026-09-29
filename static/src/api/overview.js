import request from './request'

// 首页总览
//
// 一次取回就绪状态、各数据源与各知识库的状态。刻意做成**单个**接口：
// 前端自己拼至少要 5 个请求，其中「每个库的问题数」还是 N+1。
export const overviewApi = {
  get() {
    return request.get('/v1/overview')
  }
}
