"""
自定义中间件
"""

from django.utils.deprecation import MiddlewareMixin


class CustomMiddleware(MiddlewareMixin):
    """
    自定义中间简
    """

    def process_request(self, request):
        """
        请求处理
        """
        pass

    def process_response(self, request, response):
        """
        处理响应
        """
        return response
