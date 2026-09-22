import hashlib
import logging
import os
import re
import sys

if not os.environ.get("DJANGO_SETTINGS_MODULE"):
    import django

    root_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.extend([root_path])
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings.dev")
    django.setup()

from django.db import close_old_connections
from rest_framework import exceptions, status

LOGGER = logging.getLogger(__name__)


class CustomValidationError(exceptions.APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "Invalid input."
    default_code = "invalid"

    def __init__(self, detail=None, code=None):
        if detail is None:
            detail = self.default_detail
        if code is None:
            code = self.default_code

        self.detail = detail


class ToolUtil:
    """
    小工具
    """

    @staticmethod
    def format_drf_error(errors):
        """
        处理drf校验失败参数，友好展示给前端
        :return:
        """
        error_list = list()
        if isinstance(errors, dict):
            for _, value in errors.items():
                error_list.append(str(value))
            result = " ".join(error_list)
        elif isinstance(errors, list):
            for error in errors:
                if error:
                    for _, value in error.items():
                        info = str(value)
                        if info not in error_list:
                            error_list.append(info)
            result = " ".join(error_list)
        else:
            result = str(errors)
        return result

    def generate_md5_8bit(data: str) -> str:
        md5_hash = hashlib.md5(data.encode()).hexdigest()  # 生成32位哈希值
        return md5_hash[:8]


def clean_db_connections_decorator(func):
    """
    清理django数据库连接，解决异步任务中操作数据库报连接丢失情况
    :param func:
    :return:
    """

    def _func(*arg, **kwargs):
        close_old_connections()
        func(*arg, **kwargs)
        close_old_connections()

    return _func


class CommonUtils:
    @staticmethod
    def password_encrypt(password):
        """
        密码加密工具
        """
        obj = hashlib.md5(password.encode("utf-8"))
        return obj.hexdigest()

    @staticmethod
    def password_validate(password):
        """
        密码复杂度校验
        """
        if len(password) < 8:
            return "密码长度必须至少为8个字符"

        # 定义用于检查不同字符类型的正则表达式
        has_uppercase = re.search(r"[A-Z]", password)
        has_lowercase = re.search(r"[a-z]", password)
        has_digit = re.search(r"\d", password)
        has_special_char = re.search(r'[!@#$%^&*(),.?":{}|<>]', password)

        # 计算不同类型的字符数
        types_count = sum(1 for item in [has_uppercase, has_lowercase, has_digit, has_special_char] if item)

        # 检查是否至少有三种不同类型的字符
        if types_count < 3:
            return "密码必须包含至少三种不同类型的字符（大写字母、小写字母、数字、特殊字符中的至少三种）"
        return ""


if __name__ == "__main__":
    print(CommonUtils.password_encrypt("123456"))
