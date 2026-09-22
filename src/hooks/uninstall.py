import logging
import os

LOGGER = logging.getLogger(__name__)


def main():
    """
    install入口
    """
    base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # 注销BSA菜单
    _execute_cmd(f"python {base_path}/scripts/bsa_register_menu.py 2")
    # 清理表SQL
    # _execute_cmd(f"python {base_path}/scripts/init_sql.py")
    # 其他
    _execute_cmd("echo 'uninstall...'")


def _execute_cmd(cmd):
    ret = os.system(cmd)
    LOGGER.info(f"cmd:{cmd}, ret:{ret}")
    return ret


if __name__ == "__main__":
    main()
