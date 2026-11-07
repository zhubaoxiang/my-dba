# HTTP / HTTPS 共用的站点路由

# Vite 产物：/static/index-*.js / .css
location /static/ {
    try_files $uri =404;
    expires 30d;
    add_header Cache-Control "public, immutable";
}

# 知识问答的 SSE：关闭缓冲，避免流式回答被 nginx 攒住
location /my-dba/v1/qa-session/ask-stream {
    proxy_pass http://backend;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Connection '';
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 3600s;
}

# 后端 API
location /my-dba/ {
    proxy_pass http://backend;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}

# SPA：/login、/home 等前端路由回落到 index.html
location / {
    try_files $uri $uri/ /index.html;
}
