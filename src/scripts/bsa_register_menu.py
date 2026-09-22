import argparse
import json
import os
import time
import traceback

import requests

# ===============================================================
# 以下为平台提供的SDK客户端代码，您可以将其封装在您的项目中
# ===============================================================


class BaseClient:
    """
    处理通用请求逻辑的基类客户端。
    """

    def __init__(self, base_url, headers=None):
        self.prefix = base_url
        self.headers = headers or {"Content-Type": "application/json"}

    def _request(self, method, path, **kwargs):
        """
        请求的封装。
        """
        url = self.prefix + path
        if "data" in kwargs and isinstance(kwargs["data"], dict):
            kwargs["data"] = json.dumps(kwargs["data"])

        try:
            response = requests.request(method, url, headers=self.headers, **kwargs)
            response.raise_for_status()  # 对错误的HTTP状态码抛出异常

            if response.text:
                return response.json()
            return None
        except requests.RequestException as e:
            print(f"请求 {url} 失败: {e}", exc_info=True)
            raise
        except json.JSONDecodeError:
            print(f"解析JSON响应失败: {response.text}")
            return response.text


class KongClient(BaseClient):
    """
    用于访问Kong网关后服务的基类客户端。
    """

    def __init__(self):
        # 从环境变量获取Kong的内部服务地址
        kong_address = os.environ.get("BSA_KONG", "http://kong-proxy-inner.kong.svc.cluster.local:80")
        super().__init__(kong_address)


class RightServiceClient(KongClient):
    """
    用于注册/注销组件菜单的客户端。
    """

    def register_component_menu(self, right_config, type=1):
        # right_config 可以是dict或json字符串
        #   right_config_str = json.dumps(right_config) if isinstance(right_config, dict) else right_config
        body = {"right_config_str": right_config, "type": type}
        return self._request("POST", "/permission/innerService/bsa_sdk_register_component_menu/", data=body)

    def unregister_component_menu(self, app_name):
        body = {"app_name": app_name}
        return self._request("POST", "/permission/innerService/bsa_sdk_unregister_component_menu/", data=body)


# ===============================================================
# 菜单注册逻辑
# ===============================================================


def register_menu():
    """
    读取配置文件并注册菜单。
    """
    right_config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "right_config.json")
    try:
        with open(right_config_path, encoding="utf-8") as f:
            right_config = json.load(f)

        right_service_client = RightServiceClient()
        response = right_service_client.register_component_menu(right_config)
        print(f"菜单注册结果: {response}")

        # 检查返回结果，如果失败可以进行重试
        data = response.get("data")
        if not data or not data[0]:
            print("菜单注册可能失败，正在重试...")
            time.sleep(2)  # 等待2秒
            return_dict = right_service_client.register_component_menu(right_config)
            print(f"重试结果: {return_dict}")

    except Exception as e:
        print(f"注册菜单时发生错误: {e}")
        print(traceback.format_exc())


def unregister_menu():
    """
    根据 app_name 注销菜单。
    """
    right_config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "right_config.json")
    with open(right_config_path) as fd:
        right_config = json.load(fd)
    try:
        app_name_to_unregister = right_config.get("app_name", "")
        right_service_client = RightServiceClient()
        response = right_service_client.unregister_component_menu(app_name_to_unregister)
        print(f"菜单 '{app_name_to_unregister}' 反注册结果: {response}")
    except Exception as e:
        print(f"反注册菜单时发生错误: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="BSA菜单注册")
    parser.add_argument("type", help="注册类型，1：注册；2：注销")

    args = parser.parse_args()
    register_type = int(args.type)
    if register_type == 1:
        register_menu()
    elif register_type:
        unregister_menu()
    else:
        print(f"参数异常，参数：{register_type}")
