"""
@description: 配置文件解析
@author:
"""

import configparser
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class ConfigData:
    """配置数据容器"""

    _data: dict

    def get(self, key: str, default: Any = None) -> Any:
        """获取配置项，支持默认值"""
        return self._data.get(key, default)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            return super().__getattribute__(name)
        if name not in self._data:
            raise AttributeError(f"配置项不存在: {name}")
        return self._data[name]


class Configure:
    """配置文件解析类"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self.conf = configparser.ConfigParser()
        config_path = Path(__file__).parent.parent / "config" / "conf.ini"

        if not config_path.exists():
            raise FileNotFoundError(f"配置文件不存在: {config_path}")

        self.conf.read(config_path, encoding="utf-8-sig")
        self.env_type = os.environ.get("ENV_TYPE", "local")
        self._initialized = True

    def get_conf_attr(self) -> ConfigData:
        """读取配置文件属性"""
        data = {}
        for section, options in self.conf.items():
            section_name = section
            if "_" in section:
                base, env = section.rsplit("_", 1)
                if self.env_type != env:
                    continue
                section_name = base

            for k, v in options.items():
                data[f"{section_name}_{k}"] = v

        return ConfigData(data)


CONF_ATTR = Configure().get_conf_attr()

if __name__ == "__main__":
    print(Configure().get_conf_attr())
