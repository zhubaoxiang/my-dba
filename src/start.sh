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
GUNICORN_PID=$!

# 指标采集与大数据 worker 都是常驻循环：直接启动 python，才能收到 SIGTERM。
# 它们不随 gunicorn worker 起，避免多 worker 各跑一份。
METRICS_LOG="${LOG_DIR}/metrics.log"
echo "Starting metrics collector..."
python /home/src/jobs/metrics.py > "${METRICS_LOG}" 2>&1 &
METRICS_PID=$!

PROVISIONING_LOG="${LOG_DIR}/provisioning_worker.log"
echo "Starting provisioning worker..."
python /home/src/jobs/provisioning_worker.py > "${PROVISIONING_LOG}" 2>&1 &
PROVISIONING_PID=$!

cleanup() {
    kill -TERM "${GUNICORN_PID}" "${METRICS_PID}" "${PROVISIONING_PID}" "${TAIL_PID:-}" 2>/dev/null
    wait
    exit 0
}
trap cleanup TERM INT

# echo "Starting qcluster..."
# python /home/src/manage.py qcluster > "${DJANGO_Q_LOG}" 2>&1 &

# 建议的nginx启动（已注释）
# /usr/sbin/nginx -g 'daemon off;'

tail -f "${GUNICORN_LOG}" &
TAIL_PID=$!
wait "${TAIL_PID}"
