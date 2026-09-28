from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet, ViewSet


class BaseView(ModelViewSet):
    permission_classes = (IsAuthenticated,)


class OperatorView(ModelViewSet):
    permission_classes = (IsAuthenticated,)


class SuperUserView(ModelViewSet):
    permission_classes = (IsAdminUser,)


class AnyLogin(ModelViewSet):
    permission_classes = ()
    authentication_classes = ()


class StatelessView(ViewSet):
    """
    无模型的 ViewSet 基类，供只有自定义 action 的接口使用

    例如 SQL 分析：不承载任何资源、不落库。若继承 `AnyLogin`（ModelViewSet 子类），
    会连同带上一套用不了的 list / detail / create 路由——路由注册了但访问即 500。

    权限与 `AnyLogin` 一致：本项目不做应用层鉴权，访问控制依赖网络隔离。
    """

    permission_classes = ()
    authentication_classes = ()


class FormatResponse(Response):
    def __init__(self, code, msg, content):
        format_response = {"code": code, "message": msg, "data": content}
        super().__init__(format_response)
        FormatResponse.status_code = 200


class ResponseOK(FormatResponse):
    def __init__(self, content):
        super().__init__(2000, "success", content)


class ResponseError(FormatResponse):
    def __init__(self, msg="failure", content=None):
        super().__init__(5000, msg, content)


class ResponseBadRequest(FormatResponse):
    def __init__(self, msg="failure", content=None):
        super().__init__(4000, msg, content)


class ResponseForbidden(FormatResponse):
    def __init__(self, msg="failure", content=None):
        super().__init__(4003, msg, content)


class ResponseNotFound(FormatResponse):
    def __init__(self, msg="failure", content=None):
        super().__init__(4004, msg, content)


class ResponseExpectationFailed(FormatResponse):
    def __init__(self, msg="failure", content=None):
        super().__init__(4017, msg, content)
