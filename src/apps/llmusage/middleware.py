"""
模型调用的归属中间件

按 URL 前缀映射来源功能，从 JWT 取用户名，每个请求生成一个 `request_id`，
写入 contextvars 供用量回调读取。
"""

import uuid

from apps.llmusage import context
from utils import custom_enum
from utils.authentication import JwtOperate

# URL 片段 → 来源功能。新增模型调用入口时在这里补一行。
_FEATURE_BY_SEGMENT = (
    ("/v1/qa-session", custom_enum.LlmFeatureEnum.QA),
    ("/v1/nl2sql", custom_enum.LlmFeatureEnum.NL2SQL),
    ("/v1/sql-analysis", custom_enum.LlmFeatureEnum.SQL_INTERPRET),
    ("/v1/ddl-review", custom_enum.LlmFeatureEnum.DDL_REVIEW),
    ("/v1/kb-document", custom_enum.LlmFeatureEnum.KB_INGEST),
    ("/v1/llm-provider", custom_enum.LlmFeatureEnum.PROVIDER_TEST),
)


def _feature_of(path: str) -> int:
    for segment, feature in _FEATURE_BY_SEGMENT:
        if segment in path:
            return feature
    return custom_enum.LlmFeatureEnum.OTHER


def _username_of(request) -> str:
    token = request.headers.get("Token")
    if not token or token == "undefined":
        return ""
    try:
        return JwtOperate().decode(token).get("name", "") or ""
    except Exception:  # noqa: BLE001 无效 token 由 DRF 认证统一处理，这里只做归属
        return ""


class LlmUsageContextMiddleware:
    """
    **不在响应时重置上下文**：流式回答的模型调用发生在响应返回之后，
    提前清空会让流式调用丢失归属；每个请求进入时都会覆盖，不会串到下一个请求。
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        context.set_context(
            username=_username_of(request),
            feature=_feature_of(request.path),
            request_id=uuid.uuid4().hex,
            path=request.path,
        )
        return self.get_response(request)
