"""
WSGI config for application project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/2.2/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

env_type = os.environ.get("ENV_TYPE")
print("当前环境类型为：", env_type)

env_type = f"settings.{env_type}" if env_type else "settings.local"

os.environ.setdefault("DJANGO_SETTINGS_MODULE", env_type)

application = get_wsgi_application()
