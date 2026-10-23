#!/usr/bin/env python3
"""
构建 my-dba 前后端镜像。

镜像名与 tag 直接取自 docker-compose.yml 的 web / frontend 服务，
保证构建产物和部署配置始终一致。

用法：
    python package.py                  # 构建后端 + 前端
    python package.py --only backend   # 只构建后端
    python package.py --only frontend  # 只构建前端
"""
import argparse
import os
import shutil
import subprocess

import yaml

# 项目根目录
ROOT_PATH = os.path.dirname(os.path.abspath(__file__))
# compose 配置，镜像名与 tag 的唯一来源
COMPOSE_FILE = os.path.join(ROOT_PATH, "docker-compose.yml")
# 前端构建上下文
FRONTEND_CONTEXT = os.path.join(ROOT_PATH, "static")

BACKEND_SERVICE = "web"
FRONTEND_SERVICE = "frontend"


def _image_name(compose, service):
    """
    从 compose 配置读取服务镜像名
    """
    try:
        return compose["services"][service]["image"]
    except (KeyError, TypeError):
        raise SystemExit(f"docker-compose.yml 缺少 services.{service}.image")


def _load_images():
    """
    读取前后端镜像名
    """
    with open(COMPOSE_FILE, encoding="utf-8") as fd:
        compose = yaml.safe_load(fd)
    return _image_name(compose, BACKEND_SERVICE), _image_name(compose, FRONTEND_SERVICE)


def _build(image, context):
    """
    构建单个镜像
    """
    print(f"构建镜像: {image}")
    print(f"构建上下文: {context}")
    try:
        subprocess.run(["docker", "build", "-t", image, context], cwd=ROOT_PATH, check=True)
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"镜像构建失败: {image}（退出码 {exc.returncode}）")


def main():
    parser = argparse.ArgumentParser(description="构建 my-dba 前后端镜像")
    parser.add_argument("--only", choices=["backend", "frontend"], help="只构建其中一端，默认两端都构建")
    args = parser.parse_args()

    if not shutil.which("docker"):
        raise SystemExit("未找到 docker，请先安装并启动 Docker")

    backend_image, frontend_image = _load_images()
    if args.only != "frontend":
        _build(backend_image, ROOT_PATH)
    if args.only != "backend":
        _build(frontend_image, FRONTEND_CONTEXT)

    print("镜像构建完成")


if __name__ == "__main__":
    main()
