# sdc-harness 后端服务镜像（Django + gunicorn）
#
# 内网/国内环境适配：apt 与 pip 均走阿里云镜像。
# 注意：bullseye 已归档，直接换成 mirrors.aliyun.com/debian 会 404，
# 必须写 debian-archive/debian 源，并放宽 Release 有效期校验
# （-o Acquire::Check-Valid-Until=false），否则 apt-get update 会失败。
FROM python:3.11.9-slim-bullseye

ENV PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    ENV_TYPE=test \
    DEBIAN_FRONTEND=noninteractive

# 换阿里云源（bullseye 位于 debian-archive 下）
RUN echo "deb http://mirrors.aliyun.com/debian-archive/debian/ bullseye main contrib non-free" > /etc/apt/sources.list \
 && echo "deb http://mirrors.aliyun.com/debian-archive/debian/ bullseye-updates main contrib non-free" >> /etc/apt/sources.list

WORKDIR /home/src

# ── 依赖层 ──
# 只拷 requirements 再装依赖：源码改动不会让本层缓存失效（分层的关键）。
# 编译类工具仅在 pip 构建 wheel 时需要，装完即卸以压镜像体积。
COPY src/requirements.txt ./

RUN apt-get -o Acquire::Check-Valid-Until=false update \
 && apt-get install -y --no-install-recommends \
      build-essential libfreetype6-dev libpng-dev pkg-config \
      vim curl net-tools iputils-ping procps netcat \
 && pip3 install --no-cache-dir -U pip -i https://mirrors.aliyun.com/pypi/simple \
 && pip3 install --no-cache-dir --upgrade setuptools -i https://mirrors.aliyun.com/pypi/simple \
 && pip3 install --no-cache-dir -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple \
 && apt-get purge -y --auto-remove build-essential libfreetype6-dev libpng-dev pkg-config \
 && apt-get clean \
 && rm -rf /root/.cache/pip/* /var/lib/apt/lists/*

# ── 代码与资源层 ──
# 置于依赖层之后：日常改代码只重建这一层，依赖不重装。
# 构建上下文由 .dockerignore 排除 venv / node_modules / .git 等（约 220M）。
COPY . /home

EXPOSE 8080

# 启动脚本权限（src/start.sh，生产用 gunicorn）
RUN chmod +x ./start.sh
CMD ["sh", "-c", "sh ./start.sh"]
