"""
后台任务提交

`simphe-background-task` 有两个坑，封装在这里免得各模块各踩一遍：
1. worker 线程**不会自动启动**，不 start 的话任务只是入队，永远不执行
2. `BackgroundTask` 是单例但**每次构造都会重建内部队列**，所以只能构造一次并持有，
   不能用它的 `defer()`（内部会重新构造，丢掉已入队的任务）

另注意：队列在进程内存里，进程重启后未执行的任务会丢失。需要可靠执行的任务应把
状态落到数据库，以库中的状态为准（参考采集任务与文档摄入的 status 字段）。
"""

import threading

from simple_background_task import BackgroundTask
from simple_background_task.task import Task

_worker = BackgroundTask()
# 非守护线程会拖住进程退出，这里显式设为守护线程
_worker.daemon = True
_lock = threading.Lock()
_started = False


def _ensure_worker():
    global _started
    if _started:
        return
    with _lock:
        if _started:
            return
        _worker.start()
        _started = True


def submit(func, **kwargs):
    """
    把任务放进进程内队列并立即返回
    """
    _ensure_worker()
    _worker.put(Task(func, **kwargs))
