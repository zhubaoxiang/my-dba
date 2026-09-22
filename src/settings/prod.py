from settings import settings

settings.DEBUG = False

settings.REST_FRAMEWORK.update({"DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"]})
