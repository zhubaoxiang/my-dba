import request from './request'

// 慢接口的超时（毫秒）
//
// 生成可能调用模型**两次**（首次生成 + 一轮自修），而后端单次调用受 `llm_timeout`
// 约束（默认 60 秒）。全局 axios 只等 15 秒，这里必须放宽，否则必然重演
// 「后端还在跑、前端先放弃」。**调大后端的 `llm_timeout` 时，这个常量要跟着调。**
const SLOW_TIMEOUT = 150000

// 说人话 → 查询语句
export const nl2sqlApi = {
  // 生成（可能含一轮自修）。试运行不在这里——它由前端直接调既有的 /sql-analysis/execute
  generate(data) {
    return request.post('/v1/nl2sql/generate', data, { timeout: SLOW_TIMEOUT })
  }
}
