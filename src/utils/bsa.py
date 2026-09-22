"""
底座平台(BSA)基础信息工具类
"""

import requests
import urllib3

from utils.configure import CONF_ATTR
from utils.logger import get_logger

LOGGER = get_logger("bsa.log")

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class BsaClient:
    """底座平台客户端，用于获取BSA平台上的基础信息"""

    def _get_bsa_url(self):
        """获取底座平台地址"""
        return getattr(CONF_ATTR, "common_bsa_url", "")

    def _get_proxy_headers(self):
        """构造请求头，包含认证信息"""
        bsa_url = self._get_bsa_url()
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Referer": f"{bsa_url}/isop",
            "Origin": bsa_url,
        }

        # local环境从配置文件注入认证信息
        config_sessionid = getattr(CONF_ATTR, "common_sessionid", "")
        config_csrftoken = getattr(CONF_ATTR, "common_csrftoken", "")

        if config_csrftoken:
            headers["X-Csrftoken"] = config_csrftoken

        cookie_parts = []
        if config_sessionid:
            cookie_parts.append(f"sessionid={config_sessionid}")
        if config_csrftoken:
            cookie_parts.append(f"csrftoken={config_csrftoken}")
        if cookie_parts:
            headers["Cookie"] = "; ".join(cookie_parts)

        return headers

    def get_service_component_info(self, service_ename):
        """
        获取底座平台上的组件信息

        :param service_ename: 组件英文名称，如 "b-vuln-backend"
        :return: dict 组件信息，失败时返回None
        """
        bsa_url = self._get_bsa_url()
        url = f"{bsa_url}/service-component/innerService/info"
        headers = self._get_proxy_headers()
        payload = {"serviceEname": service_ename}

        LOGGER.info(f"获取组件信息: {service_ename}, URL: {url}")

        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=30, verify=False)
            data = resp.json()

            LOGGER.info(f"获取组件信息响应: {data}")

            if data.get("success") and data.get("code") == 200:
                return data.get("data")

            LOGGER.warning(f'获取组件信息失败: {data.get("message", "未知错误")}')
            return None
        except requests.exceptions.RequestException as e:
            LOGGER.error(f"获取组件信息请求异常: {str(e)}")
            return None
        except ValueError as e:
            LOGGER.error(f"获取组件信息响应解析失败: {str(e)}")
            return None


bsa_client = BsaClient()
