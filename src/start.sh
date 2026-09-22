#!/bin/bash

# 设置环境变量
export PYTHONUNBUFFERED=1

# 日志目录设置
LOG_DIR="/home/src/logs"
GUNICORN_LOG="${LOG_DIR}/gunicorn.log"
# DJANGO_Q_LOG="${LOG_DIR}/django-q.log"

mkdir -p "${LOG_DIR}"

echo "Starting Django..."
gunicorn -c /home/src/settings/gunicorn.py config.wsgi:application > "${GUNICORN_LOG}" 2>&1 &

# echo "Starting task..."
# python /home/src/jobs/cron.py

# echo "Starting qcluster..."
# python /home/src/manage.py qcluster > "${DJANGO_Q_LOG}" 2>&1 &

# 建议的nginx启动（已注释）
# /usr/sbin/nginx -g 'daemon off;'

tail -f "${GUNICORN_LOG}"