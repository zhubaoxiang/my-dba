"""
模型调用的请求上下文

中间件在请求进入时写入，回调读取。**不在响应时重置**：流式回答的模型调用发生在
响应返回之后（生成器执行时），提前清空会让流式调用丢失归属。
"""

import contextvars

from utils import custom_enum

_username = contextvars.ContextVar("llm_usage_username", default="")
_feature = contextvars.ContextVar("llm_usage_feature", default=custom_enum.LlmFeatureEnum.OTHER.value)
_request_id = contextvars.ContextVar("llm_usage_request_id", default="")
_path = contextvars.ContextVar("llm_usage_path", default="")


def set_context(username: str, feature: int, request_id: str, path: str):
    _username.set(username)
    _feature.set(feature)
    _request_id.set(request_id)
    _path.set(path)


def get_context() -> dict:
    return {
        "username": _username.get(),
        "feature": _feature.get(),
        "request_id": _request_id.get(),
        "path": _path.get(),
    }
