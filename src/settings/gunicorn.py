# gunicorn的配置
import os

# CPU核数
# NUM_CORES = multiprocessing.cpu_count()
NUM_CORES = 3

# 绑定端口
bind = "0.0.0.0:8080"
# 进程数
workers = 3
# 每个进程线程数
threads = NUM_CORES * 2
# 访问日志
accesslog = "-"

root_path = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))

logs_path = os.path.join(root_path, "logs")
if not os.path.exists(logs_path):
    os.makedirs(logs_path)
# 访问日志格式
gunicorn_log = os.path.join(logs_path, "gunicorn.log")

logconfig_dict = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "generic": {
            "format": "%(asctime)s [%(process)d] [%(levelname)s] %(message)s",
            "class": "logging.Formatter",
        },
        "access": {
            "format": "%(message)s",
            "class": "logging.Formatter",
        },
    },
    "handlers": {
        # 控制台输出（可选）
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "generic",
            "level": "INFO",
        },
        # 关键：access 和 error 共用这个 handler
        "combined_file": {
            "class": "logging.handlers.TimedRotatingFileHandler",
            "filename": gunicorn_log,
            "when": "midnight",  # 每天切割
            "backupCount": 7,  # 保留 7 天
            "encoding": "utf-8",
            "formatter": "generic",
            "level": "INFO",
        },
    },
    "loggers": {
        "gunicorn.error": {
            "handlers": ["combined_file", "console"],
            "level": "INFO",
            "propagate": False,
        },
        "gunicorn.access": {
            "handlers": ["combined_file", "console"],
            "level": "INFO",
            "propagate": False,
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
}

access_log_format = '[%(h)s] %(l)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" time:%(T)s'
