import request from './request'

// SQL 分析 API
//
// 两个接口刻意分开：
// - analyze  只读采集快照与内存计算，可以随用随调
// - execute  会在**真实的、可能是生产的**库上执行语句，必须由使用者显式触发
export const sqlAnalysisApi = {
  // 只做格式化：独立接口，不会顺带触发规则判定与模型调用
  format(data) {
    return request.post('/v1/sql-analysis/format', data)
  },
  // 解析 + 规则 + 结构校验 + 模型解读
  analyze(data) {
    return request.post('/v1/sql-analysis/analyze', data)
  },
  // 试运行：只读语句真跑，DML/DDL 只出执行计划
  execute(data) {
    return request.post('/v1/sql-analysis/execute', data)
  }
}
