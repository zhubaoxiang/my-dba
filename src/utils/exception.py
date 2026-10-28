import logging
import traceback

from rest_framework import exceptions, status
from rest_framework.views import exception_handler as drf_exception_handler

from apps.base import baseviews
from utils.common import ToolUtil

LOGGER = logging.getLogger(__name__)


def exception_handler(exc, context):
    """
    自定义的异常处理方法
    :param exc:
    :param context:
    :return:
    """
    response = drf_exception_handler(exc, context)

    if response is None:
        try:
            context.get("view").raise_uncaught_exception(exc)
        except Exception:
            LOGGER.error("view:%s, error:%s", context.get("view"), exc)
            LOGGER.error(traceback.format_exc())
        return baseviews.ResponseError("服务器内部错误")
    # 认证类异常统一成中文文案；DRF 的 NotAuthenticated 默认是英文
    request = context.get("request")
    if isinstance(exc, exceptions.NotAuthenticated) or (
        isinstance(exc, exceptions.PermissionDenied)
        and not getattr(getattr(request, "user", None), "is_authenticated", False)
    ):
        return baseviews.ResponseForbidden("未登录或登录已失效")
    if isinstance(exc, exceptions.AuthenticationFailed):
        return baseviews.ResponseForbidden(str(exc.detail))
    if isinstance(exc, exceptions.PermissionDenied):
        return baseviews.ResponsePermissionDenied("您没有权限进行此操作")
    if response.status_code == status.HTTP_401_UNAUTHORIZED:
        detail = response.data.get("detail") if isinstance(response.data, dict) else None
        return baseviews.ResponseForbidden(str(detail) if detail else "未登录或登录已失效")
    if response.status_code == status.HTTP_403_FORBIDDEN:
        detail = response.data.get("detail") if isinstance(response.data, dict) else None
        return baseviews.ResponseForbidden(str(detail) if detail else "您没有权限进行此操作")
    elif response.status_code == status.HTTP_400_BAD_REQUEST:
        return baseviews.ResponseBadRequest(msg=ToolUtil.format_drf_error(response.data), content=response.data)
    return response
