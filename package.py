#!/usr/bin/env python3
"""
项目打包脚本，用于生成BSA R03平台的dat包
步骤：
1. 使用Dockerfile构建docker镜像，并导出镜像到指定目录
2. 使用main.py生成chart包
3. 使用tool.py生成dat包
"""
import os
import sys
import argparse
import json
import time
from datetime import datetime

# package脚本路径
ROOT_PATH = os.path.dirname(os.path.abspath(__file__))
# service-mgr-tool 路径
SERVICE_MGR_TOOL_PATH = os.path.join(ROOT_PATH, "service-mgr-tool")
# service.json路径
SERVICE_JSON = "service.json"


# FRONTEND_REPO = "https://oauth2:zRddW7Z9hhGfEkSkqrhG@gitlab.inone.nsfocus.com/SDC-1/public/demo-frontend.git"


class Package:
    """
    项目打包类
    """

    def __init__(self, skip_docker=False, frontend_path="", clean=False):
        self.skip_docker = skip_docker
        # 默认前端路径为 static 目录
        self.frontend_path = frontend_path if frontend_path else os.path.join(ROOT_PATH, "static")
        self.clean = clean
        self.backend_service_info = {}
        self.frontend_service_info = {}

    def run(self):
        """
        打包项目
        """
        # 如果指定了清理参数，先执行清理
        if self.clean:
            self._clean()
            return

        # 更新serviceVersion，在打包之前
        date_suffix = datetime.now().strftime("%y%m%d")
        self._update_service_version(ROOT_PATH, date_suffix)
        if self.frontend_path:
            self._update_service_version(self.frontend_path, date_suffix)

        self._build_backend()
        if self.frontend_path:
            self._build_frontend()
            self._integrate()

    def _build_backend(self):
        """
        构建后端项目
        """
        self.backend_service_info = self._get_service_info(ROOT_PATH)
        self._build(service_info=self.backend_service_info)

    def _update_service_version(self, base_path, date_suffix):
        """
        更新service.json中的serviceVersion，追加日期后缀
        """
        service_json_path = os.path.join(base_path, SERVICE_JSON)
        self._info(f"更新serviceVersion: {service_json_path}")

        with open(service_json_path, 'r', encoding='utf-8') as fd:
            service_config = json.load(fd)

        old_version = service_config["serviceInfo"]["serviceVersion"]
        new_version = f"{old_version}.{date_suffix}"
        service_config["serviceInfo"]["serviceVersion"] = new_version

        with open(service_json_path, 'w', encoding='utf-8') as fd:
            json.dump(service_config, fd, indent=2, ensure_ascii=False)

        self._info(f"serviceVersion: {old_version} -> {new_version}")

    def _clean(self):
        """
        清理打包产物，恢复到 git 初始版本
        """
        self._info("开始清理打包产物")
        
        # 清理 Docker 镜像（未被使用的悬空镜像）
        self._info("清理 Docker 镜像")
        cmd = "docker image prune -f"
        self._execute_cmd(cmd, check=False)
        
        # 恢复后端到 git 初始版本
        self._info("恢复后端到 git 初始版本")
        cmd = f"cd {ROOT_PATH} && git reset --hard HEAD"
        self._execute_cmd(cmd, check=False)
        
        # 清理后端未跟踪的文件
        self._info("清理后端未跟踪的文件")
        cmd = f"cd {ROOT_PATH} && git clean -fd"
        self._execute_cmd(cmd, check=False)
        
        # 恢复前端到 git 初始版本
        if self.frontend_path and os.path.exists(self.frontend_path):
            self._info("恢复前端到 git 初始版本")
            cmd = f"cd {self.frontend_path} && git reset --hard HEAD"
            self._execute_cmd(cmd, check=False)
            
            # 清理前端未跟踪的文件
            self._info("清理前端未跟踪的文件")
            cmd = f"cd {self.frontend_path} && git clean -fd"
            self._execute_cmd(cmd, check=False)
        
        self._info("清理完成")

    def _build_frontend(self):
        """
        构建前端项目
        """
        if os.path.exists(self.frontend_path):
            self.frontend_service_info = self._get_service_info(self.frontend_path)
            # Dockerfile 已经包含了编译步骤，直接构建项目
            self._build(service_info=self.frontend_service_info)
        else:
            self._info(f"前端项目路径不存在: {self.frontend_path}")
            return

    def _build(self, service_info):
        """
        构建项目
        """
        if not self.skip_docker:
            self._docker_build(base_path=service_info["base_path"],
                               app_name=service_info["app_name"],
                               image_name=service_info["image_name"],
                               image_tar_name=service_info["image_tar_name"])
        else:
            self._info("跳过Docker构建")
        self._gen_chart(base_path=service_info["base_path"], app_name=service_info["app_name"])
        self._gen_package(base_path=service_info["base_path"], app_name=service_info["app_name"])
        return service_info

    def _get_service_info(self, base_path):
        """
        获取service.json配置
        """
        service_info = {
            "base_path": base_path,
            "app_name": "",
            "app_version": "",
            "image_name": "",
            "image_tar_name": "",
        }
        prefix = "registry.kube-system.svc.cluster.local:5000"
        service_json_path = os.path.join(base_path, SERVICE_JSON)
        self._info(f"读取service.json文件: {service_json_path}")
        with open(service_json_path, 'r', encoding='utf-8') as fd:
            service_config = json.load(fd)

        service_info["app_name"] = service_config.get("serviceInfo", {}).get("serviceEname", "")
        service_info["app_version"] = service_config.get("serviceInfo", {}).get("serviceVersion", "")

        # 获取第一个container的imageInfo
        if service_config.get("container") and len(service_config["container"]) > 0:
            image_info = service_config["container"][0].get("imageInfo", {})
            image_project_name = image_info.get("imageProjectName", service_info["app_name"])
            image_repo_name = image_info.get("imageRepoName", service_info["app_name"])
            image_tag = image_info.get("imageTag", "latest")
            service_info["image_name"] = f"{prefix}/{image_project_name}/{image_repo_name}:{image_tag}"
            # 镜像tar包名使用镜像仓库名
            service_info["image_tar_name"] = f"{image_repo_name}.tar"
        else:
            self._error("错误: 未从service.json获取到容器配置")
            sys.exit(1)
        self._info(f"镜像名称: {service_info['image_name']}, 镜像tar包名: {service_info['image_tar_name']}")
        return service_info

    def _docker_build(self, base_path, app_name, image_name, image_tar_name):
        """
        构建Docker镜像并导出为tar包
        """
        self._info(f"构建Docker镜像: {image_name}")
        cmd = f"cd {base_path} && docker build -t {image_name} ."
        self._execute_cmd(cmd)

        image_path = os.path.join(base_path, app_name, "image")
        os.makedirs(image_path, exist_ok=True)
        image_tar_path = image_path + "/" + image_tar_name
        self._info(f"导出Docker镜像: {image_name} 到 {image_tar_path}")
        cmd = f"docker save -o {image_tar_path} {image_name}"
        self._execute_cmd(cmd)

    def _gen_chart(self, base_path, app_name):
        """
        生成chart包
        """
        self._info("生成chart包")
        service_json_path = os.path.join(base_path, SERVICE_JSON)
        target_path = str(os.path.join(base_path, app_name))
        main_py_path = os.path.join(SERVICE_MGR_TOOL_PATH, "main.py")
        if os.path.exists(os.path.join(target_path, "chart")):
            cmd = f"rm -rf {os.path.join(target_path, 'chart')}"
            self._execute_cmd(cmd)
        cmd = f"python3 {main_py_path} -i {service_json_path} -o {target_path}"
        self._execute_cmd(cmd)

    def _gen_package(self, base_path, app_name):
        """
        生成dat包
        """
        self._info("生成dat包")
        target_path = os.path.join(base_path, app_name)
        tool_py_path = os.path.join(SERVICE_MGR_TOOL_PATH, "tool.py")
        cmd = f"python3 {tool_py_path} -i {target_path} -o {base_path}"
        self._execute_cmd(cmd)

    def _integrate(self):
        """
        集成前端项目
        """
        # 将前端dat包移动到后端根目录下
        target_path = os.path.join(self.frontend_path, "*.dat")
        cmd = f"mv {target_path} {ROOT_PATH}"
        self._execute_cmd(cmd)

        time_str = time.strftime("%Y%m%d%H%M%S")
        app_name = self.backend_service_info["app_name"]
        app_version = self.backend_service_info["app_version"]
        package_name = f"{app_name}.{app_version}.{time_str}"
        cmd = f"cd {ROOT_PATH} && tar -zcvf {package_name}.tar.gz *.dat"
        self._execute_cmd(cmd)

        pkg_enc = os.path.join(SERVICE_MGR_TOOL_PATH, "x86", "pkg_enc")
        cmd = f"{pkg_enc} -i {package_name}.tar.gz -o {package_name}.dat"
        self._execute_cmd(cmd)
        self._info(f"生成dat集成包: {package_name}.dat")

    def _execute_cmd(self, cmd, check=True):
        """
        执行命令
        :param cmd: 要执行的命令
        :param check: 是否检查返回值，失败时退出程序
        :return: 命令返回值
        """
        ret = os.system(cmd)
        if ret:
            self._error(f'cmd:{cmd}, ret:{ret}')
            if check:
                sys.exit(1)
        else:
            self._info(f'cmd:{cmd}, ret:{ret}')
        return ret

    @staticmethod
    def _info(content):
        """
        信息
        """
        print(f"\033[92m{content}\033[0m")

    @staticmethod
    def _warn(content):
        """
        告警
        """
        print(f"\033[93m{content}\033[0m")

    @staticmethod
    def _error(content):
        """
        错误
        """
        print(f"\033[91m{content}\033[0m")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="项目打包脚本，用于生成BSA平台的dat包")
    parser.add_argument("--skip-docker", help="跳过Docker镜像构建和导出步骤")
    parser.add_argument("--frontend_path", help="前端项目路径（默认：static 目录）")
    parser.add_argument("--clean", action="store_true", help="清理打包产物，恢复到git初始版本")
    args = parser.parse_args()
    Package(skip_docker=args.skip_docker, frontend_path=args.frontend_path, clean=args.clean).run()
