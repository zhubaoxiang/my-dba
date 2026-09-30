"""
数据源指标采集进程（常驻）

按配置的间隔采集全部数据源的指标并顺手清理过期数据。

**独立于 web 进程运行**：由 `start.sh` 启动，不随 gunicorn worker 起，因此 gunicorn 的
多个 worker 不会各跑一份。外层由 `start.sh` 的 `while true` 守护——容器的
`restart: always` 只管容器、不管容器里的单个进程。

**循环内兜住所有异常**：采集出任何问题都不该让这个进程退出（退出就再也没人采了）。
"""

import os
import sys
import time

# 让脚本能被直接执行：把 src/ 放进模块搜索路径，并沿用与 web 相同的环境选择逻辑
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

env_type = os.environ.get("ENV_TYPE")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", f"settings.{env_type}" if env_type else "settings.local")

import django  # noqa: E402

django.setup()

from apps.datasource import metrics  # noqa: E402
from utils.logger import get_logger  # noqa: E402

LOGGER = get_logger("datasource.log")


def run_once() -> dict:
    """
    一轮：采集全部数据源，并清理过期数据
    """
    result = metrics.collect_all()
    purged = metrics.purge_expired()
    if purged:
        LOGGER.info("清理过期指标 %s 条", purged)
    return result


def main():
    wait = metrics.interval()
    LOGGER.info("指标采集进程启动，间隔 %s 秒", wait)
    while True:
        try:
            run_once()
        except Exception as exc:  # noqa: BLE001 任何异常都不该让采集停掉
            LOGGER.error("指标采集轮次异常（继续运行）: %s", exc)
        time.sleep(wait)


if __name__ == "__main__":
    main()
