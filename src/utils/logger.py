"""
日志记录模块
本项目中需要日志时，尽量使用该模块，方便统一管理日志配置
"""

import logging
import os
from logging.handlers import TimedRotatingFileHandler


class SuppressDeprecated(logging.Filter):
    """
    过滤已弃用的警告日志
    """

    def filter(self, record):
        WARNINGS_TO_SUPPRESS = ["RemovedInDjango18Warning", "RemovedInDjango19Warning"]
        return not any([warn in record.getMessage() for warn in WARNINGS_TO_SUPPRESS])


def get_logger(logfile: str, level: int = logging.DEBUG) -> logging.Logger:
    """
    获取日志记录器

    Args:
        logfile: 日志文件名，如 'app.log'
        level: 日志级别，默认 DEBUG

    Returns:
        logging.Logger: 配置好的日志记录器
    """
    logger = logging.getLogger(logfile)

    if logger.handlers:
        return logger

    logger.addFilter(SuppressDeprecated())
    logger.setLevel(level)

    log_root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
    if not os.path.exists(log_root):
        os.makedirs(log_root)

    fh = TimedRotatingFileHandler(os.path.join(log_root, logfile), when="midnight", backupCount=30, encoding="utf-8")
    fh.suffix = "%Y-%m-%d"
    fh.setLevel(level)

    ch = logging.StreamHandler()
    ch.setLevel(level)

    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    fh.setFormatter(formatter)
    ch.setFormatter(formatter)

    logger.addHandler(fh)
    logger.addHandler(ch)

    return logger


logger_app = get_logger("app.log")
