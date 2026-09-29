import request from './request'

// 慢接口的超时（毫秒）
//
// 全局 axios 只等 15 秒，而这两个接口的后端上限比它大得多：模型调用受 llm_timeout
// 约束（默认 60 秒）、试运行的语句受 statement_timeout 约束（默认 30 秒）。
// **若调大了后端那两项配置，这里要跟着调**，否则会重演「后端还在等、前端先放弃」。
const SLOW_TIMEOUT = 90000

// SQL 分析 API
//
// 几个接口刻意分开，因为它们是耗时差了几个数量级的不同事情：
// - format    只改排版，不需要数据源，也不跑规则与模型
// - analyze   解析 + 规则 + 结构校验，毫秒级、确定
// - interpret 模型解读，十几秒的外部调用
// - execute   会在**真实的、可能是生产的**库上执行语句，必须由使用者显式触发
export const sqlAnalysisApi = {
  format(data) {
    return request.post('/v1/sql-analysis/format', data)
  },
  analyze(data) {
    return request.post('/v1/sql-analysis/analyze', data)
  },
  interpret(data) {
    return request.post('/v1/sql-analysis/interpret', data, { timeout: SLOW_TIMEOUT })
  },
  execute(data) {
    return request.post('/v1/sql-analysis/execute', data, { timeout: SLOW_TIMEOUT })
  }
}
