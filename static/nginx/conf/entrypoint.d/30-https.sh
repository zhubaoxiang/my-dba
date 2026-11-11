#!/bin/sh
# 端口与 HTTPS 开关：
# NGINX_HTTP_PORT 默认 80，可改为 8765 等；
# NGINX_HTTPS auto=有证书才启用 443，on=强制启用，off=关闭
set -eu

http_port="${NGINX_HTTP_PORT:-80}"
case "$http_port" in
    ''|*[!0-9]*) http_port=80 ;;
esac
if [ "$http_port" -lt 1 ] || [ "$http_port" -gt 65535 ]; then
    http_port=80
fi

sed -i "s/listen [0-9][0-9]*;/listen ${http_port};/" /etc/nginx/conf.d/http.conf

case "${NGINX_HTTPS:-auto}" in
    on) enabled=1 ;;
    off) enabled=0 ;;
    *)
        enabled=0
        if [ -f /etc/nginx/cert/server.crt ] && [ -f /etc/nginx/cert/server.key ]; then
            enabled=1
        fi
        ;;
esac

if [ "$enabled" -eq 1 ] && [ "$http_port" -eq 443 ]; then
    echo "nginx: HTTP 端口不能与 HTTPS 的 443 相同" >&2
    exit 1
fi

if [ "$enabled" -eq 0 ]; then
    rm -f /etc/nginx/conf.d/https.conf /etc/nginx/conf.d/redirect.conf
    echo "nginx: HTTPS 已关闭，HTTP 监听 ${http_port}"
elif [ "$http_port" -eq 80 ]; then
    rm -f /etc/nginx/conf.d/redirect.conf
    echo "nginx: HTTPS 已启用（443）；HTTP 监听 80，不做 80→443 跳转"
else
    echo "nginx: HTTPS 已启用（443）；HTTP 监听 ${http_port}，80 跳转 443"
fi
