# my-dba 前端镜像：Node 构建 + Nginx 托管
#
# 构建上下文是 static/：
#   docker build -t my-dba-frontend:1.0.0 static
#
# 前端产物在镜像内构建，不依赖宿主机上的 static/dist。

FROM node:20-alpine AS builder

WORKDIR /app

COPY package*.json ./
RUN npm ci --registry=https://registry.npmmirror.com

COPY . .
RUN npm run build

FROM nginx:mainline-alpine

ENV TZ=Asia/Shanghai

# Vite 产物直接放到 nginx 根目录：index.html 在 /，资源在 /static/
RUN rm -rf /usr/share/nginx/html/*

COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx/conf/nginx.conf /etc/nginx/nginx.conf

EXPOSE 80 443

CMD ["nginx", "-g", "daemon off;"]
