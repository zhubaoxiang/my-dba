#!/bin/sh
# 按 NGINX_HTTPS 决定是否启用 443 与 80 跳转：
# auto（默认）= 证书存在才启用，on = 强制启用，off = 关闭，仅保留 8765 的 HTTP
set -eu

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

if [ "$enabled" -eq 1 ]; then
    echo "nginx: HTTPS 已启用（443，80 跳转）"
else
    rm -f /etc/nginx/conf.d/https.conf
    echo "nginx: HTTPS 已关闭，仅监听 8765"
fi
