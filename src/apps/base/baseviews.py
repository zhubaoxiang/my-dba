from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet


class BaseView(ModelViewSet):
    permission_classes = (IsAuthenticated,)


class OperatorView(ModelViewSet):
    permission_classes = (IsAuthenticated,)


class SuperUserView(ModelViewSet):
    permission_classes = (IsAdminUser,)


class AnyLogin(ModelViewSet):
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
