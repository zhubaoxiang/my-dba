from settings import settings

settings.DEBUG = True

settings.REST_FRAMEWORK.update({"DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"]})

from settings.settings import *  # noqa: E402,F401,F403 变异后重导出，暴露给 Django Settings 读取
