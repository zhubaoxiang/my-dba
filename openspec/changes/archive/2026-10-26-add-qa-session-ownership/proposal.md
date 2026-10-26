# Change: 问答会话按用户隔离

## Why

问答会话当前对所有登录用户可见：列表返回全部会话，拿到会话 id 还能读取、删除或继续提问。会话里可能包含业务问题与回答内容，需要按登录用户隔离。

## What Changes

- 普通用户的会话列表只返回本人创建的会话
- 普通用户的会话详情、删除、继续提问（含流式）只允许操作自己的会话
- 别人的会话 id 一律按「会话不存在」处理，不暴露是否存在
- 管理员可以查看并操作全部会话
- 新建会话继续记录 `creator`，作为归属依据

## Impact

- Affected specs: `knowledge-qa`
- Affected code: `src/apps/knowledge/views.py`（`QaSessionView`）
