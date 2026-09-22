"""
ASGI config for src project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/3.0/howto/deployment/asgi/
"""

import logging
import os

from django.core.asgi import get_asgi_application

LOGGER = logging.getLogger(__name__)

env_conf = os.environ.get("ENV_TYPE")
LOGGER.info(f"当前环境变量：{env_conf}")
if env_conf is None:
    raise OSError("找不到环境标识")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", f"settings.{env_conf}")

application = get_asgi_application()
