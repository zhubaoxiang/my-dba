import request from './request'

// DDL 评审：纯快照计算，不执行、不落库、不调用模型
export const ddlReviewApi = {
  review(data) {
    return request.post('/v1/ddl-review/review', data)
  }
}
