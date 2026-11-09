"""
测试库大数据预置 worker（常驻）

由 start.sh 守护，独立于 gunicorn：从 provisioned_database 领取 PENDING 任务，
按固定快照执行批量写入。任务状态以数据库为准，进程重启后由 stale 恢复兜底。
"""

import os
import signal
import socket
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

env_type = os.environ.get("ENV_TYPE")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", f"settings.{env_type}" if env_type else "settings.local")

import django  # noqa: E402

django.setup()

from django.db import close_old_connections  # noqa: E402

from apps.provisioning import runner, services  # noqa: E402
from utils.logger import get_logger  # noqa: E402

LOGGER = get_logger("provisioning_worker.log")
_STOP = threading.Event()


def _handle_signal(signum, frame):
    LOGGER.info("收到信号 %s，当前批次结束后退出", signum)
    _STOP.set()


def main():
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    worker_id = f"{socket.gethostname()}:{os.getpid()}"[:128]
    config = services._config()
    try:
        recovered = runner.recover_stale(config["worker_stale_seconds"])
        if recovered:
            LOGGER.info("启动时回收中断任务 %s 条", recovered)
    except Exception as exc:  # noqa: BLE001 恢复失败不应阻止 worker 启动
        LOGGER.error("回收中断任务失败: %s", exc)
    LOGGER.info("大数据预置 worker 启动，worker_id=%s", worker_id)
    while not _STOP.is_set():
        close_old_connections()
        try:
            record = runner.claim_next(worker_id)
            if record is None:
                _STOP.wait(config["worker_poll_seconds"])
                continue
            LOGGER.info("领取任务 id=%s db=%s", record.id, record.db_name)
            runner.run(record, stop_event=_STOP)
        except Exception as exc:  # noqa: BLE001 单轮异常不能让 worker 退出
            LOGGER.error("worker 轮次异常（继续运行）: %s", exc)
        finally:
            close_old_connections()
    LOGGER.info("worker 已退出")


if __name__ == "__main__":
    main()
