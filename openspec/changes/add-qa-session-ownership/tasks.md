## 1. 实现

- [x] 1.1 会话列表按 `creator` 过滤
- [x] 1.2 会话详情、删除、提问、流式提问统一走带 `creator` 的实例查询
- [x] 1.3 新建会话继续记录 `creator`
- [x] 1.4 拒绝直接创建、修改、部分修改会话，避免绕过归属

## 2. 测试

- [x] 2.1 覆盖列表、详情、删除、续问、流式续问的跨用户访问
- [x] 2.2 覆盖本人会话正常访问、直接创建/修改被拒绝
- [x] 2.3 运行 `python manage.py test`
- [x] 2.4 运行 `openspec validate add-qa-session-ownership --strict --no-interactive`
